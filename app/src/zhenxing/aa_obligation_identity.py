"""A read may identify an owned AA obligation, never authorize its payment.

AA_VIEW/CONSULT keep strict live facts. This separate write selector permits
only the current parent's own preparation/completion to change lifecycle data.
Other payments, expiry, changed amount/receiver, actor changes or missing bank
proof remain hard refusals. No stale balance or prior confirmation is reused.
"""
from sqlalchemy import select

from .authentication import as_utc
from .banking_extension_models import AAParticipant, AAParticipantIdentity
from .contracts import DomainError
from .models import Task, EvidenceSnapshot, TaskNode, Transfer, Confirmation, ToolCall
from .planning import canonical_hash
from .runtime_models import RuntimeRecord, RuntimeOperation


def _deny():
    raise DomainError("AA_OBLIGATION_IDENTITY_CHANGED", "AA份额身份、来源或付款状态已经变化，请核对当前记录。", 409)


def _read_source(session, principal, result, source_task_id):
    from .aa_obligations import VERSION
    from .accounts import require_account_role
    login = require_account_role(session, principal, {"CUSTOMER", "DEV_TEST"})
    if (not isinstance(result, dict) or result.get("schema_version") != VERSION
            or result.get("owner_scope_hash") != canonical_hash({"user_id": principal.user_id, "sandbox_id": principal.sandbox_id})
            or result.get("actor_epoch_hash") != canonical_hash({"login_id": login.id, "version": login.version, "role": login.role})
            or result.get("source_fingerprint") != canonical_hash({key: value for key, value in result.items()
                if key not in {"observed_at", "source_fingerprint"}})):
        _deny()
    query = select(Task).where(Task.user_id == principal.user_id, Task.sandbox_id == principal.sandbox_id,
        Task.state == "SUCCEEDED", Task.result_json["source_fingerprint"].as_string() == result["source_fingerprint"])
    if source_task_id is not None:
        query = query.where(Task.id == source_task_id)
    candidates = list(session.scalars(query))
    for task in candidates:
        if task.result_json != result or canonical_hash(task.plan_json) != task.plan_hash:
            continue
        node = session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == "read-aa-obligations",
            TaskNode.tool_name == "transfer.aa.view.v1", TaskNode.status == "SUCCEEDED"))
        evidence = list(session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.task_id == task.id,
            EvidenceSnapshot.source_kind == "OWNED_AA_OBLIGATION_SNAPSHOT")))
        if node and any(row.payload_json == result for row in evidence):
            return task
    _deny()


def _same_parent_binding(session, principal, identity, payment_parent_task_id):
    if identity.payment_task_id != payment_parent_task_id:
        _deny()
    parent = session.get(Task, payment_parent_task_id)
    record = session.get(RuntimeRecord, payment_parent_task_id)
    operation = session.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == payment_parent_task_id,
        RuntimeOperation.idempotency_key == identity.payment_business_key, RuntimeOperation.active == 1))
    if not parent or not record or not operation or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id) \
            or operation.action_kind not in {"AA_PAY", "TRANSFER"}:
        _deny()
    binding = record.metadata_json.get("aa_payment_bindings", {}).get(operation.id) or record.metadata_json.get("aa_payment_binding")
    if not binding or (binding.get("participant_id"), binding.get("request_id"), binding.get("identity_version"), binding.get("parent_task_id")) != (
            identity.participant_id, identity.request_id, identity.version, parent.id):
        _deny()
    return operation, binding


def _completed_by_same_parent(session, principal, identity, participant, request, operation):
    from .internal_banking import settlement_integrity
    from .internal_banking_models import InternalTransferSettlement
    transfer = session.get(Transfer, identity.transfer_id) if identity.transfer_id else None
    settlement = session.get(InternalTransferSettlement, identity.transfer_id) if transfer else None
    child = session.get(Task, operation.child_task_id) if operation.child_task_id else None
    call = session.scalar(select(ToolCall).where(ToolCall.task_id == operation.child_task_id,
        ToolCall.idempotency_key == operation.idempotency_key, ToolCall.tool_name == "transfer.commit.v1", ToolCall.status == "SUCCESS"))
    confirmation = session.scalar(select(Confirmation).where(Confirmation.task_id == operation.child_task_id,
        Confirmation.node_key == "commit-transfer", Confirmation.status == "CONSUMED", Confirmation.consumed_at.is_not(None)))
    if (not transfer or not settlement or not child or not call or not confirmation or child.state != "SUCCEEDED"
            or operation.status != "SUCCEEDED"
            or canonical_hash(child.plan_json) != child.plan_hash or confirmation.plan_hash != child.plan_hash
            or (transfer.user_id, transfer.sandbox_id, transfer.amount_minor, transfer.idempotency_key) != (
                principal.user_id, principal.sandbox_id, participant.amount_minor, operation.idempotency_key)
            or settlement.receiver_account_id != request.account_id or transfer.status != "POSTED"
            or call.result_json.get("transfer_id") != transfer.id or child.result_json.get("transfer_id") != transfer.id
            or not settlement_integrity(session, transfer)["balanced"]):
        _deny()


def aa_obligation_identity_current(session, principal, source_result, obligation_ref, *,
        source_task_id=None, payment_parent_task_id=None, audit_only=False):
    from .aa_obligations import owned_obligation
    from .aa_participant_payments import _active_terms, _validate_collection_origin
    from .banking_extension_models import AARequestTerms, AACollectionRequest
    source = _read_source(session, principal, source_result, source_task_id)
    selected = [row for row in source_result.get("payable_obligations", []) if row.get("obligation_ref") == obligation_ref]
    if len(selected) != 1 or selected[0].get("paymentable") is not True:
        _deny()
    selected = selected[0]
    identity = owned_obligation(session, principal, obligation_ref)
    participant = session.get(AAParticipant, identity.participant_id)
    request = session.get(AACollectionRequest, identity.request_id)
    terms = session.get(AARequestTerms, identity.request_id)
    if participant is None or request is None or terms is None:
        _deny()
    _validate_collection_origin(session, request, terms)
    if (selected.get("request_id"), selected.get("participant_id"), selected.get("amount_minor"), selected.get("currency")) != (
            request.id, participant.id, participant.amount_minor, "CNY"):
        _deny()
    from .payment_instruments import recipient_instrument_view
    if selected.get("recipient") != recipient_instrument_view(terms.collector_scope_json):
        _deny()
    own_preparation = payment_parent_task_id is not None and identity.payment_task_id == payment_parent_task_id
    if own_preparation:
        operation, binding = _same_parent_binding(session, principal, identity, payment_parent_task_id)
        completed_audit = identity.status == "PAID" and audit_only
        terms_match = binding.get("terms_version") == terms.version or completed_audit and type(binding.get("terms_version")) is int and binding["terms_version"] <= terms.version
        if (type(selected.get("version")) is not int or selected["version"] + 1 != identity.version
                or binding.get("amount_minor") != participant.amount_minor or not terms_match
                or binding.get("collector_scope_hash") != terms.collector_scope_json["scope_hash"]):
            _deny()
        if identity.status == "PAID":
            if not audit_only:
                _deny()
            _completed_by_same_parent(session, principal, identity, participant, request, operation)
        elif identity.status == "PAYMENT_PENDING":
            _active_terms(session, request.id)
        else:
            _deny()
    else:
        _active_terms(session, request.id)
        if identity.version != selected.get("version") or identity.status not in {"PENDING", "PAYMENT_PENDING"}:
            _deny()
        if identity.status == "PAYMENT_PENDING":
            prior = session.get(Task, identity.payment_task_id) if identity.payment_task_id else None
            from .aa_participant_payments import payment_attempt_restartable
            if not payment_attempt_restartable(session,principal,prior):
                _deny()
    return {"aa_obligation_ref": obligation_ref, "request_id": request.id, "participant_id": participant.id,
        "amount_minor": participant.amount_minor, "currency": "CNY", "recipient": recipient_instrument_view(terms.collector_scope_json),
        "identity_version": identity.version, "source_task_id": source.id,
        "authority": "OWNED_AA_IDENTITY_NOT_PAYMENT_AUTHORIZATION"}
