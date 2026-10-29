"""Actual completed domain effects for narration/phase audits, never permission."""
from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .life_plan_service import _verified_original_authorization, owned_plan
from .models import Confirmation, Task, ToolCall
from .planning import canonical_hash


TOOLS = {"OCCASION_SET": "occasion.set.v1", "LIFE_PLAN_AUTHORIZE": "life.plan.authorize.v1",
    "OCCASION_MEMBER_SET": "occasion.member.set.v1",
    "LIFE_PLAN_CANCEL": "life.plan.cancel.v1", "MERCHANT_PURCHASE": "merchant.purchase.v1"}


def completed_life_write_receipt_integrity(session, principal, child, kind=None):
    """Boolean on a real Task; validates the immutable original receipt.

    Authorization is not fulfilment. A later revoke, refund, or completed
    delivery does not rewrite or invalidate the original successful effect.
    Caller still checks parent/operation ownership and active authorization.
    """
    try:
        require_profile_actor(session, principal)
        child = session.get(Task, child) if isinstance(child, str) else child
        if child is None or (child.user_id, child.sandbox_id) != (principal.user_id, principal.sandbox_id) or child.state != "SUCCEEDED":
            return False
        nodes = child.plan_json.get("nodes", [])
        if len(nodes) != 1 or canonical_hash(child.plan_json) != child.plan_hash:
            return False
        node = nodes[0]
        kind = kind or node.get("action_kind")
        if kind not in TOOLS or node.get("action_kind") != kind or node.get("tool_name") != TOOLS[kind]:
            return False
        args = node.get("args") or {}
        calls = session.scalars(select(ToolCall).where(ToolCall.task_id == child.id,
            ToolCall.sandbox_id == principal.sandbox_id, ToolCall.tool_name == TOOLS[kind], ToolCall.status == "SUCCESS")).all()
        confirmations = session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
            Confirmation.sandbox_id == principal.sandbox_id, Confirmation.user_id == principal.user_id,
            Confirmation.action_type == TOOLS[kind], Confirmation.node_key == node.get("node_id"),
            Confirmation.status == "CONSUMED")).all()
        if len(calls) != 1 or len(confirmations) != 1 or calls[0].result_json != child.result_json:
            return False
        call, confirmation, result = calls[0], confirmations[0], calls[0].result_json
        if (confirmation.consumed_at is None or confirmation.plan_hash != child.plan_hash
                or confirmation.view_hash != canonical_hash({"plan_hash": child.plan_hash, "action": kind, "args": args})
                or call.request_hash not in {canonical_hash({"task_id": child.id, "confirmation_id": confirmation.id, "plan_hash": child.plan_hash}),
                    canonical_hash({"plan_hash": child.plan_hash, "operation_key": call.idempotency_key})}):
            return False
        if kind == "OCCASION_MEMBER_SET":
            from .extensions_models import FamilyMember
            from .family_profile_service import member_label
            rows = result.get("members", [])
            original = rows[0] if len(rows) == 1 else {}
            member = session.get(FamilyMember, original.get("family_member_id"))
            if member is None or (member.owner_user_id, member.sandbox_id) != (principal.user_id, principal.sandbox_id):
                return False
            label = member_label(session, member)
            return (confirmation.target_id == args.get("family_member_id")
                and (args.get("family_member_id") is None or args["family_member_id"] == member.id)
                and result.get("schema_version") == "family-profile-v1" and result.get("bank_state_changed") is False
                and result.get("family_profile_state_changed") is True and result.get("account_access_granted") is False
                and original.get("family_member_name") == args.get("display_name")
                and original.get("family_relationship") == args.get("relationship_label")
                and original.get("version") == args["expected_version"] + 1
                and label is not None and label.version >= original["version"])
        if kind == "OCCASION_SET":
            from .extensions_models import FamilyMember
            from .occasion_models import FamilyOccasion
            member = session.get(FamilyMember, args.get("family_member_id"))
            rows = result.get("occasions", [])
            if (member is None or (member.owner_user_id, member.sandbox_id) != (principal.user_id, principal.sandbox_id)
                    or confirmation.target_id != member.id or len(rows) != 1):
                return False
            original = rows[0]
            current = session.get(FamilyOccasion, original.get("occasion_id"))
            fields = ("family_member_id", "month", "day", "calendar", "timezone", "leap_day_policy",
                "lunar_leap_month", "confirmed_solar_date", "adult_subject_confirmed")
            return (current is not None and (current.owner_user_id, current.sandbox_id) == (principal.user_id, principal.sandbox_id)
                and current.family_member_id == member.id and type(original.get("version")) is int
                and original["version"] == args["expected_version"] + 1 and current.version >= original["version"]
                and all(original.get(field) == args.get(field) for field in fields)
                and original.get("source") == "USER_CONFIRMED" and result.get("occasion_state_changed") is True)
        if kind == "LIFE_PLAN_AUTHORIZE":
            from .life_plan_models import LifePlanMandate
            plan = owned_plan(session, principal, args["plan_id"])
            mandate = session.scalar(select(LifePlanMandate).where(LifePlanMandate.confirmation_id == confirmation.id,
                LifePlanMandate.task_id == child.id, LifePlanMandate.plan_id == plan.id))
            if mandate is None or mandate.plan_version != args["expected_plan_version"] or mandate.scope_hash != args["scope_hash"]:
                return False
            _verified_original_authorization(session, principal, plan, mandate, confirmation, child)
            from .life_reservation_replacement import receipt_integrity
            if not receipt_integrity(session, principal, plan, mandate, result):
                return False
            if mandate.scope_json.get("replaces"):
                source = mandate.scope_json["replaces"]
                replaced = result.get("replaced_plan", {})
                old = owned_plan(session, principal, source["plan_id"])
                if (replaced.get("plan_id") != source["plan_id"] or replaced.get("version") != source["version"]
                        or replaced.get("scope_hash") != source["scope_hash"] or replaced.get("status") != "CANCELLED"
                        or replaced.get("reserved_minor") != 0 or replaced.get("spent_minor") != 0
                        or old.version != source["version"] or old.scope_hash != source["scope_hash"]
                        or old.status != "CANCELLED" or old.reserved_minor != 0):
                    return False
            return (confirmation.target_id == plan.id and mandate.scope_json.get("maximum_occurrences") == 1
                and mandate.scope_json.get("recurring") is False and result.get("cash_debit_minor") == 0
                and as_utc(mandate.expires_at).isoformat() == mandate.scope_json["expires_at"])
        if kind == "LIFE_PLAN_CANCEL":
            from .life_plan_models import LifePlanMandate
            from .credit_models import PaymentCashReservation
            plan = owned_plan(session, principal, args["plan_id"])
            rows = result.get("plans", [])
            if (confirmation.target_id != plan.id or len(rows) != 1 or rows[0].get("plan_id") != plan.id
                    or rows[0].get("status") not in {"CANCELLED", "COMPENSATING", "COMPENSATED", "PARTIAL"}
                    or plan.status not in {"CANCELLED", "COMPENSATING", "COMPENSATED", "PARTIAL", "PAUSED_RECONFIRM"}):
                return False
            if session.scalar(select(LifePlanMandate.id).where(LifePlanMandate.plan_id == plan.id,
                    LifePlanMandate.status == "ACTIVE")):
                return False
            if plan.reservation_id:
                reservation = session.get(PaymentCashReservation, plan.reservation_id)
                if reservation is None or reservation.remaining_minor != 0 or (reservation.user_id, reservation.sandbox_id) != (principal.user_id, principal.sandbox_id):
                    return False
            return result.get("reserved_cash_released_minor", 0) >= 0
        from .merchant_models import MerchantOrder
        from .merchant_runtime import verified_single_order_source
        orders = session.scalars(select(MerchantOrder).where(MerchantOrder.source_task_id == child.id,
            MerchantOrder.user_id == principal.user_id, MerchantOrder.sandbox_id == principal.sandbox_id)).all()
        if len(orders) != 1:
            return False
        verified_single_order_source(session, principal, orders[0])
        original = result.get("orders", [])
        return (len(original) == 1 and original[0].get("order_id") == orders[0].id
            and original[0].get("status") == "PAID" and result.get("payment") == orders[0].payment_receipt_json)
    except (DomainError, TypeError, ValueError, KeyError, AttributeError, IndexError):
        return False
