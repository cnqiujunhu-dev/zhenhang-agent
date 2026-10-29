"""Retain a verified AA record identity after payments change its live totals.

Only a fresh owned AA_VIEW can consume it. No old amounts/status or payment
permission are carried into the new request.
"""
from .contracts import DomainError
from .planning import canonical_hash
from .models import Task
from .banking_extension_models import AACollectionRequest

KIND='HISTORICAL_AA_READ_IDENTITY'

def identities(session,principal,thread,parent,before_sequence):
    from .dialogue_references import _source
    from .aa_obligations import aa_result_fact_entities,owned_obligation
    if getattr(principal,'family_context',None) or thread.status!='ACTIVE' \
            or (thread.user_id,thread.sandbox_id)!=(principal.user_id,principal.sandbox_id):return []
    task,turn,record,snapshot,_trace,verified=_source(session,principal,thread,parent.id,before_sequence)
    if task.state!='SUCCEEDED':return []
    result=[]
    for op,child,_node in verified:
        if op.action_kind!='AA_VIEW':continue
        for entity in aa_result_fact_entities(child.result_json):
            field=entity['field'];value=entity['value']
            if field=='aa_collection_ref':
                row=session.get(AACollectionRequest,value.removeprefix('aa-collection:'))
                if not row or (row.user_id,row.sandbox_id)!=(principal.user_id,principal.sandbox_id):continue
            elif field=='aa_obligation_ref':
                owned_obligation(session,principal,value)
            else:continue
            result.append({'source_kind':KIND,'task_id':parent.id,'task_version':parent.version,
                'plan_version':record.plan_version,'snapshot_hash':snapshot.plan_hash,
                'operation_id':op.id,'child_task_id':child.id,'receipt_hash':canonical_hash(child.result_json),
                'conversation_id':thread.id,'source_turn_id':turn.id,'before_sequence':before_sequence,
                'field':field,'value':value,'label':entity['label'],'identity_only':True,'bank_write_authorized':False})
    return result

def checked_identity(session,principal,thread,entry,field,action_kind):
    if action_kind!='AA_VIEW' or entry.get('source_kind')!=KIND or field!=entry.get('field') \
            or field not in {'aa_collection_ref','aa_obligation_ref'}:
        raise DomainError('SEMANTIC_AA_IDENTITY_READ_ONLY','这项AA记录仅用于重新查询，请重新核对。',409)
    parent=session.get(Task,entry.get('task_id'))
    if not parent or entry not in identities(session,principal,thread,parent,entry.get('before_sequence')):
        raise DomainError('SEMANTIC_AA_IDENTITY_SOURCE_CHANGED','这项AA记录的来源需要重新查询。',409)
    return entry['value']
