"""One durable local plan node per caller transaction, no network or sleeps.

The worker commits each call separately. A restart after commit finds the next
node; a crash before commit rolls back that node only. Earlier paid orders are
never described as rolled back when a later order fails.
"""
from datetime import timedelta

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .credit_atomic import atomic_domain
from .life_plan_models import LifePlan, LifePlanJob, LifePlanMandate, LifePlanOccurrence
from .life_plan_service import (_principal, execution_context, owned_plan, queue_job,
    reserve_plan_cash)
from .merchant_models import MerchantOrder, MerchantRefund
from .merchant_service import (complete_order_refund, fulfil_virtual_order, place_authorized_order,
    quote_products, request_order_refund)
from .models import utcnow


def _release_unused(session, principal, plan, *, operation):
    from .payment_instruments import release_cash_reservation
    if plan.reservation_id and plan.reserved_minor:
        release_cash_reservation(session, principal, plan.reservation_id, operation_key=operation)
        plan.reserved_minor = 0


def _one_node(session, principal, plan, occurrence, job, now):
    if job.kind == "RESERVE":
        return reserve_plan_cash(session, principal, occurrence.id, now=now)
    if job.kind == "ORDER":
        if job.item_index:
            previous = session.scalar(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id,
                LifePlanJob.kind == "ORDER", LifePlanJob.item_index == job.item_index - 1,
                LifePlanJob.plan_version == job.plan_version, LifePlanJob.status == "SUCCEEDED"))
            if previous is None:
                job.next_run_at = now + timedelta(seconds=1)
                return None
        context = execution_context(session, principal, occurrence.id, now=now, for_order=True)
        if job.item_index == 0:
            # All initial choices must still be possible before the first debit.
            items = context["mandate"].scope_json["items"]
            quotes = quote_products(session, principal, [{k: row[k] for k in ("merchant_id", "product_id", "quantity")} for row in items])
            if any(q["unit_price_cap_minor"] > item["unit_price_cap_minor"] for q, item in zip(quotes, items)):
                raise DomainError("LIFE_PLAN_PRICE_OR_TERMS_CHANGED", "指定商品超价，计划暂停等待本人重新确认。", 409)
        result = place_authorized_order(session, principal, occurrence.id, job.item_index, now=now)
        queue_job(session, occurrence, "FULFIL", context["delivery_start"], item_index=job.item_index,
            version=plan.version, result={"order_id": result["order_id"]})
        occurrence.status = "WAITING_DELIVERY"
        queue_job(session, occurrence, "EXPIRE", context["delivery_end"] + timedelta(seconds=1), version=plan.version)
        return result
    if job.kind == "FULFIL":
        order = session.get(MerchantOrder, job.result_json.get("order_id"))
        if order is None or order.occurrence_id != occurrence.id:
            raise DomainError("LIFE_PLAN_JOB_ORDER_CHANGED", "履约节点不属于本次计划订单。", 409)
        result = fulfil_virtual_order(session, principal, job.result_json["order_id"], now=now)
        session.flush()
        orders = session.scalars(select(MerchantOrder).where(MerchantOrder.occurrence_id == occurrence.id)).all()
        expected = len(plan.scope_json["items"])
        if len(orders) == expected and all(row.status == "FULFILLED" for row in orders):
            _release_unused(session, principal, plan, operation=f"life:{occurrence.id}:completed-release")
            plan.status, occurrence.status = "SUCCEEDED", "SUCCEEDED"
            session.get(LifePlanMandate, occurrence.mandate_id).status = "COMPLETED"
        return result
    if job.kind == "REFUND":
        order = session.get(MerchantOrder, job.result_json.get("order_id"))
        if order is None or order.occurrence_id != occurrence.id:
            raise DomainError("LIFE_PLAN_JOB_ORDER_CHANGED", "退款节点不属于本次计划订单。", 409)
        refund = request_order_refund(session, principal, job.result_json["order_id"], now=now)
        receipt = complete_order_refund(session, principal, refund.id, now=now)
        _release_unused(session, principal, plan, operation=f"life:{occurrence.id}:compensation-release")
        session.flush()
        from .life_compensation import finish_compensation
        finish_compensation(session, principal, plan, occurrence, now=now, current_job_id=job.id)
        return receipt
    if job.kind == "EXPIRE":
        if plan.status in {"SUCCEEDED", "CANCELLED", "COMPENSATED"}:
            mandate = session.get(LifePlanMandate, occurrence.mandate_id)
            if mandate and mandate.status == 'ACTIVE' and now >= as_utc(mandate.expires_at):
                mandate.status = 'EXPIRED'
            return {'status': plan.status, 'delivery_verified': plan.status == 'SUCCEEDED', 'terminal_no_effect': True}
        if plan.status != "SUCCEEDED":
            orders = session.scalars(select(MerchantOrder).where(MerchantOrder.occurrence_id == occurrence.id)).all()
            plan.status = "PARTIAL" if orders else "PAUSED_RECONFIRM"
            occurrence.status = plan.status
            plan.pause_reason = "LIFE_PLAN_FULFILMENT_NOT_VERIFIED"
            _release_unused(session, principal, plan, operation=f"life:{occurrence.id}:expired-release")
            mandate = session.get(LifePlanMandate, occurrence.mandate_id)
            if mandate and mandate.status == "ACTIVE":
                mandate.status = "EXPIRED"
        return {"status": plan.status, "delivery_verified": plan.status == "SUCCEEDED"}
    raise DomainError("LIFE_PLAN_JOB_UNSUPPORTED", "未知计划节点，不执行任何替代动作。", 422)


@atomic_domain
def run_one_due_life_plan_job(session, *, now=None):
    """Caller commits. User -> plan -> occurrence/job serializes cancellation.

    Payment adapter then owns account/card/reservation lock ordering. No lease
    is needed for local-only transactional commerce: SKIP LOCKED + commit gives
    durable at-most-once effects with replay-safe reservation/payment keys.
    """
    now = as_utc(now or utcnow())
    candidates = session.execute(select(LifePlanJob.id, LifePlanOccurrence.plan_id).join(
        LifePlanOccurrence, LifePlanJob.occurrence_id == LifePlanOccurrence.id).where(
        LifePlanJob.status == "QUEUED", LifePlanJob.next_run_at <= now).order_by(
        LifePlanJob.next_run_at, LifePlanJob.item_index, LifePlanJob.id).limit(50)).all()
    for job_id, plan_id in candidates:
        from .banking_locks import lock_financial_users
        hint = session.get(LifePlan, plan_id)
        if hint is None:
            continue
        lock_financial_users(session, {(hint.sandbox_id, hint.user_id)})
        plan = session.scalar(select(LifePlan).where(LifePlan.id == plan_id).with_for_update(skip_locked=True)
            .execution_options(populate_existing=True))
        if plan is None:
            continue
        job = session.scalar(select(LifePlanJob).where(LifePlanJob.id == job_id).with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if job is None or job.status != "QUEUED":
            continue
        if job.plan_version != plan.version:
            job.status, job.completed_at = "CANCELLED", now
            return 1
        if job.kind not in {"REFUND", "EXPIRE"} and plan.status != "ACTIVE":
            continue
        if job.kind == "REFUND" and plan.status not in {"COMPENSATING", "CANCELLING", "PARTIAL"}:
            continue
        occurrence = session.scalar(select(LifePlanOccurrence).where(LifePlanOccurrence.id == job.occurrence_id).with_for_update().execution_options(populate_existing=True))
        principal = _principal(plan)
        try:
            with session.begin_nested():
                result = _one_node(session, principal, plan, occurrence, job, now)
                if result is None:
                    return 0
                job.status, job.result_json, job.completed_at = "SUCCEEDED", result, now
                job.attempts += 1
            return 1
        except DomainError as error:
            # Savepoint removes only this node's half-written local effects.
            # Previous nodes from earlier worker transactions remain real.
            job = session.get(LifePlanJob, job_id)
            plan = session.get(LifePlan, plan_id)
            occurrence = session.get(LifePlanOccurrence, job.occurrence_id)
            job.status, job.last_error, job.completed_at = "FAILED", error.code, now
            job.attempts += 1
            for pending in session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id,
                    LifePlanJob.status == "QUEUED", LifePlanJob.kind.notin_(["REFUND", "EXPIRE"]))):
                pending.status = "BLOCKED_RECONFIRM"
            paid = session.scalar(select(MerchantOrder.id).where(MerchantOrder.occurrence_id == occurrence.id,
                MerchantOrder.status.in_(["PAID", "FULFILLED", "REFUND_PENDING"])))
            plan.status = "PARTIAL" if paid else "PAUSED_RECONFIRM"
            occurrence.status, plan.pause_reason = plan.status, error.code
            if paid and plan.scope_json.get("failure_policy") == "REFUND_UNFULFILLED_AND_RELEASE_UNUSED":
                for index, order in enumerate(session.scalars(select(MerchantOrder).where(
                        MerchantOrder.occurrence_id == occurrence.id, MerchantOrder.status.in_(["PAID", "REFUND_PENDING"])))):
                    queue_job(session, occurrence, "REFUND", now, item_index=index, version=plan.version,
                        result={"order_id": order.id})
            return 1
    return 0


def run_due_life_plans(session, *, now=None):
    """Worker integration alias. Exactly one transaction-sized node per call."""
    return run_one_due_life_plan_job(session, now=now)
