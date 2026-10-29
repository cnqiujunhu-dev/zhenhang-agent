"""Grounded profile actions integrated with the ordinary task confirmation gate."""
from datetime import timedelta

from sqlalchemy import select

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError
from .models import Confirmation, EvidenceSnapshot, OutboxMessage, Task, TaskNode, ToolCall, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .workflow import finish_node, transition_task
from .customer_benefits import benefits_view
from .customer_membership import membership_view
from .customer_preferences import _source, _value, change_preference, preference_version, preferences_view
from .customer_profile_common import ACTION_TOOLS, PREFERENCE_KINDS, READ_ACTIONS, WRITE_ACTIONS, preference_kind, require_profile_actor


def _principal(task):
    from .services import Principal
    return Principal(task.user_id, task.sandbox_id)


def _original_request(session, task, context):
    """Recover the server-bound user turn, never the compiler's canonical sentence."""
    from .runtime_models import RuntimeRecord
    root_id = (context or {}).get("source_root_task_id")
    root = session.get(Task, root_id) if root_id else None
    record = session.get(RuntimeRecord, root_id) if root_id else None
    binding = (record.metadata_json or {}).get("cloud_semantic_intent") if record else None
    if (root is None or (root.user_id, root.sandbox_id) != (task.user_id, task.sandbox_id)
            or not isinstance(binding, dict) or not binding.get("source_turn_id")):
        raise DomainError("PREFERENCE_GROUNDED_REQUEST_REQUIRED", "请通过对话明确要保存或撤回的本人偏好。", 422)
    from .conversation_models import ChatTurn
    turn = session.get(ChatTurn, binding["source_turn_id"])
    if (turn is None or (turn.user_id, turn.sandbox_id) != (task.user_id, task.sandbox_id)
            or canonical_hash({"message": turn.message}) != binding.get("source_message_hash")):
        raise DomainError("PREFERENCE_SOURCE_CHANGED", "偏好原话来源已变化，请重新说明。", 409)
    return root, turn


def prepare_customer_profile_task(session, task, action, context=None):
    """Dispatch from services after validate_compiled_action, like extension tasks."""
    action = action.model_dump() if hasattr(action, "model_dump") else action
    kind = action.get("kind") if isinstance(action, dict) else None
    if kind not in ACTION_TOOLS:
        raise DomainError("PROFILE_ACTION_NOT_ALLOWED", "不支持这项资料操作。", 422)
    slots = action.get("slots") or {}
    slots = slots.model_dump(exclude_none=True) if hasattr(slots, "model_dump") else slots
    principal = _principal(task)
    require_profile_actor(session, principal)
    tool, args = ACTION_TOOLS[kind], {}
    try:
        if kind == "MEMBERSHIP_VIEW":
            result = membership_view(session, principal)
        elif kind == "BENEFITS_VIEW":
            args = {"benefit_name": slots["benefit_name"]} if slots.get("benefit_name") else {}
            result = benefits_view(session, principal, **args)
        elif kind == "PREFERENCE_VIEW":
            args = {"preference_kind": slots["preference_kind"]} if slots.get("preference_kind") else {}
            result = preferences_view(session, principal, **args)
        else:
            pref_kind = preference_kind(slots.get("preference_kind"))
            value = _value(slots.get("preference_value")) if kind == "PREFERENCE_SET" else None
            root, source = _original_request(session, task, context)
            source_text = _source(session, principal, value, source.message, source.id)
            args = {"kind": pref_kind, "value": value, "operation": "SET" if kind == "PREFERENCE_SET" else "REMOVE",
                "expected_version": preference_version(session, principal, pref_kind),
                "source_text": source_text, "source_turn_id": source.id, "source_root_task_id": root.id,
                "source_message_hash": canonical_hash({"message": source.message})}
            result = {"preview": {"kind": pref_kind, "kind_label": PREFERENCE_KINDS[pref_kind], "value": value,
                        "operation": args["operation"]},
                "message": (f"确认后将{PREFERENCE_KINDS[pref_kind]}设为“{value}”。" if kind == "PREFERENCE_SET"
                            else f"确认后撤回已保存的{PREFERENCE_KINDS[pref_kind]}。"),
                "bank_state_changed": False, "preference_state_changed": False}
    except DomainError as error:
        task.result_json = {"message": error.message, "error": error.code}
        if error.status_code != 422:
            transition_task(task, "FAILED")
        return task
    read_only, node_key = kind in READ_ACTIONS, kind.lower()
    node = {"node_id": node_key, "tool_name": tool, "read_only": read_only,
        "status": "SUCCEEDED" if read_only else "WAITING_CONFIRMATION",
        "requires_confirmation": not read_only, "action_kind": kind, "args": args}
    plan = {"task_id": task.id, "sandbox_id": task.sandbox_id, "plan_version": 1,
        "policy_version": "customer-profile-v1", "nodes": [node]}
    task.plan_json, task.plan_hash, task.result_json = plan, canonical_hash(plan), result
    task.fic_json = {**task.fic_json, "requested_capabilities": [tool], "status": "READY",
        "slots": {key: {"value": value, "source": "USER_TEXT" if key in {"kind", "value", "benefit_name", "preference_kind"} else "AUTHORITATIVE"}
                  for key, value in args.items()}}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=node_key,
        decision="ALLOW" if read_only else "REQUIRE_CONFIRMATION",
        reason_codes=["OWNED_CUSTOMER_PROFILE", "EXPLICIT_PREFERENCE_REQUEST" if not read_only else "READ_ONLY_PROFILE"],
        policy_version="customer-profile-v1")
    session.add(EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id,
        source_kind="OWNED_CUSTOMER_PROFILE", source_version="customer-profile-v1", payload_json=result if read_only else args))
    transition_task(task, "SUCCEEDED" if read_only else "AWAITING_CONFIRMATION")
    if not read_only:
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
            node_key=node_key, action_type=tool, target_id=args["kind"], plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}),
            expires_at=utcnow() + timedelta(minutes=5)))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="CUSTOMER_PROFILE_PREPARED",
        payload={"action": kind, "read_only": read_only, "plan_hash": task.plan_hash})
    return task


def commit_customer_profile_task(session, task, principal, confirmation, operation_key, *, request_hash=None, node=None):
    require_profile_actor(session, principal, lock=True)
    if (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError("PROFILE_OWNER_REQUIRED", "这项偏好操作不属于本人。", 403)
    plan_node = (task.plan_json.get("nodes") or [None])[0]
    if not isinstance(plan_node, dict) or plan_node.get("action_kind") not in WRITE_ACTIONS:
        raise DomainError("PROFILE_ACTION_NOT_ALLOWED", "当前任务没有待确认的偏好变更。", 409)
    kind, args, tool = plan_node["action_kind"], plan_node["args"], plan_node["tool_name"]
    if (confirmation is None or confirmation.task_id != task.id or confirmation.sandbox_id != task.sandbox_id
            or confirmation.user_id != task.user_id or confirmation.status != "PENDING"
            or as_utc(confirmation.expires_at) <= utcnow() or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or canonical_hash(task.plan_json) != task.plan_hash or confirmation.plan_hash != task.plan_hash
            or confirmation.action_type != tool or confirmation.node_key != plan_node["node_id"]
            or confirmation.target_id != args["kind"]
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args})):
        raise DomainError("PROFILE_CONFIRMATION_INVALID", "偏好确认已失效，请重新核对。", 409)
    root, source = _original_request(session, task, {"source_root_task_id": args["source_root_task_id"]})
    if source.id != args["source_turn_id"] or canonical_hash({"message": source.message}) != args["source_message_hash"]:
        raise DomainError("PREFERENCE_SOURCE_CHANGED", "偏好原话来源已变化，请重新说明。", 409)
    node = node or session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == plan_node["node_id"]))
    if node is None or node.tool_name != tool or node.status != "WAITING_CONFIRMATION":
        raise DomainError("PROFILE_CONFIRMATION_INVALID", "偏好处理步骤已变化，请重新核对。", 409)
    result = change_preference(session, principal, **{key: args[key] for key in (
        "kind", "value", "operation", "expected_version", "source_text", "source_turn_id")}, operation_key=operation_key)
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    task.result_json = result
    finish_node(session, task, node, result)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=tool, idempotency_key=operation_key,
        request_hash=request_hash or canonical_hash({"plan_hash": task.plan_hash, "operation_key": operation_key}),
        status="SUCCESS", result_json=result))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="CUSTOMER_PREFERENCE_CHANGED",
        payload={"operation": args["operation"], "kind": args["kind"], "operation_key": operation_key})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
        payload_json={"task_id": task.id, "action": kind}))
    return task


def dispatch_customer_profile_task(session, *, task, principal, request, idempotency_key):
    from .tools import ToolGateway
    node = (task.plan_json.get("nodes") or [None])[0]
    if not isinstance(node, dict) or node.get("action_kind") not in WRITE_ACTIONS:
        raise DomainError("PROFILE_ACTION_NOT_ALLOWED", "当前任务没有待确认的偏好变更。", 409)
    request_hash, prepared_node, confirmation, duplicate = ToolGateway().authorize_write(session, task=task,
        sandbox_id=principal.sandbox_id, user_id=principal.user_id, request=request,
        idempotency_key=idempotency_key, tool_name=node["tool_name"], node_key=node["node_id"])
    if duplicate:
        return task
    return commit_customer_profile_task(session, task, principal, confirmation, idempotency_key,
        request_hash=request_hash, node=prepared_node)
