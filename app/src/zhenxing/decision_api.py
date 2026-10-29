"""Actor-scoped observations, not marketing counters or authorization scores."""
from math import isfinite
import json
import hashlib
from pathlib import Path

from fastapi import Depends, Request
from sqlalchemy import select

from .models import Task
from .permissions import require_banking_actor
from .runtime_models import RuntimeRecord
from .observation_metrics import _persisted_laya_advice
from .conversation_models import ChatTurn


def semantic_shadow_status(session, principal=None, *, limit=5000):
    """Current-source-bound dialogue observations, without customer content.

    This is evidence for a separate observer, never a banking permission or an
    accuracy estimate. Old planner observations remain in their own counters.
    """
    from collections import Counter
    from sqlalchemy import func
    from .semantic_shadow import validated_observation
    query = select(ChatTurn).where(
        ChatTurn.diagnostics_json['semantic_shadow_observation'].as_string().is_not(None))
    if principal is not None:
        query = query.where(ChatTurn.sandbox_id == principal.sandbox_id,
                            ChatTurn.user_id == principal.user_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(session.scalars(query.order_by(ChatTurn.created_at.desc(), ChatTurn.id).limit(limit)))
    by_status, coverage, agreements = Counter(), Counter(), Counter()
    latencies, identities = [], []
    verified, invalid = 0, 0
    statuses = {'QUEUED', 'LEASED', 'READY', 'OFF', 'UNAVAILABLE', 'ERROR',
                'TIMED_OUT', 'PENDING', 'BUSY', 'BUDGET_EXCEEDED', 'SKIPPED', 'INVALIDATED'}
    for turn in rows:
        raw = turn.diagnostics_json.get('semantic_shadow_observation')
        if not isinstance(raw, dict):
            continue
        status = raw.get('status')
        by_status[status if isinstance(status, str) and status in statuses else 'UNKNOWN'] += 1
        # Reasons are stable machine codes from the observer, never model text.
        reason = (raw.get('coverage') or {}).get('reason') if isinstance(raw.get('coverage'), dict) else None
        coverage[reason if isinstance(reason, str) and reason.isascii() and reason.replace('_', '').isalnum() and len(reason) <= 80 else 'UNKNOWN'] += 1
        observation = validated_observation(session, turn)
        if observation is None:
            invalid += int(status == 'READY')
            continue
        advice = _persisted_laya_advice(observation.get('advice'))
        if advice is None:
            invalid += 1
            continue
        verified += 1
        identities.append(advice.identity.model_dump())
        if type(advice.latency_ms) in (int, float) and isfinite(advice.latency_ms) and advice.latency_ms >= 0:
            latencies.append(advice.latency_ms)
        agreement = observation.get('agreement')
        if isinstance(agreement, dict):
            agreement = agreement.get('agreement')
        agreements[agreement if isinstance(agreement, str) and agreement in {'AGREE', 'DISAGREE', 'UNKNOWN'} else 'UNKNOWN'] += 1
    return {'schema_version': 'semantic-shadow-status-v1',
        'scope': 'CURRENT_ACTOR_SEMANTIC_TURN_OBSERVATIONS' if principal else 'RECORDED_SEMANTIC_TURN_OBSERVATIONS',
        'record_count': total, 'sampled_records': len(rows), 'truncated': total > len(rows),
        'by_status': dict(by_status), 'coverage_reasons': dict(coverage),
        'verified_local_sdk_inferences': verified, 'invalid_or_stale_ready_records': invalid,
        'agreement': dict(agreements), 'agreement_is_accuracy': False,
        'sdk_latency_ms': {'p50': percentile(latencies, .5) if len(latencies) >= 3 else None,
            'p95': percentile(latencies, .95) if len(latencies) >= 3 else None, 'samples': len(latencies)},
        'identity_matches': identities, 'adoption_enabled': False, 'observed_adoptions': 0,
        'call_savings': None, 'latency_savings_ms': None,
        'customer_response_waits_for_laya': False, 'bank_authorization_affected': False}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values)-1)*fraction
    lo = int(index)
    hi = min(lo+1, len(values)-1)
    return round(values[lo] + (values[hi]-values[lo])*(index-lo), 2)


def decision_status(session, principal, advisor):
    require_banking_actor(session, principal)
    rows = list(session.scalars(select(RuntimeRecord).join(Task, Task.id == RuntimeRecord.task_id)
        .where(Task.sandbox_id == principal.sandbox_id, Task.user_id == principal.user_id,
               RuntimeRecord.kind == "PARENT")))
    samples, known, unknown, inferences, compiler, versions = [], 0, 0, 0, 0, 0
    verified_identities = []
    for row in rows:
        for decision in row.metadata_json.get("decisions", []):
            versions += 1
            diagnostics = decision.get("planning_diagnostics") or {}
            calls = diagnostics.get("model_api_call_count")
            if isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0:
                known += calls
            else:
                unknown += 1
            elapsed = diagnostics.get("elapsed_ms")
            if isinstance(elapsed, (float, int)) and not isinstance(elapsed, bool) and isfinite(elapsed) and elapsed >= 0:
                samples.append(elapsed)
            compiler += int(diagnostics.get("plan_source") == "STANDARD_READ_COMPILER")
            advice = _persisted_laya_advice(decision.get("laya_advice"))
            if advice and advice.status == "READY" and advice.laya_used is True and advice.backend == "LAYA_SDK_SIDECAR" and advice.identity and advice.answers:
                inferences += 1
                verified_identities.append(advice.identity.model_dump())
    config = advisor.config
    configured = bool(config.identity and advisor.backend and not config.configuration_error)
    verified = configured and config.identity.model_dump() in verified_identities
    status = "OFF" if config.mode == "OFF" else "READY" if verified else "NOT_CONFIGURED" if not configured else "UNAVAILABLE"
    comparison = {"status": "NOT_RUN", "baseline_model_calls": None, "candidate_model_calls": None,
        "call_difference": None, "latency_difference_ms": None, "quality_guard_passed": None,
        "note": "历史调用数不是反事实节省；精确只读编译器的0调用不归功于Laya。尚无可展示的受控A/B证据。"}
    # Only a separately reviewed, small synthetic report is packaged here.
    # Never read arbitrary evidence paths or another customer's metrics.
    try:
        controlled = json.loads(Path(__file__).with_name("decision_benchmark_public.json").read_text(encoding="utf-8"))
        hashes = controlled.get("source_sha256", {})
        valid_sources = all(hashes.get(name) == hashlib.sha256(Path(__file__).with_name(name+".py").read_bytes()).hexdigest()
            for name in ("model_gateway", "intelligence", "standard_read", "decision_advisor"))
        if valid_sources and controlled.get("quality_guard_passed") is True and controlled.get("scope") == "SMALL_SYNTHETIC_CONTROLLED_AB_NOT_CURRENT_USER_COUNTERFACTUAL":
            comparison = controlled
    except (OSError, ValueError, TypeError):
        pass
    shadow = semantic_shadow_status(session, principal)
    current_verified = configured and config.identity.model_dump() in shadow.pop('identity_matches')
    return {
        "scope": "CURRENT_ACTOR_DECISION_EVIDENCE",
        "engine": {"mode": config.mode, "status": status, "inference_verified": verified,
            "calibrated": False, "adoption_enabled": False,
            "identity": config.identity.model_dump() if config.identity else None,
            "semantic_inference_verified": current_verified,
            "explanation": "本机Laya影子分类仅做观察，不跳过主规划模型、不批准业务。已记录成功推理才显示验证完成。"},
        "metrics": {"planning_versions": versions, "known_model_http_calls": known,
            "unknown_model_call_records": unknown, "actual_laya_inferences": inferences,
            "standard_read_compiler_uses": compiler,
            "planning_latency_ms": {"p50": percentile(samples, .5), "p95": percentile(samples, .95), "samples": len(samples)}},
        "comparison": comparison,
        "semantic_shadow": shadow,
        "eligibility": {"readonly_only": True, "write_authorization_affected": False}, "synthetic_bank": True}


def install_decision_routes(app, get_session, get_principal):
    @app.get("/api/v3/decision/status")
    def status(request: Request, session=Depends(get_session), principal=Depends(get_principal)):
        return decision_status(session, principal, request.app.state.model_gateway.advisor)
