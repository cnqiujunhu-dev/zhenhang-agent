"""Historical own-cash STATUS from real receipts, never slots or a new payment."""
from decimal import Decimal

from sqlalchemy import select

from .contracts import DomainError
from .models import Task,ToolCall,Confirmation
from .own_transfer_presentation import own_transfer_write_recorded
from .planning import canonical_hash
from .runtime_models import RuntimeOperation,RuntimeRecord


PUBLIC_RECEIPT_FIELDS=('schema_version','action','status','source_card_last4','destination_card_last4',
    'currency','amount_minor','same_owner_internal_movement','outward_transfer_daily_quota_consumed',
    'bank_state_changed','ledger_balanced','authority')


def _deny():
    raise DomainError('SEMANTIC_SCOPE_SOURCE_CHANGED','这笔本人转存的回执来源暂时无法核对，请查看原记录；没有再次划转。',409)


def completed_own_transfer_facts(session,principal,parent):
    """None means mixed/no OWN scope; malformed pure OWN evidence fails closed."""
    from .intelligence import READ_KINDS
    from .customer_profile_common import require_profile_actor
    from .own_account_transfers import ACTION,TOOL,completed_own_account_transfer_receipt
    identifier=getattr(parent,'id',None)
    parent=session.get(Task,identifier) if isinstance(identifier,str) else None
    if parent is None or (parent.user_id,parent.sandbox_id)!=(principal.user_id,principal.sandbox_id):return None
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id)
        .order_by(RuntimeOperation.ordinal,RuntimeOperation.id)))
    writes=[operation for operation in operations if operation.action_kind not in READ_KINDS]
    if not writes or any(operation.action_kind!=ACTION for operation in writes):return None
    require_profile_actor(session,principal)
    root=session.get(RuntimeRecord,parent.id)
    if parent.state!='SUCCEEDED' or root is None or root.kind!='PARENT':_deny()
    facts=[]
    for operation in writes:
        if operation.sandbox_id!=principal.sandbox_id or operation.status!='SUCCEEDED' or operation.active!=1 \
                or operation.plan_version!=root.plan_version:_deny()
        child=session.get(Task,operation.child_task_id)
        if child is None:_deny()
        calls=list(session.scalars(select(ToolCall).where(ToolCall.task_id==child.id,ToolCall.sandbox_id==principal.sandbox_id,
            ToolCall.tool_name==TOOL,ToolCall.status=='SUCCESS',ToolCall.idempotency_key==operation.idempotency_key)))
        confirmations=list(session.scalars(select(Confirmation).where(Confirmation.task_id==child.id,
            Confirmation.user_id==principal.user_id,Confirmation.sandbox_id==principal.sandbox_id,
            Confirmation.action_type==TOOL,Confirmation.status=='CONSUMED',Confirmation.consumed_at.is_not(None))))
        if len(calls)!=1 or len(confirmations)!=1:_deny()
        receipt=completed_own_account_transfer_receipt(session,parent,operation,child,calls[0],confirmations[0])
        if not own_transfer_write_recorded(ACTION,receipt):_deny()
        facts.append({'kind':ACTION,'receipt':{key:receipt[key] for key in PUBLIC_RECEIPT_FIELDS},
            'receipt_hash':canonical_hash(receipt)})
    return facts


def render_own_transfer_status(facts,language=None):
    if not isinstance(facts,list) or not facts:return None
    lines=[];english=language=='en'
    for fact in facts:
        if not isinstance(fact,dict):return None
        receipt=fact.get('receipt')
        if fact.get('kind')!='OWN_ACCOUNT_TRANSFER' or not own_transfer_write_recorded('OWN_ACCOUNT_TRANSFER',receipt):return None
        amount=f"{Decimal(receipt['amount_minor'])/100:.2f}"
        source,destination=receipt['source_card_last4'],receipt['destination_card_last4']
        lines.append(f'Completed: CNY {amount} moved from your debit card ending {source} to your debit card ending {destination}.' if english else
            f'已完成：从本人尾号{source}借记卡转存{amount}元至尾号{destination}借记卡。')
    lines.append('Moving money between your own accounts is not spending or new income. Total owned cash is unchanged.' if english else
        '本人账户间转存不计消费、不新增收入，本人现金总额不变。')
    return '\n'.join(lines)


def completed_own_scope_status(session,principal,parent,language=None):
    facts=completed_own_transfer_facts(session,principal,parent)
    answer=render_own_transfer_status(facts,language)
    return (answer,facts) if answer else None
