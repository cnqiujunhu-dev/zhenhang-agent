from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit
from .contracts import DomainError, FinancialIntentContract
from .models import Account, Confirmation, Payee, Task, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, append_message, record_policy
from .workflow import transition_task


TRANSFER_PATTERNS = (
    re.compile(r"(?:给|向)(?P<payee>[\u4e00-\u9fa5A-Za-z0-9_]{1,80}?)转(?:账)?(?P<amount>\d+(?:\.\d{1,2})?)(?![\d.])元?"),
    re.compile(r"转账给(?P<payee>[\u4e00-\u9fa5A-Za-z_]{1,80})(?P<amount>\d+(?:\.\d{1,2})?)(?![\d.])元?"),
)


def parse_transfer_request(message: str) -> tuple[str, int, str] | None:
    for pattern in TRANSFER_PATTERNS:
        match = pattern.search(message)
        if match is None:
            continue
        try:
            amount_minor = int(Decimal(match.group("amount")) * 100)
        except (InvalidOperation, ValueError):
            return None
        if amount_minor <= 0:
            return None
        from .intelligence import _transfer_memo
        memo, memo_issue = _transfer_memo(message)
        if memo_issue:
            return None
        return match.group("payee"), amount_minor, memo or ""
    return None


def prepare_transfer_task(
    session: Session,
    *,
    task: Task,
    conversation_id: str,
    selected_payee_id: str | None = None,
    proposed_action: dict | None = None,
    source_context: dict | None = None,
) -> Task:
    values = (proposed_action or {}).get("slots") or {}
    # A fully specified recipient card is itself a bank-resolvable identity.
    # Do not reparse the compiler's canonical sentence when the model chose
    # that stronger identifier instead of duplicating a name slot.
    recipient_identity = values.get("payee_name")
    if not recipient_identity and re.fullmatch(r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}", values.get("recipient_card_reference") or ""):
        recipient_identity = values["recipient_card_reference"]
    parsed = ((recipient_identity, values["amount_minor"], values.get("memo") or "")
              if recipient_identity and values.get("amount_minor") else parse_transfer_request(task.message))
    fic = FinancialIntentContract.model_validate(task.fic_json)
    if parsed is None:
        task.result_json = {"message": "请明确收款人和金额，例如：给房东转4200元。"}
        return task
    payee_name, amount_minor, memo = parsed
    if values.get("payee_name") and values.get("amount_minor"):
        # v2 already has typed, grounded user slots; card/role resolution must
        # not lose explicit identities by reparsing a shorter canonical name.
        payee_name, amount_minor, memo = values["payee_name"], values["amount_minor"], values.get("memo") or ""
    fic.slots = {
        "payee_name": {"value": payee_name, "source": "USER_TEXT"},
        "amount_minor": {"value": amount_minor, "source": "USER_TEXT"},
        "memo": {"value": memo, "source": "USER_TEXT"},
    }
    fic.requested_capabilities = ["transfer.commit.v1"]
    task.fic_json = fic.model_dump()
    from .internal_banking import resolve_transfer_payees, recipient_scope, account_reference
    recipient_selection = values.get("recipient_card_reference")
    recipient_reference = recipient_selection
    if recipient_reference:
        from .services import Principal
        from .transfer_payment_scope import selected_recipient_reference
        recipient_reference = selected_recipient_reference(session, Principal(task.user_id, task.sandbox_id),
                                                           payee_name, recipient_reference)
    payees = resolve_transfer_payees(session, task.sandbox_id, task.user_id, recipient_reference or payee_name)
    if selected_payee_id is not None:
        payees = [item for item in payees if item.id == selected_payee_id]
        if len(payees) != 1:
            raise DomainError("PAYEE_SELECTION_INVALID", "选定的收款人不在候选列表中", 409)
        fic.intent_version += 1
    if len(payees) != 1:
        task.result_json = {
            "message": "收款人不唯一，请确认具体账户。" if payees else "没有找到该收款人，请选择已有收款人。",
            "payee_candidates": [
                {"payee_id": item.id, "name": item.display_name, "account_last4": item.account_last4}
                for item in payees
            ],
        }
        append_audit(
            session,
            sandbox_id=task.sandbox_id,
            task_id=task.id,
            event_type="TRANSFER_NEED_PAYEE_CLARIFICATION",
            payload={"candidate_count": len(payees)},
        )
        return task
    payee = payees[0]
    destination = recipient_scope(session, payee)
    target_context = alias_binding = None
    if destination["transfer_channel"] == "INTERNAL_BANK":
        from .contacts import recipient_context, prepare_alias_proposal
        target_context = recipient_context(session, task.sandbox_id, task.user_id, recipient_reference or payee_name)
        try:
            alias_binding = prepare_alias_proposal(session, task, proposed_action or {}, source_context)
        except DomainError as error:
            task.result_json = {"message": error.message, "error": error.code}
            if error.status_code != 422:
                transition_task(task, "FAILED")
            return task
    payment_scope = None
    from .operational_clock import registered_customer
    if registered_customer(session, task.sandbox_id, task.user_id):
        from .services import Principal
        from .transfer_payment_scope import resolve_transfer_payment_scope, selected_source_reference
        try:
            payment_scope = resolve_transfer_payment_scope(session, Principal(task.user_id, task.sandbox_id),
                payee=payee, payee_reference=recipient_reference or payee_name,
                source_card_reference=selected_source_reference(session, Principal(task.user_id, task.sandbox_id), values),
                recipient_card_reference=(recipient_selection if recipient_selection == "REGISTERED_DEFAULT" else recipient_reference),
                amount_minor=amount_minor)
        except DomainError as error:
            task.result_json = {"message": error.message, "error": error.code}
            if error.code in {"TRANSFER_SOURCE_AVAILABLE_FUNDS", "TRANSFER_SOURCE_CARD_LIMIT",
                    "PAYMENT_INSTRUMENT_UNAVAILABLE", "CREDIT_CARD_CASH_CHANNEL_FORBIDDEN"}:
                transition_task(task, "FAILED")
            return task
    account = session.get(Account, payment_scope["source"]["account_id"]) if payment_scope else session.scalar(
        select(Account).where(
            Account.sandbox_id == task.sandbox_id,
            Account.user_id == task.user_id,
        )
    )
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "付款账户不存在", 404)
    source_card_context = None
    if payment_scope:
        source = payment_scope["source"]
        source_card_context = {"card_id": source["card_id"], "card_version": source["card_version"],
            "account_id": source["account_id"], "card_number": source.get("card_number"),
            "display_last4": source.get("display_last4")}
    elif values.get("card_number") or values.get("card_last4"):
        from .card_identifiers import resolve_owned_cards, card_identifier_view
        matching = None
        for ref in (value for value in (values.get("card_number"), values.get("card_last4")) if value):
            candidates = {card.id: card for card in resolve_owned_cards(session, task.sandbox_id, task.user_id, ref)}
            matching = candidates if matching is None else {key: card for key, card in matching.items() if key in candidates}
        if not matching or len(matching) != 1:
            task.result_json = {"message": "无法唯一确定本人付款卡，请提供完整卡号。", "error": "TRANSFER_SOURCE_CARD_UNRESOLVED"}
            return task
        card = next(iter(matching.values()))
        if card.account_id != account.id or card.status != "ACTIVE" or card.limit_minor < amount_minor:
            task.result_json = {"message": "明确付款卡不可用或限额不足，未换卡付款。", "error": "TRANSFER_SOURCE_CARD_UNAVAILABLE"}
            transition_task(task, "FAILED")
            return task
        identity = card_identifier_view(session, card) or {}
        source_card_context = {"card_id": card.id, "card_version": card.version, "account_id": account.id,
            "card_number": identity.get("card_number"), "display_last4": identity.get("display_last4"),
            "legacy_last4": card.last4}
    if account.balance_minor < amount_minor:
        task.result_json = {
            "message": "账户余额不足，转账未执行。",
            "balance_minor": account.balance_minor,
            "amount_minor": amount_minor,
        }
        append_audit(
            session,
            sandbox_id=task.sandbox_id,
            task_id=task.id,
            event_type="TRANSFER_REJECTED",
            payload={"reason": "INSUFFICIENT_FUNDS"},
        )
        transition_task(task, "FAILED")
        return task
    if amount_minor > 5_000_000:
        task.result_json = {"message": "单笔超过当前限额，转账未执行。"}
        transition_task(task, "FAILED")
        return task
    from .transfer_risk import check_transfer_limits
    try:
        check_transfer_limits(session, account, payee, amount_minor)
    except DomainError as error:
        task.result_json = {"message": error.message, "error": error.code}
        append_audit(session,sandbox_id=task.sandbox_id,task_id=task.id,event_type='TRANSFER_RISK_STOPPED',payload={'reason_code':error.code,'bank_write_executed':False})
        record_policy(session,task_id=task.id,sandbox_id=task.sandbox_id,node_key='transfer-risk',decision='DENY',
            reason_codes=[error.code],policy_version='transfer-risk-review-v2')
        transition_task(task, "FAILED")
        return task

    fic.slots["payee_id"] = {"value": payee.id, "source": "AUTHORITATIVE", "last4": payee.account_last4}
    fic.slots["source_account_id"] = {"value": account.id, "source": "AUTHORITATIVE"}
    for field, value in destination.items():
        fic.slots[field] = {"value": value, "source": "AUTHORITATIVE_BANK_DIRECTORY"}
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    plan = {
        "plan_version": 1,
        "policy_version": "transfer-v1",
        "task_id": task.id,
        "sandbox_id": task.sandbox_id,
        "nodes": [
            {
                "node_id": "validate-transfer",
                "tool_name": "transfer.validate.v1",
                "status": "SUCCEEDED",
                "amount_minor": amount_minor,
            },
            {
                "node_id": "commit-transfer",
                "tool_name": "transfer.commit.v1",
                "read_only": False,
                "depends_on": ["validate-transfer"],
                "source_account_id": account.id,
                "expected_account_version": account.version,
                "payee_id": payee.id,
                "payee_last4": payee.account_last4,
                **destination,
                **({"recipient_context": target_context} if target_context else {}),
                **({"alias_binding": alias_binding} if alias_binding else {}),
                **({"source_card_context": source_card_context} if source_card_context else {}),
                **({"transfer_payment_scope": payment_scope} if payment_scope else {}),
                "amount_minor": amount_minor,
                "memo": memo,
                "requires_confirmation": True,
            },
        ],
    }
    task.plan_json = plan
    task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(
        session,
        task_id=task.id,
        sandbox_id=task.sandbox_id,
        node_key="commit-transfer",
        decision="REQUIRE_CONFIRMATION",
        reason_codes=["FUNDS_MOVE", "USER_REQUESTED_TRANSFER"],
        policy_version="transfer-v1",
    )
    transition_task(task, "AWAITING_CONFIRMATION")
    task.result_json = {
        "message": (f"准备向平台账号 {payee.display_name}（账户标识 {account_reference(destination['recipient_account_id'])}）转账 {amount_minor/100:.2f} 元；确认后双方同时入账。"
                    if destination["transfer_channel"] == "INTERNAL_BANK" else
                    f"准备向 {payee.display_name}（尾号 {payee.account_last4}）外部转出 {amount_minor/100:.2f} 元；资金记入外部清算账户，外部到账未核验，平台内没有对应用户入账。"),
        **destination,
        "simulated": True,
        **({"recipient_context": target_context} if target_context else {}),
        **({"alias_binding": alias_binding} if alias_binding else {}),
        **({"source_card_context": source_card_context} if source_card_context else {}),
        **({"payment_confirmation": payment_scope["display"]} if payment_scope else {}),
    }
    if alias_binding:
        task.result_json["message"] += f" 同时将你自己的‘{alias_binding['alias_name']}’身份绑定到{alias_binding['recipient_username']}；仅确认后保存。"
    confirmation = Confirmation(
        sandbox_id=task.sandbox_id,
        task_id=task.id,
        user_id=task.user_id,
        node_key="commit-transfer",
        action_type="transfer.commit.v1",
        target_id=payee.id,
        plan_hash=task.plan_hash,
        view_hash=canonical_hash(
            {"plan_hash": task.plan_hash, "payee_last4": payee.account_last4, "amount_minor": amount_minor, "recipient_scope": destination}
        ),
        expires_at=utcnow() + timedelta(minutes=5),
    )
    session.add(confirmation)
    session.flush()
    append_audit(
        session,
        sandbox_id=task.sandbox_id,
        task_id=task.id,
        event_type="TRANSFER_PLAN_PROPOSED",
        payload={"amount_minor": amount_minor, "payee_last4": payee.account_last4, "plan_hash": task.plan_hash},
    )
    append_message(
        session,
        conversation_id=conversation_id,
        sandbox_id=task.sandbox_id,
        task_id=task.id,
        role="ASSISTANT",
        content=task.result_json["message"],
    )
    return task
