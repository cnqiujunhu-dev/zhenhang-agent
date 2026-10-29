"""Confirmed birthday receipts identify a current object, never old authority.

Only the original successful, user-confirmed OCCASION_SET may offer this
identity.  Dates, payment instruments, budgets and confirmation grants are
deliberately absent; consumers must prepare a fresh owned-domain operation.
"""
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .models import Confirmation, Task, ToolCall
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

KIND = "COMPLETED_OCCASION_RECEIPT_IDENTITY"
MEMBER_KIND = "COMPLETED_FAMILY_MEMBER_RECEIPT_IDENTITY"
_CONSUMERS = frozenset({"OCCASION_VIEW", "OCCASION_SET", "LIFE_PLAN_AUTHORIZE"})


def _deny():
    raise DomainError("SEMANTIC_OCCASION_IDENTITY_SOURCE_CHANGED",
        "这份已保存生日资料的对象来源无法核验，请明确家人或重新查询当前生日资料。", 409)


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def _safe_label(name):
    from .semantic_router import _text
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 40
            or name != name.strip() or any(ord(char) < 32 for char in name)
            or re.search(r"\d{7,}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", name)):
        return None
    label = name + " · 已确认生日资料"
    try:
        _text(label, 160, private=True)
    except ValueError:
        return None
    return label


def occasion_identities(session, principal, thread, parent, before_sequence):
    """Return private identity bindings from a strictly earlier native receipt.

    The original receipt remains historical after a legitimate birthday edit.
    ``owned_occasion`` separately checks that the *current* owned object and its
    current confirmed source remain usable.  No current date is returned here.
    """
    try:
        return _occasion_identities(session, principal, thread, parent, before_sequence)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        _deny()


def member_identities(session, principal, thread, parent, before_sequence):
    try:
        return _occasion_identities(session, principal, thread, parent, before_sequence, member_profile=True)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        _deny()


def _occasion_identities(session, principal, thread, parent, before_sequence, *, member_profile=False):
    action = "OCCASION_MEMBER_SET" if member_profile else "OCCASION_SET"
    tool = "occasion.member.set.v1" if member_profile else "occasion.set.v1"
    thread = session.get(ChatThread, thread) if isinstance(thread, str) else thread
    parent = session.get(Task, parent) if isinstance(parent, str) else parent
    if (not _owned(thread, principal) or thread.status != "ACTIVE"
            or not _owned(parent, principal) or parent.state != "SUCCEEDED"
            or type(before_sequence) is not int or before_sequence <= 0):
        return []
    record = session.get(RuntimeRecord, parent.id)
    metadata = record.metadata_json if record else None
    native = metadata.get("cloud_semantic_intent") if isinstance(metadata, dict) else None
    # Do not grandfather an unproved legacy success or infer a receipt from
    # assistant text.  Navigation requires the original native source binding.
    if not record or record.kind != "PARENT" or not isinstance(native, dict):
        return []
    operations = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.parent_task_id == parent.id, RuntimeOperation.active == 1,
        RuntimeOperation.action_kind == action, RuntimeOperation.status == "SUCCEEDED")
        .order_by(RuntimeOperation.ordinal)))
    if not operations:
        return []  # Unrelated successful writes need no birthday-source audit.
    origin = session.get(ChatTurn, native.get("source_turn_id"))
    if (not _owned(origin, principal) or origin.thread_id != thread.id
            or origin.task_id != parent.id or origin.status != "ANSWERED"
            or type(origin.sequence) is not int or origin.sequence >= before_sequence):
        return []
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version))
    if (snapshot is None or snapshot.plan_hash != parent.plan_hash
            or canonical_hash(parent.plan_json) != parent.plan_hash
            or snapshot.plan_json != parent.plan_json):
        _deny()
    from .semantic_proposal import validate_semantic_source_snapshot
    from .runtime import validate_runtime_family_context
    validate_runtime_family_context(session, parent)
    validate_semantic_source_snapshot(session, principal, parent, metadata, snapshot)

    from .conversation_provenance import current_plan_completed_write
    from .life_write_receipts import completed_life_write_receipt_integrity
    from .occasion_service import owned_occasion
    from .extensions_models import FamilyMember
    from .permissions import actor_role
    out = []
    for operation in operations:
        child = session.get(Task, operation.child_task_id) if operation.child_task_id else None
        child_record = session.get(RuntimeRecord, operation.child_task_id) if child else None
        bound = [node for node in snapshot.plan_json.get("nodes", [])
            if isinstance(node, dict) and node.get("operation_id") == operation.id]
        if (operation.status != "SUCCEEDED" or operation.plan_version != record.plan_version
                or len(bound) != 1 or bound[0].get("node_id") != operation.operation_key
                or bound[0].get("kind") != action
                or bound[0].get("args") != operation.args_json
                or bound[0].get("depends_on") != operation.depends_on_json
                or not _owned(child, principal) or child.state != "SUCCEEDED"
                or not child_record or child_record.kind != "CHILD" or child_record.root_task_id != parent.id
                or child_record.metadata_json.get("operation_id") != operation.id
                or child_record.metadata_json.get("prepared_actor_role") != actor_role(session, principal.sandbox_id, principal.user_id)
                or not current_plan_completed_write(session, parent, operation)
                or not completed_life_write_receipt_integrity(session, principal, child, action)):
            _deny()
        calls = list(session.scalars(select(ToolCall).where(ToolCall.task_id == child.id,
            ToolCall.sandbox_id == principal.sandbox_id, ToolCall.tool_name == tool,
            ToolCall.status == "SUCCESS")))
        confirmations = list(session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
            Confirmation.sandbox_id == principal.sandbox_id, Confirmation.user_id == principal.user_id,
            Confirmation.action_type == tool, Confirmation.status == "CONSUMED")))
        if len(calls) != 1 or len(confirmations) != 1 or calls[0].idempotency_key != operation.idempotency_key:
            _deny()
        receipt = child.result_json["members" if member_profile else "occasions"][0]
        from .occasion_service import resolve_family_member
        from .family_profile_service import member_display_name
        current = resolve_family_member(session, principal, family_member_id=receipt["family_member_id"]) if member_profile else owned_occasion(session, principal, receipt["occasion_id"])
        name = receipt.get("family_member_name")
        if ((current.id if member_profile else current.family_member_id) != receipt.get("family_member_id")
                or not isinstance(name, str) or not name.strip()):
            _deny()
        member = current if member_profile else session.get(FamilyMember, current.family_member_id)
        label = _safe_label(name)
        if member is None or member_display_name(session, member) != name or label is None:
            continue  # Unsafe/renamed labels must not poison the model input.
        # Only hashes retain the historical payload; never expose its date,
        # budget, delivery details or authorization as a selectable fact.
        out.append({"source_kind": MEMBER_KIND if member_profile else KIND, "task_id": parent.id, "task_version": parent.version,
            "plan_version": record.plan_version, "snapshot_id": snapshot.id, "plan_hash": snapshot.plan_hash,
            "source_binding_hash": native["binding_hash"], "operation_id": operation.id,
            "child_task_id": child.id, "child_result_hash": canonical_hash(child.result_json),
            "confirmation_id": confirmations[0].id, "receipt_hash": canonical_hash({
                "confirmation_id": confirmations[0].id, "plan_hash": confirmations[0].plan_hash,
                "view_hash": confirmations[0].view_hash, "request_hash": calls[0].request_hash,
                "tool_call_id": calls[0].id, "result": calls[0].result_json}),
            **({"occasion_id": current.id} if not member_profile else {}), "family_member_id": member.id,
            "family_member_name": name, "label": label.replace("生日资料", "家庭资料") if member_profile else label,
            "conversation_id": thread.id, "before_sequence": before_sequence, "source_turn_id": origin.id,
            "field": "family_member_id" if member_profile else "occasion_id", "value": current.id})
    return out


def checked_identity(session, principal, thread, entry, field, action_kind):
    """Recheck a server-offered identity; the compiler enforces NEW REQUEST.

    Explicit entity selection, an explicit SET occasion_id change and a valid
    current source span remain the semantic compiler's responsibility.  This
    helper never manufactures a change or interprets a pronoun.
    """
    if isinstance(entry, dict) and entry.get("source_kind") == MEMBER_KIND:
        if field != "family_member_id" or action_kind not in {"OCCASION_MEMBER_VIEW", "OCCASION_MEMBER_SET", "OCCASION_SET", "LIFE_PLAN_AUTHORIZE"}: _deny()
        parent = session.get(Task, entry.get("task_id"))
        if parent is None or entry not in member_identities(session, principal, thread, parent, entry.get("before_sequence")): _deny()
        return entry["family_member_id"]
    if (not isinstance(entry, dict) or entry.get("source_kind") != KIND
            or field != "occasion_id" or action_kind not in _CONSUMERS):
        _deny()
    parent = session.get(Task, entry.get("task_id"))
    if parent is None or entry not in occasion_identities(session, principal, thread, parent, entry.get("before_sequence")):
        _deny()
    return entry["occasion_id"]
