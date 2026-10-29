"""Annotate server-bound inherited slots; never replace values or plan hashes."""
from copy import deepcopy
from dataclasses import dataclass
from sqlalchemy import select

from .conversation_models import ChatThread, ChatTurn
from .planning import canonical_hash
from .contracts import DomainError
from .intelligence import READ_KINDS, ActionSlots, IntentProposal

_EXECUTION_AUTHORITY = object()


def validate_dialogue_actor_epoch(session,parent,metadata):
    """Password/role/delegation changes cannot grandfather a chat plan."""
    expected=metadata.get("dialogue_actor_scope_hash")
    origin=session.get(ChatTurn,(metadata.get("conversation_context") or {}).get("turn_id"))
    if origin and (origin.diagnostics_json or {}).get("dialogue_actor_scope_hash") and expected!=(origin.diagnostics_json or {})["dialogue_actor_scope_hash"]:
        raise DomainError("CONVERSATION_SCOPE_CHANGED","会话的身份来源已变化，请重新核对方案。",409)
    if not expected:
        return  # Historical plans retain their existing stronger per-domain gates.
    from .dialogue_state import dialogue_actor_scope
    from .services import Principal
    principal=Principal(parent.user_id,parent.sandbox_id,family_context=metadata.get("family_context_origin"))
    _,current=dialogue_actor_scope(session,principal)
    if current!=expected:
        raise DomainError("CONVERSATION_SCOPE_CHANGED","账户或授权已经变化，请重新准备并核对方案。",409)


def current_plan_completed_write(session,parent,operation):
    """A verified earlier step of this same plan is not an old-plan replay."""
    from .runtime_models import RuntimeRecord,PlanSnapshot
    from .models import Task,ToolCall,Confirmation
    from .intelligence import action_signature
    record=session.get(RuntimeRecord,parent.id)
    if not record or operation.action_kind in READ_KINDS or not operation.active or operation.status!="SUCCEEDED" \
        or operation.parent_task_id!=parent.id or operation.sandbox_id!=parent.sandbox_id or operation.plan_version!=record.plan_version:
        return False
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==record.plan_version))
    node=next((item for item in (parent.plan_json or {}).get("nodes",[]) if item.get("node_id")==operation.operation_key),None)
    child=session.get(Task,operation.child_task_id)
    child_record=session.get(RuntimeRecord,operation.child_task_id)
    if not snapshot or snapshot.plan_hash!=parent.plan_hash or canonical_hash(snapshot.plan_json)!=parent.plan_hash \
        or canonical_hash(parent.plan_json)!=parent.plan_hash or not node or node.get("kind")!=operation.action_kind \
        or canonical_hash(node.get("args"))!=canonical_hash(operation.args_json) \
        or canonical_hash(action_signature(operation.args_json))!=operation.fingerprint \
        or not child or child.state!="SUCCEEDED" or (child.user_id,child.sandbox_id)!=(parent.user_id,parent.sandbox_id) \
        or canonical_hash(child.plan_json)!=child.plan_hash or not child_record or child_record.root_task_id!=parent.id \
        or child_record.kind!="CHILD" or child_record.metadata_json.get("operation_id")!=operation.id:
        return False
    call=session.scalar(select(ToolCall).where(ToolCall.task_id==child.id,ToolCall.status=="SUCCESS",
        ToolCall.idempotency_key==operation.idempotency_key))
    confirmation=session.scalar(select(Confirmation).where(Confirmation.task_id==child.id,
        Confirmation.user_id==parent.user_id,Confirmation.sandbox_id==parent.sandbox_id,
        Confirmation.plan_hash==child.plan_hash,Confirmation.status=="CONSUMED",Confirmation.consumed_at.is_not(None)))
    if not call or not confirmation:return False
    if not record.metadata_json.get('cloud_semantic_intent'):
        # Preserve historical per-domain continuation checks. Native semantics
        # additionally requires the matched domain posting proof below.
        return True
    from .semantic_phase_context import completed_write_receipt_integrity
    return completed_write_receipt_integrity(session,parent,operation,child,call,confirmation)


@dataclass(frozen=True)
class _DialogueExecutionContext:
    task_id: str
    user_id: str
    sandbox_id: str
    task_version: int
    plan_hash: str
    authority: object

    def permits(self, parent):
        return self.authority is _EXECUTION_AUTHORITY and (self.task_id, self.user_id, self.sandbox_id,
            self.task_version, self.plan_hash) == (parent.id, parent.user_id, parent.sandbox_id,
            parent.version, parent.plan_hash) and parent.state == "EXECUTING"


def dialogue_authorization_binding(session, parent, job, operation, request):
    """Private accepted request binding, not an authorization or a client flag."""
    from .access_models import LoginAccount
    from .runtime_models import RuntimeRecord
    from .permissions import actor_role
    account = session.scalar(select(LoginAccount).where(
        LoginAccount.user_id == parent.user_id, LoginAccount.sandbox_id == parent.sandbox_id))
    record = session.get(RuntimeRecord, parent.id)
    return {"version": "dialogue-execution-binding-v1", "job_id": job.id, "operation_id": operation.id,
        "request_hash": canonical_hash(request.model_dump()), "parent_plan_hash": parent.plan_hash,
        "parent_task_version": parent.version, "parent_plan_version": operation.plan_version,
        "input_revision": record.metadata_json.get("input_revision", 0),
        "actor_role": actor_role(session, parent.sandbox_id, parent.user_id),
        "actor_login_id": account.id if account else None, "actor_login_version": account.version if account else None}


def _validated_dialogue_execution(session, parent, execution_job):
    """Recheck the real pending grant before permitting execution-time reads.

    The bank kernel still consumes the grant and checks the monetary scope.
    Audit evidence is never substituted for that grant or confirmation.
    """
    from sqlalchemy import select
    from .authentication import as_utc, consume_auth
    from .contracts import ConfirmTaskRequest
    from .models import AuditEvent, Confirmation, Task, utcnow
    from .runtime_models import RuntimeOperation, RuntimeRecord, WorkflowJob
    def deny():
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "本次执行与已确认的会话方案不一致；未重复办理业务。", 409)
    if not isinstance(execution_job, WorkflowJob):
        deny()
    job = session.get(WorkflowJob, execution_job.id)
    if not job or job.kind != "EXECUTE" or job.status != "LEASED" or not job.lease_owner \
            or (job.task_id, job.sandbox_id) != (parent.id, parent.sandbox_id) or parent.state != "EXECUTING":
        deny()
    try:
        request = ConfirmTaskRequest.model_validate(job.payload_json.get("request"))
    except (ValueError, TypeError):
        deny()
    operation = session.get(RuntimeOperation, job.payload_json.get("operation_id"))
    record = session.get(RuntimeRecord, parent.id)
    if not operation or not record or record.kind != "PARENT" or not operation.active or operation.status != "AUTHORIZED" \
            or (operation.parent_task_id, operation.sandbox_id, operation.plan_version) != (parent.id, parent.sandbox_id, record.plan_version):
        deny()
    if any(item.id != operation.id and item.action_kind not in READ_KINDS
            and item.status in {"SUCCEEDED", "AUTHORIZED", "RUNNING", "UNKNOWN"}
            and not (item.ordinal<operation.ordinal and current_plan_completed_write(session,parent,item))
            for item in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id))):
        deny()
    binding = dialogue_authorization_binding(session, parent, job, operation, request)
    if job.payload_json.get("dialogue_authorization_binding") != binding:
        deny()
    child = session.get(Task, operation.child_task_id)
    child_record = session.get(RuntimeRecord, operation.child_task_id)
    confirmation = session.get(Confirmation, request.confirmation_id)
    if not child or not child_record or child_record.root_task_id != parent.id or child_record.kind != "CHILD" \
            or (child.user_id, child.sandbox_id) != (parent.user_id, parent.sandbox_id) \
            or child.state not in {"AWAITING_CONFIRMATION", "PARTIAL"} or child.version != request.expected_task_version \
            or child.plan_hash != request.plan_hash or canonical_hash(child.plan_json) != request.plan_hash \
            or not confirmation or (confirmation.task_id, confirmation.user_id, confirmation.sandbox_id) != (child.id, parent.user_id, parent.sandbox_id) \
            or confirmation.status != "PENDING" or confirmation.consumed_at is not None \
            or confirmation.plan_hash != request.plan_hash or as_utc(confirmation.expires_at) <= utcnow():
        deny()
    # This is the real verifier, with consume=False because the bank kernel
    # consumes atomically with the write later in this same transaction.
    consume_auth(session, task=child, confirmation=confirmation, challenge_id=request.auth_challenge_id,
        user_id=parent.user_id, consume=False)
    accepted = session.scalar(select(AuditEvent).where(AuditEvent.task_id == parent.id,
        AuditEvent.sandbox_id == parent.sandbox_id, AuditEvent.event_type == "ACTION_AUTHORIZED")
        .order_by(AuditEvent.seq.desc()).limit(1))
    from .execution_provenance import accepted_authorization
    evidence = accepted_authorization(session, parent, child, confirmation, operation, request)
    if not accepted or accepted.payload_json.get("dialogue_authorization_binding") != binding \
            or accepted.payload_json.get("operation_id") != operation.id \
            or evidence.get("scope_status") != "RECORDED" \
            or accepted.payload_json.get("authorization_provenance") != evidence:
        deny()
    return _DialogueExecutionContext(parent.id, parent.user_id, parent.sandbox_id, parent.version,
        parent.plan_hash, _EXECUTION_AUTHORITY)


def validate_continuation_proposal(session, parent, proposal, metadata):
    """A model cannot downgrade the user's newly bound amount/recipient scope."""
    from .conversation_service import validate_pending_subject_selection
    validate_pending_subject_selection(session, parent, metadata)
    context = metadata.get("conversation_context") or {}
    if context.get("authority") != "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS":
        return
    contract = context.get("continuation_contract")
    # Older pending continuation metadata is not silently upgraded to a new
    # authoritative contract. It must be explicitly replanned by the user.
    def deny():
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "续写提案与已核对的用户金额或对象不一致，请重新明确需求；未执行业务。", 409)
    if not isinstance(contract, dict) or contract.get("version") not in {"chat-continuation-v1", "chat-target-continuation-v2", "chat-memo-continuation-v1", "chat-quantity-replacement-v1"} or (
            context.get("resolved_message_hash") != canonical_hash({"message": parent.message})):
        deny()
    thread = session.get(ChatThread, context.get("conversation_id"))
    turn = session.get(ChatTurn, context.get("turn_id"))
    if not thread or not turn or (thread.sandbox_id, thread.user_id) != (parent.sandbox_id, parent.user_id) or (
            turn.sandbox_id, turn.user_id) != (parent.sandbox_id, parent.user_id) or (
            turn.thread_id != thread.id or turn.task_id != parent.id or turn.status != "ANSWERED"):
        deny()
    original_hashes = {canonical_hash({"message": item}) for item in metadata.get("messages", [])[:-1] if isinstance(item, str)}
    expected = contract.get("expected_slots")
    if contract.get("source_message_hash") not in original_hashes or type(contract.get("origin_task_version")) is not int or (
            contract["origin_task_version"] < 1 or contract.get("expected_write_count") != 1 or
            not isinstance(expected, dict) or not expected or not set(expected) <= set(ActionSlots.model_fields)):
        deny()
    writes = [action for action in proposal.actions if action.kind not in READ_KINDS]
    if len(writes) != 1 or writes[0].kind != contract.get("expected_action_kind"):
        deny()
    values = writes[0].slots.model_dump()
    if any(values.get(field) != value or type(values.get(field)) is not type(value) for field, value in expected.items()):
        deny()
    if contract["version"] in {"chat-target-continuation-v2", "chat-memo-continuation-v1", "chat-quantity-replacement-v1"}:
        # Target changes receive a strict contract: independently recompose the
        # original source with the persisted current turn, not a supplied value.
        from types import SimpleNamespace
        from .conversation_service import _short_recipient_continuation, _short_memo_continuation, _short_quantity_replacement
        originals = [item for item in metadata.get("messages", [])[:-1] if isinstance(item, str)
                     and canonical_hash({"message": item}) == contract["source_message_hash"]]
        reads = sorted([{"kind": action.kind, "slots": action.slots.model_dump(exclude_none=True)}
            for action in proposal.actions if action.kind in READ_KINDS], key=canonical_hash)
        memo = contract["version"] == "chat-memo-continuation-v1"
        quantity = contract["version"] == "chat-quantity-replacement-v1"
        if not originals or contract.get("changed_field") != ("quantity_axis" if quantity else "memo" if memo else "payee_name") or writes[0].slots.model_dump(exclude_none=True) != expected or reads != contract.get("expected_read_actions"):
            deny()
        recompose = _short_quantity_replacement if quantity else _short_memo_continuation if memo else _short_recipient_continuation
        recomposed = recompose(SimpleNamespace(id=parent.id, message=originals[-1],
            version=contract["origin_task_version"]), turn.message, turn.id)
        comparison_contract = {key:value for key,value in contract.items() if key not in {"origin_snapshot_id","origin_snapshot_hash","source_refs","source_actor_role"}} if quantity else contract
        if not recomposed or recomposed[0] != parent.message or recomposed[2] != comparison_contract or recomposed[1] != context.get("field_sources"):
            deny()
        if quantity:
            from .runtime_models import PlanSnapshot, RuntimeOperation
            from .models import Task
            from .intelligence import wealth_units_in_text
            from .permissions import actor_role
            from sqlalchemy import select
            # A formerly valid role is not silently grandfathered into a new
            # authorization. These are server-persisted submission roles, not
            # model slots; old contracts without them require fresh planning.
            role = actor_role(session, parent.sandbox_id, parent.user_id)
            if role not in {"CUSTOMER", "DEV_TEST", "DEMO_OWNER", "OWNER"} or contract.get("source_actor_role") != role or turn.diagnostics_json.get("actor_role_at_submission") != role:
                deny()
            snapshot = session.get(PlanSnapshot, contract.get("origin_snapshot_id"))
            if not snapshot or snapshot.task_id != parent.id or canonical_hash(snapshot.proposal_json) != contract.get("origin_snapshot_hash"):
                deny()
            previous = IntentProposal.model_validate(snapshot.proposal_json).actions
            if len(previous) != 1 or previous[0].kind != writes[0].kind or previous[0].slots.product_name != expected.get("product_name") or previous[0].slots.units != contract.get("removed_units") or previous[0].slots.amount_minor is not None:
                deny()
            refs = contract.get("source_refs")
            if not isinstance(refs,list) or not 1 <= len(refs) <= 8:
                deny()
            messages=[]; sequence=0
            for ref in refs:
                source=session.get(ChatTurn,ref.get("turn_id"))
                if not source or (source.thread_id,source.task_id,source.user_id,source.sandbox_id)!=(thread.id,parent.id,parent.user_id,parent.sandbox_id) or source.status!='ANSWERED' or not sequence < source.sequence < turn.sequence or source.diagnostics_json.get('input_redacted') or source.diagnostics_json.get('actor_role_at_submission')!=role or ref.get('message_hash')!=canonical_hash({'message':source.message}):
                    deny()
                sequence=source.sequence; messages.append(source.message)
            if not any(expected['product_name'] in source for source in messages) or not any(contract['removed_units'] in wealth_units_in_text(source) for source in messages):
                deny()
            # Current-plan authorization is checked by the normal runtime.
            # Reject any settled/unknown old-plan effect; do not veto the new
            # plan's legitimate EXECUTING phase after its own confirmation.
            if parent.state in {'SUCCEEDED','CLOSED','FAILED','UNKNOWN'} or any(op.status in {'SUCCEEDED','AUTHORIZED','RUNNING','UNKNOWN'} or op.child_task_id and (child:=session.get(Task,op.child_task_id)) and child.state in {'SUCCEEDED','EXECUTING','AUTHORIZED','UNKNOWN'} for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,RuntimeOperation.plan_version <= snapshot.version,RuntimeOperation.action_kind.not_in(READ_KINDS)))):
                deny()


def annotate_child_slots(session, parent, child, context):
    if context.get("authority") != "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS" or (
            context.get("resolved_message_hash") != canonical_hash({"message": parent.message})):
        return False
    thread = session.get(ChatThread, context.get("conversation_id"))
    turn = session.get(ChatTurn, context.get("turn_id"))
    if not thread or not turn or (thread.sandbox_id, thread.user_id) != (parent.sandbox_id, parent.user_id) or (
            turn.thread_id != thread.id or turn.task_id != parent.id or turn.status != "ANSWERED" or
            (turn.sandbox_id, turn.user_id) != (parent.sandbox_id, parent.user_id)) or (
            child.sandbox_id, child.user_id) != (parent.sandbox_id, parent.user_id):
        return False
    fic = deepcopy(child.fic_json)
    changed = False
    for binding in context.get("field_sources", []):
        field = binding.get("field")
        slot = fic.get("slots", {}).get(field)
        sources = binding.get("sources") or []
        if binding.get("task_id") != parent.id or not slot or slot.get("value") != binding.get("value"):
            continue
        kinds = {item.get("source") for item in sources}
        if not kinds or not kinds <= {"USER_CONTEXT", "USER_CURRENT"}:
            continue
        slot.update(source="+".join(sorted(kinds)), sources=sources,
                    context_task_id=parent.id, context_turn_id=turn.id,
                    authority="SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS")
        changed = True
    if changed:
        child.fic_json = fic
    return changed


def validate_current_continuation(session, parent, *, execution_job=None, phase_context=None):
    from sqlalchemy import select
    from .runtime_models import RuntimeRecord,PlanSnapshot
    semantic_record=session.get(RuntimeRecord,parent.id)
    from .semantic_requirements import requires_semantic_proof
    if semantic_record and requires_semantic_proof(session,parent,semantic_record.metadata_json):
        if not semantic_record.metadata_json.get('cloud_semantic_intent'):
            raise DomainError('SEMANTIC_PROOF_REQUIRED','原需求的语义来源需要重新核对。',409)
        from .semantic_proposal import validate_semantic_proposal
        semantic_snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,
            PlanSnapshot.version==semantic_record.plan_version))
        if not semantic_snapshot or semantic_snapshot.plan_hash!=parent.plan_hash:
            raise DomainError('SEMANTIC_SOURCE_CHANGED','原方案来源已变化，请重新核对。',409)
        context=_validated_dialogue_execution(session,parent,execution_job) if execution_job else None
        validate_semantic_proposal(session,parent,IntentProposal.model_validate(semantic_snapshot.proposal_json),
            semantic_record.metadata_json,execution_context=context,phase_context=phase_context)
        return
    """Confirm/execute must not grandfather an unchecked pending old revision."""
    from .runtime_models import RuntimeRecord, PlanSnapshot
    record = session.get(RuntimeRecord, parent.id)
    if record:
        validate_dialogue_actor_epoch(session,parent,record.metadata_json)
        from .conversation_service import validate_pending_subject_selection
        validate_pending_subject_selection(session, parent, record.metadata_json)
        authority=(record.metadata_json.get('conversation_context') or {}).get('authority')
        if authority=='CURRENT_USER_REQUEST_VERIFIED_RESULT_OBJECT' \
                and not record.metadata_json.get('dialogue_result_reference') \
                and not record.metadata_json.get('dialogue_slot_update'):
            raise DomainError('DIALOGUE_REFERENCE_SOURCE_CHANGED','所选对象的来源证明缺失，请重新查询并核对；不会办理。',409)
        if authority=='OWNED_USER_DIALOGUE' and not record.metadata_json.get('owned_user_dialogue') \
                and not record.metadata_json.get('dialogue_slot_update'):
            raise DomainError('CONVERSATION_SCOPE_CHANGED','原会话来源证明缺失，请重新核对；不会办理。',409)
    if record and record.metadata_json.get("dialogue_slot_update"):
        from .dialogue_slot_updates import validate_slot_update_proposal
        snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
            PlanSnapshot.version == record.plan_version))
        if not snapshot or snapshot.plan_hash != parent.plan_hash:
            raise DomainError("CONVERSATION_SCOPE_CHANGED", "续写计划已经变化，请核对最新方案。", 409)
        execution_context = _validated_dialogue_execution(session, parent, execution_job) if execution_job is not None else None
        validate_slot_update_proposal(session, parent, IntentProposal.model_validate(snapshot.proposal_json),
            record.metadata_json, execution_context=execution_context)
    if record and record.metadata_json.get("dialogue_result_reference") and not record.metadata_json.get("dialogue_slot_update"):
        from .dialogue_reference_integration import validate_result_reference_proposal
        snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,
            PlanSnapshot.version==record.plan_version))
        if not snapshot or snapshot.plan_hash != parent.plan_hash:
            raise DomainError("CONVERSATION_SCOPE_CHANGED", "所选对象或方案已变化，请核对最新结果。",409)
        execution_context = _validated_dialogue_execution(session,parent,execution_job) if execution_job is not None else None
        validate_result_reference_proposal(session,parent,IntentProposal.model_validate(snapshot.proposal_json),
            record.metadata_json,execution_context=execution_context)
    if record and record.metadata_json.get("owned_user_dialogue"):
        from .owned_user_dialogue import validate_dialogue_proposal
        snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
            PlanSnapshot.version == record.plan_version))
        if not snapshot or snapshot.plan_hash != parent.plan_hash:
            raise DomainError("CONVERSATION_SCOPE_CHANGED", "续写计划快照已变化，请重新核对当前目标；未执行业务。", 409)
        execution_context = _validated_dialogue_execution(session, parent, execution_job) if execution_job is not None else None
        validate_dialogue_proposal(session, parent, IntentProposal.model_validate(snapshot.proposal_json), record.metadata_json,
            execution_context=execution_context)
    if not record or (record.metadata_json.get("conversation_context") or {}).get("authority") != "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS":
        return
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
                                                       PlanSnapshot.version == record.plan_version))
    if not snapshot or snapshot.plan_hash != parent.plan_hash:
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "续写计划快照已变化，请重新核对当前目标；未执行业务。", 409)
    validate_continuation_proposal(session, parent, IntentProposal.model_validate(snapshot.proposal_json), record.metadata_json)
