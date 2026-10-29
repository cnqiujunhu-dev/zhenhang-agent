"""Contemporaneous confirmation and execution evidence; never banking authority.

Only already accepted confirmations create scope records. Projections read the
stored event, and never reconstruct an approval or release for old events.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import marshal
import re
import sys
from types import FunctionType

from sqlalchemy import and_, func, or_, select

from .models import AuditEvent
from .planning import canonical_hash
from .redaction import redact_credentials


SCHEMA_VERSION = "execution-provenance-v1"
_HASH = re.compile(r"^[a-f0-9]{64}$")
_RELEASE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]{0,32}$")
_SCOPE_FIELDS = frozenset({
    "source_account_id", "payee_id", "payee_name", "payee_last4", "transfer_channel",
    "recipient_username", "recipient_account_id", "card_id", "card_last4", "card_number",
    "display_last4", "subscription_id", "merchant", "source_type", "product_id", "product_name",
    "action", "order_type", "amount_minor", "limit_minor", "units", "unit_price_minor", "currency",
    "source_currency", "target_currency", "due_at", "max_amount_minor", "account_id", "total_minor",
    "clock_basis", "time_zone", "remainder_rule", "quote_id", "quote_scope_hash",
    "requested_currency", "requested_amount_minor", "debit_minor", "credit_minor", "fee_minor",
    "rounding", "goal_id", "goal_name", "target_minor", "grantee_user_id", "grantee_name",
    "owner_user_id", "maximum_amount_minor", "expires_at", "expires_days", "limit_period",
    "delegation_id", "proposal_kind", "behavior_id", "behavior_name", "reward_id", "reward_name",
    "points", "cost_points", "quantity", "estimated_avoided_grams", "proposal_id", "alias_name",
    "recipient_login_account_id", "recipient_bank_account_id", "requested_reference",
    "card_version", "account_version", "username", "account_ref", "login_id", "user_id", "sandbox_id",
})
_NESTED_SCOPE_FIELDS = frozenset({"args", "alias_binding", "recipient_context", "source_card_context", "source", "target"})
_LIST_SCOPE_FIELDS = frozenset({"participants", "scopes"})
_MODULES = (
    "zhenxing.execution_provenance", "zhenxing.runtime", "zhenxing.services", "zhenxing.tools",
    "zhenxing.authentication", "zhenxing.workflow", "zhenxing.planning", "zhenxing.posting",
    "zhenxing.internal_banking", "zhenxing.transfers", "zhenxing.cards", "zhenxing.wealth",
    "zhenxing.subscriptions", "zhenxing.fx_domain", "zhenxing.fx_banking",
    "zhenxing.banking_extensions", "zhenxing.contact_domain", "zhenxing.contacts", "zhenxing.extensions",
)


def _scope_fields(value, depth=0):
    """A bounded, explicit field projection: no message, memo, credentials or request."""
    if not isinstance(value, dict) or depth > 3:
        return {}
    result = {}
    for key, item in value.items():
        if key in _SCOPE_FIELDS:
            if item is None or type(item) in {bool, int}:
                result[key] = item
            elif isinstance(item, str) and len(item) <= 200:
                result[key] = redact_credentials(item)
        elif key in _NESTED_SCOPE_FIELDS and isinstance(item, dict):
            result[key] = _scope_fields(item, depth + 1)
        elif key in _LIST_SCOPE_FIELDS and isinstance(item, list) and len(item) <= 100:
            result[key] = [_scope_fields(row, depth + 1) if isinstance(row, dict) else redact_credentials(row)
                           for row in item if isinstance(row, dict) or isinstance(row, str) and len(row) <= 100]
    return result


def accepted_authorization(session, parent, child, confirmation, operation, request):
    """Called only after the existing ownership, current-plan and step-up checks.

    Scope is a snapshot of what the accepted request binds to, not evidence of
    consumption, execution, settlement, independent identity or a policy ALLOW.
    Missing/inconsistent source facts produce an explicit gap, not new authority.
    """
    result = {
        "schema_version": SCHEMA_VERSION, "stage": "ACCEPTED_USER_CONFIRMATION",
        "scope_status": "NOT_RECORDED", "scope_coverage": "ALLOWLIST_ONLY",
        "operation_id": operation.id, "child_task_id": child.id, "node_key": confirmation.node_key,
        "target_id": confirmation.target_id,
        "action_kind": operation.action_kind, "plan_version": operation.plan_version,
        "expected_task_version": request.expected_task_version,
        "plan_hash": request.plan_hash, "view_hash": confirmation.view_hash,
        "scope": None, "scope_hash": None, "settlement_asserted": False,
    }
    bound = (parent.user_id == child.user_id == confirmation.user_id
             and parent.sandbox_id == child.sandbox_id == confirmation.sandbox_id == operation.sandbox_id
             and confirmation.task_id == child.id == operation.child_task_id
             and operation.parent_task_id == parent.id
             and confirmation.id == request.confirmation_id
             and child.version == request.expected_task_version
             and child.plan_hash == confirmation.plan_hash == request.plan_hash
             and canonical_hash(child.plan_json) == request.plan_hash)
    nodes = child.plan_json.get("nodes", []) if isinstance(child.plan_json, dict) else []
    node = next((row for row in nodes if isinstance(row, dict) and row.get("node_id") == confirmation.node_key), None)
    if not bound or node is None or node.get("requires_confirmation") is not True:
        return result
    scope = _scope_fields(node)
    # Quote amounts/currencies are approved only when the quote's immutable
    # scope hash is the one bound by the confirmed plan and confirmation view.
    if operation.action_kind in {"FX_TRANSFER", "FX_EXCHANGE"}:
        from .fx_models import FxQuote
        quote = session.get(FxQuote, node.get("quote_id")) if node.get("quote_id") else None
        if (quote is None or quote.sandbox_id != child.sandbox_id or quote.user_id != child.user_id
                or confirmation.target_id != quote.id or node.get("quote_scope_hash") != quote.scope_hash
                or not isinstance(quote.scope_json, dict)
                or canonical_hash(quote.scope_json) != quote.scope_hash
                or confirmation.view_hash != canonical_hash({"plan_hash": child.plan_hash, "quote_scope_hash": quote.scope_hash})):
            return result
        quote_scope = quote.scope_json
        source, target = quote_scope.get("source"), quote_scope.get("target")
        if (not isinstance(source, dict) or not isinstance(target, dict)
                or (source.get("user_id"), source.get("sandbox_id")) != (child.user_id, child.sandbox_id)
                or (source.get("currency"), target.get("currency")) != (quote.source_currency, quote.target_currency)
                or (quote_scope.get("requested_currency"), quote_scope.get("requested_amount_minor"),
                    quote_scope.get("debit_minor"), quote_scope.get("credit_minor"))
                != (quote.requested_currency, quote.requested_amount_minor, quote.debit_minor, quote.credit_minor)
                or operation.action_kind == "FX_TRANSFER" and quote_scope.get("payee_id") != node.get("payee_id")):
            return result
        scope.update(_scope_fields(quote.scope_json))
        scope.update({"source_currency": source.get("currency"), "target_currency": target.get("currency")})
    # An absent approved denomination stays absent. Observed account facts are
    # deliberately not substituted for a missing confirmation field.
    result.update({"scope_status": "RECORDED", "scope": scope, "scope_hash": canonical_hash(scope)})
    return result


def _module_hash(module):
    """Hash loaded code objects, never a file that may have changed after import."""
    entries = []
    for name, value in sorted(vars(module).items()):
        if isinstance(value, FunctionType) and value.__module__ == module.__name__:
            entries.append((name, value.__code__))
        elif isinstance(value, type) and value.__module__ == module.__name__:
            for method_name, method in sorted(vars(value).items()):
                if isinstance(method, (staticmethod, classmethod)):
                    method = method.__func__
                if isinstance(method, FunctionType) and method.__module__ == module.__name__:
                    entries.append((f"{name}.{method_name}", method.__code__))
    return hashlib.sha256(marshal.dumps(entries)).hexdigest() if entries else None


def software_provenance():
    try:
        release = metadata.version("zhenxing-agent")
    except (metadata.PackageNotFoundError, OSError, ValueError):
        release = None
    if not isinstance(release, str) or not _RELEASE.fullmatch(release):
        release = None
    hashes = {}
    for name in _MODULES:
        module = sys.modules.get(name)
        if module is not None:
            digest = _module_hash(module)
            if digest:
                hashes[name] = digest
    result = {"package": "zhenxing-agent", "release": release,
              "release_source": "INSTALLED_DISTRIBUTION_METADATA",
              "module_hash_algorithm": "SHA256_MARSHALLED_LOADED_CODE_V1", "module_hashes": hashes,
              "module_hash_scope": "LISTED_LOADED_MODULE_CODE_ONLY"}
    result["runtime_fingerprint"] = canonical_hash(result)
    return result


def execution_attempt(job, operation, *, stage="STARTED", dispatch_started=False):
    return {"schema_version": SCHEMA_VERSION, "stage": stage,
            "operation_id": operation.id, "child_task_id": operation.child_task_id,
            "action_kind": operation.action_kind, "plan_version": operation.plan_version,
            "job_id": job.id, "attempt": job.attempts, "dispatch_started": dispatch_started,
            "software_status": "RECORDED", "software": software_provenance(), "settlement_asserted": False}


def _safe_software(value):
    if not isinstance(value, dict) or value.get("package") != "zhenxing-agent":
        return None
    hashes = value.get("module_hashes")
    if (not isinstance(hashes, dict) or not hashes
            or any(name not in _MODULES or not isinstance(digest, str) or not _HASH.fullmatch(digest) for name, digest in hashes.items())):
        return None
    release = value.get("release")
    if release is not None and (not isinstance(release, str) or not _RELEASE.fullmatch(release)):
        return None
    result = {"package": "zhenxing-agent", "release": release,
              "release_source": "INSTALLED_DISTRIBUTION_METADATA",
              "module_hash_algorithm": "SHA256_MARSHALLED_LOADED_CODE_V1", "module_hashes": dict(hashes),
              "module_hash_scope": "LISTED_LOADED_MODULE_CODE_ONLY"}
    if (any(value.get(key) != result[key] for key in ("release_source", "module_hash_algorithm", "module_hash_scope"))
            or value.get("runtime_fingerprint") != canonical_hash(result)):
        return None
    return {**result, "runtime_fingerprint": value["runtime_fingerprint"]}


def _event_base(event):
    from .authentication import as_utc
    return {"event_seq": event.seq, "event_type": event.event_type,
            "recorded_at": as_utc(event.created_at).isoformat(), "settlement_asserted": False}


def _project_authorization(event):
    result = {**_event_base(event), "scope_status": "NOT_RECORDED", "scope": None, "scope_hash": None}
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    value = payload.get("authorization_provenance")
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return result
    scope = _scope_fields(value.get("scope")) if isinstance(value.get("scope"), dict) else None
    if (value.get("stage") != "ACCEPTED_USER_CONFIRMATION" or value.get("scope_status") != "RECORDED"
            or scope is None or value.get("scope_hash") != canonical_hash(scope)
            or value.get("operation_id") != payload.get("operation_id")
            or value.get("scope_coverage") != "ALLOWLIST_ONLY"
            or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 120
                   for key in ("operation_id", "child_task_id", "node_key", "action_kind"))
            or any(type(value.get(key)) is not int or value[key] < 1
                   for key in ("plan_version", "expected_task_version"))
            or any(not isinstance(value.get(key), str) or not _HASH.fullmatch(value[key])
                   for key in ("plan_hash", "view_hash"))):
        return result
    keys = ("schema_version", "stage", "scope_coverage", "operation_id", "child_task_id", "node_key",
            "action_kind", "target_id", "plan_version", "expected_task_version", "plan_hash", "view_hash")
    for key in keys:
        item = value.get(key)
        if type(item) is int or isinstance(item, str) and len(item) <= 120:
            result[key] = redact_credentials(item) if isinstance(item, str) else item
    return {**result, "scope_status": "RECORDED", "scope": scope, "scope_hash": value["scope_hash"]}


def _project_execution(event):
    result = {**_event_base(event), "software_status": "NOT_RECORDED", "software": None}
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    value = payload.get("execution_provenance")
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return result
    expected_stage = "STARTED" if event.event_type == "ACTION_STARTED" else "FAILED_ATTEMPT"
    software = _safe_software(value.get("software"))
    if (value.get("stage") != expected_stage or software is None
            or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 120
                   for key in ("operation_id", "child_task_id", "action_kind", "job_id"))
            or any(type(value.get(key)) is not int or value[key] < 1 for key in ("plan_version", "attempt"))
            or type(value.get("dispatch_started")) is not bool
            or event.event_type == "ACTION_STARTED" and (value.get("operation_id") != payload.get("operation_id")
                                                         or value["dispatch_started"] is not True)):
        return result
    for key in ("schema_version", "stage", "operation_id", "child_task_id", "action_kind", "plan_version", "job_id", "attempt", "dispatch_started"):
        item = value.get(key)
        if type(item) in {int, bool} or isinstance(item, str) and len(item) <= 120:
            result[key] = redact_credentials(item) if isinstance(item, str) else item
    return {**result, "software_status": "RECORDED", "software": software}


def project_execution_provenance(session, task, *, limit=100):
    """Bounded ADMIN projection of original records, including explicit old gaps."""
    base = select(AuditEvent).where(AuditEvent.task_id == task.id, AuditEvent.sandbox_id == task.sandbox_id)
    authorizations = base.where(AuditEvent.event_type == "ACTION_AUTHORIZED")
    executions = base.where(or_(AuditEvent.event_type == "ACTION_STARTED", and_(
        AuditEvent.event_type == "WORKFLOW_JOB_FAILED", AuditEvent.payload_json["kind"].as_string() == "EXECUTE")))
    result = {}
    for name, query, projector in (("authorization_evidence", authorizations, _project_authorization),
                                   ("execution_evidence", executions, _project_execution)):
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(session.scalars(query.order_by(AuditEvent.seq.desc()).limit(limit)))
        result[name] = [projector(row) for row in reversed(rows)]
        result[f"{name}_total"] = total
        result[f"{name}_returned"] = len(rows)
        result[f"{name}_truncated"] = total > len(rows)
    return result
