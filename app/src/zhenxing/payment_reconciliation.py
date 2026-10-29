"""Read-only cash book totals, used for immutable split baselines and audit."""
from sqlalchemy import func, select
from decimal import Decimal


def accounting_integer(value):
    """PostgreSQL SUM(bigint) is NUMERIC; never stringify or truncate money."""
    if type(value) is int: return value
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value(): return int(value)
    from .contracts import DomainError
    raise DomainError("PAYMENT_ACCOUNTING_INTEGER_REQUIRED", "账本汇总不是有效的整数分，已停止处理。", 500)


def cash_account_book_totals(session, sandbox_id, account_ids):
    from .access_models import ScenarioAdjustmentLedger
    from .banking_extension_models import AALedgerEntry
    from .credit_models import PaymentLedgerEntry
    from .fx_models import FxLedgerEntry
    from .models import InvestmentLedgerEntry, LedgerEntry, SubscriptionLedgerEntry, Transaction
    totals = {account_id: {"ledger_minor": 0, "history_minor": 0} for account_id in account_ids}
    for model in (LedgerEntry, InvestmentLedgerEntry, AALedgerEntry, SubscriptionLedgerEntry,
                  ScenarioAdjustmentLedger, FxLedgerEntry, PaymentLedgerEntry):
        query = select(model.account_ref, func.sum(model.delta_minor)).where(model.sandbox_id == sandbox_id,
            model.account_ref.in_(account_ids))
        if hasattr(model, "currency"): query = query.where(model.currency == "CNY")
        for account_id, amount in session.execute(query.group_by(model.account_ref)):
            totals[account_id]["ledger_minor"] += accounting_integer(amount)
    for account_id, amount in session.execute(select(Transaction.account_id, func.sum(Transaction.amount_minor)).where(
            Transaction.sandbox_id == sandbox_id, Transaction.account_id.in_(account_ids),
            Transaction.source_kind == "HISTORY").group_by(Transaction.account_id)):
        totals[account_id]["history_minor"] = accounting_integer(amount)
    return totals


def independent_cash_account_checks(session, sandbox_id):
    from .credit_models import PaymentUpgradeRecord
    from .models import Account
    results = []
    for record in session.scalars(select(PaymentUpgradeRecord).where(PaymentUpgradeRecord.sandbox_id == sandbox_id)):
        baselines = (record.result_json or {}).get("cash_account_baselines", {})
        if not baselines:
            results.append({"migration_id": record.id, "ok": False, "error": "ACCOUNT_BASELINE_NOT_RECORDED"}); continue
        current = cash_account_book_totals(session, sandbox_id, list(baselines))
        for account_id, baseline in baselines.items():
            account = session.get(Account, account_id)
            expected = baseline["balance_minor"] + current[account_id]["ledger_minor"]-baseline["ledger_minor"] \
                +current[account_id]["history_minor"]-baseline["history_minor"]
            owned = account is not None and (account.user_id, account.sandbox_id, account.currency) == (record.user_id, record.sandbox_id, "CNY")
            results.append({"account_id": account_id, "migration_id": record.id, "expected_minor": expected,
                "actual_minor": account.balance_minor if owned else None, "ok": owned and account.balance_minor == expected})
    return {"ok": all(item["ok"] for item in results), "checks": results}
