from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit
from .contracts import FinancialIntentContract
from .models import Account, Card, CardReplacementRequest, Confirmation, EvidenceSnapshot, Task, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, append_message, record_policy
from .workflow import transition_task
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from . import credit_models as _credit_models  # Register additive Base tables for legacy isolated fixtures.


def _requested_cards(session, task, cards):
    from .card_identifiers import resolve_owned_cards
    full = re.findall(r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}(?![0-9a-fA-F])", task.message)
    tails = re.findall(r"(?:尾号|末四位)\s*([0-9a-fA-F]{4})(?![0-9a-fA-F])", task.message)
    references = full + tails
    if not references:
        return cards
    matching = {card.id for card in cards}
    for reference in references:
        matching &= {row.id for row in resolve_owned_cards(session, task.sandbox_id, task.user_id, reference)}
    return [card for card in cards if card.id in matching]


ACTION_LABELS = {
    "LOCK": "临时锁定",
    "UNLOCK": "解除临时锁定",
    "REPORT_LOSS": "挂失",
    "REQUEST_REPLACEMENT": "提交补卡申请",
    "SET_LIMIT": "调整单笔限额",
}


def _balance_projection(session, card):
    """Existing owned account/wallet facts, not a card limit or model value."""
    from .fx_banking import fxcardprojection
    from .customer_membership import card_tier_display
    projection = fxcardprojection(session, card)
    balances = {currency: amount for currency, amount in projection.get("balances_by_currency", {}).items()
        if currency in {"CNY", "USD"} and type(amount) is int and abs(amount) <= 2**53 - 1}
    scopes = {}
    account = session.get(Account, card.account_id)
    if "CNY" in balances and account and (account.sandbox_id, account.user_id, account.currency) == (
            card.sandbox_id, card.user_id, "CNY"):
        scopes["CNY"] = "CNY:" + canonical_hash({"sandbox_id": card.sandbox_id,
            "user_id": card.user_id, "account_id": account.id})
    if "USD" in balances and projection.get("usd_wallet_id"):
        scopes["USD"] = "USD:" + canonical_hash({"sandbox_id": card.sandbox_id,
            "user_id": card.user_id, "wallet_id": projection["usd_wallet_id"]})
    return {**{key: value for key, value in projection.items() if key in {
        "currency_mode", "supported_currencies", "fx_card", "fx_profile_version", "usd_wallet_version"}},
        "balances_by_currency": balances, "balance_scope_refs": scopes, "limit_currency": "CNY",
        **card_tier_display(session, card)}


def debit_card_status_row(session, card, *, include_internal=False):
    """Deposit-backed row only. Credit rows must never enter this array."""
    from .card_identifiers import card_identifier_view
    from .payment_instruments import cash_reserved_minor
    from .services import Principal
    account = session.get(Account, card.account_id)
    if not account or (account.user_id, account.sandbox_id) != (card.user_id, card.sandbox_id):
        from .contracts import DomainError
        raise DomainError("CARD_ACCOUNT_BINDING_INVALID", "卡片资金归属需要重新核对。", 409)
    row = {"card_id": card.id, "last4": card.last4, "status": card.status,
        "limit_minor": card.limit_minor, "version": card.version, "instrument_type": "DEBIT",
        "instrument_id": "debit:"+card.id, **(card_identifier_view(session, card) or {}), **_balance_projection(session, card)}
    reserved = cash_reserved_minor(session, Principal(card.user_id, card.sandbox_id), account.id)
    row["reserved_cash_minor"] = reserved
    row["available_by_currency"] = {**row["balances_by_currency"], "CNY": max(0, account.balance_minor-reserved)}
    if not include_internal: row.pop("balance_scope_refs", None)
    return row


def owned_card_status(session, principal, card_reference=None):
    if getattr(principal,"family_context",None):
        from .contracts import DomainError
        raise DomainError("CARD_OWNER_REQUIRED","完整卡片资料仅向本人身份开放。",403)
    from .permissions import require_banking_actor
    from .credit_service import credit_view
    from .payment_instruments import resolve_source_instrument
    role = require_banking_actor(session, principal)
    cards = list(session.scalars(select(Card).where(Card.sandbox_id == principal.sandbox_id,
        Card.user_id == principal.user_id, Card.status != ARCHIVED_RESOURCE_STATUS).order_by(Card.id)))
    selected = resolve_source_instrument(session, principal, card_reference, purpose="READ") if card_reference else None
    if selected: cards = [card for card in cards if selected["instrument_type"] == "DEBIT" and card.id == selected["card_id"]]
    credits = credit_view(session, principal, card_reference if selected and selected["instrument_type"] == "CREDIT" else None)["cards"] \
        if role in {"CUSTOMER", "DEV_TEST", "DEMO_OWNER"} and not getattr(principal, "family_context", None) and (not selected or selected["instrument_type"] == "CREDIT") else []
    return {"schema_version": "owned-cards-v2", "cards": [debit_card_status_row(session, card) for card in cards],
        "credit_cards": credits, "bank_state_changed": False}


def parse_card_request(message: str) -> tuple[str, int | None] | None:
    if re.search(r"(不要|别|无需|不需要|先别|暂时不).{0,6}(锁卡|冻结|挂失|解锁|补卡|限额)", message):
        return None
    if "挂失" in message:
        return "REPORT_LOSS", None
    if "补卡" in message or "换卡" in message:
        return "REQUEST_REPLACEMENT", None
    if "解锁" in message or "解除锁定" in message or "解除冻结" in message:
        return "UNLOCK", None
    if "锁卡" in message or "冻结卡" in message or "临时锁定" in message:
        return "LOCK", None
    if "限额" in message:
        match = re.search(r"(?:限额.{0,8}?|调(?:整|到).{0,4}?)(?<![\d.])(\d+(?:\.\d{1,2})?)(?![\d.])\s*元", message)
        if match is None:
            return None
        try:
            amount_minor = int(Decimal(match.group(1)) * 100)
        except (InvalidOperation, ValueError):
            return None
        return "SET_LIMIT", amount_minor
    if re.search(r"卡.{0,8}(找不到|不见了|丢了).{0,16}(先别|别|不能|不让).{0,8}(付款|支付|刷卡)", message):
        return "LOCK", None
    return None


def _wants_card_status(message: str) -> bool:
    return any(term in message for term in ("查看", "查询", "看看", "多少", "状态", "怎么样", "现在")) \
        and not any(term in message for term in ("调整", "调到", "调一下", "设置", "改成", "提高", "降低"))


def card_status_scope_matches_action(result, action):
    """The READ result cannot claim a category different from its typed source."""
    if not isinstance(result, dict) or not isinstance(action, dict): return False
    slots = action.get("slots") or {}
    if not isinstance(slots, dict): return False
    from .card_balance_scope import balance_scope, canonical_currencies, check_scalar_currency
    currencies = slots.get('card_balance_currencies')
    if currencies is None:
        if 'card_balance_scope' in result: return False
    else:
        try:
            currencies = canonical_currencies(currencies)
            check_scalar_currency(currencies, slots.get('currency'))
            if result.get('card_balance_scope') != balance_scope(result.get('cards', []), currencies,
                    slots.get('card_number') or slots.get('card_last4')): return False
        except (ValueError, KeyError, TypeError): return False
    requested = slots.get("card_type")
    if "card_status_scope" not in result: return requested is None
    return isinstance(requested, str) and requested in {"DEBIT", "ALL"} and result["card_status_scope"] == {
        "schema_version": "card-status-scope-v1", "card_type": requested,
        "card_reference": slots.get("card_number") or slots.get("card_last4")}


def card_status_scope_current(session, principal, result):
    """Opt-in finite READ scope. Legacy result/hash shapes are not rewritten."""
    if not isinstance(result, dict): return False
    if 'card_balance_scope' in result:
        from .card_balance_scope import canonical_currencies, balance_scope, project_balance_row
        marker = result['card_balance_scope']
        if not isinstance(marker, dict) or getattr(principal, 'family_context', None): return False
        try:
            from .permissions import require_banking_actor
            require_banking_actor(session, principal)
            currencies = canonical_currencies(marker.get('requested_currencies'))
            cards = list(session.scalars(select(Card).where(Card.sandbox_id == principal.sandbox_id,
                Card.user_id == principal.user_id, Card.status != ARCHIVED_RESOURCE_STATUS)))
            reference = marker.get('card_reference')
            if reference is not None:
                from .card_identifiers import resolve_owned_cards
                chosen = resolve_owned_cards(session, principal.sandbox_id, principal.user_id, reference)
                if len(chosen) != 1: return False
                cards = [row for row in cards if row.id == chosen[0].id]
            current = [debit_card_status_row(session, row, include_internal=True) for row in cards]
            if marker != balance_scope(current, currencies, reference) or result.get('credit_cards') != []: return False
            stored = result.get('cards')
            if not isinstance(stored, list) or len(stored) != len(current): return False
            expected = {row['card_id']: project_balance_row(row, currencies) for row in current}
            for row in stored:
                actual = expected.pop(row.get('card_id'), None)
                if actual is None or row != actual: return False
            if expected: return False
        except (ValueError, KeyError, TypeError, AttributeError): return False
    if "card_status_scope" not in result:
        return True
    marker = result["card_status_scope"]
    if not isinstance(marker, dict) or set(marker) != {"schema_version", "card_type", "card_reference"} \
            or marker["schema_version"] != "card-status-scope-v1" or not isinstance(marker["card_type"], str) \
            or marker["card_type"] not in {"DEBIT", "ALL"}:
        return False
    reference = marker["card_reference"]
    if reference is not None and (not isinstance(reference, str) or not reference): return False
    if getattr(principal, "family_context", None): return False
    from .permissions import require_banking_actor
    require_banking_actor(session, principal)
    cards = list(session.scalars(select(Card).where(Card.sandbox_id == principal.sandbox_id,
        Card.user_id == principal.user_id, Card.status != ARCHIVED_RESOURCE_STATUS)))
    if reference:
        from .card_identifiers import resolve_owned_cards
        selected = resolve_owned_cards(session, principal.sandbox_id, principal.user_id, reference)
        if len(selected) != 1: return False
        cards = [card for card in cards if card.id == selected[0].id]
    stored = result.get("cards")
    if not isinstance(stored, list) or len(stored) != len(cards) \
            or {row.get("card_id") for row in stored if isinstance(row, dict)} != {card.id for card in cards}:
        return False
    if marker["card_type"] == "DEBIT":
        return result.get("credit_cards") == [] and all(row.get("instrument_type") == "DEBIT" for row in stored)
    from .credit_service import credit_view
    expected = credit_view(session, principal)["cards"] if reference is None else []
    return result.get("credit_cards") == expected


def prepare_card_task(session: Session, *, task: Task, conversation_id: str, card_type=None, card_reference=None,
        card_balance_currencies=None, currency=None) -> Task:
    from .contracts import DomainError
    if card_type is not None and (not isinstance(card_type, str) or card_type not in {"DEBIT", "ALL"}):
        raise DomainError("CARD_TYPE_INVALID", "卡片查询范围无效，请选择借记卡或全部银行卡。", 422)
    parsed = parse_card_request(task.message)
    requested = None
    if card_balance_currencies is not None:
        from .card_balance_scope import canonical_currencies, check_scalar_currency
        try:
            requested = canonical_currencies(card_balance_currencies)
            check_scalar_currency(requested, currency)
        except ValueError as error: raise DomainError(str(error), '请核对本次查询的明确币种范围。', 422) from None
        if parsed is not None or card_type == 'ALL':
            raise DomainError('CARD_BALANCE_SCOPE_DEBIT_ONLY', '该币种集合只查询本人借记卡现金，不含信用额度或卡片操作。', 422)
    if card_type is not None and parsed is not None:
        raise DomainError("CARD_TYPE_READ_ONLY", "卡片类别筛选仅用于查询，不授予卡片操作权限。", 422)
    if parsed is None:
        if _wants_card_status(task.message):
            cards = list(session.scalars(select(Card).where(
                Card.sandbox_id == task.sandbox_id,
                Card.user_id == task.user_id,
                Card.status != ARCHIVED_RESOURCE_STATUS,
            )))
            if card_type is not None or requested is not None:
                if card_reference:
                    from .card_identifiers import resolve_owned_cards
                    selected = resolve_owned_cards(session, task.sandbox_id, task.user_id, card_reference)
                    if len(selected) != 1:
                        raise DomainError("CARD_TYPE_REFERENCE_MISMATCH", "请提供本人借记卡的准确标识。", 422)
                    cards = [card for card in cards if card.id == selected[0].id]
            else:
                cards = _requested_cards(session, task, cards)
            if not cards:
                task.result_json = {"message": "未找到匹配的卡片。"}
                return task
            from .card_identifiers import card_identifier_view
            rows = [debit_card_status_row(session, card, include_internal=True) for card in cards]
            # Separate liability DTO: never add credit limits to old cash cards.
            has_reference = bool(re.search(r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|(?:尾号|末四位)\s*[0-9a-fA-F]{4}", task.message))
            from .credit_service import credit_view
            from .services import Principal
            credit_rows = credit_view(session, Principal(task.user_id, task.sandbox_id))["cards"] if requested is None and card_type != "DEBIT" and not has_reference else []
            scoped = {"card_status_scope": {"schema_version": "card-status-scope-v1", "card_type": card_type,
                "card_reference": card_reference}} if card_type is not None else {}
            if requested is not None:
                from .card_balance_scope import project_balance_row, balance_scope
                scoped['card_balance_scope'] = balance_scope(rows, requested, card_reference)
                rows = [project_balance_row(row, requested) for row in rows]
            snapshot = EvidenceSnapshot(
                task_id=task.id, sandbox_id=task.sandbox_id,
                source_kind="SYNTHETIC_CARD_STATUS", source_version="seed-v1",
                payload_json={"cards": rows, "credit_cards": credit_rows, **scoped},
            )
            session.add(snapshot)
            session.flush()
            task.plan_json = {"nodes": [{"node_id": "read-card-status", "tool_name": "card.status.v1",
                                        "status": "SUCCEEDED", "read_only": True,
                                        "evidence_id": snapshot.id}]}
            add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
            fic = FinancialIntentContract.model_validate(task.fic_json)
            fic.requested_capabilities = ["card.status.v1"]
            fic.status = "READY"
            task.fic_json = fic.model_dump()
            transition_task(task, "SUCCEEDED")
            task.result_json = {"message": "已查询借记卡状态。" if card_type == "DEBIT" else "已查询卡片状态，未执行任何卡片操作。",
                "cards": rows, "credit_cards": credit_rows, **scoped}
            append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                         event_type="CARD_STATUS_READ", payload={"count": len(rows)})
            append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                           task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
            return task
        task.result_json = {"message": "请明确卡片操作和必要金额，例如：临时锁卡，或将卡片限额调整到3000元。"}
        return task
    action, limit_minor = parsed
    cards = list(session.scalars(select(Card).where(
        Card.sandbox_id == task.sandbox_id,
        Card.user_id == task.user_id,
        Card.status != ARCHIVED_RESOURCE_STATUS,
    )))
    cards = _requested_cards(session, task, cards)
    if len(cards) != 1:
        task.result_json = {"message": "无法唯一确定卡片，请提供卡片尾号。"}
        return task
    card = cards[0]
    from .card_identifiers import card_identifier_view
    identity = card_identifier_view(session, card) or {}
    if action == "LOCK" and card.status != "ACTIVE":
        task.result_json = {"message": "仅正常卡片可以临时锁定。"}
        return task
    if action == "UNLOCK" and card.status != "LOCKED":
        task.result_json = {"message": "仅临时锁定的卡片可以解锁；挂失卡不能通过此操作恢复。"}
        return task
    if action == "REPORT_LOSS" and card.status not in {"ACTIVE", "LOCKED"}:
        task.result_json = {"message": "当前卡片状态不支持重复挂失。"}
        return task
    if action == "REQUEST_REPLACEMENT" and card.status != "LOST":
        task.result_json = {"message": "请先挂失，再提交补卡申请。"}
        return task
    if action == "REQUEST_REPLACEMENT" and session.scalar(select(CardReplacementRequest).where(
        CardReplacementRequest.sandbox_id == task.sandbox_id,
        CardReplacementRequest.user_id == task.user_id,
        CardReplacementRequest.card_id == card.id,
        CardReplacementRequest.status == "REQUESTED",
    )) is not None:
        task.result_json = {"message": "该卡已有待处理的补卡申请，请勿重复提交。"}
        return task
    if action == "SET_LIMIT":
        if card.status != "ACTIVE":
            task.result_json = {"message": "仅正常卡片可以调整限额。"}
            return task
        if limit_minor is None or not 10_000 <= limit_minor <= 2_000_000:
            task.result_json = {"message": "单笔限额可设为100至20000元，请明确金额。"}
            return task
        if limit_minor == card.limit_minor:
            task.result_json = {"message": "目标限额与当前限额相同，无需更改。"}
            return task

    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.slots = {
        "card_id": {"value": card.id, "source": "AUTHORITATIVE", "last4": card.last4},
        "action": {"value": action, "source": "USER_TEXT"},
    }
    if limit_minor is not None:
        fic.slots["limit_minor"] = {"value": limit_minor, "source": "USER_TEXT"}
    fic.requested_capabilities = ["card.manage.v1"]
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    plan = {
        "plan_version": 1,
        "policy_version": "card-management-v1",
        "task_id": task.id,
        "sandbox_id": task.sandbox_id,
        "nodes": [{
            "node_id": "manage-card",
            "tool_name": "card.manage.v1",
            "read_only": False,
            "card_id": card.id,
            "card_last4": card.last4,
            **identity,
            "expected_card_version": card.version,
            "action": action,
            "limit_minor": limit_minor,
            "requires_confirmation": True,
        }],
    }
    task.plan_json = plan
    task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id,
                  node_key="manage-card", decision="REQUIRE_CONFIRMATION",
                  reason_codes=["CARD_STATE_CHANGE", f"ACTION_{action}"],
                  policy_version="card-management-v1")
    transition_task(task, "AWAITING_CONFIRMATION")
    amount_text = f"为 {limit_minor / 100:.2f} 元" if limit_minor is not None else ""
    task.result_json = {
        "message": f"准备对卡片 {identity.get('card_number') or ('旧兼容尾号 '+card.last4)}{ACTION_LABELS[action]}{amount_text}，确认后执行。",
        "simulated": True,
        "card_id": card.id,
        **identity,
    }
    confirmation = Confirmation(
        sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
        node_key="manage-card", action_type="card.manage.v1", target_id=card.id,
        plan_hash=task.plan_hash,
        view_hash=canonical_hash({"plan_hash": task.plan_hash, "card_last4": card.last4,
                                  "action": action, "limit_minor": limit_minor}),
        expires_at=utcnow() + timedelta(minutes=5),
    )
    session.add(confirmation)
    session.flush()
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                 event_type="CARD_PLAN_PROPOSED",
                 payload={"action": action, "card_last4": card.last4, "plan_hash": task.plan_hash})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                   task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
    return task
