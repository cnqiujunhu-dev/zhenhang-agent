"""Field-origin calendar interpretation; never authorizes or executes money."""
from copy import deepcopy

from .authentication import as_utc
from .contracts import DomainError
from .planning import canonical_hash

VERSION='semantic-temporal-origins-v3'
FIELDS={'TRANSFER_SCHEDULE':frozenset({'scheduled_at','schedule_end_at','schedule_occurrence_times'}),
    'AA_REQUEST':frozenset({'aa_expires_at'}),'MERCHANT_PURCHASE':frozenset({'delivery_start','delivery_end'}),
    'LIFE_PLAN_AUTHORIZE':frozenset({'delivery_start','delivery_end','plan_year','life_plan_reserve_at','life_plan_order_at'})}
LABELS={'scheduled_at':'预约执行日期','schedule_end_at':'周期结束日期','schedule_occurrence_times':'全部指定预约时点','aa_expires_at':'AA截止日期',
    'delivery_start':'配送开始日期和时间','delivery_end':'配送结束日期和时间','plan_year':'生日安排年份',
    'life_plan_reserve_at':'生活计划预留日期和时间','life_plan_order_at':'生活计划下单日期和时间'}


def _deny(field=None):
    raise DomainError('TEMPORAL_SOURCE_REQUIRED','请重新明确'+LABELS.get(field,'这项日期和时间')+'；原日期来源暂时无法核验，不会改按今天重新推算。',422)


def _actions(proposal):
    return proposal.get('actions',[]) if isinstance(proposal,dict) else [row.model_dump() for row in proposal.actions]


def capture_temporal_context(session,receipt,offered,proposal):
    """Capture only the selected ancestor, so proof storage grows linearly."""
    from .runtime_models import RuntimeRecord
    result=deepcopy(offered)
    if not any(FIELDS.get(row['kind'],frozenset()).intersection(row.get('slots',{})) for row in _actions(proposal)):
        return result
    decision=receipt['decision'];focus=decision.get('focus_ref')
    if decision['mode'] not in {'CONTINUE','REVISE','RESUME'}:return result
    entry=result.get('frame_bindings',{}).get(focus)
    if not isinstance(entry,dict):_deny()
    if not entry.get('pending_semantic') and not entry.get('prior_semantic'):
        record=session.get(RuntimeRecord,entry.get('task_id'))
        prior=(record.metadata_json or {}).get('cloud_semantic_intent') if record else None
        if isinstance(prior,dict):entry['temporal_source_binding']=deepcopy(prior)
    result['context_hash']=canonical_hash({'scope':result.get('actor_scope_hash'),
        'frames':result['frame_bindings'],'entities':result.get('entity_bindings',{})})
    return result


def _source_turn(session,principal,thread,binding,*,current=False):
    from .conversation_models import ChatTurn
    from .semantic_proposal import _valid_turn
    turn=session.get(ChatTurn,binding.get('source_turn_id'))
    _valid_turn(session,principal,thread,turn)
    receipt=(turn.diagnostics_json or {}).get('semantic_receipt')
    if turn.sequence!=binding.get('source_sequence') or turn.status not in ({'LEASED','ANSWERED'} if current else {'ANSWERED'}) \
            or canonical_hash({'message':turn.message})!=binding.get('source_message_hash') \
            or canonical_hash(receipt)!=binding.get('source_receipt_hash') \
            or not isinstance(receipt,dict) or receipt.get('status')!='READY' or receipt.get('decision')!=binding.get('decision'):_deny()
    return turn,receipt


def temporal_origin_ledger(session,principal,thread,binding,*,current=False,depth=0):
    """Rebuild origins from validated field SETs or the selected earlier proof."""
    if depth>24:_deny()
    version=binding.get('temporal_origins',{}).get('version',VERSION)
    if version not in {'semantic-temporal-origins-v1','semantic-temporal-origins-v2',VERSION}:_deny()
    turn,receipt=_source_turn(session,principal,thread,binding,current=current)
    offered=binding['offered_context'];decision=receipt['decision'];actions=_actions(binding['expected_proposal'])
    entry=offered.get('frame_bindings',{}).get(decision.get('focus_ref'),{})
    if decision['mode']=='REQUEST':refs={'new:'+str(i):i for i in range(len(actions))}
    else:
        refs=entry.get('action_refs') or entry.get('actions') or {}
        refs={ref:(value.get('ordinal') if isinstance(value,dict) else value) for ref,value in refs.items()}
    changed={(refs.get(row.get('action_ref')),row.get('field')):row for row in decision.get('changes',[])}
    inherited=None;origins=[]
    for ordinal,action in enumerate(actions):
        for field in sorted(FIELDS.get(action['kind'],frozenset())):
            raw=action.get('slots',{}).get(field)
            if raw is None:continue
            change=changed.get((ordinal,field))
            if field=='plan_year' and version!=VERSION:
                # Old versions supported only absolute numeric years; a new
                # relative year cannot masquerade as a legacy unbound value.
                from .calendar_year_fields import is_relative_year
                if change and is_relative_year(change.get('source_text')):_deny(field)
                continue
            if change is not None:
                if change.get('operation')!='SET' or change.get('entity_ref') is not None:_deny(field)
                from .semantic_proposal import _trusted_span,_literal_field
                literal=_trusted_span(turn.message,change['source_span'],field,prior=action.get('slots',{}))
                value=_literal_field(field,literal,action['kind'],action.get('slots',{}),change.get('selected_options'),
                    source_created_at=as_utc(turn.created_at))
                if value!=raw or type(value) is not type(raw):_deny(field)
                if field=='plan_year':
                    from .calendar_year_fields import is_relative_year
                    if not is_relative_year(literal):continue  # Absolute years need no clock.
                    if not current and 'temporal_origins' not in binding:_deny(field)
                origin={'action_ordinal':ordinal,'action_kind':action['kind'],'field':field,'value_hash':canonical_hash({'value':raw}),
                    'source_turn_id':turn.id,'source_sequence':turn.sequence,'source_created_at':as_utc(turn.created_at).isoformat(),
                    'source_message_hash':canonical_hash({'message':turn.message}),'source_receipt_hash':canonical_hash(receipt),
                    'source_action_ref':change['action_ref'],'source_span':deepcopy(change['source_span']),'source_text':literal}
                _role_origin(origin,turn.message,version)
                origins.append(origin)
                continue
            if decision['mode'] not in {'CONTINUE','REVISE','RESUME'}:_deny(field)
            if inherited is None:
                prior=entry.get('pending_semantic') or entry.get('prior_semantic') or entry.get('temporal_source_binding')
                if not isinstance(prior,dict) or prior.get('source_sequence',0)>=turn.sequence:_deny(field)
                body=deepcopy(prior);digest=body.pop('binding_hash',None)
                if canonical_hash(body)!=digest or prior.get('task_id')!=entry.get('task_id'):_deny(field)
                from .models import Task
                from .semantic_proposal import _validate_prior_semantic
                previous=_validate_prior_semantic(session,principal,thread,session.get(Task,prior['task_id']),prior,depth+1,audit_only=True)
                expected=entry.get('proposal_json',{}).get('actions',[])
                if not entry.get('pending_semantic') and previous!=expected:_deny(field)
                inherited=temporal_origin_ledger(session,principal,thread,prior,depth=depth+1)
                if 'temporal_origins' in prior and prior['temporal_origins']!=inherited:_deny(field)
            matches=[row for row in inherited['fields'] if row['action_ordinal']==ordinal and row['action_kind']==action['kind'] and row['field']==field]
            if field=='plan_year' and not matches:
                continue  # An audited absolute ancestor retains its fixed year.
            if len(matches)!=1 or matches[0]['value_hash']!=canonical_hash({'value':raw}):_deny(field)
            if field=='plan_year' and not current and 'temporal_origins' not in binding:_deny(field)
            origin=deepcopy(matches[0])
            from .conversation_models import ChatTurn
            _role_origin(origin,session.get(ChatTurn,origin['source_turn_id']).message,version)
            origins.append(origin)
    return {'version':version,'fields':origins}


def _role_origin(origin,message,version):
    from .temporal_field_roles import ROLES,expiry_role_evidence
    role=ROLES.get((origin['action_kind'],origin['field']))
    evidence=None
    if role=='EXPIRY':
        evidence=expiry_role_evidence(message,origin['source_span'])
        if evidence is None:
            from .semantic_proposal import _deny as reject
            reject('SEMANTIC_TEMPORAL_ROLE_REQUIRED')
    if version in {'semantic-temporal-origins-v2',VERSION}:
        origin['temporal_role']=role
        if evidence is not None:origin['role_evidence']=evidence


def checked_temporal_origins(session,principal,binding):
    from .conversation_models import ChatThread
    thread=session.get(ChatThread,binding.get('conversation_id'))
    result=temporal_origin_ledger(session,principal,thread,binding)
    if 'temporal_origins' in binding and binding['temporal_origins']!=result:_deny()
    return result


def temporal_field_clock(session,task,context,field,raw):
    """None is legacy only; a semantic action never falls back to current now."""
    from .compiled_actions import KEY,VerifiedCompiledAction,validate_compiled_action
    from .services import Principal
    from .intelligence import canonical_action_message,ProposedAction
    from .runtime_models import RuntimeRecord
    proof=(context or {}).get(KEY)
    if proof is None:
        child=session.get(RuntimeRecord,task.id)
        roots=[(context or {}).get('source_root_task_id'),child.root_task_id if child and child.kind=='CHILD' else None]
        for root in roots:
            if root is None:continue
            if not isinstance(root,str):_deny(field)
            record=session.get(RuntimeRecord,root)
            if record and (record.metadata_json or {}).get('cloud_semantic_intent'):_deny(field)
        return None
    if not isinstance(proof,VerifiedCompiledAction):_deny(field)
    actor=Principal(task.user_id,task.sandbox_id)
    action=validate_compiled_action(session,actor,canonical_action_message(ProposedAction.model_validate(proof.action)),proof.action,context)
    if field not in FIELDS.get(action.kind,frozenset()) or action.slots.model_dump().get(field)!=raw:_deny(field)
    matches=[row for row in proof.temporal_origins if row['field']==field]
    if len(matches)!=1:_deny(field)
    from .models import Task
    parent=session.get(Task,proof.parent_id);record=session.get(RuntimeRecord,proof.parent_id)
    current=checked_temporal_origins(session,actor,record.metadata_json['cloud_semantic_intent'])
    if not any(row==matches[0] for row in current['fields']):_deny(field)
    from .conversation_models import ChatTurn
    turn=session.get(ChatTurn,matches[0]['source_turn_id'])
    return as_utc(turn.created_at)
