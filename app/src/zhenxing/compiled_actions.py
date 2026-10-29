"""Private typed domain compilation: never reclassify canonical/user wording."""
from dataclasses import dataclass
from copy import deepcopy
import hashlib,hmac,secrets
from .planning import canonical_hash
from .contracts import DomainError

KEY='_verified_compiled_action'
_AUTHORITY=object();_SEAL_KEY=secrets.token_bytes(32)


def _seal(value):return hmac.new(_SEAL_KEY,canonical_hash(value).encode(),hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class VerifiedCompiledAction:
    authority:object
    parent_id:str
    operation_id:str
    owner:tuple
    parent_version:int
    plan_version:int
    plan_hash:str
    action:dict
    temporal_origins:list
    seal:str


def bind_compiled_action(session,principal,parent,operation,action,*,phase_context=None):
    from .runtime_models import RuntimeRecord,PlanSnapshot
    from .intelligence import ProposedAction,action_signature
    from .semantic_proposal import validate_semantic_proposal
    from .intelligence import IntentProposal
    record=session.get(RuntimeRecord,parent.id)
    if not record or not record.metadata_json.get('cloud_semantic_intent'):return None
    if (parent.user_id,parent.sandbox_id)!=(principal.user_id,principal.sandbox_id) or operation.parent_task_id!=parent.id \
        or not operation.active or operation.plan_version!=record.plan_version:
        raise DomainError('SEMANTIC_COMPILER_SCOPE_CHANGED','当前步骤来源已变化。',409)
    if phase_context is not None:
        from .semantic_phase_context import verified_semantic_phase
        phase=verified_semantic_phase(session,parent,phase_context)
        if phase['phase']!='PREPARE_NEXT_STEP' or phase['target_id']!=operation.id:
            raise DomainError('SEMANTIC_COMPILER_SCOPE_CHANGED','后续步骤的来源许可不匹配。',409)
    snapshot=session.query(PlanSnapshot).filter_by(task_id=parent.id,version=record.plan_version).one()
    validate_semantic_proposal(session,parent,IntentProposal.model_validate(snapshot.proposal_json),record.metadata_json,phase_context=phase_context)
    original=ProposedAction.model_validate(operation.args_json)
    selected=ProposedAction.model_validate(action.model_dump())
    if selected.kind!=original.kind or selected.depends_on!=original.depends_on:
        raise DomainError('SEMANTIC_COMPILER_ACTION_CHANGED','当前动作范围已变化。',409)
    if selected.slots!=original.slots:
        # The only existing compiler resolution is one bank-evidenced anomaly
        # card. It is not a model-authored card/amount or an expanded operation.
        a=selected.slots.model_dump(exclude_none=True);b=original.slots.model_dump(exclude_none=True)
        if selected.kind!='CARD_LOCK' or set(a)-set(b)!={'card_last4'} or any(a.get(k)!=v for k,v in b.items()):
            raise DomainError('SEMANTIC_COMPILER_FIELD_CHANGED','当前字段范围已变化。',409)
        from .runtime_models import RuntimeOperation
        from .models import Task,Card
        card_ids=set()
        for prior in session.query(RuntimeOperation).filter_by(parent_task_id=parent.id).all():
            child=session.get(Task,prior.child_task_id) if prior.child_task_id else None
            if child:
                card_ids.update(row['card_id'] for row in child.analysis_json.get('anomalies',[]) if row.get('card_id'))
        if len(card_ids)!=1:raise DomainError('SEMANTIC_COMPILER_CARD_UNPROVED','卡片对象尚未核验。',409)
        card=session.get(Card,next(iter(card_ids)))
        if not card or (card.user_id,card.sandbox_id)!=(parent.user_id,parent.sandbox_id) or card.last4!=a['card_last4']:
            raise DomainError('SEMANTIC_COMPILER_CARD_UNPROVED','卡片对象尚未核验。',409)
    temporal=[]
    from .temporal_fields import FIELDS,checked_temporal_origins
    if FIELDS.get(selected.kind,frozenset()).intersection(selected.slots.model_dump(exclude_none=True)):
        ledger=checked_temporal_origins(session,principal,record.metadata_json['cloud_semantic_intent'])
        actions=snapshot.proposal_json['actions']
        try:ordinal=int(operation.operation_key.rsplit('-a',1)[1])-1
        except (ValueError,IndexError):ordinal=-1
        if not 0<=ordinal<len(actions) or ProposedAction.model_validate(actions[ordinal]).model_dump()!=original.model_dump():
            raise DomainError('TEMPORAL_SOURCE_REQUIRED','日期字段对应的原步骤无法核验，请重新明确日期。',422)
        temporal=[deepcopy(row) for row in ledger['fields'] if row['action_ordinal']==ordinal]
    payload={'parent_id':parent.id,'operation_id':operation.id,'owner':[parent.user_id,parent.sandbox_id],
        'parent_version':parent.version,'plan_version':record.plan_version,'plan_hash':parent.plan_hash,'action':selected.model_dump(),
        'temporal_origins':temporal}
    return VerifiedCompiledAction(_AUTHORITY,parent.id,operation.id,(parent.user_id,parent.sandbox_id),parent.version,
        record.plan_version,parent.plan_hash,deepcopy(payload['action']),temporal,_seal(payload))


def validate_compiled_action(session,principal,message,proposed,context):
    from .intelligence import ProposedAction,canonical_action_message
    from .runtime_models import RuntimeRecord,RuntimeOperation
    from .models import Task
    proof=(context or {}).get(KEY)
    if proof is None:return None
    if not isinstance(proof,VerifiedCompiledAction) or proof.authority is not _AUTHORITY:
        raise DomainError('SEMANTIC_PRIVATE_COMPILER_REQUIRED','步骤需要可信编译来源。',409)
    payload={'parent_id':proof.parent_id,'operation_id':proof.operation_id,'owner':list(proof.owner),
        'parent_version':proof.parent_version,'plan_version':proof.plan_version,'plan_hash':proof.plan_hash,'action':proof.action,
        'temporal_origins':proof.temporal_origins}
    parent=session.get(Task,proof.parent_id);record=session.get(RuntimeRecord,proof.parent_id);operation=session.get(RuntimeOperation,proof.operation_id)
    parsed=ProposedAction.model_validate(proposed)
    if not hmac.compare_digest(_seal(payload),proof.seal) or proof.owner!=(principal.user_id,principal.sandbox_id) \
        or not parent or not record or not operation or operation.parent_task_id!=parent.id or not operation.active \
        or parent.version!=proof.parent_version or parent.plan_hash!=proof.plan_hash or record.plan_version!=proof.plan_version \
        or parsed.model_dump()!=proof.action or message!=canonical_action_message(parsed):
        raise DomainError('SEMANTIC_COMPILER_SCOPE_CHANGED','步骤来源或范围已变化。',409)
    return parsed
