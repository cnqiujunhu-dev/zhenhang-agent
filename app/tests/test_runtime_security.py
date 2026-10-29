from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from zhenxing.app import create_app
from zhenxing.contracts import DomainError
from zhenxing.intelligence import rule_proposal
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import Account, Confirmation, LedgerEntry, Task, ToolCall, Transaction, Transfer, utcnow
from zhenxing.runtime import run_one_job
from zhenxing.runtime_models import AuthChallenge, PlanSnapshot, RuntimeOperation, WorkflowJob


class HookGateway(ModelGateway):
    def __init__(self):
        super().__init__(RuleIntentModel())
        self.hook = None

    def propose(self, message, context=None):
        hook, self.hook = self.hook, None
        if hook:
            hook(message, context)
        return rule_proposal(message)


@pytest.fixture
def runtime(tmp_path):
    gateway = HookGateway()
    app = create_app(f"sqlite:///{(tmp_path / 'runtime-security.db').as_posix()}", gateway,
                     allow_legacy_commands=True)
    with TestClient(app) as http:
        demo = http.post("/api/v1/demo/sessions").json()
        headers = {"Authorization": f"Bearer {demo['token']}"}
        yield http, headers, app.state.session_factory, gateway, demo


def trace_for(runtime, task_id):
    http, headers, _factory, _gateway, _demo = runtime
    response = http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers)
    assert response.status_code == 200
    return response.json()


def accept(runtime, message="给房东转100元"):
    http, headers, _factory, _gateway, _demo = runtime
    response = http.post("/api/v2/tasks", headers=headers, json={"message": message})
    assert response.status_code == 202
    return response.json()["task_id"]


def begin(runtime, message="给房东转100元"):
    task_id = accept(runtime, message)
    assert run_one_job(runtime[2], runtime[3]) is True
    return trace_for(runtime, task_id)


def challenge_for(runtime, trace, *, verify=True):
    http, headers, _factory, _gateway, _demo = runtime
    pending = trace["pending_confirmation"]
    response = http.post("/api/v2/auth/challenges", headers=headers,
                         json={"task_id": pending["task_id"], "confirmation_id": pending["confirmation_id"]})
    assert response.status_code == 200
    challenge = response.json()
    if verify:
        result = http.post(f"/api/v2/auth/challenges/{challenge['challenge_id']}/verify", headers=headers,
                           json={"code": challenge["demo_code"]})
        assert result.status_code == 200
    return challenge


def confirm_body(trace, challenge_id=None):
    pending = trace["pending_confirmation"]
    return {"confirmation_id": pending["confirmation_id"], "plan_hash": pending["plan_hash"],
            "expected_task_version": pending["expected_task_version"], "auth_challenge_id": challenge_id}


def authorize(runtime, trace):
    http, headers, _factory, _gateway, _demo = runtime
    challenge = challenge_for(runtime, trace)
    body = confirm_body(trace, challenge["challenge_id"])
    response = http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body)
    assert response.status_code == 202
    return challenge, body


def balance(runtime):
    http, headers, *_ = runtime
    return http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"]


def test_every_legacy_write_rejects_managed_child_even_in_explicit_fixture_mode(runtime):
    http, headers, factory, _gateway, _demo = runtime
    trace = begin(runtime)
    pending = trace["pending_confirmation"]
    child_id = pending["task_id"]
    body = confirm_body(trace)
    requests = [
        (f"/api/v1/tasks/{child_id}/confirm", body),
        (f"/api/v1/tasks/{child_id}/cancel", {"expected_task_version": pending["expected_task_version"]}),
        (f"/api/v1/tasks/{child_id}/clarify", {"expected_task_version": pending["expected_task_version"], "message": "改成200元"}),
        (f"/api/v1/tasks/{child_id}/clarify-payee", {"expected_task_version": pending["expected_task_version"], "payee_id": "any-payee"}),
    ]
    before = balance(runtime)
    for path, payload in requests:
        response = http.post(path, headers={**headers, "Idempotency-Key": "legacy-bypass"}, json=payload)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "RUNTIME_COMMAND_REQUIRED"
    assert trace_for(runtime, trace["task_id"])["state"] == "AWAITING_CONFIRMATION"
    assert balance(runtime) == before
    with factory() as db:
        assert db.scalar(select(ToolCall)) is None


def test_production_default_legacy_write_is_gone_but_reads_remain(tmp_path, monkeypatch):
    monkeypatch.delenv("ZX_ENABLE_LEGACY_COMMANDS", raising=False)
    app = create_app(f"sqlite:///{(tmp_path / 'production-default.db').as_posix()}", HookGateway())
    with TestClient(app) as http:
        demo = http.post("/api/v1/demo/sessions").json()
        headers = {"Authorization": f"Bearer {demo['token']}"}
        assert http.post("/api/v1/tasks", headers=headers, json={"message": "给房东转100元"}).status_code == 410
        assert http.get("/api/v1/accounts", headers=headers).status_code == 200
        assert http.post("/api/v2/tasks", headers=headers, json={"message": "只分析账单"}).status_code == 202


def test_five_wrong_auth_answers_lock_and_cannot_later_be_verified(runtime):
    http, headers, factory, _gateway, _demo = runtime
    trace = begin(runtime)
    challenge = challenge_for(runtime, trace, verify=False)
    wrong = "000000" if challenge["demo_code"] != "000000" else "111111"
    path = f"/api/v2/auth/challenges/{challenge['challenge_id']}/verify"
    for _ in range(5):
        assert http.post(path, headers=headers, json={"code": wrong}).status_code == 403
    assert http.post(path, headers=headers, json={"code": challenge["demo_code"]}).status_code == 403
    with factory() as db:
        saved = db.get(AuthChallenge, challenge["challenge_id"])
        assert saved.attempts == 5 and saved.status == "LOCKED"
        assert db.scalar(select(Transfer)) is None


def test_expired_and_other_sandbox_auth_grants_never_authorize(runtime):
    http, headers, factory, _gateway, _demo = runtime
    trace = begin(runtime)
    grant = challenge_for(runtime, trace)
    other = http.post("/api/v1/demo/sessions").json()
    other_headers = {"Authorization": f"Bearer {other['token']}"}
    assert http.post(f"/api/v2/auth/challenges/{grant['challenge_id']}/verify", headers=other_headers,
                     json={"code": grant["demo_code"]}).status_code == 404
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=other_headers,
                     json=confirm_body(trace, grant["challenge_id"])).status_code == 404
    with factory.begin() as db:
        db.get(AuthChallenge, grant["challenge_id"]).expires_at = utcnow() - timedelta(seconds=1)
    response = http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers,
                         json=confirm_body(trace, grant["challenge_id"]))
    assert response.status_code == 403 and response.json()["error"]["code"] == "AUTH_GRANT_INVALID"
    with factory() as db:
        assert db.scalar(select(WorkflowJob).where(WorkflowJob.kind == "EXECUTE")) is None


def test_expired_confirmation_is_rejected_even_if_later_verified_grant_is_still_valid(runtime):
    http, headers, factory, _gateway, _demo = runtime
    trace = begin(runtime)
    grant = challenge_for(runtime, trace)
    body = confirm_body(trace, grant["challenge_id"])
    with factory.begin() as db:
        # A challenge can be issued near the end of the confirmation window.
        # Its own lifetime does not extend the action's original confirmation.
        db.get(Confirmation, body["confirmation_id"]).expires_at = utcnow() - timedelta(seconds=1)
    response = http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFIRMATION_EXPIRED"
    with factory() as db:
        assert db.get(AuthChallenge, grant["challenge_id"]).status == "VERIFIED"
        assert db.scalar(select(WorkflowJob).where(WorkflowJob.kind == "EXECUTE")) is None
        assert db.scalar(select(Transfer)) is None


def test_revise_revokes_old_plan_grant_and_confirmation(runtime):
    http, headers, factory, gateway, _demo = runtime
    original = begin(runtime)
    grant = challenge_for(runtime, original)
    old_body = confirm_body(original, grant["challenge_id"])
    response = http.post(f"/api/v2/tasks/{original['task_id']}/revise", headers=headers,
                         json={"message": "给房东转200元", "expected_task_version": original["version"]})
    assert response.status_code == 202
    assert run_one_job(factory, gateway)
    revised = trace_for(runtime, original["task_id"])
    assert http.post(f"/api/v2/tasks/{original['task_id']}/confirm", headers=headers, json=old_body).status_code == 409
    response = http.post(f"/api/v2/tasks/{original['task_id']}/confirm", headers=headers,
                         json=confirm_body(revised, grant["challenge_id"]))
    assert response.status_code == 403
    with factory() as db:
        assert db.get(AuthChallenge, grant["challenge_id"]).status == "REVOKED"
        assert db.get(Confirmation, old_body["confirmation_id"]).status == "CANCELLED"
        assert db.scalar(select(Transfer)) is None


def test_consumed_grant_replay_does_not_authorize_a_second_action(runtime):
    http, headers, factory, gateway, _demo = runtime
    first = begin(runtime)
    grant, body = authorize(runtime, first)
    assert run_one_job(factory, gateway)
    assert http.post(f"/api/v2/tasks/{first['task_id']}/confirm", headers=headers, json=body).status_code == 409
    second = begin(runtime, "给房东转200元")
    response = http.post(f"/api/v2/tasks/{second['task_id']}/confirm", headers=headers,
                         json=confirm_body(second, grant["challenge_id"]))
    assert response.status_code == 403
    with factory() as db:
        assert db.get(AuthChallenge, grant["challenge_id"]).status == "CONSUMED"
        assert db.scalar(select(func.count()).select_from(Transfer)) == 1


def test_cancel_during_inference_cannot_restore_a_plan(runtime):
    http, headers, factory, gateway, _demo = runtime
    task_id = accept(runtime)

    def cancel_while_inference(_message, _context):
        current = trace_for(runtime, task_id)
        assert current["state"] == "UNDERSTANDING"
        response = http.post(f"/api/v2/tasks/{task_id}/cancel", headers=headers,
                             json={"expected_task_version": current["version"]})
        assert response.status_code == 200

    gateway.hook = cancel_while_inference
    assert run_one_job(factory, gateway)
    after = trace_for(runtime, task_id)
    assert after["state"] == "CLOSED" and after["nodes"] == []
    with factory() as db:
        assert db.scalar(select(PlanSnapshot)) is None
        assert db.scalar(select(Transfer)) is None
        assert db.scalar(select(WorkflowJob)).status == "CANCELLED"


def test_revise_during_inference_discards_the_previous_message_proposal(runtime):
    http, headers, factory, gateway, _demo = runtime
    task_id = accept(runtime)

    def revise_while_inference(_message, _context):
        current = trace_for(runtime, task_id)
        response = http.post(f"/api/v2/tasks/{task_id}/revise", headers=headers,
                             json={"message": "只分析账单", "expected_task_version": current["version"]})
        assert response.status_code == 202

    gateway.hook = revise_while_inference
    assert run_one_job(factory, gateway)
    after_old_lease = trace_for(runtime, task_id)
    assert after_old_lease["message"] == "只分析账单"
    assert after_old_lease["state"] == "QUEUED"
    assert not any(node["action"] == "TRANSFER" for node in after_old_lease["nodes"])
    assert run_one_job(factory, gateway)
    revised = trace_for(runtime, task_id)
    assert revised["state"] == "SUCCEEDED"
    assert [node["action"] for node in revised["nodes"]] == ["BILL_ANALYSIS"]


def test_retired_lease_failure_cannot_overwrite_recovered_planner(runtime):
    _http, _headers, factory, gateway, _demo = runtime
    task_id = accept(runtime)

    def expire_and_take_over(_message, _context):
        with factory.begin() as db:
            job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == task_id))
            assert job.lease_owner == "retired-planner"
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
        assert run_one_job(factory, gateway, worker_id="recovered-planner")
        assert trace_for(runtime, task_id)["state"] == "AWAITING_CONFIRMATION"
        raise RuntimeError("retired model response failed after a replacement worker finished")

    gateway.hook = expire_and_take_over
    assert run_one_job(factory, gateway, worker_id="retired-planner")
    after = trace_for(runtime, task_id)
    assert after["state"] == "AWAITING_CONFIRMATION"
    with factory() as db:
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == task_id))
        assert job.lease_owner == "recovered-planner" and job.status == "SUCCEEDED"
        assert db.scalar(select(func.count()).select_from(PlanSnapshot)) == 1


def test_expired_execution_lease_recovers_already_committed_action_without_double_debit(runtime):
    _http, _headers, factory, gateway, _demo = runtime
    trace = begin(runtime)
    before = balance(runtime)
    authorize(runtime, trace)
    assert run_one_job(factory, gateway)
    with factory.begin() as db:
        # Simulate a worker dying after its bank transaction committed but before
        # the durable scheduler acknowledged completion.
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.kind == "EXECUTE"))
        old_owner = job.lease_owner
        job.status = "LEASED"
        job.lease_owner = "dead-worker"
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert run_one_job(factory, gateway)
    assert balance(runtime) == before - 10000
    assert trace_for(runtime, trace["task_id"])["state"] == "SUCCEEDED"
    with factory() as db:
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.kind == "EXECUTE"))
        assert job.status == "SUCCEEDED" and job.lease_owner != old_owner
        assert job.lease_owner != "dead-worker"
        assert db.scalar(select(func.count()).select_from(Transfer)) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 2
        assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "TRANSFER")) == 1
        assert db.scalar(select(func.count()).select_from(ToolCall)) == 1


def test_default_worker_identity_is_unique_and_lease_covers_model_budget(runtime):
    _http, _headers, factory, gateway, _demo = runtime
    observed = []

    def capture_lease(_message, _context):
        with factory() as db:
            job = db.scalar(select(WorkflowJob).where(WorkflowJob.status == "LEASED"))
            observed.append(job.lease_owner)
            expiry = job.lease_expires_at.replace(tzinfo=utcnow().tzinfo)
            assert 400 <= (expiry - utcnow()).total_seconds() <= 420

    for _ in range(2):
        gateway.hook = capture_lease
        begin(runtime, "只分析账单")
    assert len(set(observed)) == 2


def test_unknown_reconciliation_budget_exhaustion_stays_unknown(runtime):
    _http, _headers, factory, gateway, _demo = runtime
    trace = begin(runtime)
    before = balance(runtime)
    with factory.begin() as db:
        parent = db.get(Task, trace["task_id"])
        operation = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id))
        parent.state = operation.status = "UNKNOWN"
        job = WorkflowJob(task_id=parent.id, sandbox_id=parent.sandbox_id, kind="RECONCILE",
                          dedupe_key=f"query-budget:{operation.id}", payload_json={"operation_id": operation.id},
                          max_attempts=2, next_run_at=utcnow())
        db.add(job)
        db.flush()
        job_id = job.id
    for _ in range(2):
        assert run_one_job(factory, gateway)
        with factory.begin() as db:
            db.get(WorkflowJob, job_id).next_run_at = utcnow()
    assert run_one_job(factory, gateway) is False
    assert trace_for(runtime, trace["task_id"])["state"] == "UNKNOWN"
    assert balance(runtime) == before
    with factory() as db:
        job = db.get(WorkflowJob, job_id)
        assert job.attempts == 2 and job.last_error == "RETRY_BUDGET_EXCEEDED"
        assert db.scalar(select(Transfer)) is None


def test_readonly_lookup_failure_keeps_committed_action_unknown(runtime, monkeypatch):
    http, headers, factory, gateway, _demo = runtime
    trace = begin(runtime)
    before = balance(runtime)
    assert http.post("/api/v2/simulation/fault", headers=headers,
                     json={"mode": "AFTER_COMMIT_RESPONSE_LOST"}).status_code == 200
    authorize(runtime, trace)
    assert run_one_job(factory, gateway)
    assert trace_for(runtime, trace["task_id"])["state"] == "UNKNOWN"
    with factory.begin() as db:
        db.scalar(select(WorkflowJob).where(WorkflowJob.kind == "RECONCILE")).next_run_at = utcnow()
    original_scalar = Session.scalar
    failed = []

    def unavailable_lookup(session, statement, *args, **kwargs):
        descriptions = getattr(statement, "column_descriptions", [])
        if not failed and any(row.get("entity") is ToolCall for row in descriptions):
            failed.append(True)
            raise DomainError("QUERY_UNAVAILABLE", "查单暂时不可用", 503)
        return original_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", unavailable_lookup)
    assert run_one_job(factory, gateway)
    assert failed == [True]
    assert trace_for(runtime, trace["task_id"])["state"] == "UNKNOWN"
    assert balance(runtime) == before - 10000
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transfer)) == 1
        assert db.scalar(select(ToolCall)).status == "SUCCESS"


def test_facts_changed_after_acceptance_do_not_consume_auth_or_write_bank(runtime):
    _http, _headers, factory, gateway, _demo = runtime
    trace = begin(runtime)
    before = balance(runtime)
    grant, body = authorize(runtime, trace)
    with factory.begin() as db:
        account = db.scalar(select(Account))
        account.version += 1
    assert run_one_job(factory, gateway)
    finished = trace_for(runtime, trace["task_id"])
    assert finished["state"] == "FAILED"
    assert finished["nodes"][0]["error"] == "ACCOUNT_VERSION_CHANGED"
    assert balance(runtime) == before
    with factory() as db:
        assert db.get(AuthChallenge, grant["challenge_id"]).status == "VERIFIED"
        assert db.get(Confirmation, body["confirmation_id"]).status == "PENDING"
        assert db.scalar(select(Transfer)) is None
        assert db.scalar(select(ToolCall)) is None


def test_parent_idempotency_lookup_returns_child_receipt_and_never_suggests_retry(runtime):
    http, headers, factory, gateway, _demo = runtime
    trace = begin(runtime)
    key = trace["nodes"][0]["idempotency_key"]
    path = f"/api/v1/tasks/{trace['task_id']}/executions/{key}"
    before = http.get(path, headers=headers).json()
    assert before["status"] == "NOT_RECORDED" and before["retry_with_same_key_allowed"] is False
    authorize(runtime, trace)
    assert run_one_job(factory, gateway)
    recorded = http.get(path, headers=headers)
    assert recorded.status_code == 200
    result = recorded.json()
    assert result["status"] == "RECORDED" and result["retry_with_same_key_allowed"] is False
    assert result["executions"][0]["result"]["amount_minor"] == 10000
    assert result["executions"][0]["tool_name"] == "transfer.commit.v1"
    other = http.post("/api/v1/demo/sessions").json()
    assert http.get(path, headers={"Authorization": f"Bearer {other['token']}"}).status_code == 404
