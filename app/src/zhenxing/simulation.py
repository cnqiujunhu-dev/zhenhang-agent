from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .banking_extension_models import AALedgerEntry
from .models import (
    Account, Card, InvestmentLedgerEntry, InvestmentProduct, LedgerEntry,
    Payee, Subscription, SubscriptionLedgerEntry, Transaction,
)
from .simulation_models import SimulationState
from .credit_models import PaymentJournal, PaymentLedgerEntry
from .payment_reconciliation import accounting_integer
from .internal_banking_models import InternalTransferSettlement
from . import extensions_models as _extension_models  # Pure metadata for standalone Base fixtures.
from . import family_account_context_models as _family_context_models
from . import card_identifier_models as _card_identifier_models


SEED_VERSION = "bank-ledger-v1"
DEFAULT_SEED = 20260930
BUSINESS_TIME = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)
DISPLAY_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
FAULT_MODES = frozenset({
    "NONE", "PRE_COMMIT_FAILURE", "AFTER_COMMIT_RESPONSE_LOST", "DELAY", "PRICE_CHANGED",
})
PROFILE_LABELS = {
    "baseline": "小型回归基线",
    "young_professional": "年轻上班族",
    "family": "家庭账套",
    "pensioner": "养老金用户",
    "investor": "稳健投资者",
}


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def add_month(value: datetime) -> datetime:
    month = value.month % 12 + 1
    year = value.year + (value.month == 12)
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def list_profiles() -> list[dict]:
    return [
        {
            "profile": profile,
            "label": label,
            "seed_version": SEED_VERSION,
            "default_seed": DEFAULT_SEED,
            "business_time": BUSINESS_TIME.isoformat(),
            "history_months": 0 if profile == "baseline" else 12,
            "synthetic": True,
        }
        for profile, label in PROFILE_LABELS.items()
    ]


def get_simulation_state(session: Session, sandbox_id: str) -> SimulationState:
    state = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == sandbox_id))
    if state is None:
        raise ValueError("SIMULATION_NOT_FOUND")
    return state


def business_now(session: Session, sandbox_id: str) -> datetime:
    """Read bank time only; authentication/lease code must continue using utcnow()."""
    return as_utc(get_simulation_state(session, sandbox_id).business_time)


def inspect_simulation(session: Session, sandbox_id: str) -> dict:
    session.flush()
    state = get_simulation_state(session, sandbox_id)
    accounts = list(session.scalars(select(Account).where(Account.sandbox_id == sandbox_id).order_by(Account.id)))
    if not accounts:
        raise ValueError("SIMULATION_ACCOUNT_NOT_FOUND")
    account_ids = [account.id for account in accounts]
    actual_balance = sum(account.balance_minor for account in accounts)
    transactions = list(session.scalars(select(Transaction).where(
        Transaction.sandbox_id == sandbox_id, Transaction.account_id.in_(account_ids),
    ).order_by(Transaction.occurred_at)))
    history_transactions = [row for row in transactions if row.source_kind == "HISTORY"]
    movement_kinds = {"TRANSFER", "INVESTMENT", "AA_COLLECTION", "DEV_TEST_ADJUSTMENT", "FX", "SUBSCRIPTION", "MERCHANT_PAYMENT", "MERCHANT_REFUND", "CREDIT_REPAYMENT",'OWN_ACCOUNT_TRANSFER'}
    movement_transactions = [row for row in transactions if row.source_kind in movement_kinds]
    unsupported_sources = sorted({row.source_kind for row in transactions
                                  if row.source_kind not in {"HISTORY", *movement_kinds}})
    history_net = sum(row.amount_minor for row in history_transactions)
    transfer_cash_delta = session.scalar(select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
        LedgerEntry.sandbox_id == sandbox_id, LedgerEntry.account_ref.in_(account_ids),
    ))
    investment_cash_delta = session.scalar(select(func.coalesce(func.sum(InvestmentLedgerEntry.delta_minor), 0)).where(
        InvestmentLedgerEntry.sandbox_id == sandbox_id, InvestmentLedgerEntry.account_ref.in_(account_ids),
    ))
    aa_cash_delta = session.scalar(select(func.coalesce(func.sum(AALedgerEntry.delta_minor), 0)).where(
        AALedgerEntry.sandbox_id == sandbox_id, AALedgerEntry.account_ref.in_(account_ids),
    ))
    subscription_cash_delta = session.scalar(select(func.coalesce(func.sum(SubscriptionLedgerEntry.delta_minor),0)).where(
        SubscriptionLedgerEntry.sandbox_id==sandbox_id,SubscriptionLedgerEntry.account_ref.in_(account_ids)))
    subscription_ledger_net = session.scalar(select(func.coalesce(func.sum(SubscriptionLedgerEntry.delta_minor),0)).where(
        SubscriptionLedgerEntry.sandbox_id==sandbox_id))
    unbalanced_subscriptions=list(session.scalars(select(SubscriptionLedgerEntry.payment_id).where(
        SubscriptionLedgerEntry.sandbox_id==sandbox_id).group_by(SubscriptionLedgerEntry.payment_id)
        .having(func.sum(SubscriptionLedgerEntry.delta_minor)!=0)))
    from .access_models import ScenarioAdjustmentLedger
    development_cash_delta = session.scalar(select(func.coalesce(func.sum(ScenarioAdjustmentLedger.delta_minor), 0)).where(
        ScenarioAdjustmentLedger.sandbox_id == sandbox_id, ScenarioAdjustmentLedger.account_ref.in_(account_ids)))
    development_ledger_net = session.scalar(select(func.coalesce(func.sum(ScenarioAdjustmentLedger.delta_minor), 0)).where(
        ScenarioAdjustmentLedger.sandbox_id == sandbox_id))
    from .fx_models import FxLedgerEntry
    from .fx_banking import inspect_fx
    fx_cash_delta = accounting_integer(session.scalar(select(func.coalesce(func.sum(FxLedgerEntry.delta_minor), 0)).where(
        FxLedgerEntry.sandbox_id == sandbox_id, FxLedgerEntry.account_ref.in_(account_ids),
        FxLedgerEntry.currency == "CNY")))
    fx_reconciliation = inspect_fx(session, sandbox_id)
    unbalanced_development_edits = list(session.scalars(select(ScenarioAdjustmentLedger.edit_event_id).where(
        ScenarioAdjustmentLedger.sandbox_id == sandbox_id).group_by(ScenarioAdjustmentLedger.edit_event_id)
        .having(func.sum(ScenarioAdjustmentLedger.delta_minor) != 0)))
    transfer_ledger_net = session.scalar(select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
        LedgerEntry.sandbox_id == sandbox_id,
    ))
    investment_ledger_net = session.scalar(select(func.coalesce(func.sum(InvestmentLedgerEntry.delta_minor), 0)).where(
        InvestmentLedgerEntry.sandbox_id == sandbox_id,
    ))
    aa_ledger_net = session.scalar(select(func.coalesce(func.sum(AALedgerEntry.delta_minor), 0)).where(
        AALedgerEntry.sandbox_id == sandbox_id,
    ))
    unbalanced_transfers = [row[0] for row in session.execute(
        select(LedgerEntry.transfer_id).where(LedgerEntry.sandbox_id == sandbox_id)
        .group_by(LedgerEntry.transfer_id).having(func.sum(LedgerEntry.delta_minor) != 0)
    )]
    unbalanced_orders = [row[0] for row in session.execute(
        select(InvestmentLedgerEntry.order_id).where(InvestmentLedgerEntry.sandbox_id == sandbox_id)
        .group_by(InvestmentLedgerEntry.order_id).having(func.sum(InvestmentLedgerEntry.delta_minor) != 0)
    )]
    unbalanced_aa = [row[0] for row in session.execute(
        select(AALedgerEntry.participant_id).where(AALedgerEntry.sandbox_id == sandbox_id)
        .group_by(AALedgerEntry.participant_id).having(func.sum(AALedgerEntry.delta_minor) != 0)
    )]
    expected_mirrors = {}
    for model, key, kind, prefix in [
        (LedgerEntry, LedgerEntry.transfer_id, "TRANSFER", "transfer"),
        (InvestmentLedgerEntry, InvestmentLedgerEntry.order_id, "INVESTMENT", "investment"),
        (AALedgerEntry, AALedgerEntry.participant_id, "AA_COLLECTION", "aa"),
        (SubscriptionLedgerEntry, SubscriptionLedgerEntry.payment_id, "SUBSCRIPTION", "subscription"),
        (ScenarioAdjustmentLedger, ScenarioAdjustmentLedger.edit_event_id, "DEV_TEST_ADJUSTMENT", "test_adjust"),
    ]:
        for reference, amount in session.execute(select(key, func.sum(model.delta_minor)).where(
            model.sandbox_id == sandbox_id, model.account_ref.in_(account_ids),
        ).group_by(key)):
            expected_mirrors[f"{prefix}:{reference}"] = (kind, amount)
    for reference, amount in session.execute(select(FxLedgerEntry.operation_id, func.sum(FxLedgerEntry.delta_minor)).where(
            FxLedgerEntry.sandbox_id == sandbox_id, FxLedgerEntry.account_ref.in_(account_ids),
            FxLedgerEntry.currency == "CNY").group_by(FxLedgerEntry.operation_id)):
        expected_mirrors[f"fx:{reference}:CNY"] = ("FX", amount)
    payment_cash_delta = accounting_integer(session.scalar(select(func.coalesce(func.sum(PaymentLedgerEntry.delta_minor), 0)).where(
        PaymentLedgerEntry.sandbox_id == sandbox_id, PaymentLedgerEntry.account_ref.in_(account_ids), PaymentLedgerEntry.currency == "CNY")))
    payment_nets = {currency: accounting_integer(amount) for currency, amount in session.execute(select(PaymentLedgerEntry.currency,
        func.sum(PaymentLedgerEntry.delta_minor)).where(PaymentLedgerEntry.sandbox_id == sandbox_id).group_by(PaymentLedgerEntry.currency))}
    unbalanced_payments = list(session.execute(select(PaymentLedgerEntry.journal_id, PaymentLedgerEntry.currency).where(
        PaymentLedgerEntry.sandbox_id == sandbox_id).group_by(PaymentLedgerEntry.journal_id, PaymentLedgerEntry.currency)
        .having(func.sum(PaymentLedgerEntry.delta_minor) != 0)))
    unsupported_payment_journals = []
    for journal, amount in session.execute(select(PaymentJournal, func.sum(PaymentLedgerEntry.delta_minor)).join(
            PaymentLedgerEntry, PaymentLedgerEntry.journal_id == PaymentJournal.id).where(PaymentJournal.sandbox_id == sandbox_id,
            PaymentLedgerEntry.account_ref.in_(account_ids), PaymentLedgerEntry.currency == "CNY").group_by(PaymentJournal.id)):
        receipt = journal.result_json or {}
        if journal.kind == "INTERNAL_ACCOUNT_SPLIT" and amount == 0: continue
        if journal.kind=='OWN_ACCOUNT_TRANSFER':
            from .own_account_transfer_models import OwnAccountTransferRecord
            from .own_account_transfers import own_transfer_posting_integrity
            record=session.scalar(select(OwnAccountTransferRecord).where(OwnAccountTransferRecord.journal_id==journal.id))
            if record is None or amount!=0 or not own_transfer_posting_integrity(session,record):
                unsupported_payment_journals.append(journal.id);continue
            expected_mirrors[f'own-transfer:{record.id}:OUT']=('OWN_ACCOUNT_TRANSFER',-record.amount_minor)
            expected_mirrors[f'own-transfer:{record.id}:IN']=('OWN_ACCOUNT_TRANSFER',record.amount_minor)
            continue
        mappings = {"MERCHANT_PAYMENT": ("payment_id", "merchant", "MERCHANT_PAYMENT"),
            "MERCHANT_REFUND": ("refund_id", "merchant-refund", "MERCHANT_REFUND"),
            "CREDIT_REPAYMENT": ("repayment_id", "credit-repayment", "CREDIT_REPAYMENT")}
        if journal.kind not in mappings:
            unsupported_payment_journals.append(journal.id); continue
        receipt_key, prefix, source = mappings[journal.kind]
        if not receipt.get(receipt_key): unsupported_payment_journals.append(journal.id); continue
        expected_mirrors[f"{prefix}:{receipt[receipt_key]}"] = (source, amount)
    observed_mirrors = {}
    duplicate_mirrors = []
    for row in movement_transactions:
        if row.operation_ref in observed_mirrors:
            duplicate_mirrors.append(row.operation_ref)
        observed_mirrors[row.operation_ref] = (row.source_kind, row.amount_minor)
    missing_mirrors = sorted(set(expected_mirrors) - set(observed_mirrors))
    unexpected_mirrors = [reference for reference in observed_mirrors if reference not in expected_mirrors]
    mismatched_mirrors = sorted(reference for reference in set(expected_mirrors) & set(observed_mirrors)
                                if expected_mirrors[reference] != observed_mirrors[reference])
    statement_cash_delta = sum(row.amount_minor for row in movement_transactions)
    ledger_cash_delta = transfer_cash_delta + investment_cash_delta + aa_cash_delta + development_cash_delta + fx_cash_delta + subscription_cash_delta + payment_cash_delta
    expected_balance = state.opening_balance_minor + history_net + ledger_cash_delta
    monthly: dict[str, dict] = {}
    running_balance = state.opening_balance_minor
    minimum_history_balance = running_balance
    for row in history_transactions:
        month = as_utc(row.occurred_at).astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m")
        summary = monthly.setdefault(month, {
            "month": month, "income_minor": 0, "refund_minor": 0, "spending_minor": 0,
            "net_minor": 0, "transaction_count": 0, "opening_balance_minor": running_balance,
        })
        summary["transaction_count"] += 1
        summary["net_minor"] += row.amount_minor
        if row.amount_minor >= 0 and row.category == "退款":
            summary["refund_minor"] += row.amount_minor
        elif row.amount_minor >= 0:
            summary["income_minor"] += row.amount_minor
        else:
            summary["spending_minor"] -= row.amount_minor
        running_balance += row.amount_minor
        summary["closing_balance_minor"] = running_balance
        minimum_history_balance = min(minimum_history_balance, running_balance)
    counts = {
        "accounts": len(accounts),
        "cards": session.scalar(select(func.count()).select_from(Card).where(Card.sandbox_id == sandbox_id)),
        "payees": session.scalar(select(func.count()).select_from(Payee).where(Payee.sandbox_id == sandbox_id)),
        "subscriptions": session.scalar(select(func.count()).select_from(Subscription).where(Subscription.sandbox_id == sandbox_id)),
        "products": session.scalar(select(func.count()).select_from(InvestmentProduct).where(InvestmentProduct.sandbox_id == sandbox_id)),
        "transactions": len(transactions),
    }
    from .internal_banking_models import InternalTransferSettlement
    from .internal_banking import settlement_integrity
    from .models import Transfer
    relevant = session.scalars(select(InternalTransferSettlement).where(
        (InternalTransferSettlement.sender_sandbox_id == sandbox_id) | (InternalTransferSettlement.receiver_sandbox_id == sandbox_id)))
    settlement_errors = []
    for settlement in relevant:
        transfer = session.get(Transfer, settlement.transfer_id)
        result = settlement_integrity(session, transfer) if transfer else {"balanced": False, "errors": ["TRANSFER_RECORD_MISSING"]}
        if not result["balanced"]:
            settlement_errors.append({"transfer_id": settlement.transfer_id, "errors": result["errors"]})
    from .payment_reconciliation import independent_cash_account_checks
    independent_accounts = independent_cash_account_checks(session, sandbox_id)
    return {
        "sandbox_id": sandbox_id,
        "profile": state.profile,
        "profile_label": PROFILE_LABELS.get(state.profile, state.profile),
        "seed_version": state.seed_version,
        "seed": state.seed,
        "synthetic": True,
        "business_time": as_utc(state.business_time).isoformat(),
        "security_clock": "REAL_UTC",
        "time_zone": "Asia/Shanghai",
        "fault_mode": state.fault_mode,
        "fx_reconciliation": fx_reconciliation,
        "independent_account_reconciliation": independent_accounts,
        "counts": counts,
        "history": {
            "first_occurred_at": as_utc(history_transactions[0].occurred_at).isoformat() if history_transactions else None,
            "last_occurred_at": as_utc(history_transactions[-1].occurred_at).isoformat() if history_transactions else None,
            "months": len(monthly),
            "monthly": list(monthly.values()),
            "anomaly_count": sum(row.flag == "ANOMALY" for row in history_transactions),
            "refund_count": sum(row.category == "退款" for row in history_transactions),
            "minimum_balance_minor": minimum_history_balance,
        },
        "reconciliation": {
            "opening_balance_minor": state.opening_balance_minor,
            "history_net_minor": history_net,
            "transfer_cash_delta_minor": transfer_cash_delta,
            "investment_cash_delta_minor": investment_cash_delta,
            "aa_cash_delta_minor": aa_cash_delta,
            "subscription_cash_delta_minor": subscription_cash_delta,
            "development_cash_delta_minor": development_cash_delta,
            "fx_cash_delta_minor": fx_cash_delta,
            "payment_cash_delta_minor": payment_cash_delta,
            "statement_cash_delta_minor": statement_cash_delta,
            "ledger_cash_delta_minor": ledger_cash_delta,
            "statement_ledger_difference_minor": statement_cash_delta - ledger_cash_delta,
            "expected_balance_minor": expected_balance,
            "actual_balance_minor": actual_balance,
            "difference_minor": actual_balance - expected_balance,
            "transfer_ledger_net_minor": transfer_ledger_net,
            "investment_ledger_net_minor": investment_ledger_net,
            "aa_ledger_net_minor": aa_ledger_net,
            "subscription_ledger_net_minor": subscription_ledger_net,
            "unbalanced_subscription_payment_ids": unbalanced_subscriptions,
            "development_ledger_net_minor": development_ledger_net,
            "unbalanced_development_edit_ids": unbalanced_development_edits,
            "unbalanced_transfer_ids": unbalanced_transfers,
            "unbalanced_investment_order_ids": unbalanced_orders,
            "unbalanced_aa_participant_ids": unbalanced_aa,
            "missing_cash_operation_refs": missing_mirrors,
            "unexpected_cash_operation_refs": unexpected_mirrors,
            "mismatched_cash_operation_refs": mismatched_mirrors,
            "duplicate_cash_operation_refs": duplicate_mirrors,
            "unsupported_source_kinds": unsupported_sources,
            "payment_ledger_net_by_currency": payment_nets,
            "unbalanced_payment_journal_ids": sorted({row[0] for row in unbalanced_payments}),
            "unsupported_cash_payment_journal_ids": unsupported_payment_journals,
            "internal_settlement_errors": settlement_errors,
            "balanced": (fx_reconciliation["balanced"] and not settlement_errors and actual_balance == expected_balance and transfer_ledger_net == 0
                         and development_ledger_net == 0 and not unbalanced_development_edits
                         and investment_ledger_net == 0 and aa_ledger_net == 0 and subscription_ledger_net == 0
                         and not unbalanced_transfers and not unbalanced_orders and not unbalanced_aa and not unbalanced_subscriptions
                         and not missing_mirrors and not unexpected_mirrors and not mismatched_mirrors
                         and not duplicate_mirrors and not unsupported_sources and not unbalanced_payments
                         and not unsupported_payment_journals and all(amount == 0 for amount in payment_nets.values())
                         and independent_accounts["ok"]),
        },
    }


def advance_business_time(session: Session, sandbox_id: str, days: int) -> dict:
    """Advance a sandbox clock and post connected active recurring debits atomically.

    The caller owns commit/rollback and authorization. External unconnected merchant
    subscriptions cannot debit. Declined debits do not create a cash transaction.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("BUSINESS_TIME_DELTA_MUST_BE_1_TO_365_DAYS")
    state = session.scalar(select(SimulationState).where(
        SimulationState.sandbox_id == sandbox_id,
    ).with_for_update())
    if state is None:
        raise ValueError("SIMULATION_NOT_FOUND")
    if session.scalar(select(Account.id).where(Account.sandbox_id == sandbox_id)) is None:
        raise ValueError("SIMULATION_ACCOUNT_NOT_FOUND")
    old_time = as_utc(state.business_time)
    new_time = old_time + timedelta(days=days)
    subscriptions = list(session.scalars(select(Subscription).where(
        Subscription.sandbox_id == sandbox_id,
        Subscription.status == "ACTIVE",
        Subscription.auto_renew.is_(True),
        Subscription.source_type.in_(["BANK_MANDATE", "MERCHANT_SIM"]),
    ).order_by(Subscription.next_due_at, Subscription.merchant).with_for_update()))
    due_events: list[tuple[datetime, Subscription]] = []
    for subscription in subscriptions:
        due = as_utc(subscription.next_due_at)
        while due <= new_time:
            due_events.append((due, subscription))
            for _month in range(subscription.billing_months or 1):
                due = add_month(due)
        if as_utc(subscription.next_due_at) <= new_time:
            subscription.next_due_at = due
            subscription.version += 1
    posted = []
    declined = []
    for due, subscription in sorted(due_events, key=lambda item: (item[0], item[1].merchant)):
        from .contracts import DomainError
        from .payment_instruments import resolve_source_instrument, validate_instrument_snapshot
        from .services import Principal
        try:
            owner = Principal(subscription.user_id, sandbox_id)
            from .subscription_autorenew import restored_mandate_instrument
            source = restored_mandate_instrument(session, subscription)
            if source is None:
                source = resolve_source_instrument(session, owner, currency="CNY", purpose="MERCHANT")
            source = validate_instrument_snapshot(session, owner, source, purpose="MERCHANT", amount_minor=subscription.amount_minor)
            if source["instrument_type"] != "DEBIT":
                raise DomainError("RECURRING_CASH_SOURCE_REQUIRED", "旧订阅授权只能使用登记借记资金账户。", 409)
        except DomainError as error:
            declined.append({"subscription_id": subscription.id, "merchant": subscription.merchant,
                "due_at": due.isoformat(), "reason": "INSUFFICIENT_FUNDS" if error.code == "PAYMENT_INSUFFICIENT_FUNDS" else error.code})
            continue
        account = session.get(Account, source["account_id"])
        session.add(Transaction(
            sandbox_id=sandbox_id, account_id=account.id,
            card_id=source["card_id"],
            occurred_at=due, amount_minor=-subscription.amount_minor,
            merchant=subscription.merchant, category="订阅", flag="NORMAL",
        ))
        account.balance_minor -= subscription.amount_minor
        account.version += 1
        paid_end=due
        for _month in range(subscription.billing_months or 1):
            paid_end=add_month(paid_end)
        if subscription.entitlement_end_at is None or as_utc(subscription.entitlement_end_at)<paid_end:
            subscription.entitlement_end_at=paid_end
        # Only a posted debit extends rights. A decline, cancellation, or a
        # read of the current calendar never manufactures a paid period.
        subscription.last_paid_at=due
        subscription.last_payment_id=None  # Recurring statement, not an interactive payment receipt.
        subscription.version+=1
        posted.append({"subscription_id": subscription.id, "merchant": subscription.merchant,
                       "due_at": due.isoformat(), "amount_minor": subscription.amount_minor})
    state.business_time = new_time
    result = inspect_simulation(session, sandbox_id)
    result["advance"] = {"from": old_time.isoformat(), "to": new_time.isoformat(),
                         "days": days, "posted_recurring": posted, "declined_recurring": declined}
    return result


def set_fault_mode(session: Session, sandbox_id: str, mode: str) -> dict:
    if mode not in FAULT_MODES:
        raise ValueError("UNKNOWN_SIMULATION_FAULT_MODE")
    state = get_simulation_state(session, sandbox_id)
    state.fault_mode = mode
    return inspect_simulation(session, sandbox_id)
