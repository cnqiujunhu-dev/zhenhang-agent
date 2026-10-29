"""Verified FX receipt -> owned instrument identity -> fresh read; never money authority."""
from sqlalchemy import select
from .contracts import DomainError
from .models import Task,Card
from .conversation_models import ChatThread,ChatTurn
from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
from .planning import canonical_hash

KIND='COMPLETED_FX_CARD_IDENTITY'

def _deny():raise DomainError('SEMANTIC_FX_IDENTITY_SOURCE_CHANGED','换汇回执中的本人卡片身份需要重新核对。',409)

def card_identities(session,principal,thread,parent,before_sequence):
    from .fx_models import FxOperation,FxQuote
    from .semantic_status_facts import completed_transfer_facts
    from .card_identifiers import card_identifier_view
    from .conversation_provenance import validate_dialogue_actor_epoch
    from .semantic_proposal import validate_semantic_source_snapshot
    thread=session.get(ChatThread,thread) if isinstance(thread,str) else thread
    parent=session.get(Task,parent) if isinstance(parent,str) else parent
    owned=lambda row:row and (row.user_id,row.sandbox_id)==(principal.user_id,principal.sandbox_id)
    if not owned(thread) or not owned(parent) or thread.status!='ACTIVE' or parent.state!='SUCCEEDED' or getattr(principal,'family_context',None):return []
    record=session.get(RuntimeRecord,parent.id);metadata=record.metadata_json if record else {}
    native=metadata.get('cloud_semantic_intent')or{}
    origin=session.get(ChatTurn,native.get('source_turn_id')) if native else None
    if not owned(origin) or origin.thread_id!=thread.id or origin.task_id!=parent.id or origin.sequence>=before_sequence:return []
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==record.plan_version))
    if not snapshot or snapshot.plan_hash!=parent.plan_hash or canonical_hash(parent.plan_json)!=parent.plan_hash:_deny()
    validate_dialogue_actor_epoch(session,parent,metadata)
    validate_semantic_source_snapshot(session,principal,parent,metadata,snapshot)
    facts=completed_transfer_facts(session,principal,parent);values=[]
    for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,RuntimeOperation.active==1,
            RuntimeOperation.status=='SUCCEEDED',RuntimeOperation.action_kind.in_(['FX_EXCHANGE','FX_TRANSFER']))):
        child=session.get(Task,op.child_task_id)
        if not owned(child):_deny()
        result=child.result_json or {};posted=session.get(FxOperation,result.get('fx_operation_id')or result.get('operation_id'))
        if not posted:continue
        fact=next((x for x in facts if x['source_id']=='bank-receipt:'+posted.id),None)
        if not fact:_deny()
        quote=session.get(FxQuote,posted.quote_id);source=(quote.scope_json or {}).get('source') if quote else None
        card=session.get(Card,source.get('card_id')) if source else None
        if not owned(card) or card.account_id!=source.get('bank_account_id'):continue
        number=(card_identifier_view(session,card)or{}).get('card_number')
        if not number or number!=source.get('card_number'):_deny()
        values.append({'source_kind':KIND,'task_id':parent.id,'task_version':parent.version,'plan_version':record.plan_version,
            'operation_id':op.id,'child_task_id':child.id,'child_result_hash':canonical_hash(result),'fx_operation_id':posted.id,
            'receipt_hash':fact['receipt_hash'],'quote_scope_hash':quote.scope_hash,'card_id':card.id,'account_id':card.account_id,
            'conversation_id':thread.id,'source_turn_id':origin.id,'before_sequence':before_sequence,
            'field':'card_number','value':number,'label':f'刚才换汇使用的本人双币卡 · 尾号{number[-4:]}',
            'identity_only':True,'bank_write_authorized':False})
    return values

def checked_identity(session,principal,thread,entry,field,action_kind):
    if field!='card_number' or action_kind!='CARD_STATUS' or entry.get('source_kind')!=KIND:_deny()
    parent=session.get(Task,entry.get('task_id'))
    if not parent or entry not in card_identities(session,principal,thread,parent,entry['before_sequence']):_deny()
    return entry['value']
