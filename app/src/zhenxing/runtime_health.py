from __future__ import annotations

from datetime import timedelta, timezone
import logging
import math
import re
from threading import Event, Thread

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .models import AuditEvent, ModelInvocation, Task, utcnow
from .runtime_health_models import WorkerHeartbeat
from .runtime_models import RuntimeOperation, RuntimeRecord, WorkflowJob


HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_TTL_SECONDS = 20
logger = logging.getLogger(__name__)


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def write_worker_heartbeat(factory, worker_id: str, *, stopped: bool = False) -> bool:
    """Use a fresh transaction for each pulse; a stopped identity cannot revive."""
    if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 80:
        raise ValueError("WORKER_ID_MUST_BE_1_TO_80_CHARACTERS")
    now = utcnow()
    with factory.begin() as session:
        existing = session.get(WorkerHeartbeat, worker_id)
        if existing is None:
            session.add(WorkerHeartbeat(worker_id=worker_id, status="STOPPED" if stopped else "RUNNING",
                                        started_at=now, last_seen_at=now, stopped_at=now if stopped else None))
            return True
        if stopped:
            session.execute(update(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id).values(
                status="STOPPED", last_seen_at=now, stopped_at=now,
            ))
            return True
        # Conditional SQL prevents an in-flight late pulse from overwriting STOPPED.
        result = session.execute(update(WorkerHeartbeat).where(
            WorkerHeartbeat.worker_id == worker_id, WorkerHeartbeat.status == "RUNNING",
        ).values(last_seen_at=now))
        return bool(result.rowcount)


class Heartbeat:
    """Independent real-time pulse while the main executor waits for a model."""

    def __init__(self, factory, worker_id: str, *, interval: float = HEARTBEAT_INTERVAL_SECONDS):
        if interval <= 0:
            raise ValueError("HEARTBEAT_INTERVAL_MUST_BE_POSITIVE")
        self.factory = factory
        self.worker_id = worker_id
        self.interval = interval
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Heartbeat already started")
        write_worker_heartbeat(self.factory, self.worker_id)
        self._thread = Thread(target=self._run, name=f"heartbeat-{self.worker_id}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                write_worker_heartbeat(self.factory, self.worker_id)
            except SQLAlchemyError as error:
                # Credentials and SQL parameter values are never logged here.
                logger.warning("worker_heartbeat_failed kind=%s", type(error).__name__)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        try:
            write_worker_heartbeat(self.factory, self.worker_id, stopped=True)
        except SQLAlchemyError as error:
            logger.warning("worker_stopped_marker_failed kind=%s", type(error).__name__)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def read_runtime_health(session: Session) -> dict:
    result = {"status": "unready", "database": "unavailable", "worker": "offline",
              "online_workers": 0, "last_heartbeat_at": None,
              "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS, "clock": "REAL_UTC"}
    try:
        if session.scalar(text("SELECT 1")) != 1:
            return result
    except SQLAlchemyError:
        return result
    result["database"] = "ok"
    now = utcnow()
    try:
        alive = session.scalar(select(func.count()).select_from(WorkerHeartbeat).where(
            WorkerHeartbeat.status == "RUNNING",
            WorkerHeartbeat.last_seen_at >= now - timedelta(seconds=HEARTBEAT_TTL_SECONDS),
        ))
        latest = session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    except SQLAlchemyError:
        result["worker"] = "unavailable"
        return result
    result.update({"status": "ready" if alive else "unready", "worker": "online" if alive else "offline",
                   "online_workers": alive, "last_heartbeat_at": as_utc(latest).isoformat() if latest else None})
    return result


def _failure_kind(value) -> str:
    if not isinstance(value, str) or len(value) > 100:
        return "UNCLASSIFIED_ERROR"
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", value):
        return value
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}(?:Error|Exception)", value):
        return value
    return "UNCLASSIFIED_ERROR"


def runtime_metrics(session: Session, principal) -> dict:
    """Authenticated operational aggregates restricted to the user's tasks."""
    now = utcnow()
    owned_parent_ids = select(Task.id).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(
        Task.sandbox_id == principal.sandbox_id, Task.user_id == principal.user_id,
        RuntimeRecord.kind == "PARENT",
    )
    task_states = dict(session.execute(select(Task.state, func.count()).where(
        Task.id.in_(owned_parent_ids),
    ).group_by(Task.state)).all())
    job_states = dict(session.execute(select(WorkflowJob.status, func.count()).where(
        WorkflowJob.task_id.in_(owned_parent_ids), WorkflowJob.sandbox_id == principal.sandbox_id,
    ).group_by(WorkflowJob.status)).all())
    jobs = list(session.scalars(select(WorkflowJob).where(
        WorkflowJob.task_id.in_(owned_parent_ids), WorkflowJob.sandbox_id == principal.sandbox_id,
    )))
    due = sum(job.status == "QUEUED" and as_utc(job.next_run_at) <= now for job in jobs)
    stale = sum(job.status == "LEASED" and job.lease_expires_at is not None
                and as_utc(job.lease_expires_at) <= now for job in jobs)
    queued_ages = [max(0, (now - as_utc(job.created_at)).total_seconds()) for job in jobs if job.status == "QUEUED"]
    failures = list(session.scalars(select(AuditEvent).where(
        AuditEvent.task_id.in_(owned_parent_ids), AuditEvent.sandbox_id == principal.sandbox_id,
        AuditEvent.event_type.in_(["WORKFLOW_JOB_FAILED", "RECONCILE_RETRY_SCHEDULED"]),
    ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(10)))
    model_outcomes = dict(session.execute(select(ModelInvocation.outcome, func.count()).where(
        ModelInvocation.task_id.in_(owned_parent_ids), ModelInvocation.sandbox_id == principal.sandbox_id,
        ModelInvocation.purpose == "INTENT_AND_PLAN_PROPOSAL",
    ).group_by(ModelInvocation.outcome)).all())
    model_total = sum(model_outcomes.values())
    fallback = model_outcomes.get("FALLBACK", 0)
    from .intelligence import domain_for_action
    activity = {}
    for kind, status, count in session.execute(select(RuntimeOperation.action_kind, RuntimeOperation.status, func.count()).where(
            RuntimeOperation.parent_task_id.in_(owned_parent_ids), RuntimeOperation.sandbox_id == principal.sandbox_id,
            RuntimeOperation.active == 1).group_by(RuntimeOperation.action_kind, RuntimeOperation.status)):
        domain = domain_for_action(kind)
        item = activity.setdefault(domain, {"domain": domain, "operations": 0, "by_status": {}})
        item["operations"] += count
        item["by_status"][status] = item["by_status"].get(status, 0) + count
    runtime_records = list(session.scalars(select(RuntimeRecord).where(RuntimeRecord.task_id.in_(owned_parent_ids))))
    # New plans retain observations by version; a legacy latest observation is
    # included once, never used to invent observations for its older versions.
    diagnostics = []
    for row in runtime_records:
        observed_versions = set()
        for decision in row.metadata_json.get("decisions", []):
            observed = decision.get("planning_diagnostics")
            if observed is not None:
                diagnostics.append(observed)
                observed_versions.add(decision.get("plan_version"))
        latest = row.metadata_json.get("planning_diagnostics")
        if latest is not None and row.plan_version not in observed_versions:
            diagnostics.append(latest)
    advice_modes, advice_statuses, verified_advice = {}, {}, 0
    for row in runtime_records:
        advice = row.metadata_json.get("laya_advice")
        if advice:
            advice_modes[advice.get("mode", "UNKNOWN")] = advice_modes.get(advice.get("mode", "UNKNOWN"), 0) + 1
            advice_statuses[advice.get("status", "UNKNOWN")] = advice_statuses.get(advice.get("status", "UNKNOWN"), 0) + 1
            if advice.get("status") == "READY" and advice.get("backend") == "LAYA_SDK_SIDECAR" and advice.get("laya_used") and advice.get("identity"):
                verified_advice += 1
    latencies = sorted(row["elapsed_ms"] for row in diagnostics if isinstance(row.get("elapsed_ms"), (int, float)))
    def percentile(fraction):
        if not latencies:
            return None
        return round(latencies[min(len(latencies)-1, max(0, math.ceil(len(latencies)*fraction)-1))], 2)
    known_calls = [row["model_api_call_count"] for row in diagnostics if isinstance(row.get("model_api_call_count"), int)]
    return {
        "scope": "CURRENT_USER", "observed_at": now.isoformat(), "clock": "REAL_UTC",
        "runtime_health": read_runtime_health(session),
        "tasks": {"total": sum(task_states.values()), "by_state": task_states},
        "jobs": {"total": sum(job_states.values()), "by_status": job_states, "queued_due": due,
                 "stale_leases": stale, "oldest_queued_age_seconds": round(max(queued_ages), 2) if queued_ages else None},
        "recent_failures": [{"task_id": event.task_id, "occurred_at": as_utc(event.created_at).isoformat(),
                             "event_type": event.event_type, "kind": _failure_kind(event.payload_json.get("kind", "RECONCILE")),
                             "error_type": _failure_kind(event.payload_json.get("error"))} for event in failures],
        "models": {"planning_invocations": model_total, "fallback_count": fallback,
                   "fallback_rate": round(fallback / model_total, 4) if model_total else None,
                   "by_outcome": model_outcomes, "observed_api_calls": sum(known_calls) if known_calls else None,
                   "api_call_counts_unknown": max(0, model_total - len(known_calls)),
                   "api_call_count_scope": "ALL_RECORDED_PLAN_OBSERVATIONS",
                   "api_calls_are_attempts": True},
        "scenario_activity": list(activity.values()),
        "planning_latency_ms": {"sample_count": len(latencies), "p50": percentile(0.50), "p95": percentile(0.95),
                                "scope": "ALL_RECORDED_PLAN_OBSERVATIONS", "includes_rule_and_model": True},
        "decision_advice": {"scope": "LATEST_PLAN_PER_TASK", "by_mode": advice_modes, "by_status": advice_statuses,
                            "actual_laya_inference_verified": bool(verified_advice),
                            "verified_inference_records": verified_advice, "scores_are_accuracy": False},
    }
