"""Current owned access/renewal after a verified setting change, not an action."""
from sqlalchemy import select
from .intelligence import READ_KINDS
from .models import Task,ToolCall,Confirmation,Subscription
from .runtime_models import RuntimeOperation
from .subscription_write_receipts import completed_subscription_setting_receipt


def completed_subscription_status_summary(session,principal,tasks,contexts,language='zh-CN'):
    if len(tasks)!=1 or len(contexts)!=1:return None
    parent,context=tasks[0],contexts[0]
    if (parent.user_id,parent.sandbox_id)!=(principal.user_id,principal.sandbox_id) or parent.state!='SUCCEEDED' \
            or not context or context.get('possible_write_effect') is not False or context.get('verified') is not True:return None
    ops=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,
        RuntimeOperation.action_kind.not_in(READ_KINDS))))
    if len(ops)!=1 or ops[0].action_kind not in {'SUBSCRIPTION_CANCEL','SUBSCRIPTION_START'}:return None
    op=ops[0];child=session.get(Task,op.child_task_id)
    if child is None:return None
    calls=list(session.scalars(select(ToolCall).where(ToolCall.task_id==child.id,ToolCall.status=='SUCCESS',ToolCall.idempotency_key==op.idempotency_key)))
    cfs=list(session.scalars(select(Confirmation).where(Confirmation.task_id==child.id,Confirmation.status=='CONSUMED')))
    if len(calls)!=1 or len(cfs)!=1:return None
    from .conversation_provenance import current_plan_completed_write
    if not current_plan_completed_write(session,parent,op):return None
    receipt=completed_subscription_setting_receipt(session,parent,op,child,calls[0],cfs[0])
    if receipt is None:return None
    sub=session.get(Subscription,receipt['subscription_id'])
    if sub is None or (sub.user_id,sub.sandbox_id)!=(principal.user_id,principal.sandbox_id):return None
    from .subscription_commerce import subscription_entitlement
    from .subscription_answer_facts import access_answer,renewal_answer
    row={'merchant':sub.merchant,'source_type':sub.source_type,'status':sub.status,**subscription_entitlement(sub)}
    answer=access_answer(row,language)+' '+renewal_answer(row,language)
    return {'version':'verified-current-subscription-status-v1','verified':True,'answer':answer,
        'receipt':receipt,'current_subscription_version':sub.version,'auto_renew':row['auto_renew'],
        'entitled':row['entitled'],'entitlement_end_at':row['entitlement_end_at']}
