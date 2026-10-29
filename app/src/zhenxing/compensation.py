"""Receipt-bound proposals for reversing completed temporary card state changes."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit
from .contracts import DomainError
from .intelligence import ActionSlots, IntentProposal, ProposedAction, canonical_action_message
from .models import Card, Task, ToolCall
from .planning import canonical_hash
from .runtime_models import RuntimeOperation, RuntimeRecord
from .services import owned_task


REVERSES = {"CARD_LOCK": ("CARD_UNLOCK", "LOCKED"), "CARD_UNLOCK": ("CARD_LOCK", "ACTIVE")}


def _source_receipt(session, principal, source: Task, operation_id: str):
    operation = session.scalar(select(RuntimeOperation).where(
        RuntimeOperation.id == operation_id, RuntimeOperation.parent_task_id == source.id,
        RuntimeOperation.sandbox_id == principal.sandbox_id).with_for_update())
    if operation is None:
        raise DomainError("COMPENSATION_OPERATION_NOT_FOUND", "未找到属于该任务的操作", 404)
    if operation.action_kind not in REVERSES:
        raise DomainError("COMPENSATION_NOT_SUPPORTED", "本次仅支持恢复临时锁卡或解锁，不撤回资金、挂失或订阅操作", 409)
    if operation.status != "SUCCEEDED" or operation.child_task_id is None:
        raise DomainError("COMPENSATION_SOURCE_NOT_SUCCEEDED", "只有已核验成功的卡片操作可以申请恢复", 409)
    child = owned_task(session, principal, operation.child_task_id)
    if child.state != "SUCCEEDED":
        raise DomainError("COMPENSATION_SOURCE_NOT_SUCCEEDED", "原操作的业务状态未核验成功", 409)
    calls = list(session.scalars(select(ToolCall).where(
        ToolCall.task_id == child.id, ToolCall.sandbox_id == principal.sandbox_id,
        ToolCall.idempotency_key == operation.idempotency_key,
        ToolCall.tool_name.in_(["card.manage.v1", "card.lock.v1"]), ToolCall.status == "SUCCESS")))
    if len(calls) != 1:
        raise DomainError("COMPENSATION_RECEIPT_INVALID", "原操作缺少唯一成功业务回执", 409)
    receipt = calls[0].result_json
    expected_status = REVERSES[operation.action_kind][1]
    if receipt.get("card_status") != expected_status or not receipt.get("card_id") \
            or not isinstance(receipt.get("card_version"), int):
        raise DomainError("COMPENSATION_RECEIPT_INVALID", "原操作的卡片回执无效", 409)
    return operation, calls[0], receipt


def _card_at_receipt(session, principal, receipt):
    card = session.scalar(select(Card).where(Card.id == receipt["card_id"],
        Card.sandbox_id == principal.sandbox_id, Card.user_id == principal.user_id).with_for_update())
    if card is None or card.version != receipt["card_version"] or card.status != receipt["card_status"]:
        raise DomainError("COMPENSATION_CARD_CHANGED", "卡片在原操作后已有变化，请基于当前状态发起新的卡片任务", 409)
    return card


def propose_compensation(session: Session, principal, source_task_id: str,
                         operation_id: str, expected_task_version: int) -> Task:
    """Caller commits; the source parent row serializes concurrent requests."""
    source = owned_task(session, principal, source_task_id, lock=True)
    source_record = session.get(RuntimeRecord, source.id)
    if source_record is None or source_record.kind != "PARENT":
        raise DomainError("COMPENSATION_PARENT_REQUIRED", "请从持久父任务选择已完成操作", 409)
    bindings = dict(source_record.metadata_json.get("compensations", {}))
    binding = bindings.get(operation_id)
    if binding:
        if binding["source_task_version"] != expected_task_version:
            raise DomainError("TASK_VERSION_CHANGED", "补偿请求版本与原请求不一致，请刷新任务", 409)
        return owned_task(session, principal, binding["task_id"])
    if source.version != expected_task_version:
        raise DomainError("TASK_VERSION_CHANGED", "原任务版本已变化，请刷新任务", 409)
    operation, call, receipt = _source_receipt(session, principal, source, operation_id)
    card = _card_at_receipt(session, principal, receipt)
    inverse = REVERSES[operation.action_kind][0]
    action = ProposedAction(kind=inverse, slots=ActionSlots(card_last4=card.last4),
                            label="恢复原临时卡片状态")
    proposal = IntentProposal(summary=f"恢复尾号{card.last4}的临时卡片状态，需要独立确认",
        actions=[action], model_label="server-compensation-v1", fallback=False)
    # Import lazily to keep the API/module graph acyclic; this only enqueues PLAN.
    from .runtime import accept_task
    task = accept_task(session, principal, canonical_action_message(action))
    record = session.get(RuntimeRecord, task.id)
    trusted = {"proposal": proposal.model_dump(), "source_task_id": source.id,
        "source_operation_id": operation.id, "source_child_task_id": operation.child_task_id,
        "source_task_version": source.version, "source_tool_call_id": call.id,
        "card_id": card.id, "expected_card_version": card.version,
        "expected_card_status": card.status, "original_receipt_hash": canonical_hash(receipt),
        "canonical_message": task.message}
    record.metadata_json = {**record.metadata_json, "trusted_compensation": trusted}
    bindings[operation.id] = {"task_id": task.id, "source_task_version": source.version}
    source_record.metadata_json = {**source_record.metadata_json, "compensations": bindings}
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=source.id,
                 event_type="COMPENSATION_PROPOSED", payload={"operation_id": operation.id,
                     "compensation_task_id": task.id, "receipt_hash": trusted["original_receipt_hash"]})
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=task.id,
                 event_type="COMPENSATION_SOURCE_BOUND", payload={"source_task_id": source.id,
                     "source_operation_id": operation.id, "card_id": card.id,
                     "expected_card_version": card.version})
    session.flush()
    return task


def validate_trusted_compensation(session: Session, task: Task) -> IntentProposal:
    """Recheck receipt and live card before runtime compiles the server proposal."""
    record = session.get(RuntimeRecord, task.id)
    trusted = record.metadata_json.get("trusted_compensation") if record else None
    if not trusted or record.kind != "PARENT":
        raise DomainError("COMPENSATION_BINDING_MISSING", "补偿任务缺少原回执绑定", 409)
    if task.message != trusted["canonical_message"] or record.metadata_json.get("input_revision", 0) != 0:
        raise DomainError("COMPENSATION_PLAN_FIXED", "恢复提案已绑定原卡片操作；请取消后另行提出新任务", 409)
    from .services import Principal
    principal = Principal(task.user_id, task.sandbox_id)
    source = owned_task(session, principal, trusted["source_task_id"])
    operation, call, receipt = _source_receipt(session, principal, source, trusted["source_operation_id"])
    if call.id != trusted["source_tool_call_id"] or canonical_hash(receipt) != trusted["original_receipt_hash"] \
            or operation.child_task_id != trusted["source_child_task_id"] \
            or receipt["card_id"] != trusted["card_id"] \
            or receipt["card_version"] != trusted["expected_card_version"] \
            or receipt["card_status"] != trusted["expected_card_status"]:
        raise DomainError("COMPENSATION_RECEIPT_CHANGED", "原操作回执或补偿绑定发生变化", 409)
    card = _card_at_receipt(session, principal, receipt)
    proposal = IntentProposal.model_validate(trusted["proposal"])
    inverse = REVERSES[operation.action_kind][0]
    if len(proposal.actions) != 1 or proposal.actions[0].kind != inverse \
            or proposal.actions[0].slots.model_dump(exclude_none=True) != {"card_last4": card.last4} \
            or proposal.actions[0].depends_on or proposal.clarification_questions:
        raise DomainError("COMPENSATION_PLAN_INVALID", "恢复提案与原卡片操作不一致", 409)
    return proposal
