from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from zhenxing.app import create_app
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import Account, Subscription, ToolCall, Transaction
from zhenxing.runtime import run_one_job
from zhenxing.simulation import advance_business_time


@pytest.fixture
def http(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'subscription.db').as_posix()}", ModelGateway(RuleIntentModel()))
    with TestClient(app) as client:
        yield client


def headers_for(http):
    token = http.post("/api/v1/demo/sessions").json()["token"]
    return {"Authorization": f"Bearer {token}"}


def task_for(http, headers, message):
    response = http.post("/api/v1/tasks", headers=headers, json={"message": message})
    assert response.status_code == 200
    return response.json()


def confirm(http, headers, task, key):
    return http.post(
        f"/api/v1/tasks/{task['task_id']}/confirm",
        headers={**headers, "Idempotency-Key": key},
        json={"confirmation_id": task["confirmation_id"],
              "plan_hash": task["plan_hash"],
              "expected_task_version": task["version"]},
    )


def test_list_and_cancel_bank_mandate(http):
    headers = headers_for(http)
    listed = task_for(http, headers, "帮我查看订阅和自动续费")
    assert listed["state"] == "SUCCEEDED"
    assert len(listed["result"]["subscriptions"]) == 3
    assert sum(item["can_cancel_here"] for item in listed["result"]["subscriptions"]) == 2
    task = task_for(http, headers, "取消视频会员自动续费")
    assert task["state"] == "AWAITING_CONFIRMATION"
    assert next(item for item in http.get("/api/v1/subscriptions", headers=headers).json()
                if item["merchant"] == "视频会员")["status"] == "ACTIVE"
    assert confirm(http, headers, task, "cancel-video-membership").status_code == 200
    assert confirm(http, headers, task, "cancel-video-membership").status_code == 200
    assert next(item for item in http.get("/api/v1/subscriptions", headers=headers).json()
                if item["merchant"] == "视频会员")["status"] == "CANCELLED"
    evidence = http.get(f"/api/v1/tasks/{task['task_id']}/evidence", headers=headers).json()
    assert evidence["audit_chain_valid"] is True
    assert any(item["type"] == "SUBSCRIPTION_CANCELLED" for item in evidence["events"])


def test_external_subscription_never_has_write_confirmation(http):
    headers = headers_for(http)
    task = task_for(http, headers, "取消未接入服务的订阅")
    assert task["state"] == "SUCCEEDED"
    assert task["confirmation_id"] is None
    assert task["result"]["can_cancel_here"] is False
    assert next(item for item in http.get("/api/v1/subscriptions", headers=headers).json()
                if item["merchant"] == "未接入服务")["status"] == "ACTIVE"


def test_ambiguous_and_negated_cancel_do_not_write(http):
    headers = headers_for(http)
    ambiguous = task_for(http, headers, "取消自动续费")
    assert ambiguous["state"] == "NEED_CLARIFICATION"
    assert ambiguous["confirmation_id"] is None
    negated = task_for(http, headers, "不要取消订阅，先看看有哪些")
    assert negated["state"] == "SUCCEEDED"
    assert negated["confirmation_id"] is None


def test_simulated_merchant_cancellation_is_confirmed_idempotent_and_stops_future_debits(http):
    headers = headers_for(http)
    task = task_for(http, headers, "取消云存储订阅")
    assert task["state"] == "AWAITING_CONFIRMATION"
    assert task["result"]["source_type"] == "MERCHANT_SIM"
    assert "商户订阅" in task["result"]["message"]
    plan_node = task["plan"]["nodes"][0]
    assert plan_node["source_type"] == "MERCHANT_SIM"
    before = http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"]
    receipt = confirm(http, headers, task, "cancel-merchant-subscription")
    assert receipt.status_code == 200
    assert receipt.json()["result"]["status"] == "CANCELLED"
    assert "商户订阅已取消" in receipt.json()["result"]["message"]
    replay = confirm(http, headers, task, "cancel-merchant-subscription")
    assert replay.status_code == 200
    repeated_request = task_for(http, headers, "取消云存储订阅")
    assert repeated_request["state"] == "SUCCEEDED" and repeated_request["confirmation_id"] is None
    assert repeated_request["result"]["already_cancelled"] is True
    factory = http.app.state.session_factory
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.tool_name == "subscription.cancel.v1")) == 1
        # Isolate this merchant's due event from unrelated recurring subscriptions.
        sandbox_id = db.scalar(select(Subscription).where(Subscription.merchant == "云存储")).sandbox_id
        for subscription in db.scalars(select(Subscription).where(Subscription.sandbox_id == sandbox_id)):
            if subscription.merchant != "云存储":
                subscription.status = "CANCELLED"
        snapshot = advance_business_time(db, sandbox_id, 40)
        assert snapshot["reconciliation"]["balanced"] is True
        assert snapshot["advance"]["posted_recurring"] == []
        assert db.scalar(select(Account).where(Account.sandbox_id == sandbox_id)).balance_minor == before
        assert db.scalar(select(func.count()).select_from(Transaction).where(
            Transaction.sandbox_id == sandbox_id, Transaction.merchant == "云存储")) == 0


@pytest.mark.parametrize(("field", "value", "code"), [
    ("source_type", "EXTERNAL_UNCONNECTED", "UNCONNECTED_SUBSCRIPTION"),
    ("source_type", "BANK_MANDATE", "SUBSCRIPTION_SOURCE_CHANGED"),
    ("version", 2, "SUBSCRIPTION_CHANGED"), ("status", "CANCELLED", "SUBSCRIPTION_CHANGED"),
    ("amount_minor", 9999, "SUBSCRIPTION_CHANGED"),
])
def test_rechecks_subscription_source_status_version_and_amount(http, field, value, code):
    headers = headers_for(http)
    task = task_for(http, headers, "取消云存储订阅")
    with http.app.state.session_factory.begin() as db:
        subscription = db.scalar(select(Subscription).where(Subscription.merchant == "云存储"))
        setattr(subscription, field, value)
    response = confirm(http, headers, task, "changed-subscription")
    assert response.status_code == (409 if code != "UNCONNECTED_SUBSCRIPTION" else 403)
    assert response.json()["error"]["code"] == code
    with http.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ToolCall)) == 0


def test_duplicate_exact_subscription_names_do_not_select_random_record(http):
    headers = headers_for(http)
    with http.app.state.session_factory.begin() as db:
        existing = db.scalar(select(Subscription).where(Subscription.merchant == "云存储"))
        db.add(Subscription(sandbox_id=existing.sandbox_id, user_id=existing.user_id, merchant=existing.merchant,
            amount_minor=existing.amount_minor, source_type="MERCHANT_SIM", next_due_at=existing.next_due_at))
    task = task_for(http, headers, "取消云存储订阅")
    assert task["state"] == "NEED_CLARIFICATION" and task["confirmation_id"] is None


@pytest.mark.parametrize("message", ["不要关闭云存储订阅", "别停用云存储订阅", "如果不使用就取消云存储订阅", "如何取消云存储订阅"])
def test_denial_condition_and_how_to_question_do_not_plan_cancellation(http, message):
    headers = headers_for(http)
    task = task_for(http, headers, message)
    assert task["state"] == "SUCCEEDED" and task["confirmation_id"] is None
    assert all(subscription["status"] == "ACTIVE" for subscription in task["result"]["subscriptions"])


def test_merchant_cancellation_is_available_through_persistent_v2_runtime(http):
    headers = headers_for(http)
    response = http.post("/api/v2/tasks", headers=headers, json={"message": "取消云存储订阅"})
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    run_one_job(http.app.state.session_factory, http.app.state.model_gateway)
    trace = http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers).json()
    assert trace["state"] == "AWAITING_CONFIRMATION"
    pending = trace["pending_confirmation"]
    response = http.post(f"/api/v2/tasks/{task_id}/confirm", headers=headers,
        json={"confirmation_id": pending["confirmation_id"], "plan_hash": pending["plan_hash"],
              "expected_task_version": pending["expected_task_version"]})
    assert response.status_code == 202
    run_one_job(http.app.state.session_factory, http.app.state.model_gateway)
    assert http.get(f"/api/v2/tasks/{task_id}/trace", headers=headers).json()["state"] == "SUCCEEDED"
    with http.app.state.session_factory() as db:
        subscription = db.scalar(select(Subscription).where(Subscription.merchant == "云存储"))
        assert subscription.status == "CANCELLED"
