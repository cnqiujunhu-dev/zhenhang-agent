"""Reproducible local-bank journey; no real model or external merchant claims."""
import json
from copy import deepcopy

from sqlalchemy import func, select

from test_life_plan_runtime import h, draft, authorize, tick, RESERVE, ORDER, DELIVER
from zhenxing.credit_models import PaymentRecord, PaymentLedgerEntry
from zhenxing.life_plan_models import LifePlan
from zhenxing.life_plan_service import life_plan_view, cancel_life_plan
from zhenxing.merchant_models import MerchantProduct, MerchantOrder
from zhenxing.models import Account, Card


def snapshot(h, plan_id):
    with h.factory() as session:
        plan = life_plan_view(session, h.principal, plan_id)["plans"][0]
        return {"plan": plan, "balance_minor": session.get(Account, h.account_id).balance_minor,
            "other_balance_minor": session.get(Account, h.other_account_id).balance_minor,
            "payment_count": session.scalar(select(func.count()).select_from(PaymentRecord)),
            "ledger_sum_minor": sum(session.scalars(select(PaymentLedgerEntry.delta_minor))),
            "card_status": session.get(Card, h.card_id).status}


def evidence(record_property, case, stages):
    # Strip generated identities and recipient information from shared evidence.
    safe = []
    for name, row in stages:
        plan = row["plan"]
        safe.append({"stage": name, **{k: v for k, v in row.items() if k != "plan"},
            "plan": {k: plan[k] for k in ("status", "version", "budget_minor", "reserved_minor",
                "spent_minor", "refunded_minor", "net_cash_debit_minor", "execution_review")}})
    record_property("journey_evidence", json.dumps({"case": case, "stages": safe}, ensure_ascii=False))


def test_normal_birthday_budget_to_fulfilment_and_replay(h, record_property):
    p = draft(h); pid = p["plan_id"]
    initial = snapshot(h, pid)
    authorize(h, pid); tick(h, RESERVE)
    held = snapshot(h, pid)
    assert held["balance_minor"] == initial["balance_minor"] == 200000
    assert held["plan"]["reserved_minor"] == 100000 and held["payment_count"] == 0
    tick(h, ORDER); paid = snapshot(h, pid)
    assert paid["balance_minor"] == 100200 and paid["payment_count"] == 2
    assert paid["plan"]["spent_minor"] == 99800
    tick(h, DELIVER); final = snapshot(h, pid)
    assert final["plan"]["status"] == "SUCCEEDED" and final["plan"]["reserved_minor"] == 0
    assert final["other_balance_minor"] == 50000 and final["ledger_sum_minor"] == 0
    assert tick(h, DELIVER) == 0 and snapshot(h, pid) == final
    evidence(record_property, "NORMAL_AND_REPLAY", [("draft", initial), ("reserved", held), ("paid", paid), ("fulfilled_and_replayed", final)])


def test_price_change_within_total_budget_stops_and_cancel_releases(h, record_property):
    p = draft(h); pid = p["plan_id"]; authorize(h, pid); tick(h, RESERVE)
    with h.factory.begin() as s:
        product = s.get(MerchantProduct, h.items[0]["product_id"])
        product.price_minor += 100; product.version += 1
    tick(h, ORDER); stopped = snapshot(h, pid)
    review = stopped["plan"]["execution_review"]
    assert review["reason"] == "PRICE_CHANGED" and review["payment_count"] == 0
    assert stopped["balance_minor"] == 200000 and stopped["plan"]["reserved_minor"] == 100000
    with h.factory.begin() as s: cancel_life_plan(s, h.principal, pid, expected_version=1, now=ORDER)
    cancelled = snapshot(h, pid)
    assert cancelled["plan"]["reserved_minor"] == 0 and cancelled["balance_minor"] == 200000
    assert cancelled["card_status"] == "ACTIVE" and cancelled["payment_count"] == 0
    evidence(record_property, "PRICE_CHANGED_THEN_CANCEL", [("paused", stopped), ("cancelled", cancelled)])


def test_changed_recipient_cannot_reuse_authorization(h, record_property):
    p = draft(h); pid = p["plan_id"]; authorize(h, pid)
    with h.factory.begin() as s:
        row = s.get(LifePlan, pid); scope = deepcopy(row.scope_json)
        scope["recipient"]["address"] = "UNAUTHORIZED_DESTINATION"
        row.scope_json = scope
    tick(h, RESERVE); stopped = snapshot(h, pid)
    assert stopped["plan"]["execution_review"]["reason"] == "AUTHORIZATION_CHANGED"
    assert stopped["payment_count"] == 0 and stopped["balance_minor"] == 200000
    assert stopped["plan"]["reserved_minor"] == 0
    evidence(record_property, "RECIPIENT_TAMPER", [("paused_before_reservation", stopped)])


def test_partial_failure_preserves_payment_then_refunds_without_second_debit(h, record_property):
    p = draft(h); pid = p["plan_id"]; authorize(h, pid); tick(h, RESERVE)
    assert tick(h, ORDER, limit=1) == 1
    with h.factory.begin() as s:
        order = s.scalar(select(MerchantOrder)); amount = order.amount_minor
        second = next(x for x in h.items if x["product_id"] != order.product_id)
        s.get(MerchantProduct, second["product_id"]).stock = 0
    assert tick(h, ORDER, limit=1) == 1
    partial = snapshot(h, pid)
    assert partial["plan"]["execution_review"]["reason"] == "ITEM_UNAVAILABLE"
    assert partial["plan"]["execution_review"]["payment_count"] == 1
    assert partial["balance_minor"] == 200000 - amount
    assert partial["plan"]["refunded_minor"] == 0
    tick(h, ORDER); compensated = snapshot(h, pid)
    assert compensated["balance_minor"] == 200000 and compensated["payment_count"] == 1
    assert compensated["plan"]["refunded_minor"] == amount
    assert compensated["plan"]["reserved_minor"] == 0 and compensated["ledger_sum_minor"] == 0
    evidence(record_property, "PARTIAL_AND_REFUND", [("partial_before_refund", partial), ("refund_recorded", compensated)])


def test_inconsistent_checkpoint_never_gets_a_no_payment_claim(h):
    p = draft(h); pid = p["plan_id"]; authorize(h, pid)
    with h.factory.begin() as s:
        s.get(Card, h.card_id).status = "LOCKED"
    tick(h, RESERVE)
    with h.factory.begin() as s:
        s.get(LifePlan, pid).spent_minor = 100
    assert snapshot(h, pid)["plan"]["execution_review"] is None


def test_unrecorded_pause_and_old_version_do_not_create_evidence(h):
    p = draft(h); pid = p["plan_id"]
    with h.factory.begin() as s:
        row=s.get(LifePlan,pid);row.status="PAUSED_RECONFIRM";row.pause_reason="LIFE_PLAN_PRICE_OR_TERMS_CHANGED"
    assert snapshot(h,pid)["plan"]["execution_review"] is None
    authorize(h,pid)
    with h.factory.begin() as s:s.get(Card,h.card_id).status="LOCKED"
    tick(h,RESERVE)
    with h.factory.begin() as s:s.get(LifePlan,pid).version+=1
    assert snapshot(h,pid)["plan"]["execution_review"] is None
