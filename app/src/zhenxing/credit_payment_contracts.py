"""Server-only payment contracts shared by cash, credit and bounded plans.

Resolvers never write. Posting functions use the caller's transaction and never
commit, call a model, fetch a rate, or manufacture a confirmation. Card control
and binding checks remain mandatory even for a durable mandate. Only the cash
balance version may be refreshed under CARD_BINDING/IDENTITY_AND_CONTROLS.

Lock order: business User(s) sorted by ID -> domain mandate/occurrence/order ->
accounts or credit facilities sorted by ID -> card/binding -> reservation ->
business inventory. Cross-subject callers obtain their identity locks before
all business Users. Never acquire a User after an Account, or an Account after
a card, in the opposite order.
"""
from typing import Literal, TypedDict


InstrumentType = Literal["DEBIT", "CREDIT"]
VersionPolicy = Literal["STRICT", "CARD_BINDING", "IDENTITY_AND_CONTROLS"]
Currency = Literal["CNY", "USD"]
PAYMENT_SNAPSHOT_VERSION = "payment-instrument-v1"
PAYMENT_RECEIPT_VERSION = "payment-receipt-v1"
REFUND_RECEIPT_VERSION = "payment-refund-receipt-v1"
CASH_ONLY_PURPOSES = frozenset({"TRANSFER", "FX", "WEALTH", "LIFE_PLAN", "REPAYMENT"})
PURPOSES = frozenset({*CASH_ONLY_PURPOSES, "MERCHANT", "READ"})
VERSION_POLICIES = frozenset({"STRICT", "CARD_BINDING", "IDENTITY_AND_CONTROLS"})


class InstrumentSnapshot(TypedDict, total=False):
    schema_version: str
    instrument_id: str
    instrument_type: InstrumentType
    card_id: str
    card_number: str
    display_last4: str
    sandbox_id: str
    user_id: str
    account_id: str | None
    credit_account_id: str | None
    wallet_id: str | None
    balance_ref: str
    currency: Currency
    status: str
    card_version: int
    account_version: int
    binding_version: int
    default_version: int | None
    selection_kind: str
    cash_balance_minor: int
    available_balance_minor: int
    base_credit_limit_minor: int
    posted_exposure_minor: int
    authorization_exposure_minor: int
    available_credit_minor: int
    paymentable: bool
    purpose: str
    scope_hash: str


class PaymentReceipt(TypedDict, total=False):
    schema_version: str
    payment_id: str
    reservation_id: str | None
    operation_key: str
    status: Literal["POSTED"]
    instrument_id: str
    instrument_type: InstrumentType
    account_id: str | None
    credit_account_id: str | None
    amount_minor: int
    currency: Currency
    merchant_ref: str
    authorization_ref: str | None
    cash_debit_minor: int
    credit_debit_minor: int
    base_exposure_minor: int
    ledger_balanced: bool
    posted_at: str
    replayed: bool


# Public service signatures (implemented in payment_instruments):
# resolve_source_instrument(session, principal, card_reference=None,
#     currency='CNY', *, purpose='TRANSFER') -> InstrumentSnapshot
# resolve_recipient_instrument(session, username, card_reference=None,
#     currency='CNY', *, caller_principal=None) -> dict
# validate_instrument_snapshot(session, principal, snapshot, *, lock=True,
#     purpose='TRANSFER', amount_minor=None, version_policy='STRICT') -> dict
# validate_recipient_snapshot(session, snapshot, *, lock=True,
#     version_policy='CARD_BINDING') -> dict
# recipient_instrument_view(snapshot) -> dict  # no foreign balance/card list
# reserve_cash(session, principal, *, instrument_snapshot, amount_minor,
#     reservation_key, owner_ref, expires_at=None) -> dict
# release_cash_reservation(session, principal, reservation_id, *, operation_key)
# post_merchant_payment(session, principal, *, instrument_snapshot, amount_minor,
#     merchant_ref, operation_key, reservation_id=None, authorization_ref=None,
#     channel='MERCHANT', fx_quote_id=None) -> PaymentReceipt
# refund_merchant_payment(session, principal, payment_id, *, operation_key,
#     authorization_ref=None) -> dict
