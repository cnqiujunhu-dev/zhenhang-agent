"""Authenticated functional observations; no inference or business writes.

Process readiness, last observed success, and unobserved dependencies are
deliberately distinct. This endpoint does not manufacture a successful probe.
"""
from sqlalchemy import func, inspect, select

from .authentication import as_utc
from .conversation_models import ChatTurn
from .models import AuditEvent, utcnow
from .runtime_models import WorkflowJob
from .runtime_health import _failure_kind, read_runtime_health

WINDOW_SECONDS = 900
SAMPLE_LIMIT = 200


def _queue(session, model, state_field, time_field, *, where=()):
    states = dict(session.execute(select(state_field,func.count()).where(*where).group_by(state_field)).all())
    oldest = session.scalar(select(func.min(time_field)).where(*where,state_field.in_(['QUEUED','LEASED'])))
    return {'by_state':states,'oldest_pending_age_seconds':round(max(0,(utcnow()-as_utc(oldest)).total_seconds()),2) if oldest else None}


def _recent_model_observation(rows, key):
    from datetime import datetime
    from .observation_metrics import chat_purpose_diagnostic
    retry_present = key == 'semantic_diagnostics' and any(
        (row.diagnostics_json or {}).get('understanding_retry_history') for row in rows)
    candidates = []
    for row in rows:
        meta = row.diagnostics_json or {}
        purpose='understanding' if key=='semantic_diagnostics' else 'consultation'
        observation,conflict=chat_purpose_diagnostic(meta,purpose)
        archived = False
        when = row.completed_at or row.updated_at
        if purpose == 'understanding' and not observation and not conflict and meta.get('understanding_retry_history'):
            archived = True
            history = meta['understanding_retry_history']
            valid = isinstance(history, list) and 1 <= len(history) <= 2 and all(
                isinstance(entry, dict) and type(entry.get('retry_version')) is int and entry['retry_version'] == index
                for index, entry in enumerate(history))
            entry = history[-1] if valid else {}
            observation = entry.get('model_diagnostics') if isinstance(entry.get('model_diagnostics'), dict) else {}
            conflict = not valid
            # Requeue updates the row and clears completed_at. Neither is the
            # time of the previous HTTP. Legacy archives without a timestamp
            # remain freshness-UNKNOWN rather than a fresh success or an older
            # unrelated success elsewhere in the conversation sample.
            when = None
            raw_when = entry.get('observed_at')
            if isinstance(raw_when, str) and len(raw_when) <= 64:
                try:
                    parsed = datetime.fromisoformat(raw_when)
                    if parsed.tzinfo is not None and parsed <= utcnow():
                        when = parsed
                except (ValueError, TypeError):
                    pass
        if not observation and not conflict:
            if not archived:
                continue
        age = max(0,(utcnow()-as_utc(when)).total_seconds()) if when else None
        calls = observation.get('model_api_call_count')
        errors = observation.get('failure_reasons')
        codes = [_failure_kind(code) for code in errors[:5]] if isinstance(errors,list) else []
        status = 'DEGRADED' if archived or conflict or meta.get('semantic_processing_error') or codes or observation.get('fallback') is True else (
            'OBSERVED_OK' if type(calls) is int and calls > 0 and observation.get('fallback') is False and row.status=='ANSWERED' else 'UNKNOWN')
        value = {'status':'UNKNOWN' if age is None else 'STALE' if age > WINDOW_SECONDS else status,'last_observation_status':status,
            'observed_at':as_utc(when).isoformat() if when else None,'age_seconds':round(age,2) if age is not None else None,
            'http_attempts':calls if type(calls) is int and calls >= 0 else None,
            'error_codes':codes,'correlation_id':row.id,'fallback':observation.get('fallback') if type(observation.get('fallback')) is bool else None,
            'result_adopted':status=='OBSERVED_OK' if status in {'OBSERVED_OK','DEGRADED'} else None,
            'diagnostic_conflict':conflict}
        if not retry_present:
            return value
        # A requeue is a new row update, not a new model observation. Compare
        # known attempt timestamps with other turns' actual observations. An
        # unknown archive time stays conservative, never guessed to be old.
        candidates.append((as_utc(when or row.updated_at), value))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return {'status':'NOT_OBSERVED','observed_at':None,'http_attempts':None,'error_codes':[]}


def _recent_narration_observation(session):
    from .observation_metrics import _narration_samples
    with session.no_autoflush:
        observations,sampling=_narration_samples(session,SAMPLE_LIMIT,50)
    if not observations:
        return {'status':'NOT_OBSERVED','observed_at':None,'http_attempts':None,'error_codes':[],
            'fallback':None,'result_adopted':None,'job_success_is_model_success':False}
    known=[row for row in observations if row.get('observed_at') is not None]
    selected=max(known,key=lambda row:as_utc(row['observed_at'])) if known else observations[0]
    when=selected.get('observed_at');age=max(0,(utcnow()-as_utc(when)).total_seconds()) if when else None
    state=('DEGRADED' if selected['diagnostic_conflict'] or selected['fallback'] is True or selected['adoption']=='REJECTED'
        or selected['availability_failure'] or selected['response_rejected'] else
        'OBSERVED_OK' if selected['adoption']=='ADOPTED' else 'UNKNOWN')
    return {'status':'UNKNOWN' if age is None else 'STALE' if age>WINDOW_SECONDS else state,
        'last_observation_status':state,'observed_at':as_utc(when).isoformat() if when else None,
        'age_seconds':round(age,2) if age is not None else None,'http_attempts':selected['calls'],
        'fallback':selected['fallback'],'result_adopted':False if selected['fallback'] is True else selected['adoption']=='ADOPTED' if selected['adoption'] in {'ADOPTED','REJECTED'} else None,
        'diagnostic_conflict':selected['diagnostic_conflict'],'error_codes':[],
        'correlation_id':selected.get('correlation_id'),'job_status':selected.get('job_status'),
        'additional_unknown_observations':sum(row['calls'] is None or row['adoption']=='UNKNOWN' for row in observations),
        'timestamp_unknown_observations':len(observations)-len(known),'sample_truncated':sampling['truncated'],
        'job_success_is_model_success':False,'scope':'LATEST_DATED_PERSISTED_NARRATION_OBSERVATION'}


def knowledge_observation(factory=None):
    try:
        if factory is None:
            from .knowledge_runtime import knowledge_factory
            factory = knowledge_factory()
        if factory is None:
            return {'status':'UNCONFIGURED','live_probe_performed':False}
        from .knowledge_store import index_status
        from .knowledge_index_runner import runner_status
        from .knowledge_models import KnowledgeRetrievalTrace
        with factory() as session:
            index = index_status(session)
            worker = runner_status(session) if inspect(session.connection()).has_table('knowledge_runner_state') else {'state':'NOT_OBSERVED','online':False}
            last = session.scalar(select(KnowledgeRetrievalTrace).order_by(KnowledgeRetrievalTrace.created_at.desc()).limit(1))
            jobs = index['jobs']
            degraded = any(job.get('state') in {'FAILED','STALE','UNCONFIGURED','INTERRUPTED'} for job in jobs[:1])
            status = 'DEGRADED' if degraded or not worker['online'] else 'INDEX_READY' if index['semantic_ready'] else 'INDEX_PENDING'
            return {'status':status,'live_probe_performed':False,
                'semantic_index_ready':index['semantic_ready'],'active_chunks':index['active_chunks'],
                'indexed_chunks':index['indexed_chunks'],'pending_index_jobs':sum(job.get('state') in {'QUEUED','RUNNING'} for job in jobs),
                'job_sample_limit':20,'index_worker':{'state':worker['state'],'online':worker['online']},
                'embedding':{'configuration_status':index['embedding']['status'],'live_status':'NOT_PROBED'},
                'last_retrieval':{'status':_failure_kind(last.status),'mode':_failure_kind(last.mode),
                    'observed_at':as_utc(last.created_at).isoformat()} if last else None}
    except Exception as error:
        from .operation_diagnostics import failure_observation
        return {'status':'UNAVAILABLE','live_probe_performed':False,
            'failure':failure_observation(error,stage='KNOWLEDGE_RETRIEVAL',correlation_id=None,code='KNOWLEDGE_STORE_UNAVAILABLE')}


def capability_health(session, *, knowledge_factory=None):
    from .attachment_models import AttachmentJob
    rows = list(session.scalars(select(ChatTurn).order_by(ChatTurn.updated_at.desc()).limit(SAMPLE_LIMIT)))
    failures = list(session.scalars(select(AuditEvent).where(AuditEvent.event_type=='OPERATION_PROCESSING_FAILED')
        .order_by(AuditEvent.created_at.desc()).limit(20)))
    retained = []
    for row in rows:
        body=(row.diagnostics_json or {}).get('processing_failure')
        if not isinstance(body,dict) or body.get('schema_version')!='operation-failure-v1':
            continue
        retained.append({'turn_id':row.id,'task_id':row.task_id,'observed_at':as_utc(row.completed_at or row.updated_at).isoformat(),
            'stage':_failure_kind(body.get('stage')),'error_type':_failure_kind(body.get('error_type')),
            'code':_failure_kind(body.get('code')),'correlation_id':row.id})
    for row in failures:
        body = row.payload_json or {}
        retained.append({'audit_id':row.id,'task_id':row.task_id,'observed_at':as_utc(row.created_at).isoformat(),
            'stage':_failure_kind(body.get('stage')),'error_type':_failure_kind(body.get('error_type')),
            'code':_failure_kind(body.get('code')),
            'correlation_id':body.get('correlation_id') if isinstance(body.get('correlation_id'),str) and len(body['correlation_id'])<=80 else None})
    return {'schema_version':'capability-health-v1','scope':'ADMIN_READ_ONLY_OPERATIONS',
        'observed_at':utcnow().isoformat(),'process':read_runtime_health(session),
        'observation_window_seconds':WINDOW_SECONDS,'sampled_chat_turns':len(rows),'chat_sample_limit':SAMPLE_LIMIT,
        'models':{'understanding':_recent_model_observation(rows,'semantic_diagnostics'),
            'consultation':_recent_model_observation(rows,'semantic_explanation'),
            'narration':_recent_narration_observation(session)},
        'queues':{'chat':_queue(session,ChatTurn,ChatTurn.status,ChatTurn.created_at),
            'narration':_queue(session,WorkflowJob,WorkflowJob.status,WorkflowJob.created_at,where=(WorkflowJob.kind=='CUSTOMER_REPLY',)),
            'images':_queue(session,AttachmentJob,AttachmentJob.status,AttachmentJob.created_at)},
        'knowledge':knowledge_observation(knowledge_factory),'recent_failures':sorted(retained,key=lambda row:row['observed_at'],reverse=True)[:20],
        'live_model_probe_performed':False,'business_write_performed':False}
