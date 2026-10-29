"""Durable post-tool narration, isolated from financial execution authority."""
import os
from datetime import timedelta

from sqlalchemy import select

from .audit import append_audit
from .models import Task, ModelInvocation, utcnow
from .runtime_models import RuntimeRecord, WorkflowJob
from .services import Principal
from .permissions import require_banking_actor


STATES = {'NEED_CLARIFICATION','AWAITING_CONFIRMATION','SUCCEEDED','PARTIAL','FAILED','UNKNOWN','WAITING'}
SNAPSHOT_VERSION = 'conversation-reply-snapshots-v1'
SNAPSHOT_LIMIT = 128


def enabled():
    return os.getenv('ZX_CUSTOMER_REPLY_ENABLED', '0') == '1'


def reply_trace(session, parent):
    from .runtime import task_trace
    principal = Principal(parent.user_id, parent.sandbox_id)
    role = require_banking_actor(session, principal)
    from .extensions import validate_family_origin
    validate_family_origin(session, parent)
    trace = task_trace(session, principal, parent.id)
    from .credit_status_display import attach_credit_status_display
    return attach_credit_status_display(session, principal, parent, {**trace, 'actor_role': role})


def reply_history_context(session,parent,record):
    """Reuse verified understanding's final wire history, with current sources.

    The legacy builder alone is insufficient for native cloud turns: the
    whole-context budget may have removed complete rounds after it ran.
    """
    from .conversation_models import ChatThread,ChatTurn
    from .semantic_history import semantic_history
    from .dialogue_state import dialogue_actor_scope
    from .planning import canonical_hash
    principal=Principal(parent.user_id,parent.sandbox_id)
    role=require_banking_actor(session,principal)
    context=record.metadata_json.get('conversation_context') or {}
    thread=session.get(ChatThread,context.get('conversation_id')) if isinstance(context,dict) and context.get('conversation_id') else None
    turn=session.get(ChatTurn,context.get('turn_id')) if isinstance(context,dict) and context.get('turn_id') else None
    owned=bool(thread and turn and (thread.sandbox_id,thread.user_id)==(parent.sandbox_id,parent.user_id)
        and (turn.thread_id,turn.sandbox_id,turn.user_id,turn.task_id)==(thread.id,parent.sandbox_id,parent.user_id,parent.id)
        and turn.status=='ANSWERED' and not turn.error_code)
    if owned:
        from .conversation_turn_state import _turn_principal
        principal=_turn_principal(session,turn)
        role=require_banking_actor(session,principal)
    from .semantic_proposal import load_verified_semantic_intent,validate_semantic_source_snapshot
    from .runtime_models import PlanSnapshot
    binding=record.metadata_json.get('cloud_semantic_intent')
    if binding is not None:
        snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,
            PlanSnapshot.version==record.plan_version)) if record.plan_version else None
        if snapshot:
            validate_semantic_source_snapshot(session,principal,parent,record.metadata_json,snapshot)
        else:
            load_verified_semantic_intent(session,parent,record.metadata_json)
        from .contracts import DomainError
        if not owned or binding.get('source_turn_id')!=turn.id or binding.get('conversation_id')!=thread.id:
            raise DomainError('CUSTOMER_REPLY_HISTORY_SOURCE_CHANGED','本次回复的会话来源已变化。',409)
    else:
        # A removed native marker cannot silently opt into legacy history.
        load_verified_semantic_intent(session,parent,record.metadata_json)
    history,groups,history_diagnostics=[],[],{}
    if owned and turn.context_policy!='INDEPENDENT':
        history,groups,history_diagnostics=semantic_history(session,principal,thread,turn,
            role=role,actor_scope_hash=dialogue_actor_scope(session,principal)[1])
    origin='LEGACY_AUTHORIZED_HISTORY'
    if binding is not None:
        from .semantic_router import prepare_semantic_input
        from .contracts import DomainError
        offered=binding['offered_context']
        wire_history=prepare_semantic_input(**offered['model'])['history']
        # The final budget only drops whole oldest rounds. Recheck the exact
        # suffix against today's authorized sources; never refill its budget.
        retained=None
        for count in range(len(groups)+1):
            suffix=[item for group in groups[-count:] for item in group] if count else []
            if suffix==wire_history:
                retained=count
                break
        if retained is None:
            raise DomainError('CUSTOMER_REPLY_HISTORY_SOURCE_CHANGED','本次回复的历史来源或权限已变化。',409)
        hashes=history_diagnostics.get('history_source_hashes',[])[-retained:] if retained else []
        original_hashes=(offered.get('context_budget_diagnostics') or {}).get('history_source_hashes')
        if original_hashes is not None and hashes!=(original_hashes[-retained:] if retained else []):
            raise DomainError('CUSTOMER_REPLY_HISTORY_SOURCE_CHANGED','本次回复的历史资料来源已变化。',409)
        history=wire_history
        history_diagnostics={'history_version':history_diagnostics.get('history_version'),
            'history_included_turn_count':retained,'history_included_message_count':len(history),
            'history_included_character_count':sum(len(item['content']) for item in history),
            'history_source_hashes':hashes,'semantic_input_hash':binding['input_hash'],
            'semantic_binding_hash':binding['binding_hash']}
        origin='VERIFIED_SEMANTIC_WIRE_HISTORY'
    projected={'history':history,'history_policy':history_diagnostics,'user_message':turn.message if owned else parent.message,
        'history_origin':origin,
        'scope':{'user_id':parent.user_id,'sandbox_id':parent.sandbox_id,'actor_role':role,
            'conversation_id':thread.id if owned else None,'turn_id':turn.id if owned else None,
            'sequence':turn.sequence if owned else None,'context_policy':turn.context_policy if owned else None}}
    if binding is not None and 'memory' in binding['offered_context']['model']:
        from .conversation_memory import validate_memory_context
        memory,memory_digest=validate_memory_context(session,principal,thread,turn,binding['offered_context'])
        projected.update(memory=memory,memory_fingerprint=memory_digest)
    return projected,canonical_hash(projected)


def enqueue_customer_reply(session, parent):
    record = session.get(RuntimeRecord, parent.id)
    if not record or parent.state not in STATES:
        return None
    narration_enabled=enabled() and record.metadata_json.get('customer_reply_enabled')
    if not narration_enabled and not record.metadata_json.get('cloud_semantic_intent'):
        return None  # Disabled legacy records are not new source evidence.
    try:
        trace = reply_trace(session, parent)
    except Exception as error:
        # Capturing a READ and writing its wording are observational. A
        # presentation/source failure must not fail or reissue bank work.
        reason=error.code if hasattr(error,'code') and isinstance(error.code,str) else 'SOURCE_NOT_RETAINED'
        record.metadata_json={**record.metadata_json,'customer_read_snapshot_unavailable':reason}
        if narration_enabled:
            unavailable_reply(record,'CUSTOMER_REPLY_SOURCE_UNAVAILABLE')
        return None
    from .customer_read_history import retain_customer_read_snapshot
    try:
        retain_customer_read_snapshot(session,parent,record,trace)
    except Exception as error:
        # Evidence retention is not banking execution. It must not roll back
        # a successful read or convert an unconfirmed plan into a bank failure.
        reason=error.code if hasattr(error,'code') and isinstance(error.code,str) else 'SOURCE_NOT_RETAINED'
        record.metadata_json={**record.metadata_json,'customer_read_snapshot_unavailable':reason}
    if not narration_enabled:
        return None
    from .customer_reply_gateway import fingerprint
    digest = fingerprint(trace)
    try:
        _,history_digest=reply_history_context(session,parent,record)
    except Exception:
        # History/source failure is observational, just like trace failure;
        # completed banking work must not be rolled back by narration enqueue.
        unavailable_reply(record,'CUSTOMER_REPLY_SOURCE_UNAVAILABLE',digest)
        return None
    from .planning import canonical_hash
    key = 'customer-reply:'+parent.id+':'+canonical_hash({'business':digest,'history':history_digest})
    existing = session.scalar(select(WorkflowJob).where(WorkflowJob.dedupe_key == key))
    if existing: return existing
    job = WorkflowJob(task_id=parent.id, sandbox_id=parent.sandbox_id, kind='CUSTOMER_REPLY', dedupe_key=key,
        payload_json={'fingerprint': digest,'history_fingerprint':history_digest,'version': parent.version}, max_attempts=1)
    session.add(job)
    record.metadata_json = {**record.metadata_json, 'customer_reply': {
        'fingerprint': digest,'history_fingerprint':history_digest,'state': 'QUEUED', 'task_version': parent.version}}
    session.flush()
    return job


def unavailable_reply(record, reason, digest=None):
    previous = record.metadata_json.get('customer_reply') or {}
    # A failed narration attempt has no new text. In particular, never attach
    # the new business fingerprint to an older pending-confirmation sentence.
    # Historical snapshots remain in their separate immutable store.
    unavailable = {key:value for key,value in previous.items() if key not in {
        'intro','detail','text','fact_refs','question_refs','bill_remark','card_intro','card_detail',
        'query_layout_version','query_focus','query_focus_by_node'}}
    record.metadata_json = {**record.metadata_json, 'customer_reply': {
        **unavailable, 'fingerprint': digest or previous.get('fingerprint'), 'state': 'UNAVAILABLE',
        'diagnostics': {'purpose':'CUSTOMER_REPLY', 'fallback':True,
            'model_api_call_count': None if previous.get('reserved') else 0, 'failure_reasons':[reason]}}}


def persist_conversation_reply_snapshot(session, parent, record, trace, envelope):
    """Immutable, owned-turn narration; not a bank result or current authority.

    The current task's origin is not a browser request. Verify the actual chat
    row before linking text to it. Keep the first text for a turn/version, retain
    all old snapshots, and explicitly mark capacity rather than silently evict.
    """
    if trace.get('reviewed_bill_comparison'):
        return False  # Material-derived wording is freshly checked, never stored as permanent historical bank narration.
    from .conversation_models import ChatThread, ChatTurn
    from .authentication import as_utc
    from .customer_reply_gateway import fingerprint
    if trace.get('task_id') != parent.id or trace.get('version') != parent.version or trace.get('state') != parent.state \
        or envelope.get('fingerprint') != fingerprint(trace): return False
    context = record.metadata_json.get('conversation_context')
    if not isinstance(context,dict): return False
    conversation_id,turn_id=context.get('conversation_id'),context.get('turn_id')
    if not isinstance(conversation_id,str) or not isinstance(turn_id,str): return False
    thread,turn=session.get(ChatThread,conversation_id),session.get(ChatTurn,turn_id)
    if not thread or not turn or (thread.sandbox_id,thread.user_id) != (parent.sandbox_id,parent.user_id) \
        or (turn.thread_id,turn.sandbox_id,turn.user_id,turn.task_id) != (conversation_id,parent.sandbox_id,parent.user_id,parent.id) \
        or turn.status != 'ANSWERED' or turn.mode not in {'NEW_TASK','REVISE','CLARIFY'}:
        return False
    previous=record.metadata_json.get('conversation_reply_snapshots')
    if previous is not None and (not isinstance(previous,dict) or previous.get('schema_version') != SNAPSHOT_VERSION
        or not isinstance(previous.get('items'),list) or len(previous['items']) > SNAPSHOT_LIMIT):
        record.metadata_json={**record.metadata_json,'conversation_reply_snapshot_unavailable':'EXISTING_STORE_INVALID'}
        return False
    previous=previous or {}
    items=list(previous.get('items') or [])
    identity={'conversation_id':conversation_id,'turn_id':turn_id,'task_version':parent.version}
    valid_payload=(isinstance(envelope.get('intro'),str) and 0<len(envelope['intro'])<=5000
        and isinstance(envelope.get('text'),str) and 0<len(envelope['text'])<=10000
        and (envelope.get('detail') is None or isinstance(envelope['detail'],str) and len(envelope['detail'])<=5000)
        and envelope['text']==envelope['intro']+('\n\n'+envelope['detail'] if envelope.get('detail') else '')
        and trace.get('state') in STATES)
    if not valid_payload:
        record.metadata_json={**record.metadata_json,'conversation_reply_snapshots':{**previous,
            'schema_version':SNAPSHOT_VERSION,'limit':SNAPSHOT_LIMIT,'items':items,
            'last_unretained':identity,'last_unretained_reason':'INVALID_REPLY_PAYLOAD'}}
        return False
    if any(isinstance(item,dict) and all(item.get(key)==value for key,value in identity.items()) for item in items):
        return False
    if len(items) >= SNAPSHOT_LIMIT:
        record.metadata_json={**record.metadata_json,'conversation_reply_snapshots':{**previous,
            'schema_version':SNAPSHOT_VERSION,'limit':SNAPSHOT_LIMIT,'items':items,
            'capacity_reached':True,'last_unretained':identity,'last_unretained_reason':'CAPACITY_REACHED'}}
        return False
    snapshot={**identity,'schema_version':'conversation-reply-snapshot-v1','task_id':parent.id,
        'state':trace['state'],'reply_language':trace.get('reply_language','zh-CN'),'actor_role':trace.get('actor_role'),
        'fingerprint':envelope['fingerprint'],'intro':envelope['intro'],'detail':envelope.get('detail'),
        'text':envelope['text'],'observed_at':as_utc(utcnow()).isoformat(),
        'authority':'HISTORICAL_NARRATION_NOT_CURRENT_STATUS_OR_AUTHORIZATION'}
    record.metadata_json={**record.metadata_json,'conversation_reply_snapshots':{**previous,
        'schema_version':SNAPSHOT_VERSION,'limit':SNAPSHOT_LIMIT,'items':items+[snapshot],
        'capacity_reached':bool(previous.get('capacity_reached',False))}}
    return True


def process_customer_reply_job(factory, gateway, job_id, worker_id):
    """Reserve once; inference outside a transaction; revalidate before saving."""
    from .customer_reply_gateway import narrate_customer_reply, fingerprint
    from .model_routing import read_routing_policy
    try:
        with factory.begin() as session:
            job = session.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
            if not job or job.kind != 'CUSTOMER_REPLY' or job.status != 'LEASED' or job.lease_owner != worker_id:
                return True
            parent = session.get(Task, job.task_id)
            record = session.get(RuntimeRecord, parent.id)
            trace = reply_trace(session, parent)
            digest = fingerprint(trace)
            history_context,history_digest=reply_history_context(session,parent,record)
            if digest != job.payload_json['fingerprint'] or history_digest != job.payload_json.get('history_fingerprint'):
                job.status, job.completed_at = 'SUPERSEDED', utcnow()
                enqueue_customer_reply(session, parent)
                return True
            previous = record.metadata_json.get('customer_reply') or {}
            if previous.get('reserved'):
                unavailable_reply(record, 'CUSTOMER_REPLY_PREVIOUS_OUTCOME_UNKNOWN_NO_REISSUE', digest)
                job.status, job.completed_at = 'FAILED', utcnow()
                return True
            record.metadata_json = {**record.metadata_json, 'customer_reply': {
                **previous, 'fingerprint':digest, 'state':'PROCESSING', 'reserved':True}}
            policy = read_routing_policy(session)
        result = narrate_customer_reply(gateway,{**trace,'customer_user_message':history_context['user_message']},policy,history=history_context['history'],
            **({'memory':history_context['memory']} if history_context.get('memory') else {}))
        with factory.begin() as session:
            job = session.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
            if job.status != 'LEASED' or job.lease_owner != worker_id: return True
            parent = session.get(Task, job.task_id)
            record = session.get(RuntimeRecord, parent.id)
            current = reply_trace(session, parent)
            _,current_history_digest=reply_history_context(session,parent,record)
            if fingerprint(current) != digest or result.fingerprint != digest or current_history_digest != history_digest:
                job.status, job.completed_at = 'SUPERSEDED', utcnow()
                from .credit_status_display import KEY as CREDIT_DISPLAY_KEY, checked_policy
                if checked_policy(trace) is not None and checked_policy(current) is not None \
                        and fingerprint({key:value for key,value in trace.items() if key!=CREDIT_DISPLAY_KEY}) == fingerprint({key:value for key,value in current.items() if key!=CREDIT_DISPLAY_KEY}) \
                        and current_history_digest == history_digest:
                    unavailable_reply(record, 'CUSTOMER_REPLY_SOURCE_UNAVAILABLE', fingerprint(current))
                    return True  # Display-only source change never retries inference or banking work.
                enqueue_customer_reply(session, parent)
                return True
            envelope = result.public()
            from .credit_status_display import credit_status_response, VERSION as CREDIT_DISPLAY_VERSION
            status_reply = credit_status_response(current, current.get('reply_language') or 'zh-CN')
            if status_reply:
                intro, detail = status_reply['intro'], status_reply.get('detail')
                envelope.update(intro=intro, detail=detail, text=intro+('\n\n'+detail if detail else ''))
                envelope['diagnostics']={**envelope['diagnostics'],'credit_status_display':{
                    'version':CREDIT_DISPLAY_VERSION,'authority':'SERVER_CHECKED_CURRENT_STATUS','model_free_prose_used':False}}
            record.metadata_json = {**record.metadata_json, 'customer_reply': {
                **envelope, 'state':'UNAVAILABLE' if result.diagnostics.get('fallback') else 'READY',
                'reserved':True,'history_fingerprint':history_digest,'task_version':parent.version}}
            persist_conversation_reply_snapshot(session,parent,record,current,envelope)
            job.status, job.completed_at, job.lease_expires_at = 'SUCCEEDED', utcnow(), None
            job.result_json = {'reply_fingerprint': digest, 'model_api_call_count': result.diagnostics.get('model_api_call_count'),
                'history_fingerprint':history_digest,'fallback':result.diagnostics.get('fallback')}
            session.add(ModelInvocation(task_id=parent.id,sandbox_id=parent.sandbox_id,
                provider_label=str(result.diagnostics.get('actual_provider'))+':'+str(result.diagnostics.get('actual_model')),
                purpose='CUSTOMER_REPLY', outcome='FALLBACK' if result.diagnostics.get('fallback') else 'SUCCESS'))
            append_audit(session,sandbox_id=parent.sandbox_id,task_id=parent.id,event_type='CUSTOMER_REPLY_READY',
                payload={'fingerprint':digest,'model_api_call_count':result.diagnostics.get('model_api_call_count'),
                    'history_fingerprint':history_digest,'fallback':result.diagnostics.get('fallback'),'authority':'NARRATION_NOT_EXECUTION'})
    except Exception as error:
        with factory.begin() as session:
            job = session.get(WorkflowJob, job_id)
            if job and job.status == 'LEASED' and job.lease_owner == worker_id:
                job.status, job.completed_at, job.last_error = 'FAILED', utcnow(), 'CUSTOMER_REPLY_UNAVAILABLE'
                from .operation_diagnostics import failure_observation,record_failure
                observation=failure_observation(error,stage='CUSTOMER_REPLY',correlation_id=job.id,code='CUSTOMER_REPLY_UNAVAILABLE')
                job.result_json={**(job.result_json or {}),'processing_failure':observation}
                record_failure(session,sandbox_id=job.sandbox_id,task_id=job.task_id,observation=observation)
                record = session.get(RuntimeRecord, job.task_id)
                if record and (record.metadata_json.get('customer_reply') or {}).get('fingerprint') == job.payload_json.get('fingerprint'):
                    unavailable_reply(record, 'CUSTOMER_REPLY_UNAVAILABLE')
        # Narration failures must never turn a successful query/payment into a
        # failed business task, reissue a tool, or consume a confirmation.
    return True


def reply_for_current_trace(trace):
    from .customer_reply_gateway import fingerprint
    envelope = trace.get('customer_reply')
    if not isinstance(envelope, dict): return None, None
    if envelope.get('fingerprint') != fingerprint(trace): return None, 'STALE'
    # Reject already-persisted old prose even if an earlier unavailable path
    # accidentally stamped it with the current fingerprint.
    if 'task_version' in envelope and envelope['task_version'] != trace.get('version'):
        return None, 'STALE'
    state = envelope.get('state')
    return (envelope if state in {'READY','UNAVAILABLE'} and isinstance(envelope.get('intro'), str) else None), state
