"""Read-only, purpose-separated metrics from persisted observations.

These are operational measurements, not accuracy scores, customer amounts or
counterfactual savings. Missing evidence is never converted to zero. In
particular an attachment job attempt does not prove that HTTP was issued.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite

from sqlalchemy import func, or_, select

from .attachment_models import Attachment, AttachmentJob
from .conversation_models import ChatTurn
from .decision_models import DecisionAdvice
from .models import ModelInvocation, Task
from .runtime_models import RuntimeRecord, WorkflowJob
from .vision_gateway import safe_vision_model_observation


_PURPOSES = ("understanding", "planning", "consultation", "narration", "vision", "laya")
_PROVIDERS = {"deepseek", "ollama", "rule", "server", "server_knowledge", "TEST_STUB", "LAYA_SDK_SIDECAR"}
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens")
_AVAILABILITY_ERRORS = {
    "MODEL_TIMEOUT", "MODEL_HTTP_ERROR", "MODEL_TRANSPORT_FAILED", "MODEL_HTTP_CAPACITY",
    "MODEL_UNAVAILABLE", "HTTP_CAPACITY_EXHAUSTED", "HTTP_TIMEOUT", "HTTP_TRANSPORT_FAILED",
    "MODEL_AUTH_ERROR", "MODEL_PAYMENT_REQUIRED", "MODEL_RATE_LIMIT", "MODEL_CIRCUIT_OPEN",
    "MODEL_TRANSPORT_CAPACITY_EXHAUSTED", "MODEL_BUDGET_EXCEEDED", "CONSULT_MODEL_BUDGET_EXCEEDED",
    "HTTP_OUTCOME_UNKNOWN_DEADLINE", "CONSULT_MODEL_TIMEOUT", "CONSULT_MODEL_HTTP_ERROR",
    "CONSULT_CLOUD_NOT_CONFIGURED",
    "VISION_CLOUD_REJECTED", "VISION_CLOUD_NOT_CONFIGURED",
    "VISION_CLOUD_OUTCOME_UNKNOWN", "VISION_HTTP_CAPACITY",
}
_VALIDATION_ERRORS = {
    "MODEL_EMPTY_CONTENT", "MODEL_INVALID_JSON", "MODEL_INVALID_PROPOSAL", "MODEL_OUTPUT_TRUNCATED",
    "MODEL_RESPONSE_ENCODING_INVALID", "MODEL_RESPONSE_SHAPE", "MODEL_RESPONSE_TOO_LARGE",
    "CONSULT_OUTPUT_TRUNCATED", "CONSULT_SOURCE_NOT_PROVIDED", "CONSULT_FACT_REFERENCE_NOT_PROVIDED",
    "CONSULT_UNSUPPORTED_NUMERIC_CLAIM", "CONSULT_EXECUTION_OR_RETURN_CLAIM",
    "VISION_INVALID_RESPONSE", "VISION_OUTPUT_TRUNCATED", "VISION_RESPONSE_TOO_LARGE",
    "SEMANTIC_MODEL_INVALID", "CUSTOMER_REPLY_MODEL_INVALID", "CUSTOMER_REPLY_OUTPUT_TRUNCATED",
}
_AVAILABILITY_ERRORS.update({"SEMANTIC_MODEL_UNAVAILABLE", "CUSTOMER_REPLY_CLOUD_NOT_CONFIGURED",
    "CUSTOMER_REPLY_MODEL_TIMEOUT", "CUSTOMER_REPLY_MODEL_HTTP_ERROR", "CUSTOMER_REPLY_MODEL_UNAVAILABLE",
    "CUSTOMER_REPLY_MODEL_BUDGET_EXCEEDED", "CUSTOMER_REPLY_MODEL_OUTCOME_UNKNOWN_NO_REISSUE"})
_VISION_PRE_HTTP = {
    "VISION_CLOUD_NOT_CONFIGURED", "VISION_INVALID_INPUT", "VISION_HTTP_CAPACITY",
    "ATTACHMENT_BLOB_MISSING", "ATTACHMENT_HASH_CHANGED", "ATTACHMENT_ACTOR_UNAVAILABLE",
}
_VISION_POST_HTTP = {"VISION_INVALID_RESPONSE", "VISION_OUTPUT_TRUNCATED", "VISION_RESPONSE_TOO_LARGE"}
_MINIMUM_RATE_SAMPLES = 3


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _number(value):
    return float(value) if type(value) in (int, float) and isfinite(value) and value >= 0 else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] + (values[upper] - values[lower]) * (index - lower), 2)


def _observation(diagnostics=None, *, adoption="UNKNOWN", **extra):
    diagnostics = _mapping(diagnostics)
    calls = _integer(diagnostics.get("model_api_call_count"))
    provider = diagnostics.get("actual_provider")
    return {"diagnostics_present": bool(diagnostics), "calls": calls,
            "http_lower_bound": calls or 0, "adoption": adoption,
            "provider": provider if isinstance(provider, str) and provider in _PROVIDERS else "UNKNOWN",
            "elapsed_ms": _number(diagnostics.get("elapsed_ms")),
            "response_success": None,
            "fallback": diagnostics.get("fallback") if type(diagnostics.get("fallback")) is bool else None,
            "diagnostic_conflict": False,
            "usage": _mapping(diagnostics.get("usage")),
            "usage_attempt_coverage_complete": calls is not None and calls <= 1,
            "attempts": diagnostics.get("attempts") if isinstance(diagnostics.get("attempts"), list) else [],
            "availability_failure": any(code in _AVAILABILITY_ERRORS for code in
                diagnostics.get("failure_reasons", []) if isinstance(code, str))
                if isinstance(diagnostics.get("failure_reasons"), list) else False,
            "response_rejected": any(code in _VALIDATION_ERRORS for code in
                diagnostics.get("failure_reasons", []) if isinstance(code, str))
                if isinstance(diagnostics.get("failure_reasons"), list) else False,
            "failure_unclassified": any(code not in _AVAILABILITY_ERRORS and code not in _VALIDATION_ERRORS
                for code in diagnostics.get("failure_reasons", []) if isinstance(code, str))
                if isinstance(diagnostics.get("failure_reasons"), list) else False,
            **extra}


def _sample_summary(total, sampled, *, omitted_observations=0):
    return {"record_count": total, "sampled_records": sampled, "records_omitted": total - sampled,
            "truncated": total > sampled or omitted_observations > 0,
            "observations_omitted_in_sample": omitted_observations,
            "denominator_scope": "SAMPLED_PERSISTED_OBSERVATIONS"}


def _summarize(purpose, observations, sampling):
    expected = len(observations)
    known = [row["calls"] for row in observations if row["calls"] is not None]
    missing = sum(not row["diagnostics_present"] for row in observations)
    latencies = [row["elapsed_ms"] for row in observations if row["elapsed_ms"] is not None]
    adoption = Counter(row["adoption"] for row in observations)
    attempted_known = adoption["ADOPTED"] + adoption["REJECTED"]
    attempts = [item for row in observations for item in row["attempts"] if isinstance(item, dict)]
    issued = sum(item.get("http_issued") is True for item in attempts)
    not_issued = sum(item.get("http_issued") is False for item in attempts)
    # Consultation diagnostics record per-attempt counters, not http_issued.
    if purpose == "consultation":
        issued = sum(_integer(item.get("model_api_call_count")) or 0 for item in attempts)
        not_issued = sum(_integer(item.get("model_api_call_count")) == 0 for item in attempts)
        unknown_attempts = sum(_integer(item.get("model_api_call_count")) is None for item in attempts)
    else:
        unknown_attempts = len(attempts) - issued - not_issued
    complete = expected > 0 and len(known) == expected and not sampling["truncated"]
    usage = {}
    for field in _TOKEN_FIELDS:
        counts = [_integer(row["usage"].get(field)) for row in observations]
        valid = [value for value in counts if value is not None]
        usage[field] = {"observed_sum": sum(valid) if valid else None,
                        "complete_total": sum(valid) if expected and len(valid) == expected and not sampling["truncated"]
                            and all(row["usage_attempt_coverage_complete"] for row in observations) else None,
                        "known_observations": len(valid), "unknown_observations": expected - len(valid),
                        "partial_attempt_coverage_observations":sum(not row["usage_attempt_coverage_complete"] for row in observations)}
    return {"purpose": purpose, **sampling, "observation_count": expected,
            "diagnostics_records": expected - missing, "missing_diagnostics": missing,
            "http_calls": {"observed_sum": sum(known) if known else None,
                "complete_total": sum(known) if complete else None,
                "known_observations": len(known), "unknown_observations": expected - len(known),
                "missing_observations": missing,
                "zero_call_observations": sum(value == 0 for value in known),
                "positive_call_observations": sum(value > 0 for value in known),
                "known_lower_bound": sum(row["http_lower_bound"] for row in observations) if observations else None,
                "calls_are_http_attempts_not_adopted_results": True},
            "attempts": {"recorded_attempts": len(attempts), "issued_http_calls": issued,
                "not_issued_attempts": not_issued, "http_issued_unknown_attempts": unknown_attempts,
                "availability_failed_attempts": sum(isinstance(item.get("error_code"), str) and item["error_code"] in _AVAILABILITY_ERRORS for item in attempts),
                "scope": "RECORDED_ATTEMPT_DETAILS_NOT_ALL_HTTP_CALLS"},
            "adoption": {"adopted_observations": adoption["ADOPTED"],
                "rejected_observations": adoption["REJECTED"],
                "not_attempted_observations": adoption["NOT_ATTEMPTED"],
                "unknown_observations": adoption["UNKNOWN"],
                "known_attempted_denominator": attempted_known,
                "adoption_rate": adoption["ADOPTED"] / attempted_known if attempted_known >= _MINIMUM_RATE_SAMPLES else None,
                "minimum_rate_samples": _MINIMUM_RATE_SAMPLES,
                "rate_status": "AVAILABLE" if attempted_known >= _MINIMUM_RATE_SAMPLES else "INSUFFICIENT_KNOWN_ATTEMPTED_SAMPLES",
                "rate_is_accuracy": False, "bank_execution_success_implied": False},
            "availability_failure_observations": sum(row["availability_failure"] for row in observations),
            "response_validation_rejection_observations": sum(row["response_rejected"] for row in observations),
            "unclassified_failure_observations": sum(row["failure_unclassified"] for row in observations),
            "successful_http_response_observations": sum(row["response_success"] is True for row in observations),
            "http_response_success_unknown_observations": sum(row["response_success"] is None for row in observations),
            "fallback": {"fallback_observations": sum(row["fallback"] is True for row in observations),
                "nonfallback_observations": sum(row["fallback"] is False for row in observations),
                "unknown_observations": sum(row["fallback"] is None for row in observations)},
            "conflicting_diagnostic_observations": sum(row["diagnostic_conflict"] is True for row in observations),
            "by_actual_provider": dict(Counter(row["provider"] for row in observations)),
            "diagnostic_latency_ms": {"p50": _percentile(latencies, .5) if len(latencies) >= _MINIMUM_RATE_SAMPLES else None,
                "p95": _percentile(latencies, .95) if len(latencies) >= _MINIMUM_RATE_SAMPLES else None,
                "sample_count": len(latencies), "unknown_observations": expected - len(latencies),
                "minimum_percentile_samples": _MINIMUM_RATE_SAMPLES,
                "scope": "SERVER_PURPOSE_DIAGNOSTIC_NOT_CLIENT_JOURNEY"},
            "token_usage": usage,
            "quality": {"accuracy": None, "labelled_evaluation_samples": 0,
                        "status": "NO_LABELLED_QUALITY_EVALUATION_IN_OPERATIONAL_OBSERVATIONS"}}


def _planning_samples(session, limit, per_record_limit):
    query = select(RuntimeRecord).where(RuntimeRecord.kind == "PARENT")
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    records = list(session.scalars(query.order_by(RuntimeRecord.created_at.desc(), RuntimeRecord.task_id).limit(limit)))
    ids = [row.task_id for row in records]
    counts = dict(session.execute(select(ModelInvocation.task_id, func.count()).where(
        ModelInvocation.purpose == "INTENT_AND_PLAN_PROPOSAL", ModelInvocation.task_id.in_(ids)
    ).group_by(ModelInvocation.task_id)).all()) if ids else {}
    observations, advice_samples, omitted = [], [], 0
    for record in records:
        metadata = _mapping(record.metadata_json)
        decisions = metadata.get("decisions") if isinstance(metadata.get("decisions"), list) else []
        # Compensation is server-generated and deliberately has no model call.
        decisions = [item for item in decisions if isinstance(item, dict) and item.get("source") != "SERVER_COMPENSATION"]
        omitted += max(0, len(decisions) - per_record_limit)
        samples = []
        versions = set()
        for decision in decisions[-per_record_limit:]:
            version = decision.get("plan_version")
            version = version if type(version) is int and version >= 0 else None
            if version is not None and version in versions:
                continue
            if version is not None:
                versions.add(version)
            samples.append((decision.get("planning_diagnostics"), decision.get("fallback"), decision.get("laya_advice")))
        if record.plan_version not in versions and isinstance(metadata.get("planning_diagnostics"), dict):
            samples.append((metadata["planning_diagnostics"], None, metadata.get("laya_advice")))
        # Historical invocation rows without diagnostic evidence remain unknown.
        expected = max(len(samples), counts.get(record.task_id, 0))
        for diagnostics, fallback, advice in samples:
            diagnostics = _mapping(diagnostics)
            if diagnostics.get("purpose") == "UNIFIED_SEMANTIC_UNDERSTANDING":
                # Some historic metadata copied the upstream receipt here.
                # Its original ChatTurn is the understanding source, not a
                # second planner HTTP observation.
                continue
            calls = _integer(diagnostics.get("model_api_call_count"))
            provider = diagnostics.get("actual_provider")
            if calls == 0:
                adopted = "NOT_ATTEMPTED"
            elif calls is not None and calls > 0 and isinstance(provider, str) and provider in {"deepseek", "ollama", "TEST_STUB"} and fallback is False:
                adopted = "ADOPTED"
            elif calls is not None and calls > 0 and provider == "rule" and (fallback is True or diagnostics.get("degraded") is True):
                adopted = "REJECTED"
            else:
                adopted = "UNKNOWN"
            observations.append(_observation(diagnostics, adoption=adopted,
                standard_read_compiler=diagnostics.get("plan_source") == "STANDARD_READ_COMPILER" and calls == 0))
            if isinstance(advice, dict):
                advice_samples.append(advice)
        observations.extend(_observation() for _ in range(expected - len(samples)))
        if not any(isinstance(item[2], dict) for item in samples) and isinstance(metadata.get("laya_advice"), dict):
            advice_samples.append(metadata["laya_advice"])
    sampling = _sample_summary(total, len(records), omitted_observations=omitted)
    # Orphan/legacy invocation-only records cannot be silently discarded.
    invocation_only = session.scalar(select(func.count()).select_from(ModelInvocation).where(
        ModelInvocation.purpose == "INTENT_AND_PLAN_PROPOSAL",
        ~ModelInvocation.task_id.in_(select(RuntimeRecord.task_id).where(RuntimeRecord.kind == "PARENT"))))
    observations.extend(_observation() for _ in range(min(invocation_only, limit)))
    sampling["invocation_only_missing_diagnostic_records"] = invocation_only
    sampling["truncated"] = sampling["truncated"] or invocation_only > limit
    return observations, advice_samples, sampling


def _consistent_diagnostic(primary, duplicate):
    """One persisted observation may have a nested copy, never two requests."""
    primary, duplicate = _mapping(primary), _mapping(duplicate)
    if primary and duplicate:
        for key in ("model_api_call_count", "actual_provider", "actual_model", "response_model", "http_issued", "http_status", "fallback", "input_hash"):
            if key in primary and key in duplicate and primary[key] != duplicate[key]:
                return {}, True
    return primary or duplicate, False


def chat_purpose_diagnostic(raw, purpose):
    """Shared by metrics and functional health; finite metadata only."""
    raw = _mapping(raw)
    if purpose == "understanding":
        receipt = _mapping(raw.get("semantic_receipt"))
        nested = receipt.get("diagnostics") if receipt.get("purpose") == "UNIFIED_SEMANTIC_UNDERSTANDING" else None
        return _consistent_diagnostic(raw.get("semantic_diagnostics"), nested)
    if purpose == "consultation":
        nested = _mapping(raw.get("semantic_explanation"))
        nested = nested if nested.get("purpose") == "CHAT_CONSULTATION" else {}
        legacy = raw if raw.get("purpose") == "CHAT_CONSULTATION" else {}
        return _consistent_diagnostic(nested, legacy)
    raise ValueError("UNREGISTERED_CHAT_PURPOSE")


def _response_success(diagnostics):
    code = diagnostics.get("http_status")
    if type(code) is int:
        return 200 <= code < 300
    codes = [entry.get("http_status") for entry in diagnostics.get("attempts", []) if isinstance(entry,dict)] \
        if isinstance(diagnostics.get("attempts"),list) else []
    if any(type(value) is int and 200 <= value < 300 for value in codes):return True
    if codes and all(type(value) is int for value in codes):return False
    return None


def _model_adoption(diagnostics, *, completed=False, rejected=False):
    calls = _integer(diagnostics.get("model_api_call_count"))
    if calls == 0:return "NOT_ATTEMPTED"
    if calls is not None and calls > 0:
        if rejected or diagnostics.get("fallback") is True:return "REJECTED"
        if completed and diagnostics.get("fallback") is False:return "ADOPTED"
    return "UNKNOWN"


def _understanding_retry_observation(diagnostics, *, failure=None, conflict=False, completed=False):
    """One witnessed attempt, not one conversation turn or one receipt copy."""
    diagnostics = _mapping(diagnostics)
    # The retry archive retains the original transport witness. Missing issued
    # state remains unknown; a failed attempt never becomes adopted simply
    # because a later attempt answered the same turn.
    issued = diagnostics.get('http_issued')
    attempt = {'http_issued': issued if type(issued) is bool else None,
        'http_status': diagnostics.get('http_status') if type(diagnostics.get('http_status')) is int else None,
        'error_code': failure if isinstance(failure, str) else None}
    result = _observation(diagnostics,
        adoption=_model_adoption(diagnostics, completed=completed, rejected=bool(failure) or conflict),
        response_success=_response_success(diagnostics), diagnostic_conflict=conflict,
        attempts=[attempt])
    if result['calls'] is None and issued is True:
        result['http_lower_bound'] = 1
    return result


def _understanding_samples(session, limit):
    query=select(ChatTurn).where(or_(ChatTurn.diagnostics_json["semantic_cloud_reserved"].as_boolean()==True,
        ChatTurn.diagnostics_json["semantic_diagnostics"].as_string().is_not(None),
        ChatTurn.diagnostics_json["understanding_retry_history"].as_string().is_not(None),
        ChatTurn.diagnostics_json["semantic_receipt"]["purpose"].as_string()=="UNIFIED_SEMANTIC_UNDERSTANDING"))
    total=session.scalar(select(func.count()).select_from(query.subquery()))
    rows=list(session.scalars(query.order_by(ChatTurn.created_at.desc(),ChatTurn.id).limit(limit)))
    observations=[]
    for row in rows:
        meta=_mapping(row.diagnostics_json);diagnostics,conflict=chat_purpose_diagnostic(meta,"understanding")
        from .semantic_reunderstanding import KEY,metric_attempts
        if KEY in meta:
            outer=meta[KEY];attempts=metric_attempts(outer)
            if attempts is None:
                observations.append(_observation(diagnostic_conflict=True))
            else:
                for index,entry in enumerate(attempts):
                    final=index==len(attempts)-1
                    failure='SEMANTIC_RESPONSE_REJECTED' if entry['status']!='READY' else \
                        meta.get('semantic_processing_error') if final else None
                    observations.append(_understanding_retry_observation(entry['diagnostics'],
                        failure=failure,conflict=conflict if final else False,
                        completed=row.status=='ANSWERED' and entry['status']=='READY'))
                if outer['reserved_attempts']>len(attempts):
                    observations.append(_observation())  # Reserved outcome is unknown, not a zero call.
            continue  # Final receipt/diagnostic and manual archive are copies, not extra calls.
        history = meta.get('understanding_retry_history')
        if history:
            if not isinstance(history, list) or len(history) > 2:
                # The public retry protocol permits at most two predecessors.
                # Corrupt/unbounded history cannot mint a complete zero total.
                observations.append(_observation(diagnostic_conflict=True))
            else:
                for version, entry in enumerate(history):
                    if not isinstance(entry, dict) or type(entry.get('retry_version')) is not int or entry['retry_version'] != version:
                        observations.append(_observation(diagnostic_conflict=True))
                        continue
                    observations.append(_understanding_retry_observation(entry.get('model_diagnostics'),
                        failure=entry.get('failure_code') or 'SEMANTIC_PREVIOUS_ATTEMPT_FAILED'))
        current_present = meta.get('semantic_cloud_reserved') is True or bool(diagnostics) or conflict \
            or _mapping(meta.get('semantic_receipt')).get('purpose') == 'UNIFIED_SEMANTIC_UNDERSTANDING'
        # Immediately after requeue, history exists but no new HTTP has been
        # reserved. Retain past observations without inventing another attempt.
        if history and not current_present:
            continue
        rejected=bool(meta.get("semantic_processing_error")) or _mapping(meta.get("semantic_receipt")).get("status") not in {None,"READY"}
        if history:
            observations.append(_understanding_retry_observation(diagnostics,
                failure=meta.get('semantic_processing_error') or ('SEMANTIC_RESPONSE_REJECTED' if rejected else None),
                conflict=conflict, completed=row.status=='ANSWERED'))
        else:
            observations.append(_observation(diagnostics,adoption=_model_adoption(diagnostics,completed=row.status=="ANSWERED",rejected=rejected),
                response_success=_response_success(diagnostics),diagnostic_conflict=conflict))
    sampling=_sample_summary(total,len(rows));sampling["deduplication_key"]="CHAT_TURN_ID_RETRY_VERSION_WITH_SINGLE_CURRENT_SEMANTIC_RECEIPT"
    return observations,sampling


def _consultation_samples(session, limit):
    query = select(ChatTurn.status, ChatTurn.diagnostics_json).where(or_(
        ChatTurn.mode == "CONSULT", ChatTurn.diagnostics_json["purpose"].as_string() == "CHAT_CONSULTATION",
        ChatTurn.diagnostics_json["semantic_explanation"]["purpose"].as_string() == "CHAT_CONSULTATION",
        ChatTurn.diagnostics_json["consult_cloud_reserved"].as_boolean() == True))
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(session.execute(query.order_by(ChatTurn.created_at.desc(), ChatTurn.id).limit(limit)))
    observations = []
    for status, raw in rows:
        diagnostics,conflict=chat_purpose_diagnostic(raw,"consultation")
        observations.append(_observation(diagnostics, adoption=_model_adoption(diagnostics,completed=status=="ANSWERED"),
            response_success=_response_success(diagnostics),diagnostic_conflict=conflict))
    sampling=_sample_summary(total,len(rows));sampling["deduplication_key"]="CHAT_TURN_ID_NESTED_OR_LEGACY_CONSULTATION"
    return observations,sampling


def _narration_samples(session, limit, per_record_limit):
    """Per-job facts plus the exact matching latest envelope, never job=LLM success.

    Historic jobs retain calls/fallback but not full provider/token diagnostics.
    Missing historic fields remain unknown; ModelInvocation rows only reveal
    missing observations, not HTTP counts or response adoption.
    """
    job_tasks=select(WorkflowJob.task_id).where(WorkflowJob.kind=="CUSTOMER_REPLY")
    invocation_tasks=select(ModelInvocation.task_id).where(ModelInvocation.purpose=="CUSTOMER_REPLY")
    query=select(Task,RuntimeRecord).outerjoin(RuntimeRecord,RuntimeRecord.task_id==Task.id).where(or_(
        Task.id.in_(job_tasks),Task.id.in_(invocation_tasks),RuntimeRecord.metadata_json["customer_reply"].as_string().is_not(None)))
    total=session.scalar(select(func.count()).select_from(query.subquery()))
    records=list(session.execute(query.order_by(Task.updated_at.desc(),Task.id).limit(limit)))
    ids=[task.id for task,record in records]
    jobs_by_task={identifier:[] for identifier in ids}
    if ids:
        for job in session.scalars(select(WorkflowJob).where(WorkflowJob.task_id.in_(ids),WorkflowJob.kind=="CUSTOMER_REPLY")
                .order_by(WorkflowJob.created_at.desc(),WorkflowJob.id)):
            jobs_by_task[job.task_id].append(job)
    counts=dict(session.execute(select(ModelInvocation.task_id,func.count()).where(ModelInvocation.task_id.in_(ids),
        ModelInvocation.purpose=="CUSTOMER_REPLY").group_by(ModelInvocation.task_id)).all()) if ids else {}
    observations=[];omitted=0;invocation_only=0
    for task,record in records:
        envelope=_mapping(_mapping(record.metadata_json if record else {}).get("customer_reply"))
        full=_mapping(envelope.get("diagnostics"));full=full if full.get("purpose")=="CUSTOMER_REPLY" else {}
        all_jobs=jobs_by_task[task.id];jobs=all_jobs[:per_record_limit]
        omitted+=max(0,len(all_jobs)-len(jobs));matched=False;observed_for_task=0
        for job in jobs:
            body=_mapping(job.result_json);payload=_mapping(job.payload_json)
            minimal={key:body[key] for key in ("model_api_call_count","fallback") if key in body}
            identity=(body.get("reply_fingerprint") or payload.get("fingerprint"),body.get("history_fingerprint") or payload.get("history_fingerprint"))
            exact=bool(envelope.get("fingerprint") and identity==(envelope.get("fingerprint"),envelope.get("history_fingerprint")))
            if exact and not matched:
                matched=True;diagnostics,conflict=_consistent_diagnostic(full,minimal)
            else:diagnostics,conflict=minimal,False
            terminal=job.status in {"SUCCEEDED","FAILED","SUPERSEDED","CANCELLED","UNKNOWN"}
            if not diagnostics and not terminal:continue  # Pending queue is not an invented call observation.
            observations.append(_observation(diagnostics,adoption=_model_adoption(diagnostics,completed=job.status=="SUCCEEDED"),
                response_success=_response_success(diagnostics),diagnostic_conflict=conflict,
                observed_at=job.completed_at,correlation_id=job.id,job_status=job.status))
            observed_for_task+=1
        if full and not matched:
            # A matching omitted job is outside this sample, not a new request.
            belongs_to_omitted=any((_mapping(job.result_json).get("reply_fingerprint") or _mapping(job.payload_json).get("fingerprint"))==envelope.get("fingerprint")
                and (_mapping(job.result_json).get("history_fingerprint") or _mapping(job.payload_json).get("history_fingerprint"))==envelope.get("history_fingerprint") for job in all_jobs[len(jobs):])
            if not belongs_to_omitted:
                observations.append(_observation(full,adoption=_model_adoption(full,completed=envelope.get("state")=="READY"),
                    response_success=_response_success(full),observed_at=None,correlation_id=task.id,job_status=None))
                observed_for_task+=1
        witnessed_jobs=sum(job.status in {"SUCCEEDED","FAILED","SUPERSEDED","CANCELLED","UNKNOWN"}
            or "model_api_call_count" in _mapping(job.result_json) or "fallback" in _mapping(job.result_json) for job in all_jobs)
        missing=max(0,counts.get(task.id,0)-max(witnessed_jobs,observed_for_task))
        invocation_only+=missing
        observations.extend(_observation(observed_at=None,correlation_id=task.id,job_status=None) for _ in range(min(missing,per_record_limit)))
        omitted+=max(0,missing-per_record_limit)
    sampling=_sample_summary(total,len(records),omitted_observations=omitted)
    sampling.update(deduplication_key="TASK_JOB_ID_WITH_MATCHED_REPLY_AND_HISTORY_FINGERPRINT",
        invocation_only_missing_diagnostic_records=invocation_only,job_success_is_model_success=False,
        historical_provider_and_usage_may_be_unknown=True)
    return observations,sampling


def _vision_samples(session, limit):
    query = select(Attachment.state, Attachment.error_code, Attachment.extraction_json,
                   AttachmentJob.status, AttachmentJob.attempts, AttachmentJob.last_error, AttachmentJob.result_json
                   ).outerjoin(AttachmentJob, AttachmentJob.attachment_id == Attachment.id)
    total = session.scalar(select(func.count()).select_from(Attachment))
    rows = list(session.execute(query.order_by(Attachment.created_at.desc(), Attachment.id).limit(limit)))
    observations = []
    job_attempts = 0
    for state, error, raw, job_status, attempts, last_error, result in rows:
        extraction, result = _mapping(raw), _mapping(result)
        attempts = _integer(attempts)
        job_attempts += attempts or 0
        error = last_error or error
        provider = result.get("provider", extraction.get("provider"))
        successful = job_status == "SUCCEEDED" and isinstance(provider, str) and provider in {"deepseek", "TEST_STUB"}
        observed_entries = result.get("model_observations") if isinstance(result.get("model_observations"), list) else []
        observed_entries = [safe for value in observed_entries[:2] if (safe := safe_vision_model_observation(value)) is not None]
        # A successful legacy multi-attempt job only proves the final HTTP request.
        # The legacy counters are job-derived and do not prove earlier issuance.
        if successful and provider == "deepseek":
            calls, lower = (1 if attempts == 1 else None), 1
        elif successful and provider == "TEST_STUB":
            calls, lower = 0, 0
        elif isinstance(error, str) and error in _VISION_PRE_HTTP and attempts in (0, 1):
            calls, lower = 0, 0
        elif isinstance(error, str) and error in _VISION_POST_HTTP:
            calls, lower = (1 if attempts == 1 else None), 1
        elif attempts == 0:
            calls, lower = 0, 0
        else:
            calls, lower = None, 0
        adoption = "ADOPTED" if successful and provider == "deepseek" else (
            "NOT_ATTEMPTED" if calls == 0 else "REJECTED" if job_status == "FAILED" and calls else "UNKNOWN")
        usage = _mapping(result.get("usage", extraction.get("usage")))
        diagnostics = {"model_api_call_count": calls, "actual_provider": provider,
            "usage": {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
                      "total_tokens": usage.get("total_tokens")},
            "failure_reasons": [error] if isinstance(error, str) else []}
        extra = {"http_lower_bound": lower, "response_success": True if successful and provider == "deepseek" else None}
        if observed_entries:
            values = [_integer(item.get("model_api_call_count")) for item in observed_entries]
            evidence_complete = attempts is not None and len(observed_entries) == attempts and all(value is not None for value in values)
            extra["usage_attempt_coverage_complete"] = evidence_complete
            diagnostics["model_api_call_count"] = sum(values) if evidence_complete else None
            extra["http_lower_bound"] = sum(value for value in values if value is not None)
            diagnostics["attempts"] = [{"http_issued": item["http_issued"], "http_status": item["http_status"], "error_code": item["error_code"]} for item in observed_entries]
            diagnostics["elapsed_ms"] = sum(item["elapsed_ms"] for item in observed_entries) if evidence_complete and all(item["elapsed_ms"] is not None for item in observed_entries) else None
            diagnostics["failure_reasons"] = [item["error_code"] for item in observed_entries if item["error_code"]]
            if isinstance(error, str):
                diagnostics["failure_reasons"].append(error)
            if any(item["result_adopted"] is True and item["provider"] == "deepseek" and item["response_validated"] is True for item in observed_entries):
                adoption = "ADOPTED"
            elif diagnostics["model_api_call_count"] == 0:
                adoption = "NOT_ATTEMPTED"
            elif extra["http_lower_bound"] > 0 and job_status == "FAILED":
                adoption = "REJECTED"
            else:
                adoption = "UNKNOWN"
            extra["response_success"] = True if any(item["response_success"] is True for item in observed_entries) else False if evidence_complete and all(item["response_success"] is False for item in observed_entries) else None
            # The legacy TRANSIENT enum mixes HTTP failure with malformed JSON.
            # A recorded 200 response rejected by extraction validation is not
            # provider unavailability. Classify only from the new boundary.
            extra["response_rejected"] = any(item["error_code"] in _VALIDATION_ERRORS or
                item["response_success"] is True and item["response_validated"] is False for item in observed_entries)
            extra["availability_failure"] = any(item["error_code"] in _AVAILABILITY_ERRORS or
                item["error_code"] == "VISION_CLOUD_TRANSIENT" and item["response_success"] is False for item in observed_entries)
            extra["failure_unclassified"] = not evidence_complete or any(item["error_code"] is not None
                and item["error_code"] not in _AVAILABILITY_ERRORS and item["error_code"] not in _VALIDATION_ERRORS
                and not (item["error_code"] == "VISION_CLOUD_TRANSIENT" and (item["response_success"] is False
                    or item["response_success"] is True and item["response_validated"] is False))
                for item in observed_entries)
            usage_rows = [item["usage"] for item in observed_entries]
            diagnostics["usage"] = {target: sum(item[source] for item in usage_rows) if evidence_complete and all(_integer(item.get(source)) is not None for item in usage_rows) else None
                for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"), ("total_tokens", "total_tokens"))}
        observations.append(_observation(diagnostics, adoption=adoption, **extra))
    sampling = _sample_summary(total, len(rows))
    sampling["job_attempts"] = job_attempts
    sampling["job_attempts_are_http_calls"] = False
    sampling["legacy_multi_attempt_http_total_verifiable"] = False
    return observations, sampling


def _persisted_laya_advice(raw):
    """Read the runtime's envelope, not an unbound or repaired SDK response.

    Runtime persistence adds exactly two version fields to DecisionAdvice.
    They are not SDK fields. Other unexpected fields still fail validation;
    malformed binding and coerced booleans must never earn inference credit.
    Legacy unwrapped advice is accepted only when its own contract validates.
    """
    if not isinstance(raw, dict):
        return None
    binding = {key for key in ("plan_version", "input_revision") if key in raw}
    if binding and (len(binding) != 2
        or type(raw["plan_version"]) is not int or raw["plan_version"] < 1
        or type(raw["input_revision"]) is not int or raw["input_revision"] < 0):
        return None
    payload = {key: value for key, value in raw.items() if key not in binding}
    try:
        return DecisionAdvice.model_validate(payload, strict=True)
    except (ValueError, TypeError):
        return None


def _laya_samples(advice_samples, planning_sampling):
    observations, verified, known_unused, candidates = [], 0, 0, 0
    by_status = Counter()
    for raw in advice_samples:
        advice = _persisted_laya_advice(raw)
        status = raw.get("status")
        by_status[status if isinstance(status, str) and status in {"OFF", "READY", "UNAVAILABLE", "ERROR", "TIMED_OUT", "PENDING", "BUSY", "BUDGET_EXCEEDED", "SKIPPED"} else "UNKNOWN"] += 1
        real = bool(advice and advice.status == "READY" and advice.backend == "LAYA_SDK_SIDECAR"
                    and advice.laya_used and advice.identity and advice.answers)
        unused = bool(advice and advice.laya_used is False and advice.status in {"OFF", "SKIPPED"})
        verified += real
        known_unused += unused
        candidates += bool(advice and advice.readonly_candidate and advice.readonly_candidate.observation.status == "EXPERIMENTAL_CANDIDATE")
        # SHADOW observations explicitly have no model-response adoption. A local
        # sidecar inference is separate from cloud HTTP and compiler fast-paths.
        observations.append(_observation({"actual_provider": "LAYA_SDK_SIDECAR" if real else "UNKNOWN",
            "elapsed_ms": advice.latency_ms if advice else None},
            adoption="NOT_ATTEMPTED" if advice else "UNKNOWN"))
    sampling = _sample_summary(len(advice_samples), len(advice_samples))
    sampling["truncated"] = planning_sampling["truncated"]
    summary = _summarize("laya", observations, sampling)
    summary.update(by_status=dict(by_status), verified_local_sdk_inferences=verified,
        known_no_inference_observations=known_unused,
        inference_evidence_unknown_observations=len(observations) - verified - known_unused,
        experimental_readonly_candidates=candidates, observed_adoptions=0,
        adoption_enabled=False, quality_gate_passed=False,
        call_savings=None, latency_savings_ms=None,
        http_count_scope="NOT_RECORDED_LOCAL_SIDECAR_TRANSPORT_IS_NOT_CLOUD_MODEL_HTTP")
    return summary


def collect_observation_metrics(session, *, limit=5000, per_record_limit=50):
    """Only SELECTs. No model invocation, telemetry insertion or bank write."""
    if type(limit) is not int or not 1 <= limit <= 10000 or type(per_record_limit) is not int or not 1 <= per_record_limit <= 100:
        raise ValueError("INVALID_OBSERVATION_SAMPLE_LIMIT")
    with session.no_autoflush:
        return _collect_observation_metrics(session, limit=limit, per_record_limit=per_record_limit)


def _collect_observation_metrics(session, *, limit, per_record_limit):
    understanding,understanding_sampling=_understanding_samples(session,limit)
    planning, advice, planning_sampling = _planning_samples(session, limit, per_record_limit)
    consultation, consultation_sampling = _consultation_samples(session, limit)
    narration,narration_sampling=_narration_samples(session,limit,per_record_limit)
    vision, vision_sampling = _vision_samples(session, limit)
    by_purpose = {"understanding":_summarize("understanding",understanding,understanding_sampling),
        "planning": _summarize("planning", planning, planning_sampling),
        "consultation": _summarize("consultation", consultation, consultation_sampling),
        "narration":_summarize("narration",narration,narration_sampling),
        "vision": _summarize("vision", vision, vision_sampling),
        "laya": _laya_samples(advice, planning_sampling)}
    from .decision_api import semantic_shadow_status
    semantic_shadow = semantic_shadow_status(session, limit=limit)
    semantic_shadow.pop('identity_matches')
    # Do not relabel historic planner observations as current semantic usage.
    by_purpose['laya']['semantic_shadow'] = semantic_shadow
    by_purpose["planning"]["standard_read_compiler_zero_http_observations"] = sum(row.get("standard_read_compiler") is True for row in planning)
    purpose_http = [by_purpose[key]["http_calls"] for key in _PURPOSES if key != "laya"]
    sums = [item["observed_sum"] for item in purpose_http if item["observed_sum"] is not None]
    complete = all(item["unknown_observations"] == 0 for item in purpose_http) and not any(by_purpose[key]["truncated"] for key in _PURPOSES if key != "laya")
    observed_count = sum(by_purpose[key]["observation_count"] for key in _PURPOSES if key != "laya")
    unclassified=session.scalar(select(func.count()).select_from(ModelInvocation).where(
        ~ModelInvocation.purpose.in_(["INTENT_AND_PLAN_PROPOSAL","UNIFIED_SEMANTIC_INTENT_PROPOSAL","CUSTOMER_REPLY"])))
    aggregate={"observed_sum":sum(sums) if sums else None,
        "complete_total":sum(sums) if complete and observed_count and not unclassified else None,
        "unknown_observations":sum(item["unknown_observations"] for item in purpose_http),
        "known_lower_bound":sum(item["known_lower_bound"] or 0 for item in purpose_http) if observed_count else None,
        "includes_local_laya_sidecar":False,"scope":"REGISTERED_PURPOSE_MODEL_HTTP_WITNESSES",
        "unclassified_legacy_invocations":unclassified,"unclassified_invocations_are_http_counts":False}
    return {"schema_version": "purpose-observation-metrics-v1", "scope": "REGISTERED_PERSISTED_PURPOSE_OBSERVATIONS_NOT_FULL_JOURNEY",
        "registered_purposes":list(_PURPOSES),"whole_client_journey_complete":False,
        "nested_diagnostics_counted_once":True,"narration_jobs_are_model_success":False,
        "read_only": True, "sample_limit_per_purpose": limit, "by_purpose": by_purpose,
        "registered_model_http":aggregate,
        "all_cloud_and_planning_http":aggregate,
        "legacy_aggregate_alias":"all_cloud_and_planning_http refers only to registered purpose witnesses, not a complete client journey",
        "customer_journey_latency_ms": {"p50": None, "p95": None, "sample_count": 0,
            "scope": "NOT_RECORDED_CLIENT_INTERACTION_TELEMETRY", "server_latency_is_customer_journey": False},
        "unknown_counts_are_zero": False, "raw_customer_content_returned": False,
        "raw_reasoning_retained": False, "bank_confirmation_permitted": False,
        "quality_is_inferred_from_proposed_action_counts": False}
