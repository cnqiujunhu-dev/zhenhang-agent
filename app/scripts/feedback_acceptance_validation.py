"""Pure review validators; importing this module performs no I/O or API work.

The strict JSON/projection and dual-DTO checks originate in the prepared
next-candidate journey. They are generalized here without importing or running
that historical CLI, its globals, pinned witnesses, or financial workflow.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re


class AcceptanceStopped(RuntimeError):
    def __init__(self, code, *, observation=None):
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,160}", code) else "ACCEPTANCE_STOPPED"
        self.observation = observation
        super().__init__(self.code)


def require(value, code):
    if not value:
        raise AcceptanceStopped(code)


def integer(value):
    return type(value) is int


def instant(value):
    require(isinstance(value, str), "AWARE_INSTANT_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AcceptanceStopped("AWARE_INSTANT_REQUIRED") from None
    require(result.tzinfo is not None, "AWARE_INSTANT_REQUIRED")
    return result.astimezone(timezone.utc)


def exact_json(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(exact_json(a, b) for a, b in zip(left, right))
    return left == right


def public_projection_matches(public, actual):
    """Each public field must match its source; never synthesize omitted fields."""
    if type(public) is not type(actual):
        return False
    if isinstance(public, dict):
        return all(key in actual and public_projection_matches(value, actual[key]) for key, value in public.items())
    if isinstance(public, list):
        return len(public) == len(actual) and all(public_projection_matches(a, b) for a, b in zip(public, actual))
    return exact_json(public, actual)


def rows(value, key):
    require(isinstance(value, dict) and isinstance(value.get(key), list), "EXPECTED_LIST_" + key.upper())
    return value[key]


def response_error_code(value):
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    candidates = (value.get("code"), value.get("error_code"),
        error.get("code") if isinstance(error, dict) else error if isinstance(error, str) else None)
    return next((code for code in candidates if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,100}", code)), None)


def validated_confirmation_pair(customer, trace, expected_action, *, selected_customer=None, observed_at=None):
    """Bind one current confirmation, including a node in a larger workflow.

    Action permission belongs to the caller's explicit allowlist, not a frozen
    action-name list here. ``observed_at`` exists only for offline saved-DTO
    tests. The live client never supplies an alternative expiry clock.
    """
    selected = customer if selected_customer is None else selected_customer
    require(isinstance(expected_action, str) and bool(expected_action), "EXPECTED_ACTION_REQUIRED")
    for value in (selected, customer, trace):
        require(isinstance(value, dict) and isinstance(value.get("task_id"), str) and value["task_id"]
            and integer(value.get("version")) and value["version"] > 0
            and value.get("state") == "AWAITING_CONFIRMATION", "CURRENT_PENDING_REQUIRED")
    for key in ("task_id", "version", "state"):
        require(exact_json(selected[key], customer[key]) and exact_json(customer[key], trace[key]), "DUAL_TASK_" + key.upper() + "_MISMATCH")
    node_sets = [rows(value, "nodes") for value in (selected, customer, trace)]
    require(len(node_sets[0]) == len(node_sets[1]) == len(node_sets[2]) and bool(node_sets[0]), "DUAL_NODE_COUNT_MISMATCH")
    for original, public, actual in zip(*node_sets):
        require(all(isinstance(value, dict) for value in (original, public, actual)), "NODE_OBJECT_REQUIRED")
        for key in ("node_id", "operation_id", "action", "status", "depends_on", "child_task_id"):
            require(all(key in value for value in (original, public, actual)) and exact_json(original[key], public[key])
                and exact_json(public[key], actual[key]), "DUAL_NODE_" + key.upper() + "_MISMATCH")
        empty_blocked_read = (actual.get('status') == 'BLOCKED' and actual.get('child_task_id') is None
            and actual.get('result') == {} and public.get('result') is None and original.get('result') is None)
        require((empty_blocked_read or public_projection_matches(public.get("result"), actual.get("result")))
            and exact_json(original.get("result"), public.get("result")), "DUAL_NODE_RESULT_MISMATCH")
    pending_sets = [value.get("pending_confirmation") for value in (selected, customer, trace)]
    require(all(isinstance(value, dict) for value in pending_sets), "ACTUAL_PENDING_CONFIRMATIONS_REQUIRED")
    original_pending, public_pending, pending = pending_sets
    for key in ("task_id", "confirmation_id", "plan_hash", "expected_task_version", "node_key", "requires_step_up", "expires_at"):
        require(all(key in value for value in pending_sets) and exact_json(original_pending[key], public_pending[key])
            and exact_json(public_pending[key], pending[key]), "DUAL_CONFIRMATION_" + key.upper() + "_MISMATCH")
    candidates = [node for node in node_sets[2] if node.get("status") == "WAITING_CONFIRMATION"]
    require(len(candidates) == 1, "EXACT_CURRENT_NODE_REQUIRED")
    actual_node = candidates[0]
    require(actual_node.get("action") == expected_action and actual_node.get("child_task_id") == pending["task_id"]
        and all(isinstance(actual_node.get(key), str) and actual_node[key] for key in ("node_id", "operation_id", "child_task_id"))
        and isinstance(actual_node.get("depends_on"), list) and actual_node.get("error") is None, "EXACT_CURRENT_NODE_REQUIRED")
    require(integer(pending["expected_task_version"]) and pending["expected_task_version"] > 0
        and type(pending["requires_step_up"]) is bool
        and all(isinstance(pending[key], str) and pending[key] for key in ("confirmation_id", "node_key"))
        and isinstance(pending["plan_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", pending["plan_hash"]),
        "EXACT_CURRENT_CONFIRMATION_BINDING_REQUIRED")
    require(isinstance(actual_node.get("evidence"), dict) and actual_node["evidence"].get("plan_hash") == pending["plan_hash"],
        "ACTUAL_CHILD_PLAN_HASH_REQUIRED")
    now = datetime.now(timezone.utc) if observed_at is None else instant(observed_at)
    require(instant(pending["expires_at"]) > now + timedelta(seconds=20), "CONFIRMATION_TOO_CLOSE_TO_EXPIRY")
    require(("scope" in original_pending) == ("scope" in public_pending), "SELECTED_CUSTOMER_SCOPE_CHANGED")
    if "scope" in public_pending:
        require(isinstance(public_pending["scope"], dict) and isinstance(pending.get("scope"), dict)
            and exact_json(original_pending["scope"], public_pending["scope"])
            and public_projection_matches(public_pending["scope"], pending["scope"]), "DUAL_CONFIRMATION_SCOPE_MISMATCH")
    require(bool(pending.get("scope")) or isinstance(actual_node.get("result"), dict) and bool(actual_node["result"]),
        "FULL_ACTUAL_REVIEW_SOURCE_REQUIRED")
    return pending, {"schema_version":"actual-dual-dto-confirmation-binding-v2", "parent_task_id":trace["task_id"],
        "parent_version":trace["version"], "action":expected_action, "node_id":actual_node["node_id"],
        "operation_id":actual_node["operation_id"], "child_task_id":actual_node["child_task_id"],
        "confirmation_id":pending["confirmation_id"], "plan_hash":pending["plan_hash"],
        "child_expected_version":pending["expected_task_version"], "node_key":pending["node_key"],
        "expires_at":pending["expires_at"], "selected_and_fresh_customer_bindings_equal":True,
        "actual_trace_customer_projection_equal":True, "no_scope_synthesized":True}


def validated_expected_constraints(pending, node, trace, customer, constraints, validate_scope=None):
    """Dotted paths address actual facts, including pending.scope.domain_confirmation."""
    require(isinstance(constraints, dict) and bool(constraints), "EXPLICIT_EXPECTED_CONSTRAINTS_REQUIRED")
    roots = {"scope":pending.get("scope", {}), "pending":pending, "result":node.get("result"),
             "node":node, "trace":trace, "customer":customer}
    for path, expected in constraints.items():
        require(isinstance(path, str) and bool(path) and all(path.split(".")), "CONSTRAINT_PATH_INVALID")
        actual = roots
        for part in path.split("."):
            if isinstance(actual, dict) and part in actual:
                actual = actual[part]
            elif isinstance(actual, list) and part.isdigit() and int(part) < len(actual):
                actual = actual[int(part)]
            else:
                raise AcceptanceStopped("EXPECTED_CONSTRAINT_SOURCE_MISSING")
        require(exact_json(actual, expected), "EXPECTED_CONSTRAINT_MISMATCH")
    full_review = any(key in constraints and isinstance(constraints[key], dict) and bool(constraints[key]) for key in ("scope", "result", "pending.scope"))
    require(full_review or callable(validate_scope), "COMPLETE_SCOPE_REVIEW_CALLBACK_REQUIRED")
    if validate_scope is not None:
        require(callable(validate_scope), "SCOPE_REVIEW_CALLBACK_REQUIRED")
        try:
            accepted = validate_scope(deepcopy(pending.get("scope", {})), deepcopy(node), deepcopy(trace))
        except Exception:
            raise AcceptanceStopped("SCOPE_REVIEW_CALLBACK_FAILED") from None
        require(accepted is True, "SCOPE_REVIEW_NOT_EXPLICITLY_ACCEPTED")
    return True


def validated_cleanup_read_completions(trace):
    """Only runtime-proven completed reads may precede draft cleanup.

    ``completed_read_results`` is emitted by runtime after registered READ
    capability, child ownership, actor, plan and source binding checks. A plain
    action name or a model's read_only boolean cannot substitute for it.
    """
    facts = trace.get("completed_read_results",[])
    require(isinstance(facts,list), "CLEANUP_READ_PROOF_REQUIRED")
    completed = set()
    for node in rows(trace,"nodes"):
        require(isinstance(node,dict) and node.get("status") not in {"UNKNOWN","WAITING_TIME"}, "CLEANUP_UNCERTAIN_OPERATION")
        if node.get("status") != "SUCCEEDED":
            continue
        matches = [fact for fact in facts if isinstance(fact,dict) and fact.get("operation_id") == node.get("operation_id")]
        require(len(matches) == 1, "CLEANUP_CANNOT_HIDE_EXECUTED_WRITE")
        fact = matches[0]
        require(fact.get("schema_version") == "completed-read-result-v1" and fact.get("task_id") == trace.get("task_id")
            and exact_json(fact.get("task_version"),trace.get("version"))
            and all(exact_json(fact.get(key),node.get(key)) for key in ("node_id","operation_id","child_task_id","action","status","result"))
            and isinstance(node.get("result"),dict) and node["result"].get("bank_state_changed") is not True,
            "CLEANUP_READ_PROOF_MISMATCH")
        completed.add(node["operation_id"])
    observed = (trace.get("outcome") or {}).get("completed_operation_ids",[])
    require(isinstance(observed,list) and all(isinstance(value,str) and value in completed for value in observed),
        "CLEANUP_UNPROVEN_COMPLETED_OPERATION")
    return completed
