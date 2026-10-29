"""Close only fully verified original-source compensation, without money writes."""
from sqlalchemy import select
from .authentication import as_utc
from .planning import canonical_hash


def finish_compensation(session, principal, plan, occurrence, *, now, current_job_id=None):
    from .contracts import DomainError
    from .credit_models import PaymentCashReservation, PaymentJournal, PaymentLedgerEntry, PaymentRecord, PaymentRefund
    from .life_plan_models import LifePlanJob, LifePlanMandate
    from .life_plan_service import refund_context
    from .merchant_models import MerchantOrder, MerchantRefund
    from .merchant_service import _verified_life_order, verified_order_payment
    if plan.status not in {'PARTIAL', 'COMPENSATING', 'CANCELLING'} or plan.reserved_minor != 0:
        return False
    try:
        context = refund_context(session, principal, occurrence.id, now=now, audit_completed=True)
        mandate = context['mandate']
        if occurrence.plan_id != plan.id or mandate.plan_id != plan.id or mandate.plan_version != plan.version \
                or plan.scope_hash != mandate.scope_hash or plan.scope_json != mandate.scope_json:
            return False
        if mandate.status not in {'ACTIVE', 'REVOKED', 'EXPIRED', 'COMPLETED'}:
            return False
        active = list(session.scalars(select(LifePlanMandate).where(LifePlanMandate.plan_id == plan.id,
            LifePlanMandate.status == 'ACTIVE').with_for_update().execution_options(populate_existing=True)))
        if any(row.id != mandate.id for row in active): return False
        reservation = session.scalar(select(PaymentCashReservation).where(PaymentCashReservation.id == plan.reservation_id)
            .with_for_update().execution_options(populate_existing=True)) if plan.reservation_id else None
        if reservation is None or (reservation.user_id, reservation.sandbox_id, reservation.account_id) != (
                principal.user_id, principal.sandbox_id, plan.account_id) or reservation.remaining_minor != 0 \
                or reservation.status not in {'RELEASED', 'CONSUMED'} \
                or reservation.owner_ref != f'life:{occurrence.id}' \
                or reservation.reservation_key != f'life:{occurrence.id}:v{plan.version}:reserve' \
                or reservation.instrument_id != mandate.scope_json['instrument']['instrument_id'] \
                or reservation.amount_minor != plan.budget_minor:
            return False
        orders = list(session.scalars(select(MerchantOrder).where(MerchantOrder.occurrence_id == occurrence.id)
            .order_by(MerchantOrder.id).with_for_update().execution_options(populate_existing=True)))
        if not orders or any(row.status != 'REFUNDED' for row in orders): return False
        payments = []
        for order in orders:
            if (order.user_id, order.sandbox_id) != (principal.user_id, principal.sandbox_id): return False
            _verified_life_order(order, context)
            payment = verified_order_payment(session, principal, order)
            if payment.status != 'REFUNDED' or payment.instrument_type != 'DEBIT' or payment.account_id != plan.account_id \
                    or payment.reservation_id != reservation.id or payment.currency != 'CNY' \
                    or payment.instrument_id != mandate.scope_json['instrument']['instrument_id']:
                return False
            refund = session.scalar(select(MerchantRefund).where(MerchantRefund.order_id == order.id))
            central = session.scalar(select(PaymentRefund).where(PaymentRefund.payment_id == payment.id))
            if refund is None or central is None or refund.status != 'POSTED' or refund.completed_at is None \
                    or refund.amount_minor != order.amount_minor or refund.operation_key != f'life-refund:{order.id}' \
                    or (refund.user_id, refund.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                    or (central.user_id, central.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                    or central.operation_key != refund.operation_key \
                    or central.request_hash != canonical_hash({'payment_id': payment.id, 'authorization_ref': mandate.id}):
                return False
            receipt = refund.receipt_json
            # A replay marker is not a second refund. The persisted central
            # receipt and every financial/identity field must still be equal.
            if not isinstance(receipt, dict) or not isinstance(central.receipt_json, dict) \
                    or type(receipt.get('replayed')) is not bool or type(central.receipt_json.get('replayed')) is not bool \
                    or {key:value for key,value in receipt.items() if key != 'replayed'} != \
                       {key:value for key,value in central.receipt_json.items() if key != 'replayed'}:
                return False
            expected = {'schema_version':'payment-refund-receipt-v1', 'status':'POSTED', 'refund_id':central.id,
                'payment_id':payment.id, 'amount_minor':order.amount_minor, 'currency':'CNY', 'account_id':payment.account_id,
                'instrument_id':payment.instrument_id, 'instrument_type':'DEBIT', 'reservation_id':payment.reservation_id,
                'operation_key':central.operation_key, 'authorization_ref':mandate.id, 'cash_credit_minor':order.amount_minor,
                'credit_reduction_minor':0, 'ledger_balanced':True}
            if not isinstance(receipt,dict) or any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value for key,value in expected.items()):
                return False
            journal = session.scalar(select(PaymentJournal).where(PaymentJournal.user_id == principal.user_id,
                PaymentJournal.sandbox_id == principal.sandbox_id, PaymentJournal.operation_key == central.operation_key))
            if journal is None or journal.kind != 'MERCHANT_REFUND' or journal.request_hash != central.request_hash \
                    or journal.result_json != central.receipt_json: return False
            entries = list(session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == journal.id)))
            expected_legs = {(payment.account_id, order.amount_minor), ('MERCHANT:' + order.merchant_id, -order.amount_minor)}
            if len(entries) != 2 or {(row.account_ref,row.delta_minor) for row in entries} != expected_legs \
                    or any((row.user_id,row.sandbox_id,row.currency) != (principal.user_id,principal.sandbox_id,'CNY') for row in entries):
                return False
            payments.append(payment.id)
        registered = list(session.scalars(select(PaymentRecord).where(PaymentRecord.user_id == principal.user_id,
            PaymentRecord.sandbox_id == principal.sandbox_id, PaymentRecord.authorization_ref == mandate.id)))
        if {row.id for row in registered} != set(payments) or sum(row.amount_minor for row in orders) != plan.spent_minor:
            return False
        jobs = list(session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id)
            .with_for_update().execution_options(populate_existing=True)))
        pending = [row for row in jobs if row.id != current_job_id and row.status in {'QUEUED','BLOCKED_RECONFIRM'}]
        if any(row.status not in {'QUEUED','BLOCKED_RECONFIRM','SUCCEEDED','FAILED','CANCELLED'}
                or row.plan_version != plan.version for row in jobs) \
                or any(row.kind not in {'RESERVE','ORDER','FULFIL','REFUND','EXPIRE'} for row in pending):
            return False
    except (DomainError, KeyError, TypeError, ValueError):
        return False
    for job in pending:
        job.status, job.completed_at = 'CANCELLED', as_utc(now)
    if mandate.status == 'ACTIVE':
        mandate.status, mandate.revoked_at = 'REVOKED', as_utc(now)
    plan.status, occurrence.status = 'COMPENSATED', 'COMPENSATED'
    return True
