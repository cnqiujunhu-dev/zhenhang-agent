"""Owner-only directory metadata. No bank user, account or grant is created."""
import unicodedata

from sqlalchemy import select

from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .extensions_models import FamilyMember
from .family_profile_models import FamilyProfileLabel
from .models import Confirmation, Task, ToolCall, utcnow
from .planning import canonical_hash


def _key(value):
    return unicodedata.normalize("NFKC", value).strip().casefold() if isinstance(value, str) else None


def _text(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40 or any(ord(c) < 32 for c in value):
        raise DomainError("FAMILY_PROFILE_VALUE_REQUIRED", f"请明确{label}，长度须为1至40字。", 422)
    return value.strip()


def _confirmed_label_source(session, member, label):
    if not label.source_reference.startswith("confirmed-task:"):
        return
    task = session.get(Task, label.source_reference.removeprefix("confirmed-task:"))
    rows = (task.plan_json or {}).get("nodes", []) if task else []
    node = rows[0] if len(rows) == 1 else {}
    args = node.get("args") or {}
    confirmations = session.scalars(select(Confirmation).where(Confirmation.task_id == task.id,
        Confirmation.action_type == "occasion.member.set.v1")).all() if task else []
    calls = session.scalars(select(ToolCall).where(ToolCall.task_id == task.id,
        ToolCall.tool_name == "occasion.member.set.v1", ToolCall.status == "SUCCESS")).all() if task else []
    receipt = calls[0].result_json.get("members", []) if len(calls) == 1 else []
    original = receipt[0] if len(receipt) == 1 else {}
    if (task is None or task.state != "SUCCEEDED" or (task.user_id, task.sandbox_id) != (member.owner_user_id, member.sandbox_id)
            or canonical_hash(task.plan_json) != task.plan_hash or node.get("action_kind") != "OCCASION_MEMBER_SET"
            or node.get("tool_name") != "occasion.member.set.v1" or args.get("source_reference") != label.source_reference
            or len(confirmations) != 1 or confirmations[0].status != "CONSUMED" or confirmations[0].consumed_at is None
            or (confirmations[0].user_id, confirmations[0].sandbox_id) != (member.owner_user_id, member.sandbox_id)
            or confirmations[0].plan_hash != task.plan_hash or confirmations[0].node_key != node.get("node_id")
            or confirmations[0].target_id != args.get("family_member_id")
            or confirmations[0].view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": "OCCASION_MEMBER_SET", "args": args})
            or len(calls) != 1 or calls[0].sandbox_id != member.sandbox_id or calls[0].result_json != task.result_json
            or original.get("family_member_id") != member.id or original.get("family_member_name") != label.display_name
            or original.get("family_relationship") != label.relationship_label or original.get("version") != label.version
            or args.get("display_name") != label.display_name or args.get("relationship_label") != label.relationship_label
            or args.get("expected_version") != label.version - 1):
        raise DomainError("FAMILY_PROFILE_SOURCE_CHANGED", "家庭资料与原确认来源不一致，请重新核对。", 409)


def member_label(session, member, *, verify_source=True):
    row = session.scalar(select(FamilyProfileLabel).where(FamilyProfileLabel.member_id == member.id,
        FamilyProfileLabel.owner_user_id == member.owner_user_id, FamilyProfileLabel.sandbox_id == member.sandbox_id,
        FamilyProfileLabel.status == "ACTIVE").execution_options(populate_existing=True))
    if row is not None and verify_source:
        _confirmed_label_source(session, member, row)
    return row


def member_display_name(session, member):
    row = member_label(session, member)
    return row.display_name if row else member.display_name


def family_member_profiles(session, principal, family_member_id=None, *, _verify_sources=True):
    require_profile_actor(session, principal)
    query = select(FamilyMember).where(FamilyMember.sandbox_id == principal.sandbox_id,
        FamilyMember.owner_user_id == principal.user_id).order_by(FamilyMember.id)
    if family_member_id is not None:
        query = query.where(FamilyMember.id == family_member_id)
    members = session.scalars(query).all()
    if family_member_id and not members:
        raise DomainError("OCCASION_MEMBER_SELECTION_REQUIRED", "请明确选择本人已登记的家庭成员。", 422)
    rows = []
    for member in members:
        label = member_label(session, member, verify_source=_verify_sources)
        rows.append({"family_member_id": member.id, "family_member_name": label.display_name if label else member.display_name,
            "family_relationship": label.relationship_label if label else None,
            "version": label.version if label else 0, "status": "ACTIVE", "source": "USER_CONFIRMED" if label else "EXISTING_FAMILY_DIRECTORY",
            "bank_identity_linked": member.helper_user_id is not None, "account_access_granted": False})
    return {"schema_version": "family-profile-v1", "members": rows, "bank_state_changed": False,
        "family_profile_state_changed": False, "account_access_granted": False}


def prepare_member_values(session, principal, *, family_member_id=None, family_member_name=None,
        family_member_new_name=None, family_relationship=None):
    require_profile_actor(session, principal)
    from .occasion_service import resolve_family_member
    member = resolve_family_member(session, principal, family_member_id=family_member_id,
        family_member_name=family_member_name) if family_member_id or family_member_name else None
    label = member_label(session, member) if member else None
    name = _text(family_member_new_name if family_member_new_name is not None else
        member_display_name(session, member) if member else None, "家庭成员显示名")
    relationship = _text(family_relationship if family_relationship is not None else
        label.relationship_label if label else None, "家庭关系标签")
    for current in session.scalars(select(FamilyMember).where(FamilyMember.sandbox_id == principal.sandbox_id,
            FamilyMember.owner_user_id == principal.user_id)):
        if member is not None and current.id == member.id:
            continue
        if _key(name) in {_key(current.display_name), _key(member_display_name(session, current))}:
            raise DomainError("FAMILY_PROFILE_NAME_AMBIGUOUS", "这个显示名已经登记，请使用不同称呼以便准确选择家人。", 422)
    return {"family_member_id": member.id if member else None, "display_name": name,
        "relationship_label": relationship, "expected_version": label.version if label else 0}


def put_member_profile(session, principal, *, family_member_id, display_name, relationship_label,
        expected_version, source_reference):
    require_profile_actor(session, principal, lock=True)
    values = prepare_member_values(session, principal, family_member_id=family_member_id,
        family_member_new_name=display_name, family_relationship=relationship_label)
    if type(expected_version) is not int or expected_version != values["expected_version"]:
        raise DomainError("FAMILY_PROFILE_VERSION_CHANGED", "家庭资料已变化，请重新核对确认。", 409)
    if not isinstance(source_reference, str) or not 1 <= len(source_reference) <= 120:
        raise DomainError("FAMILY_PROFILE_SOURCE_REQUIRED", "家庭资料需要本人的明确确认来源。", 422)
    if family_member_id is None:
        member = FamilyMember(sandbox_id=principal.sandbox_id, owner_user_id=principal.user_id,
            helper_user_id=None, display_name=values["display_name"])
        session.add(member); session.flush()
    else:
        from .occasion_service import resolve_family_member
        member = resolve_family_member(session, principal, family_member_id=family_member_id)
    label = member_label(session, member)
    if label is None:
        label = FamilyProfileLabel(sandbox_id=principal.sandbox_id, owner_user_id=principal.user_id,
            member_id=member.id, display_name=values["display_name"], relationship_label=values["relationship_label"],
            source_reference=source_reference)
        session.add(label)
    else:
        label.display_name, label.relationship_label = values["display_name"], values["relationship_label"]
        label.version += 1
        label.source_reference, label.confirmed_at = source_reference, utcnow()
    session.flush()
    result = family_member_profiles(session, principal, member.id, _verify_sources=False)
    result["family_profile_state_changed"] = True
    return result
