from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from zhenxing.access_models import AccessAudit, LoginAccount
from zhenxing.accounts import (
    authenticate_access_session, create_customer_account, install_account_routes, password_hash,
)
from zhenxing.admin_api import install_admin_routes, redact_observation
from zhenxing.asset_models import AssetBase, AssetSampleBootstrap, AssetSamplePosition
from zhenxing.contracts import DomainError
from zhenxing.customer_profile_models import CustomerCardTier, CustomerExplicitPreference, CustomerMembership
from zhenxing.database import Base
from zhenxing.models import Account, AuditEvent, DemoSession, ModelInvocation, PolicyDecisionRecord, Sandbox, Task, User
from zhenxing.runtime_models import RuntimeOperation, RuntimeRecord, WorkflowJob
from zhenxing.schema import ensure_schema
from zhenxing.test_data_api import install_test_data_routes


@pytest.fixture
def access_api(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'account-api.db').as_posix()}", connect_args={"check_same_thread": False})
    # Exercise the same bootstrap as the app, including separate AssetBase
    # metadata used by new registration's personal-service initialization.
    ensure_schema(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        customer = create_customer_account(db, "customerone", "customer88", seed=71)
        dev = create_customer_account(db, "test", "test88", seed=72, role="DEV_TEST")
        sandbox = Sandbox()
        db.add(sandbox)
        db.flush()
        user = User(sandbox_id=sandbox.id, display_name="管理员")
        db.add(user)
        db.flush()
        admin = LoginAccount(username="admin", password_hash=password_hash("admin88"),
                             user_id=user.id, sandbox_id=sandbox.id, role="ADMIN")
        db.add(admin)
        db.commit()
        account_ids = {"customer": customer.id, "dev": dev.id, "admin": admin.id}
    app = FastAPI()

    def get_session():
        with factory() as db:
            yield db

    def get_principal(session=Depends(get_session), authorization: str | None = Header(default=None)):
        return authenticate_access_session(session, authorization)

    @app.exception_handler(DomainError)
    async def domain_error(_request, error):
        return JSONResponse(status_code=error.status_code, content={"error": {"code": error.code, "message": error.message}})

    install_account_routes(app, get_session, get_principal)
    install_admin_routes(app, get_session, get_principal)
    install_test_data_routes(app, get_session, get_principal)
    with TestClient(app) as http:
        yield http, factory, account_ids
    engine.dispose()


def login(http, username, password):
    response = http.post("/api/v2/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}, response.json()


def all_row_counts(factory):
    with factory() as db:
        return {table.name: db.scalar(select(func.count()).select_from(table))
            for metadata in (Base.metadata, AssetBase.metadata) for table in metadata.tables.values()}


def assert_new_personal_services(factory, user_id):
    with factory() as db:
        membership = db.scalar(select(CustomerMembership).where(CustomerMembership.user_id == user_id))
        assert membership and (membership.tier_code, membership.status, membership.version) == ("STANDARD", "ACTIVE", 1)
        tiers = list(db.scalars(select(CustomerCardTier).where(CustomerCardTier.user_id == user_id)))
        assert len(tiers) == 2 and all(row.tier_code == "STANDARD" for row in tiers)
        assert db.scalar(select(func.count()).select_from(AssetSamplePosition).where(AssetSamplePosition.user_id == user_id)) == 2
        assert db.scalar(select(func.count()).select_from(AssetSampleBootstrap).where(AssetSampleBootstrap.user_id == user_id)) == 1
        assert db.scalar(select(func.count()).select_from(CustomerExplicitPreference).where(CustomerExplicitPreference.user_id == user_id)) == 0


def test_recursive_observation_redaction_preserves_numeric_usage_and_plan_identity():
    raw_session = "aB_9" * 10 + "xY7"
    digest = "a" * 64
    raw = {"password_hash": "never-return", "nested": {"apiKey": "never-return", "demo_code": "123456",
        "reasoning_content": "never-return", "text": f"sk-sensitive_key Bearer opaque-session 密码：private-value {raw_session}",
        "encoded": "pbkdf2_sha256$310000$" + "a" * 32 + "$" + digest},
        "input_tokens": 17, "reasoning_tokens": None, "plan_hash": digest,
        "allowed_actions": ["confirm"], "pending_confirmation": {"confirmation_id": "unusable"}, "can_compensate": True}
    result = redact_observation(raw)
    assert result["input_tokens"] == 17 and result["reasoning_tokens"] is None and result["plan_hash"] == digest
    assert result["allowed_actions"] == [] and result["pending_confirmation"] is None and not result["can_compensate"]
    rendered = json.dumps(result, ensure_ascii=False)
    assert all(secret not in rendered for secret in ["never-return", "123456", "sensitive_key", "opaque-session", "private-value", raw_session, "pbkdf2_sha256$"])
    assert raw["pending_confirmation"]["confirmation_id"] == "unusable" and raw["can_compensate"]


def test_auth_me_refresh_logout_and_password_revocation(access_api):
    http, factory, _ids = access_api
    old, info = login(http, "customerone", "customer88")
    me = http.get("/api/v2/auth/me", headers=old).json()
    assert me["registered"] and me["role"] == "CUSTOMER" and "token" not in me
    refreshed = http.post("/api/v2/auth/refresh", headers=old)
    assert refreshed.status_code == 200 and refreshed.json()["token"] != info["token"]
    assert http.get("/api/v2/auth/me", headers=old).status_code == 401
    current = {"Authorization": f"Bearer {refreshed.json()['token']}"}
    other, _ = login(http, "customerone", "customer88")
    change = http.post("/api/v2/auth/password", headers=current,
                       json={"current_password": "customer88", "new_password": "replacement88"})
    assert change.status_code == 200 and change.json()["requires_login"]
    assert change.json()["sessions_revoked"] == 2
    assert http.get("/api/v2/auth/me", headers=current).status_code == 401
    assert http.get("/api/v2/auth/me", headers=other).status_code == 401
    last, _ = login(http, "customerone", "replacement88")
    assert http.post("/api/v2/auth/logout", headers=last).json()["logged_out"]
    assert http.get("/api/v2/auth/me", headers=last).status_code == 401
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DemoSession)) == 0


def test_register_customer_only_reserved_names_and_min_password(access_api):
    http, factory, _ids = access_api
    payload = {"username": "newcustomer", "password": "newcustomer88", "profile": "family"}
    result = http.post("/api/v2/auth/register", json=payload)
    assert result.status_code == 200 and result.json()["role"] == "CUSTOMER"
    assert_new_personal_services(factory, result.json()["user_id"])
    before_duplicate = all_row_counts(factory)
    assert http.post("/api/v2/auth/register", json=payload).status_code == 409
    assert all_row_counts(factory) == before_duplicate
    for name in ["test", "admin", "wangwei"]:
        assert http.post("/api/v2/auth/register", json={"username": name, "password": "longpass88"}).status_code == 409
    assert http.post("/api/v2/auth/register", json={"username": "shortpass", "password": "88"}).status_code == 422
    assert http.post("/api/v2/auth/register", json={"username": "roleinject", "password": "longpass88", "role": "ADMIN"}).status_code == 422


def test_login_http_failures_are_generic_and_lock_persists(access_api):
    http, _factory, _ids = access_api
    bad_responses = [http.post("/api/v2/auth/login", json={"username": name, "password": "wrong-value"})
                     for name in ["customerone", "missingperson"]]
    assert all(response.status_code == 401 for response in bad_responses)
    assert bad_responses[0].json() == bad_responses[1].json()
    for _ in range(4):
        assert http.post("/api/v2/auth/login", json={"username": "customerone", "password": "wrong-value"}).status_code == 401
    assert http.post("/api/v2/auth/login", json={"username": "customerone", "password": "customer88"}).status_code == 401


def test_admin_crud_actual_role_version_and_secret_free_lists(access_api):
    http, factory, ids = access_api
    customer, _ = login(http, "customerone", "customer88")
    dev, _ = login(http, "test", "test88")
    admin, _ = login(http, "admin", "admin88")
    assert http.get("/api/v2/admin/accounts").status_code == 401
    for headers in [customer, dev]:
        assert http.get("/api/v2/admin/accounts", headers=headers).status_code == 403
    overview = http.get("/api/v2/admin/overview", headers=admin).json()
    assert not overview["bank_confirmation_permitted"] and not overview["customer_data_edit_permitted"]
    created = http.post("/api/v2/admin/accounts", headers=admin,
                        json={"username": "managedperson", "password": "managed88", "profile": "investor"})
    assert created.status_code == 201 and created.json()["role"] == "CUSTOMER"
    target = created.json()
    assert_new_personal_services(factory, target["user_id"])
    target_headers, _ = login(http, "managedperson", "managed88")
    disabled = http.patch(f"/api/v2/admin/accounts/{target['account_id']}/status", headers=admin,
                          json={"status": "DISABLED", "expected_version": target["version"], "reason": "停用测试账号"})
    assert disabled.status_code == 200 and disabled.json()["sessions_revoked"] == 1
    assert http.get("/api/v2/auth/me", headers=target_headers).status_code == 401
    stale = http.patch(f"/api/v2/admin/accounts/{target['account_id']}/status", headers=admin,
                       json={"status": "ACTIVE", "expected_version": target["version"], "reason": "旧版本不可写"})
    assert stale.status_code == 409
    active = http.patch(f"/api/v2/admin/accounts/{target['account_id']}/status", headers=admin,
                        json={"status": "ACTIVE", "expected_version": disabled.json()["version"], "reason": "恢复"})
    assert active.status_code == 200
    reset = http.post(f"/api/v2/admin/accounts/{target['account_id']}/reset-password", headers=admin,
                      json={"new_password": "replacement88", "expected_version": active.json()["version"], "reason": "重置"})
    assert reset.status_code == 200 and reset.json()["password_reset"]
    login(http, "managedperson", "replacement88")
    assert http.patch(f"/api/v2/admin/accounts/{ids['admin']}/status", headers=admin,
                      json={"status": "DISABLED", "expected_version": 1, "reason": "不可关闭管理员"}).status_code == 403
    rendered = json.dumps(http.get("/api/v2/admin/accounts", headers=admin).json())
    assert all(secret not in rendered for secret in ["password", "token", "managed88", "replacement88", "pbkdf2_sha256"])
    with factory() as db:
        real_admin = db.get(LoginAccount, ids["admin"])
        assert db.scalar(select(Account).where(Account.user_id == real_admin.user_id)) is None


@pytest.mark.parametrize("same_username", [True, False])
def test_concurrent_registration_keeps_unique_accounts_and_complete_personal_services(access_api, same_username):
    http, factory, _ids = access_api
    before = all_row_counts(factory)
    with factory() as db:
        old_money = {row.id: (row.balance_minor, row.version) for row in db.scalars(select(Account))}
    barrier = Barrier(2)
    names = ["parallelcustomer", "parallelcustomer" if same_username else "secondparallel"]

    def register(index):
        barrier.wait(timeout=10)
        return http.post("/api/v2/auth/register", json={"username": names[index],
            "password": "isolated-parallel88", "display_name": f"Parallel {index}"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(register, range(2)))
    assert sorted(response.status_code for response in responses) == ([200, 409] if same_username else [200, 200])
    successful = [response.json() for response in responses if response.status_code == 200]
    user_ids = {row["user_id"] for row in successful}
    assert len(user_ids) == len(successful)
    after = all_row_counts(factory)
    for table in ("login_accounts", "users", "sandboxes", "customer_memberships", "asset_sample_bootstraps"):
        assert after[table] - before[table] == len(successful)
    for row in successful:
        assert_new_personal_services(factory, row["user_id"])
        headers = {"Authorization": f"Bearer {row['token']}"}
        me = http.get("/api/v2/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["user_id"] == row["user_id"]
        assert http.get("/api/v2/admin/accounts", headers=headers).status_code == 403
    with factory() as db:
        assert {row.id: (row.balance_minor, row.version) for row in db.scalars(select(Account)) if row.id in old_money} == old_money


@pytest.mark.parametrize("endpoint", ["/api/v2/auth/register", "/api/v2/admin/accounts"])
def test_personal_service_initialization_failure_rolls_back_entire_creation(access_api, monkeypatch, endpoint):
    from zhenxing import accounts
    http, factory, _ids = access_api
    headers = login(http, "admin", "admin88")[0] if "/admin/" in endpoint else {}
    before = all_row_counts(factory)
    initialize = accounts.initialize_personal_services

    def fail_after_real_initialization(db, account):
        initialize(db, account)
        raise DomainError("ISOLATED_INIT_FAILURE", "隔离测试初始化失败", 409)

    monkeypatch.setattr(accounts, "initialize_personal_services", fail_after_real_initialization)
    response = http.post(endpoint, headers=headers, json={"username": "rollbackcustomer", "password": "rollbackcustomer88"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "ISOLATED_INIT_FAILURE"
    assert all_row_counts(factory) == before


def test_anonymous_admin_creation_has_no_initialization_side_effects(access_api):
    http, factory, _ids = access_api
    before = all_row_counts(factory)
    response = http.post("/api/v2/admin/accounts", json={"username": "untrustednew", "password": "untrustednew88"})
    assert response.status_code == 401
    assert all_row_counts(factory) == before


def test_admin_observation_trace_does_not_return_messages_codes_or_payload(access_api):
    http, factory, ids = access_api
    admin, _ = login(http, "admin", "admin88")
    marker = "sk-SENSITIVE_INPUT_MARKER"
    with factory() as db:
        account = db.get(LoginAccount, ids["customer"])
        task = Task(sandbox_id=account.sandbox_id, user_id=account.user_id, message=marker,
                    intent_type="COMPOSITE", state="UNKNOWN", model_label="rule-test",
                    result_json={"token": marker, "demo_code": marker})
        db.add(task)
        db.flush()
        db.add(RuntimeRecord(task_id=task.id, kind="PARENT"))
        db.add(RuntimeOperation(parent_task_id=task.id, sandbox_id=account.sandbox_id, operation_key="opaque",
            fingerprint="a" * 64, action_kind="TRANSFER", label=marker, args_json={"code": marker}, ordinal=1,
            plan_version=1, status="UNKNOWN", idempotency_key="test-admin-observe"))
        db.add(WorkflowJob(task_id=task.id, sandbox_id=account.sandbox_id, kind="EXECUTE", status="UNKNOWN",
            dedupe_key="admin-observe-job", last_error=marker, payload_json={"token": marker}))
        db.add(AuditEvent(task_id=task.id, sandbox_id=account.sandbox_id, seq=1, event_type="UNKNOWN",
                          payload_json={"demo_code": marker}, previous_hash="0" * 64, event_hash="a" * 64))
        db.add(AccessAudit(action="TEST_OBSERVE", payload_json={"token": marker}))
        db.commit()
        task_id = task.id
    for path in ["/overview", "/accounts", "/tasks", "/jobs", "/audit", "/policies", f"/tasks/{task_id}/trace"]:
        response = http.get("/api/v2/admin" + path, headers=admin)
        assert response.status_code == 200
        assert marker not in response.text and "demo_code" not in response.text and "password_hash" not in response.text
    trace = http.get(f"/api/v2/admin/tasks/{task_id}/trace", headers=admin).json()
    assert trace["read_only"] and trace["operations"][0]["status"] == "UNKNOWN"
    assert trace["runtime_trace"]["allowed_actions"] == [] and trace["runtime_trace"]["pending_confirmation"] is None
    assert not trace["runtime_trace"]["nodes"][0]["can_compensate"]
    assert http.get("/api/v2/admin/tasks?username=customerone", headers=admin).json()["total"] == 1
    assert http.get("/api/v2/admin/tasks?username=test", headers=admin).json()["total"] == 0


def test_admin_metrics_keep_unknown_usage_distinct_and_audit_kinds_safe(access_api):
    http, factory, ids = access_api
    admin, _ = login(http, "admin", "admin88")
    with factory() as db:
        actor = db.get(LoginAccount, ids["customer"])
        task = Task(sandbox_id=actor.sandbox_id, user_id=actor.user_id, message="真实只读记录",
                    intent_type="COMPOSITE", state="SUCCEEDED", model_label="test")
        db.add(task)
        db.flush()
        known = {"routing_mode": "AUTO", "model_api_call_count": 2, "actual_provider": "ollama", "degraded": True,
                 "usage": {"input_tokens": 31, "output_tokens": 9, "total_tokens": 40, "reasoning_tokens": None}}
        db.add(RuntimeRecord(task_id=task.id, kind="PARENT", plan_version=2, metadata_json={
            "decisions": [{"plan_version": 1, "planning_diagnostics": known}],
            "planning_diagnostics": {"routing_mode": "LOCAL_ONLY", "actual_provider": "rule", "degraded": True,
                                     "model_api_call_count": None, "usage": {"input_tokens": None}}}))
        db.add_all([ModelInvocation(task_id=task.id, sandbox_id=actor.sandbox_id, provider_label="ollama:synthetic",
                                   purpose="INTENT_AND_PLAN_PROPOSAL", outcome="SUCCESS"),
                    ModelInvocation(task_id=task.id, sandbox_id=actor.sandbox_id, provider_label="rule:synthetic",
                                   purpose="INTENT_AND_PLAN_PROPOSAL", outcome="FALLBACK")])
        db.add(PolicyDecisionRecord(task_id=task.id, sandbox_id=actor.sandbox_id, node_key="read",
            decision="ALLOW", policy_version="v1", reason_codes_json=["READ_ONLY"]))
        from zhenxing.audit import append_audit
        event = append_audit(db, sandbox_id=actor.sandbox_id, task_id=task.id, event_type="TASK_SUCCEEDED",
                             payload={"error": "ACCOUNT_VERSION_CHANGED", "demo_code": "654321", "message": "Bearer opaque-session-token"})
        db.add(AccessAudit(action="TEST_REASON", payload_json={"reason": "密码：sensitive-password", "token": "hidden-session"}))
        db.commit()
        event_id, original_hash = event.id, event.event_hash
    overview = http.get("/api/v2/admin/overview", headers=admin).json()
    assert overview["policies"]["by_decision"] == {"ALLOW": 1}
    observed = overview["models"]
    assert observed["planning_invocations"] == 2 and observed["observed_api_calls"] == 2
    assert observed["api_call_counts_unknown"] == 1 and observed["route_fallback_count"] == 2
    assert observed["fallback_count"] == 1 and observed["by_actual_provider"] == {"ollama": 1, "rule": 1}
    assert observed["token_usage"]["input_tokens"] == {"observed_sum": 31, "complete_total": None, "known_observations": 1, "unknown_observations": 1}
    assert observed["token_usage"]["reasoning_tokens"]["observed_sum"] is None
    workflow = http.get("/api/v2/admin/audit?kind=WORKFLOW", headers=admin).json()
    assert workflow["total"] == 1 and workflow["items"][0]["seq"] == 1
    assert workflow["items"][0]["reason_codes"] == ["ACCOUNT_VERSION_CHANGED"]
    combined = http.get("/api/v2/admin/audit?kind=ALL&page_size=100", headers=admin).json()
    assert {row["kind"] for row in combined["items"]} == {"ACCESS", "WORKFLOW"}
    assert all(secret not in json.dumps(combined, ensure_ascii=False) for secret in ["654321", "opaque-session-token", "sensitive-password", "hidden-session"])
    with factory() as db:
        original = db.get(AuditEvent, event_id)
        assert original.event_hash == original_hash and original.payload_json["demo_code"] == "654321"


def test_test_data_http_role_isolation_version_and_cash_journal(access_api):
    http, factory, ids = access_api
    dev, _ = login(http, "test", "test88")
    customer, _ = login(http, "customerone", "customer88")
    admin, _ = login(http, "admin", "admin88")
    for headers in [customer, admin]:
        assert http.get("/api/v2/test-data", headers=headers).status_code == 403
    data = http.get("/api/v2/test-data?history_page_size=2", headers=dev).json()
    assert len(data["history"]) == 2 and data["history_total"] >= 700
    account = data["account"]
    patched = http.patch(f"/api/v2/test-data/account/{account['row_id']}", headers=dev,
                         json={"expected_version": account["version"], "changes": {"balance_minor": account["balance_minor"] + 12345}, "reason": "合成余额场景"})
    assert patched.status_code == 200 and patched.json()["cash_delta_minor"] == 12345
    assert patched.json()["journal_balanced"] and not patched.json()["bank_receipts_changed"]
    assert http.patch(f"/api/v2/test-data/account/{account['row_id']}", headers=dev,
                     json={"expected_version": account["version"], "changes": {"balance_minor": 100}, "reason": "旧版本"}).status_code == 409
    with factory() as db:
        foreign = db.scalar(select(Account).where(Account.user_id == db.get(LoginAccount, ids["customer"]).user_id))
        foreign_id = foreign.id
    assert http.patch(f"/api/v2/test-data/account/{foreign_id}", headers=dev,
                     json={"expected_version": 1, "changes": {"balance_minor": 100}, "reason": "越权"}).status_code == 404
