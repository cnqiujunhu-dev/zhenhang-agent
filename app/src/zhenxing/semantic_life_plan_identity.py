"""Completed life-plan identity for a new owned read or separately confirmed cancel."""
from sqlalchemy import select
from .contracts import DomainError
from .models import Task
from .conversation_models import ChatTurn
from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
from .life_plan_models import LifePlan
from .planning import canonical_hash

KIND='COMPLETED_LIFE_PLAN_IDENTITY'
KINDS=frozenset({'LIFE_PLAN_AUTHORIZE','LIFE_PLAN_CANCEL'})

def _deny():
    raise DomainError('SEMANTIC_LIFE_PLAN_IDENTITY_SOURCE_CHANGED','刚才办理的生日安排需要重新核对。',409)

def plan_identities(session,principal,thread,parent,before_sequence):
    from .conversation_provenance import current_plan_completed_write,validate_dialogue_actor_epoch
    from .semantic_proposal import validate_semantic_source_snapshot
    owned=lambda row:row is not None and (row.user_id,row.sandbox_id)==(principal.user_id,principal.sandbox_id)
    if not owned(thread) or not owned(parent) or thread.status!='ACTIVE' or parent.state!='SUCCEEDED' \
            or getattr(principal,'family_context',None) or type(before_sequence)is not int:return []
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,
        RuntimeOperation.active==1,RuntimeOperation.status=='SUCCEEDED',RuntimeOperation.action_kind.in_(KINDS))))
    if not operations:return []
    record=session.get(RuntimeRecord,parent.id);metadata=record.metadata_json if record else {}
    native=metadata.get('cloud_semantic_intent')or{};origin=session.get(ChatTurn,native.get('source_turn_id'))if native else None
    if not owned(origin) or origin.thread_id!=thread.id or origin.task_id!=parent.id or origin.status!='ANSWERED' \
            or origin.sequence>=before_sequence:return []
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==record.plan_version))
    if not snapshot or snapshot.plan_hash!=parent.plan_hash or canonical_hash(parent.plan_json)!=parent.plan_hash:_deny()
    validate_dialogue_actor_epoch(session,parent,metadata)
    validate_semantic_source_snapshot(session,principal,parent,metadata,snapshot)
    entries=[]
    for operation in operations:
        if not current_plan_completed_write(session,parent,operation):continue
        child=session.get(Task,operation.child_task_id)
        if not owned(child):_deny()
        nodes=child.plan_json.get('nodes',[])
        if len(nodes)!=1:_deny()
        identifier=(nodes[0].get('args')or{}).get('plan_id')
        plan=session.get(LifePlan,identifier)
        results=child.result_json.get('plans')or[]
        if not owned(plan) or len(results)!=1 or results[0].get('plan_id')!=plan.id:_deny()
        entries.append({'source_kind':KIND,'task_id':parent.id,'task_version':parent.version,'plan_version':record.plan_version,
            'operation_id':operation.id,'child_task_id':child.id,'receipt_hash':canonical_hash(child.result_json),
            'life_plan_id':plan.id,'conversation_id':thread.id,'before_sequence':before_sequence,'source_turn_id':origin.id,
            'field':'life_plan_id','value':plan.id,'identity_hash':canonical_hash({'id':plan.id,'user_id':plan.user_id,'sandbox_id':plan.sandbox_id}),
            'label':'刚才办理的生日安排 · '+plan.name,'identity_only':True,'bank_write_authorized':False})
    return entries

def checked_identity(session,principal,thread,entry,field,action_kind):
    if field!='life_plan_id' or action_kind not in {'LIFE_PLAN_VIEW','LIFE_PLAN_CANCEL'} or entry.get('source_kind')!=KIND:_deny()
    parent=session.get(Task,entry.get('task_id'))
    if parent is None or entry not in plan_identities(session,principal,thread,parent,entry.get('before_sequence')):_deny()
    return entry['value']
