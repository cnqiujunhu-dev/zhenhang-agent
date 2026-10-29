from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from zhenxing.app import create_app
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.models import InvestmentLedgerEntry, InvestmentOrder


@pytest.fixture
def client(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'wealth.db').as_posix()}", ModelGateway(RuleIntentModel()))
    with TestClient(app) as http:
        yield http, app


def login(http):
    token = http.post("/api/v1/demo/sessions").json()["token"]
    return {"Authorization": f"Bearer {token}"}


def task_for(http, headers, message):
    response = http.post("/api/v1/tasks", headers=headers, json={"message": message})
    assert response.status_code == 200
    return response.json()


def approve(http, headers, task, key):
    return http.post(
        f"/api/v1/tasks/{task['task_id']}/confirm",
        headers={**headers, "Idempotency-Key": key},
        json={"confirmation_id": task["confirmation_id"],
              "plan_hash": task["plan_hash"],
              "expected_task_version": task["version"]},
    )


def test_compare_buy_redeem_balanced_and_idempotent(client):
    http, app = client
    headers = login(http)
    compared = task_for(http, headers, "比较理财产品")
    assert compared["state"] == "SUCCEEDED"
    assert len(compared["result"]["products"]) == 2
    assert sum(product["suitable"] for product in compared["result"]["products"]) == 1
    before_balance = http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"]
    before_units = next(item for item in http.get("/api/v1/investments", headers=headers).json()
                        if "稳健现金管理" in item["name"])["holding_units"]
    buy = task_for(http, headers, "申购稳健现金管理200元")
    assert buy["state"] == "AWAITING_CONFIRMATION"
    assert approve(http, headers, buy, "wealth-buy-1").status_code == 200
    assert approve(http, headers, buy, "wealth-buy-1").status_code == 200
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before_balance - 20000
    after_buy_units = next(item for item in http.get("/api/v1/investments", headers=headers).json()
                           if "稳健现金管理" in item["name"])["holding_units"]
    assert after_buy_units == before_units + 2
    redeem = task_for(http, headers, "赎回稳健现金管理100元")
    assert approve(http, headers, redeem, "wealth-redeem-1").status_code == 200
    assert http.get("/api/v1/accounts", headers=headers).json()[0]["balance_minor"] == before_balance - 10000
    with app.state.session_factory() as db:
        orders = list(db.scalars(select(InvestmentOrder)))
        entries = list(db.scalars(select(InvestmentLedgerEntry)))
        assert len(orders) == 2
        assert len(entries) == 4
        for order in orders:
            assert sum(item.delta_minor for item in entries if item.order_id == order.id) == 0


def test_risk_reserve_and_missing_amount_block(client):
    http, app = client
    headers = login(http)
    risk = task_for(http, headers, "申购成长精选200元")
    assert risk["state"] == "FAILED"
    assert risk["confirmation_id"] is None
    reserve = task_for(http, headers, "申购稳健现金管理11万元")
    assert reserve["state"] == "FAILED"
    assert reserve["confirmation_id"] is None
    vague = task_for(http, headers, "买收益最高的理财")
    assert vague["state"] == "NEED_CLARIFICATION"
    assert vague["confirmation_id"] is None
    bad_precision = task_for(http, headers, "申购稳健现金管理200.999元")
    assert bad_precision["state"] == "NEED_CLARIFICATION"
    assert bad_precision["confirmation_id"] is None
    with app.state.session_factory() as db:
        assert db.scalar(select(InvestmentOrder)) is None


def test_stale_product_blocks_post(client):
    http, app = client
    headers = login(http)
    task = task_for(http, headers, "申购稳健现金管理100元")
    with app.state.session_factory.begin() as db:
        from zhenxing.models import InvestmentProduct
        product = db.scalar(select(InvestmentProduct).where(InvestmentProduct.name.like("%稳健%")))
        product.version += 1
    result = approve(http, headers, task, "wealth-stale")
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "WEALTH_FACTS_CHANGED"
