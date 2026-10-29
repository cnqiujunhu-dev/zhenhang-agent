"""Read-only explanations of recorded worker stops, never payment authority."""
from sqlalchemy import select

from .contracts import DomainError
from .life_plan_models import LifePlanJob, LifePlanOccurrence
from .merchant_models import MerchantOrder, MerchantRefund


REASONS = {
    "LIFE_PLAN_PRICE_OR_TERMS_CHANGED": "PRICE_CHANGED",
    "MERCHANT_STOCK_INSUFFICIENT": "ITEM_UNAVAILABLE",
    "MERCHANT_PRODUCT_UNAVAILABLE": "ITEM_UNAVAILABLE",
    "LIFE_PLAN_AUTHORIZATION_NOT_ACTIVE": "AUTHORIZATION_CHANGED",
    "LIFE_PLAN_SCOPE_CHANGED": "AUTHORIZATION_CHANGED",
    "LIFE_PLAN_OCCASION_CHANGED": "AUTHORIZATION_CHANGED",
    "LIFE_PLAN_PAYMENT_SCOPE_CHANGED": "PAYMENT_CONTROL_CHANGED",
    "LIFE_PLAN_RESERVATION_CHANGED": "PAYMENT_CONTROL_CHANGED",
    "LIFE_PLAN_BUDGET_EXCEEDED": "BUDGET_EXCEEDED",
    "LIFE_PLAN_FULFILMENT_NOT_VERIFIED": "FULFILMENT_UNVERIFIED",
}


def execution_review(session, principal, plan):
    """Require an actual current-version stop and reconcile payment evidence.

    Scope changes are not repaired here. An unavailable source yields no new
    reassurance. The ordinary plan projection remains available for recovery.
    """
    if plan.status not in {"PAUSED_RECONFIRM", "PARTIAL", "COMPENSATING"} or not plan.pause_reason:
        return None
    jobs = session.scalars(select(LifePlanJob).join(LifePlanOccurrence,
        LifePlanJob.occurrence_id == LifePlanOccurrence.id).where(
        LifePlanOccurrence.plan_id == plan.id, LifePlanJob.plan_version == plan.version)).all()
    if not any((job.status == "FAILED" and job.last_error == plan.pause_reason) or
            (job.kind == "EXPIRE" and job.status == "SUCCEEDED" and
             plan.pause_reason == "LIFE_PLAN_FULFILMENT_NOT_VERIFIED") for job in jobs):
        return None
    from .merchant_service import verified_order_payment
    orders = session.scalars(select(MerchantOrder).join(LifePlanOccurrence,
        MerchantOrder.occurrence_id == LifePlanOccurrence.id).where(
        LifePlanOccurrence.plan_id == plan.id, MerchantOrder.user_id == principal.user_id,
        MerchantOrder.sandbox_id == principal.sandbox_id)).all()
    try:
        payments = [verified_order_payment(session, principal, order) for order in orders]
    except DomainError:
        return None
    if len({payment.id for payment in payments}) != len(payments):
        return None
    paid = sum(payment.amount_minor for payment in payments)
    refunds = session.scalars(select(MerchantRefund).join(MerchantOrder,
        MerchantRefund.order_id == MerchantOrder.id).join(LifePlanOccurrence,
        MerchantOrder.occurrence_id == LifePlanOccurrence.id).where(
        LifePlanOccurrence.plan_id == plan.id, MerchantRefund.status == "POSTED",
        MerchantRefund.user_id == principal.user_id, MerchantRefund.sandbox_id == principal.sandbox_id)).all()
    # Refund receipt verification is owned by the normal refund service. Do not
    # turn a standalone refund status or damaged amount into a credited claim.
    from .credit_models import PaymentRefund, PaymentJournal, PaymentLedgerEntry, PaymentCashReservation
    refunded = 0
    for refund in refunds:
        receipt = refund.receipt_json or {}
        bank = session.get(PaymentRefund, receipt.get("refund_id"))
        if (bank is None or (bank.user_id, bank.sandbox_id) != (principal.user_id, principal.sandbox_id)
                or bank.receipt_json != receipt or receipt.get("status") != "POSTED"
                or receipt.get("amount_minor") != refund.amount_minor or receipt.get("ledger_balanced") is not True):
            return None
        if bank.payment_id not in {payment.id for payment in payments}:
            return None
        payment = next(payment for payment in payments if payment.id == bank.payment_id)
        journal = session.scalar(select(PaymentJournal).where(PaymentJournal.user_id == principal.user_id,
            PaymentJournal.sandbox_id == principal.sandbox_id, PaymentJournal.operation_key == bank.operation_key))
        entries = session.scalars(select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.journal_id == journal.id)).all() if journal else []
        if (journal is None or journal.result_json != receipt or journal.request_hash != bank.request_hash
                or len(entries) != 2 or sum(entry.delta_minor for entry in entries) != 0
                or sum(entry.delta_minor for entry in entries if entry.account_ref == payment.account_id) != refund.amount_minor
                or sum(entry.delta_minor for entry in entries if entry.account_ref == "MERCHANT:" + payment.merchant_ref) != -refund.amount_minor):
            return None
        refunded += refund.amount_minor
    if plan.reservation_id:
        hold = session.get(PaymentCashReservation, plan.reservation_id)
        if (hold is None or (hold.user_id, hold.sandbox_id, hold.account_id) !=
                (principal.user_id, principal.sandbox_id, plan.account_id) or hold.remaining_minor != plan.reserved_minor):
            return None
    elif plan.reserved_minor:
        return None
    if (type(plan.spent_minor) is not int or paid != plan.spent_minor or refunded > paid
            or type(plan.reserved_minor) is not int or plan.reserved_minor < 0):
        return None
    return {"schema_version": "life-execution-review-v1", "plan_version": plan.version,
        "plan_status": plan.status, "reason": REASONS.get(plan.pause_reason, "REVIEW_REQUIRED"),
        "payment_count": len(payments), "paid_minor": paid, "refunded_minor": refunded,
        "reserved_minor": plan.reserved_minor, "source": "LOCAL_BANK_AND_VIRTUAL_MERCHANT"}
