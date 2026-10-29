"""Distinguish authorization, reservation and actual merchant payment from owned facts."""
from sqlalchemy import select
from .runtime_models import RuntimeOperation
from .models import Task
from .intelligence import READ_KINDS
from .life_write_receipts import completed_life_write_receipt_integrity
from .life_plan_service import life_plan_view

KINDS={'LIFE_PLAN_AUTHORIZE','LIFE_PLAN_CANCEL'}
def completed_life_status_summary(session,principal,tasks,contexts,language='zh-CN'):
    if not tasks or len(tasks)!=len(contexts):return None
    if any(context is None or any(kind not in READ_KINDS|KINDS for kind in context.get('action_kinds',[])) for context in contexts):return None
    plans={}
    for parent,context in zip(tasks,contexts):
        if not set(context.get('action_kinds',[]))&KINDS:continue
        if parent.state!='SUCCEEDED' or not context.get('verified'):return None
        for operation in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,RuntimeOperation.active==1,RuntimeOperation.action_kind.in_(KINDS))):
            child=session.get(Task,operation.child_task_id)
            if operation.status!='SUCCEEDED' or not completed_life_write_receipt_integrity(session,principal,child,operation.action_kind):return None
            rows=(child.result_json or {}).get('plans',[])
            if len(rows)!=1 or not rows[0].get('plan_id'):return None
            current=life_plan_view(session,principal,rows[0]['plan_id'])['plans']
            if len(current)!=1:return None
            plan=current[0]
            if plan.get('status') in {'UNKNOWN','SOURCE_UNVERIFIED','PARTIAL','COMPENSATING'}:return None
            plans[plan['plan_id']]=plan
    if not plans:return None
    lines=[]
    for plan in plans.values():
        reserved,spent=plan.get('reserved_minor'),plan.get('spent_minor')
        if type(reserved) is not int or type(spent) is not int:return None
        name=plan['name'];status=plan['status'];en=language=='en'
        if status=='CANCELLED':
            lead=f'{name}: future arrangements are cancelled.' if en else f'“{name}”后续安排已取消。'
        elif spent==0:
            lead=f'{name}: authorized, but no purchase has been paid.' if en else f'“{name}”已完成授权，还没有为商品付款。'
        else:
            lead=f'{name}: merchant payments have been posted; delivery is a separate status.' if en else f'“{name}”已有商品付款，配送状态需按订单分别查看。'
        lines.append(lead+(f' Reserved CNY {reserved/100:.2f}; paid CNY {spent/100:.2f}.' if en else
            f'目前预留{reserved/100:.2f}元，已花费{spent/100:.2f}元。'))
    return {'version':'owned-life-status-v1','verified':True,'plan_ids':list(plans),'answer':'\n'.join(lines)}
