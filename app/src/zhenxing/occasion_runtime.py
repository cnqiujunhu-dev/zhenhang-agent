"""Conversational birthday read/set via ordinary versioned confirmation."""
from datetime import timedelta

from sqlalchemy import select

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .models import Confirmation, OutboxMessage, TaskNode, ToolCall, utcnow
from .occasion_models import FamilyOccasion
from .occasion_service import (normalize_occasion_values, occasion_view, put_family_occasion,
    resolve_family_member, resolve_family_occasion)
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .workflow import finish_node, transition_task


ACTION_TOOLS = {"OCCASION_VIEW": "occasion.view.v1", "OCCASION_SET": "occasion.set.v1",
    "OCCASION_MEMBER_VIEW": "occasion.member.view.v1", "OCCASION_MEMBER_SET": "occasion.member.set.v1"}
READ_ACTIONS = {"OCCASION_VIEW", "OCCASION_MEMBER_VIEW"}
TOOL_DEFINITIONS = {tool: {"read_only": kind in READ_ACTIONS, "risk_level": "L1" if kind in READ_ACTIONS else "L2",
    "owner": "family_occasions"} for kind, tool in ACTION_TOOLS.items()}
FIELDS = {"birthday_month": "month", "birthday_day": "day", "birthday_calendar": "calendar",
    "birthday_timezone": "timezone", "leap_day_policy": "leap_day_policy", "lunar_leap_month": "lunar_leap_month",
    "confirmed_solar_date": "confirmed_solar_date", "adult_subject_confirmed": "adult_subject_confirmed"}


def _prepare_occasion_task(session, task, action, context=None):
    from .services import Principal
    principal = Principal(task.user_id, task.sandbox_id)
    require_profile_actor(session, principal)
    action = action.model_dump() if hasattr(action, "model_dump") else action
    kind, slots = action.get("kind"), action.get("slots") or {}
    slots = slots.model_dump(exclude_none=True) if hasattr(slots, "model_dump") else slots
    slots = {key:value for key,value in slots.items() if value is not None}
    if kind not in ACTION_TOOLS:
        raise DomainError("OCCASION_ACTION_NOT_ALLOWED", "未登记的生日资料操作。", 422)
    if kind == "OCCASION_MEMBER_VIEW":
        from .family_profile_service import family_member_profiles
        selected = resolve_family_member(session, principal, family_member_id=slots.get("family_member_id"),
            family_member_name=slots.get("family_member_name")) if slots.get("family_member_id") or slots.get("family_member_name") else None
        result = family_member_profiles(session, principal, selected.id if selected else None)
        from .life_plan_facts import finish_life_read
        result = finish_life_read(session, principal, kind, result, {"family_member_id": selected.id if selected else None})
        args, target = {}, None
    elif kind == "OCCASION_MEMBER_SET":
        from .family_profile_service import prepare_member_values
        args = prepare_member_values(session, principal, **{key: slots.get(key) for key in
            ("family_member_id", "family_member_name", "family_member_new_name", "family_relationship")})
        args["source_reference"] = "confirmed-task:" + task.id
        target = args["family_member_id"]
        result = {"preview": dict(args), "bank_state_changed": False, "family_profile_state_changed": False,
            "message": "确认后仅保存家庭成员显示名和关系标签；不会创建登录、银行账户或家庭协助权限。"}
    elif kind == "OCCASION_VIEW":
        if slots.get("occasion_id") or slots.get("family_member_name"):
            occasion = resolve_family_occasion(session, principal, occasion_id=slots.get("occasion_id"),
                family_member_name=slots.get("family_member_name"))
            result = occasion_view(session, principal, occasion.id, as_of=utcnow())
        else:
            result = occasion_view(session, principal, as_of=utcnow())
            occasion = None
        from .life_plan_facts import finish_life_read
        result = finish_life_read(session, principal, kind, result, {"occasion_id": occasion.id if occasion else None})
        args, target = {}, None
    else:
        if slots.get("occasion_id"):
            # A source-bound identity selects the current owned member only.
            # New date values below still come exclusively from this action;
            # neither the prior receipt nor its confirmation is reused.
            selected = resolve_family_occasion(session, principal, occasion_id=slots["occasion_id"],
                family_member_name=slots.get("family_member_name"),
                family_member_id=slots.get("family_member_id") or slots.get("family_id"))
            member = resolve_family_member(session, principal, family_member_id=selected.family_member_id)
        else:
            member = resolve_family_member(session, principal, family_member_id=slots.get("family_member_id") or slots.get("family_id"),
                family_member_name=slots.get("family_member_name"))
        existing = session.scalar(select(FamilyOccasion).where(FamilyOccasion.family_member_id == member.id,
            FamilyOccasion.owner_user_id == principal.user_id, FamilyOccasion.sandbox_id == principal.sandbox_id))
        values = {destination: slots[source] for source, destination in FIELDS.items() if source in slots}
        if "month" not in values or "day" not in values:
            raise DomainError("OCCASION_BIRTHDAY_REQUIRED", "请明确生日月日和历法。", 422)
        # This source names the original prepared/confirmed task, not a model's memory.
        values = normalize_occasion_values(**values, source_reference=f"confirmed-task:{task.id}")
        args = {**values, "family_member_id": member.id, "expected_version": existing.version if existing else 0}
        target = member.id
        from .family_profile_service import member_display_name
        result = {"preview": {**values, "family_member_name": member_display_name(session, member)},
            "message": "确认后仅保存这位已登记家庭成员的生日；孩子也可作为收件人，这不授予其账户、信用或付款权限。",
            "bank_state_changed": False, "occasion_state_changed": False}
    read, key, tool = kind in READ_ACTIONS, kind.lower(), ACTION_TOOLS[kind]
    plan_node = {"node_id": key, "tool_name": tool, "action_kind": kind, "args": args, "read_only": read,
        "requires_confirmation": not read, "status": "SUCCEEDED" if read else "WAITING_CONFIRMATION"}
    task.plan_json = {"task_id": task.id, "sandbox_id": task.sandbox_id, "policy_version": "family-occasion-v1", "nodes": [plan_node]}
    task.plan_hash, task.result_json = canonical_hash(task.plan_json), result
    task.fic_json = {**task.fic_json, "requested_capabilities": [tool], "status": "READY"}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=key,
        decision="ALLOW" if read else "REQUIRE_CONFIRMATION", reason_codes=["OWNED_CONFIRMED_FAMILY_OCCASION"],
        policy_version="family-occasion-v1")
    if not read:
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
            node_key=key, action_type=tool, target_id=target, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}),
            expires_at=utcnow() + timedelta(minutes=5)))
    transition_task(task, "SUCCEEDED" if read else "AWAITING_CONFIRMATION")
    return task


def prepare_occasion_task(session, task, action, context=None):
    try:
        return _prepare_occasion_task(session, task, action, context)
    except DomainError as error:
        if error.status_code != 422:
            raise
        task.result_json = {"error": error.code, "message": error.message, "bank_state_changed": False}
        task.fic_json = {**task.fic_json, "status": "NEED_CLARIFICATION"}
        return task


def commit_occasion_task(session, task, principal, confirmation, operation_key, *, request_hash=None, node=None):
    require_profile_actor(session, principal)
    rows = task.plan_json.get("nodes", [])
    row = rows[0] if len(rows) == 1 else {}
    kind = row.get("action_kind")
    args, tool = row.get("args") or {}, ACTION_TOOLS.get(kind)
    if ((task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or kind not in {"OCCASION_SET", "OCCASION_MEMBER_SET"} or confirmation is None
            or confirmation.task_id != task.id or confirmation.user_id != principal.user_id
            or confirmation.sandbox_id != principal.sandbox_id or confirmation.status != "PENDING"
            or confirmation.action_type != tool or confirmation.node_key != row.get("node_id")
            or confirmation.target_id != args.get("family_member_id") or confirmation.plan_hash != task.plan_hash
            or canonical_hash(task.plan_json) != task.plan_hash or as_utc(confirmation.expires_at) <= utcnow()
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args})):
        raise DomainError("OCCASION_CONFIRMATION_INVALID", "生日资料确认已失效，请重新核对。", 409)
    node = node or session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == row["node_id"]))
    if node is None or node.status != "WAITING_CONFIRMATION" or node.tool_name != tool:
        raise DomainError("OCCASION_CONFIRMATION_INVALID", "生日资料步骤已变化。", 409)
    if kind == "OCCASION_MEMBER_SET":
        from .family_profile_service import put_member_profile
        result = put_member_profile(session, principal, **args)
    else:
        result = put_family_occasion(session, principal, **args)
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    task.result_json = result; finish_node(session, task, node, result)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=tool, idempotency_key=operation_key,
        request_hash=request_hash or canonical_hash({"plan_hash": task.plan_hash, "operation_key": operation_key}),
        status="SUCCESS", result_json=result))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="FAMILY_PROFILE_CONFIRMED" if kind == "OCCASION_MEMBER_SET" else "FAMILY_OCCASION_CONFIRMED",
        payload={"family_member_id": args["family_member_id"], "operation_key": operation_key})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED", payload_json={"task_id": task.id}))
    return task
