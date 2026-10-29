from __future__ import annotations

import json
import time

from fastapi import Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select

from .authentication import as_utc, create_challenge, verify_challenge
from .contracts import CancelTaskRequest, ClarifyTaskRequest, ConfirmTaskRequest, CreateTaskRequest, DomainError, StrictModel
from .models import AuditEvent, Confirmation, Task
from .runtime import accept_task, cancel_runtime, clarify_runtime, confirm_runtime, task_trace
from .runtime_models import RuntimeRecord
from .services import owned_task


class ChallengeRequest(StrictModel):
    task_id: str
    confirmation_id: str


class VerifyRequest(StrictModel):
    code: str = Field(pattern=r"^\d{6}$")


class AdvanceRequest(StrictModel):
    days: int = Field(ge=1, le=365, strict=True)


class FaultRequest(StrictModel):
    mode: str = Field(pattern=r"^(NONE|PRE_COMMIT_FAILURE|AFTER_COMMIT_RESPONSE_LOST|DELAY|PRICE_CHANGED)$")


class CompensationRequest(StrictModel):
    expected_task_version: int = Field(ge=1, strict=True)
    operation_id: str = Field(min_length=1, max_length=36)


def install_runtime_routes(app, get_session, get_principal):
    def accepted(session, principal, task):
        record = session.get(RuntimeRecord, task.id)
        return {"schema_version": "2.0", "request_id": record.metadata_json.get("request_id"),
                "task_id": task.id, "state": task.state, "version": task.version}

    @app.get("/api/v2/demo/profiles")
    def profiles():
        from .simulation import list_profiles
        return list_profiles()

    @app.post("/api/v2/tasks", status_code=202)
    def post_task(payload: CreateTaskRequest, session=Depends(get_session), principal=Depends(get_principal),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        task = accept_task(session, principal, payload.message, idempotency_key)
        session.commit()
        return accepted(session, principal, task)

    @app.get("/api/v2/tasks")
    def tasks(session=Depends(get_session), principal=Depends(get_principal)):
        from .customer_history_archive import visible_history
        rows = session.scalars(select(Task).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(
            RuntimeRecord.kind == "PARENT", Task.sandbox_id == principal.sandbox_id,
            Task.user_id == principal.user_id,
            visible_history(session, principal, Task.created_at),
        ).order_by(Task.updated_at.desc()).limit(50))
        return [{"task_id": task.id, "state": task.state, "version": task.version,
                 "message": task.message, "intent_type": task.intent_type,
                 "updated_at": as_utc(task.updated_at).isoformat()} for task in rows]

    @app.get("/api/v2/tasks/{task_id}/trace")
    def trace(task_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        return task_trace(session, principal, task_id)

    @app.post("/api/v2/tasks/{task_id}/confirm", status_code=202)
    def confirm(task_id: str, payload: ConfirmTaskRequest,
                session=Depends(get_session), principal=Depends(get_principal)):
        task = confirm_runtime(session, principal, task_id, payload)
        session.commit()
        return accepted(session, principal, task)

    @app.post("/api/v2/tasks/{task_id}/clarify", status_code=202)
    def clarify(task_id: str, payload: ClarifyTaskRequest,
                session=Depends(get_session), principal=Depends(get_principal)):
        task = clarify_runtime(session, principal, task_id, payload.message, payload.expected_task_version)
        session.commit()
        return accepted(session, principal, task)

    @app.post("/api/v2/tasks/{task_id}/revise", status_code=202)
    def revise(task_id: str, payload: ClarifyTaskRequest,
               session=Depends(get_session), principal=Depends(get_principal)):
        task = clarify_runtime(session, principal, task_id, payload.message, payload.expected_task_version, revise=True)
        session.commit()
        return accepted(session, principal, task)

    @app.post("/api/v2/tasks/{task_id}/cancel")
    def cancel(task_id: str, payload: CancelTaskRequest,
               session=Depends(get_session), principal=Depends(get_principal)):
        task = cancel_runtime(session, principal, task_id, payload.expected_task_version)
        session.commit()
        return accepted(session, principal, task)

    @app.post("/api/v2/tasks/{task_id}/compensate", status_code=202)
    def compensate(task_id: str, payload: CompensationRequest,
                   session=Depends(get_session), principal=Depends(get_principal)):
        from .compensation import propose_compensation
        task = propose_compensation(session, principal, task_id,
            payload.operation_id, payload.expected_task_version)
        session.commit()
        return accepted(session, principal, task)

    @app.post("/api/v2/auth/challenges")
    def challenge(payload: ChallengeRequest, session=Depends(get_session), principal=Depends(get_principal)):
        task = owned_task(session, principal, payload.task_id, lock=True)
        confirmation = session.scalar(select(Confirmation).where(
            Confirmation.id == payload.confirmation_id, Confirmation.task_id == task.id,
            Confirmation.sandbox_id == principal.sandbox_id, Confirmation.user_id == principal.user_id))
        if confirmation is None:
            raise DomainError("CONFIRMATION_NOT_FOUND", "确认记录不存在", 404)
        result = create_challenge(session, task=task, confirmation=confirmation, user_id=principal.user_id)
        session.commit()
        return result

    @app.post("/api/v2/auth/challenges/{challenge_id}/verify")
    def verify(challenge_id: str, payload: VerifyRequest,
               session=Depends(get_session), principal=Depends(get_principal)):
        result = verify_challenge(session, challenge_id=challenge_id, code=payload.code,
                                  user_id=principal.user_id, sandbox_id=principal.sandbox_id)
        session.commit()  # Bad-code attempts must persist even when returning an error.
        if not result["verified"]:
            raise DomainError(result["error"], "验证码错误或认证已失效", 403)
        return result

    @app.get("/api/v2/simulation")
    def simulation_state(session=Depends(get_session), principal=Depends(get_principal)):
        from .permissions import require_sandbox_owner
        require_sandbox_owner(session, principal)
        from .simulation import inspect_simulation
        return inspect_simulation(session, principal.sandbox_id)

    @app.post("/api/v2/simulation/advance")
    def advance(payload: AdvanceRequest, session=Depends(get_session), principal=Depends(get_principal)):
        from .permissions import require_sandbox_owner
        require_sandbox_owner(session, principal)
        from .simulation import advance_business_time
        result = advance_business_time(session, principal.sandbox_id, payload.days)
        session.commit()
        return result

    @app.post("/api/v2/simulation/fault")
    def fault(payload: FaultRequest, session=Depends(get_session), principal=Depends(get_principal)):
        from .permissions import require_sandbox_owner
        require_sandbox_owner(session, principal)
        from .simulation import set_fault_mode
        result = set_fault_mode(session, principal.sandbox_id, payload.mode)
        session.commit()
        return result

    @app.get("/api/v2/tasks/{task_id}/events")
    def events(task_id: str, request: Request, last_event_id: int = 0,
               session=Depends(get_session), principal=Depends(get_principal)):
        owned_task(session, principal, task_id)
        factory = request.app.state.session_factory

        def stream():
            cursor = max(0, last_event_id)
            for _ in range(60):
                with factory() as poll:
                    rows = list(poll.scalars(select(AuditEvent).where(
                        AuditEvent.task_id == task_id, AuditEvent.sandbox_id == principal.sandbox_id,
                        AuditEvent.seq > cursor).order_by(AuditEvent.seq)))
                    task = poll.get(Task, task_id)
                    state, version = task.state, task.version
                for event in rows:
                    cursor = event.seq
                    payload = {"seq": event.seq, "type": event.event_type, "payload": event.payload_json,
                               "task_state": state, "task_version": version}
                    yield f"id: {cursor}\nevent: task-event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if not rows and state in {"SUCCEEDED", "FAILED", "CLOSED"}:
                    return
                time.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
