"""Disposable database only: unified cash controls and independent credit debt."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from zhenxing.access_models import LoginAccount
from zhenxing.card_identifiers import bootstrap_card_identifiers
from zhenxing.contracts import DomainError
from zhenxing.credit_bootstrap import initialize_customer_payment_services
from zhenxing.credit_billing import (close_credit_statement, create_credit_installment,
    repay_credit, settle_credit_installment, statement_view)
from zhenxing.credit_lifecycle import control_credit_card
from zhenxing.credit_models import (CreditAccount, CreditCard, CreditCurrencyBalance, CreditObligation,
    CreditStatement, CustomerPaymentDefaults, PaymentJournal, PaymentLedgerEntry, PaymentUpgradeRecord)
from zhenxing.credit_service import (authorize_credit_purchase, capture_credit_authorization,
    credit_bill_rows, credit_view, prepare_credit_fx_quote, void_credit_authorization)
from zhenxing.database import create_database
from zhenxing.models import Account, Card, Sandbox, User, utcnow
from zhenxing.payment_instruments import (post_merchant_payment, refund_merchant_payment, reserve_cash,
    resolve_recipient_instrument, resolve_source_instrument, validate_instrument_snapshot, validate_recipient_snapshot)
from zhenxing.schema import ensure_schema


@pytest.fixture
def db(monkeypatch):
    # Domain-only fixture registers the same exported metadata root integrates;
    # the production registry remains owned by root, not silently patched here.
    from zhenxing.credit_runtime import TOOL_DEFINITIONS
    from zhenxing.platform import TOOL_REGISTRY
    for key, value in TOOL_DEFINITIONS.items(): monkeypatch.setitem(TOOL_REGISTRY, key, value)
    engine, factory = create_database("sqlite:///:memory:")
    ensure_schema(engine)
    with factory.begin() as session:
        sandbox = Sandbox(); session.add(sandbox); session.flush()
        user = User(sandbox_id=sandbox.id, display_name="隔离客户"); session.add(user); session.flush()
        account = Account(sandbox_id=sandbox.id, user_id=user.id, name="旧资金账户", balance_minor=100001, currency="CNY")
        session.add(account); session.flush()
        for last4 in ("4321", "8876"):
            session.add(Card(sandbox_id=sandbox.id, user_id=user.id, account_id=account.id, last4=last4, status="ACTIVE"))
        session.add(LoginAccount(username="credit-fixture", password_hash="unused-isolated", sandbox_id=sandbox.id,
            user_id=user.id, role="CUSTOMER", status="ACTIVE"))
        session.flush(); bootstrap_card_identifiers(session, user.id)
        actor = SimpleNamespace(user_id=user.id, sandbox_id=sandbox.id, family_context=None)
        result = initialize_customer_payment_services(session, sandbox.id, user.id, adult_eligible=True, eligibility_source="ISOLATED_ADULT_SAMPLE")
        yield session, actor, result
    engine.dispose()


def credit_snapshot(session, actor, currency="CNY"):
    card = session.scalar(select(CreditCard).where(CreditCard.user_id == actor.user_id))
    return resolve_source_instrument(session, actor, "credit:"+card.id, currency, purpose="MERCHANT")


def purchase(session, actor, amount=10000, key="buy", currency="CNY", quote=None):
    return post_merchant_payment(session, actor, instrument_snapshot=credit_snapshot(session, actor, currency),
        amount_minor=amount, merchant_ref="fixture-shop", operation_key=key, fx_quote_id=quote)


def test_upgrade_preserves_cash_card_ids_once_and_shared_single_credit(db):
    session, actor, result = db
    assert (result["primary_balance_minor"], result["secondary_balance_minor"]) == (70001, 30000)
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001
    assert session.scalar(select(func.count()).select_from(Card)) == 2
    assert session.scalar(select(func.count()).select_from(CreditCard)) == 1
    again = initialize_customer_payment_services(session, actor.sandbox_id, actor.user_id)
    assert again["replayed"] and again["migration_id"] == result["migration_id"]
    assert session.scalar(select(func.count()).select_from(PaymentUpgradeRecord)) == 1
    rows = credit_view(session, actor)["cards"]
    assert rows[0]["shared_credit_limit"] and rows[0]["supported_currencies"] == ["CNY", "USD"]
    assert "balance_minor" not in rows[0] and not rows[0]["credit_is_cash"]


def test_credit_forbidden_for_cash_channels_and_snapshot_owner(db):
    session, actor, _ = db
    snap = credit_snapshot(session, actor)
    for purpose in ("TRANSFER", "WEALTH", "FX", "LIFE_PLAN", "REPAYMENT"):
        with pytest.raises(DomainError, match="现金"):
            resolve_source_instrument(session, actor, snap["instrument_id"], purpose=purpose)
    forged = deepcopy(snap); forged["available_credit_minor"] += 1
    with pytest.raises(DomainError): validate_instrument_snapshot(session, actor, forged)


def test_purchase_replay_debt_not_cash_and_refund_after_loss(db):
    session, actor, initial = db
    receipt = purchase(session, actor)
    assert receipt["cash_debit_minor"] == 0 and receipt["credit_debit_minor"] == 10000
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001
    replay = purchase(session, actor)
    assert replay["replayed"] and replay["payment_id"] == receipt["payment_id"]
    with pytest.raises(DomainError): purchase(session, actor, 10001)
    control_credit_card(session, actor, card_reference=receipt["instrument_id"], action="REPORT_LOSS", operation_key="loss")
    with pytest.raises(DomainError): purchase(session, actor, key="blocked")
    refund = refund_merchant_payment(session, actor, receipt["payment_id"], operation_key="refund")
    assert refund["credit_reduction_minor"] == 10000 and refund["cash_credit_minor"] == 0
    facility = session.get(CreditAccount, initial["credit_account_id"])
    assert facility.posted_exposure_minor == 0
    with pytest.raises(DomainError): control_credit_card(session, actor, card_reference=receipt["instrument_id"], action="UNLOCK", operation_key="unlock-lost")


def test_authorization_partial_capture_after_lock_and_void_balance(db):
    session, actor, initial = db
    snap = credit_snapshot(session, actor)
    auth = authorize_credit_purchase(session, actor, instrument_snapshot=snap, amount_minor=30000,
        merchant_ref="hotel", operation_key="auth")
    control_credit_card(session, actor, card_reference=snap["instrument_id"], action="LOCK", operation_key="lock")
    first = capture_credit_authorization(session, actor, auth["authorization_id"], amount_minor=10000, operation_key="cap1")
    assert first["capture_policy"] == "HONOR_UNEXPIRED_ORIGINAL_AMOUNT"
    assert capture_credit_authorization(session, actor, auth["authorization_id"], amount_minor=10000, operation_key="cap1")["replayed"]
    facility = session.get(CreditAccount, initial["credit_account_id"])
    assert (facility.posted_exposure_minor, facility.authorization_exposure_minor) == (10000, 20000)
    void_credit_authorization(session, actor, auth["authorization_id"], operation_key="void")
    assert facility.authorization_exposure_minor == 0
    with pytest.raises(DomainError): capture_credit_authorization(session, actor, auth["authorization_id"], amount_minor=1, operation_key="cap2")


def test_shared_usd_exposure_quote_required_and_no_double_limit(db):
    session, actor, initial = db
    with pytest.raises(DomainError): purchase(session, actor, 10000, currency="USD")
    now = utcnow()
    quote = prepare_credit_fx_quote(session, actor, 10000, {"provider": "TEST_STUB", "source_url": "test://fx-rate",
        "rate": "7.1", "market_date": now.date().isoformat(), "fetched_at": now.isoformat()})
    receipt = purchase(session, actor, 10000, currency="USD", quote=quote["quote_id"])
    assert receipt["base_exposure_minor"] == 71000
    facility = session.get(CreditAccount, initial["credit_account_id"])
    facility.base_credit_limit_minor = 75000
    with pytest.raises(DomainError): purchase(session, actor, 5000, key="over-shared")
    view = credit_view(session, actor)["cards"][0]
    assert view["available_base_credit_minor"] == 4000
    assert {row["currency"]: row["principal_minor"] for row in view["balances"]} == {"CNY": 0, "USD": 10000}


def test_repay_partial_refund_and_economic_rows_not_repayment(db):
    session, actor, initial = db
    bought = purchase(session, actor)
    source = resolve_source_instrument(session, actor, purpose="REPAYMENT")
    receipt = repay_credit(session, actor, source_snapshot=source, amount_minor=4000, operation_key="repay")
    assert receipt["remaining_debt_minor"] == 6000
    assert repay_credit(session, actor, source_snapshot=source, amount_minor=4000, operation_key="repay")["replayed"]
    assert session.scalar(select(func.sum(Account.balance_minor))) == 96001
    refund = refund_merchant_payment(session, actor, bought["payment_id"], operation_key="refund")
    assert refund["credit_balance_added_minor"] == 4000
    assert session.scalar(select(func.sum(Account.balance_minor))) == 96001
    rows = credit_bill_rows(session, actor, utcnow()-timedelta(days=1), utcnow()+timedelta(days=1))
    assert [(row["source_kind"], row["amount_minor"]) for row in rows] == [("CREDIT_PURCHASE", -10000), ("CREDIT_REFUND", 10000)]
    assert session.get(CreditAccount, initial["credit_account_id"]).posted_exposure_minor == 0


def test_installment_principal_not_duplicate_and_selective_early_settlement(db):
    session, actor, _ = db
    first = purchase(session, actor, 1000, key="old")
    second = purchase(session, actor, 10001, key="new")
    item = create_credit_installment(session, actor, payment_id=second["payment_id"], periods=3, operation_key="inst")
    assert sum(part["principal_minor"] for part in item["schedule"]) == 10001
    assert sum(part["fee_minor"] for part in item["schedule"]) == item["fee_minor"] == 121
    assert item["new_consumption_minor"] == 0
    source = resolve_source_instrument(session, actor, purpose="REPAYMENT")
    receipt = settle_credit_installment(session, actor, installment_id=item["installment_id"], source_snapshot=source, operation_key="settle")
    assert receipt["status"] == "SETTLED"
    obligations = list(session.scalars(select(CreditObligation).order_by(CreditObligation.created_at)))
    assert {row.payment_id: row.remaining_minor for row in obligations} == {first["payment_id"]: 1000, second["payment_id"]: 0}
    assert statement_view(session, actor)["statements_by_currency"][0]["outstanding_minor"] == 1000
    economic = credit_bill_rows(session, actor, utcnow()-timedelta(days=1), utcnow()+timedelta(days=1))
    assert [row["amount_minor"] for row in economic if row["source_kind"] == "CREDIT_FEE"] == [-121]


def test_statement_close_immutable_replay_and_currency_separate(db):
    session, actor, _ = db
    purchase(session, actor)
    now = utcnow()
    report = close_credit_statement(session, actor, currency="CNY", cycle_key="fixture",
        start=now-timedelta(days=1), end=now, due_at=now+timedelta(days=20))
    repay_credit(session, actor, source_snapshot=resolve_source_instrument(session, actor, purpose="REPAYMENT"), amount_minor=1000, operation_key="repay")
    row = session.get(CreditStatement, report["statement_id"])
    assert row.snapshot_json["due_minor"] == 10000
    assert statement_view(session, actor)["statements_by_currency"][0]["outstanding_minor"] == 9000


def test_central_reservation_protects_other_spending_and_own_budget(db):
    session, actor, _ = db
    source = resolve_source_instrument(session, actor, purpose="LIFE_PLAN")
    reserved = reserve_cash(session, actor, instrument_snapshot=source, amount_minor=60000, reservation_key="plan", owner_ref="plan1")
    current = resolve_source_instrument(session, actor, purpose="LIFE_PLAN")
    assert current["available_balance_minor"] == 10001
    with pytest.raises(DomainError): post_merchant_payment(session, actor, instrument_snapshot=current,
        amount_minor=20000, merchant_ref="other", operation_key="steal")
    result = post_merchant_payment(session, actor, instrument_snapshot=current, amount_minor=20000, merchant_ref="plan-shop",
        operation_key="plan-pay", reservation_id=reserved["reservation_id"], channel="LIFE_PLAN", authorization_ref="plan1")
    assert result["ledger_balanced"]


def test_explicit_recipient_not_invalidated_by_default_change(db):
    session, actor, initial = db
    source = resolve_source_instrument(session, actor)
    explicit = resolve_recipient_instrument(session, "credit-fixture", source["instrument_id"])
    default = resolve_recipient_instrument(session, "credit-fixture")
    assert explicit["selection_kind"] == "EXPLICIT" and "card_number" not in explicit
    settings = session.scalar(select(CustomerPaymentDefaults)); settings.recipient_instrument_id = "debit:"+initial["debit_card_ids"][1]; settings.version += 1
    assert validate_recipient_snapshot(session, explicit)["instrument_id"] == source["instrument_id"]
    with pytest.raises(DomainError): validate_recipient_snapshot(session, default)


def test_all_payment_journals_balanced_per_currency(db):
    session, actor, _ = db
    purchase(session, actor)
    for journal in session.scalars(select(PaymentJournal)):
        entries = list(session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == journal.id)))
        assert all(sum(row.delta_minor for row in entries if row.currency == currency) == 0 for currency in {row.currency for row in entries})


def test_weak_snapshot_allows_cash_change_not_card_controls_or_binding(db):
    session, actor, _ = db
    snap = resolve_source_instrument(session, actor)
    account = session.get(Account, snap["account_id"]); account.balance_minor -= 1; account.version += 1
    with pytest.raises(DomainError): validate_instrument_snapshot(session, actor, snap)
    assert validate_instrument_snapshot(session, actor, snap, version_policy="CARD_BINDING")["cash_balance_minor"] == 70000
    card = session.get(Card, snap["card_id"]); card.status = "LOCKED"; card.version += 1
    with pytest.raises(DomainError): validate_instrument_snapshot(session, actor, snap, version_policy="IDENTITY_AND_CONTROLS")


def test_failed_fee_capacity_rolls_back_installment_and_debt(db):
    session, actor, initial = db
    payment = purchase(session, actor)
    facility = session.get(CreditAccount, initial["credit_account_id"]); facility.base_credit_limit_minor = 10000
    from zhenxing.credit_models import CreditInstallment, CreditFeeCharge
    with pytest.raises(DomainError): create_credit_installment(session, actor, payment_id=payment["payment_id"], periods=3, operation_key="fee-over")
    assert session.scalar(select(func.count()).select_from(CreditInstallment)) == 0
    assert session.scalar(select(func.count()).select_from(CreditFeeCharge)) == 0
    assert session.scalar(select(CreditObligation.installment_id)) is None
    assert facility.posted_exposure_minor == 10000


def test_outer_rollback_reverts_nested_credit_payment(db):
    session, actor, _ = db
    count = session.scalar(select(func.count()).select_from(PaymentJournal))
    with pytest.raises(RuntimeError):
        with session.begin_nested():
            purchase(session, actor)
            raise RuntimeError("caller rollback")
    assert session.scalar(select(func.count()).select_from(PaymentJournal)) == count
    assert session.scalar(select(CreditAccount.posted_exposure_minor)) == 0


def test_usd_repayment_from_cny_requires_explicit_server_quote_and_balances_both_currencies(db):
    session, actor, _ = db
    now = utcnow(); rate = {"provider": "TEST_STUB", "source_url": "test://fx-rate", "rate": "7.1",
        "market_date": now.date().isoformat(), "fetched_at": now.isoformat()}
    quote = prepare_credit_fx_quote(session, actor, 1000, rate)
    purchase(session, actor, 1000, currency="USD", quote=quote["quote_id"])
    source = resolve_source_instrument(session, actor, purpose="REPAYMENT")
    with pytest.raises(DomainError): repay_credit(session, actor, source_snapshot=source, currency="USD", amount_minor=1000, operation_key="noquote")
    assert session.get(Account, source["account_id"]).balance_minor == 70001
    receipt = repay_credit(session, actor, source_snapshot=source, currency="USD", amount_minor=1000,
        operation_key="quoted-repay", fx_quote_id=quote["quote_id"])
    assert receipt["cash_debit_minor"] == 7100 and receipt["currency"] == "USD"
    assert session.get(Account, source["account_id"]).balance_minor == 62901
    assert session.scalar(select(CreditAccount.posted_exposure_minor)) == 0
    entries = list(session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == receipt["journal_id"])))
    assert {row.currency for row in entries} == {"CNY", "USD"}
    assert all(sum(row.delta_minor for row in entries if row.currency == curr) == 0 for curr in ("CNY", "USD"))


def test_fee_policy_zero_and_nonzero_disclosure_and_refund_retains_only_disclosed_fee(db):
    session, actor, initial = db
    facility = session.get(CreditAccount, initial["credit_account_id"])
    payment = purchase(session, actor, 10001)
    item = create_credit_installment(session, actor, payment_id=payment["payment_id"], periods=3, operation_key="fee")
    assert item["fee_minor"] == 121 and item["pricing"]["total_fee_bps"] == 120
    assert facility.posted_exposure_minor == 10122
    refund_merchant_payment(session, actor, payment["payment_id"], operation_key="refunded")
    balance = session.scalar(select(CreditCurrencyBalance).where(CreditCurrencyBalance.currency == "CNY"))
    assert (balance.principal_minor, balance.fees_minor) == (0, 121)
    view = statement_view(session, actor)["statements_by_currency"][0]
    assert view["current_due_minor"] == 121
    facility.policy_json = {**facility.policy_json, "installment_total_fee_bps": 0}
    payment2 = purchase(session, actor, 1000, key="fee-free")
    free = create_credit_installment(session, actor, payment_id=payment2["payment_id"], periods=3, operation_key="free")
    assert free["fee_minor"] == 0 and sum(row["fee_minor"] for row in free["schedule"]) == 0


def task_for(session, actor, kind="CREDIT_REPAY"):
    from zhenxing.models import Task
    task = Task(sandbox_id=actor.sandbox_id, user_id=actor.user_id, message="隔离显式办理",
        intent_type="CREDIT_CARD", state="NEED_CLARIFICATION", model_label="isolated-no-model")
    session.add(task); session.flush()
    return task


def test_runtime_read_no_confirm_and_repayment_before_confirmation_changes_nothing(db):
    from zhenxing.credit_runtime import prepare_credit_task, commit_credit_task
    from zhenxing.models import Confirmation
    session, actor, _ = db
    purchase(session, actor)
    task = task_for(session, actor)
    prepare_credit_task(session, task, {"kind": "CREDIT_REPAY", "slots": {"repayment_mode": "FULL"}})
    session.flush()
    assert task.state == "AWAITING_CONFIRMATION"
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001
    confirmation = session.scalar(select(Confirmation).where(Confirmation.task_id == task.id))
    with pytest.raises(DomainError): commit_credit_task(session, task, actor, None, "no-confirm")
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001
    commit_credit_task(session, task, actor, confirmation, "confirmed")
    assert task.state == "SUCCEEDED" and confirmation.status == "CONSUMED"
    assert task.result_json["remaining_debt_minor"] == 0
    readonly = task_for(session, actor)
    prepare_credit_task(session, readonly, {"kind": "CREDIT_VIEW", "slots": {}})
    assert readonly.state == "SUCCEEDED" and readonly.result_json["bank_state_changed"] is False


@pytest.mark.parametrize("mutation", ["expired", "view_hash", "card", "facility", "owner", "plan"])
def test_runtime_stale_confirmation_rejected_atomically(db, mutation):
    from zhenxing.credit_runtime import prepare_credit_task, commit_credit_task
    from zhenxing.models import Confirmation
    session, actor, initial = db
    purchase(session, actor)
    task = task_for(session, actor)
    prepare_credit_task(session, task, {"kind": "CREDIT_REPAY", "slots": {"repayment_mode": "FULL"}})
    session.flush(); confirmation = session.scalar(select(Confirmation).where(Confirmation.task_id == task.id))
    if mutation == "expired": confirmation.expires_at = utcnow()-timedelta(seconds=1)
    elif mutation == "view_hash": confirmation.view_hash = "forged"
    elif mutation == "card": session.scalar(select(CreditCard)).version += 1
    elif mutation == "facility": session.get(CreditAccount, initial["credit_account_id"]).version += 1
    elif mutation == "owner": confirmation.user_id = "not-owner"
    elif mutation == "plan": task.plan_json = {**task.plan_json, "policy_version": "forged"}
    with pytest.raises(DomainError): commit_credit_task(session, task, actor, confirmation, "stale")
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001
    assert session.get(CreditAccount, initial["credit_account_id"]).posted_exposure_minor == 10000


def test_application_admin_review_issue_then_owner_activate_and_shared_same_facility(db):
    from zhenxing.credit_lifecycle import submit_credit_application, decide_credit_application
    session, actor, initial = db
    result = submit_credit_application(session, actor, kind="CARD", operation_key="apply")
    assert result["status"] == "PENDING_REVIEW" and session.scalar(select(func.count()).select_from(CreditCard)) == 1
    with pytest.raises(DomainError): decide_credit_application(session, actor, result["application_id"], approved=True, operation_key="self-review")
    sandbox = Sandbox(); session.add(sandbox); session.flush()
    adminuser = User(sandbox_id=sandbox.id, display_name="审核员"); session.add(adminuser); session.flush()
    session.add(LoginAccount(username="credit-review-fixture", password_hash="unused", user_id=adminuser.id,
        sandbox_id=sandbox.id, role="ADMIN", status="ACTIVE")); session.flush()
    admin = SimpleNamespace(user_id=adminuser.id, sandbox_id=sandbox.id, family_context=None)
    decision = decide_credit_application(session, admin, result["application_id"], approved=True, operation_key="admin-review")
    issued = session.get(CreditCard, decision["card_id"])
    assert issued.status == "ISSUED" and issued.credit_account_id == initial["credit_account_id"]
    with pytest.raises(DomainError): resolve_source_instrument(session, actor, "credit:"+issued.id, purpose="MERCHANT")
    control_credit_card(session, actor, card_reference="credit:"+issued.id, action="ACTIVATE", operation_key="activate")
    assert resolve_source_instrument(session, actor, "credit:"+issued.id, purpose="MERCHANT")["paymentable"]
    assert session.scalar(select(func.count()).select_from(CreditAccount)) == 1
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001


def test_unknown_age_account_upgrade_does_not_issue_credit(db):
    session, _, _ = db
    sandbox = Sandbox(); session.add(sandbox); session.flush()
    user = User(sandbox_id=sandbox.id, display_name="未登记成年条件"); session.add(user); session.flush()
    account = Account(sandbox_id=sandbox.id, user_id=user.id, name="旧账户", balance_minor=101, currency="CNY")
    session.add(account); session.flush()
    for tail in ("4321", "8876"): session.add(Card(sandbox_id=sandbox.id, user_id=user.id, account_id=account.id, last4=tail))
    session.add(LoginAccount(username="age-unknown", password_hash="unused", user_id=user.id, sandbox_id=sandbox.id, role="CUSTOMER"))
    session.flush()
    result = initialize_customer_payment_services(session, sandbox.id, user.id)
    assert result["credit_card_ids"] == [] and result["credit_account_id"] is None
    assert result["primary_balance_minor"] == 71 and result["secondary_balance_minor"] == 30


def test_schema22_repeat_adoption_with_two_debit_accounts_preserves_cash_and_history(tmp_path):
    from zhenxing.simulation_models import SimulationState
    from zhenxing.models import Transaction
    engine, factory = create_database("sqlite:///" + (tmp_path/"isolated-upgrade.sqlite3").as_posix())
    ensure_schema(engine)
    with factory.begin() as session:
        sandbox = Sandbox(); session.add(sandbox); session.flush()
        user = User(sandbox_id=sandbox.id, display_name="隔离迁移"); session.add(user); session.flush()
        account = Account(sandbox_id=sandbox.id, user_id=user.id, name="旧账户", balance_minor=100001, currency="CNY")
        session.add(account); session.flush()
        for tail in ("4321", "8876"): session.add(Card(sandbox_id=sandbox.id, user_id=user.id, account_id=account.id, last4=tail))
        session.add(LoginAccount(username="migration-only", password_hash="unused", user_id=user.id, sandbox_id=sandbox.id, role="CUSTOMER"))
        session.flush()
        initialize_customer_payment_services(session, sandbox.id, user.id)
        actor = SimpleNamespace(sandbox_id=sandbox.id, user_id=user.id, family_context=None)
        source = resolve_source_instrument(session, actor, purpose="MERCHANT")
        post_merchant_payment(session, actor, instrument_snapshot=source, amount_minor=1000, merchant_ref="fixture", operation_key="pay")
    ensure_schema(engine); ensure_schema(engine)
    with factory() as session:
        assert session.scalar(select(func.sum(Account.balance_minor))) == 99001
        assert session.scalar(select(func.count()).select_from(SimulationState)) == 1
        assert session.scalar(select(SimulationState.opening_balance_minor)) == 100001
        assert session.scalar(select(func.count()).select_from(Transaction)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentUpgradeRecord)) == 1
    engine.dispose()


def test_card_union_keeps_debit_cash_separate_and_no_internal_scope_refs(db):
    from zhenxing.cards import owned_card_status
    session, actor, _ = db
    result = owned_card_status(session, actor)
    assert len(result["cards"]) == 2 and len(result["credit_cards"]) == 1
    assert all(row["instrument_type"] == "DEBIT" for row in result["cards"])
    assert sorted(row["balances_by_currency"]["CNY"] for row in result["cards"]) == [30000, 70001]
    assert all("balance_scope_refs" not in row for row in result["cards"])
    assert all("balances_by_currency" not in row for row in result["credit_cards"])


def test_simulation_total_cash_and_individual_accounts_reconcile_without_credit_assets(db):
    from zhenxing.simulation import inspect_simulation
    from zhenxing.simulation_models import SimulationState
    from zhenxing.credit_service import credit_account_invariants
    session, actor, _ = db
    session.add(SimulationState(sandbox_id=actor.sandbox_id, profile="baseline", seed_version="isolated", seed=1,
        business_time=utcnow(), opening_balance_minor=100001)); session.flush()
    credit = purchase(session, actor)
    source = resolve_source_instrument(session, actor, purpose="MERCHANT")
    post_merchant_payment(session, actor, instrument_snapshot=source, amount_minor=1000, merchant_ref="cash-shop", operation_key="cash-buy")
    repay_credit(session, actor, source_snapshot=resolve_source_instrument(session, actor, purpose="REPAYMENT"), amount_minor=3000, operation_key="repay")
    result = inspect_simulation(session, actor.sandbox_id)
    assert result["counts"]["accounts"] == 2
    assert result["reconciliation"]["actual_balance_minor"] == 96001
    assert result["reconciliation"]["payment_cash_delta_minor"] == -4000
    assert result["reconciliation"]["balanced"] and result["independent_account_reconciliation"]["ok"]
    assert credit_account_invariants(session, actor)["ok"]
    # Equal and opposite unjournaled errors must not disappear in total cash.
    accounts = list(session.scalars(select(Account).order_by(Account.id)))
    accounts[0].balance_minor -= 1; accounts[1].balance_minor += 1
    broken = inspect_simulation(session, actor.sandbox_id)
    assert broken["reconciliation"]["difference_minor"] == 0
    assert not broken["reconciliation"]["balanced"] and not broken["independent_account_reconciliation"]["ok"]


def test_credit_read_proof_excludes_observation_time_and_rejects_changed_or_foreign_source(db):
    from zhenxing.credit_facts import credit_read_result_current, credit_result_fact_entities
    session, actor, _ = db
    result = credit_view(session, actor)
    assert credit_read_result_current(session, actor, "CREDIT_VIEW", result)
    changed_time = deepcopy(result); changed_time["observed_at"] = "2026-10-18T00:00:00+00:00"
    assert credit_read_result_current(session, actor, "CREDIT_VIEW", changed_time)
    foreign = SimpleNamespace(sandbox_id="foreign", user_id="foreign", family_context=None)
    assert not credit_read_result_current(session, foreign, "CREDIT_VIEW", result)
    entities = credit_result_fact_entities("CREDIT_VIEW", result)
    assert {row["field"] for row in entities} >= {"credit_collection", "credit_card_status", "credit_facility", "credit_currency_debt"}
    debts = [row for row in entities if row["field"] == "credit_currency_debt"]
    assert {row["facts"]["currency"] for row in debts} == {"CNY", "USD"}
    assert all(row["facts"]["credit_is_cash"] is False for row in entities)
    purchase(session, actor)
    assert not credit_read_result_current(session, actor, "CREDIT_VIEW", result)
    forged = deepcopy(result); forged["cards"][0]["base_credit_limit_minor"] += 1
    assert credit_result_fact_entities("CREDIT_VIEW", forged) == []


def test_credit_statement_facts_keep_fees_and_installment_remaining_distinct(db):
    from zhenxing.credit_facts import credit_read_result_current, credit_result_fact_entities
    session, actor, _ = db
    payment = purchase(session, actor, 10001)
    item = create_credit_installment(session, actor, payment_id=payment["payment_id"], periods=3, operation_key="facts-inst")
    result = statement_view(session, actor)
    assert credit_read_result_current(session, actor, "CREDIT_STATEMENT", result)
    facts = credit_result_fact_entities("CREDIT_STATEMENT", result)
    installment = next(row["facts"] for row in facts if row["field"] == "credit_installment")
    assert installment["remaining_principal_minor"] == 10001 and installment["remaining_fee_minor"] == 121
    assert "policy_hash" not in installment
    repay_credit(session, actor, source_snapshot=resolve_source_instrument(session, actor, purpose="REPAYMENT"), amount_minor=100, operation_key="facts-repay")
    assert not credit_read_result_current(session, actor, "CREDIT_STATEMENT", result)


def test_same_currency_usd_repayment_uses_original_wallet_and_fx_inspector_includes_central_cash(db):
    from zhenxing.fx_models import DualCurrencyProfile, FxLedgerEntry, FxOperation, FxWallet
    from zhenxing.fx_banking import inspect_fx, _statement, usd_ref
    session, actor, initial = db
    debit_card = session.get(Card, initial["debit_card_ids"][1])
    wallet = FxWallet(sandbox_id=actor.sandbox_id, user_id=actor.user_id, balance_minor=1000)
    session.add(wallet); session.flush()
    session.add(DualCurrencyProfile(sandbox_id=actor.sandbox_id, user_id=actor.user_id,
        card_id=debit_card.id, usd_wallet_id=wallet.id))
    operation = FxOperation(sandbox_id=actor.sandbox_id, user_id=actor.user_id, kind="INITIAL_FUNDING",
        original_key="fixture-usd-funding", requested_currency="USD", requested_amount_minor=1000,
        source_currency="USD", target_currency="USD", source_ref="SIM_FX_INITIAL_POOL", target_ref=usd_ref(wallet.id),
        debit_minor=1000, credit_minor=1000, receipt_json={"synthetic": True,
            "funding_source": "EXPLICIT_SIM_INITIAL_FUNDS", "fee_minor": 0, "fee_source": "SIM_POLICY"})
    session.add(operation); session.flush()
    session.add_all([FxLedgerEntry(operation_id=operation.id, sandbox_id=actor.sandbox_id, user_id=actor.user_id,
        account_ref=usd_ref(wallet.id), currency="USD", delta_minor=1000),
        FxLedgerEntry(operation_id=operation.id, sandbox_id=actor.sandbox_id, user_id=actor.user_id,
            account_ref="FX_POOL:"+operation.id+":USD", currency="USD", delta_minor=-1000)])
    _statement(session, operation, actor.sandbox_id, actor.user_id, usd_ref(wallet.id), "USD", 1000, "隔离美元初始资金")
    now = utcnow(); quote = prepare_credit_fx_quote(session, actor, 200, {"provider": "TEST_STUB", "source_url": "test://fx-rate",
        "rate": "7.1", "market_date": now.date().isoformat(), "fetched_at": now.isoformat()})
    purchase(session, actor, 200, currency="USD", quote=quote["quote_id"])
    source = resolve_source_instrument(session, actor, "debit:"+debit_card.id, "USD", purpose="REPAYMENT")
    receipt = repay_credit(session, actor, source_snapshot=source, currency="USD", amount_minor=200, operation_key="usd-own-wallet")
    assert receipt["cash_currency"] == "USD" and wallet.balance_minor == 800
    assert session.scalar(select(func.sum(Account.balance_minor))) == 100001
    fx = inspect_fx(session, actor.sandbox_id, actor.user_id)
    assert fx["balanced"] and fx["balances"][0]["central_payment_cash_minor"] == -200, (fx["mismatches"], fx["balances"])


def test_postgresql_bigint_sum_is_exact_integer_not_json_string():
    from decimal import Decimal
    import json
    from zhenxing.payment_reconciliation import accounting_integer
    assert type(accounting_integer(Decimal("70001"))) is int
    assert json.dumps({"balance_minor":accounting_integer(Decimal("70001"))}) == '{"balance_minor": 70001}'
    for invalid in (Decimal("0.01"),Decimal("NaN"),Decimal("Infinity"),True,"70001",70001.0):
        with pytest.raises(DomainError):accounting_integer(invalid)


def test_partial_usd_capture_then_void_does_not_round_shared_exposure_down(db):
    session,actor,initial=db;now=utcnow()
    quote=prepare_credit_fx_quote(session,actor,2,{"provider":"TEST_STUB","source_url":"test://fx-rate","rate":"7.1",
        "market_date":now.date().isoformat(),"fetched_at":now.isoformat()})
    authorization=authorize_credit_purchase(session,actor,instrument_snapshot=credit_snapshot(session,actor,"USD"),
        amount_minor=2,merchant_ref="rounding-fixture",operation_key="tiny-auth",fx_quote_id=quote["quote_id"])
    capture=capture_credit_authorization(session,actor,authorization["authorization_id"],amount_minor=1,operation_key="tiny-capture")
    void_credit_authorization(session,actor,authorization["authorization_id"],operation_key="tiny-void")
    facility=session.get(CreditAccount,initial["credit_account_id"])
    assert capture["base_exposure_minor"]==facility.posted_exposure_minor==8
    assert facility.authorization_exposure_minor==0
