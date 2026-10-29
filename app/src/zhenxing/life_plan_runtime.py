"""Normal task preparation/confirmation adapter; shared router owns registration."""
from datetime import timedelta

from sqlalchemy import select

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError
from .life_plan_service import (AUTH_TOOL, authorization_args, authorize_life_plan, cancel_life_plan,
    life_plan_view, owned_plan, public_plan_scope)
from .models import Confirmation, OutboxMessage, TaskNode, ToolCall, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .workflow import finish_node, transition_task


ACTION_TOOLS = {"LIFE_PLAN_VIEW": "life.plan.view.v1", "LIFE_PLAN_AUTHORIZE": AUTH_TOOL,
    "LIFE_PLAN_CANCEL": "life.plan.cancel.v1"}
TOOL_DEFINITIONS = {tool: {"read_only": kind == "LIFE_PLAN_VIEW", "risk_level": "L1" if kind == "LIFE_PLAN_VIEW" else "L3",
    "owner": "life_plans"} for kind, tool in ACTION_TOOLS.items()}


def _principal(task):
    from .services import Principal
    return Principal(task.user_id, task.sandbox_id)


def _scope_change_requested(session, principal, plan, slots):
    """Do not silently discard a newly supplied bound on an existing plan.

    Repeating an exact existing identity/budget/recipient is harmless. A new
    bound requires a separately prepared scope; this check never releases a
    reservation or amends a mandate while merely preparing its confirmation.
    """
    scope = plan.scope_json
    if any(slots.get(field) is not None for field in ("delivery_start", "delivery_end", "life_plan_items", "life_plan_reserve_at", "life_plan_order_at")):
        return True  # Preserve the existing explicit-window review contract.
    for field, old in (("budget_minor", plan.budget_minor), ("plan_year", scope["year"]),
            ("occasion_id", plan.occasion_id), ("family_id", scope["family_member_id"]),
            ("family_member_id", scope["family_member_id"]), ("currency", scope["currency"])):
        if slots.get(field) is not None and slots[field] != old:
            return True
    for field in ("name", "phone", "address"):
        value = slots.get("recipient_" + field)
        if value is not None and (not isinstance(value, str) or value.strip() != scope["recipient"][field]):
            return True
    if slots.get("family_member_name") is not None:
        from .occasion_service import resolve_family_member
        member = resolve_family_member(session, principal, family_member_name=slots["family_member_name"])
        if member.id != scope["family_member_id"]:
            return True
    from .payment_instruments import resolve_source_instrument
    for field in ("card_reference", "card_number", "card_last4"):
        if slots.get(field) is not None:
            try:
                selected = resolve_source_instrument(session, principal, slots[field], "CNY", purpose="READ")
            except DomainError as error:
                if error.code != "PAYMENT_INSTRUMENT_AMBIGUOUS":
                    raise
                # An unknown/ambiguous newly named card is an unresolved new
                # scope, not a reason to fail the old authorized plan or fall
                # back to its previous funding instrument.
                return True
            if selected["instrument_id"] != scope["instrument"]["instrument_id"]:
                return True
    return False


def _prepare_life_plan_task(session, task, action, context=None):
    action = action.model_dump() if hasattr(action, "model_dump") else action
    kind = action.get("kind") if isinstance(action, dict) else None
    if kind not in ACTION_TOOLS:
        raise DomainError("LIFE_PLAN_ACTION_NOT_ALLOWED", "未登记的生活计划操作。", 422)
    slots = action.get("slots") or {}
    slots = slots.model_dump(exclude_none=True) if hasattr(slots, "model_dump") else slots
    principal, plan_id = _principal(task), slots.get("life_plan_id")
    from .life_plan_dialogue import prepare_dialogue_life_plan_draft, resolve_life_plan
    args, read = {}, kind == "LIFE_PLAN_VIEW"
    if read:
        if slots.get("life_plan_name"):
            plan_id = resolve_life_plan(session, principal, life_plan_id=plan_id,
                life_plan_name=slots["life_plan_name"], include_terminal=True).id
        result = life_plan_view(session, principal, plan_id)
        from .life_plan_facts import finish_life_read
        result = finish_life_read(session, principal, kind, result, {"plan_id": plan_id})
    else:
        created_draft = False
        if not plan_id:
            named = resolve_life_plan(session, principal, life_plan_name=slots["life_plan_name"], allow_missing=True) if slots.get("life_plan_name") else None
            if named is not None:
                plan_id = named.id
            elif kind == "LIFE_PLAN_AUTHORIZE" and (slots.get("occasion_id") or slots.get("family_member_name") or slots.get("family_id") or slots.get("family_member_id")
                    or slots.get("recipient_address")):
                plan_id = prepare_dialogue_life_plan_draft(session, principal, slots,
                    request_key=f"dialogue:{task.id}", task=task, context=context).id
                created_draft = True
            else:
                plan_id = resolve_life_plan(session, principal, life_plan_name=slots.get("life_plan_name")).id
        plan = owned_plan(session, principal, plan_id, lock=True)
        if kind == "LIFE_PLAN_CANCEL" and plan.status in {"CANCELLED", "COMPENSATED", "SUCCEEDED"}:
            from .life_plan_models import LifePlan
            same_name_active = session.scalar(select(LifePlan.id).where(
                LifePlan.sandbox_id == principal.sandbox_id, LifePlan.user_id == principal.user_id,
                LifePlan.name == plan.name, LifePlan.id != plan.id, LifePlan.status == "ACTIVE").limit(1))
            message = "这份具体安排已经结束，不需要再次取消；本次未生成新的确认，也未改动资金。"
            if same_name_active:
                message += "另有同名的生效安排，请核对具体安排后再办理；不会自动改选。"
            raise DomainError("LIFE_PLAN_VERSION_CHANGED", message, 422)
        if kind == "LIFE_PLAN_AUTHORIZE" and not created_draft and _scope_change_requested(session, principal, plan, slots):
            plan = prepare_dialogue_life_plan_draft(session, principal, slots,
                request_key=f"dialogue:{task.id}:replacement", task=task, context=context, prior_plan=plan)
            plan_id = plan.id
        args = authorization_args(plan) if kind == "LIFE_PLAN_AUTHORIZE" else {"plan_id": plan.id, "expected_plan_version": plan.version}
        result = {"plan_id": plan.id, "plan_version": plan.version, "scope": public_plan_scope(plan.scope_json),
            "budget_minor": plan.budget_minor, "message": ("确认后仅在本次生日、商品、现金账户、预算、收件信息和时间范围内自动办理；越界暂停。部分失败时按原订单政策自动申请未履约订单原路退款，到账另核验；预留不等于锁卡。"
                if kind == "LIFE_PLAN_AUTHORIZE" else "确认后撤销未执行授权，释放剩余预留；已支付订单按原退款政策单独处理，不伪造回滚。"),
            "bank_state_changed": False}
        if kind == "LIFE_PLAN_AUTHORIZE" and plan.scope_json.get("replaces"):
            from .life_plan_service import replacement_source
            old = replacement_source(session, principal, plan.scope_json["replaces"])
            result["replacement_notice"] = {"old_plan_name": old.name, "replace_only_after_confirmation": True,
                "release_old_unused_reservation": True, "old_plan_unchanged_during_preparation": True}
            from .life_reservation_replacement import public_adjustment
            adjustment = public_adjustment(plan.scope_json)
            if adjustment is not None:
                result["replacement_notice"]["reservation_adjustment"] = adjustment
                result["message"] += " 确认后在同一事务内调整原已预留预算，保留原下单及配送排期；准备阶段原安排和预留未改变，本次尚未付款。"
            else:
                result["message"] += " 确认后替换原安排并释放原未用预留，按新预算和时间重新预留；准备阶段原安排未改变。"
    tool, key = ACTION_TOOLS[kind], kind.lower()
    node = {"node_id": key, "tool_name": tool, "action_kind": kind, "args": args,
        "read_only": read, "requires_confirmation": not read, "status": "SUCCEEDED" if read else "WAITING_CONFIRMATION"}
    policy = plan.scope_json['schema_version'] if not read and plan.scope_json.get('reservation_replacement') else "bounded-birthday-plan-v2"
    task.plan_json = {"task_id": task.id, "sandbox_id": task.sandbox_id, "plan_version": 1,
        "policy_version": policy, "nodes": [node]}
    task.plan_hash, task.result_json = canonical_hash(task.plan_json), result
    task.fic_json = {**task.fic_json, "requested_capabilities": [tool], "status": "READY"}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=key,
        decision="ALLOW" if read else "REQUIRE_CONFIRMATION", reason_codes=["OWNED_BOUNDED_LIFE_PLAN"],
        policy_version=policy)
    if not read:
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
            node_key=key, action_type=tool, target_id=plan_id, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}),
            expires_at=utcnow() + timedelta(minutes=5)))
    transition_task(task, "SUCCEEDED" if read else "AWAITING_CONFIRMATION")
    return task


def prepare_life_plan_task(session, task, action, context=None):
    try:
        return _prepare_life_plan_task(session, task, action, context)
    except DomainError as error:
        if error.status_code != 422:
            raise
        task.result_json = {"error": error.code, "message": error.message, "bank_state_changed": False}
        if hasattr(error, "schedule_options"):
            task.result_json["schedule_options"] = error.schedule_options
        task.fic_json = {**task.fic_json, "status": "NEED_CLARIFICATION"}
        return task


def commit_life_plan_task(session, task, principal, confirmation, operation_key, *, request_hash=None,
        node=None, auth_challenge_id=None):
    if (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError("LIFE_PLAN_OWNER_REQUIRED", "计划操作不属于本人。", 403)
    nodes = task.plan_json.get("nodes", [])
    row = nodes[0] if len(nodes) == 1 else {}
    kind, args = row.get("action_kind"), row.get("args") or {}
    if (kind not in {"LIFE_PLAN_AUTHORIZE", "LIFE_PLAN_CANCEL"} or confirmation is None
            or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or confirmation.task_id != task.id or confirmation.user_id != principal.user_id
            or confirmation.sandbox_id != principal.sandbox_id or confirmation.status != "PENDING"
            or confirmation.action_type != ACTION_TOOLS[kind] or confirmation.target_id != args.get("plan_id")
            or confirmation.plan_hash != task.plan_hash or canonical_hash(task.plan_json) != task.plan_hash
            or as_utc(confirmation.expires_at) <= utcnow()
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args})):
        raise DomainError("LIFE_PLAN_CONFIRMATION_INVALID", "计划确认已失效，请重新核对。", 409)
    node = node or session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == row["node_id"]))
    if node is None or node.status != "WAITING_CONFIRMATION" or node.tool_name != ACTION_TOOLS[kind]:
        raise DomainError("LIFE_PLAN_CONFIRMATION_INVALID", "计划办理节点已变化。", 409)
    if kind == "LIFE_PLAN_AUTHORIZE":
        result = authorize_life_plan(session, principal, args["plan_id"], expected_version=args["expected_plan_version"],
            scope_hash=args["scope_hash"], confirmation_id=confirmation.id, task_id=task.id, auth_challenge_id=auth_challenge_id)
    else:
        result = cancel_life_plan(session, principal, args["plan_id"], expected_version=args["expected_plan_version"])
        confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    task.result_json = result
    finish_node(session, task, node, result)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=ACTION_TOOLS[kind],
        idempotency_key=operation_key, request_hash=request_hash or canonical_hash({"plan_hash": task.plan_hash, "operation_key": operation_key}),
        status="SUCCESS", result_json=result))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="LIFE_PLAN_AUTHORIZED" if kind == "LIFE_PLAN_AUTHORIZE" else "LIFE_PLAN_CANCELLED",
        payload={"plan_id": args["plan_id"], "scope_hash": args.get("scope_hash"), "operation_key": operation_key})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
        payload_json={"task_id": task.id, "action": kind}))
    return task
