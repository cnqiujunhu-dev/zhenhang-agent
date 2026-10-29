"""Receipt-bound user context across long conversations, never bank authority.

The existing cloud decision may select a preference or consultation constraint
from the current user message. The server retains the complete original wording
and its provenance, not a generated summary. Event replay applies explicit
retractions before source eligibility, so expired replacements cannot revive
an older preference. No model call, action selection or banking mutation lives
here; events use the owning ChatTurn's existing JSON column.
"""
from copy import deepcopy
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .conversation_models import ChatTurn
from .dialogue_state import dialogue_actor_scope
from .dialogue_state_models import ConversationDialogueState
from .models import utcnow
from .planning import canonical_hash
from .redaction import redact_credentials

VERSION = 'source-bound-conversation-memory-v1'
EVENT_KEY = 'conversation_memory_event'
AUTHORITY = 'USER_CONTEXT_NOT_BANK_FACT_OR_AUTHORIZATION'
MAX_ENTRIES = 24
MAX_CHARACTERS = 8000
MAX_EVENTS = 1024
MAX_AGE_DAYS = 30
KINDS = ('RESPONSE_PREFERENCE','CONSULTATION_TOPIC','CONSULTATION_CONSTRAINT')
NON_OPERATIONAL_MODES = {'CONSULT','FEEDBACK','DISCOVERY','GREETING','CLARIFY','OUT_OF_SCOPE'}


class MemorySpan(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,frozen=True)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,frozen=True)
    operation: Literal['REMEMBER','RETRACT']
    kind: Literal['RESPONSE_PREFERENCE','CONSULTATION_TOPIC','CONSULTATION_CONSTRAINT']
    source_span: MemorySpan
    supersedes: list[str] = Field(default_factory=list,max_length=MAX_ENTRIES)


def _deny(code='CONVERSATION_MEMORY_SOURCE_CHANGED'):
    raise DomainError(code,'此前保留的偏好或咨询约束暂时无法核对，请重新说明；没有据此执行业务。',409)


def prepare_memory_input(memory):
    """Validate an optional language-context list without inventing fields."""
    if not isinstance(memory,list) or len(memory)>MAX_ENTRIES:
        raise ValueError('SEMANTIC_MEMORY_BOUND')
    result=[];refs=set();characters=0
    for entry in memory:
        if not isinstance(entry,dict) or set(entry)!={'memory_ref','kind','quote','source_sequence','authority'}:
            raise ValueError('SEMANTIC_MEMORY_SHAPE_INVALID')
        ref=entry['memory_ref'];quote=entry['quote']
        if not isinstance(ref,str) or not 1<=len(ref)<=100 or not ref.startswith('m') \
                or not all(char.isascii() and (char.isalnum() or char in ':_-') for char in ref) or ref in refs \
                or entry['kind'] not in KINDS or entry['authority']!=AUTHORITY \
                or type(entry['source_sequence']) is not int or entry['source_sequence']<1 \
                or not isinstance(quote,str) or not 0<len(quote)<=2000 or redact_credentials(quote)!=quote \
                or any(ord(char)<32 and char not in '\n\r\t' for char in quote):
            raise ValueError('SEMANTIC_MEMORY_SOURCE_INVALID')
        characters+=len(quote);refs.add(ref);result.append(deepcopy(entry))
    if characters>MAX_CHARACTERS:raise ValueError('SEMANTIC_MEMORY_CHARACTER_BOUND')
    return result


def validate_memory_updates(updates,prepared):
    """Only current-message spans and already offered memory references."""
    if not isinstance(updates,list) or len(updates)>4:raise ValueError('SEMANTIC_MEMORY_UPDATE_BOUND')
    message=prepared.get('message');memory=prepare_memory_input(prepared.get('memory',[]))
    if not isinstance(message,str):raise ValueError('SEMANTIC_MEMORY_SOURCE_INVALID')
    offered={entry['memory_ref']:entry for entry in memory};consumed=set();result=[]
    for raw in updates:
        value=raw.model_dump() if isinstance(raw,MemoryUpdate) else raw
        item=MemoryUpdate.model_validate(value)
        span=item.source_span
        if not 0<=span.start<span.end<=len(message):raise ValueError('SEMANTIC_MEMORY_SPAN_INVALID')
        if item.operation=='RETRACT' and not item.supersedes:raise ValueError('SEMANTIC_MEMORY_RETRACTION_TARGET_REQUIRED')
        if len(set(item.supersedes))!=len(item.supersedes) or any(ref not in offered or ref in consumed for ref in item.supersedes):
            raise ValueError('SEMANTIC_MEMORY_REFERENCE_NOT_OFFERED')
        consumed.update(item.supersedes);result.append(item.model_dump())
    return result


def _scope(session,principal,thread,turn):
    if not thread or not turn or thread.status!='ACTIVE' or turn.thread_id!=thread.id \
            or (thread.user_id,thread.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
            or (turn.user_id,turn.sandbox_id)!=(principal.user_id,principal.sandbox_id):
        _deny('CONVERSATION_MEMORY_NOT_OWNED')
    scope,epoch=dialogue_actor_scope(session,principal)
    state=session.get(ConversationDialogueState,thread.id)
    start=(state.checkpoint_json or {}).get('epoch_start_sequence',0) if state else 0
    if state and (state.actor_scope_hash!=epoch or turn.sequence<=start):_deny()
    diagnostics=turn.diagnostics_json or {}
    if diagnostics.get('actor_role_at_submission')!=scope['role'] or diagnostics.get('dialogue_actor_scope_hash')!=epoch:
        _deny('CONVERSATION_MEMORY_ACTOR_CHANGED')
    return scope['role'],epoch,start


def _eligible_wording(row,decision):
    diagnostics=row.diagnostics_json or {}
    if row.status!='ANSWERED' or row.error_code or row.task_id or row.context_policy!='AUTO' \
            or row.reviewed_evidence_refs_json or diagnostics.get('input_redacted') or diagnostics.get('credential_only') \
            or diagnostics.get('semantic_processing_error') or decision.get('mode') not in NON_OPERATIONAL_MODES \
            or decision.get('action_kinds') or decision.get('changes') or decision.get('entity_refs') or decision.get('focus_ref') \
            or not isinstance(row.message,str) or not 0<len(row.message)<=2000 or redact_credentials(row.message)!=row.message:
        return False
    # Reuse the established untrusted-material provenance guard, not a new
    # vocabulary for recognizing customer intent or deciding what to remember.
    from .intelligence import _source_spans
    if _source_spans(row.message):return False
    if any(left in row.message and right in row.message for left,right in (('“','”'),('「','」'),('‘','’'))) \
            or row.message.count('"')>=2:
        return False
    return True


def _public_entries(row,updates):
    return [{'memory_ref':'m'+canonical_hash({'turn_id':row.id,'ordinal':index})[:24],
        'kind':item['kind'],'quote':row.message,'source_sequence':row.sequence,'authority':AUTHORITY}
        for index,item in enumerate(updates) if item['operation']=='REMEMBER']


def _event_valid(row,event,role,epoch):
    if not isinstance(event,dict):return False
    payload=deepcopy(event);digest=payload.pop('event_hash',None)
    receipt=(row.diagnostics_json or {}).get('semantic_receipt')
    if digest!=canonical_hash(payload) or event.get('schema_version')!=VERSION \
            or event.get('conversation_id')!=row.thread_id or event.get('source_turn_id')!=row.id \
            or event.get('source_sequence')!=row.sequence or event.get('user_id')!=row.user_id \
            or event.get('sandbox_id')!=row.sandbox_id or event.get('actor_scope_hash')!=epoch \
            or event.get('actor_role')!=role or not isinstance(receipt,dict) \
            or receipt.get('status')!='READY' or receipt.get('purpose')!='UNIFIED_SEMANTIC_UNDERSTANDING' \
            or receipt.get('source_message_hash')!=canonical_hash({'message':row.message}) \
            or event.get('source_message_hash')!=receipt.get('source_message_hash') \
            or event.get('source_receipt_hash')!=canonical_hash(receipt) \
            or event.get('input_hash')!=receipt.get('input_hash') \
            or event.get('expires_at')!=(as_utc(row.created_at)+timedelta(days=MAX_AGE_DAYS)).isoformat():
        return False
    try:
        updates=validate_memory_updates((receipt.get('decision') or {}).get('memory_updates',[]),
            {'message':row.message,'memory':event.get('offered_memory')})
        return bool(updates) and event.get('updates')==updates and event.get('entries')==_public_entries(row,updates) \
            and event.get('retired_refs')==[ref for item in updates for ref in item['supersedes']]
    except (ValueError,TypeError,KeyError):return False


def _build_memory_context(session,principal,thread,turn,*,now=None,current_state=False):
    role,epoch,start=_scope(session,principal,thread,turn)
    empty={'memory':[],'memory_bindings':{},'memory_diagnostics':{
        'memory_version':VERSION,'memory_status':'READY','memory_event_count':0,
        'memory_active_count':0,'memory_expired_count':0,'memory_unavailable_count':0,
        'memory_dropped_budget_count':0,'memory_model_calls':0}}
    if turn.context_policy=='INDEPENDENT':
        empty['memory_diagnostics']['memory_status']='INDEPENDENT';return empty
    query=select(ChatTurn).where(ChatTurn.thread_id==thread.id,ChatTurn.user_id==principal.user_id,
        ChatTurn.sandbox_id==principal.sandbox_id,ChatTurn.sequence>start,
        ChatTurn.diagnostics_json[EVENT_KEY].as_string().is_not(None),ChatTurn.id!=turn.id)
    if not current_state:query=query.where(ChatTurn.sequence<turn.sequence)
    from .semantic_context_history import bounded_context_turns
    rows=bounded_context_turns(session,principal,thread.id,query.order_by(ChatTurn.sequence).limit(MAX_EVENTS+1),descending=False)
    diagnostics=empty['memory_diagnostics'];diagnostics['memory_event_count']=len(rows)
    if any(getattr(row,'_context_source_unavailable',False) for row in rows):
        diagnostics.update(memory_status='SOURCE_UNAVAILABLE',memory_unavailable_count=1)
        return empty  # An unread FORGET must not revive an older preference.
    if len(rows)>MAX_EVENTS:
        diagnostics['memory_status']='CAPACITY_REQUIRES_REVIEW';return empty
    active={};retired=set();observed=as_utc(now or utcnow())
    from .product_knowledge import sources_still_authorized
    from .knowledge_runtime import knowledge_sources_current
    for row in rows:
        event=(row.diagnostics_json or {}).get(EVENT_KEY)
        if not isinstance(event,dict) or event.get('actor_scope_hash')!=epoch:
            diagnostics['memory_unavailable_count']+=1;continue
        if not _event_valid(row,event,role,epoch):
            # An invalid correction cannot expose the superseded wording.
            diagnostics['memory_status']='SOURCE_INVALID';return empty
        retired.update(event['retired_refs'])
        for ref in event['retired_refs']:active.pop(ref,None)
        d=row.diagnostics_json or {}
        if d.get('actor_role_at_submission')!=role or d.get('dialogue_actor_scope_hash')!=epoch \
                or not _eligible_wording(row,(d.get('semantic_receipt') or {}).get('decision',{})) \
                or event.get('source_sources_hash')!=canonical_hash(row.sources_json) \
                or not sources_still_authorized(session,principal,row.sources_json) \
                or not knowledge_sources_current(row.sources_json,role):
            diagnostics['memory_unavailable_count']+=len(event['entries']);continue
        try:
            from datetime import datetime
            expires=as_utc(datetime.fromisoformat(event['expires_at']))
        except (KeyError,TypeError,ValueError):
            diagnostics['memory_status']='SOURCE_INVALID';return empty
        if expires<=observed:
            diagnostics['memory_expired_count']+=len(event['entries']);continue
        for entry in event['entries']:
            if entry['memory_ref'] not in retired:
                active[entry['memory_ref']]=(entry,{'schema_version':VERSION,'source_turn_id':row.id,
                    'source_sequence':row.sequence,'source_message_hash':event['source_message_hash'],
                    'source_receipt_hash':event['source_receipt_hash'],'source_sources_hash':event['source_sources_hash'],
                    'actor_scope_hash':epoch,'event_hash':event['event_hash'],'expires_at':event['expires_at']})
    selected=[];characters=0
    for pair in reversed(list(active.values())):
        size=len(pair[0]['quote'])
        if len(selected)>=MAX_ENTRIES or characters+size>MAX_CHARACTERS:
            diagnostics['memory_dropped_budget_count']+=1;continue
        selected.append(pair);characters+=size
    selected.reverse()
    memory=prepare_memory_input([entry for entry,_binding in selected])
    diagnostics['memory_active_count']=len(memory)
    return {'memory':memory,'memory_bindings':{entry['memory_ref']:binding for entry,binding in selected},
        'memory_diagnostics':diagnostics}


def build_memory_context(session,principal,thread,turn,*,now=None):
    """Complete, current-authorized preferences preceding this source turn."""
    return _build_memory_context(session,principal,thread,turn,now=now)


def validate_memory_context(session,principal,thread,turn,offered,*,current_state=True):
    """Recheck exactly the consumed subset, including newer explicit retractions.

    No omitted memory is reintroduced. Use current_state=False when recording
    this turn's event; publication checks also see later completed corrections.
    """
    expected=prepare_memory_input(offered.get('model',{}).get('memory',[]))
    bindings=offered.get('memory_bindings',{})
    if not isinstance(bindings,dict) or set(bindings)!={entry['memory_ref'] for entry in expected}:_deny()
    current=_build_memory_context(session,principal,thread,turn,current_state=current_state)
    actual={entry['memory_ref']:entry for entry in current['memory']}
    if any(actual.get(entry['memory_ref'])!=entry or current['memory_bindings'].get(entry['memory_ref'])!=bindings[entry['memory_ref']]
            for entry in expected):_deny()
    return deepcopy(expected),canonical_hash({'memory':expected,'bindings':bindings})


def apply_memory_updates(session,principal,thread,turn,result,offered):
    """Persist only a real verified decision after its final answered source."""
    from .semantic_proposal import _validated_result
    receipt=_validated_result(result,offered,turn.message)
    role,epoch,_start=_scope(session,principal,thread,turn)
    if receipt!=(turn.diagnostics_json or {}).get('semantic_receipt'):_deny('CONVERSATION_MEMORY_RECEIPT_CHANGED')
    updates=validate_memory_updates((receipt.get('decision') or {}).get('memory_updates',[]),offered['model'])
    if not updates:return {'status':'NO_UPDATES','model_api_call_count':0}
    if not _eligible_wording(turn,receipt['decision']):return {'status':'INELIGIBLE_SOURCE','model_api_call_count':0}
    memory,_digest=validate_memory_context(session,principal,thread,turn,offered,current_state=False)
    event={'schema_version':VERSION,'conversation_id':thread.id,'source_turn_id':turn.id,'source_sequence':turn.sequence,
        'user_id':principal.user_id,'sandbox_id':principal.sandbox_id,'actor_role':role,'actor_scope_hash':epoch,
        'source_message_hash':receipt['source_message_hash'],'source_receipt_hash':canonical_hash(receipt),
        'source_sources_hash':canonical_hash(turn.sources_json),'input_hash':receipt['input_hash'],
        'updates':updates,'offered_memory':memory,'entries':_public_entries(turn,updates),
        'retired_refs':[ref for item in updates for ref in item['supersedes']],
        'expires_at':(as_utc(turn.created_at)+timedelta(days=MAX_AGE_DAYS)).isoformat()}
    event['event_hash']=canonical_hash(event)
    previous=(turn.diagnostics_json or {}).get(EVENT_KEY)
    if previous is not None:
        if previous!=event:_deny('CONVERSATION_MEMORY_EVENT_CHANGED')
        return {'status':'ALREADY_RETAINED','model_api_call_count':0}
    turn.diagnostics_json={**turn.diagnostics_json,EVENT_KEY:event}
    return {'status':'RETAINED','model_api_call_count':0,'memory_event_hash':event['event_hash']}
