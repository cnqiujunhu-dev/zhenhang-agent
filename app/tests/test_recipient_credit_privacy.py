"""Transfer paths do not read or infer another customer's credit billing state.

All parties, credit purchases, bills and payments live in disposable databases.
"""
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, update

from test_internal_banking import internal_bank, receive_payee
from test_transfer_daily_risk import post
from test_credit_payment_domain import purchase
from test_fx_banking import fx_bank
from test_access_runtime import named_runtime
from zhenxing.credit_bootstrap import initialize_customer_payment_services
from zhenxing.credit_billing import close_credit_statement, repay_credit
from zhenxing.credit_models import CreditAccount, CreditStatement
from zhenxing.models import Card, utcnow
from zhenxing.payment_instruments import resolve_source_instrument
from zhenxing.recipient_account_risk import credit_billing_status
from zhenxing.transfer_risk import check_transfer_limits


def recipient_bill(session, receiver):
    actor = SimpleNamespace(user_id=receiver.user_id, sandbox_id=receiver.sandbox_id, family_context=None)
    initialize_customer_payment_services(session, actor.sandbox_id, actor.user_id,
        adult_eligible=True, eligibility_source="DISPOSABLE_PRIVACY_TEST")
    purchase(session, actor, 10000, key="recipient-private-purchase")
    clock = utcnow() + timedelta(seconds=1)
    close_credit_statement(session, actor, currency="CNY", cycle_key="recipient-private-cycle",
        start=clock-timedelta(days=1), end=clock, due_at=clock+timedelta(days=20), now=clock)
    session.flush()
    state = credit_billing_status(session, receiver, now=clock+timedelta(seconds=1))
    assert state["has_billed_outstanding"] and state["private_sources"][0]["remaining_due_minor"] == 10000
    assert state["label"] == "已出账待还" and not state["overdue_asserted"]
    return actor, clock


@contextmanager
def no_private_billing_reads(session_or_engine):
    engine = getattr(session_or_engine, "bind", session_or_engine)
    statements = []

    def inspect(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", inspect)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    assert not any("credit_statements" in sql or "credit_obligations" in sql for sql in statements), statements


def test_billed_unbilled_settled_and_bad_private_source_have_same_transfer_decision(internal_bank):
    session, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(session, sender)
    with no_private_billing_reads(session):
        original = check_transfer_limits(session, source, payee, 103)
    actor, clock = recipient_bill(session, receiver)
    with no_private_billing_reads(session):
        assert check_transfer_limits(session, source, payee, 103) == original
    statement = session.scalar(select(CreditStatement).join(CreditAccount,
        CreditAccount.id == CreditStatement.credit_account_id).where(CreditAccount.user_id == receiver.user_id))
    original_hash = statement.snapshot_hash
    # Deliberately corrupt only this disposable fixture below the ORM's
    # immutable-posting guard to exercise a private source-integrity failure.
    session.execute(update(CreditStatement).where(CreditStatement.id == statement.id)
        .values(snapshot_hash="unavailable-private-source"))
    with no_private_billing_reads(session):
        assert check_transfer_limits(session, source, payee, 103) == original
    session.execute(update(CreditStatement).where(CreditStatement.id == statement.id)
        .values(snapshot_hash=original_hash))
    card = session.scalar(select(Card).where(Card.user_id == actor.user_id, Card.account_id == target.id))
    repay_credit(session, actor, source_snapshot=resolve_source_instrument(session, actor,
        "debit:"+card.id, "CNY", purpose="REPAYMENT"), currency="CNY", amount_minor=10000,
        operation_key="recipient-private-repay")
    assert not credit_billing_status(session, receiver, now=clock+timedelta(seconds=1))["has_billed_outstanding"]
    with no_private_billing_reads(session):
        assert check_transfer_limits(session, source, payee, 103) == original


def test_actual_cny_payment_to_customer_with_unsettled_bill_posts_once(internal_bank):
    session, sender, receiver, source, target, _ = internal_bank
    recipient_bill(session, receiver)
    payee = receive_payee(session, sender)
    before = source.balance_minor, target.balance_minor
    with no_private_billing_reads(session):
        transfer = post(session, source, payee, 103, "private-credit-does-not-gate-cny")
        assert post(session, source, payee, 103, "private-credit-does-not-gate-cny").id == transfer.id
    assert transfer.status == "POSTED"
    assert (source.balance_minor, target.balance_minor) == (before[0]-103, before[1]+103)


def test_fx_payment_does_not_read_recipient_credit_billing(fx_bank):
    from zhenxing.fx_banking import create_fx_quote, post_fx_operation
    from zhenxing.fx_models import DualCurrencyProfile
    from zhenxing.card_identifiers import card_identifier_view
    session, sender, receiver, _, source, target, usd_source, usd_target, market = fx_bank
    recipient_bill(session, receiver)
    profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == receiver.user_id))
    recipient_card = card_identifier_view(session, session.get(Card, profile.card_id))["card_number"]
    before = usd_source.balance_minor, usd_target.balance_minor
    with no_private_billing_reads(session):
        quote = create_fx_quote(session, sender.sandbox_id, sender.user_id, "TRANSFER",
            {"payee_name": receiver.username, "currency": "USD", "amount_minor": 108,
                "source_currency": "USD", "target_currency": "USD",
                "recipient_card_reference": recipient_card}, market)
        operation = post_fx_operation(session, quote, "private-credit-does-not-gate-fx")
    assert operation.status == "POSTED"
    assert (usd_source.balance_minor, usd_target.balance_minor) == (before[0]-108, before[1]+108)


def test_due_schedule_does_not_read_recipient_credit_billing(internal_bank, monkeypatch):
    from test_schedule_recipient_identity import prepare_schedule, register
    from test_fx_runtime_integration import OCTOBER_NOW, TOMORROW_NINE
    from zhenxing.banking_extensions import execute_due_schedules
    session, sender, receiver, source, target, _ = internal_bank
    recipient_bill(session, receiver)
    monkeypatch.setattr("zhenxing.reporting_clock.calendar_now", lambda: OCTOBER_NOW)
    monkeypatch.setattr("zhenxing.operational_clock.utcnow", lambda: OCTOBER_NOW)
    before = source.balance_minor, target.balance_minor
    with no_private_billing_reads(session):
        task = prepare_schedule(session, sender, receiver.username)
        register(session, task, sender)
        assert execute_due_schedules(session, calendar_time=TOMORROW_NINE) == 1
    assert task.state == "SUCCEEDED"
    assert (source.balance_minor, target.balance_minor) == (before[0]-10000, before[1]+10000)


def test_aa_payment_does_not_read_collectors_credit_bill(named_runtime):
    from test_aa_participant_payments import collection, prepare, pay
    from zhenxing.access_models import LoginAccount
    http, factory, _, _ = named_runtime
    with factory.begin() as session:
        receiver = session.scalar(select(LoginAccount).where(LoginAccount.username == "customerone"))
        recipient_bill(session, receiver)
    _collector, payer, _other, invitation, _second = collection(named_runtime)
    with factory() as session, no_private_billing_reads(session):
        _, _, trace = prepare(named_runtime, payer, invitation)
        final, _ = pay(named_runtime, payer, trace)
    assert final["state"] == "SUCCEEDED", final
    assert "TRANSFER_RECIPIENT_RECHECK_REQUIRED" not in str(final)
