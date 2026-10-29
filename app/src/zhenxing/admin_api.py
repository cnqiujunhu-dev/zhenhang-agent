from __future__ import annotations

from typing import Literal
import re

from fastapi import Depends, Query
from pydantic import Field, SecretStr
from sqlalchemy import func, literal, select, union_all
from sqlalchemy.exc import IntegrityError

from .access_models import AccessAudit, LoginAccount
from .accounts import create_customer_account, public_account, require_account_role, reset_account_password, set_account_status
from .contracts import DomainError, StrictModel
from .models import AuditEvent, ModelInvocation, PolicyDecisionRecord, Task, ToolCall
from .runtime_health import read_runtime_health
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord, WorkflowJob
from .simulation import as_utc
from .observation_metrics import collect_observation_metrics


_SECRET_KEYS = {"token", "accesstoken", "refreshtoken", "tokenhash", "apikey", "authorization", "bearer",
                "code", "democode", "otp", "verificationcode", "codehash", "salt", "secret", "reasoningcontent",
                "authchallengeid", "challengeid", "confirmationid", "chainofthought", "rawprompt", "rawresponse", "headers"}


def redact_observation(value):
    """Only the returned projection is sanitized; original signed evidence is untouched."""
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _SECRET_KEYS or "password" in normalized or normalized.endswith("secret"):
                continue
            safe_key = redact_observation(str(key))
            if key == "allowed_actions":
                safe[safe_key] = []
            elif key == "pending_confirmation":
                safe[safe_key] = None
            elif key in {"can_compensate", "bank_confirmation_permitted"}:
                safe[safe_key] = False
            else:
                safe[safe_key] = redact_observation(item)
        return safe
    if isinstance(value, list):
        return [redact_observation(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"pbkdf2_sha256\$\d+\$[a-f0-9]+\$[a-f0-9]+", "[REDACTED_PASSWORD_HASH]", value, flags=re.I)
        value = re.sub(r"\bsk-[A-Za-z0-9_\-]+", "[REDACTED_KEY]", value, flags=re.I)
        value = re.sub(r"\bBearer\s+[^\s，,；;。\"']+", "[REDACTED_SESSION]", value, flags=re.I)
        # Demo sessions issue exactly 32 random bytes -> 43 base64url chars.
        # Do not redact SHA256 plan/audit hashes (64 chars) or UUID identities.
        value = re.sub(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])", "[REDACTED_SESSION]", value)
        value = re.sub(r"((?:验证码|动态码|口令|密码|OTP|password|api[_ -]?key|access[_ -]?token)\s*[：:=]?\s*)[^\s，,；;。\"']+",
                       r"\1[REDACTED_SECRET]", value, flags=re.I)
        return value
    return value


def _planning_observations(session, *, limit=5000, per_record_limit=50):
    observations = []
    for record in session.scalars(select(RuntimeRecord).where(RuntimeRecord.kind == "PARENT")
            .order_by(RuntimeRecord.created_at.desc(), RuntimeRecord.task_id).limit(limit)):
        metadata = record.metadata_json if isinstance(record.metadata_json, dict) else {}
        observed_versions = set()
        decisions = metadata.get("decisions") if isinstance(metadata.get("decisions"), list) else []
        for decision in decisions[-per_record_limit:]:
            if not isinstance(decision, dict):
                continue
            diagnostics = decision.get("planning_diagnostics")
            if isinstance(diagnostics, dict):
                observations.append(diagnostics)
                version = decision.get("plan_version")
                if type(version) is int:
                    observed_versions.add(version)
        latest = metadata.get("planning_diagnostics")
        if isinstance(latest, dict) and record.plan_version not in observed_versions:
            observations.append(latest)
    return observations


def _model_aggregates(session):
    with session.no_autoflush:
        return _model_aggregates_readonly(session)


def _model_aggregates_readonly(session):
    purpose_filter = ModelInvocation.purpose == "INTENT_AND_PLAN_PROPOSAL"
    row_count = session.scalar(select(func.count()).select_from(ModelInvocation).where(purpose_filter))
    outcomes = dict(session.execute(select(ModelInvocation.outcome, func.count()).where(purpose_filter)
                                    .group_by(ModelInvocation.outcome)).all())
    providers = dict(session.execute(select(ModelInvocation.provider_label, func.count()).where(purpose_filter)
                                     .group_by(ModelInvocation.provider_label)).all())
    purposes = collect_observation_metrics(session)
    truncated = purposes["by_purpose"]["planning"]["truncated"]
    observed = _planning_observations(session)
    expected = max(row_count, len(observed))
    known_calls = [row["model_api_call_count"] for row in observed
                   if type(row.get("model_api_call_count")) is int and row["model_api_call_count"] >= 0]
    routes = [row for row in observed if "routing_mode" in row]
    actual_providers = {}
    for row in routes:
        provider = row.get("actual_provider", "UNKNOWN")
        provider = provider if isinstance(provider, str) else "UNKNOWN"
        actual_providers[provider] = actual_providers.get(provider, 0) + 1
    route_fallbacks = sum(row.get("degraded") is True for row in routes)
    usage = {}
    for field in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens"):
        known = [row["usage"][field] for row in observed if isinstance(row.get("usage"), dict)
                 and type(row["usage"].get(field)) is int and row["usage"][field] >= 0]
        usage[field] = {"observed_sum": sum(known) if known else None,
                        "complete_total": sum(known) if expected and len(known) == expected and not truncated else None,
                        "known_observations": len(known), "unknown_observations": expected - len(known)}
    return redact_observation({"scope": "BOUNDED_RECORDED_PLAN_OBSERVATIONS" if truncated else "ALL_RECORDED_PLAN_OBSERVATIONS", "planning_invocations": row_count,
        "by_outcome": outcomes, "by_provider": providers, "diagnostics_record_count": len(observed),
        "fallback_count": outcomes.get("FALLBACK", 0),
        "fallback_rate": outcomes.get("FALLBACK", 0) / row_count if row_count else None,
        "observed_api_calls": sum(known_calls) if known_calls else None,
        "api_call_counts_unknown": expected - len(known_calls), "api_calls_are_attempts": True,
        "route_observations": len(routes), "route_fallback_count": route_fallbacks,
        "route_fallback_rate": route_fallbacks / len(routes) if routes else None,
        "by_actual_provider": actual_providers, "token_usage": usage,
        "unknown_counts_are_zero": False, "raw_reasoning_retained": False,
        "legacy_diagnostics_sample_truncated": truncated, "purpose_observations": purposes})


def _audit_page(session, kind, page, page_size):
    sources = []
    if kind in {"ACCESS", "ALL"}:
        sources.append(select(AccessAudit.id.label("audit_id"), literal("ACCESS").label("kind"),
            AccessAudit.actor_account_id, AccessAudit.target_account_id, literal(None).label("task_id"),
            literal(None).label("seq"), AccessAudit.action, AccessAudit.created_at, AccessAudit.payload_json))
    if kind in {"WORKFLOW", "ALL"}:
        sources.append(select(AuditEvent.id.label("audit_id"), literal("WORKFLOW").label("kind"),
            literal(None).label("actor_account_id"), literal(None).label("target_account_id"), AuditEvent.task_id,
            AuditEvent.seq, AuditEvent.event_type.label("action"), AuditEvent.created_at, AuditEvent.payload_json))
    combined = (union_all(*sources) if len(sources) > 1 else sources[0]).subquery()
    total = session.scalar(select(func.count()).select_from(combined))
    rows = session.execute(select(combined).order_by(combined.c.created_at.desc(), combined.c.audit_id)
                           .offset((page-1)*page_size).limit(page_size)).mappings()
    items = []
    for row in rows:
        payload = row["payload_json"] or {}
        reason = payload.get("reason") if row["kind"] == "ACCESS" else None
        codes = payload.get("reason_codes", [])
        if isinstance(codes, str):
            codes = [codes]
        if not isinstance(codes, list):
            codes = []
        error = payload.get("error")
        if isinstance(error, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,119}", error):
            codes = [*codes, error]
        items.append(redact_observation({"audit_id": row["audit_id"], "kind": row["kind"],
            "actor_account_id": row["actor_account_id"], "target_account_id": row["target_account_id"],
            "task_id": row["task_id"], "seq": row["seq"], "action": row["action"],
            "created_at": as_utc(row["created_at"]).isoformat(), "reason": str(reason)[:200] if reason else None,
            "reason_codes": [code for code in codes if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,119}", code)]}))
    return {"items": items, "total": total, "page": page, "page_size": page_size, "kind": kind, "read_only": True}


def _page(session, query, page, page_size, render):
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {"items": [render(row) for row in session.scalars(query.offset((page-1)*page_size).limit(page_size))],
            "total": total, "page": page, "page_size": page_size}


def _task_row(session, task):
    account = session.scalar(select(LoginAccount).where(LoginAccount.user_id == task.user_id))
    return {"task_id": task.id, "username": account.username if account else None,
            "user_id": task.user_id, "sandbox_id": task.sandbox_id, "intent_type": task.intent_type,
            "state": task.state, "version": task.version, "created_at": as_utc(task.created_at).isoformat(),
            "updated_at": as_utc(task.updated_at).isoformat()}


def _job_row(job):
    return {"job_id": job.id, "task_id": job.task_id, "kind": job.kind, "status": job.status,
            "attempts": job.attempts, "max_attempts": job.max_attempts,
            "created_at": as_utc(job.created_at).isoformat(), "next_run_at": as_utc(job.next_run_at).isoformat(),
            "lease_expires_at": as_utc(job.lease_expires_at).isoformat() if job.lease_expires_at else None}


class AdminCreateAccount(StrictModel):
    username: str = Field(min_length=3, max_length=32)
    password: SecretStr = Field(max_length=128)
    display_name: str | None = Field(default=None, max_length=80)
    profile: str = "young_professional"


class AdminAccountStatus(StrictModel):
    status: Literal["ACTIVE", "DISABLED"]
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


class AdminPasswordReset(StrictModel):
    new_password: SecretStr = Field(max_length=128)
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


def install_admin_routes(app, get_session, get_principal):
    from .admin_life_plans import install_admin_life_plan_routes
    install_admin_life_plan_routes(app,get_session,get_principal)
    def admin(session, principal):
        return require_account_role(session, principal, {"ADMIN"})

    @app.get("/api/v2/admin/capability-health")
    def capability_observations(session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        from .capability_health import capability_health
        return capability_health(session)

    @app.get("/api/v2/admin/overview")
    def overview(session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        return {"scope": "ADMIN_READ_ONLY_OPERATIONS", "runtime_health": read_runtime_health(session),
                "accounts": {"total": session.scalar(select(func.count()).select_from(LoginAccount)),
                             "by_role": dict(session.execute(select(LoginAccount.role, func.count()).group_by(LoginAccount.role)).all()),
                             "by_status": dict(session.execute(select(LoginAccount.status, func.count()).group_by(LoginAccount.status)).all())},
                "tasks": {"total": session.scalar(select(func.count()).select_from(Task)),
                          "by_state": dict(session.execute(select(Task.state, func.count()).group_by(Task.state)).all())},
                "jobs": {"total": session.scalar(select(func.count()).select_from(WorkflowJob)),
                         "by_status": dict(session.execute(select(WorkflowJob.status, func.count()).group_by(WorkflowJob.status)).all()),
                         "status_scope": "HISTORICAL_JOB_ATTEMPTS",
                         "active_unknown_operations": session.scalar(select(func.count()).select_from(RuntimeOperation).where(
                             RuntimeOperation.active == 1, RuntimeOperation.status == "UNKNOWN"))},
                "policies": {"total": session.scalar(select(func.count()).select_from(PolicyDecisionRecord)),
                             "by_decision": dict(session.execute(select(PolicyDecisionRecord.decision, func.count()).group_by(PolicyDecisionRecord.decision)).all())},
                "models": _model_aggregates(session),
                "bank_confirmation_permitted": False, "customer_data_edit_permitted": False}

    @app.get("/api/v2/admin/accounts")
    def accounts(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                 session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        return _page(session, select(LoginAccount).order_by(LoginAccount.username), page, page_size, public_account)

    @app.get('/api/v2/admin/credit-billing-status')
    @app.get('/api/v2/admin/account-risk-labels', include_in_schema=False)
    def account_risk_labels(session=Depends(get_session),principal=Depends(get_principal)):
        admin(session,principal)
        from .recipient_account_risk import credit_billing_status,VERSION
        from .credit_models import CreditAccount,CreditStatement
        candidates=select(LoginAccount).join(CreditAccount,CreditAccount.user_id==LoginAccount.user_id).join(CreditStatement,CreditStatement.credit_account_id==CreditAccount.id).where(LoginAccount.role.in_(['CUSTOMER','DEV_TEST'])).distinct()
        count=session.scalar(select(func.count()).select_from(candidates.subquery()))
        rows=[credit_billing_status(session,account)for account in session.scalars(candidates.order_by(LoginAccount.username).limit(100))]
        return {'policy_version':VERSION,'scope':'ADMIN_PRIVATE_CREDIT_BILLING','accounts':rows,
            'population_scope':'ACCOUNTS_WITH_CLOSED_STATEMENTS','total_candidates':count,'truncated':count>100,
            'transfer_risk_input':False,
            'description':'已出账款项的待还状态，仅用于信用卡账务服务；不等同逾期或涉诈，不影响其他客户向该账户转账。'}

    @app.post("/api/v2/admin/accounts", status_code=201)
    def create(payload: AdminCreateAccount, session=Depends(get_session), principal=Depends(get_principal)):
        actor = admin(session, principal)
        try:
            account = create_customer_account(session, payload.username, payload.password.get_secret_value(), payload.profile,
                                              actor=actor, display_name=payload.display_name)
            from .demo_carbon_funding import fund_demo_carbon_points
            fund_demo_carbon_points(session, account.sandbox_id, account.user_id)
            from .accounts import initialize_personal_services
            initialize_personal_services(session, account)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise DomainError("USERNAME_UNAVAILABLE", "该账号不可使用", 409) from None
        return public_account(account)

    @app.patch("/api/v2/admin/accounts/{account_id}/status")
    def status(account_id: str, payload: AdminAccountStatus, session=Depends(get_session), principal=Depends(get_principal)):
        result = set_account_status(session, admin(session, principal), account_id, payload.status, payload.expected_version, payload.reason)
        session.commit()
        return result

    @app.post("/api/v2/admin/accounts/{account_id}/reset-password")
    def reset(account_id: str, payload: AdminPasswordReset, session=Depends(get_session), principal=Depends(get_principal)):
        result = reset_account_password(session, admin(session, principal), account_id,
                                        payload.new_password.get_secret_value(), payload.expected_version, payload.reason)
        session.commit()
        return result

    @app.get("/api/v2/admin/tasks")
    def tasks(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
              username: str | None = Query(default=None, max_length=32),
              session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        query = select(Task).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(RuntimeRecord.kind == "PARENT")
        if username:
            query = query.join(LoginAccount, LoginAccount.user_id == Task.user_id).where(LoginAccount.username == username.strip().lower())
        return _page(session, query.order_by(Task.created_at.desc()), page, page_size, lambda task: _task_row(session, task))

    @app.get("/api/v2/admin/tasks/{task_id}/trace")
    def trace(task_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        task = session.get(Task, task_id)
        if task is None:
            raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
        record = session.get(RuntimeRecord, task.id)
        projection = None
        if record and record.kind == "PARENT":
            from .runtime import task_trace
            from .services import Principal
            projection = redact_observation(task_trace(session, Principal(task.user_id, task.sandbox_id), task.id))
        child_ids = list(session.scalars(select(Task.id).join(RuntimeOperation, RuntimeOperation.child_task_id == Task.id).where(
            RuntimeOperation.parent_task_id == task.id, RuntimeOperation.sandbox_id == task.sandbox_id,
            Task.sandbox_id == task.sandbox_id, Task.user_id == task.user_id)))
        tool_query = select(ToolCall).where(ToolCall.sandbox_id == task.sandbox_id, ToolCall.task_id.in_([task.id, *child_ids]))
        tool_total = session.scalar(select(func.count()).select_from(tool_query.subquery()))
        tool_rows = list(session.scalars(tool_query.order_by(ToolCall.created_at.desc(), ToolCall.id).limit(100)))
        snapshot_query = select(PlanSnapshot).where(PlanSnapshot.task_id == task.id)
        snapshot_total = session.scalar(select(func.count()).select_from(snapshot_query.subquery()))
        snapshots = list(session.scalars(snapshot_query.order_by(PlanSnapshot.version.desc()).limit(20)))
        from .execution_provenance import project_execution_provenance
        return redact_observation({**_task_row(session, task), "read_only": True, "runtime_trace": projection,
                **project_execution_provenance(session, task),
                "tool_calls": [{"tool_call_id": row.id, "task_id": row.task_id, "tool_name": row.tool_name,
                                "status": row.status, "idempotency_key": row.idempotency_key, "request_hash": row.request_hash,
                                "result": row.result_json, "created_at": as_utc(row.created_at).isoformat()} for row in tool_rows],
                "tool_calls_total": tool_total, "tool_calls_truncated": tool_total > len(tool_rows),
                "plan_snapshots": [{"snapshot_id": row.id, "version": row.version, "plan_hash": row.plan_hash,
                                    "proposal": row.proposal_json, "plan": row.plan_json,
                                    "created_at": as_utc(row.created_at).isoformat()} for row in snapshots],
                "plan_snapshots_total": snapshot_total, "plan_snapshots_truncated": snapshot_total > len(snapshots),
                "operations": [{"operation_id": row.id, "action_kind": row.action_kind, "status": row.status,
                                "active": bool(row.active), "plan_version": row.plan_version}
                               for row in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id))],
                "events": [{"seq": row.seq, "event_type": row.event_type, "created_at": as_utc(row.created_at).isoformat()}
                           for row in session.scalars(select(AuditEvent).where(AuditEvent.task_id == task.id).order_by(AuditEvent.seq))],
                "bank_confirmation_permitted": False})

    @app.get("/api/v2/admin/jobs")
    def jobs(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
             session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        return _page(session, select(WorkflowJob).order_by(WorkflowJob.created_at.desc()), page, page_size, _job_row)

    @app.get("/api/v2/admin/audit")
    def audit(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
              kind: Literal["ACCESS", "WORKFLOW", "ALL"] = Query(default="ACCESS"),
              session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        return _audit_page(session, kind, page, page_size)

    @app.get("/api/v2/admin/policies")
    def policies(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                 session=Depends(get_session), principal=Depends(get_principal)):
        admin(session, principal)
        return _page(session, select(PolicyDecisionRecord).order_by(PolicyDecisionRecord.created_at.desc()), page, page_size,
                     lambda row: {"policy_id": row.id, "task_id": row.task_id, "sandbox_id": row.sandbox_id,
                                  "node_key": row.node_key, "decision": row.decision, "policy_version": row.policy_version,
                                  "reason_codes": row.reason_codes_json, "created_at": as_utc(row.created_at).isoformat()})
