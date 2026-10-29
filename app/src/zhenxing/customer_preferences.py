"""Explicit, retractable user preferences; bill patterns remain suggestions."""
from copy import deepcopy
from datetime import timedelta
import re

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .models import Account, Transaction, utcnow
from .planning import canonical_hash
from .redaction import redact_credentials
from .customer_profile_common import (PREFERENCE_KINDS, finish_read, preference_kind as checked_preference_kind,
    profile_clock, require_profile_actor)
from .customer_profile_models import CustomerExplicitPreference, CustomerPreferenceChange


def _value(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 80
            or any(ord(char) < 32 for char in value) or value != redact_credentials(value)):
        raise DomainError("PREFERENCE_VALUE_REQUIRED", "请用八十字以内的文字明确这项偏好。", 422)
    return value.strip()


def _view(row):
    return {"kind": row.kind, "kind_label": PREFERENCE_KINDS[row.kind], "value": row.value,
        "status": row.status, "version": row.version, "source_kind": row.source_kind,
        "source_text": row.source_text, "confirmed_at": as_utc(row.confirmed_at).isoformat()}


def preference_suggestions(session, principal):
    """Bounded posted-expense pattern, neither an active label nor eligibility."""
    require_profile_actor(session, principal)
    clock = profile_clock(session, principal)
    accounts = list(session.scalars(select(Account.id).where(Account.sandbox_id == principal.sandbox_id,
        Account.user_id == principal.user_id)))
    if not accounts:
        return []
    from .analytics import is_movement
    rows = list(session.scalars(select(Transaction).where(Transaction.sandbox_id == principal.sandbox_id,
        Transaction.account_id.in_(accounts), Transaction.amount_minor < 0,
        Transaction.occurred_at >= clock - timedelta(days=90), Transaction.occurred_at <= clock)
        .order_by(Transaction.occurred_at.desc(), Transaction.id).limit(500)))
    totals = {}
    evidence = []
    for row in rows:
        if is_movement(row) or not row.category or len(row.category) > 80 or row.category != redact_credentials(row.category):
            continue
        totals[row.category] = totals.get(row.category, 0) + abs(row.amount_minor)
        evidence.append({"id": row.id, "amount_minor": row.amount_minor, "category": row.category,
                         "occurred_at": as_utc(row.occurred_at).isoformat()})
    if not totals:
        return []
    category = sorted(totals, key=lambda key: (-totals[key], key))[0]
    return [{"kind": "CONSUMPTION", "value": category,
        "reason": "最近九十天已取得的消费记录中，该分类支出较高；是否作为偏好由你决定。",
        "confirmed": False, "source_kind": "BILL_PATTERN", "source_fingerprint": canonical_hash(evidence),
        "coverage": {"days": 90, "records_used": len(evidence), "maximum_records": 500},
        "eligible_for_automatic_save": False}]


def preferences_view(session, principal, *, preference_kind=None):
    require_profile_actor(session, principal)
    kind = checked_preference_kind(preference_kind, optional=True)
    query = select(CustomerExplicitPreference).where(CustomerExplicitPreference.sandbox_id == principal.sandbox_id,
        CustomerExplicitPreference.user_id == principal.user_id)
    if kind is not None:
        query = query.where(CustomerExplicitPreference.kind == kind)
    rows = list(session.scalars(query.order_by(CustomerExplicitPreference.kind)))
    suggestions = [row for row in preference_suggestions(session, principal) if kind is None or row["kind"] == kind]
    return finish_read(session, principal, "PREFERENCE_VIEW", {
        "preferences": [_view(row) for row in rows if row.status == "ACTIVE"],
        "retracted_kinds": [row.kind for row in rows if row.status == "RETRACTED"],
        "suggestions": suggestions, "query": {"preference_kind": kind},
        "inferred_suggestions_are_active_preferences": False,
    }, {"preferences": [{"kind": row.kind, "version": row.version, "status": row.status} for row in rows]})


def preference_version(session, principal, kind):
    require_profile_actor(session, principal)
    kind = checked_preference_kind(kind)
    row = session.scalar(select(CustomerExplicitPreference).where(
        CustomerExplicitPreference.sandbox_id == principal.sandbox_id,
        CustomerExplicitPreference.user_id == principal.user_id, CustomerExplicitPreference.kind == kind))
    return row.version if row else 0


def _source(session, principal, value, source_text, source_turn_id):
    if source_turn_id:
        from .conversation_models import ChatTurn
        turn = session.get(ChatTurn, source_turn_id)
        if (turn is None or (turn.user_id, turn.sandbox_id) != (principal.user_id, principal.sandbox_id)
                or (turn.diagnostics_json or {}).get("input_redacted") or (turn.diagnostics_json or {}).get("credential_only")):
            raise DomainError("PREFERENCE_SOURCE_CHANGED", "偏好原话暂时不能核对，请重新说明。", 409)
        if source_text is not None and source_text != turn.message:
            raise DomainError("PREFERENCE_SOURCE_CHANGED", "偏好原话已变化，请重新说明。", 409)
        source_text = turn.message
    if (not isinstance(source_text, str) or not source_text.strip() or len(source_text) > 2000
            or value is not None and value not in source_text):
        raise DomainError("PREFERENCE_SOURCE_REQUIRED", "请先明确说出要保存或撤回的本人偏好。", 422)
    return redact_credentials(source_text)


def change_preference(session, principal, *, kind, value=None, operation="SET", operation_key,
                      expected_version, source_text, source_turn_id=None, source_kind="USER_EXPLICIT"):
    """Called only after the existing grounded-request/confirmation gate.

    A source kind is not an inferred confidence switch. Bill suggestions have no
    accepted write source and can only become explicit after a new user request.
    """
    require_profile_actor(session, principal, lock=True)
    kind = checked_preference_kind(kind)
    if operation not in {"SET", "REMOVE"} or source_kind != "USER_EXPLICIT":
        raise DomainError("PREFERENCE_EXPLICIT_REQUEST_REQUIRED", "推测标签不会自动保存，请由本人明确确认偏好。", 422)
    value = _value(value) if operation == "SET" else None
    if not isinstance(operation_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", operation_key):
        raise DomainError("PREFERENCE_OPERATION_KEY_INVALID", "偏好请求标识无效，请重新提交。", 422)
    if type(expected_version) is not int or expected_version < 0:
        raise DomainError("PREFERENCE_VERSION_REQUIRED", "请重新核对偏好版本后提交。", 422)
    source = _source(session, principal, value, source_text, source_turn_id)
    request_hash = canonical_hash({"kind": kind, "value": value, "operation": operation,
        "expected_version": expected_version, "source_text": source, "source_turn_id": source_turn_id})
    existing = session.scalar(select(CustomerPreferenceChange).where(
        CustomerPreferenceChange.sandbox_id == principal.sandbox_id,
        CustomerPreferenceChange.user_id == principal.user_id, CustomerPreferenceChange.operation_key == operation_key))
    if existing:
        if existing.request_hash != request_hash:
            raise DomainError("IDEMPOTENCY_CONFLICT", "这个请求标识已用于另一项偏好变更。", 409)
        return deepcopy(existing.result_json)
    row = session.scalar(select(CustomerExplicitPreference).where(
        CustomerExplicitPreference.sandbox_id == principal.sandbox_id,
        CustomerExplicitPreference.user_id == principal.user_id, CustomerExplicitPreference.kind == kind).with_for_update())
    if expected_version != (row.version if row else 0):
        raise DomainError("PREFERENCE_VERSION_CHANGED", "偏好已在别处更新，请查看最新内容后再确认。", 409)
    previous = _view(row) if row else None
    changed = not (operation == "REMOVE" and (row is None or row.status == "RETRACTED"))
    if operation == "SET":
        if row is None:
            row = CustomerExplicitPreference(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
                kind=kind, value=value, status="ACTIVE", version=1, source_kind=source_kind,
                source_text=source, source_turn_id=source_turn_id, confirmed_at=utcnow(), updated_at=utcnow())
            session.add(row)
        else:
            row.value, row.status, row.version = value, "ACTIVE", row.version + 1
            row.source_kind, row.source_text, row.source_turn_id = source_kind, source, source_turn_id
            row.confirmed_at = row.updated_at = utcnow()
    elif changed:
        row.status, row.version = "RETRACTED", row.version + 1
        row.source_kind, row.source_text, row.source_turn_id = source_kind, source, source_turn_id
        row.confirmed_at = row.updated_at = utcnow()
    session.flush()
    result = {"schema_version": "customer-preference-change-v1", "kind": kind,
        "kind_label": PREFERENCE_KINDS[kind], "operation": operation, "changed": changed,
        "preference": _view(row) if row else None, "bank_state_changed": False,
        "preference_state_changed": changed, "status": "SAVED" if operation == "SET" else "RETRACTED"}
    session.add(CustomerPreferenceChange(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        operation_key=operation_key, request_hash=request_hash, kind=kind, operation=operation,
        previous_json=previous, result_json=deepcopy(result)))
    session.flush()
    return result
