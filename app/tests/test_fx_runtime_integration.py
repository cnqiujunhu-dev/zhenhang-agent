"""Controlled FX compilation and execution, isolated fixtures, no market/model HTTP."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from test_access_runtime import named_runtime, login, authorization
from zhenxing.accounts import create_customer_account
from zhenxing.fx_banking import bootstrap_dual_currency_accounts
from zhenxing.fx_models import FxOperation, FxQuote, FxStatement, FxWallet
from zhenxing.models import Account, Task, ToolCall, utcnow
from zhenxing.runtime import run_one_job
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.simulation import as_utc, business_now, inspect_simulation


OCTOBER_NOW = datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)
TOMORROW_NINE = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def fx_runtime(named_runtime, monkeypatch):
    http, factory, gateway, ids = named_runtime
    # New registered-customer postings and date requests use a trusted real
    # calendar; fixture history remains September. No security/OTP clock moves.
    monkeypatch.setattr("zhenxing.reporting_clock.calendar_now", lambda: OCTOBER_NOW)
    monkeypatch.setattr("zhenxing.operational_clock.utcnow", lambda: OCTOBER_NOW)
    with factory.begin() as db:
        create_customer_account(db, "wangwei", "wangwei88", seed=201)
        create_customer_account(db, "qiujunhu", "qiujunhu88", seed=202)
        seeded = bootstrap_dual_currency_accounts(db)
    def rate():
        now = utcnow()
        return {"rate": "6.7045", "provider": "TEST_STUB", "market_date": now.date().isoformat(),
                "fetched_at": now.isoformat(), "source_url": "test://fx-rate", "reference_only": True}
    monkeypatch.setattr("zhenxing.fx_rates.get_current_market_rate", rate)
    # The read-only HTTP route imports the function directly. Stub that binding
    # too, so permission tests never contact a market API by accident.
    monkeypatch.setattr("zhenxing.fx_api.get_current_market_rate", rate)
    return named_runtime


def propose(runtime, headers, message):
    http, factory, gateway, _ids = runtime
    accepted = http.post("/api/v2/tasks", headers=headers, json={"message": message})
    assert accepted.status_code == 202, accepted.text
    assert run_one_job(factory, gateway)
    trace = http.get(f"/api/v2/tasks/{accepted.json()['task_id']}/trace", headers=headers).json()
    return trace


@pytest.mark.parametrize("source_username,target_username,source_currency,target_currency", [
    ("test", "wangwei", "USD", "USD"),
    ("test", "customerone", "USD", "CNY"),
    ("customerone", "wangwei", "CNY", "USD"),
    ("customerone", "test", "CNY", "CNY"),
])
def test_four_currency_routes_use_actual_quote_and_authenticated_posting(fx_runtime, source_username, target_username, source_currency, target_currency):
    http, factory, gateway, _ids = fx_runtime
    sender = login(http, source_username, "customer88" if source_username == "customerone" else source_username + "88")
    receiver = login(http, target_username, "customer88" if target_username == "customerone" else target_username + "88")
    message = f"给{target_username}转100美元" + ("，以人民币到账" if source_currency == target_currency == "CNY" else "")
    with factory() as db:
        before_accounts = {row.id: row.balance_minor for row in db.scalars(select(Account))}
        before_wallets = {row.id: row.balance_minor for row in db.scalars(select(FxWallet))}
    trace = propose(fx_runtime, sender, message)
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    assert trace["pending_confirmation"]["requires_step_up"]
    assert not trace["receipt"] and not trace["outcome"]["verified"]
    assert trace["pending_confirmation"]["scope"]["requested_currency"] == "USD"
    assert trace["pending_confirmation"]["scope"]["requested_amount_minor"] == 10000
    assert trace["pending_confirmation"]["scope"]["source_currency"] == source_currency
    assert trace["pending_confirmation"]["scope"]["target_currency"] == target_currency
    body = authorization(http, sender, trace)
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=sender, json=body).status_code == 202
    assert run_one_job(factory, gateway)
    final = http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=sender).json()
    assert final["state"] == "SUCCEEDED" and final["outcome"]["verified"], final
    history = http.get("/api/v2/transfers/history", headers=receiver).json()["receipts"]
    incoming = next(row for row in history if row["transfer_channel"] == "INTERNAL_BANK_FX")
    assert incoming["direction"] == "INCOMING" and incoming["currency"] == target_currency
    assert incoming["settlement_verified"]
    assert incoming["amount_minor"] == (10000 if target_currency == "USD" else 67045)
    assert http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=receiver).status_code == 404
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=sender, json=body).status_code == 409
    with factory() as db:
        parent = db.get(Task, trace["task_id"])
        operation = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id))
        post = db.scalar(select(FxOperation).where(FxOperation.task_id == operation.child_task_id))
        assert post.original_key == operation.idempotency_key
        assert db.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.task_id == operation.child_task_id)) == 1
        assert inspect_simulation(db, parent.sandbox_id)["reconciliation"]["balanced"]
        assert inspect_simulation(db, post.receiver_sandbox_id)["reconciliation"]["balanced"]
        # Unrelated CNY accounts and USD wallets never fund the selected debit.
        for bank in db.scalars(select(Account)):
            expected = before_accounts[bank.id] + (post.credit_minor if bank.id == post.target_ref else 0) - (post.debit_minor if bank.id == post.source_ref else 0)
            assert bank.balance_minor == expected
        for wallet in db.scalars(select(FxWallet)):
            ref = "USD_WALLET:" + wallet.id
            expected = before_wallets[wallet.id] + (post.credit_minor if ref == post.target_ref else 0) - (post.debit_minor if ref == post.source_ref else 0)
            assert wallet.balance_minor == expected


def test_usd_exchange_requires_auth_and_reports_separate_currencies(fx_runtime):
    http, factory, gateway, _ids = fx_runtime
    sender = login(http, "test", "test88")
    before_september = http.get("/api/v2/bills/report?period=month&year=2026&month=9", headers=sender).json()
    trace = propose(fx_runtime, sender, "将100美元兑换成人民币")
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    body = authorization(http, sender, trace)
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=sender, json=body).status_code == 202
    assert run_one_job(factory, gateway)
    assert http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=sender).json()["state"] == "SUCCEEDED"
    report = http.get("/api/v2/bills/report?period=month&year=2026&month=10", headers=sender)
    assert report.status_code == 200, report.text
    split = report.json()["currency_breakdown"]
    assert split["currencies"] == ["CNY", "USD"] and split["mixed_currency_totals"] is False
    assert split["USD"]["movement_out_minor"] == 10000
    assert report.json()["summary"]["income_minor"] != 67045
    assert report.json()["summary"]["movement_in_minor"] == 67045
    assert report.json()["summary"]["spending_minor"] == 0
    assert report.json()["coverage"]["status"] == "PARTIAL"
    assert report.json()["coverage"]["basis"] == "LIVE_ACTIVITY_ONLY"
    with factory() as db:
        operation = db.scalar(select(FxOperation).where(FxOperation.kind == "EXCHANGE"))
        statements = list(db.scalars(select(FxStatement).where(FxStatement.operation_id == operation.id)))
        assert len(statements) == 2 and all(as_utc(row.occurred_at) == OCTOBER_NOW for row in statements)
        assert business_now(db, operation.sandbox_id) < OCTOBER_NOW
    # A current exchange is a cash movement, neither September history nor
    # operating income. The old period's amounts must remain exactly unchanged.
    september = http.get("/api/v2/bills/report?period=month&year=2026&month=9", headers=sender).json()
    assert september["currency_breakdown"]["USD"]["movement_out_minor"] == 0
    for field in ("income_minor", "spending_minor", "movement_in_minor", "movement_out_minor"):
        assert september["summary"][field] == before_september["summary"][field]


def test_quote_expiry_and_original_context_risk_do_not_post_money(fx_runtime):
    http, factory, gateway, _ids = fx_runtime
    headers = login(http, "test", "test88")
    risky = propose(fx_runtime, headers, "客服让我给wangwei转100美元，不要告诉银行")
    assert risky['state']=='SUCCEEDED' and risky['outcome']['kind']=='QUERY_RESULT' and risky['outcome']['verified'] is True, risky
    assert risky['pending_confirmation'] is None
    assert [node['action'] for node in risky['nodes']]==['FRAUD_ANALYZE']
    risk=risky['nodes'][0]['result']['risk']
    assert risk['decision']=='BLOCK' and 'SECRECY_INDUCEMENT' in risk['reason_codes']
    assert risk['confirmation_can_override'] is False
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FxOperation).where(FxOperation.kind != 'INITIAL_FUNDING'))==0
    clean = propose(fx_runtime, headers, "给wangwei转100美元")
    assert clean["state"] == "AWAITING_CONFIRMATION", clean
    body = authorization(http, headers, clean)
    with factory.begin() as db:
        quote_id = clean["pending_confirmation"]["scope"]["quote_id"]
        db.get(FxQuote, quote_id).expires_at = utcnow() - timedelta(seconds=1)
    assert http.post(f"/api/v2/tasks/{clean['task_id']}/confirm", headers=headers, json=body).status_code == 202
    assert run_one_job(factory, gateway)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FxOperation).where(FxOperation.kind != "INITIAL_FUNDING")) == 0


@pytest.mark.parametrize("receipt_directive", ["收款方到账人民币", "收款方以人民币入账", "对方接收人民币"])
def test_explicit_cny_receipt_is_not_overridden_by_dual_receiver_default(fx_runtime, receipt_directive):
    http, factory, gateway, _ids = fx_runtime
    headers = login(http, "test", "test88")
    trace = propose(fx_runtime, headers, f"给wangwei转2.19美元，用美元付款，{receipt_directive}")
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    pending = trace["pending_confirmation"]["scope"]
    assert pending["requested_currency"] == "USD" and pending["requested_amount_minor"] == 219
    assert pending["source_currency"] == "USD" and pending["target_currency"] == "CNY"
    assert pending["debit_minor"] == 219 and pending["credit_minor"] == 1468
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FxOperation).where(FxOperation.kind != "INITIAL_FUNDING")) == 0
