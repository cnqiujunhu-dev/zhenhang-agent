from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import BillAnalysis
from .models import Transaction


SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
MOVEMENT_SOURCES = frozenset({"TRANSFER", "INVESTMENT", "AA_COLLECTION", "DEV_TEST_ADJUSTMENT", "FX", "OWN_ACCOUNT_TRANSFER"})


def is_movement(transaction: Transaction) -> bool:
    return transaction.source_kind in MOVEMENT_SOURCES


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def is_refund(transaction: Transaction) -> bool:
    # Transaction descriptions are untrusted evidence. Classification uses the
    # bank-provided category and never executes instructions found in a merchant.
    return not is_movement(transaction) and transaction.amount_minor > 0 and any(term in transaction.category for term in ("退款", "冲正"))


def load_bill_transactions(
    session: Session, sandbox_id: str, account_id: str,
    *, start: datetime | None = None, end: datetime | None = None,
    business_time: datetime | None = None,
) -> list[Transaction]:
    if (start is None) != (end is None):
        raise ValueError("REPORT_REQUIRES_BOTH_BOUNDARIES")
    query = select(Transaction).where(
        Transaction.sandbox_id == sandbox_id, Transaction.account_id == account_id,
    )
    if start is not None:
        if as_utc(start) >= as_utc(end):
            raise ValueError("REPORT_BOUNDARIES_MUST_INCREASE")
        query = query.where(Transaction.occurred_at >= as_utc(start), Transaction.occurred_at < as_utc(end))
    if business_time is not None:
        query = query.where(Transaction.occurred_at <= as_utc(business_time))
    rows = list(session.scalars(query.order_by(Transaction.occurred_at.desc(), Transaction.id)))
    from .billing_evidence import verify_owned_cash_movement_row
    for row in rows:
        verify_owned_cash_movement_row(session, row)
    return rows


def summarize_transactions(
    transactions: list[Transaction], *, start: datetime | None = None, end: datetime | None = None,
) -> BillAnalysis:
    spending = 0
    refunds = 0
    income = 0
    movement_in = 0
    movement_out = 0
    categories: dict[str, int] = {}
    income_categories: dict[str, int] = {}
    monthly: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    anomalies: list[dict] = []
    for transaction in transactions:
        refund = is_refund(transaction)
        movement = is_movement(transaction)
        if movement:
            if transaction.amount_minor > 0:
                movement_in += transaction.amount_minor
            else:
                movement_out -= transaction.amount_minor
        elif transaction.amount_minor < 0:
            amount = -transaction.amount_minor
            spending += amount
            categories[transaction.category] = categories.get(transaction.category, 0) + amount
        elif refund:
            refunds += transaction.amount_minor
        else:
            income += transaction.amount_minor
            income_categories[transaction.category] = income_categories.get(transaction.category, 0) + transaction.amount_minor
        local_time = as_utc(transaction.occurred_at).astimezone(SHANGHAI)
        for summaries, label, key in [(monthly, "month", local_time.strftime("%Y-%m")),
                                      (daily, "day", local_time.strftime("%Y-%m-%d"))]:
            summary = summaries.setdefault(key, {label: key, "transaction_count": 0,
                "income_minor": 0, "refund_minor": 0, "spending_minor": 0, "net_minor": 0,
                "movement_in_minor": 0, "movement_out_minor": 0})
            summary["transaction_count"] += 1
            summary["net_minor"] += transaction.amount_minor
            if movement:
                if transaction.amount_minor > 0:
                    summary["movement_in_minor"] += transaction.amount_minor
                else:
                    summary["movement_out_minor"] -= transaction.amount_minor
            elif transaction.amount_minor < 0:
                summary["spending_minor"] -= transaction.amount_minor
            elif refund:
                summary["refund_minor"] += transaction.amount_minor
            else:
                summary["income_minor"] += transaction.amount_minor
        if transaction.flag == "ANOMALY" and not movement:
            anomalies.append(
                {
                    "transaction_id": transaction.id,
                    "card_id": transaction.card_id,
                    "amount_minor": transaction.amount_minor,
                    "merchant": transaction.merchant,
                    "reason": "账务记录中标记为待核验的交易",
                }
            )
    return BillAnalysis(
        transaction_count=len(transactions),
        spending_minor=spending,
        refund_minor=refunds,
        category_spending_minor=categories,
        income_minor=income,
        net_minor=income + refunds - spending + movement_in - movement_out,
        movement_in_minor=movement_in,
        movement_out_minor=movement_out,
        category_income_minor=income_categories,
        monthly=[monthly[key] for key in sorted(monthly)],
        daily=[daily[key] for key in sorted(daily)],
        period_start=as_utc(start).isoformat() if start is not None else None,
        period_end=as_utc(end).isoformat() if end is not None else None,
        anomalies=anomalies,
        explanation="金额由账务流水逐笔汇总；转账、申赎和AA回款单列资金搬移，净额=收入+退款-消费+搬移流入-搬移流出。待核验标记仅提示核验，不自动采取行动。",
    )


def analyze_bills(
    session: Session, sandbox_id: str, account_id: str,
    *, start: datetime | None = None, end: datetime | None = None,
    business_time: datetime | None = None,
) -> BillAnalysis:
    rows = load_bill_transactions(session, sandbox_id, account_id,
                                  start=start, end=end, business_time=business_time)
    return summarize_transactions(rows, start=start, end=end)

