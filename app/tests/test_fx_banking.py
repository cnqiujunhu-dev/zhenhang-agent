from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from zhenxing.access_models import LoginAccount
from zhenxing.accounts import create_customer_account
from zhenxing.contracts import DomainError
from zhenxing.database import Base
from zhenxing.extensions_models import CashflowGoal
from zhenxing.fx_banking import (
    bootstrap_dual_currency_accounts, create_fx_quote, fx_operation_integrity, fxcardprojection,
    inspect_fx, post_fx_operation, quote_public, recipientcurrencies,
)
from zhenxing.fx_models import DualCurrencyProfile, FxLedgerEntry, FxOperation, FxQuote, FxStatement, FxWallet
from zhenxing.fx_rates import get_current_market_rate
from zhenxing.models import Account, Card, Transaction, utcnow


@pytest.fixture
def fx_bank(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'fx-bank.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        sender = create_customer_account(db, "qiujunhu", "qiujunhu88", seed=61)
        receiver = create_customer_account(db, "wangwei", "wangwei88", seed=62)
        regular = create_customer_account(db, "regular", "regular88", seed=63)
        db.commit()
        bootstrap_dual_currency_accounts(db)
        db.commit()
        source = db.scalar(select(Account).where(Account.user_id == sender.user_id))
        target = db.scalar(select(Account).where(Account.user_id == receiver.user_id))
        usd_source = db.scalar(select(FxWallet).where(FxWallet.user_id == sender.user_id))
        usd_target = db.scalar(select(FxWallet).where(FxWallet.user_id == receiver.user_id))
        market = get_current_market_rate(fetcher=lambda: {"rate": "6.7045", "market_date": utcnow().date().isoformat()})
        yield db, sender, receiver, regular, source, target, usd_source, usd_target, market
    engine.dispose()


def error_code(code, action):
    with pytest.raises(DomainError) as error:
        action()
    assert error.value.code == code


def test_bootstrap_reuses_second_card_usd_journal_idempotently_without_cny_reset(fx_bank):
    db, sender, receiver, regular, source, target, usd_source, usd_target, _market = fx_bank
    before = source.balance_minor, target.balance_minor
    assert (usd_source.balance_minor, usd_target.balance_minor) == (350000, 500000)
    assert db.scalar(select(func.count()).select_from(Account).where(Account.user_id == sender.user_id)) == 1
    old_card = db.scalar(select(Card).where(Card.user_id == sender.user_id, Card.last4 == "4321"))
    profile = db.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == sender.user_id))
    dual = db.get(Card, profile.card_id)
    assert old_card.limit_minor == 1000000 and dual.limit_minor == 500000
    assert dual.last4 == "6672"
    assert db.scalar(select(func.count()).select_from(Card).where(Card.user_id == sender.user_id)) == 2
    assert fxcardprojection(db, old_card)["supported_currencies"] == ["CNY"]
    projection = fxcardprojection(db, dual)
    assert projection["currency_mode"] == "CNY_USD" and projection["balances_by_currency"]["USD"] == 350000
    result = bootstrap_dual_currency_accounts(db)
    db.commit()
    assert result["created"] == [] and set(result["existing"]) == {"qiujunhu", "wangwei"}
    assert (source.balance_minor, target.balance_minor) == before
    assert recipientcurrencies(db, regular) == ["CNY"]
    for login in (sender, receiver):
        assert inspect_fx(db, login.sandbox_id)["balanced"]
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "FX")) == 0


def test_bootstrap_postgresql_registry_locks_entire_seed_set_once_in_id_order(fx_bank):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    captured = []

    def capture(execution):
        if getattr(execution.statement, "_for_update_arg", None) is not None:
            captured.append(str(execution.statement.compile(dialect=postgresql.dialect())))

    event.listen(db, "do_orm_execute", capture)
    try:
        result = bootstrap_dual_currency_accounts(db)
        db.commit()
    finally:
        event.remove(db, "do_orm_execute", capture)
    assert len(captured) == 1
    assert "login_accounts.username IN" in captured[0]
    assert "ORDER BY login_accounts.id FOR UPDATE" in captured[0]
    assert result["created"] == [] and set(result["existing"]) == {"qiujunhu", "wangwei"}
    assert (usd_source.balance_minor, usd_target.balance_minor) == (350000, 500000)


@pytest.mark.parametrize("source_currency,target_currency,debit,credit", [
    ("USD", "USD", 117, 117), ("USD", "CNY", 117, 784),
    ("CNY", "USD", 785, 117), ("CNY", "CNY", 784, 784),
])
def test_four_transfer_routes_actual_cash_distinct_currency_journals_and_mirrors(fx_bank, source_currency, target_currency, debit, credit):
    db, sender, receiver, _regular, source, target, usd_source, usd_target, market = fx_bank
    cny_before, usd_before = (source.balance_minor, target.balance_minor), (usd_source.balance_minor, usd_target.balance_minor)
    quote = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER", {"payee_name": "wangwei", "currency": "USD", "amount_minor": 117,
        "source_currency": source_currency, "target_currency": target_currency}, market)
    assert (quote.debit_minor, quote.credit_minor) == (debit, credit)
    op = post_fx_operation(db, quote, "fx-four-route")
    db.commit()
    assert source.balance_minor == cny_before[0] - (debit if source_currency == "CNY" else 0)
    assert target.balance_minor == cny_before[1] + (credit if target_currency == "CNY" else 0)
    assert usd_source.balance_minor == usd_before[0] - (debit if source_currency == "USD" else 0)
    assert usd_target.balance_minor == usd_before[1] + (credit if target_currency == "USD" else 0)
    assert fx_operation_integrity(db, op)["balanced"]
    assert fx_operation_integrity(db, op)["ledger_entry_count"] == 4
    assert fx_operation_integrity(db, op)["statement_count"] == 2
    mirrors = list(db.scalars(select(Transaction).where(Transaction.source_kind == "FX")))
    assert len(mirrors) == (source_currency == "CNY") + (target_currency == "CNY")
    assert all(row.operation_ref == "fx:" + op.id + ":CNY" for row in mirrors)
    assert inspect_fx(db, sender.sandbox_id)["balanced"] and inspect_fx(db, receiver.sandbox_id)["balanced"]
    if source_currency == target_currency == "CNY":
        assert "USD计价" in quote_public(quote)["settlement_notice"]


def test_default_route_usd_first_else_explicit_cny_preview_not_blended_and_receiver_fallback(fx_bank):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    direct = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 10000}, market)
    assert direct.source_currency == direct.target_currency == "USD"
    # Keep fixture ledger honest while adjusting available USD for a scenario.
    # Respect the existing secondary-card limit while making available USD
    # insufficient through real fixture ledger operations, never a naked reset.
    for index in range(6):
        first = create_fx_quote(db, sender.sandbox_id, sender.user_id, "EXCHANGE",
            {"currency": "USD", "amount_minor": 50000, "source_currency": "USD", "target_currency": "CNY"}, market)
        post_fx_operation(db, first, f"consume-usd-for-scenario:{index}"); db.commit()
    lower_usd = usd_source.balance_minor
    assert lower_usd == 50000
    fallback = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 60000}, market)
    assert fallback.source_currency == "CNY" and fallback.target_currency == "USD"
    assert "人民币" in quote_public(fallback)["funding_notice"]
    post_fx_operation(db, fallback, "not-blended"); db.commit()
    assert usd_source.balance_minor == lower_usd
    error_code("FX_INSUFFICIENT_FUNDS", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 60000, "source_currency": "USD"}, market))
    regular_target = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "regular", "currency": "USD", "amount_minor": 117}, market)
    assert regular_target.source_currency == "USD" and regular_target.target_currency == "CNY"


def test_reused_dual_card_limit_still_rejects_oversized_exchange_without_money_change(fx_bank):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    before = source.balance_minor, source.version, usd_source.balance_minor, usd_source.version
    counts = tuple(db.scalar(select(func.count()).select_from(model))
                   for model in (FxQuote, FxOperation, FxLedgerEntry, FxStatement, Transaction))
    error_code("FX_CARD_LIMIT", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "EXCHANGE",
        {"currency": "USD", "amount_minor": 200000, "source_currency": "USD", "target_currency": "CNY"}, market))
    assert (source.balance_minor, source.version, usd_source.balance_minor, usd_source.version) == before
    assert tuple(db.scalar(select(func.count()).select_from(model))
                 for model in (FxQuote, FxOperation, FxLedgerEntry, FxStatement, Transaction)) == counts


@pytest.mark.parametrize("currency,source_currency,target_currency,debit,credit", [
    ("USD", "USD", "CNY", 117, 784), ("USD", "CNY", "USD", 785, 117),
    ("CNY", "CNY", "USD", 784, 116), ("CNY", "USD", "CNY", 117, 784),
])
def test_exchange_source_floor_target_ceil_self_two_currency_entries(fx_bank, currency, source_currency, target_currency, debit, credit):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    input_amount = 117 if currency == "USD" else 784
    quote = create_fx_quote(db, sender.sandbox_id, sender.user_id, "EXCHANGE",
        {"currency": currency, "amount_minor": input_amount, "source_currency": source_currency, "target_currency": target_currency}, market)
    assert (quote.debit_minor, quote.credit_minor) == (debit, credit)
    op = post_fx_operation(db, quote, "self-exchange")
    db.commit()
    rows = list(db.scalars(select(FxStatement).where(FxStatement.operation_id == op.id)))
    assert len(rows) == 2 and {row.currency for row in rows} == {"CNY", "USD"}
    assert {row.operation_ref for row in rows} == {"fx:"+op.id+":CNY", "fx:"+op.id+":USD"}
    assert inspect_fx(db, sender.sandbox_id)["balanced"]


def test_idempotency_quote_expiry_card_status_and_exact_currency_not_overridden(fx_bank, monkeypatch):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    action = {"payee_name": "wangwei", "currency": "USD", "amount_minor": 117}
    quote = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER", action, market)
    op = post_fx_operation(db, quote, "stable-original")
    db.commit()
    before = source.balance_minor, target.balance_minor, usd_source.balance_minor, usd_target.balance_minor
    import zhenxing.fx_banking as module
    original_clock = module.utcnow
    monkeypatch.setattr(module, "utcnow", lambda: original_clock()+timedelta(minutes=3))
    duplicate = post_fx_operation(db, quote, "stable-original")
    assert duplicate.id == op.id and (source.balance_minor, target.balance_minor, usd_source.balance_minor, usd_target.balance_minor) == before
    monkeypatch.setattr(module, "utcnow", original_clock)
    fresh = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER", action, market)
    profile = db.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == sender.user_id))
    card = db.get(Card, profile.card_id)
    card.status, card.version = "LOCKED", card.version+1
    error_code("FX_CARD_CHANGED", lambda: post_fx_operation(db, fresh, "locked-card"))
    error_code("FX_SOURCE_CARD_UNAVAILABLE", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER", action, market))
    card.status = "ACTIVE"
    error_code("FX_RECEIVER_CURRENCY_UNAVAILABLE", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {**action, "payee_name": "regular", "target_currency": "USD"}, market))
    error_code("FX_REGISTERED_RECIPIENT_REQUIRED", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {**action, "payee_name": "房东"}, market))


def test_fx_money_fault_savepoint_and_outer_rollback_erase_every_currency(fx_bank, monkeypatch):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    quote = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 117, "source_currency": "CNY", "target_currency": "USD"}, market)
    db.commit()
    before = source.balance_minor, target.balance_minor, usd_source.balance_minor, usd_target.balance_minor
    import zhenxing.fx_banking as module
    original = module._statement

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise DomainError("SYNTHETIC_FX_MIRROR_FAIL", "synthetic failure", 409)

    monkeypatch.setattr(module, "_statement", interrupted)
    error_code("SYNTHETIC_FX_MIRROR_FAIL", lambda: post_fx_operation(db, quote, "partial-interrupted"))
    db.commit()  # scheduler-like catch-and-commit must still leave no FX cash.
    assert (source.balance_minor, target.balance_minor, usd_source.balance_minor, usd_target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(FxOperation).where(FxOperation.kind == "TRANSFER")) == 0
    monkeypatch.setattr(module, "_statement", original)
    post_fx_operation(db, quote, "outer-rollback")
    db.rollback()
    assert (source.balance_minor, target.balance_minor, usd_source.balance_minor, usd_target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "FX")) == 0


def test_quote_rejects_tamper_old_rate_disabled_goal_protection_and_no_rounding_profit(fx_bank):
    db, sender, receiver, regular, source, target, usd_source, usd_target, market = fx_bank
    quote = create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 117, "source_currency": "CNY"}, market)
    goal = CashflowGoal(sandbox_id=sender.sandbox_id, user_id=sender.user_id, account_id=source.id,
                        name="fx-protect-all", target_minor=source.balance_minor, reserved_minor=source.balance_minor)
    db.add(goal); db.flush()
    error_code("FX_FUNDS_CHANGED", lambda: post_fx_operation(db, quote, "protect-cny-goal"))
    goal.reserved_minor = 0
    old = {**market, "market_date": (utcnow().date()-timedelta(days=8)).isoformat()}
    error_code("FX_RATE_STALE", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 117}, old))
    receiver.status = "DISABLED"
    error_code("FX_RECIPIENT_UNAVAILABLE", lambda: create_fx_quote(db, sender.sandbox_id, sender.user_id, "TRANSFER",
        {"payee_name": "wangwei", "currency": "USD", "amount_minor": 117}, market))
    receiver.status = "ACTIVE"
    before = usd_source.balance_minor
    out = create_fx_quote(db, sender.sandbox_id, sender.user_id, "EXCHANGE",
        {"currency": "USD", "amount_minor": 117, "source_currency": "USD", "target_currency": "CNY"}, market)
    post_fx_operation(db, out, "roundtrip-out"); db.commit()
    back = create_fx_quote(db, sender.sandbox_id, sender.user_id, "EXCHANGE",
        {"currency": "CNY", "amount_minor": out.credit_minor, "source_currency": "CNY", "target_currency": "USD"}, market)
    post_fx_operation(db, back, "roundtrip-back"); db.commit()
    assert usd_source.balance_minor <= before
