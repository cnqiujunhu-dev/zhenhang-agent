"""Participant-owned AA payments use ordinary authenticated transfer tasks.

The collector's confirmation creates an obligation, never a payer authorization.
This module does not take card numbers/amounts from the collector as consent by
another account. Every participant chooses their own DEBIT funding instrument.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib

from sqlalchemy import select

from .authentication import as_utc
from .banking_extension_models import AACollectionRequest, AAParticipant, AAParticipantIdentity, AARequestTerms
from .contracts import DomainError, FinancialIntentContract
from .models import Account, Task, User, new_id, utcnow
from .planning import canonical_hash


def register_real_collection(session, request, collector_scope, participants, *, expires_at=None):
    expiry = as_utc(expires_at) if expires_at is not None else utcnow() + timedelta(hours=24)
    if not utcnow() < expiry <= utcnow() + timedelta(days=30):
        raise DomainError("AA_EXPIRY_INVALID", "AA有效期须在未来30天内。", 422)
    if len({login.id for _, login in participants}) != len(participants):
        raise DomainError("AA_PARTICIPANT_DUPLICATE", "同一实际参与人不能因多个称呼或卡片重复分摊。", 409)
    session.add(AARequestTerms(request_id=request.id, collector_scope_json=deepcopy(collector_scope), expires_at=expiry))
    for participant, login in participants:
        session.add(AAParticipantIdentity(participant_id=participant.id, request_id=request.id,
            login_account_id=login.id, sandbox_id=login.sandbox_id, user_id=login.user_id))
    session.flush()


def _owned_invitation(session, principal, request_id, participant_id, *, lock=False):
    from .accounts import require_account_role
    login = require_account_role(session, principal, {"CUSTOMER", "DEV_TEST"})
    query = select(AAParticipantIdentity).where(AAParticipantIdentity.participant_id == participant_id,
        AAParticipantIdentity.request_id == request_id, AAParticipantIdentity.user_id == principal.user_id,
        AAParticipantIdentity.sandbox_id == principal.sandbox_id, AAParticipantIdentity.login_account_id == login.id)
    identity = session.scalar(query.with_for_update() if lock else query)
    if identity is None:
        raise DomainError("AA_INVITATION_NOT_FOUND", "不存在属于本人的这笔AA付款邀请。", 404)
    return identity


def _active_terms(session, request_id, *, lock=True):
    terms = session.scalar(select(AARequestTerms).where(AARequestTerms.request_id == request_id).with_for_update()
                           if lock else select(AARequestTerms).where(AARequestTerms.request_id == request_id))
    request = session.get(AACollectionRequest, request_id)
    if terms is None or request is None:
        raise DomainError("AA_TERMS_NOT_FOUND", "该历史模拟请求不具备本人付款授权范围。", 409)
    _validate_collection_origin(session, request, terms)
    if terms.status != "ACTIVE" or request.status not in {"COLLECTING", "PARTIAL"}:
        raise DomainError("AA_REQUEST_NOT_PAYABLE", "AA请求已经取消、完成或结束。", 409)
    if as_utc(terms.expires_at) <= utcnow():
        raise DomainError("AA_REQUEST_EXPIRED", "AA请求已过期，未扣款。", 409)
    return request, terms


def _validate_collection_origin(session, request, terms):
    """The invitation remains bound to the collector's consumed confirmation."""
    from .models import Confirmation, ToolCall
    task = session.get(Task, request.task_id)
    node = next((row for row in (task.plan_json or {}).get("nodes", []) if row.get("node_id") == "create-aa-request"), None) if task else None
    confirmation = session.scalar(select(Confirmation).where(Confirmation.task_id == request.task_id,
        Confirmation.node_key == "create-aa-request", Confirmation.action_type == "transfer.aa.v1",
        Confirmation.status == "CONSUMED", Confirmation.consumed_at.is_not(None)))
    call = session.scalar(select(ToolCall).where(ToolCall.task_id == request.task_id,
        ToolCall.tool_name == "transfer.aa.v1", ToolCall.status == "SUCCESS"))
    scope = {key: value for key, value in (node or {}).items() if key not in {"node_id", "tool_name", "read_only", "requires_confirmation"}}
    try:
        original_expiry = as_utc(datetime.fromisoformat(scope.get("aa_expires_at", "")))
    except (ValueError, TypeError):
        raise DomainError("AA_COLLECTION_SOURCE_CHANGED", "AA原始有效期来源缺失。", 409) from None
    participants = list(session.scalars(select(AAParticipant).where(AAParticipant.request_id == request.id).order_by(AAParticipant.ordinal)))
    if (not task or not node or task.state != "SUCCEEDED" or (task.user_id, task.sandbox_id, task.intent_type) != (request.user_id, request.sandbox_id, "AA_REQUEST")
            or canonical_hash(task.plan_json) != task.plan_hash or confirmation is None or confirmation.plan_hash != task.plan_hash
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "scope": scope})
            or call is None or call.result_json.get("aa_request_id") != request.id
            or (request.account_id, request.total_minor, request.participant_count) != (
                scope.get("account_id"), scope.get("total_minor"), scope.get("participant_count"))
            or terms.collector_scope_json != scope.get("aa_collector_scope")
            or as_utc(terms.expires_at) != original_expiry
            or [(row.payee_id, row.amount_minor, row.display_name) for row in participants] != [
                (row["payee_id"], row["amount_minor"], row["name"]) for row in scope.get("participants", [])]):
        raise DomainError("AA_COLLECTION_SOURCE_CHANGED", "AA原始收款范围或确认回执已变化，请发起人重新核对。", 409)


def payment_attempt_restartable(session,principal,previous):
    """An expired, never-consumed preparation is not an uncertain payment."""
    from .models import Confirmation,ToolCall
    from .runtime_models import RuntimeOperation
    if not previous or (previous.user_id,previous.sandbox_id)!=(principal.user_id,principal.sandbox_id):return False
    if previous.state in {'FAILED','CLOSED'}:return True
    if previous.state!='AWAITING_CONFIRMATION':return False
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==previous.id,RuntimeOperation.active==1)))
    if len(operations)!=1 or operations[0].action_kind not in {'AA_PAY','TRANSFER'} or operations[0].status!='WAITING_CONFIRMATION':return False
    child_id=operations[0].child_task_id
    if not child_id or session.scalar(select(ToolCall.id).where(ToolCall.task_id==child_id)) is not None:return False
    confirmations=list(session.scalars(select(Confirmation).where(Confirmation.task_id==child_id)))
    return bool(confirmations) and all(row.consumed_at is None and row.status in {'PENDING','EXPIRED'}
        and as_utc(row.expires_at)<=utcnow() for row in confirmations)


def close_expired_payment_attempt(session,principal,previous):
    if previous and previous.state=='AWAITING_CONFIRMATION' and payment_attempt_restartable(session,principal,previous):
        from .runtime import cancel_runtime
        cancel_runtime(session,principal,previous.id,previous.version)


def own_invitations(session, principal):
    from .accounts import require_account_role
    require_account_role(session, principal, {"CUSTOMER", "DEV_TEST"})
    from .customer_history_archive import visible_history
    identities = list(session.scalars(select(AAParticipantIdentity).join(AACollectionRequest,
        AACollectionRequest.id == AAParticipantIdentity.request_id).where(AAParticipantIdentity.user_id == principal.user_id,
        AAParticipantIdentity.sandbox_id == principal.sandbox_id,
        visible_history(session, principal, AACollectionRequest.created_at)).order_by(AAParticipantIdentity.participant_id)))
    result = []
    for identity in identities:
        participant = session.get(AAParticipant, identity.participant_id)
        terms = session.get(AARequestTerms, identity.request_id)
        request = session.get(AACollectionRequest, identity.request_id)
        if not participant or not terms or not request:
            continue
        from .payment_instruments import recipient_instrument_view
        from .payment_instruments import validate_recipient_snapshot
        try:
            validate_recipient_snapshot(session, terms.collector_scope_json, lock=False)
            _validate_collection_origin(session, request, terms)
            recipient_available = True
        except DomainError:
            recipient_available = False
        collector = session.get(User, request.user_id)
        payment_task = session.get(Task, identity.payment_task_id) if identity.payment_task_id else None
        owned_task = payment_task and (payment_task.user_id, payment_task.sandbox_id) == (principal.user_id, principal.sandbox_id)
        can_prepare = identity.status == "PENDING" or identity.status == "PAYMENT_PENDING" and owned_task and payment_attempt_restartable(session,principal,payment_task)
        display_status, receipt_verified = identity.status, None
        if identity.status == "PAID":
            receipt_verified = aa_participant_receipt_verified(session, identity, participant, request)
            if not receipt_verified:
                display_status = "UNVERIFIED"
        from .aa_note import collection_note
        result.append({"request_id": request.id, "participant_id": participant.id,
            "memo": collection_note(session, request),
            "amount_minor": participant.amount_minor, "currency": "CNY", "status": display_status,
            "receipt_verified": receipt_verified,
            "request_status": terms.status, "version": identity.version,
            "expired": as_utc(terms.expires_at) <= utcnow(), "expires_at": as_utc(terms.expires_at).isoformat(),
            "recipient_available": recipient_available,
            "paymentable": bool(can_prepare and terms.status == "ACTIVE" and as_utc(terms.expires_at) > utcnow() and recipient_available),
            "collector_display_name": collector.display_name if collector else terms.collector_scope_json.get("username"),
            "recipient": recipient_instrument_view(terms.collector_scope_json),
            "payment_task_id": identity.payment_task_id if owned_task else None,
            "payment_task_state": payment_task.state if owned_task else None, "transfer_id": identity.transfer_id})
    return result


def aa_participant_receipt_verified(session, identity, participant, request):
    from .models import Transfer
    from .internal_banking_models import InternalTransferSettlement
    from .internal_banking import settlement_integrity
    transfer = session.get(Transfer, identity.transfer_id) if identity and identity.transfer_id else None
    settlement = session.get(InternalTransferSettlement, identity.transfer_id) if transfer else None
    return bool(identity and identity.status == participant.status == "PAID" and transfer and settlement and transfer.status == "POSTED"
        and (transfer.user_id, transfer.sandbox_id, transfer.amount_minor) == (identity.user_id, identity.sandbox_id, participant.amount_minor)
        and settlement.receiver_account_id == request.account_id and settlement_integrity(session, transfer)["balanced"])


def prepare_participant_payment(session, principal, request_id, participant_id, source_card_reference, idempotency_key):
    from .runtime_models import RuntimeRecord, RuntimeOperation, RuntimeTaskRequest, PlanSnapshot
    from .intelligence import ProposedAction, ActionSlots, IntentProposal, canonical_action_message, action_signature
    from .internal_banking import resolve_transfer_payees, lock_transfer_accounts
    from .payment_instruments import resolve_source_instrument, validate_recipient_snapshot
    from .services import owned_task
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 120:
        raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "AA付款需要明确的请求幂等键。", 422)
    identity = _owned_invitation(session, principal, request_id, participant_id)
    key = "aa-payment:" + hashlib.sha256(idempotency_key.encode()).hexdigest()
    request_hash = canonical_hash({"request_id": request_id, "participant_id": participant_id, "source_card": source_card_reference})
    old = session.scalar(select(RuntimeTaskRequest).where(RuntimeTaskRequest.sandbox_id == principal.sandbox_id,
        RuntimeTaskRequest.user_id == principal.user_id, RuntimeTaskRequest.idempotency_key == key))
    if old:
        if old.request_hash != request_hash:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一AA付款请求键不能换卡或改变对象。", 409)
        return owned_task(session, principal, old.task_id)
    request, terms = _active_terms(session, request_id, lock=False)
    source = resolve_source_instrument(session, principal, source_card_reference, "CNY", purpose="TRANSFER")
    recipient = validate_recipient_snapshot(session, terms.collector_scope_json, lock=False)
    from .transfer_payment_scope import recipient_card_number
    target_reference = recipient_card_number(session, recipient)
    payee = resolve_transfer_payees(session, principal.sandbox_id, principal.user_id, target_reference)[0]
    lock_transfer_accounts(session, principal.sandbox_id, principal.user_id, source["account_id"], payee)
    request, terms = _active_terms(session, request_id)
    identity = _owned_invitation(session, principal, request_id, participant_id, lock=True)
    old = session.scalar(select(RuntimeTaskRequest).where(RuntimeTaskRequest.sandbox_id == principal.sandbox_id,
        RuntimeTaskRequest.user_id == principal.user_id, RuntimeTaskRequest.idempotency_key == key))
    if old:
        if old.request_hash != request_hash:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一AA付款请求键不能换卡或改变对象。", 409)
        return owned_task(session, principal, old.task_id)
    if identity.status not in {"PENDING", "PAYMENT_PENDING"}:
        raise DomainError("AA_PARTICIPANT_NOT_PAYABLE", "本人的AA份额已经支付、拒绝或取消。", 409)
    if identity.payment_task_id:
        previous = session.get(Task, identity.payment_task_id)
        close_expired_payment_attempt(session,principal,previous)
        if previous and previous.state not in {"FAILED", "CLOSED"}:
            raise DomainError("AA_PAYMENT_ALREADY_PENDING", "该份额已有待确认或处理中付款，请查看原任务。", 409)
    participant = session.get(AAParticipant, participant_id)
    action = ProposedAction(kind="TRANSFER", slots=ActionSlots(payee_name=target_reference,
        amount_minor=participant.amount_minor, currency="CNY", card_number=source["card_number"], memo="AA份额付款"))
    proposal = IntentProposal(actions=[action], summary="支付本人已核对的AA份额", model_label="server-aa-obligation")
    message = canonical_action_message(action)
    parent = Task(sandbox_id=principal.sandbox_id, user_id=principal.user_id, message=message,
        intent_type="TRANSFER", state="PLANNING", model_label="server-aa-obligation", fic_json={}, plan_json={}, analysis_json={}, result_json={})
    session.add(parent); session.flush()
    operation_key = "aa-pay:" + new_id()
    identity.status, identity.version, identity.payment_task_id = "PAYMENT_PENDING", identity.version + 1, parent.id
    identity.payment_business_key = operation_key
    from .access_models import LoginAccount
    actor_login = session.get(LoginAccount, identity.login_account_id)
    binding = {"request_id": request_id, "participant_id": participant_id, "identity_version": identity.version,
        "terms_version": terms.version, "amount_minor": participant.amount_minor, "recipient_account_id": request.account_id,
        "parent_task_id": parent.id, "collector_scope_hash": terms.collector_scope_json["scope_hash"],
        "payer_login_version": actor_login.version}
    record = RuntimeRecord(task_id=parent.id, kind="PARENT", plan_version=1, metadata_json={
        "messages": [message], "summary": proposal.summary, "domains": ["TRANSFER"], "aa_payment_binding": binding,
        "input_revision": 0, "customer_reply_enabled": False, "deadline_at": (utcnow() + timedelta(hours=24)).isoformat()})
    session.add(record)
    child = Task(sandbox_id=principal.sandbox_id, user_id=principal.user_id, message=message, intent_type="TRANSFER",
        state="NEED_CLARIFICATION", model_label="server-aa-obligation", fic_json=FinancialIntentContract(
            intent_id=new_id(), intent_version=1, intent_type="TRANSFER", actor_user_id=principal.user_id,
            sandbox_id=principal.sandbox_id, source_channel="WEB", slots={}, requested_capabilities=[], status="NEED_CLARIFICATION").model_dump())
    session.add(child); session.flush()
    from .platform import get_or_create_conversation
    from .transfers import prepare_transfer_task
    conversation = get_or_create_conversation(session, principal.sandbox_id, principal.user_id)
    prepare_transfer_task(session, task=child, conversation_id=conversation.id, proposed_action=action.model_dump())
    if child.state != "AWAITING_CONFIRMATION":
        raise DomainError("AA_PAYMENT_PREPARE_FAILED", "付款卡或银行核验尚未通过，未建立付款确认。", 409)
    session.add(RuntimeRecord(task_id=child.id, root_task_id=parent.id, kind="CHILD", metadata_json={"aa_payment_binding": binding}))
    operation = RuntimeOperation(parent_task_id=parent.id, sandbox_id=parent.sandbox_id, child_task_id=child.id,
        operation_key="p1-a1", fingerprint=canonical_hash(action_signature(action.model_dump())), action_kind="TRANSFER",
        label="本人AA份额付款", args_json=action.model_dump(), depends_on_json=[], ordinal=0, plan_version=1,
        idempotency_key=operation_key, status="WAITING_CONFIRMATION")
    session.add(operation); session.flush()
    plan = {"version": 1, "nodes": [{"node_id": operation.operation_key, "operation_id": operation.id,
        "kind": operation.action_kind, "args": operation.args_json, "depends_on": []}]}
    parent.plan_json, parent.plan_hash = plan, canonical_hash(plan)
    session.add(PlanSnapshot(task_id=parent.id, version=1, plan_hash=parent.plan_hash, proposal_json=proposal.model_dump(), plan_json=plan))
    session.add(RuntimeTaskRequest(sandbox_id=principal.sandbox_id, user_id=principal.user_id, idempotency_key=key,
                                  request_hash=request_hash, task_id=parent.id))
    from .runtime import refresh_parent
    refresh_parent(session, parent, prepare_ready=False)
    from .audit import append_audit
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=parent.id, event_type="AA_PARTICIPANT_PAYMENT_PROPOSED", payload=binding)
    session.flush()
    return parent


def prepare_aa_payment_child(session, *, task, conversation_id, proposed_action, source_context):
    """Compile one verified AA_PAY operation into its ordinary transfer child.

    Caller is the existing runtime domain compiler, not a client-authority flag.
    The exact amount and receiver come only from the owned invitation database.
    """
    from .services import Principal
    from .aa_obligations import owned_obligation
    from .runtime_models import RuntimeOperation, RuntimeRecord
    from .payment_instruments import resolve_source_instrument, validate_recipient_snapshot
    from .transfer_payment_scope import selected_source_reference, recipient_card_number
    from .internal_banking import resolve_transfer_payees, lock_transfer_accounts
    principal = Principal(task.user_id, task.sandbox_id)
    values = (proposed_action or {}).get("slots") or {}
    identity = owned_obligation(session, principal, values.get("aa_obligation_ref"))
    obligation=session.get(AAParticipant,identity.participant_id)
    if (values.get('amount_minor') is not None and values['amount_minor']!=obligation.amount_minor) or values.get('currency') not in (None,'CNY'):
        task.result_json={'error':'AA_AMOUNT_MISMATCH','message':f'这笔AA登记的本人应付金额是{obligation.amount_minor/100:.2f}元人民币，与你说的不一致。请核对要支付哪笔；尚未付款。',
            'amount_minor':obligation.amount_minor,'currency':'CNY','bank_state_changed':False}
        return task
    _owned_invitation(session, principal, identity.request_id, identity.participant_id)
    operation = session.get(RuntimeOperation, (source_context or {}).get("source_operation_id"))
    parent = session.get(Task, (source_context or {}).get("source_root_task_id"))
    if (not operation or not parent or operation.parent_task_id != parent.id or operation.action_kind != "AA_PAY"
            or (parent.user_id, parent.sandbox_id) != (task.user_id, task.sandbox_id) or not operation.active):
        raise DomainError("AA_RUNTIME_OPERATION_REQUIRED", "AA付款需要当前本人已核验语义操作来源。", 409)
    request, terms = _active_terms(session, identity.request_id, lock=False)
    source = resolve_source_instrument(session, principal, selected_source_reference(session, principal, values), "CNY", purpose="TRANSFER")
    recipient = validate_recipient_snapshot(session, terms.collector_scope_json, lock=False)
    reference = recipient_card_number(session, recipient)
    payee = resolve_transfer_payees(session, task.sandbox_id, task.user_id, reference)[0]
    lock_transfer_accounts(session, task.sandbox_id, task.user_id, source["account_id"], payee)
    request, terms = _active_terms(session, identity.request_id)
    identity = _owned_invitation(session, principal, identity.request_id, identity.participant_id, lock=True)
    if identity.status != "PENDING":
        previous = session.get(Task, identity.payment_task_id) if identity.payment_task_id else None
        close_expired_payment_attempt(session,principal,previous)
        if identity.status != "PAYMENT_PENDING" or not previous or previous.state not in {"FAILED", "CLOSED"}:
            raise DomainError("AA_PARTICIPANT_NOT_PAYABLE", "该份额已付款、结束或已有付款任务，请查看原记录。", 409)
    participant = session.get(AAParticipant, identity.participant_id)
    identity.status, identity.version = "PAYMENT_PENDING", identity.version + 1
    identity.payment_task_id, identity.payment_business_key = parent.id, operation.idempotency_key
    from .access_models import LoginAccount
    actor_login = session.get(LoginAccount, identity.login_account_id)
    binding = {"request_id": request.id, "participant_id": participant.id, "identity_version": identity.version,
        "terms_version": terms.version, "amount_minor": participant.amount_minor, "recipient_account_id": request.account_id,
        "parent_task_id": parent.id, "collector_scope_hash": terms.collector_scope_json["scope_hash"],
        "payer_login_version": actor_login.version}
    record = session.get(RuntimeRecord, parent.id)
    if not record:
        raise DomainError("AA_RUNTIME_OPERATION_REQUIRED", "AA付款运行时来源缺失。", 409)
    record.metadata_json = {**record.metadata_json, "aa_payment_bindings": {
        **record.metadata_json.get("aa_payment_bindings", {}), operation.id: binding}}
    from .transfers import prepare_transfer_task
    derived = {"kind": "TRANSFER", "slots": {"payee_name": reference, "amount_minor": participant.amount_minor,
        "currency": "CNY", "card_number": source["card_number"], "memo": "AA份额付款"}}
    prepare_transfer_task(session, task=task, conversation_id=conversation_id, proposed_action=derived, source_context=source_context)
    if task.fic_json and task.state == "AWAITING_CONFIRMATION":
        fic = deepcopy(task.fic_json)
        for field in ("payee_name", "amount_minor", "memo"):
            fic["slots"][field]["source"] = "OWNED_REGISTERED_AA_OBLIGATION"
        task.fic_json = fic
    return task


def validate_aa_bank_posting(session, source, payee, amount_minor, business_key):
    identity = session.scalar(select(AAParticipantIdentity).where(AAParticipantIdentity.payment_business_key == business_key))
    if identity is None:
        return None
    request, terms = _active_terms(session, identity.request_id)
    identity = session.scalar(select(AAParticipantIdentity).where(AAParticipantIdentity.participant_id == identity.participant_id).with_for_update())
    participant = session.get(AAParticipant, identity.participant_id)
    from .runtime_models import RuntimeRecord, RuntimeOperation
    record = session.get(RuntimeRecord, identity.payment_task_id)
    operation = session.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == identity.payment_task_id,
        RuntimeOperation.idempotency_key == business_key, RuntimeOperation.active == 1))
    binding = ((record.metadata_json or {}).get("aa_payment_bindings", {}).get(operation.id)
               if record and operation else None) or ((record.metadata_json or {}).get("aa_payment_binding") if record else None)
    from .internal_banking import recipient_scope
    from .payment_instruments import validate_recipient_snapshot
    from .access_models import LoginAccount
    payer = session.get(LoginAccount, identity.login_account_id)
    if (not binding or not operation or operation.status not in {"RUNNING", "AUTHORIZED"}
            or identity.status != "PAYMENT_PENDING" or participant.status != "PENDING"
            or payer is None or payer.status != "ACTIVE" or payer.role not in {"CUSTOMER", "DEV_TEST"}
            or payer.version != binding.get("payer_login_version")
            or (source.user_id, source.sandbox_id) != (identity.user_id, identity.sandbox_id)
            or (binding["identity_version"], binding["terms_version"], binding["amount_minor"], binding["collector_scope_hash"])
                != (identity.version, terms.version, amount_minor, terms.collector_scope_json["scope_hash"])
            or participant.amount_minor != amount_minor or recipient_scope(session, payee)["recipient_account_id"] != request.account_id):
        raise DomainError("AA_PAYMENT_SCOPE_CHANGED", "AA本人份额、收款范围或付款授权已经变化，未扣款。", 409)
    validate_recipient_snapshot(session, terms.collector_scope_json, lock=True)
    return identity, participant, request, terms


def finish_aa_bank_posting(session, verified, transfer):
    if verified is None:
        return
    identity, participant, request, terms = verified
    identity.status, identity.transfer_id = "PAID", transfer.id
    participant.status, participant.paid_at = "PAID", utcnow()
    session.flush()
    others = list(session.scalars(select(AAParticipantIdentity).where(AAParticipantIdentity.request_id == request.id)))
    if all(item.status == "PAID" for item in others):
        request.status, terms.status = "COMPLETED", "COMPLETED"
    elif any(item.status == "PAID" for item in others):
        request.status = "PARTIAL"
    task = session.get(Task, request.task_id)
    if task:
        from .banking_extensions import aa_request_view
        task.result_json = {**task.result_json, **aa_request_view(session, request),
                           "message": "AA已全部实际到账。" if request.status == "COMPLETED" else "AA已有实际到账，其他份额尚未完成。"}
        task.version += 1


def completed_aa_payment_receipt(session, parent, operation, child, call, confirmation):
    """Verified actual amount for phase/presentation, never old AA_PAY slots."""
    try:
        from .models import Transfer
        from .internal_banking_models import InternalTransferSettlement
        from .aa_obligation_identity import _same_parent_binding, _completed_by_same_parent
        from .services import Principal
        if (operation.action_kind not in {"AA_PAY", "TRANSFER"} or operation.parent_task_id != parent.id or operation.child_task_id != child.id
                or parent.state in {"UNKNOWN", "FAILED", "CLOSED"} or operation.status != "SUCCEEDED"
                or (parent.user_id, parent.sandbox_id) != (child.user_id, child.sandbox_id)
                or call.task_id != child.id or call.tool_name != "transfer.commit.v1" or call.status != "SUCCESS"
                or call.idempotency_key != operation.idempotency_key or confirmation.task_id != child.id
                or confirmation.action_type != "transfer.commit.v1" or confirmation.status != "CONSUMED"):
            return None
        identity = session.scalar(select(AAParticipantIdentity).where(
            AAParticipantIdentity.payment_business_key == operation.idempotency_key,
            AAParticipantIdentity.payment_task_id == parent.id, AAParticipantIdentity.status == "PAID"))
        if identity is None:
            return None
        participant = session.get(AAParticipant, identity.participant_id)
        request = session.get(AACollectionRequest, identity.request_id)
        terms = session.get(AARequestTerms, identity.request_id)
        if participant is None or request is None or terms is None:
            return None
        _validate_collection_origin(session, request, terms)
        checked_operation, binding = _same_parent_binding(session, Principal(parent.user_id, parent.sandbox_id), identity, parent.id)
        if checked_operation.id != operation.id or binding.get("amount_minor") != participant.amount_minor:
            return None
        _completed_by_same_parent(session, Principal(parent.user_id, parent.sandbox_id), identity, participant, request, operation)
        transfer = session.get(Transfer, identity.transfer_id)
        settlement = session.get(InternalTransferSettlement, transfer.id)
        scope = next(row for row in child.plan_json["nodes"] if row.get("node_id") == "commit-transfer")
        return {"action_kind": "AA_PAY", "status": "POSTED", "obligation_ref": "aa-due:" + participant.id,
            "request_id": request.id, "transfer_id": transfer.id, "amount_minor": transfer.amount_minor, "currency": "CNY",
            "source_account_id": transfer.source_account_id, "recipient_account_id": settlement.receiver_account_id,
            "collector_username": settlement.receiver_username,
            "source_card_display_last4": (scope.get("source_card_context") or {}).get("display_last4"),
            "recipient_card_display_last4": terms.collector_scope_json.get("display_last4"),
            "bank_state_changed": True, "authority": "VERIFIED_PARTICIPANT_AA_TRANSFER_RECEIPT"}
    except (DomainError, ValueError, TypeError, KeyError, AttributeError, StopIteration):
        return None


def decline_invitation(session, principal, request_id, participant_id, expected_version):
    _owned_invitation(session, principal, request_id, participant_id)
    _active_terms(session, request_id)
    identity = _owned_invitation(session, principal, request_id, participant_id, lock=True)
    if identity.version != expected_version or identity.status not in {"PENDING", "PAYMENT_PENDING"}:
        raise DomainError("AA_PARTICIPANT_STATE_CHANGED", "该份额状态已变化，不能拒绝已完成的付款。", 409)
    identity.status, identity.version = "DECLINED", identity.version + 1
    session.get(AAParticipant, participant_id).status = "DECLINED"
    return {"request_id": request_id, "participant_id": participant_id, "status": "DECLINED", "version": identity.version}


def cancel_collection(session, principal, request_id, expected_version):
    request = session.get(AACollectionRequest, request_id)
    if request is None or (request.user_id, request.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError("AA_REQUEST_NOT_FOUND", "AA请求不存在。", 404)
    request, terms = _active_terms(session, request_id)
    if terms.version != expected_version:
        raise DomainError("AA_REQUEST_VERSION_CHANGED", "AA请求版本已变化。", 409)
    terms.status, terms.version, request.status = "CANCELLED", terms.version + 1, "CANCELLED"
    for identity in session.scalars(select(AAParticipantIdentity).where(AAParticipantIdentity.request_id == request_id).with_for_update()):
        if identity.status in {"PENDING", "PAYMENT_PENDING"}:
            identity.status, identity.version = "CANCELLED", identity.version + 1
            session.get(AAParticipant, identity.participant_id).status = "CANCELLED"
    return {"request_id": request_id, "status": "CANCELLED", "version": terms.version,
            "message": "未付份额已取消，已到账付款不撤回。"}


def expire_collections(session):
    terms_rows = list(session.scalars(select(AARequestTerms).where(AARequestTerms.status == "ACTIVE",
        AARequestTerms.expires_at <= utcnow()).limit(50).with_for_update(skip_locked=True)))
    for terms in terms_rows:
        terms.status, terms.version = "EXPIRED", terms.version + 1
        request = session.get(AACollectionRequest, terms.request_id)
        request.status = "EXPIRED"
        for identity in session.scalars(select(AAParticipantIdentity).where(
                AAParticipantIdentity.request_id == request.id).with_for_update()):
            if identity.status in {"PENDING", "PAYMENT_PENDING"}:
                identity.status, identity.version = "EXPIRED", identity.version + 1
                session.get(AAParticipant, identity.participant_id).status = "EXPIRED"
    return len(terms_rows)
