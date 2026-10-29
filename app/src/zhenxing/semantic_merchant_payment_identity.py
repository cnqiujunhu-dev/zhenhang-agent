"""Completed owned credit purchase identifies the consumption, not its eligibility."""
from sqlalchemy import select
from .models import Task
from .conversation_models import ChatThread,ChatTurn
from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
from .planning import canonical_hash
from .contracts import DomainError

KIND='COMPLETED_MERCHANT_PAYMENT_IDENTITY'
def _deny():raise DomainError('SEMANTIC_MERCHANT_PAYMENT_SOURCE_CHANGED','刚才消费的支付来源无法核验，请先查询本人信用卡明细。',409)

def payment_identities(session,principal,thread,parent,before_sequence):
    from .merchant_models import MerchantOrder
    from .merchant_runtime import verified_single_order_source
    from .merchant_service import verified_order_payment
    from .conversation_provenance import current_plan_completed_write,validate_dialogue_actor_epoch
    from .semantic_proposal import validate_semantic_source_snapshot
    from .life_write_receipts import completed_life_write_receipt_integrity
    owned=lambda row:row and (row.user_id,row.sandbox_id)==(principal.user_id,principal.sandbox_id)
    if not owned(thread) or not owned(parent) or parent.state!='SUCCEEDED' or thread.status!='ACTIVE' or getattr(principal,'family_context',None):return []
    record=session.get(RuntimeRecord,parent.id);metadata=record.metadata_json if record else {};native=metadata.get('cloud_semantic_intent')or{}
    origin=session.get(ChatTurn,native.get('source_turn_id')) if native else None
    if not owned(origin) or origin.thread_id!=thread.id or origin.task_id!=parent.id or origin.sequence>=before_sequence:return []
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==record.plan_version))
    if not snapshot or snapshot.plan_hash!=parent.plan_hash:_deny()
    validate_dialogue_actor_epoch(session,parent,metadata);validate_semantic_source_snapshot(session,principal,parent,metadata,snapshot)
    values=[]
    for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,RuntimeOperation.active==1,
            RuntimeOperation.action_kind=='MERCHANT_PURCHASE',RuntimeOperation.status=='SUCCEEDED')):
        child=session.get(Task,op.child_task_id)
        if not owned(child) or not current_plan_completed_write(session,parent,op) or not completed_life_write_receipt_integrity(session,principal,child,'MERCHANT_PURCHASE'):_deny()
        orders=list(session.scalars(select(MerchantOrder).where(MerchantOrder.source_task_id==child.id)))
        if len(orders)!=1:_deny()
        order=orders[0];verified_single_order_source(session,principal,order);payment=verified_order_payment(session,principal,order)
        if payment.instrument_type!='CREDIT' or payment.status!='POSTED':continue
        label=f"刚才已支付的信用卡消费 · {order.product_snapshot_json.get('product_name','商品')} · {order.amount_minor/100:.2f}{order.currency}"
        values.append({'source_kind':KIND,'task_id':parent.id,'task_version':parent.version,'plan_version':record.plan_version,
            'operation_id':op.id,'child_task_id':child.id,'child_result_hash':canonical_hash(child.result_json),'order_id':order.id,
            'payment_id':payment.id,'payment_receipt_hash':canonical_hash(order.payment_receipt_json),'conversation_id':thread.id,
            'before_sequence':before_sequence,'source_turn_id':origin.id,'field':'payment_id','value':payment.id,'label':label,
            'identity_only':True,'bank_write_authorized':False})
    return values

def checked_identity(session,principal,thread,entry,field,action_kind):
    if field!='payment_id' or action_kind!='CREDIT_INSTALLMENT' or entry.get('source_kind')!=KIND:_deny()
    parent=session.get(Task,entry.get('task_id'))
    if not parent or entry not in payment_identities(session,principal,thread,parent,entry['before_sequence']):_deny()
    return entry['payment_id']
