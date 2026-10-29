"""Standalone personal identity binding, using the same confirmation kernel."""
from datetime import timedelta

from .audit import append_audit
from .contacts import commit_alias_proposal, prepare_alias_proposal, validate_alias_proposal
from .contracts import DomainError
from .models import Confirmation, EvidenceSnapshot, OutboxMessage, ToolCall, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .workflow import finish_node, transition_task


def prepare_contact_task(session, task, action, context=None):
    if action.get("kind") != "CONTACT_BIND" or task.intent_type != "CONTACT_MANAGEMENT":
        raise DomainError("CONTACT_ACTION_INVALID", "当前任务没有联系人绑定能力", 403)
    try:
        proposal = prepare_alias_proposal(session, task, action, context)
        if not proposal:
            raise DomainError("CONTACT_ROLE_REQUIRED", "请明确收款账号及其在你这里的身份称呼", 422)
    except DomainError as error:
        task.result_json = {"message": error.message, "error": error.code}
        if error.status_code != 422:
            transition_task(task, "FAILED")
        return task
    node = {"node_id": "contact-bind", "tool_name": "contact.bind.v1", "read_only": False,
        "requires_confirmation": True, "alias_binding": proposal}
    task.plan_json = {"task_id": task.id, "policy_version": "personal-contact-v1", "nodes": [node]}
    task.plan_hash = canonical_hash(task.plan_json)
    task.fic_json = {**task.fic_json, "status": "READY", "requested_capabilities": ["contact.bind.v1"],
        "slots": {key: {"value": value, "source": "USER_TEXT"} for key, value in (action.get("slots") or {}).items() if value is not None}}
    task.result_json = {"message": f"拟把你自己的‘{proposal['alias_name']}’收款身份绑定到{proposal['recipient_username']}；确认后保存，不转账、不修改对方姓名。",
        "alias_binding": proposal, "funds_changed": False}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key="contact-bind", decision="REQUIRE_CONFIRMATION",
        reason_codes=["PERSONAL_RECIPIENT_MEMORY", "EXPLICIT_REFERENCE_AND_ROLE", "VERSIONED_CHANGE"], policy_version="personal-contact-v1")
    session.add(EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id, source_kind="PERSONAL_CONTACT_PROPOSAL",
        source_version="personal-contact-v1", payload_json=proposal))
    transition_task(task, "AWAITING_CONFIRMATION")
    session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
        node_key="contact-bind", action_type="contact.bind.v1", target_id=proposal["proposal_id"],
        plan_hash=task.plan_hash, view_hash=canonical_hash({"plan_hash": task.plan_hash, "alias_binding": proposal}),
        expires_at=utcnow() + timedelta(minutes=5)))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="CONTACT_ALIAS_PROPOSED",
        payload={"alias_name": proposal["alias_name"], "recipient_username": proposal["recipient_username"], "effective": False})
    session.flush()
    return task


def dispatch_contact_task(session, task, principal, request, key):
    from .tools import ToolGateway
    request_hash, node, confirmation, duplicate = ToolGateway().authorize_write(session, task=task,
        sandbox_id=principal.sandbox_id, user_id=principal.user_id, request=request,
        idempotency_key=key, tool_name="contact.bind.v1", node_key="contact-bind")
    if duplicate:
        return task
    proposal = task.plan_json["nodes"][0]["alias_binding"]
    if confirmation.target_id != proposal["proposal_id"] or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "alias_binding": proposal}):
        raise DomainError("CONTACT_CONFIRMATION_CHANGED", "联系人确认范围已变化", 409)
    validate_alias_proposal(session, task, proposal)
    result = commit_alias_proposal(session, task, proposal)
    task.result_json = {"message": f"已将你的‘{result['alias_name']}’收款身份保存为{result['recipient_username']}。未执行转账。",
        "contact_alias": result, "funds_changed": False}
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    finish_node(session, task, node, task.result_json)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name="contact.bind.v1",
        idempotency_key=key, request_hash=request_hash, status="SUCCESS", result_json=task.result_json))
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED", payload_json={"task_id": task.id}))
    session.flush()
    return task

