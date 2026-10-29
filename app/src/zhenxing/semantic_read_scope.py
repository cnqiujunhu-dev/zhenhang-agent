"""Model-selected previous read scope, never old money or write authority."""
from datetime import datetime
from .models import Task
from .runtime_models import RuntimeRecord,PlanSnapshot
from .planning import canonical_hash
from .contracts import DomainError


def current_bill_period_scope(message, calendar_time):
    """Resolve a trusted current bill period once, with auditable provenance.

    The caller has already selected BILL_ANALYSIS. This function never assigns
    a bill action to another domain or treats an assistant's text as a period.
    """
    from .reports import bill_request_period,bill_request_text,positive_bill_period_text
    from .authentication import as_utc
    period=bill_request_period(message,calendar_time)
    if period is None:return None
    start,end=period
    return {'version':'verified-read-period-v1','read_kind':'BILL_ANALYSIS','source_kind':'CURRENT_MESSAGE',
        'source_message_hash':canonical_hash({'message':message}),
        'source_text_hash':canonical_hash({'text':positive_bill_period_text(bill_request_text(message))}),
        'timezone':'Asia/Shanghai','resolved_at':as_utc(calendar_time).isoformat(),
        'period_start':start.isoformat(),'period_end':end.isoformat(),'boundary':'[start,end)'}


def inherited_bill_period(session,principal,parent,*,operation_id=None):
    from .semantic_proposal import load_verified_semantic_intent
    record=session.get(RuntimeRecord,parent.id)
    proof=load_verified_semantic_intent(session,parent,record.metadata_json) if record else None
    if not proof:return None
    from .semantic_read_history_scope import inherited_history_period
    historical=inherited_history_period(session,principal,parent,proof,operation_id)
    if historical is not None:return historical
    if [action.kind for action in proof.expected_proposal.actions]!=['BILL_ANALYSIS']:return None
    binding=proof.binding;focus=binding['decision'].get('focus_ref')
    entry=binding['offered_context']['frame_bindings'].get(focus)
    if not entry:return None
    origin=session.get(Task,entry['task_id']);snapshot=session.get(PlanSnapshot,entry.get('snapshot_id'))
    if not origin or (origin.user_id,origin.sandbox_id)!=(principal.user_id,principal.sandbox_id) or not snapshot \
        or snapshot.task_id!=origin.id or snapshot.plan_hash!=entry.get('plan_hash') \
        or snapshot.proposal_json!=entry.get('proposal_json'):
        raise DomainError('SEMANTIC_READ_SCOPE_CHANGED','之前的查询范围已变化，请重新核对。',409)
    from .owned_user_dialogue import _bill_result_scope
    scope=_bill_result_scope(session,principal,origin)
    if not scope or not scope.get('period_start') or not scope.get('period_end'):return None
    frame=next((item for item in binding['offered_context']['model'].get('frames',[])
        if item.get('frame_ref')==focus),{})
    expected=frame.get('read_scope')
    if expected is not None and expected!={'read_kind':'BILL_ANALYSIS',
            'period_start':scope['period_start'],'period_end':scope['period_end']}:
        raise DomainError('SEMANTIC_READ_SCOPE_CHANGED','之前展示的账单期间已变化，请重新核对。',409)
    start,end=datetime.fromisoformat(scope['period_start']),datetime.fromisoformat(scope['period_end'])
    if start.tzinfo is None or end.tzinfo is None or start>=end:
        raise DomainError('SEMANTIC_READ_SCOPE_CHANGED','之前的统计期间需要重新核对。',409)
    return start,end
