"""Cancelling future mandate occurrences is not editing a confirmed draft."""
from sqlalchemy import select
from .contracts import DomainError
from .planning import canonical_hash


def offered_schedule_cancel(decision, prepared):
    get=lambda key,default=None: decision.get(key,default) if isinstance(decision,dict) else getattr(decision,key,default)
    frame=next((f for f in prepared.get('frames',[]) if f.get('frame_ref')==get('focus_ref')),None)
    return bool(get('mode')=='CANCEL' and not get('action_kinds',[]) and not get('changes',[]) and not get('dependencies',[])
        and frame and frame.get('state')=='WAITING' and frame.get('write_effect_possible') is False
        and frame.get('paused') is False and [a.get('kind') for a in frame.get('actions',[])]==['TRANSFER_SCHEDULE'])


def cancel_registered_schedule(session, principal, task):
    from .permissions import require_banking_actor
    from .runtime import validate_runtime_family_context,cancel_runtime
    from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
    from .banking_extension_models import ScopedScheduledTransfer
    from .models import Task
    from .schedule_amendments import replacement_snapshot
    require_banking_actor(session,principal);validate_runtime_family_context(session,task)
    def deny():raise DomainError('SCHEDULE_CANCEL_SOURCE_CHANGED','这份预约当前不能取消，请先核对最新执行结果。',409)
    if (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id) or task.state!='WAITING':deny()
    record=session.get(RuntimeRecord,task.id)
    if record is None or record.kind!='PARENT':deny()
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,PlanSnapshot.version==record.plan_version))
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id,RuntimeOperation.active==1)))
    if (snapshot is None or snapshot.plan_hash!=task.plan_hash or canonical_hash(task.plan_json)!=task.plan_hash
            or snapshot.plan_json!=task.plan_json or len(operations)!=1):deny()
    op=operations[0];child=session.get(Task,op.child_task_id) if op.child_task_id else None
    if op.action_kind!='TRANSFER_SCHEDULE' or op.status!='WAITING_TIME' or op.plan_version!=record.plan_version or child is None \
            or child.state!='WAITING' or (child.user_id,child.sandbox_id)!=(principal.user_id,principal.sandbox_id):deny()
    child_record=session.get(RuntimeRecord,child.id)
    if child_record is None or child_record.kind!='CHILD' or child_record.root_task_id!=task.id \
            or child_record.metadata_json.get('operation_id')!=op.id:deny()
    row=session.scalar(select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.task_id==child.id,
        ScopedScheduledTransfer.user_id==principal.user_id,ScopedScheduledTransfer.sandbox_id==principal.sandbox_id))
    if row is None:deny()
    _,revision=replacement_snapshot(session,principal,row.id,lock=True)
    result=cancel_runtime(session,principal,task.id,task.version)
    return result,len(revision['remaining_dates'])
