"""Receipt-backed preference STATUS facts, separate from current preference state.

No intent classification, model call or mutation lives here. A completed
preference task is an historical effect, never authority to save it again.
"""
from sqlalchemy import select

from .contracts import DomainError
from .customer_profile_common import ACTION_TOOLS, PREFERENCE_KINDS, require_profile_actor
from .customer_profile_models import CustomerExplicitPreference, CustomerPreferenceChange
from .models import Confirmation, Task, ToolCall
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord


PREFERENCE_ACTIONS = frozenset({"PREFERENCE_SET", "PREFERENCE_REMOVE"})


def _deny():
    raise DomainError("SEMANTIC_SCOPE_SOURCE_CHANGED", "这项偏好办理的回执来源暂时不能核对，请查看原记录。", 409)


def _current_preference(session, principal, kind):
    """Do not promote an old receipt to a current preference or resurrect it."""
    from .customer_preferences import _view
    row = session.scalar(select(CustomerExplicitPreference).where(
        CustomerExplicitPreference.sandbox_id == principal.sandbox_id,
        CustomerExplicitPreference.user_id == principal.user_id, CustomerExplicitPreference.kind == kind)
        .execution_options(populate_existing=True))
    if row is None:
        return None
    if row.status not in {"ACTIVE", "RETRACTED"} or type(row.version) is not int or row.version < 1:
        return None
    current = _view(row)
    events = session.scalars(select(CustomerPreferenceChange).where(
        CustomerPreferenceChange.sandbox_id == principal.sandbox_id,
        CustomerPreferenceChange.user_id == principal.user_id, CustomerPreferenceChange.kind == kind))
    if not any(isinstance(event.result_json, dict) and event.result_json.get("preference") == current for event in events):
        return None
    return {"kind": row.kind, "value": row.value, "status": row.status, "version": row.version}


def _checked_change(session, principal, parent, operation, node):
    """Bind the domain journal to the actual executed child, call and confirmation."""
    from .intelligence import action_signature
    child = session.get(Task, operation.child_task_id)
    child_record = session.get(RuntimeRecord, operation.child_task_id)
    if (operation.sandbox_id != parent.sandbox_id or operation.status != "SUCCEEDED"
            or node.get("kind") != operation.action_kind
            or canonical_hash(node.get("args")) != canonical_hash(operation.args_json)
            or canonical_hash(action_signature(operation.args_json)) != operation.fingerprint
            or child is None or (child.user_id, child.sandbox_id) != (parent.user_id, parent.sandbox_id)
            or child.state != "SUCCEEDED" or canonical_hash(child.plan_json) != child.plan_hash
            or child_record is None or child_record.kind != "CHILD" or child_record.root_task_id != parent.id
            or child_record.metadata_json.get("operation_id") != operation.id):
        _deny()
    plan_nodes = (child.plan_json or {}).get("nodes")
    if not isinstance(plan_nodes, list) or len(plan_nodes) != 1:
        _deny()
    child_node = plan_nodes[0]
    tool = ACTION_TOOLS[operation.action_kind]
    args = child_node.get("args") or {}
    kind = args.get("kind")
    requested_slots = (operation.args_json or {}).get("slots") or {}
    expected_operation = "SET" if operation.action_kind == "PREFERENCE_SET" else "REMOVE"
    expected_version = args.get("expected_version")
    if (child_node.get("action_kind") != operation.action_kind or child_node.get("tool_name") != tool
            or kind not in PREFERENCE_KINDS or args.get("operation") != expected_operation
            or requested_slots.get("preference_kind") != kind
            or operation.action_kind == "PREFERENCE_SET" and requested_slots.get("preference_value") != args.get("value")
            or type(expected_version) is not int or expected_version < 0
            or args.get("source_root_task_id") != parent.id):
        _deny()
    calls = list(session.scalars(select(ToolCall).where(ToolCall.task_id == child.id,
        ToolCall.sandbox_id == principal.sandbox_id, ToolCall.status == "SUCCESS",
        ToolCall.tool_name == tool, ToolCall.idempotency_key == operation.idempotency_key)))
    if len(calls) != 1 or calls[0].result_json != child.result_json:
        _deny()
    confirmations = list(session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
        Confirmation.sandbox_id == principal.sandbox_id, Confirmation.user_id == principal.user_id,
        Confirmation.action_type == tool, Confirmation.node_key == child_node.get("node_id"),
        Confirmation.plan_hash == child.plan_hash, Confirmation.status == "CONSUMED", Confirmation.consumed_at.is_not(None))))
    matched = [confirmation for confirmation in confirmations if confirmation.target_id == kind
        and confirmation.view_hash == canonical_hash({"plan_hash": child.plan_hash, "action": operation.action_kind, "args": args})
        and calls[0].request_hash == canonical_hash({"task_id": child.id, "confirmation_id": confirmation.id, "plan_hash": child.plan_hash})]
    if len(matched) != 1:
        _deny()
    journal = session.scalar(select(CustomerPreferenceChange).where(
        CustomerPreferenceChange.sandbox_id == principal.sandbox_id,
        CustomerPreferenceChange.user_id == principal.user_id,
        CustomerPreferenceChange.operation_key == operation.idempotency_key))
    expected_request = canonical_hash({"kind": kind, "value": args.get("value"), "operation": expected_operation,
        "expected_version": expected_version, "source_text": args.get("source_text"), "source_turn_id": args.get("source_turn_id")})
    result = child.result_json
    if (journal is None or journal.kind != kind or journal.operation != expected_operation
            or journal.request_hash != expected_request or journal.result_json != result
            or not isinstance(result, dict) or result.get("schema_version") != "customer-preference-change-v1"
            or result.get("kind") != kind or result.get("kind_label") != PREFERENCE_KINDS[kind]
            or result.get("operation") != expected_operation or type(result.get("changed")) is not bool
            or result.get("preference_state_changed") is not result["changed"] or result.get("bank_state_changed") is not False
            or result.get("status") != ("SAVED" if expected_operation == "SET" else "RETRACTED")):
        _deny()
    receipt_preference = result.get("preference")
    if receipt_preference is None:
        if expected_operation != "REMOVE" or result["changed"] is not False or expected_version != 0:
            _deny()
    else:
        if (not isinstance(receipt_preference, dict) or receipt_preference.get("kind") != kind
                or receipt_preference.get("kind_label") != PREFERENCE_KINDS[kind]
                or receipt_preference.get("source_kind") != "USER_EXPLICIT"
                or receipt_preference.get("status") != ("ACTIVE" if expected_operation == "SET" else "RETRACTED")
                or type(receipt_preference.get("version")) is not int
                or receipt_preference["version"] != expected_version + (1 if result["changed"] else 0)
                or not isinstance(receipt_preference.get("value"), str) or not receipt_preference["value"].strip()):
            _deny()
        if expected_operation == "SET" and receipt_preference["value"] != args.get("value"):
            _deny()
    from .authentication import as_utc
    return {"kind": kind, "operation": expected_operation, "changed": result["changed"],
        "action": operation.action_kind, "child_task_id": child.id, "change_id": journal.id,
        "status": "VERIFIED_COMPLETED_EFFECT",
        "recorded_preference": {key: receipt_preference[key] for key in ("kind", "value", "status", "version")} if receipt_preference else None,
        "current_preference": _current_preference(session, principal, kind),
        "recorded_at": as_utc(journal.created_at).isoformat(),
        "source_id": "preference-change:" + journal.id, "receipt_hash": canonical_hash(result),
        "task_id": parent.id, "task_version": parent.version, "operation_id": operation.id}


def completed_preference_facts(session, principal, parent):
    """Only an owned, source-audited, verified current plan's completed receipts."""
    if (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id):
        _deny()
    require_profile_actor(session, principal)
    if parent.state != "SUCCEEDED":
        return []
    record = session.get(RuntimeRecord, parent.id)
    if not record or record.kind != "PARENT":
        return []
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id,
        RuntimeOperation.active == 1, RuntimeOperation.plan_version == record.plan_version,
        RuntimeOperation.action_kind.in_(PREFERENCE_ACTIONS)).order_by(RuntimeOperation.ordinal)))
    if not operations:
        return []
    from .runtime import task_trace
    trace = task_trace(session, principal, parent.id)
    outcome = trace.get("outcome") or {}
    if outcome.get("verified") is not True or outcome.get("kind") != "BUSINESS_RECEIPT":
        return []
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version))
    if (snapshot is None or snapshot.plan_hash != parent.plan_hash or canonical_hash(snapshot.plan_json) != parent.plan_hash
            or canonical_hash(parent.plan_json) != parent.plan_hash):
        _deny()
    from .conversation_provenance import validate_dialogue_actor_epoch, validate_current_continuation
    validate_dialogue_actor_epoch(session, parent, record.metadata_json)
    if record.metadata_json.get("cloud_semantic_intent"):
        from .semantic_proposal import validate_semantic_source_snapshot
        validate_semantic_source_snapshot(session, principal, parent, record.metadata_json, snapshot)
    else:
        validate_current_continuation(session, parent)
    nodes = {node.get("node_id"): node for node in snapshot.plan_json.get("nodes", [])}
    if len(nodes) != len(snapshot.plan_json.get("nodes", [])):
        _deny()
    facts = []
    for operation in operations:
        node = nodes.get(operation.operation_key)
        if operation.status != "SUCCEEDED" or not isinstance(node, dict):
            _deny()
        facts.append(_checked_change(session, principal, parent, operation, node))
    return facts


def render_preference_status(facts, language="zh-CN"):
    """Historical effect first; current effective setting is explicitly separate."""
    lines = []
    english = language == "en"
    for fact in facts:
        kind = fact["kind"]
        label = {"TRAVEL": "travel preference", "CONSUMPTION": "spending preference"}[kind] if english else PREFERENCE_KINDS[kind]
        recorded, current = fact["recorded_preference"], fact["current_preference"]
        if fact["operation"] == "SET":
            line = (f'That completed request saved your {label} as “{recorded["value"]}”.' if english
                    else f'这次已将你的{label}保存为“{recorded["value"]}”。')
        elif fact["changed"]:
            line = (f'That completed request removed your {label} “{recorded["value"]}”.' if english
                    else f'这次已撤回你的{label}“{recorded["value"]}”。')
        else:
            line = (f'That request found no active {label} to remove.' if english else f'这次核对时没有生效的{label}，没有重复修改。')
        if current is None:
            line += (' The current saved setting cannot be verified from this receipt alone.' if english
                     else '当前设置暂不能核对，不能仅凭这条回执认定仍生效。')
        elif current["status"] == "RETRACTED":
            line += ((f' It was subsequently removed; no {label} is active now.' if english else f'这项偏好之后已撤回，目前没有生效的{label}。')
                     if recorded and current["version"] > recorded["version"] else
                     (f' No {label} is active now.' if english else f'目前没有生效的{label}。'))
        elif recorded and current == recorded:
            line += (' It is still your current saved preference.' if english else '目前仍是你保存的偏好。')
        else:
            line += (f' A later setting is active now: “{current["value"]}”.' if english else f'之后已有新的设置，当前保存的是“{current["value"]}”。')
        lines.append(line)
    return "\n".join(lines) if lines else None


def render_preference_conversation_status(facts, language="zh-CN"):
    """One fresh state per kind, then the actual journal-ordered effects.

    This is only a rendering of already verified receipts. In particular, a
    completed SET is not assumed to be the latest setting, and repeated chat
    references to one operation must not become repeated historical changes.
    """
    if not facts:
        return None
    unique = {}
    for fact in facts:
        key = fact["operation_id"]
        if key in unique and unique[key] != fact:
            return None
        unique[key] = fact
    ordered = sorted(unique.values(), key=lambda fact: (fact["recorded_at"], fact["change_id"]))
    current_by_kind = {}
    for fact in ordered:
        current = fact["current_preference"]
        if current is None or fact["kind"] in current_by_kind and current_by_kind[fact["kind"]] != current:
            return None
        current_by_kind[fact["kind"]] = current
    english = language == "en"
    labels = {kind: ({"TRAVEL": "travel preference", "CONSUMPTION": "spending preference"}[kind]
        if english else PREFERENCE_KINDS[kind]) for kind in current_by_kind}
    states = []
    for kind, current in current_by_kind.items():
        label = labels[kind]
        if current["status"] == "ACTIVE":
            states.append(f'Your current {label} is “{current["value"]}”.' if english
                else f'当前{label}是“{current["value"]}”。')
        else:
            states.append(f'No {label} is active now.' if english else f'当前没有生效的{label}。')
    history, previous = [], {}
    several_kinds = len(labels) > 1
    for fact in ordered:
        kind, recorded = fact["kind"], fact["recorded_preference"]
        label = labels[kind]
        prior = previous.get(kind)
        if fact["operation"] == "SET":
            value = recorded["value"]
            if english:
                clause = f'saved your {label} as “{value}”' if not prior else (
                    f'saved your {label} again as “{value}”' if prior["operation"] == "REMOVE" else
                    f'changed your {label} to “{value}”')
            elif not prior or several_kinds:
                clause = f'将{label}保存为“{value}”'
            elif prior["operation"] == "REMOVE":
                clause = f'重新保存为“{value}”'
            elif prior["recorded_preference"]["value"] == value:
                clause = f'再次保存为“{value}”'
            else:
                clause = f'改为“{value}”'
        elif fact["changed"]:
            clause = (f'removed your {label} “{recorded["value"]}”' if english else
                f'撤回了{label if not prior or several_kinds else "该偏好"}“{recorded["value"]}”')
        else:
            clause = (f'checked removal of your {label}; none was active and no setting changed' if english
                else f'核对撤回{label}时没有生效设置，未重复修改')
        history.append(clause)
        previous[kind] = fact
    if english:
        history_text = ('In this conversation, I ' + history[0] + ''.join('; then ' + clause for clause in history[1:]) + '.')
    else:
        history_text = ('本会话' + ('先' if len(history) > 1 else '已') + history[0]
            + ''.join('，随后' + clause for clause in history[1:]) + '。')
    return (' ' if english else '').join(states) + (' ' if english else '') + history_text
