"""Production natural-language entry: cloud semantics, then controlled services.

No phrase/FAQ selector or rule-model fallback creates a banking task here.
The semantic result never consumes a financial confirmation or invokes a write.
"""
from copy import deepcopy
from sqlalchemy import select
from .contracts import DomainError
from .conversation_models import ChatThread,ChatTurn
from .models import Task,utcnow
from .runtime_models import RuntimeRecord
from .planning import canonical_hash
from .intelligence import READ_KINDS


def _leased(session,turn_id,identity):
    turn=session.get(ChatTurn,turn_id)
    if not turn:return None,None
    thread=session.scalar(select(ChatThread).where(ChatThread.id==turn.thread_id).with_for_update().execution_options(populate_existing=True))
    turn=session.scalar(select(ChatTurn).where(ChatTurn.id==turn_id).with_for_update().execution_options(populate_existing=True))
    if not thread or not turn or turn.thread_id!=thread.id or thread.status!='ACTIVE' \
            or turn.status!='LEASED' or turn.lease_owner!=identity:return None,None
    return thread,turn


def _failure(turn,code,english=False):
    from .conversation_turn_state import _finish
    from .semantic_recovery import recovery_for
    message,recovery=recovery_for(turn,code,english=english)
    _finish(turn,'NEED_CONTEXT',message)
    turn.answer=message
    turn.response_meta_json={**(turn.response_meta_json or {}),'recovery':recovery}
    turn.diagnostics_json={**turn.diagnostics_json,'semantic_processing_error':code,'bank_task_created':False,'bank_write_authorized':False}


def _focus(session,principal,offered,decision):
    from .services import owned_task
    entry=offered['frame_bindings'].get(decision.focus_ref)
    return owned_task(session,principal,entry['task_id'],lock=True) if entry else None


def _retain_memory(session,principal,thread,turn,result,offered):
    """Commit a successful reply and its user memory atomically for FIFO turns."""
    if not result.decision.memory_updates:return
    from .conversation_memory import apply_memory_updates
    observation=apply_memory_updates(session,principal,thread,turn,result,offered)
    turn.diagnostics_json={**turn.diagnostics_json,'conversation_memory_update':observation}
    if observation.get('status')=='INELIGIBLE_SOURCE':
        # Never promise durable memory for a quote, attachment or bank command.
        note=('This information has not been saved as an ongoing preference.' if
            (turn.diagnostics_json or {}).get('reply_language')=='en' else '这条内容没有作为后续偏好保存。')
        turn.answer=note if turn.mode=='FEEDBACK' else (turn.answer or '')+'\n'+note


def _reunderstanding_context_current(session,principal,thread,turn,offered,policy,role):
    """Compare full sources; never refresh the model input to repair a failure."""
    from .authentication import as_utc
    from .permissions import require_banking_actor
    from .dialogue_state import dialogue_actor_scope
    from .model_routing import read_routing_policy
    from .semantic_context import semantic_context
    from .semantic_router import semantic_input_hash
    from .semantic_reunderstanding import policy_hash
    if not turn.lease_expires_at or as_utc(turn.lease_expires_at)<=as_utc(utcnow()):return 'LEASE_CHANGED'
    if turn.task_id or turn.diagnostics_json.get('semantic_receipt'):return 'APPLICATION_ALREADY_PRESENT'
    if require_banking_actor(session,principal)!=role or dialogue_actor_scope(session,principal)[1]!=offered['actor_scope_hash']:
        return 'ACTOR_CHANGED'
    if policy_hash(read_routing_policy(session))!=policy_hash(policy):return 'POLICY_CHANGED'
    fresh=semantic_context(session,principal,thread,turn)
    if fresh['thread_version']!=offered['thread_version'] or fresh['context_hash']!=offered['context_hash'] \
            or semantic_input_hash(**fresh['model'])!=semantic_input_hash(**offered['model']):return 'SOURCE_CHANGED'
    return None


def _reunderstand_once(factory,gateway,turn_id,identity,offered,policy,role,result,budget):
    """Archive first result, reserve at most one second route before any apply."""
    from .conversation_turn_state import _turn_principal
    from .semantic_router import route_semantic_turn,semantic_input_hash
    from .semantic_reunderstanding import (KEY,record_attempt,reserve_second,verified_feedback)
    feedback=result.reunderstanding_feedback
    retry=False
    with factory.begin() as session:
        thread,turn=_leased(session,turn_id,identity)
        if turn is None:return result
        ledger=record_attempt(turn.diagnostics_json[KEY],result,now=utcnow())
        if len(ledger['attempts'])==1:ledger['automatic_eligible']=feedback is not None
        turn.diagnostics_json={**turn.diagnostics_json,KEY:ledger,'semantic_diagnostics':deepcopy(result.diagnostics)}
    # First evidence commits before any access/source revalidation can fail.
    if feedback is None:return result
    with factory.begin() as session:
        thread,turn=_leased(session,turn_id,identity)
        if turn is None:return result
        current=turn.diagnostics_json[KEY]
        if current!=ledger:return result  # Row lock + exact prior ledger is the CAS boundary.
        reason=None
        if feedback is not None and ledger['reserved_attempts']==1:
            principal=_turn_principal(session,turn)
            reason=_reunderstanding_context_current(session,principal,thread,turn,offered,policy,role)
            if reason is None and verified_feedback(feedback,result=result,policy=policy,
                    input_hash=semantic_input_hash(**offered['model']),source_hash=offered['message_hash']):
                if any(frame['state'] in {'UNKNOWN','AUTHORIZED','EXECUTING','PARTIAL'} for frame in offered['model']['frames']):
                    reason='UNRESOLVED_BANK_WORK'
                else:
                    reserved=reserve_second(ledger,policy,now=utcnow(),kind='VALIDATED_STRUCTURE')
                    if reserved is not None:ledger=reserved;retry=True
                    else:reason='BUDGET_EXHAUSTED'
            elif reason is None:reason='FEEDBACK_INVALID'
        if reason:ledger={**ledger,'stop_reason':reason}
        turn.diagnostics_json={**turn.diagnostics_json,KEY:ledger,'semantic_diagnostics':deepcopy(result.diagnostics)}
    if not retry:return result
    # No database transaction covers network I/O. Each route retains count==1.
    result=route_semantic_turn(gateway,policy=policy,**offered['model'],
        _attempt_budget=budget,_validation_feedback=feedback)
    with factory.begin() as session:
        _thread,turn=_leased(session,turn_id,identity)
        if turn is not None:
            ledger=record_attempt(turn.diagnostics_json[KEY],result,now=utcnow())
            turn.diagnostics_json={**turn.diagnostics_json,KEY:ledger,'semantic_diagnostics':deepcopy(result.diagnostics)}
    return result


def process_semantic_turn(factory,gateway,turn_id,identity):
    from .conversation_turn_state import _finish,_turn_principal,_turn_language
    from .semantic_context import semantic_context
    from .semantic_router import (route_semantic_turn,serialize_semantic_receipt,is_verified_semantic_result,
        semantic_input_hash,is_completed_read_followup)
    from .model_routing import read_routing_policy
    from .dialogue_state import dialogue_actor_scope
    from .permissions import require_banking_actor
    from .semantic_reunderstanding import (KEY,enabled,valid_ledger,start_ledger,reserve_second,
        attempt_budget,policy_hash)
    explanation=None
    stage='CONTEXT'
    try:
        with factory.begin() as session:
            thread,turn=_leased(session,turn_id,identity)
            if turn is None:return True
            principal=_turn_principal(session,turn);role=require_banking_actor(session,principal)
            if turn.diagnostics_json.get('credential_only'):
                _finish(turn,'CONFIRM_HINT','凭据已脱敏，聊天不会使用密码或验证码。请在专用控件操作。')
                return True
            if turn.diagnostics_json.get('semantic_cloud_reserved'):
                _failure(turn,'SEMANTIC_PREVIOUS_OUTCOME_UNKNOWN_NO_REISSUE',_turn_language(turn)=='en')
                return True
            offered=semantic_context(session,principal,thread,turn)
            policy=read_routing_policy(session);locale=_turn_language(turn)
            budget=None
            ledger=turn.diagnostics_json.get(KEY)
            if KEY in turn.diagnostics_json:
                # Only a normal, explicit manual retry can reach this path;
                # an interrupted network reservation was rejected above.
                if not valid_ledger(ledger) or ledger['input_hash']!=semantic_input_hash(**offered['model']) \
                        or ledger['source_message_hash']!=offered['message_hash'] \
                        or ledger.get('context_hash')!=offered['context_hash'] \
                        or ledger.get('thread_version')!=offered['thread_version'] \
                        or ledger.get('actor_scope_hash')!=offered['actor_scope_hash'] \
                        or ledger['policy_hash']!=policy_hash(policy):
                    _failure(turn,'MODEL_INVALID',locale=='en');return True
                ledger=reserve_second(ledger,policy,now=utcnow())
                if ledger is None:
                    _failure(turn,'MODEL_INVALID',locale=='en');return True
            elif enabled(policy) and not turn.diagnostics_json.get('understanding_retry_history'):
                ledger=start_ledger(policy,input_hash=semantic_input_hash(**offered['model']),
                    source_hash=offered['message_hash'],now=utcnow(),context_hash=offered['context_hash'],
                    thread_version=offered['thread_version'],actor_scope_hash=offered['actor_scope_hash'])
            if ledger is not None:
                from .authentication import as_utc
                if not turn.lease_expires_at or as_utc(turn.lease_expires_at)<=as_utc(utcnow()):
                    _failure(turn,'SEMANTIC_PREVIOUS_OUTCOME_UNKNOWN_NO_REISSUE',locale=='en');return True
                budget=attempt_budget(ledger,policy,now=utcnow())
                turn.diagnostics_json={**turn.diagnostics_json,KEY:ledger}
            turn.diagnostics_json={**turn.diagnostics_json,'semantic_cloud_reserved':True,
                'semantic_entry_version':'unified-cloud-semantics-v1','bank_write_authorized':False,
                'semantic_context_budget':offered.get('context_budget_diagnostics',{}),
                'semantic_offered_frames':[{'frame_ref':frame['frame_ref'],'state':frame['state'],
                    'kinds':[action['kind'] for action in frame['actions']],
                    'continuation_allowed':frame['continuation_allowed'],'paused':frame['paused']}
                    for frame in offered['model']['frames']]}
        stage='SEMANTIC_MODEL'
        result=route_semantic_turn(gateway,policy=policy,**offered['model'],
            **({'_attempt_budget':budget} if budget is not None else {}))
        if budget is not None:
            result=_reunderstand_once(factory,gateway,turn_id,identity,offered,policy,role,result,budget)
        stage='SEMANTIC_APPLY'
        with factory.begin() as session:
            thread,turn=_leased(session,turn_id,identity)
            if turn is None:return True
            principal=_turn_principal(session,turn);fresh_role=require_banking_actor(session,principal)
            turn.diagnostics_json={**turn.diagnostics_json,'semantic_diagnostics':result.diagnostics}
            if budget is not None:
                reason=_reunderstanding_context_current(session,principal,thread,turn,offered,policy,role)
                if reason:
                    turn.diagnostics_json={**turn.diagnostics_json,KEY:{**turn.diagnostics_json[KEY],'stop_reason':reason}}
                    _failure(turn,'SEMANTIC_ACTOR_CHANGED' if reason=='ACTOR_CHANGED' else 'SEMANTIC_EXPLANATION_SOURCE_CHANGED',locale=='en')
                    return True
            if fresh_role!=role or dialogue_actor_scope(session,principal)[1]!=offered['actor_scope_hash']:
                _failure(turn,'SEMANTIC_ACTOR_CHANGED',locale=='en');return True
            if result.status!='READY':
                _failure(turn,result.status,locale=='en');return True
            if not is_verified_semantic_result(result,input_hash=semantic_input_hash(**offered['model'])):
                _failure(turn,result.status,locale=='en');return True
            decision=result.decision;mode=decision.mode
            from .conversation_memory import validate_memory_context
            if 'memory' in offered['model']:
                validate_memory_context(session,principal,thread,turn,offered)
            turn.diagnostics_json={**turn.diagnostics_json,'semantic_receipt':serialize_semantic_receipt(result),
                'semantic_mode':mode,'semantic_selected_actions':list(decision.action_kinds)}
            from .semantic_task_focus import checked_focus
            task=checked_focus(session,principal,offered,decision,turn)
            if 'OPERATION_SCOPE' in decision.question_aspects:
                from .semantic_query_purpose import finish_operation_scope
                finish_operation_scope(session,principal,turn,decision,offered)
                return True
            if mode=='CLARIFY':
                from .semantic_query_purpose import checked_clarification
                evidence=checked_clarification(decision,offered,turn.message)
                _finish(turn,'NEED_CONTEXT',decision.clarification)
                turn.answer=decision.clarification
                turn.diagnostics_json={**turn.diagnostics_json,'clarification_evidence':evidence}
                _retain_memory(session,principal,thread,turn,result,offered)
                return True
            if mode=='STATUS':
                if task is None:_failure(turn,'SEMANTIC_STATUS_FOCUS_MISSING',locale=='en');return True
                from .conversation_turn_state import _finish_status
                _finish_status(session,principal,turn,task);return True
            if mode in {'PAUSE','CANCEL'}:
                if task is None:_failure(turn,'SEMANTIC_CONTROL_FOCUS_MISSING',locale=='en');return True
                from .semantic_schedule_cancel_contract import offered_schedule_cancel,cancel_registered_schedule
                if mode=='CANCEL' and offered_schedule_cancel(decision,offered['model']):
                    task,count=cancel_registered_schedule(session,principal,task)
                    text=(f'Cancelled the remaining {count} scheduled transfers. Completed transfers are unchanged.' if locale=='en'
                        else f'已取消后续{count}次预约转账，已完成的转账保持不变。')
                    _finish(turn,'FEEDBACK',text,task,'CURRENT');turn.answer=text
                    turn.diagnostics_json={**turn.diagnostics_json,'semantic_control':mode,
                        'registered_schedule_future_cancel':True,'cancelled_future_occurrences':count,
                        'preserve_goal_context':True,'bank_write_authorized':False}
                    return True
                from .semantic_lifecycle import pause_semantic_task
                pause_semantic_task(session,principal,thread,turn,task)
                if mode=='CANCEL':
                    from .runtime import cancel_runtime
                    task=cancel_runtime(session,principal,task.id,task.version)
                    record=session.get(RuntimeRecord,task.id)
                    record.metadata_json={k:v for k,v in record.metadata_json.items() if k!='semantic_pause'}
                text=("I've paused this unexecuted request and kept the details." if mode=='PAUSE' else "I've closed this unexecuted request. Existing paid benefits and completed records are kept.") if locale=='en' else (
                    '先暂停这项尚未执行的需求，已说清的信息会保留；想继续时告诉我。' if mode=='PAUSE' else '已停止这次尚未执行的办理，已有权益和完成记录会保留。')
                _finish(turn,'FEEDBACK',text,task,'CURRENT');turn.answer=text
                turn.diagnostics_json={**turn.diagnostics_json,'semantic_control':mode,'preserve_goal_context':True,'bank_write_authorized':False}
                return True
            if mode in {'REQUEST','CONTINUE','REVISE','RESUME'}:
                from .semantic_proposal import build_semantic_intent_binding,METADATA_KEY
                from .runtime import accept_task,clarify_runtime
                # Adjusting a completed READ is a new query with an explicitly
                # bound original scope. It never reopens the old task, restores
                # its confirmations, or makes completed WRITE work mutable.
                read_clone=is_completed_read_followup(decision,offered['model'])
                if mode=='REQUEST' or read_clone:
                    if any(kind not in READ_KINDS for kind in decision.action_kinds):
                        for entry in offered['frame_bindings'].values():
                            current=session.get(Task,entry['task_id'])
                            if current and current.state in {'UNKNOWN','AUTHORIZED','EXECUTING'}:
                                _finish(turn,'NEED_CONTEXT','先核对原业务结果，我不会在结果未确定时重复准备资金操作。',current,'CURRENT');return True
                    task=accept_task(session,principal,turn.message,'chat:'+turn.id)
                    binding=build_semantic_intent_binding(session,principal,thread,turn,result,offered,parent=task)
                    relationship='CREATED';reply_mode='NEW_TASK'
                else:
                    if task is None:_failure(turn,'SEMANTIC_CONTINUATION_FOCUS_MISSING',locale=='en');return True
                    from .semantic_lifecycle import require_unexecuted,current_semantic_pause
                    record,_ops=require_unexecuted(session,principal,task)
                    binding=build_semantic_intent_binding(session,principal,thread,turn,result,offered,parent=task)
                    if mode in {'CONTINUE','REVISE'} and current_semantic_pause(session,principal,task):
                        record.metadata_json={**record.metadata_json,'semantic_pause':{**record.metadata_json['semantic_pause'],
                            'pending_binding':binding,'pending_turn_id':turn.id}}
                        _finish(turn,'FEEDBACK','补充已记下，这项需求仍先暂停；想继续时告诉我。',task,'CURRENT')
                        turn.diagnostics_json={**turn.diagnostics_json,'preserve_goal_context':True,'bank_write_authorized':False};return True
                    task=clarify_runtime(session,principal,task.id,turn.message,task.version,revise=True)
                    relationship='CONTINUED';reply_mode='REVISE'
                record=session.get(RuntimeRecord,task.id)
                clean={k:v for k,v in record.metadata_json.items() if k not in {'owned_user_dialogue','dialogue_slot_update',
                    'dialogue_result_reference','pending_subject_selection','dialogue_pause','semantic_pause','conversation_context'}}
                record.metadata_json={**clean,METADATA_KEY:binding,'reply_language':locale,
                    'conversation_context':{'conversation_id':thread.id,'turn_id':turn.id,'original_user_message':turn.message,
                        'authority':'VERIFIED_CLOUD_SEMANTIC_USER_INTENT','bank_write_authorized':False}}
                thread.current_task_id=task.id;thread.version+=1
                _finish(turn,reply_mode,'好的，我会按你的需求核对资料并准备；需要你确认时会提示。',task,relationship)
                turn.diagnostics_json={**turn.diagnostics_json,'bank_task_created':mode=='REQUEST' or read_clone,
                    'completed_read_followup':read_clone,'bank_write_authorized':False}
                return True
            if mode == 'FEEDBACK' and not decision.entity_refs and decision.focus_ref is None \
                    and decision.retrieval_query is None and not decision.knowledge_topics \
                    and not decision.question_aspects and not decision.rule_aspects:
                # The model has already understood this as acknowledgement,
                # not a new question. No unsupported explanation/knowledge
                # round-trip is needed and no task or permission is changed.
                answer = 'Understood. You can tell me whenever you need anything else.' if locale == 'en' else '好的，有需要可以继续告诉我。'
                _finish(turn, 'FEEDBACK', answer)
                turn.diagnostics_json = {**turn.diagnostics_json, 'bank_task_created': False,
                    'bank_write_authorized': False, 'preserve_goal_context': True,
                    'feedback_presentation': 'NON_OPERATIONAL_ACKNOWLEDGEMENT'}
                _retain_memory(session, principal, thread, turn, result, offered)
                return True
            from .semantic_knowledge import public_semantic_sources
            from .completed_transfer_status_summary import selected_completed_transfer_consultation
            transfer_answer=selected_completed_transfer_consultation(session,principal,thread,turn,decision,offered)
            if transfer_answer is not None:
                _finish(turn,'CONSULT',transfer_answer['answer'])
                turn.answer=transfer_answer['answer']
                turn.response_meta_json={**(turn.response_meta_json or {}),
                    'transfer_receipt_summary':{key:value for key,value in transfer_answer.items() if key!='answer'}}
                turn.diagnostics_json={**turn.diagnostics_json,
                    'transfer_receipt_consultation':transfer_answer['version'],
                    'bank_task_created':False,'bank_write_authorized':False,'preserve_goal_context':True}
                return True
            from .merchant_catalog_consultation import selected_merchant_consultation
            merchant_answer=selected_merchant_consultation(session,principal,decision,offered)
            if merchant_answer is not None:
                _finish(turn,'CONSULT',merchant_answer['answer'],sources=[merchant_answer['source']])
                turn.answer=merchant_answer['answer']
                turn.diagnostics_json={**turn.diagnostics_json,'merchant_catalog_consultation':merchant_answer['diagnostics'],
                    'bank_task_created':False,'bank_write_authorized':False,'preserve_goal_context':True}
                return True
            from .product_knowledge import source_fingerprint
            records=public_semantic_sources(session,decision.knowledge_topics,role,locale)
            from .conversation_turn_state import _attachment_sources
            records+=_attachment_sources(session,principal,turn.reviewed_evidence_refs_json)
            from .semantic_source_bundle import OFFERED_V2,SELECTOR_VERSION,capture_bundle
            compact_sources=offered.get('schema_version')==OFFERED_V2
            selection={'topics':list(decision.knowledge_topics),
                'selected_objects':bool(decision.entity_refs),'selected_records':[]}
            if not compact_sources:selection['offered_context']=deepcopy(offered)
            from .semantic_goal_source import selected_goal_calculation_source
            selected_goal=selected_goal_calculation_source(session,principal,thread,turn,decision,offered)
            if selected_goal is not None:
                goal_source,goal_scope=selected_goal
                records.append(goal_source);selection['goal_calculation_scope']=goal_scope
            from .semantic_knowledge import select_public_family_policy
            public_family=select_public_family_policy(session,decision,records,role,turn.reviewed_evidence_refs_json,locale)
            if public_family is not None:
                records=public_family;selection['family_policy_scope']=True
            from .public_wealth_knowledge import select_public_wealth_scope
            public_wealth=select_public_wealth_scope(session,principal,thread,turn,result,offered,records,locale)
            if public_wealth is not None:
                records=public_wealth['records']
                selection['public_wealth_scope']=public_wealth['scope']
                selection['public_wealth_records']=deepcopy(records)
            if compact_sources:
                public_records=selection.pop('public_wealth_records',[])
                selection.update(version=SELECTOR_VERSION,bundle_ref=capture_bundle(session,principal,thread,turn,offered,
                    public_records=public_records),public_wealth_record_refs=[{'source_id':row['source_id'],
                    'source_hash':canonical_hash(row)} for row in public_records])
                selection.pop('selected_records',None)
            if decision.entity_refs:
                from .semantic_proposal import semantic_selected_object_sources
                selected=semantic_selected_object_sources(session,principal,thread,turn,result,offered)
                records+=selected
                if compact_sources:
                    selection['selected_source_refs']=[{'source_id':row['source_id'],'source_hash':canonical_hash(row)} for row in selected]
                else:selection['selected_records']=deepcopy(selected)
            # SELECTs in the source validator can autoflush the turn. Build the
            # complete selection first, then assign an independent JSON value
            # once: nested in-place mutation is not tracked by plain JSON.
            turn.diagnostics_json={**deepcopy(turn.diagnostics_json),
                'semantic_knowledge_selection':deepcopy(selection),'bank_task_created':False,
                'family_permission_consultation':selection.get('family_policy_scope') is True}
            # An explicitly model-selected wealth RULES topic is already a
            # registered public policy scope; it need not repeat the same
            # subject in an optional retrieval object. Never infer from source IDs.
            rule_subject=decision.retrieval_query.subject_ref if decision.retrieval_query else (
                'topic:WEALTH' if decision.knowledge_topics==['WEALTH'] and 'RULES' in decision.question_aspects else None)
            explanation=(turn.message,records,offered['model']['history'],policy,source_fingerprint(records),locale,mode,
                list(decision.question_aspects),rule_subject,list(decision.rule_aspects),
                list(decision.retrieval_query.selected_public_refs) if decision.retrieval_query else [])
        if explanation:
            from .consultation_gateway import explain
            message,records,history,policy,fingerprint,locale,mode,question_aspects,public_subject_ref,rule_aspects,selected_public_refs=explanation
            from .knowledge_runtime import retrieve_dialogue_knowledge,knowledge_sources_current
            from .semantic_knowledge import exact_public_family_policy
            from .dialogue_result_aspects import selected_query_fact_catalog,selected_public_price_facts
            structured=selected_query_fact_catalog(records) or selected_public_price_facts(records)
            from .capability_consultation_facts import is_capability_subject
            if is_capability_subject(public_subject_ref):
                retrieved,retrieval_meta=[],{'retrieval_mode':'REGISTERED_CAPABILITY_DIRECTORY',
                    'knowledge_status':'CURRENT_ROLE_FILTERED_CAPABILITY_FACTS'}
            elif structured and ('RULES' not in question_aspects or set(rule_aspects) in ({'SETTLEMENT'},{'ELIGIBILITY'})):
                retrieved,retrieval_meta=[],{'retrieval_mode':'VERIFIED_STRUCTURED_QUERY_FACTS',
                    'knowledge_status':'REGISTERED_READ_AND_SETTLEMENT_CONTRACT'}
            elif exact_public_family_policy(records,selection.get('family_policy_scope')):
                retrieved,retrieval_meta=[],{'retrieval_mode':'SCOPED_POLICY','knowledge_status':'PUBLIC_POLICY_SCOPE_ONLY'}
            elif selection.get('public_wealth_scope') in {'NAMED_REGISTERED_PUBLIC_PRODUCT','NAMED_REGISTERED_PUBLIC_PRODUCTS','GENERAL_PUBLIC_WEALTH_RULES'}:
                retrieved,retrieval_meta=[],{'retrieval_mode':'SCOPED_PUBLIC_PRODUCT',
                    'knowledge_status':selection['public_wealth_scope']}
            else:
                stage='KNOWLEDGE_RETRIEVAL'
                from .semantic_retrieval_query import compile_retrieval_query
                from .semantic_router import prepare_semantic_input
                retrieval_plan=compile_retrieval_query(decision.retrieval_query,prepare_semantic_input(**offered['model']),
                    mode=mode,knowledge_topics=decision.knowledge_topics)
                retrieved,retrieval_meta=retrieve_dialogue_knowledge(retrieval_plan['query'],role)
                retrieval_meta={**retrieval_meta,'semantic_retrieval_query':retrieval_plan['diagnostics']}
            records=records+retrieved
            stage='CONSULTATION_MODEL'
            memory=offered['model'].get('memory',[])
            answer,diagnostic=explain(gateway,message,records,history,policy,reply_language=locale,
                **({'dialogue_mode':mode} if mode=='OUT_OF_SCOPE' else {}),
                **({'memory':memory} if memory else {}),
                **({'selected_public_refs':selected_public_refs} if selected_public_refs else {}),
                **({'question_aspects':question_aspects,'public_subject_ref':public_subject_ref,'rule_aspects':rule_aspects} if question_aspects or public_subject_ref or rule_aspects else {}))
            stage='CONSULTATION_COMMIT'
            with factory.begin() as session:
                thread,turn=_leased(session,turn_id,identity)
                if turn is None:return True
                principal=_turn_principal(session,turn)
                if dialogue_actor_scope(session,principal)[1]!=offered['actor_scope_hash']:
                    _failure(turn,'SEMANTIC_ACTOR_CHANGED',locale=='en');return True
                if 'memory' in offered['model']:
                    validate_memory_context(session,principal,thread,turn,offered)
                if decision.retrieval_query is not None:
                    from .semantic_retrieval_query import revalidate_retrieval_query
                    from .semantic_router import prepare_semantic_input
                    fresh_offered=semantic_context(session,principal,thread,turn)
                    if not revalidate_retrieval_query(decision.retrieval_query,prepare_semantic_input(**offered['model']),
                            prepare_semantic_input(**fresh_offered['model']),mode=mode,knowledge_topics=decision.knowledge_topics):
                        _failure(turn,'SEMANTIC_EXPLANATION_SOURCE_CHANGED',locale=='en');return True
                from .semantic_knowledge import current_semantic_consultation_sources
                from .product_knowledge import source_fingerprint,sources_still_authorized
                fresh=current_semantic_consultation_sources(session,principal,thread,turn)
                from .conversation_turn_state import _attachment_sources
                fresh+=_attachment_sources(session,principal,turn.reviewed_evidence_refs_json)
                if source_fingerprint(fresh)!=fingerprint or not sources_still_authorized(session,principal,records) or not knowledge_sources_current(retrieved,role):
                    _failure(turn,'SEMANTIC_EXPLANATION_SOURCE_CHANGED',locale=='en');return True
                response_mode=mode if mode in {'DISCOVERY','GREETING','FEEDBACK','OUT_OF_SCOPE'} else 'CONSULT'
                _finish(turn,response_mode,answer,sources=records);turn.answer=answer
                turn.diagnostics_json={**turn.diagnostics_json,'semantic_explanation':diagnostic,
                    **retrieval_meta,'bank_task_created':False,'bank_write_authorized':False,'preserve_goal_context':True}
                if selection.get('goal_calculation_scope'):
                    turn.diagnostics_json={**turn.diagnostics_json,'preserve_read_goal_context':True,
                        'read_goal_task_id':selection['goal_calculation_scope']['task_id'],'goal_calculation_source_verified':True}
                if mode in {'DISCOVERY','GREETING'}:
                    from .capability_catalog import suggestion_catalog
                    from .customer_capability_data import CUSTOMER_DIRECTORY_VERSION
                    turn.suggestions_json=list(suggestion_catalog(role,locale).values())[:3]
                    turn.response_meta_json={**turn.response_meta_json,'suggestion_source':'CUSTOMER_CAPABILITY_CATALOG',
                        'suggestion_catalog_version':CUSTOMER_DIRECTORY_VERSION,'dialogue_kind':mode,
                        'suggestion_role':role,'preserve_goal_context':True}
                _retain_memory(session,principal,thread,turn,result,offered)
    except DomainError as error:
        with factory.begin() as session:
            _thread,turn=_leased(session,turn_id,identity)
            if turn is not None:
                if error.code=='TEMPORAL_SOURCE_REQUIRED':
                    _failure(turn,error.code,_turn_language(turn)=='en')
                    turn.answer=('Please restate the affected date and time explicitly. Its original source cannot be verified; it will not be recalculated from today.'
                        if _turn_language(turn)=='en' else error.message)
                    return True
                if error.code in {'CHAT_SELECTED_TASK_MISMATCH','CHAT_SELECTED_TASK_CHANGED'}:
                    _failure(turn,error.code,_turn_language(turn)=='en')
                else:
                    _finish(turn,'NEED_CONTEXT',error.message);turn.answer=error.message
                failed_semantic={}
                if error.code=='SEMANTIC_SOURCE_CONTEXT_TOO_LARGE' and 'offered' in locals():
                    selected_refs=set(result.decision.entity_refs) if 'result' in locals() and result.decision else set()
                    kinds=sorted({entity['read_kind'] for entity in offered['model'].get('entities',[])
                        if entity.get('entity_ref') in selected_refs and entity.get('read_kind') in READ_KINDS})
                    failed_semantic['source_bundle_unavailable']={'version':'source-bundle-availability-v1',
                        'status':'SOURCE_UNAVAILABLE','read_kinds':kinds,'bank_write_authorized':False}
                if 'result' in locals():
                    failed_semantic['semantic_diagnostics']=result.diagnostics
                    from .semantic_router import is_verified_semantic_result,serialize_semantic_receipt
                    if is_verified_semantic_result(result):
                        failed_semantic['semantic_receipt']=serialize_semantic_receipt(result)
                        failed_semantic['semantic_mode']=result.decision.mode
                turn.diagnostics_json={**turn.diagnostics_json,'semantic_processing_error':error.code,'bank_write_authorized':False,
                    'bank_task_created':False,**failed_semantic}
    except Exception as error:
        with factory.begin() as session:
            _thread,turn=_leased(session,turn_id,identity)
            if turn is not None:
                from .operation_diagnostics import failure_observation,record_failure
                observation=failure_observation(error,stage=stage,correlation_id=turn.id,code='SEMANTIC_PROCESSING_FAILED')
                _failure(turn,'SEMANTIC_PROCESSING_FAILED',_turn_language(turn)=='en')
                failed_semantic = {}
                if 'result' in locals():
                    failed_semantic['semantic_diagnostics'] = result.diagnostics
                    if is_verified_semantic_result(result):
                        failed_semantic['semantic_receipt'] = serialize_semantic_receipt(result)
                        failed_semantic['semantic_mode'] = result.decision.mode
                turn.diagnostics_json={**turn.diagnostics_json,'processing_failure':observation,**failed_semantic}
                record_failure(session,sandbox_id=turn.sandbox_id,task_id=turn.task_id,observation=observation)
    finally:
        # A separate observer owns its network deadline. It cannot delay the
        # answer, alter a sealed Pro decision, or enqueue a banking action.
        try:
            if 'result' in locals() and is_verified_semantic_result(result):
                from .semantic_shadow import enqueue_turn
                enqueue_turn(factory, getattr(gateway, 'advisor', None), turn_id,
                             verified_result=result)
        except Exception:
            import logging
            logging.getLogger(__name__).warning('semantic_shadow_enqueue_unavailable')
    return True
