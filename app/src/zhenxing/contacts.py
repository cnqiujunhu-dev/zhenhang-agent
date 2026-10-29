"""Personal recipient roles with explicit proposal/confirmation/version bounds."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy import select

from .access_models import LoginAccount
from .audit import append_audit
from .contact_models import ContactAlias, ContactAliasEvent, ContactAliasProposal
from .contracts import DomainError
from .models import Account, Card, Task, utcnow
from .planning import canonical_hash


BANK_ROLES = {"CUSTOMER", "DEV_TEST"}


def _owner(session, sid, uid):
    actor = session.scalar(select(LoginAccount).where(LoginAccount.sandbox_id == sid, LoginAccount.user_id == uid))
    if actor is None or actor.status != "ACTIVE" or actor.role not in BANK_ROLES:
        raise DomainError("CONTACT_OWNER_REQUIRED", "仅本人有效业务账号可保存自己的收款身份", 403)
    return actor


def _lock_participants(session, sid, uid, recipient_login_id):
    owner = _owner(session, sid, uid)
    rows = list(session.scalars(select(LoginAccount).where(LoginAccount.id.in_({owner.id, recipient_login_id}))
        .order_by(LoginAccount.id).with_for_update().execution_options(populate_existing=True)))
    if len(rows) != 2 or any(row.status != "ACTIVE" or row.role not in BANK_ROLES for row in rows):
        raise DomainError("CONTACT_TARGET_UNAVAILABLE", "身份绑定涉及的本人或收款账号不可用", 409)


def alias_name(value):
    if not isinstance(value, str):
        raise DomainError("CONTACT_ALIAS_INVALID", "请明确收款人在你这里的称呼", 422)
    name = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(name) <= 32 or not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_ ]+", name):
        raise DomainError("CONTACT_ALIAS_INVALID", "收款称呼须为1至32位中文、字母、数字或下划线", 422)
    return name


def find_alias(session, sid, uid, name):
    return session.scalar(select(ContactAlias).where(ContactAlias.sandbox_id == sid,
        ContactAlias.user_id == uid, ContactAlias.alias_name == name, ContactAlias.status == "ACTIVE"))


def registered_reference(session, reference):
    """Resolve a username or exact simulated card, never a client database ID."""
    from .card_identifiers import lookup_card_number, normalize_card_number
    if not isinstance(reference, str) or not reference.strip():
        raise DomainError("CONTACT_RECIPIENT_REQUIRED", "请明确收款账号或完整平台卡号", 422)
    reference = reference.strip()
    card = None
    if reference.lower().startswith(("zhenhang", "xiaozhao")):
        number = normalize_card_number(reference)
        card = lookup_card_number(session, number)
        if card is None:
            raise DomainError("RECIPIENT_CARD_UNAVAILABLE", "该完整平台卡号不存在、停用或不能收款", 409)
        login = session.scalar(select(LoginAccount).where(LoginAccount.user_id == card.user_id,
            LoginAccount.sandbox_id == card.sandbox_id))
    else:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", reference.lower()):
            raise DomainError("CONTACT_REGISTERED_RECIPIENT_REQUIRED", "身份绑定需要明确的平台账号或完整平台卡号", 422)
        login = session.scalar(select(LoginAccount).where(LoginAccount.username == reference.lower()))
    if login is None or login.status != "ACTIVE" or login.role not in BANK_ROLES:
        raise DomainError("RECIPIENT_ACCOUNT_UNAVAILABLE", "收款业务账号不存在或当前不可用", 409)
    banks = list(session.scalars(select(Account).where(Account.user_id == login.user_id, Account.sandbox_id == login.sandbox_id)))
    if card:
        banks = [bank for bank in banks if bank.id == card.account_id]
    elif len(banks) != 1:
        from .payment_instruments import resolve_recipient_instrument
        snapshot = resolve_recipient_instrument(session, login.username)
        banks = [bank for bank in banks if bank.id == snapshot.get("account_id")]
    if len(banks) != 1 or banks[0].currency != "CNY":
        raise DomainError("RECIPIENT_BINDING_CHANGED", "卡号、身份与银行账户关联不一致", 409)
    return login, banks[0], card


def _alias_target(session, alias):
    login = session.get(LoginAccount, alias.recipient_login_account_id)
    bank = session.get(Account, alias.recipient_bank_account_id)
    card = session.get(Card, alias.recipient_card_id) if alias.recipient_card_id else None
    if login is None or login.status != "ACTIVE" or login.role not in BANK_ROLES or bank is None \
            or (bank.user_id, bank.sandbox_id, bank.currency) != (login.user_id, login.sandbox_id, "CNY") \
            or (alias.recipient_card_id and (card is None or card.status != "ACTIVE" or
                (card.user_id, card.sandbox_id, card.account_id) != (login.user_id, login.sandbox_id, bank.id))):
        raise DomainError("CONTACT_TARGET_UNAVAILABLE", "已保存身份的收款对象当前不可用，不会改走外部转出", 409)
    return login, bank, card


def recipient_context(session, sid, uid, reference):
    """Snapshot role reads and explicit cards without changing any identity."""
    alias = find_alias(session, sid, uid, reference.strip()) if isinstance(reference, str) else None
    login, bank, card = _alias_target(session, alias) if alias else registered_reference(session, reference)
    if login.user_id == uid:
        raise DomainError("SELF_TRANSFER_FORBIDDEN", "不能将本人同一银行账户作为收款人", 409)
    result = {"requested_reference": reference, "recipient_username": login.username,
        "recipient_login_account_id": login.id, "recipient_bank_account_id": bank.id}
    if card:
        from .card_identifiers import card_identifier_view
        view = card_identifier_view(session, card) or {}
        result.update({"recipient_card_id": card.id, "recipient_card_version": card.version,
            "recipient_card_number": view.get("card_number"), "recipient_card_display_last4": view.get("display_last4")})
    if alias:
        result["alias_read"] = {"alias_id": alias.id, "alias_name": alias.alias_name, "version": alias.version,
            "recipient_login_account_id": alias.recipient_login_account_id,
            "recipient_bank_account_id": alias.recipient_bank_account_id, "recipient_card_id": alias.recipient_card_id}
    return result


def validate_recipient_context(session, sid, uid, snapshot):
    if not snapshot:
        return
    _lock_participants(session, sid, uid, snapshot["recipient_login_account_id"])
    expected_alias = snapshot.get("alias_read")
    if expected_alias:
        alias = session.scalar(select(ContactAlias).where(ContactAlias.id == expected_alias["alias_id"],
            ContactAlias.sandbox_id == sid, ContactAlias.user_id == uid).with_for_update())
        current = None if alias is None else {"alias_id": alias.id, "alias_name": alias.alias_name, "version": alias.version,
            "recipient_login_account_id": alias.recipient_login_account_id,
            "recipient_bank_account_id": alias.recipient_bank_account_id, "recipient_card_id": alias.recipient_card_id}
        if alias is None or alias.status != "ACTIVE" or current != expected_alias:
            raise DomainError("CONTACT_ALIAS_VERSION_CHANGED", "收款身份绑定已变化，请重新规划并确认", 409)
        login, bank, _card = _alias_target(session, alias)
    else:
        login, bank, _card = registered_reference(session, snapshot["requested_reference"])
    if (login.id, bank.id, login.username) != (snapshot["recipient_login_account_id"],
            snapshot["recipient_bank_account_id"], snapshot["recipient_username"]):
        raise DomainError("RECIPIENT_BINDING_CHANGED", "明确收款对象已变化", 409)
    if snapshot.get("recipient_card_id"):
        card = session.scalar(select(Card).where(Card.id == snapshot["recipient_card_id"]).with_for_update())
        if card is None or card.status != "ACTIVE" or card.version != snapshot["recipient_card_version"]:
            raise DomainError("RECIPIENT_CARD_CHANGED", "明确收款卡已变化，请重新确认", 409)


def _source_message(session, task, context):
    root_id = (context or {}).get("source_root_task_id")
    if root_id:
        root = session.get(Task, root_id)
        if root and (root.sandbox_id, root.user_id) == (task.sandbox_id, task.user_id):
            return root.message
    from .runtime_models import RuntimeRecord
    record = session.get(RuntimeRecord, task.id)
    if record and record.root_task_id:
        root = session.get(Task, record.root_task_id)
        if root and (root.sandbox_id, root.user_id) == (task.sandbox_id, task.user_id):
            return root.message
    return task.message


def prepare_alias_proposal(session, task, action, context=None):
    values = action.get("slots") or {}
    if not values.get("recipient_alias"):
        return None
    source = _source_message(session, task, context)
    from .intelligence import contact_memory_declined
    if contact_memory_declined(source):
        raise DomainError("CONTACT_ALIAS_MEMORY_DECLINED", "你的原话拒绝保存收款身份，不能根据模型提案建立或改绑关系", 403)
    _owner(session, task.sandbox_id, task.user_id)
    label = alias_name(values["recipient_alias"])
    ref = values.get("payee_name")
    if ref == label:
        raise DomainError("CONTACT_EXPLICIT_TARGET_REQUIRED", "不能凭称呼猜收款对象并新增绑定", 422)
    # Independently recover the positive first-party statement, excluding quoted
    # notes/conditions. A model-selected label alone cannot create memory.
    from .compiled_actions import validate_compiled_action
    from .services import Principal
    compiled = validate_compiled_action(session, Principal(task.user_id, task.sandbox_id), task.message, action, context)
    if compiled is None:
        from .intelligence import rule_proposal
        candidates = rule_proposal(source).actions
        if not any(item.kind in {"CONTACT_BIND", "TRANSFER", "FX_TRANSFER"}
                   and item.slots.recipient_alias == values["recipient_alias"]
                   and item.slots.payee_name and item.slots.payee_name.lower() == str(ref).lower()
                   and bool(item.slots.replace_alias) == bool(values.get("replace_alias")) for item in candidates):
            raise DomainError("CONTACT_ALIAS_SOURCE_UNPROVEN", "原文没有明确绑定本收款对象身份的指令", 403)
    target = recipient_context(session, task.sandbox_id, task.user_id, ref)
    label_row = find_alias(session, task.sandbox_id, task.user_id, label)
    same = bool(label_row and (label_row.recipient_login_account_id, label_row.recipient_bank_account_id,
        label_row.recipient_card_id) == (target["recipient_login_account_id"], target["recipient_bank_account_id"], target.get("recipient_card_id")))
    if label_row and not same and values.get("replace_alias") is not True:
        raise DomainError("CONTACT_ALIAS_REPLACE_REQUIRES_EXPLICIT", "该称呼已绑定另一个账户；请明确是否改绑，当前未转账或覆盖身份", 422)
    kind = "UNCHANGED" if same else "REPLACE" if label_row else "CREATE"
    from .models import Payee
    from .internal_banking_models import InternalPayeeBinding
    external = session.scalar(select(Payee.id).outerjoin(InternalPayeeBinding, InternalPayeeBinding.payee_id == Payee.id)
        .where(Payee.sandbox_id == task.sandbox_id, Payee.user_id == task.user_id,
               Payee.display_name == label, Payee.status == "ACTIVE", InternalPayeeBinding.payee_id.is_(None)))
    scope = {"alias_name": label, "target": target, "expected_alias_id": label_row.id if label_row else None,
        "expected_alias_version": label_row.version if label_row else None, "change_kind": kind,
        "previous_recipient_username": session.get(LoginAccount, label_row.recipient_login_account_id).username if label_row else None,
        "existing_external_contact_preserved": bool(external),
        "source_hash": hashlib.sha256(source.encode()).hexdigest()}
    previous = session.scalar(select(ContactAliasProposal).where(ContactAliasProposal.task_id == task.id,
        ContactAliasProposal.alias_name == label))
    if previous:
        if previous.scope_json != scope:
            raise DomainError("CONTACT_ALIAS_PROPOSAL_CHANGED", "同一任务的身份提案范围已变化", 409)
        return alias_proposal_view(previous)
    row = ContactAliasProposal(sandbox_id=task.sandbox_id, user_id=task.user_id, task_id=task.id, alias_name=label,
        recipient_login_account_id=target["recipient_login_account_id"], recipient_bank_account_id=target["recipient_bank_account_id"],
        recipient_card_id=target.get("recipient_card_id"), recipient_username=target["recipient_username"],
        requested_reference=ref, expected_alias_id=scope["expected_alias_id"], expected_alias_version=scope["expected_alias_version"],
        change_kind=kind, scope_json=scope, scope_hash=canonical_hash(scope))
    session.add(row); session.flush()
    return alias_proposal_view(row)


def alias_proposal_view(row):
    return {"proposal_id": row.id, "alias_name": row.alias_name, "recipient_username": row.recipient_username,
        "recipient_reference": row.requested_reference, "change_kind": row.change_kind,
        "expected_alias_version": row.expected_alias_version, "scope_hash": row.scope_hash,
        "previous_recipient_username": row.scope_json.get("previous_recipient_username"),
        "existing_external_contact_preserved": row.scope_json.get("existing_external_contact_preserved", False),
        "status": row.status, "notice": "确认后仅保存为你自己的收款身份，不修改对方的全局姓名。"
            + (" 今后对话简称优先使用此平台账户；已有同名外部联系人与历史记录保留。"
               if row.scope_json.get("existing_external_contact_preserved") else "")}


def validate_alias_proposal(session, task, pending):
    if not pending:
        return None
    preview = session.get(ContactAliasProposal, pending["proposal_id"])
    if preview is None or (preview.sandbox_id, preview.user_id, preview.task_id) != (task.sandbox_id, task.user_id, task.id):
        raise DomainError("CONTACT_ALIAS_PROPOSAL_CHANGED", "收款身份提案不属于当前任务", 409)
    _lock_participants(session, task.sandbox_id, task.user_id, preview.recipient_login_account_id)
    row = session.scalar(select(ContactAliasProposal).where(ContactAliasProposal.id == pending["proposal_id"],
        ContactAliasProposal.sandbox_id == task.sandbox_id, ContactAliasProposal.user_id == task.user_id,
        ContactAliasProposal.task_id == task.id).with_for_update())
    if row is None or row.scope_hash != pending["scope_hash"] or canonical_hash(row.scope_json) != row.scope_hash \
            or alias_proposal_view(row) != pending:
        raise DomainError("CONTACT_ALIAS_PROPOSAL_CHANGED", "待确认的收款身份范围不一致", 409)
    source = _source_message(session, task, None)
    if hashlib.sha256(source.encode()).hexdigest() != row.scope_json["source_hash"]:
        raise DomainError("CONTACT_ALIAS_SOURCE_CHANGED", "身份绑定的原始指令已变化", 409)
    alias = session.scalar(select(ContactAlias).where(ContactAlias.sandbox_id == row.sandbox_id,
        ContactAlias.user_id == row.user_id, ContactAlias.alias_name == row.alias_name).with_for_update())
    if (alias.id if alias else None, alias.version if alias else None) != (row.expected_alias_id, row.expected_alias_version):
        raise DomainError("CONTACT_ALIAS_VERSION_CHANGED", "身份已被另一任务更新，请重新确认", 409)
    validate_recipient_context(session, task.sandbox_id, task.user_id, row.scope_json["target"])
    return row


def commit_alias_proposal(session, task, pending):
    if not pending:
        return None
    row = validate_alias_proposal(session, task, pending)
    alias = session.get(ContactAlias, row.expected_alias_id) if row.expected_alias_id else None
    if alias is None:
        alias = ContactAlias(sandbox_id=row.sandbox_id, user_id=row.user_id, alias_name=row.alias_name,
            recipient_login_account_id=row.recipient_login_account_id, recipient_bank_account_id=row.recipient_bank_account_id,
            recipient_card_id=row.recipient_card_id)
        session.add(alias); session.flush()
    elif row.change_kind != "UNCHANGED":
        alias.recipient_login_account_id, alias.recipient_bank_account_id = row.recipient_login_account_id, row.recipient_bank_account_id
        alias.recipient_card_id = row.recipient_card_id
        alias.version += 1
        alias.updated_at = utcnow()
    row.status, row.committed_at = "COMMITTED", utcnow()
    session.add(ContactAliasEvent(sandbox_id=row.sandbox_id, user_id=row.user_id, alias_id=alias.id,
        proposal_id=row.id, task_id=task.id, action=row.change_kind, version=alias.version,
        payload_json={"alias_name": row.alias_name, "recipient_username": row.recipient_username,
            "requested_reference": row.requested_reference, "source_hash": row.scope_json["source_hash"]}))
    append_audit(session, sandbox_id=row.sandbox_id, task_id=task.id, event_type="CONTACT_ALIAS_COMMITTED",
        payload={"alias_id": alias.id, "version": alias.version, "alias_name": alias.alias_name,
            "recipient_username": row.recipient_username, "personal_only": True})
    return {"alias_name": alias.alias_name, "recipient_username": row.recipient_username,
            "version": alias.version, "personal_only": True, "change_kind": row.change_kind}
