"""Private current-read display proof, never a bank or WRITE authorization."""
from copy import deepcopy
import hashlib
import hmac
import re
import secrets

from .planning import canonical_hash

KEY = 'credit_status_display_policy'
VERSION = 'credit-status-presentation-v1'
_SEAL_KEY = secrets.token_bytes(32)


def _shape(trace):
    from .credit_presentation import credit_read_recorded
    from .credit_read_common import credit_read_fingerprint
    from .card_identifiers import CARD_NUMBER_PATTERN
    if (not isinstance(trace, dict) or trace.get('state') != 'SUCCEEDED'
            or (trace.get('outcome') or {}).get('verified') is not True
            or trace.get('pending_confirmation') is not None):
        return None
    nodes = trace.get('nodes')
    if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict): return None
    node = nodes[0]; result = node.get('result')
    if node.get('action') != 'CREDIT_VIEW' or node.get('status') != 'SUCCEEDED' \
            or not credit_read_recorded('CREDIT_VIEW', result) or len(result['cards']) != 1 \
            or result.get('read_fingerprint') != credit_read_fingerprint(result): return None
    card = result['cards'][0]
    if not isinstance(card.get('card_id'), str) or not card['card_id'] \
            or not isinstance(card.get('card_number'), str) or re.fullmatch(CARD_NUMBER_PATTERN, card['card_number']) is None \
            or type(card.get('version')) is not int or card['version'] < 1: return None
    return {'task_id': trace.get('task_id'), 'task_version': trace.get('version'),
        'node_id': node.get('node_id'), 'operation_id': node.get('operation_id'), 'child_task_id': node.get('child_task_id'),
        'result_hash': canonical_hash(result), 'card_id': card['card_id'], 'card_number': card['card_number'],
        'card_version': card['version'], 'status': card.get('status')}


def _sealed(value):
    value = deepcopy(value)
    value['source_seal'] = hmac.new(_SEAL_KEY, canonical_hash(value).encode('ascii'), hashlib.sha256).hexdigest()
    return value


def checked_policy(trace):
    value = trace.get(KEY) if isinstance(trace, dict) else None
    if not isinstance(value, dict): return None
    shape = _shape(trace)
    if shape is None or set(value) != {
            'schema_version', 'status', 'task_id', 'task_version', 'projection_hash', 'binding_hash', 'source_seal'}:
        return None
    payload = deepcopy(value); seal = payload.pop('source_seal')
    if payload.get('schema_version') != VERSION or payload.get('status') not in {'READY', 'UNAVAILABLE'} \
            or payload.get('task_id') != shape['task_id'] or payload.get('task_version') != shape['task_version'] \
            or payload.get('projection_hash') != canonical_hash(shape) or not isinstance(seal, str): return None
    expected = hmac.new(_SEAL_KEY, canonical_hash(payload).encode('ascii'), hashlib.sha256).hexdigest()
    return payload if hmac.compare_digest(expected, seal) else None


def attach_credit_status_display(session, principal, parent, trace):
    """Called only by narration/customer presentation, never raw task_trace."""
    from .credit_repayment_closure import attach_repayment_closure
    trace = attach_repayment_closure(session, principal, parent, trace)
    trace = {key: value for key, value in trace.items() if key != KEY}
    shape = _shape(trace)
    if shape is None: return trace
    from .contracts import DomainError
    from .customer_profile_common import require_profile_actor
    require_profile_actor(session, principal)  # Do not turn revoked access into missing display data.
    if parent is None or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError('PROFILE_OWNER_REQUIRED', '当前信用卡资料不属于本人。', 403)
    from sqlalchemy import select
    from .runtime_models import RuntimeRecord, PlanSnapshot
    from .models import Task
    from .authentication import as_utc
    record = session.get(RuntimeRecord, parent.id)
    binding = (record.metadata_json or {}).get('cloud_semantic_intent') if record else None
    if not isinstance(binding, dict): return trace  # Original unmarked legacy display/hash stays unchanged.
    payload = {'schema_version': VERSION, 'status': 'UNAVAILABLE', 'task_id': shape['task_id'],
        'task_version': shape['task_version'], 'projection_hash': canonical_hash(shape), 'binding_hash': None}
    try:
        if parent.id != shape['task_id'] or parent.version != shape['task_version'] or parent.state != 'SUCCEEDED' \
                or trace.get('audit_chain_valid') is not True or record.kind != 'PARENT' or not record.plan_version:
            raise ValueError('DISPLAY_SOURCE_CHANGED')
        snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
            PlanSnapshot.version == record.plan_version))
        if snapshot is None: raise ValueError('DISPLAY_SOURCE_CHANGED')
        from .semantic_proposal import validate_semantic_source_snapshot
        proof = validate_semantic_source_snapshot(session, principal, parent, record.metadata_json, snapshot)
        if not proof or [row.get('kind') for row in proof['proposal_json']['actions']] != ['CREDIT_VIEW']:
            raise ValueError('DISPLAY_SOURCE_CHANGED')
        node = trace['nodes'][0]; child = session.get(Task, node.get('child_task_id'))
        fact = {'task_id': parent.id, 'task_version': parent.version, 'node_id': node['node_id'],
            'operation_id': node['operation_id'], 'child_task_id': node['child_task_id'], 'action': 'CREDIT_VIEW',
            'status': node['status'], 'result': node['result'], 'analysis': child.analysis_json or None if child else None,
            'observed_at': as_utc(child.updated_at).isoformat() if child else None}
        from .customer_read_history import _live_read
        _live_read(session, principal, parent, snapshot, fact)
        from .credit_facts import credit_read_result_current
        if not credit_read_result_current(session, principal, 'CREDIT_VIEW', node['result']):
            raise ValueError('DISPLAY_SOURCE_CHANGED')
        payload.update(status='READY', binding_hash=proof['binding_hash'])
    except DomainError as error:
        if error.status_code != 409: raise
    except (ValueError, KeyError):
        pass
    return {**trace, KEY: _sealed(payload)}


def fingerprint_policy(trace):
    policy = checked_policy(trace)
    return {**policy, 'source_seal_validated': True} if policy else None


def credit_status_response(trace, language='zh-CN'):
    policy = checked_policy(trace)
    if policy is None: return None
    from .credit_presentation import credit_status_summary
    shape = _shape(trace)
    intro, detail = credit_status_summary(shape['card_number'][-4:], shape['status'],
        source_current=policy['status'] == 'READY', english=language == 'en')
    return {'intro': intro, 'detail': detail, 'detail_kind': 'CREDIT', 'state_kind': 'QUERY_RESULT',
        'read_only': True, 'reply_language': language}


def public_credit_status_display(trace):
    policy = checked_policy(trace)
    if policy is None: return None
    shape = _shape(trace)
    from .credit_presentation import credit_status_summary
    notices = {}
    for lang in ('zh-CN', 'en'):
        intro, _detail = credit_status_summary(shape['card_number'][-4:], shape['status'],
            source_current=policy['status'] == 'READY', english=lang == 'en')
        notices[lang] = intro
    return {'schema_version': VERSION, 'status': policy['status'], 'node_id': shape['node_id'],
        'task_version': shape['task_version'], 'card_id': shape['card_id'],
        'card_status': shape['status'] if policy['status'] == 'READY' and isinstance(shape['status'], str) else None,
        'notices': notices}
