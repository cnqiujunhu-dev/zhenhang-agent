"""One signed funding/receiving scope for transfer and scheduled occurrences."""
from copy import deepcopy

from .contracts import DomainError
from .planning import canonical_hash


VERSION = "transfer-payment-scope-v1"


def _registered_recipient_default(session, principal, username, currency):
    """Require an actual default row, never the facade's legacy fallback."""
    from sqlalchemy import select
    from .access_models import LoginAccount
    from .credit_models import CustomerPaymentDefaults
    from .payment_instruments import resolve_recipient_instrument
    login = session.scalar(select(LoginAccount).where(LoginAccount.username == username,
        LoginAccount.status == "ACTIVE", LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"})))
    defaults = session.scalar(select(CustomerPaymentDefaults).where(CustomerPaymentDefaults.user_id == login.user_id,
        CustomerPaymentDefaults.sandbox_id == login.sandbox_id).execution_options(populate_existing=True)) if login else None
    if defaults is None or not isinstance(defaults.recipient_instrument_id, str) \
            or not defaults.recipient_instrument_id.startswith("debit:"):
        raise DomainError("RECIPIENT_REGISTERED_DEFAULT_REQUIRED", "收款人尚无有效登记默认借记卡，请明确收款卡；不会任取其他卡。", 422)
    expected = defaults.recipient_instrument_id, defaults.version
    selected = resolve_recipient_instrument(session, username, None, currency, caller_principal=principal)
    if selected.get("selection_kind") != "DEFAULT" or (selected.get("instrument_id"), selected.get("default_version")) != expected:
        raise DomainError("RECIPIENT_DEFAULT_CHANGED", "登记默认收款卡在核对中发生变化，请重新确认。", 409)
    return selected


def recipient_card_number(session, snapshot):
    from .models import Card
    from .card_identifiers import card_identifier_view
    card = session.get(Card, snapshot.get("card_id"))
    if card is None or (card.user_id, card.sandbox_id, card.account_id) != (
            snapshot.get("user_id"), snapshot.get("sandbox_id"), snapshot.get("account_id")):
        raise DomainError("TRANSFER_RECIPIENT_INSTRUMENT_MISMATCH", "收款卡与登记账户不一致。", 409)
    number = (card_identifier_view(session, card) or {}).get("card_number")
    if not number:
        raise DomainError("TRANSFER_RECIPIENT_INSTRUMENT_MISMATCH", "收款卡登记标识尚未核实。", 409)
    return number


def selected_source_reference(session, principal, values):
    from .payment_instruments import resolve_source_instrument
    full, tail = values.get("card_number"), values.get("card_last4")
    if full and tail:
        first = resolve_source_instrument(session, principal, full, "CNY", purpose="TRANSFER")
        second = resolve_source_instrument(session, principal, tail, "CNY", purpose="TRANSFER")
        if first["instrument_id"] != second["instrument_id"]:
            raise DomainError("TRANSFER_SOURCE_CARD_CONFLICT", "明示付款卡号与尾号不是同一张卡，未替换付款卡。", 409)
    return full or tail


def selected_recipient_reference(session, principal, payee_reference, explicit):
    if not explicit:
        return payee_reference
    from .contacts import recipient_context
    from .payment_instruments import resolve_recipient_instrument
    original = recipient_context(session, principal.sandbox_id, principal.user_id, payee_reference)
    from .recipient_card_selection import REGISTERED_DEFAULT
    selected = (_registered_recipient_default(session, principal, original["recipient_username"], "CNY")
        if explicit == REGISTERED_DEFAULT else resolve_recipient_instrument(session,
            original["recipient_username"], explicit, "CNY", caller_principal=principal))
    if selected["user_id"] == principal.user_id:
        raise DomainError("SELF_TRANSFER_FORBIDDEN", "不能把本人卡作为对方收款卡。", 409)
    return recipient_card_number(session, selected)


def _public_instrument(snapshot):
    return {key: deepcopy(snapshot.get(key)) for key in
            ("instrument_type", "display_last4", "currency", "username") if snapshot.get(key) is not None}


def resolve_transfer_payment_scope(session, principal, *, payee, payee_reference,
        source_card_reference=None, recipient_card_reference=None, amount_minor, currency="CNY", require_available=True):
    from .internal_banking import recipient_scope
    from .payment_instruments import resolve_source_instrument, resolve_recipient_instrument
    source = resolve_source_instrument(session, principal, source_card_reference, currency, purpose="TRANSFER")
    if source.get("instrument_type") != "DEBIT" or source.get("paymentable") is not True:
        raise DomainError("TRANSFER_SOURCE_UNAVAILABLE", "请选择本人可用于转账的借记卡。", 409)
    if type(amount_minor) is not int or amount_minor <= 0:
        raise DomainError("TRANSFER_AMOUNT_INVALID", "转账金额须为正整数分。", 422)
    if amount_minor > source.get("transaction_limit_minor", 0):
        raise DomainError("TRANSFER_SOURCE_CARD_LIMIT", "金额超过明示或默认付款卡的单笔限额。", 409)
    if require_available and amount_minor > source.get("available_balance_minor", 0):
        raise DomainError("TRANSFER_SOURCE_AVAILABLE_FUNDS", "指定付款账户当前可支出余额不足，未改用其他卡。", 409)
    destination = recipient_scope(session, payee)
    target = None
    if destination["transfer_channel"] == "INTERNAL_BANK":
        from .contacts import recipient_context
        context = recipient_context(session, principal.sandbox_id, principal.user_id, payee_reference)
        from .recipient_card_selection import REGISTERED_DEFAULT
        if recipient_card_reference == REGISTERED_DEFAULT:
            target = _registered_recipient_default(session, principal, destination["recipient_username"], currency)
        else:
            exact = recipient_card_reference or context.get("recipient_card_number")
            target = resolve_recipient_instrument(session, destination["recipient_username"], exact, currency,
                                                   caller_principal=principal)
        if target.get("account_id") != destination["recipient_account_id"] or target.get("instrument_type") != "DEBIT":
            raise DomainError("TRANSFER_RECIPIENT_INSTRUMENT_MISMATCH", "明确收款卡与收款账户不一致，未替换收款对象。", 409)
    elif recipient_card_reference == "REGISTERED_DEFAULT":
        raise DomainError("RECIPIENT_REGISTERED_DEFAULT_REQUIRED", "外部收款对象没有本地登记默认借记卡，请明确收款信息。", 422)
    body = {"schema_version": VERSION, "source": source, "recipient": target,
            "recipient_external": None if target else {"display_last4": payee.account_last4, "currency": currency},
            "currency": currency, "amount_minor": amount_minor,
            "display": {"source": _public_instrument(source),
                        "recipient": _public_instrument(target) if target else {"display_last4": payee.account_last4, "currency": currency},
                        "amount_minor": amount_minor, "currency": currency}}
    body["scope_hash"] = canonical_hash(body)
    return body


def validate_transfer_payment_scope(session, principal, scope, *, scheduled=False, require_funds=True, lock=True):
    from .payment_instruments import validate_instrument_snapshot, validate_recipient_snapshot
    if (not isinstance(scope, dict) or scope.get("schema_version") != VERSION
            or not isinstance(scope.get("source"),dict) or not isinstance(scope.get("amount_minor"),int)
            or scope.get("recipient") is not None and not isinstance(scope["recipient"],dict)
            or scope.get("currency")!="CNY" or (scope.get("source") or {}).get("currency")!="CNY"
            or scope.get("recipient") is not None and scope["recipient"].get("currency")!="CNY"
            or scope.get("scope_hash") != canonical_hash({key: value for key, value in scope.items() if key != "scope_hash"})):
        raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED", "收付款卡范围发生变化，请重新核对确认。", 409)
    policy = "CARD_BINDING" if scheduled else "STRICT"
    source = validate_instrument_snapshot(session, principal, scope["source"], lock=lock, purpose="TRANSFER",
        amount_minor=scope["amount_minor"] if require_funds else None, version_policy=policy)
    if source.get("instrument_type") != "DEBIT":
        raise DomainError("CREDIT_CARD_CASH_CHANNEL_FORBIDDEN", "信用卡不能作为现金转账资金来源。", 403)
    if _public_instrument(source)!=_public_instrument(scope["source"]):
        raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED", "付款卡展示身份已变化，请重新核对。", 409)
    if type(scope.get("amount_minor")) is not int or not 0<scope["amount_minor"]<=source.get("transaction_limit_minor",0):
        raise DomainError("TRANSFER_SOURCE_CARD_LIMIT", "确认金额超出指定付款卡当前限额。", 409)
    if scope.get("recipient") is not None:
        target=validate_recipient_snapshot(session, scope["recipient"], lock=lock, version_policy=policy)
        if _public_instrument(target)!=_public_instrument(scope["recipient"]):
            raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED", "收款卡展示身份已变化，请重新核对。", 409)
    return source


def transfer_confirmation_scope(scope):
    """No recipient balances, limits, owner IDs, or catalog of other cards."""
    if not isinstance(scope,dict) or scope.get("schema_version")!=VERSION \
            or not isinstance(scope.get("source"),dict) \
            or scope.get("recipient") is not None and not isinstance(scope["recipient"],dict) \
            or scope.get("scope_hash")!=canonical_hash({key:value for key,value in scope.items() if key!="scope_hash"}):
        return None
    expected={"source":_public_instrument(scope.get("source") or {}),
        "recipient":_public_instrument(scope["recipient"]) if scope.get("recipient") else deepcopy(scope.get("recipient_external")),
        "amount_minor":scope.get("amount_minor"),"currency":scope.get("currency")}
    if type(expected["amount_minor"]) is not int or expected["amount_minor"]<=0 or expected["currency"]!="CNY" \
            or any(not isinstance(expected[key],dict) or not isinstance(expected[key].get("display_last4"),str)
                or len(expected[key]["display_last4"])!=4 or expected[key].get("currency")!="CNY" for key in ("source","recipient")):
        return None
    return deepcopy(expected) if scope.get("display")==expected else None
