"""Owned user analysis material, never account facts or banking permission.

The cloud selects an offered opaque entity for a read-only analysis. Original
messages are recovered and rechecked from the same live thread/actor epoch;
assistant text, RAG prose and model-generated summaries are not material.
"""
from copy import deepcopy
from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .dialogue_state import dialogue_actor_scope
from .dialogue_state_models import ConversationDialogueState
from .planning import canonical_hash
from .redaction import redact_credentials

VERSION = 'owned-user-risk-material-v1'
KIND = 'USER_ANALYSIS_MATERIAL'
MAX_TURNS = 6
MAX_CHARACTERS = 1800


def _deny():
    raise DomainError('SEMANTIC_ANALYSIS_MATERIAL_CHANGED',
        '前面这段分析材料暂时无法核对，请重新提供要分析的内容。没有执行资金操作。',409)


def _scope(session, principal, thread, turn):
    if not thread or not turn or thread.status != 'ACTIVE' or turn.thread_id != thread.id \
            or (thread.user_id, thread.sandbox_id) != (principal.user_id, principal.sandbox_id) \
            or (turn.user_id, turn.sandbox_id) != (principal.user_id, principal.sandbox_id):
        _deny()
    role, epoch = dialogue_actor_scope(session, principal)
    state = session.get(ConversationDialogueState, thread.id)
    start = (state.checkpoint_json or {}).get('epoch_start_sequence',0) if state else 0
    if state and (state.actor_scope_hash != epoch or turn.sequence <= start):
        _deny()
    return role['role'], epoch, start


def _source(row, role, epoch, start):
    d = row.diagnostics_json or {}
    receipt = d.get('semantic_receipt') or {}
    decision = receipt.get('decision') or {}
    if row.status != 'ANSWERED' or row.sequence <= start or row.error_code \
            or d.get('input_redacted') or d.get('credential_only') or d.get('semantic_processing_error') \
            or d.get('dialogue_actor_scope_hash') != epoch or d.get('actor_role_at_submission') != role \
            or receipt.get('status') != 'READY' or receipt.get('purpose')!='UNIFIED_SEMANTIC_UNDERSTANDING' \
            or receipt.get('source_message_hash') != canonical_hash({'message':row.message}) \
            or not isinstance(row.message,str) or not row.message.strip() \
            or len(row.message)>2000 or redact_credentials(row.message) != row.message:
        return None
    if 'FRAUD' not in decision.get('knowledge_topics',[]) and 'FRAUD_ANALYZE' not in decision.get('action_kinds',[]):
        return None
    return {'turn_id':row.id,'sequence':row.sequence,'message_hash':canonical_hash({'message':row.message}),
        'receipt_hash':canonical_hash(receipt)}


def build_analysis_material_catalog(session, principal, thread, turn):
    if turn.context_policy == 'INDEPENDENT':
        return []
    role, epoch, start = _scope(session, principal, thread, turn)
    from .semantic_context_history import bounded_context_turns
    rows = bounded_context_turns(session,principal,thread.id,select(ChatTurn).where(ChatTurn.thread_id==thread.id,
        ChatTurn.user_id==principal.user_id,ChatTurn.sandbox_id==principal.sandbox_id,
        ChatTurn.sequence<turn.sequence,ChatTurn.status=='ANSWERED')
        .order_by(ChatTurn.sequence.desc()).limit(MAX_TURNS))
    selected=[]; chars=0
    for row in rows:
        if getattr(row,'_context_source_unavailable',False):continue
        source=_source(row,role,epoch,start)
        if source is None:
            continue
        size=len(row.message)+(1 if selected else 0)
        if chars+size>MAX_CHARACTERS:
            break  # Whole messages only; never truncate into risk clearance.
        selected.append((source,row.message)); chars+=size
    if not selected:
        return []
    selected.reverse()
    material='\n'.join(message for _,message in selected)
    binding={'source_kind':KIND,'version':VERSION,'thread_id':thread.id,'analysis_turn_id':turn.id,
        'actor_scope_hash':epoch,'sources':[source for source,_ in selected],
        'value':material,'allowed_action':'FRAUD_ANALYZE','allowed_field':'context_message',
        'authority':'USER_WORDING_FOR_ANALYSIS_NOT_BANK_FACT_OR_WRITE_PERMISSION'}
    binding['source_hash']=canonical_hash(binding)
    public={'label':f'此前提供的风险话术（{len(selected)}条本人原话，仅供分析）',
        'kind':KIND,'domain':'FRAUD','field':'context_message','allowed_fields':['context_message'],
        'source_hash':binding['source_hash'],'ordinal':0}
    return [{'public':public,'binding':binding}]


def validate_analysis_material_entity(session, principal, thread, entry, field, action_kind, *, audit_only=False):
    # Even a genuine quoted instruction cannot be selected for a payment field.
    if action_kind!='FRAUD_ANALYZE' or field!='context_message' or not isinstance(entry,dict) \
            or entry.get('source_kind')!=KIND or entry.get('version')!=VERSION \
            or entry.get('allowed_action')!=action_kind or entry.get('allowed_field')!=field:
        _deny()
    payload=deepcopy(entry); expected=payload.pop('source_hash',None)
    if expected!=canonical_hash(payload) or entry.get('thread_id')!=thread.id:
        _deny()
    current=session.get(ChatTurn,entry.get('analysis_turn_id'))
    role,epoch,start=_scope(session,principal,thread,current)
    if current.context_policy=='INDEPENDENT' or entry.get('actor_scope_hash')!=epoch:
        _deny()
    refs=entry.get('sources')
    if not isinstance(refs,list) or not 1<=len(refs)<=MAX_TURNS:
        _deny()
    texts=[]; previous=start; seen=set()
    for ref in refs:
        if not isinstance(ref,dict) or ref.get('turn_id') in seen:
            _deny()
        row=session.get(ChatTurn,ref.get('turn_id'))
        if not row or row.thread_id!=thread.id or (row.user_id,row.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                or not previous<row.sequence<current.sequence or _source(row,role,epoch,start)!=ref:
            _deny()
        seen.add(row.id); previous=row.sequence; texts.append(row.message)
    value='\n'.join(texts)
    if len(value)>MAX_CHARACTERS or entry.get('value')!=value:
        _deny()
    return value


def analysis_material_sources(session, principal, thread, entry):
    """A consultation may cite user wording, not promote it into money facts."""
    value=validate_analysis_material_entity(session,principal,thread,entry,'context_message','FRAUD_ANALYZE')
    return [{'source_id':'material:risk:'+entry['source_hash'][:24],
        'title':'你此前提供的风险话术','title_en':'User-provided risk messages',
        'kind':KIND,'version':VERSION,'reference':'owned-user-analysis-material',
        'content':value,'content_en':value,
        'source_binding':deepcopy(entry),
        'authority':'USER_WORDING_FOR_ANALYSIS_NOT_BANK_FACT_OR_WRITE_PERMISSION'}]


def analysis_material_sources_current(session, principal, records):
    """Recheck the complete source before publishing or using history again."""
    for row in records:
        if not isinstance(row,dict):
            return False
        if row.get('kind')!=KIND and not str(row.get('source_id','')).startswith('material:risk:'):
            continue
        try:
            entry=row.get('source_binding')
            if not isinstance(entry,dict):
                return False
            thread=session.get(ChatThread,entry.get('thread_id'))
            expected=analysis_material_sources(session,principal,thread,entry)
            if expected!=[row]:
                return False
        except (DomainError,ValueError,TypeError,KeyError,AttributeError):
            return False
    return True


def semantic_analysis_message(session, task, action, context):
    """Return verified material only from the compiled parent provenance.

    The legacy path retains its authoritative full original user message.
    Model action slots alone never select or override an analysis message.
    """
    from .models import Task
    from .runtime_models import RuntimeRecord
    from .services import Principal
    from .semantic_proposal import load_verified_semantic_intent
    parent_id=(context or {}).get('source_root_task_id')
    parent=session.get(Task,parent_id) if parent_id else None
    if not parent or (parent.user_id,parent.sandbox_id)!=(task.user_id,task.sandbox_id):
        return None
    record=session.get(RuntimeRecord,parent.id)
    proof=load_verified_semantic_intent(session,parent,record.metadata_json if record else {})
    if proof is None:
        return None
    supplied=action.model_dump() if hasattr(action,'model_dump') else action
    matched=[item for item in proof.expected_proposal.actions
        if item.kind=='FRAUD_ANALYZE' and item.model_dump()==supplied]
    if len(matched)!=1:
        _deny()
    principal=Principal(parent.user_id,parent.sandbox_id)
    thread=session.get(ChatThread,proof.binding['conversation_id'])
    decision=proof.binding['decision']; entries=proof.binding['offered_context'].get('entity_bindings',{})
    selected=[]
    for change in decision.get('changes',[]):
        ref=change.get('entity_ref')
        if change.get('field')!='context_message' or not ref:
            continue
        entry=entries.get(ref)
        if not entry or entry.get('source_kind')!=KIND:
            continue
        value=validate_analysis_material_entity(session,principal,thread,entry,'context_message','FRAUD_ANALYZE')
        if value!=matched[0].slots.context_message:
            _deny()
        selected.append((entry,value))
    if len(selected)>1:
        _deny()
    material=(selected[0][1]+'\n'+parent.message) if selected else parent.message
    if len(material)>4000:
        _deny()
    return {'message':material,'material_source_count':len(selected[0][0]['sources']) if selected else 1,
        'analysis_kind':'USER_MESSAGE_ANALYSIS','authority':'ANALYSIS_ONLY_NO_BANKING_AUTHORIZATION'}
