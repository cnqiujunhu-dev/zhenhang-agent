"""Bind an FX recipient selector to the separately declared payee.

This resolves structured, source-checked fields only; it never infers intent or
uses a receiving card as a funding card. Quotes without the new field retain
their original recipient context and do not gain a new historical scope.
"""
from .contracts import DomainError


def fx_recipient_context(session, sandbox_id, user_id, payee_reference, selector=None):
    from .contacts import recipient_context
    declared = recipient_context(session, sandbox_id, user_id, payee_reference)
    if not selector:
        return declared, None
    from .services import Principal
    from .transfer_payment_scope import selected_recipient_reference
    # The shared helper resolves the card *within the declared owner's scope*;
    # a different person's full number cannot redefine that owner.
    number = selected_recipient_reference(session, Principal(user_id, sandbox_id), payee_reference, selector)
    selected = recipient_context(session, sandbox_id, user_id, number)
    if (selected["recipient_login_account_id"] != declared["recipient_login_account_id"]
            or declared.get("recipient_card_id") and declared["recipient_card_id"] != selected["recipient_card_id"]
            or declared.get("alias_read") and declared["recipient_bank_account_id"] != selected["recipient_bank_account_id"]):
        raise DomainError("FX_RECIPIENT_CARD_CONFLICT", "声明收款身份与明确收款卡冲突，未替换收款对象或默认卡。", 409)
    return selected, declared


def fx_recipient_instrument(session, principal, username, selector, currency):
    from .recipient_card_selection import REGISTERED_DEFAULT
    from .transfer_payment_scope import _registered_recipient_default
    from .payment_instruments import resolve_recipient_instrument
    if selector == REGISTERED_DEFAULT:
        return _registered_recipient_default(session, principal, username, currency)
    return resolve_recipient_instrument(session, username, selector, currency, caller_principal=principal)


def validate_fx_recipient_contexts(session, quote):
    from .contacts import validate_recipient_context
    scope = quote.scope_json
    # The declared identity remains a separate risk subject, not a replacement
    # by the full receiving-card reference. Legacy quotes lack this key.
    declared = scope.get("declared_recipient_context")
    if declared:
        target = scope["target"]
        if (declared.get("recipient_login_account_id"), declared.get("recipient_username")) != (
                target.get("login_id"), target.get("username")):
            raise DomainError("FX_RECIPIENT_CARD_CONFLICT", "声明收款主体与报价收款卡不一致。", 409)
        validate_recipient_context(session, quote.sandbox_id, quote.user_id, declared)
    if scope.get("recipient_context"):
        validate_recipient_context(session, quote.sandbox_id, quote.user_id, scope["recipient_context"])
