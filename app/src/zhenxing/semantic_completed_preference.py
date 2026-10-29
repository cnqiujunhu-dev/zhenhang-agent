"""A completed preference SET may identify a NEW, separately confirmed SET.

No completed transfer, investment, removal or mixed write can use this codec.
The prior preference kind is identity only; the new value always has a current
literal span. Both normalization and DB rebuilding are read-only.
"""
from contextvars import ContextVar
from copy import deepcopy

from sqlalchemy import select

from .contracts import DomainError
from .planning import canonical_hash


KINDS = frozenset({"TRAVEL", "CONSUMPTION"})
_DEPTH = ContextVar("completed_preference_identity_depth", default=0)
# These carry another source, target, persistent effect or operation boundary.
# Registered question/topic/presentation metadata is left intact for the main
# validator's existing enum, mode and capability checks, not treated as a write.
_SOURCE_OR_OPERATION_FIELDS = ("entity_refs", "memory_updates", "read_targets", "scope_frame_refs",
    "operation_scope", "query_reference", "query_base_source", "retrieval_query")


def _deny():
    raise DomainError("SEMANTIC_PREFERENCE_REBASE_SOURCE_CHANGED", "此前偏好设置的来源已变化，请重新说明要保存的偏好；不会复用旧确认。", 409)


def _candidate(value, prepared, reference, diagnostic=None):
    def skip(reason):
        if diagnostic is not None:
            diagnostic["rebase_skip_reason"] = reason
        return None

    if not isinstance(value, dict) or not isinstance(prepared, dict) or not isinstance(prepared.get("message"), str):
        return skip("CONTEXT_SHAPE_INVALID")
    if not isinstance(prepared.get("frames"), list) or not isinstance(prepared.get("capabilities"), list):
        return skip("CONTEXT_SHAPE_INVALID")
    frames = [row for row in prepared["frames"] if isinstance(row, dict) and row.get("frame_ref") == reference]
    if len(frames) != 1:
        return skip("FRAME_NOT_UNIQUE_OR_OFFERED")
    frame = frames[0]
    actions = frame.get("actions")
    if frame.get("state") != "SUCCEEDED" or frame.get("paused") is True or frame.get("write_effect_possible") is not True \
            or not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], dict) \
            or actions[0].get("kind") != "PREFERENCE_SET":
        return skip("FRAME_NOT_COMPLETED_SINGLE_SET")
    action = actions[0]
    slots = action.get("slots") or {}
    if not isinstance(slots, dict) or slots.get("preference_kind") not in KINDS or not isinstance(action.get("action_ref"), str):
        return skip("PREVIOUS_KIND_IDENTITY_INVALID")
    capabilities = [row for row in prepared["capabilities"] if isinstance(row, dict) and row.get("kind") == "PREFERENCE_SET"]
    if len(capabilities) != 1 or capabilities[0].get("read_only") is not False \
            or not isinstance(capabilities[0].get("allowed_fields"), list) \
            or not {"preference_kind", "preference_value"} <= set(capabilities[0]["allowed_fields"]):
        return skip("SET_CAPABILITY_NOT_OFFERED")
    blocked = [key for key in _SOURCE_OR_OPERATION_FIELDS if value.get(key) not in (None, [], "")]
    if value.get("query_base") not in (None, "CURRENT"):
        blocked.append("query_base")
    if blocked:
        if diagnostic is not None:
            diagnostic["blocking_fields"] = blocked
        return skip("OTHER_PURPOSE_OR_SOURCE_FIELDS_PRESENT")
    changes = value.get("changes")
    if not isinstance(changes, list) or len(changes) != 1 or not isinstance(changes[0], dict):
        return skip("NOT_SINGLE_CURRENT_VALUE_CHANGE")
    change = changes[0]
    if change.get("field") != "preference_value" or change.get("operation", "SET") != "SET" \
            or change.get("entity_ref") is not None or change.get("selected_options") not in (None, []):
        return skip("NOT_LITERAL_VALUE_SET")
    span = change.get("source_span")
    if not isinstance(span, dict) or set(span) != {"start", "end"} \
            or type(span["start"]) is not int or type(span["end"]) is not int \
            or span["start"] < 0 or span["end"] < 1:
        return skip("SOURCE_SPAN_SHAPE_INVALID")
    message = prepared["message"]
    start, end = span["start"], span["end"]
    literal = change.get("source_text")
    in_bounds = 0 <= start < end <= len(message)
    if diagnostic is not None:
        diagnostic.update(source_span_in_bounds=in_bounds, source_text_present=literal is not None,
            source_span_normalized=False)
    if literal is not None:
        if not isinstance(literal, str) or not 1 <= len(literal) <= 80 or literal != literal.strip():
            return skip("SOURCE_TEXT_SHAPE_INVALID")
        first = message.find(literal)
        multiple = first >= 0 and message.find(literal, first + 1) >= 0
        matched = message[start:end] == literal
        if diagnostic is not None:
            diagnostic.update(source_literal_matches_span=matched,
                source_literal_occurrence="ZERO" if first < 0 else "MULTIPLE" if multiple else "ONE")
        if not matched:
            # Same closed, literal-only correction as semantic_router: a
            # supplied verbatim value must occur exactly once in THIS message.
            # Run it before the completed-frame edit gate; never infer a value,
            # shift an unlabelled span, or search history/old preference text.
            if first < 0 or multiple:
                return skip("SOURCE_LITERAL_NOT_UNIQUE")
            start, end = first, first + len(literal)
            if diagnostic is not None:
                diagnostic["source_span_normalized"] = True
    if not 0 <= start < end <= len(message):
        return skip("SOURCE_SPAN_OUT_OF_BOUNDS")
    raw = message[start:end]
    if not 1 <= len(raw) <= 80 or raw != raw.strip():
        return skip("CURRENT_VALUE_INVALID")
    if raw == slots.get("preference_value"):
        return skip("CURRENT_VALUE_EQUALS_PREVIOUS_VALUE")
    from .redaction import redact_credentials
    if redact_credentials(raw) != raw:
        return skip("CURRENT_VALUE_REDACTED")
    canonical_change = {**deepcopy(change), "source_span": {"start": start, "end": end}, "source_text": raw, "operation": "SET"}
    return frame, action, canonical_change


def completed_preference_rebase_diagnostics(value, prepared):
    """Fixed reason/shape symbols only, never offsets, text, values or prompts.

    This is diagnostic-only and must not authorize or alter a decision. Keep it
    total even for malformed JSON so rejection logging cannot hide the error.
    """
    diagnostic = {"version": "completed-preference-rebase-v2", "eligible": False,
        "rebase_skip_reason": "DECISION_SHAPE_INVALID"}
    try:
        wire = value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value
        if not isinstance(wire, dict):
            return diagnostic
        marker = wire.get("preference_rebase_ref")
        if marker is None and wire.get("mode") not in {"REVISE", "CONTINUE"}:
            diagnostic["rebase_skip_reason"] = "NOT_REBASE_MODE"
            return diagnostic
        if marker is None and wire.get("dependencies") not in (None, []):
            diagnostic["rebase_skip_reason"] = "DEPENDENCIES_PRESENT"
            return diagnostic
        if marker is None and wire.get("action_kinds") not in (None, [], ["PREFERENCE_SET"]):
            diagnostic["rebase_skip_reason"] = "ACTION_KINDS_CHANGED"
            return diagnostic
        candidate = _candidate(wire, prepared, marker if marker is not None else wire.get("focus_ref"), diagnostic)
        if candidate is None:
            return diagnostic
        if marker is not None and (wire.get("mode") != "REQUEST" or wire.get("focus_ref") is not None
                or wire.get("action_kinds") != ["PREFERENCE_SET"] or wire.get("dependencies") != [[]]):
            diagnostic["rebase_skip_reason"] = "PERSISTED_MARKER_SHAPE_INVALID"
            return diagnostic
        if candidate[2].get("action_ref") != ("new:0" if marker is not None else candidate[1]["action_ref"]):
            diagnostic["rebase_skip_reason"] = "CHANGE_ACTION_REFERENCE_MISMATCH"
            return diagnostic
        diagnostic.update(eligible=True, rebase_skip_reason=None)
    except Exception:
        diagnostic.update(eligible=False, rebase_skip_reason="CONTEXT_OR_DECISION_SHAPE_INVALID")
    return diagnostic


def completed_preference_rebase(value, prepared):
    """Lossless closed-kind navigation normalization, not write authorization.

    Nonmatching old receipts remain byte-structure compatible. A persisted
    REQUEST marker is validated and returned unchanged for idempotent replay.
    """
    wire = value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value
    if not isinstance(wire, dict):
        return value
    marker = wire.get("preference_rebase_ref")
    if marker is not None:
        candidate = _candidate(wire, prepared, marker)
        if not candidate or wire.get("mode") != "REQUEST" or wire.get("focus_ref") is not None \
                or wire.get("action_kinds") != ["PREFERENCE_SET"] or wire.get("dependencies") != [[]] \
                or candidate[2].get("action_ref") != "new:0":
            raise ValueError("SEMANTIC_PREFERENCE_REBASE_INVALID")
        if candidate[2] != wire["changes"][0] or wire.get("query_base") is not None:
            normalized = deepcopy(wire)
            normalized["changes"] = [candidate[2]]
            normalized["query_base"] = None
            return normalized
        return value
    if wire.get("mode") not in {"REVISE", "CONTINUE"} or wire.get("dependencies") not in (None, []) \
            or wire.get("action_kinds") not in (None, [], ["PREFERENCE_SET"]):
        return value
    candidate = _candidate(wire, prepared, wire.get("focus_ref"))
    if not candidate or candidate[2].get("action_ref") != candidate[1]["action_ref"]:
        return value
    normalized = deepcopy(wire)
    normalized.update(mode="REQUEST", action_kinds=["PREFERENCE_SET"], dependencies=[[]], focus_ref=None,
        preference_rebase_ref=wire["focus_ref"], query_base=None)
    normalized["changes"] = [{**candidate[2], "action_ref": "new:0"}]
    return normalized


def completed_preference_kind(session, principal, thread, turn, decision, offered, depth=0, *, audit_only=False):
    """Revalidate a completed SET receipt and return ONLY its category identity.

    Fresh preparation requires the selected setting still to be current. During
    an immutable source audit, the subsequent new SET may already have changed
    that value, so its old receipt remains identity evidence, never old value.
    """
    if depth > 24 or _DEPTH.get() >= 24:
        _deny()
    token = _DEPTH.set(_DEPTH.get() + 1)
    try:
        from .semantic_proposal import _frame, _valid_turn, _trusted_span
        from .runtime_models import RuntimeOperation, RuntimeRecord
        from .conversation_models import ChatTurn
        from .customer_profile_common import require_profile_actor
        from .preference_status_facts import completed_preference_facts

        require_profile_actor(session, principal)
        thread, _scope, epoch = _valid_turn(session, principal, thread, turn)
        value = decision.model_dump(exclude_none=True) if hasattr(decision, "model_dump") else decision
        if not isinstance(offered, dict) or not isinstance(value, dict) or not value.get("preference_rebase_ref"):
            _deny()
        if offered.get("thread_id") != thread.id or offered.get("turn_id") != turn.id \
                or offered.get("actor_scope_hash") != epoch or offered.get("message_hash") != canonical_hash({"message": turn.message}) \
                or (offered.get("model") or {}).get("message") != turn.message:
            _deny()
        try:
            completed_preference_rebase(value, offered["model"])
        except (ValueError, TypeError, KeyError):
            _deny()
        reference = value["preference_rebase_ref"]
        parent, entry, actions = _frame(session, principal, thread, offered, reference, depth, audit_only=audit_only)
        record = session.get(RuntimeRecord, parent.id)
        if parent.state != "SUCCEEDED" or parent.version != entry.get("task_version") or not record or record.kind != "PARENT" \
                or record.plan_version != entry.get("plan_version") or parent.plan_hash != entry.get("plan_hash") \
                or canonical_hash({"message": parent.message}) != entry.get("message_hash") \
                or (record.metadata_json or {}).get("input_revision", 0) != entry.get("input_revision", 0) \
                or len(actions) != 1 or actions[0].get("kind") != "PREFERENCE_SET":
            _deny()
        source = session.get(ChatTurn, entry.get("source_turn_id"))
        if source is None or source.task_id != parent.id or source.thread_id != thread.id or source.status != "ANSWERED" \
                or source.sequence >= turn.sequence or (source.user_id, source.sandbox_id) != (principal.user_id, principal.sandbox_id):
            _deny()
        _valid_turn(session, principal, thread, source)
        operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id,
            RuntimeOperation.active == 1)))
        if len(operations) != 1 or operations[0].action_kind != "PREFERENCE_SET" or operations[0].status != "SUCCEEDED" \
                or operations[0].plan_version != record.plan_version:
            _deny()
        facts = completed_preference_facts(session, principal, parent)
        if len(facts) != 1:
            _deny()
        fact = facts[0]
        kind = fact.get("kind")
        if fact.get("status") != "VERIFIED_COMPLETED_EFFECT" or fact.get("action") != "PREFERENCE_SET" \
                or fact.get("operation") != "SET" or kind not in KINDS or fact.get("task_id") != parent.id \
                or fact.get("task_version") != parent.version or fact.get("operation_id") != operations[0].id \
                or fact.get("child_task_id") != operations[0].child_task_id or not fact.get("change_id") \
                or actions[0].get("slots", {}).get("preference_kind") != kind:
            _deny()
        recorded, current = fact.get("recorded_preference"), fact.get("current_preference")
        if not isinstance(recorded, dict) or recorded.get("kind") != kind or recorded.get("status") != "ACTIVE" \
                or not audit_only and current != recorded:
            _deny()
        change = value["changes"][0]
        raw = _trusted_span(turn.message, change["source_span"], "preference_value", prior={})
        if raw != change.get("source_text") or raw == recorded.get("value"):
            _deny()
        return kind
    finally:
        _DEPTH.reset(token)
