"""Verified native semantic memo delimiter equivalence, never write authority."""
from .planning import canonical_hash


def verified_complete_inner_memo(session,parent,operation,action):
    """Only the whole exact inside of the original explicit memo is eligible.

    Rule/legacy proposals retain their old raw-quote contract. The existing
    sealed native source must prove this exact operation and field; the normal
    compiler, payment policy and fresh confirmation still run afterwards.
    """
    if action.kind not in {'TRANSFER','TRANSFER_SCHEDULE'} or not isinstance(action.slots.memo,str):return False
    from .intelligence import _transfer_memo
    value,issue=_transfer_memo(parent.message)
    pairs={'“':'”','「':'」','"':'"','‘':'’',"'":"'"}
    if issue or not isinstance(value,str) or len(value)<2 or pairs.get(value[0])!=value[-1] \
            or action.slots.memo!=value[1:-1]:return False
    from .runtime_models import RuntimeRecord
    from .semantic_proposal import load_verified_semantic_intent,_trusted_span
    from .contracts import DomainError
    record=session.get(RuntimeRecord,parent.id)
    if not record or not record.metadata_json.get('cloud_semantic_intent'):return False
    try:
        proof=load_verified_semantic_intent(session,parent,record.metadata_json)
        if not proof or type(operation.ordinal) is not int or not 0<=operation.ordinal<len(proof.expected_proposal.actions) \
                or canonical_hash(proof.expected_proposal.actions[operation.ordinal].model_dump())!=canonical_hash(action.model_dump()):return False
        binding=proof.binding;decision=binding['decision']
        if decision['mode']=='REQUEST':refs={'new:'+str(index):index for index in range(len(proof.expected_proposal.actions))}
        else:
            entry=binding['offered_context']['frame_bindings'].get(decision.get('focus_ref')) or {}
            values=entry.get('actions') or entry.get('action_refs') or {}
            refs={ref:(item.get('ordinal') if isinstance(item,dict) else item) for ref,item in values.items()}
        changes=[row for row in decision.get('changes',[]) if row.get('field')=='memo'
            and row.get('operation','SET')=='SET' and refs.get(row.get('action_ref'))==operation.ordinal]
        if len(changes)!=1:return False
        span=changes[0]['source_span']
        return _trusted_span(parent.message,span,'memo')==action.slots.memo \
            and span['start']>0 and span['end']<len(parent.message) \
            and parent.message[span['start']-1:span['end']+1]==value
    except (DomainError,ValueError,TypeError,KeyError,AttributeError):return False
