"""Lifecycle commands already interpreted by the cloud; no phrase classifier."""
from sqlalchemy import select
from .contracts import DomainError
from .models import Task,Confirmation,utcnow
from .runtime_models import RuntimeOperation,RuntimeRecord,WorkflowJob,AuthChallenge,PlanSnapshot
from .intelligence import READ_KINDS
from .planning import canonical_hash
from .dialogue_state import dialogue_actor_scope


def require_unexecuted(session,principal,task):
    from .permissions import require_banking_actor
    from .runtime import validate_runtime_family_context
    require_banking_actor(session,principal);validate_runtime_family_context(session,task)
    if (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id) or task.state not in {
        'QUEUED','UNDERSTANDING','PLANNING','NEED_CLARIFICATION','AWAITING_CONFIRMATION'}:
        raise DomainError('SEMANTIC_LIFECYCLE_NOT_MUTABLE','这项业务已不能按未执行需求修改，请先核对原结果。',409)
    record=session.get(RuntimeRecord,task.id)
    if not record or record.kind!='PARENT':raise DomainError('SEMANTIC_SOURCE_CHANGED','这项需求的来源已变化。',409)
    ops=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id)))
    for op in ops:
        child=session.get(Task,op.child_task_id) if op.child_task_id else None
        if op.action_kind not in READ_KINDS and (op.status in {'SUCCEEDED','UNKNOWN','AUTHORIZED','RUNNING'} or
            child and child.state in {'SUCCEEDED','UNKNOWN','AUTHORIZED','EXECUTING'}):
            raise DomainError('SEMANTIC_WRITE_EFFECT_PRESENT','原业务已有执行或未确定结果，不能作为未执行需求重做。',409)
    return record,ops


def pause_semantic_task(session,principal,thread,turn,task):
    record,ops=require_unexecuted(session,principal,task)
    ids=[task.id,*[op.child_task_id for op in ops if op.child_task_id]]
    for op in ops:
        op.active=0
        if op.status!='SUCCEEDED':op.status='CANCELLED'
    for confirmation in session.scalars(select(Confirmation).where(Confirmation.task_id.in_(ids),Confirmation.status=='PENDING')):
        confirmation.status='CANCELLED'
    for challenge in session.scalars(select(AuthChallenge).where(AuthChallenge.task_id.in_(ids),AuthChallenge.status.in_(['PENDING','VERIFIED']))):
        challenge.status='REVOKED'
    for job in session.scalars(select(WorkflowJob).where(WorkflowJob.task_id.in_(ids),WorkflowJob.kind.in_(['PLAN','EXECUTE']),WorkflowJob.status.in_(['QUEUED','LEASED']))):
        job.status='CANCELLED';job.completed_at=utcnow();job.lease_expires_at=None
    task.version+=1
    record.metadata_json={**record.metadata_json,'input_revision':record.metadata_json.get('input_revision',0)+1,
        'semantic_pause':{'version':'semantic-owned-plan-pause-v1','turn_id':turn.id,'thread_id':thread.id,
            'actor_scope_hash':dialogue_actor_scope(session,principal)[1],'message_hash':canonical_hash({'message':turn.message}),
            'intent_message_hash':canonical_hash({'message':task.message}),'task_version':task.version,
            'plan_version':record.plan_version,'plan_hash':task.plan_hash,'bank_write_authorized':False}}
    from .audit import append_audit
    append_audit(session,sandbox_id=task.sandbox_id,task_id=task.id,event_type='SEMANTIC_PLAN_PAUSED',
        payload={'source_turn_id':turn.id,'new_version':task.version,'confirmations_invalidated':True,'bank_write_authorized':False})


def current_semantic_pause(session,principal,task):
    """A UI projection proves current pause/absence of grants, not user prose."""
    from .conversation_models import ChatThread,ChatTurn
    try:
        record,ops=require_unexecuted(session,principal,task)
        pause=record.metadata_json.get('semantic_pause')
        if not isinstance(pause,dict) or pause.get('version')!='semantic-owned-plan-pause-v1':return None
        source=session.get(ChatTurn,pause.get('turn_id'));thread=session.get(ChatThread,pause.get('thread_id'))
        if not source or not thread or source.thread_id!=thread.id or source.task_id!=task.id or source.mode!='FEEDBACK' \
            or source.status!='ANSWERED' or source.task_relationship!='CURRENT' or source.error_code \
            or source.diagnostics_json.get('semantic_control')!='PAUSE' or source.diagnostics_json.get('bank_write_authorized') is not False:
            return None
        if thread.status!='ACTIVE' or (source.user_id,source.sandbox_id)!=(principal.user_id,principal.sandbox_id):return None
        scope_hash=dialogue_actor_scope(session,principal)[1]
        if pause.get('actor_scope_hash')!=scope_hash or source.diagnostics_json.get('dialogue_actor_scope_hash')!=scope_hash \
            or pause.get('message_hash')!=canonical_hash({'message':source.message}) \
            or pause.get('intent_message_hash')!=canonical_hash({'message':task.message}) \
            or pause.get('task_version')!=task.version or pause.get('plan_version')!=record.plan_version or pause.get('plan_hash')!=task.plan_hash:return None
        ids=[task.id,*[op.child_task_id for op in ops if op.child_task_id]]
        if any(op.active for op in ops):return None
        if session.scalar(select(Confirmation.id).where(Confirmation.task_id.in_(ids),Confirmation.status=='PENDING').limit(1)):return None
        if session.scalar(select(AuthChallenge.id).where(AuthChallenge.task_id.in_(ids),AuthChallenge.status.in_(['PENDING','VERIFIED'])).limit(1)):return None
        if session.scalar(select(WorkflowJob.id).where(WorkflowJob.task_id.in_(ids),WorkflowJob.kind.in_(['PLAN','EXECUTE']),WorkflowJob.status.in_(['QUEUED','LEASED'])).limit(1)):return None
        return {'version':'customer-paused-plan-v1','paused':True,'task_id':task.id,
            'task_version':task.version,'confirmation_available':False}
    except (DomainError,TypeError,ValueError,KeyError):return None
