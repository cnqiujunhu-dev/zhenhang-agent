"""Immutable verified status wording, with fresh ownership at every read.

Snapshots describe what the server observed for one chat turn. They never
contain a confirmation token or substitute for the current task authority.
"""
from copy import deepcopy
from datetime import datetime

from .authentication import as_utc
from .contracts import DomainError
from .dialogue_state import dialogue_actor_scope
from .models import Task, utcnow
from .planning import canonical_hash
from .redaction import redact_credentials

VERSION = 'conversation-status-snapshot-v1'
AUTHORITY = 'HISTORICAL_STATUS_NOT_CURRENT_AUTHORIZATION'
KEY = 'verified_status_snapshot'
STATES = {'QUEUED','UNDERSTANDING','PLANNING','NEED_CLARIFICATION',
    'AWAITING_CONFIRMATION','AUTHORIZED','EXECUTING','UNKNOWN','PARTIAL',
    'WAITING','SUCCEEDED','FAILED','CLOSED'}
PUBLIC_FIELDS = ('schema_version','conversation_id','turn_id','task_id','task_version',
    'state','reply_language','text','observed_at','authority','projection','fingerprint')


def retain_status_snapshot(session, principal, turn, task, context):
    if not context or turn.status != 'ANSWERED' or turn.mode != 'STATUS' \
            or turn.task_id != task.id or context.get('task_id') != task.id \
            or context.get('task_version') != task.version \
            or context.get('state') != task.state \
            or (turn.user_id,turn.sandbox_id) != (principal.user_id,principal.sandbox_id) \
            or (task.user_id,task.sandbox_id) != (principal.user_id,principal.sandbox_id):
        return False
    if KEY in (turn.diagnostics_json or {}):
        return False  # A later status change cannot replace the original observation.
    from .conversation_turn_state import _turn_language
    text = turn.answer
    if not isinstance(text,str) or not 1 <= len(text) <= 5000 or redact_credentials(text) != text:
        return False
    projection = deepcopy((turn.response_meta_json or {}).get('status_projection'))
    if not isinstance(projection,dict):
        return False
    value = {'schema_version':VERSION,'conversation_id':turn.thread_id,'turn_id':turn.id,
        'task_id':task.id,'task_version':task.version,'state':task.state,
        'reply_language':_turn_language(turn),'text':text,'observed_at':as_utc(utcnow()).isoformat(),
        'authority':AUTHORITY,'projection':projection,
        'actor_scope_hash':dialogue_actor_scope(session,principal)[1],
        'owner':{'user_id':principal.user_id,'sandbox_id':principal.sandbox_id}}
    value['fingerprint'] = canonical_hash(value)
    turn.diagnostics_json = {**(turn.diagnostics_json or {}),KEY:value}
    return True


def read_status_snapshot(session, principal, turn, *, authorized):
    if turn.mode != 'STATUS' or turn.status != 'ANSWERED' or not turn.task_id:
        return None,'NOT_APPLICABLE'
    value = (turn.diagnostics_json or {}).get(KEY)
    if value is None:
        return None,'NOT_RETAINED'
    if not authorized:
        return None,'UNAUTHORIZED'
    if not isinstance(value,dict) or set(value) != set(PUBLIC_FIELDS)|{'actor_scope_hash','owner'}:
        return None,'INVALID'
    body = {key:item for key,item in value.items() if key != 'fingerprint'}
    if value.get('fingerprint') != canonical_hash(body) \
            or value.get('schema_version') != VERSION or value.get('authority') != AUTHORITY \
            or value.get('conversation_id') != turn.thread_id or value.get('turn_id') != turn.id \
            or value.get('task_id') != turn.task_id or value.get('text') != turn.answer \
            or value.get('owner') != {'user_id':principal.user_id,'sandbox_id':principal.sandbox_id} \
            or (turn.user_id,turn.sandbox_id) != (principal.user_id,principal.sandbox_id):
        return None,'INVALID'
    from .conversation_turn_state import _turn_language,_turn_principal
    if value.get('reply_language') != _turn_language(turn) or value.get('state') not in STATES \
            or not isinstance(value.get('text'),str) or not 1 <= len(value['text']) <= 5000 \
            or redact_credentials(value['text']) != value['text']:
        return None,'INVALID'
    projection = value.get('projection')
    if not isinstance(projection,dict) or projection != (turn.response_meta_json or {}).get('status_projection') \
            or (projection.get('task_id'),projection.get('version'),projection.get('state')) != (turn.task_id,value['task_version'],value['state']):
        return None,'INVALID'
    try:
        if not datetime.fromisoformat(value['observed_at']).tzinfo:
            return None,'INVALID'
    except (ValueError,TypeError):
        return None,'INVALID'
    task = session.get(Task,turn.task_id)
    if not task or (task.user_id,task.sandbox_id) != (principal.user_id,principal.sandbox_id):
        return None,'UNAUTHORIZED'
    if type(value.get('task_version')) is not int or not 1 <= value['task_version'] <= task.version:
        return None,'INVALID'
    try:
        current_principal = _turn_principal(session,turn)
        if dialogue_actor_scope(session,current_principal)[1] != value['actor_scope_hash']:
            return None,'UNAUTHORIZED'
        from .extensions import validate_family_origin
        from .runtime import validate_task_trace_access
        validate_family_origin(session,task)
        validate_task_trace_access(session,current_principal,task.id)
    except DomainError:
        return None,'UNAUTHORIZED'
    return {key:deepcopy(value[key]) for key in PUBLIC_FIELDS},'RETAINED'
