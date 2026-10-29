"""Owner-checked finite review of the exact sealed life/merchant write plan.

This is read-only display evidence, not a replacement for confirmation, OTP or
the domain's transactional execution checks. No model text supplies the scope.
"""
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .models import TaskNode, utcnow
from .planning import canonical_hash


VERSION = "customer-life-confirmation-v1"
TOOLS = {"MERCHANT_PURCHASE":"merchant.purchase.v1", "OCCASION_SET":"occasion.set.v1",
    "OCCASION_MEMBER_SET":"occasion.member.set.v1",
    "LIFE_PLAN_AUTHORIZE":"life.plan.authorize.v1", "LIFE_PLAN_CANCEL":"life.plan.cancel.v1"}
TITLES = {"MERCHANT_PURCHASE":"核对商品付款", "OCCASION_SET":"核对生日资料",
    "OCCASION_MEMBER_SET":"核对家庭成员资料",
    "LIFE_PLAN_AUTHORIZE":"核对本次生日计划授权", "LIFE_PLAN_CANCEL":"核对撤销生活计划"}


def _deny():
    raise DomainError("LIFE_CONFIRMATION_SCOPE_UNAVAILABLE", "本次确认范围已变化或暂时无法核对，请重新准备；尚未执行。", 409)


def _date(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None: _deny()
    return as_utc(parsed)


def _runtime_binding(session, principal, parent, operation, child):
    from .intelligence import action_signature
    from .permissions import actor_role
    from .runtime_models import RuntimeRecord, PlanSnapshot
    record = session.get(RuntimeRecord, parent.id)
    child_record = session.get(RuntimeRecord, child.id)
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version)) if record else None
    bound = next((row for row in (snapshot.plan_json.get("nodes", []) if snapshot else [])
        if isinstance(row, dict) and row.get("operation_id") == operation.id), None)
    if ((parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or parent.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or not record or record.kind != "PARENT" or not child_record or child_record.kind != "CHILD"
            or child_record.root_task_id != parent.id or child_record.metadata_json.get("operation_id") != operation.id
            or child_record.metadata_json.get("prepared_actor_role") != actor_role(session,parent.sandbox_id,parent.user_id)
            or operation.parent_task_id != parent.id or operation.child_task_id != child.id
            or operation.sandbox_id != parent.sandbox_id or operation.active != 1
            or operation.status != "WAITING_CONFIRMATION" or operation.plan_version != record.plan_version
            or operation.args_json.get("kind") != operation.action_kind
            or canonical_hash(action_signature(operation.args_json)) != operation.fingerprint
            or snapshot is None or snapshot.plan_hash != parent.plan_hash
            or snapshot.plan_json != parent.plan_json or canonical_hash(parent.plan_json) != parent.plan_hash
            or not bound or bound.get("node_id") != operation.operation_key
            or bound.get("kind") != operation.action_kind or bound.get("args") != operation.args_json
            or bound.get("depends_on") != operation.depends_on_json): _deny()


def _payment(instrument):
    if instrument.get("instrument_type") not in {"DEBIT", "CREDIT"} or instrument.get("currency") != "CNY": _deny()
    tail = instrument.get("display_last4")
    if not isinstance(tail, str) or len(tail) != 4 or any(letter not in "0123456789abcdefABCDEF" for letter in tail): _deny()
    return {"instrument_type":instrument["instrument_type"], "display_last4":tail, "currency":"CNY"}


def _items(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 20: _deny()
    output = []
    for item in values:
        if (type(item.get("quantity")) is not int or item["quantity"] < 1
                or type(item.get("unit_price_cap_minor")) is not int or item["unit_price_cap_minor"] <= 0
                or type(item.get("amount_minor")) is not int or not 0 < item["amount_minor"] <= 2**53-1
                or item.get("amount_minor") != item["quantity"] * item["unit_price_cap_minor"]
                or item.get("currency") != "CNY" or not isinstance(item.get("merchant_name"), str)
                or not item["merchant_name"] or not isinstance(item.get("product_name"), str) or not item["product_name"]): _deny()
        output.append({"merchant_name":item["merchant_name"], "product_name":item["product_name"],
            "quantity":item["quantity"], "unit_price_minor":item["unit_price_cap_minor"],
            "amount_minor":item["amount_minor"], "currency":"CNY"})
        if item.get("merchant_category") == "RESTAURANT":
            output[-1]["fulfilment_mode"] = "ON_SITE"
    return output


def _recipient(value, mode):
    if not isinstance(value, dict) or set(value) != {"name", "phone", "address"}: _deny()
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in ("name", "address")): _deny()
    if mode == "DELIVERY" and (not isinstance(value.get("phone"), str) or not value["phone"].strip()): _deny()
    return deepcopy(value)


def customer_life_confirmation(session, principal, child, confirmation, action, *, parent=None, operation=None, now=None):
    """Return only a verified display projection, or reject without any mutation."""
    try:
        require_profile_actor(session, principal)
        now = as_utc(now or utcnow())
        if action not in TOOLS or (parent is None) != (operation is None): _deny()
        if parent is not None:
            if operation.action_kind != action: _deny()
            _runtime_binding(session, principal, parent, operation, child)
        rows = child.plan_json.get("nodes", [])
        row = rows[0] if len(rows) == 1 else {}
        args = row.get("args")
        if ((child.user_id, child.sandbox_id) != (principal.user_id, principal.sandbox_id)
                or child.state not in {"AWAITING_CONFIRMATION", "PARTIAL"} or not isinstance(args, dict)
                or child.plan_json.get("task_id") != child.id or child.plan_json.get("sandbox_id") != child.sandbox_id
                or canonical_hash(child.plan_json) != child.plan_hash or row.get("action_kind") != action
                or row.get("tool_name") != TOOLS[action] or row.get("read_only") is not False
                or row.get("requires_confirmation") is not True or row.get("status") != "WAITING_CONFIRMATION"
                or confirmation is None or confirmation.task_id != child.id or confirmation.user_id != principal.user_id
                or confirmation.sandbox_id != principal.sandbox_id or confirmation.status != "PENDING"
                or confirmation.action_type != TOOLS[action] or confirmation.node_key != row.get("node_id")
                or confirmation.plan_hash != child.plan_hash
                or confirmation.view_hash != canonical_hash({"plan_hash":child.plan_hash,"action":action,"args":args})): _deny()
        node = session.scalar(select(TaskNode).where(TaskNode.task_id == child.id,
            TaskNode.sandbox_id == principal.sandbox_id, TaskNode.node_key == confirmation.node_key))
        if node is None or node.status != "WAITING_CONFIRMATION" or node.tool_name != TOOLS[action]: _deny()
        if as_utc(confirmation.expires_at) <= now:
            # Exact owned, sealed pending scope only. Expiry explains why no
            # confirmation is returned; it never revives its OTP or authority.
            raise DomainError("LIFE_CONFIRMATION_EXPIRED", "本次确认已过期，请修改需求并重新准备；尚未执行。", 409)
        result = {"schema_version":VERSION, "action":action, "title":TITLES[action], "scope_verified":True}
        if action == "MERCHANT_PURCHASE":
            from .merchant_service import quote_products
            from .payment_instruments import validate_instrument_snapshot
            quote = args["quote"]
            if confirmation.target_id != quote["product_id"] or child.result_json.get("preview") != args: _deny()
            fresh_quote = quote_products(session, principal, [{key:quote[key] for key in ("merchant_id","product_id","quantity")}])[0]
            if fresh_quote != quote: _deny()
            instrument = validate_instrument_snapshot(session, principal, args["instrument"], lock=False,
                purpose="MERCHANT", amount_minor=quote["amount_minor"])
            if instrument["instrument_type"] == "CREDIT" and quote["amount_minor"] > instrument["available_credit_minor"]: _deny()
            start, end = _date(args["delivery_start"]), _date(args["delivery_end"])
            if start <= now or end <= start or end-start > timedelta(hours=24) or args["fulfilment_mode"] not in {"DELIVERY","ON_SITE"}: _deny()
            result.update(items=_items([quote]), total_amount_minor=quote["amount_minor"], currency="CNY",
                payment=_payment(instrument), recipient=_recipient(args["recipient"], args["fulfilment_mode"]),
                fulfilment_mode=args["fulfilment_mode"], delivery_start=start.isoformat(), delivery_end=end.isoformat(),
                timezone="Asia/Shanghai", payment_is_delivery=False)
        elif action == "OCCASION_MEMBER_SET":
            from .family_profile_service import prepare_member_values
            fields = prepare_member_values(session, principal, family_member_id=args.get("family_member_id"),
                family_member_new_name=args.get("display_name"), family_relationship=args.get("relationship_label"))
            expected = {**fields, "source_reference": "confirmed-task:" + child.id}
            if (args != expected or child.result_json.get("preview") != expected
                    or confirmation.target_id != args.get("family_member_id")): _deny()
            result.update(family_member_name=fields["display_name"], family_relationship=fields["relationship_label"],
                operation="UPDATE" if fields["family_member_id"] else "CREATE", money_operation=False, account_access_granted=False)
        elif action == "OCCASION_SET":
            from .occasion_models import FamilyOccasion
            from .occasion_service import normalize_occasion_values, resolve_family_member
            member = resolve_family_member(session,principal,family_member_id=args["family_member_id"])
            existing = session.scalar(select(FamilyOccasion).where(FamilyOccasion.sandbox_id == principal.sandbox_id,
                FamilyOccasion.owner_user_id == principal.user_id, FamilyOccasion.family_member_id == member.id))
            if confirmation.target_id != member.id or args["expected_version"] != (existing.version if existing else 0): _deny()
            fields = {key:args[key] for key in ("month","day","calendar","timezone","leap_day_policy",
                "lunar_leap_month","confirmed_solar_date","adult_subject_confirmed","source_reference")}
            if normalize_occasion_values(**fields) != fields or fields["source_reference"] != "confirmed-task:" + child.id: _deny()
            from .family_profile_service import member_display_name
            name = member_display_name(session, member)
            if child.result_json.get("preview") != {**fields,"family_member_name":name}: _deny()
            result.update(family_member_name=name,
                birthday={key:fields[key] for key in ("month","day","calendar","timezone","leap_day_policy","lunar_leap_month","confirmed_solar_date")},
                money_operation=False, account_access_granted=False)
        else:
            from .life_plan_service import owned_plan, public_plan_scope, scope_budget_valid
            from .occasion_service import owned_occasion, occurrence_date
            from .payment_instruments import validate_instrument_snapshot
            plan = owned_plan(session,principal,args["plan_id"])
            scope = plan.scope_json
            if (confirmation.target_id != plan.id or args["expected_plan_version"] != plan.version
                    or canonical_hash(scope) != plan.scope_hash or child.result_json.get("scope") != public_plan_scope(scope)
                    or child.result_json.get("budget_minor") != plan.budget_minor): _deny()
            if action == "LIFE_PLAN_AUTHORIZE":
                from .merchant_service import quote_products
                if plan.status not in {"DRAFT","PAUSED_RECONFIRM"} or args.get("scope_hash") != plan.scope_hash: _deny()
                occasion = owned_occasion(session,principal,plan.occasion_id)
                if (occasion.version != scope["occasion_version"] or occurrence_date(occasion,scope["year"]).isoformat() != scope["birthday"]
                        or now >= _date(scope["order_window_end"])): _deny()
                current = quote_products(session,principal,[{key:item[key] for key in ("merchant_id","product_id","quantity")} for item in scope["items"]])
                if current != scope["items"]: _deny()
                instrument = validate_instrument_snapshot(session,principal,scope["instrument"],lock=False,
                    purpose="LIFE_PLAN",version_policy="IDENTITY_AND_CONTROLS")
                if instrument["instrument_type"] != "DEBIT" or instrument["account_id"] != plan.account_id: _deny()
                if scope.get("replaces"):
                    from .life_plan_service import replacement_source
                    old = replacement_source(session, principal, scope["replaces"])
                    notice = {"old_plan_name": old.name, "replace_only_after_confirmation": True,
                        "release_old_unused_reservation": True, "old_plan_unchanged_during_preparation": True}
                    from .life_reservation_replacement import validate_source, public_adjustment
                    if validate_source(session, principal, scope, now) is not None:
                        notice["reservation_adjustment"] = public_adjustment(scope)
                    if child.result_json.get("replacement_notice") != notice: _deny()
                    result["replacement"] = notice
            else:
                if plan.status == "SUCCEEDED": _deny()
                instrument = scope["instrument"]  # Original source, not permission for a new payment.
            if (scope["currency"] != "CNY" or scope["maximum_occurrences"] != 1 or scope["replacement_allowed"] is not False
                    or instrument.get("instrument_type") != "DEBIT" or not scope_budget_valid(scope)
                    or scope["price_increase_allowed"] is not False or scope["failure_policy"] != "REFUND_UNFULFILLED_AND_RELEASE_UNUSED"
                    or scope["cancellation_policy"] != "REFUND_UNFULFILLED_TO_ORIGINAL_SOURCE"): _deny()
            items = _items(scope["items"])
            if sum(item["amount_minor"] for item in items) > plan.budget_minor: _deny()
            result.update(plan_name=plan.name, family_member_name=scope["family_member_name"], birthday=scope["birthday"],
                budget_minor=plan.budget_minor, total_amount_minor=sum(item["amount_minor"] for item in items), currency="CNY",
                budget_limit_minor=scope.get("budget_limit_minor", 100000), schedule_policy=scope.get("schedule_policy", "MONTH_START_AND_T_MINUS_2"),
                items=items, payment=_payment(instrument), recipient=_recipient(scope["recipient"],"DELIVERY"),
                timezone=scope["timezone"], reserve_at=scope["reserve_at"], reserve_policy=scope.get("reserve_policy"), order_at=scope["order_at"],
                order_window_end=scope["order_window_end"], delivery_start=scope["delivery_start"], delivery_end=scope["delivery_end"],
                expires_at=scope["expires_at"], maximum_occurrences=1, cash_reservation_is_card_lock=False,
                change_requires_new_confirmation=True, failure_policy=scope["failure_policy"],
                cancellation_policy=scope["cancellation_policy"], payment_is_delivery=False)
        return result
    except DomainError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        _deny()
