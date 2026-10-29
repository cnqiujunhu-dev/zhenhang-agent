"""Explicit historical USER read conditions; never historical bank authority."""
from copy import deepcopy
from datetime import datetime
import re

from .contracts import DomainError
from .planning import canonical_hash

VERSION='explicit-history-user-read-scope-v1'
FIELDS=frozenset({'period','currency','card_number','card_last4'})


def current_history_combination(value,prepared):
    """Shape proof only; both source families are revalidated in the compiler."""
    from .semantic_query_scope import is_bound_current_read_request
    return isinstance(value,dict) and value.get('mode')=='REQUEST' \
        and value.get('query_base')=='CURRENT' and is_bound_current_read_request(value,prepared)


class HistoryScopeLiteralError(ValueError):
    def __init__(self,source_index,message_index,error,source_message):
        super().__init__('SEMANTIC_HISTORY_SCOPE_LITERAL_INVALID')
        location=error.diagnostic.get('location',[])
        field_index=location[1] if len(location)>1 and type(location[1]) is int else 0
        self.diagnostic={'location':['history_read_sources',source_index,'fields',field_index,'source_span'],
            'source':'HISTORY_USER','message_index':message_index,'decoder_version':error.version,
            'reason':error.diagnostic.get('reason'),'source_message_hash':canonical_hash({'message':source_message}),
            'bank_write_authorized':False}


def check_history_available(value,prepared):
    if isinstance(value,dict) and isinstance(value.get('history_read_sources'),list) and value['history_read_sources'] \
            and not any(row.get('role')=='user' for row in prepared.get('history',[])):
        raise ValueError('SEMANTIC_HISTORY_SCOPE_SOURCE_NOT_OFFERED')
    if isinstance(value,dict) and isinstance(value.get('history_read_sources'),list) and value['history_read_sources'] \
            and (value.get('mode')!='REQUEST' or value.get('focus_ref') or value.get('query_reference') \
                or value.get('query_base')=='CURRENT') and not current_history_combination(value,prepared):
        raise ValueError('SEMANTIC_HISTORY_SCOPE_CONTEXT_CONFLICT')


def attach_history_sources(offered,groups,bindings):
    """Keep the original turn mapping through whole-group suffix budgeting."""
    count=(offered.get('context_budget_diagnostics') or {}).get('context_included_history_turn_count',0)
    if not count or len(groups)!=len(bindings):return
    retained=groups[-count:];sources=bindings[-count:]
    if [row for group in retained for row in group]!=offered['model'].get('history'):return
    index=0;result={}
    for group,binding in zip(retained,sources):
        if binding.get('group_hash')!=canonical_hash(group):return
        for row in group:
            if row['role']=='user':result[str(index)]=deepcopy(binding)
            index+=1
    if result:offered['history_read_scope_bindings']=result


def validate_history_sources(value,prepared):
    sources=value.get('history_read_sources',[])
    if not sources:return
    from .intelligence import READ_KINDS
    kinds=value.get('action_kinds',[]);caps={row['kind']:row for row in prepared['capabilities']}
    if value.get('mode')!='REQUEST' or not kinds or any(kind not in READ_KINDS for kind in kinds) \
            or (value.get('focus_ref') or value.get('query_reference') or value.get('query_base')=='CURRENT') \
                and not current_history_combination(value,prepared):
        raise ValueError('SEMANTIC_HISTORY_SCOPE_READ_ONLY')
    seen=set()
    for source in sources:
        match=re.fullmatch(r'new:([0-7])',source.get('action_ref',''))
        ordinal=int(match[1]) if match else -1
        if not 0<=ordinal<len(kinds) or ordinal in seen:raise ValueError('SEMANTIC_HISTORY_SCOPE_ACTION_INVALID')
        seen.add(ordinal)
        index=source.get('message_index');history=prepared.get('history',[])
        if source.get('source')!='HISTORY_USER' or type(index) is not int or not 0<=index<len(history) \
                or history[index].get('role')!='user':raise ValueError('SEMANTIC_HISTORY_SCOPE_SOURCE_INVALID')
        fields=source.get('fields',[]);names=[row.get('field') for row in fields]
        if not names or len(names)!=len(set(names)) or not set(names)<=FIELDS:
            raise ValueError('SEMANTIC_HISTORY_SCOPE_FIELD_INVALID')
        for field in fields:
            name=field['field']
            if name=='period' and kinds[ordinal]!='BILL_ANALYSIS' \
                    or name!='period' and name not in caps.get(kinds[ordinal],{}).get('allowed_fields',[]):
                raise ValueError('SEMANTIC_HISTORY_SCOPE_FIELD_INVALID')
            span=field.get('source_span');text=history[index]['content']
            if not isinstance(span,dict) or set(span)!={'start','end'} or any(type(span[key]) is not int for key in span) \
                    or not 0<=span['start']<span['end']<=len(text) or field.get('source_text')!=text[span['start']:span['end']]:
                raise ValueError('SEMANTIC_HISTORY_SCOPE_LITERAL_INVALID')


def normalize_history_wire(value,prepared):
    """The same exact literal codec, but explicitly in the selected USER row."""
    if not isinstance(value,dict) or not isinstance(value.get('history_read_sources'),list) \
            or len(value['history_read_sources'])>8:return value,[]
    from .semantic_literal_span_wire import normalize_literal_span_wire,LiteralSpanWireError
    canonical=None;events=[]
    for source_index,source in enumerate(value['history_read_sources']):
        if not isinstance(source,dict):continue
        index=source.get('message_index');history=prepared.get('history',[])
        if source.get('source')!='HISTORY_USER' or type(index) is not int or not 0<=index<len(history) \
                or history[index].get('role')!='user' or not isinstance(source.get('fields'),list) or len(source['fields'])>4:continue
        try:encoded,observations=normalize_literal_span_wire({'changes':source['fields']},history[index]['content'])
        except LiteralSpanWireError as error:
            raise HistoryScopeLiteralError(source_index,index,error,history[index]['content']) from error
        if not observations:continue
        if canonical is None:canonical=deepcopy(value)
        canonical['history_read_sources'][source_index]['fields']=encoded['changes']
        events.extend({**row,'location':['history_read_sources',source_index,'fields',row['location'][1],'source_span'],
            'message_index':index,'source':'HISTORY_USER'} for row in observations)
    return (canonical if canonical is not None else value),events


def checked_sources(session,principal,thread,turn,decision,offered):
    """Fresh DB proof for the exact selected old USER turn, including time."""
    from .conversation_models import ChatTurn
    from .dialogue_state import dialogue_actor_scope
    from .authentication import as_utc
    from .redaction import redact_credentials
    from .product_knowledge import sources_still_authorized
    from .knowledge_runtime import knowledge_sources_current
    from .semantic_proposal import _trusted_span,_literal_field,_request_boundary
    from .reports import bill_request_period
    validate_history_sources(decision,offered['model'])
    if not decision.get('history_read_sources'):return []
    scope,epoch=dialogue_actor_scope(session,principal)
    if offered.get('actor_scope_hash')!=epoch:raise DomainError('SEMANTIC_HISTORY_SCOPE_SOURCE_CHANGED','之前的查询条件需要重新核对。',409)
    all_bindings=offered.get('history_read_scope_bindings') or {}
    indexed=sorted((int(index),binding.get('sequence')) for index,binding in all_bindings.items()
        if isinstance(index,str) and index.isdigit() and isinstance(binding,dict))
    if len(indexed)!=len(all_bindings) or any(type(sequence) is not int for _,sequence in indexed) \
            or any(left[1]>=right[1] for left,right in zip(indexed,indexed[1:])):
        raise DomainError('SEMANTIC_HISTORY_SCOPE_SOURCE_CHANGED','之前的查询顺序需要重新核对。',409)
    result=[]
    for source in decision['history_read_sources']:
        index=source['message_index'];binding=(offered.get('history_read_scope_bindings') or {}).get(str(index))
        row=session.get(ChatTurn,binding.get('turn_id')) if isinstance(binding,dict) else None
        diag=(row.diagnostics_json or {}) if row else {}
        if not row or binding.get('version')!=VERSION or row.thread_id!=thread.id \
                or (row.user_id,row.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                or row.status!='ANSWERED' or not row.sequence<turn.sequence or row.sequence!=binding.get('sequence') \
                or binding.get('thread_id')!=thread.id or binding.get('user_id')!=principal.user_id \
                or binding.get('sandbox_id')!=principal.sandbox_id or binding.get('actor_scope_hash')!=epoch \
                or binding.get('role')!=scope['role'] or diag.get('dialogue_actor_scope_hash')!=epoch \
                or diag.get('actor_role_at_submission')!=scope['role'] or diag.get('input_redacted') or diag.get('credential_only') \
                or row.message!=offered['model']['history'][index]['content'] or redact_credentials(row.message)!=row.message \
                or canonical_hash({'message':row.message})!=binding.get('message_hash') \
                or as_utc(row.created_at).isoformat()!=binding.get('created_at') \
                or not sources_still_authorized(session,principal,row.sources_json) or not knowledge_sources_current(row.sources_json,scope['role']):
            raise DomainError('SEMANTIC_HISTORY_SCOPE_SOURCE_CHANGED','之前的查询条件需要重新核对。',409)
        ordinal=int(source['action_ref'].split(':')[1]);kind=decision['action_kinds'][ordinal]
        _request_boundary(row.message,kind,[field['source_span'] for field in source['fields']])
        fields=[]
        for field in source['fields']:
            raw=_trusted_span(row.message,field['source_span'],field['field'])
            if field['field']=='period':
                period=bill_request_period(raw,as_utc(row.created_at))
                if period is None:raise DomainError('SEMANTIC_HISTORY_SCOPE_PERIOD_INVALID','之前的统计期间需要明确。',409)
                value={'period_start':period[0].isoformat(),'period_end':period[1].isoformat()}
            else:value=_literal_field(field['field'],raw,kind,{})
            fields.append({'field':field['field'],'source_span':deepcopy(field['source_span']),'value':value})
        result.append({'version':VERSION,'action_ordinal':ordinal,'action_kind':kind,'source':'HISTORY_USER',
            'message_index':index,'source_turn_id':row.id,'source_sequence':row.sequence,
            'source_message_hash':binding['message_hash'],'created_at':binding['created_at'],'actor_scope_hash':epoch,
            'fields':fields,'bank_write_authorized':False})
    return result


def _compatible_current_sources(session,principal,thread,decision,offered,actions,sources,current_base,*,audit_only=False):
    """Corroborate an existing READ scope; historical language cannot replace it."""
    from .models import Task
    from .owned_user_dialogue import _bill_result_scope
    from .semantic_proposal import _entity
    from .payment_instruments import resolve_source_instrument
    if not current_history_combination(decision,offered['model']) or not current_base:
        raise DomainError('SEMANTIC_HISTORY_SCOPE_CONTEXT_CONFLICT','请明确要沿用的查询范围。',409)
    origin,entry=current_base
    frame=next((row for row in offered['model']['frames'] if row['frame_ref']==decision.get('focus_ref')),None)
    if not isinstance(origin,Task) or origin.state!='SUCCEEDED' or not frame \
            or not audit_only and origin.version!=entry.get('task_version'):
        raise DomainError('SEMANTIC_HISTORY_SCOPE_SOURCE_CHANGED','之前的查询来源已变化，请重新核对。',409)
    period=None
    for source in sources:
        ordinal=source['action_ordinal'];slots=actions[ordinal]['slots']
        for field in source['fields']:
            name,value=field['field'],field['value'];matches=False
            if name=='period':
                if period is None:
                    scope=_bill_result_scope(session,principal,origin)
                    expected=frame.get('read_scope')
                    if scope and expected=={'read_kind':'BILL_ANALYSIS',**{key:scope.get(key) for key in ('period_start','period_end')}}:
                        period={key:scope[key] for key in ('period_start','period_end')}
                    else:period={}
                try:matches=all(datetime.fromisoformat(value[key])==datetime.fromisoformat(period[key])
                    for key in ('period_start','period_end'))
                except (ValueError,TypeError,KeyError):matches=False
            elif name=='currency':matches=slots.get(name)==value
            else:
                # All current selected identity fields are independently
                # consumed from their owned catalog or current exact literal.
                # Do not guess a card from a label/default or truncate a tail.
                refs=[]
                for change in decision.get('changes',[]):
                    if change.get('action_ref')!='new:'+str(ordinal) or change.get('field') not in {'card_number','card_last4'}:continue
                    if change.get('operation')!='SET':continue
                    if change.get('entity_ref'):
                        refs.append(_entity(session,principal,thread,offered,change['entity_ref'],change['field'],
                            action_kind=actions[ordinal]['kind'],audit_only=audit_only))
                refs=refs or [slots[key] for key in ('card_number','card_last4') if slots.get(key)]
                if refs:
                    historical=resolve_source_instrument(session,principal,value,'CNY',purpose='READ')['instrument_id']
                    matches=all(resolve_source_instrument(session,principal,ref,'CNY',purpose='READ')['instrument_id']==historical for ref in refs)
            if not matches:
                raise DomainError('SEMANTIC_CURRENT_HISTORY_FILTER_CONFLICT','之前的条件与选中的查询范围不一致，请明确要用哪一个。',409)


def seed_read_slots(session,principal,thread,turn,decision,offered,actions,*,current_base=None,audit_only=False):
    sources=checked_sources(session,principal,thread,turn,decision,offered)
    if decision.get('query_base')=='CURRENT':
        _compatible_current_sources(session,principal,thread,decision,offered,actions,sources,current_base,audit_only=audit_only)
        return  # Same verified values remain in the base; every source stays in the receipt.
    for source in sources:
        index=source['action_ordinal']
        current={change['field'] for change in decision.get('changes',[]) if change.get('action_ref')=='new:'+str(index)}
        for field in source['fields']:
            name=field['field']
            if name=='period' or name in current or name in {'card_number','card_last4'} and current&{'card_number','card_last4'}:continue
            actions[index]['slots'][name]=deepcopy(field['value'])


def inherited_history_period(session,principal,parent,proof,operation_id):
    """Use a revalidated explicit source for only this child READ operation."""
    from .conversation_models import ChatThread,ChatTurn
    from .runtime_models import RuntimeOperation
    binding=proof.binding;decision=binding['decision']
    if not any(field['field']=='period' for source in decision.get('history_read_sources',[]) for field in source['fields']):return None
    op=session.get(RuntimeOperation,operation_id) if operation_id else None
    if not op or op.parent_task_id!=parent.id or op.action_kind!='BILL_ANALYSIS':
        raise DomainError('SEMANTIC_HISTORY_SCOPE_SOURCE_CHANGED','这次查询条件需要重新核对。',409)
    rows=checked_sources(session,principal,session.get(ChatThread,binding['conversation_id']),
        session.get(ChatTurn,binding['source_turn_id']),decision,binding['offered_context'])
    periods=[field['value'] for row in rows if row['action_ordinal']==op.ordinal for field in row['fields'] if field['field']=='period']
    if not periods:return None
    return datetime.fromisoformat(periods[0]['period_start']),datetime.fromisoformat(periods[0]['period_end'])
