from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from zhenxing.app import create_app
from zhenxing.intelligence import rule_proposal
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import Account, Card, ToolCall, utcnow
from zhenxing.runtime import run_one_job
from zhenxing.runtime_models import AuthChallenge, PlanSnapshot, WorkflowJob


class Gateway(ModelGateway):
    def __init__(self):
        super().__init__(RuleIntentModel())

    def propose(self, message, context=None):
        return rule_proposal(message)


@pytest.fixture
def runtime(tmp_path):
    gateway = Gateway()
    app = create_app(f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}", gateway)
    with TestClient(app) as http:
        demo = http.post("/api/v1/demo/sessions").json()
        headers = {"Authorization": f"Bearer {demo['token']}"}
        yield http, headers, app.state.session_factory, gateway


def begin(runtime, message):
    http, headers, factory, gateway = runtime
    response = http.post("/api/v2/tasks", headers=headers, json={"message": message})
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert response.json()["state"] == "QUEUED"
    run_one_job(factory, gateway)
    return http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers).json()


def authorize(runtime, trace):
    http, headers, _factory, _gateway = runtime
    pending = trace["pending_confirmation"]
    assert pending
    body = {"confirmation_id": pending["confirmation_id"], "plan_hash": pending["plan_hash"],
            "expected_task_version": pending["expected_task_version"]}
    if pending["requires_step_up"]:
        challenge = http.post("/api/v2/auth/challenges", headers=headers,
                              json={"task_id": pending["task_id"], "confirmation_id": pending["confirmation_id"]}).json()
        response = http.post(f"/api/v2/auth/challenges/{challenge['challenge_id']}/verify",
                             headers=headers, json={"code": challenge["demo_code"]})
        assert response.status_code == 200
        body["auth_challenge_id"] = challenge["challenge_id"]
    return body


def test_task_is_persistent_before_model_and_transfer_requires_auth(runtime):
    http, headers, factory, gateway = runtime
    response = http.post("/api/v2/tasks", headers=headers, json={"message": "给房东转100元"})
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers).json()["state"] == "QUEUED"
    run_one_job(factory, gateway)
    trace = http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers).json()
    assert trace["state"] == "AWAITING_CONFIRMATION"
    assert trace["pending_confirmation"]["requires_step_up"] is True
    pending = trace["pending_confirmation"]
    denied = http.post(f"/api/v2/tasks/{task_id}/confirm", headers=headers,
                       json={"confirmation_id": pending["confirmation_id"], "plan_hash": pending["plan_hash"],
                             "expected_task_version": pending["expected_task_version"]})
    assert denied.status_code == 403
    before = http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"]
    body = authorize(runtime, trace)
    assert http.post(f"/api/v2/tasks/{task_id}/confirm", headers=headers, json=body).status_code == 202
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before
    run_one_job(factory, gateway)
    finished = http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers).json()
    assert finished["state"] == "SUCCEEDED"
    assert finished["audit_chain_valid"] is True
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before - 10000
    assert http.post(f"/api/v2/tasks/{task_id}/confirm", headers=headers, json=body).status_code == 409
    with factory() as db:
        assert len(list(db.scalars(select(ToolCall)))) == 1
        assert db.scalar(select(AuthChallenge)).status == "CONSUMED"


def test_auth_bad_attempts_expiry_and_cross_sandbox(runtime):
    http, headers, factory, _gateway = runtime
    trace = begin(runtime, "给房东转100元")
    pending = trace["pending_confirmation"]
    challenge = http.post("/api/v2/auth/challenges", headers=headers,
                          json={"task_id": pending["task_id"], "confirmation_id": pending["confirmation_id"]}).json()
    wrong = "000000" if challenge["demo_code"] != "000000" else "111111"
    assert http.post(f"/api/v2/auth/challenges/{challenge['challenge_id']}/verify",
                     headers=headers, json={"code": wrong}).status_code == 403
    with factory() as db:
        assert db.get(AuthChallenge, challenge["challenge_id"]).attempts == 1
    other = http.post("/api/v1/demo/sessions").json()
    assert http.post(f"/api/v2/auth/challenges/{challenge['challenge_id']}/verify",
                     headers={"Authorization": f"Bearer {other['token']}"},
                     json={"code": challenge["demo_code"]}).status_code == 404
    with factory.begin() as db:
        db.get(AuthChallenge, challenge["challenge_id"]).expires_at = utcnow() - timedelta(seconds=1)
    assert http.post(f"/api/v2/auth/challenges/{challenge['challenge_id']}/verify",
                     headers=headers, json={"code": challenge["demo_code"]}).status_code == 403


def test_post_commit_response_loss_reconciles_without_double_debit(runtime):
    http, headers, factory, gateway = runtime
    trace = begin(runtime, "给房东转100元")
    http.post("/api/v2/simulation/fault", headers=headers,
              json={"mode": "AFTER_COMMIT_RESPONSE_LOST"}).raise_for_status()
    before = http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"]
    body = authorize(runtime, trace)
    http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body).raise_for_status()
    run_one_job(factory, gateway)
    unknown = http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=headers).json()
    assert unknown["state"] == "UNKNOWN"
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before - 10000
    with factory.begin() as db:
        db.scalar(select(WorkflowJob).where(WorkflowJob.kind == "RECONCILE")).next_run_at = utcnow()
    run_one_job(factory, gateway)
    assert http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=headers).json()["state"] == "SUCCEEDED"
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before - 10000
    with factory() as db:
        assert len(list(db.scalars(select(ToolCall)))) == 1


def test_replan_preserves_completed_card_operation(runtime):
    http, headers, factory, gateway = runtime
    trace = begin(runtime, "临时锁卡尾号4321，并取消视频会员自动续费")
    assert len(trace["nodes"]) == 2
    body = authorize(runtime, trace)
    http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body).raise_for_status()
    run_one_job(factory, gateway)
    first = http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=headers).json()
    assert first["nodes"][0]["status"] == "SUCCEEDED"
    card_version = http.get("/api/v1/cards", headers=headers).json()[0]["version"]
    http.post(f"/api/v2/tasks/{trace['task_id']}/revise", headers=headers,
              json={"message": "只分析账单", "expected_task_version": first["version"]}).raise_for_status()
    run_one_job(factory, gateway)
    revised = http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=headers).json()
    assert revised["state"] == "SUCCEEDED"
    assert revised["plan_version"] == 2
    assert http.get("/api/v1/cards", headers=headers).json()[0]["version"] == card_version
    with factory() as db:
        assert len(list(db.scalars(select(PlanSnapshot)))) == 2


def test_legacy_endpoint_cannot_bypass_runtime_ownership(runtime):
    http, headers, _factory, _gateway = runtime
    trace = begin(runtime, "给房东转100元")
    body = authorize(runtime, trace)
    pending = trace["pending_confirmation"]
    response = http.post(f"/api/v1/tasks/{pending['task_id']}/confirm", headers={
        **headers, "Idempotency-Key": "bypass-test"}, json=body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RUNTIME_COMMAND_REQUIRED"


def test_production_api_disables_legacy_writes(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'v2-only.db').as_posix()}", Gateway(),
                     allow_legacy_commands=False)
    with TestClient(app) as http:
        demo = http.post("/api/v1/demo/sessions").json()
        headers = {"Authorization": f"Bearer {demo['token']}"}
        assert http.post("/api/v1/tasks", headers=headers, json={"message": "给房东转100元"}).status_code == 410
        assert http.post("/api/v2/tasks", headers=headers, json={"message": "给房东转100元"}).status_code == 202
