from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from zhenxing.accounts import create_customer_account
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import Account, Task, Transfer
from zhenxing.runtime import run_one_job
from zhenxing.simulation import inspect_simulation


@pytest.fixture
def named_runtime(tmp_path, monkeypatch):
    # Even a first import's module-level ASGI object uses a disposable database.
    monkeypatch.setenv("ZX_DATABASE_URL", f"sqlite:///{(tmp_path / 'unused-global.db').as_posix()}")
    monkeypatch.setenv("ZX_BOOTSTRAP_NAMED_ACCOUNTS", "0")
    from zhenxing.app import create_app
    from zhenxing.access_models import LoginAccount
    from zhenxing.accounts import password_hash
    from zhenxing.models import Sandbox, User

    gateway = ModelGateway(RuleIntentModel())
    app = create_app(f"sqlite:///{(tmp_path / 'named-runtime.db').as_posix()}", gateway,
                     allow_legacy_commands=True, bootstrap_accounts=False)
    factory = app.state.session_factory
    with factory.begin() as db:
        customer = create_customer_account(db, "customerone", "customer88", seed=31)
        dev = create_customer_account(db, "test", "test88", seed=32, role="DEV_TEST")
        sandbox = Sandbox()
        db.add(sandbox)
        db.flush()
        user = User(sandbox_id=sandbox.id, display_name="管理员")
        db.add(user)
        db.flush()
        admin = LoginAccount(username="admin", password_hash=password_hash("admin88"),
                             user_id=user.id, sandbox_id=sandbox.id, role="ADMIN")
        db.add(admin)
        db.flush()
        ids = {"customer": customer.id, "dev": dev.id, "admin": admin.id}
    with TestClient(app) as http:
        yield http, factory, gateway, ids


def login(http, username, password):
    response = http.post("/api/v2/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def begin(runtime, headers, message="给房东转100元"):
    http, factory, gateway, _ids = runtime
    accepted = http.post("/api/v2/tasks", headers=headers, json={"message": message})
    assert accepted.status_code == 202
    assert run_one_job(factory, gateway)
    return http.get(f"/api/v2/tasks/{accepted.json()['task_id']}/trace", headers=headers).json()


def authorization(http, headers, trace):
    pending = trace["pending_confirmation"]
    challenge = http.post("/api/v2/auth/challenges", headers=headers,
                          json={"task_id": pending["task_id"], "confirmation_id": pending["confirmation_id"]})
    assert challenge.status_code == 200
    details = challenge.json()
    assert http.post(f"/api/v2/auth/challenges/{details['challenge_id']}/verify", headers=headers,
                     json={"code": details["demo_code"]}).status_code == 200
    return {"confirmation_id": pending["confirmation_id"], "plan_hash": pending["plan_hash"],
            "expected_task_version": pending["expected_task_version"], "auth_challenge_id": details["challenge_id"]}


def test_named_customer_real_rule_workflow_transfer_remains_balanced(named_runtime):
    http, factory, gateway, _ids = named_runtime
    headers = login(http, "customerone", "customer88")
    before = http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"]
    trace = begin(named_runtime, headers)
    body = authorization(http, headers, trace)
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body).status_code == 202
    assert run_one_job(factory, gateway)
    assert http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=headers).json()["state"] == "SUCCEEDED"
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before - 10000
    with factory() as db:
        task = db.get(Task, trace["task_id"])
        assert inspect_simulation(db, task.sandbox_id)["reconciliation"]["balanced"]


def test_admin_cannot_submit_legacy_or_v2_banking_or_confirm_customer(named_runtime):
    http, _factory, _gateway, _ids = named_runtime
    customer = login(http, "customerone", "customer88")
    admin = login(http, "admin", "admin88")
    trace = begin(named_runtime, customer)
    body = authorization(http, customer, trace)
    assert http.post("/api/v2/tasks", headers=admin, json={"message": "给房东转100元"}).status_code == 403
    assert http.post("/api/v1/tasks", headers=admin, json={"message": "给房东转100元"}).status_code == 403
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=admin, json=body).status_code == 403
    observed = http.get(f"/api/v2/admin/tasks/{trace['task_id']}/trace", headers=admin)
    assert observed.status_code == 200
    projected = observed.json()["runtime_trace"]
    assert projected["allowed_actions"] == [] and projected["pending_confirmation"] is None
    assert projected["audit_chain_valid"] and projected["nodes"][0]["evidence"]["policy"]
    assert "plan_version" in projected and "planning_diagnostics" in projected


def test_dev_test_edit_invalidates_old_bank_facts_not_existing_receipts(named_runtime):
    http, factory, gateway, _ids = named_runtime
    headers = login(http, "test", "test88")
    trace = begin(named_runtime, headers)
    body = authorization(http, headers, trace)
    data = http.get("/api/v2/test-data", headers=headers).json()
    account = data["account"]
    edit = http.patch(f"/api/v2/test-data/account/{account['row_id']}", headers=headers,
                      json={"expected_version": account["version"], "changes": {"balance_minor": account["balance_minor"] + 1}, "reason": "确认前修改测试数据"})
    assert edit.status_code == 200
    response = http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body)
    assert response.status_code == 202  # Acceptance is asynchronous; facts recheck occurs before posting.
    assert run_one_job(factory, gateway)
    with factory() as db:
        from zhenxing.runtime_models import AuthChallenge, WorkflowJob
        bank = db.get(Account, account["row_id"])
        assert db.scalar(select(Transfer).where(Transfer.sandbox_id == bank.sandbox_id)) is None
        assert inspect_simulation(db, bank.sandbox_id)["reconciliation"]["balanced"]
        assert db.get(AuthChallenge, body["auth_challenge_id"]).status == "VERIFIED"
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == trace["task_id"], WorkflowJob.kind == "EXECUTE"))
        assert job.status == "FAILED" and "ACCOUNT_VERSION_CHANGED" in job.last_error


def test_disabled_authorized_owner_cannot_execute_after_session_revoked(named_runtime):
    http, factory, gateway, ids = named_runtime
    customer = login(http, "customerone", "customer88")
    admin = login(http, "admin", "admin88")
    trace = begin(named_runtime, customer)
    body = authorization(http, customer, trace)
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=customer, json=body).status_code == 202
    with factory() as db:
        from zhenxing.access_models import LoginAccount
        row = db.get(LoginAccount, ids["customer"])
        version, uid = row.version, row.user_id
        original = db.scalar(select(Account).where(Account.user_id == uid)).balance_minor
    assert http.patch(f"/api/v2/admin/accounts/{ids['customer']}/status", headers=admin,
                      json={"status": "DISABLED", "expected_version": version, "reason": "执行前撤销账号"}).status_code == 200
    assert http.get("/api/v2/auth/me", headers=customer).status_code == 401
    assert run_one_job(factory, gateway)
    with factory() as db:
        assert db.scalar(select(Account).where(Account.user_id == uid)).balance_minor == original
        assert db.scalar(select(Transfer).where(Transfer.user_id == uid)) is None


def test_global_validation_does_not_echo_password_or_token_input(named_runtime):
    http, _factory, _gateway, _ids = named_runtime
    marker = "DO_NOT_ECHO_SECRET_VALUE"
    response = http.post("/api/v2/auth/register", json={"username": "x", "password": marker, "role": marker})
    assert response.status_code == 422 and marker not in response.text and "input" not in response.text
