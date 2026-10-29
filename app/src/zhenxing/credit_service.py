"""Transactional dual-currency credit book, sharing one CNY exposure pool.

No network, commits, automatic credit scoring or cash-account facsimiles. Public
write entry points are called only after task confirmation/merchant mandate.
Every replay is bound to the owner and the immutable economic command.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .credit_models import (CreditAccount, CreditAuthorization, CreditCard, CreditCurrencyBalance, CreditFxQuote,
    CreditObligation, CreditOperation, CreditRepayment, CreditRepaymentAllocation, CreditStatement,
    PaymentRecord, PaymentRefund)
from .customer_profile_common import require_profile_actor
from .models import Account, Transaction, new_id, utcnow
from .planning import canonical_hash
from .credit_atomic import atomic_domain


def _fail(code, text="信用卡资金方案已变化，请重新核对。", status=409):
    raise DomainError(code, text, status)


def _facility(session, principal, facility_id, *, lock=True):
    require_profile_actor(session, principal)
    query = select(CreditAccount).where(CreditAccount.id == facility_id, CreditAccount.user_id == principal.user_id,
        CreditAccount.sandbox_id == principal.sandbox_id)
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None: _fail("CREDIT_OWNER_REQUIRED", status=403)
    return row


def _balance(session, facility, currency):
    if currency not in {"CNY", "USD"}: _fail("CREDIT_CURRENCY_INVALID", status=422)
    row = session.scalar(select(CreditCurrencyBalance).where(CreditCurrencyBalance.credit_account_id == facility.id,
        CreditCurrencyBalance.currency == currency).execution_options(populate_existing=True))
    if row is None: _fail("CREDIT_BOOK_INCOMPLETE")
    return row


def _operation(session, principal, key, kind, material):
    from .payment_instruments import _key
    _key(key); require_profile_actor(session, principal, lock=True)
    digest = canonical_hash({"kind": kind, **material})
    prior = session.scalar(select(CreditOperation).where(CreditOperation.sandbox_id == principal.sandbox_id,
        CreditOperation.user_id == principal.user_id, CreditOperation.operation_key == key))
    if prior and (prior.kind != kind or prior.request_hash != digest): _fail("CREDIT_IDEMPOTENCY_CONFLICT")
    return digest, ({**deepcopy(prior.result_json), "replayed": True} if prior else None)


def _record(session, principal, key, kind, digest, result):
    session.add(CreditOperation(sandbox_id=principal.sandbox_id, user_id=principal.user_id, operation_key=key,
        kind=kind, request_hash=digest, result_json=deepcopy(result)))
    session.flush()
    return result


@atomic_domain
def prepare_credit_fx_quote(session, principal, amount_minor, market_rate):
    """Only a server-acquired rate may be passed by runtime; no client rate slot."""
    from .fx_rates import validate_market_rate, QUOTE_TTL
    from .payment_instruments import _amount
    require_profile_actor(session, principal); _amount(amount_minor)
    now = as_utc(utcnow()); checked = validate_market_rate(market_rate, now=now)
    exposure = int((Decimal(amount_minor) * Decimal(checked["rate"])).to_integral_value(rounding=ROUND_CEILING))
    material = {"sandbox_id": principal.sandbox_id, "user_id": principal.user_id, "currency": "USD",
        "amount_minor": amount_minor, "rate": checked["rate"], "base_exposure_minor": exposure,
        "source": checked, "expires_at": (now + QUOTE_TTL).isoformat()}
    row = CreditFxQuote(sandbox_id=principal.sandbox_id, user_id=principal.user_id, amount_minor=amount_minor,
        rate=Decimal(checked["rate"]), base_exposure_minor=exposure, source_json=material,
        scope_hash=canonical_hash(material), expires_at=now + QUOTE_TTL)
    session.add(row); session.flush()
    return {"quote_id": row.id, "currency": "USD", "amount_minor": amount_minor, "base_currency": "CNY",
        "base_exposure_minor": exposure, "rate": checked["rate"], "expires_at": as_utc(row.expires_at).isoformat(),
        "source": deepcopy(checked), "quote_hash": row.scope_hash}


def _exposure(session, principal, currency, amount, quote_id):
    if currency == "CNY":
        if quote_id: _fail("CREDIT_FX_QUOTE_UNEXPECTED")
        return amount
    row = session.get(CreditFxQuote, quote_id) if quote_id else None
    if (not row or (row.sandbox_id, row.user_id, row.currency, row.amount_minor) !=
            (principal.sandbox_id, principal.user_id, "USD", amount)
            or as_utc(row.expires_at) <= as_utc(utcnow()) or canonical_hash(row.source_json) != row.scope_hash
            or row.source_json.get("base_exposure_minor") != row.base_exposure_minor
            or Decimal(row.source_json.get("rate", "0")) != row.rate):
        _fail("CREDIT_VERIFIED_FX_QUOTE_REQUIRED", "美元信用交易需先核对服务器汇率及人民币共享额度占用。")
    return row.base_exposure_minor


def _capacity(facility, additional):
    if facility.status != "ACTIVE" or additional > facility.base_credit_limit_minor - facility.posted_exposure_minor - facility.authorization_exposure_minor:
        _fail("CREDIT_SHARED_LIMIT_EXCEEDED", "人民币和美元共用的授信可用额度不足。")


@atomic_domain
def authorize_credit_purchase(session, principal, *, instrument_snapshot, amount_minor, merchant_ref,
        operation_key, fx_quote_id=None, expires_at=None):
    from .payment_instruments import _amount, validate_instrument_snapshot
    _amount(amount_minor)
    digest, prior = _operation(session, principal, operation_key, "AUTHORIZE", {"instrument": instrument_snapshot.get("instrument_id"),
        "amount_minor": amount_minor, "currency": instrument_snapshot.get("currency"), "merchant_ref": merchant_ref,
        "fx_quote_id": fx_quote_id, "expires_at": as_utc(expires_at).isoformat() if expires_at else None})
    if prior: return prior
    fresh = validate_instrument_snapshot(session, principal, instrument_snapshot, purpose="MERCHANT")
    if fresh["instrument_type"] != "CREDIT": _fail("CREDIT_INSTRUMENT_REQUIRED")
    facility = _facility(session, principal, fresh["credit_account_id"])
    exposure = _exposure(session, principal, fresh["currency"], amount_minor, fx_quote_id)
    if exposure > fresh["transaction_limit_minor"]: _fail("PAYMENT_CARD_LIMIT_EXCEEDED")
    if not isinstance(merchant_ref, str) or not merchant_ref or len(merchant_ref) > 120: _fail("PAYMENT_MERCHANT_REQUIRED", status=422)
    expires = as_utc(expires_at or utcnow() + timedelta(days=7))
    if expires <= as_utc(utcnow()) or expires > as_utc(utcnow()) + timedelta(days=30): _fail("CREDIT_AUTH_EXPIRY_INVALID")
    _capacity(facility, exposure)
    auth = CreditAuthorization(sandbox_id=principal.sandbox_id, user_id=principal.user_id, credit_account_id=facility.id,
        card_id=fresh["card_id"], currency=fresh["currency"], amount_minor=amount_minor, remaining_minor=amount_minor,
        remaining_exposure_minor=exposure, original_exposure_minor=exposure, merchant_ref=merchant_ref,
        operation_key=operation_key, request_hash=digest, fx_quote_id=fx_quote_id, expires_at=expires)
    session.add(auth); facility.authorization_exposure_minor += exposure; facility.version += 1; session.flush()
    result = {"authorization_id": auth.id, "status": "AUTHORIZED", "currency": auth.currency,
        "amount_minor": amount_minor, "base_exposure_minor": exposure, "credit_account_id": facility.id,
        "expires_at": expires.isoformat(), "cash_debit_minor": 0, "replayed": False}
    return _record(session, principal, operation_key, "AUTHORIZE", digest, result)


def _post_credit(session, principal, *, facility, card_id, currency, amount_minor, exposure, merchant_ref,
        operation_key, payment_hash, authorization_ref=None):
    from .payment_instruments import _journal
    balance = _balance(session, facility, currency)
    credit_used = min(balance.credit_balance_minor, amount_minor)
    principal_amount = amount_minor - credit_used
    actual_exposure = exposure if not credit_used else (exposure * principal_amount+amount_minor-1) // amount_minor
    _capacity(facility, actual_exposure)
    balance.credit_balance_minor -= credit_used; balance.principal_minor += principal_amount; balance.version += 1
    facility.posted_exposure_minor += actual_exposure; facility.version += 1
    payment_id = new_id(); now = as_utc(utcnow())
    journal = _journal(session, principal, operation_key=operation_key, request_hash=payment_hash, kind="CREDIT_PURCHASE",
        entries=[("CREDIT:" + facility.id, currency, -amount_minor), ("MERCHANT:" + merchant_ref, currency, amount_minor)])
    receipt = {"schema_version": "payment-receipt-v1", "payment_id": payment_id, "reservation_id": None,
        "operation_key": operation_key, "status": "POSTED", "instrument_id": "credit:" + card_id,
        "instrument_type": "CREDIT", "account_id": None, "credit_account_id": facility.id,
        "amount_minor": amount_minor, "currency": currency, "merchant_ref": merchant_ref,
        "authorization_ref": authorization_ref, "cash_debit_minor": 0, "credit_debit_minor": principal_amount,
        "credit_balance_used_minor": credit_used, "base_exposure_minor": actual_exposure,
        "quoted_base_exposure_minor": exposure,
        "ledger_balanced": True, "posted_at": now.isoformat(), "replayed": False}
    session.add(PaymentRecord(id=payment_id, sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        instrument_id="credit:" + card_id, instrument_type="CREDIT", credit_account_id=facility.id,
        amount_minor=amount_minor, currency=currency, merchant_ref=merchant_ref, authorization_ref=authorization_ref,
        operation_key=operation_key, request_hash=payment_hash, journal_id=journal.id, receipt_json=receipt))
    session.flush()
    session.add(CreditObligation(credit_account_id=facility.id, payment_id=payment_id, currency=currency,
        amount_minor=principal_amount, remaining_minor=principal_amount, remaining_exposure_minor=actual_exposure))
    journal.result_json = receipt; session.flush()
    return receipt


@atomic_domain
def post_credit_purchase(session, principal, *, instrument_snapshot, amount_minor, merchant_ref, operation_key,
        fx_quote_id=None, authorization_ref=None):
    from .payment_instruments import _amount, _key, validate_instrument_snapshot
    _amount(amount_minor); _key(operation_key); require_profile_actor(session, principal, lock=True)
    # Same economic hash as facade, so its replay check remains authoritative.
    material = {"instrument_id": instrument_snapshot.get("instrument_id"), "account_id": None,
        "credit_account_id": instrument_snapshot.get("credit_account_id"), "currency": instrument_snapshot.get("currency"),
        "amount_minor": amount_minor, "merchant_ref": merchant_ref, "reservation_id": None,
        "authorization_ref": authorization_ref, "channel": "MERCHANT", "fx_quote_id": fx_quote_id}
    digest = canonical_hash(material)
    prior = session.scalar(select(PaymentRecord).where(PaymentRecord.sandbox_id == principal.sandbox_id,
        PaymentRecord.user_id == principal.user_id, PaymentRecord.operation_key == operation_key))
    if prior:
        if prior.request_hash != digest: _fail("PAYMENT_IDEMPOTENCY_CONFLICT")
        return {**deepcopy(prior.receipt_json), "replayed": True}
    fresh = validate_instrument_snapshot(session, principal, instrument_snapshot, purpose="MERCHANT")
    if fresh["instrument_type"] != "CREDIT": _fail("CREDIT_INSTRUMENT_REQUIRED")
    if not isinstance(merchant_ref, str) or not merchant_ref or len(merchant_ref) > 120: _fail("PAYMENT_MERCHANT_REQUIRED", status=422)
    facility = _facility(session, principal, fresh["credit_account_id"])
    exposure = _exposure(session, principal, fresh["currency"], amount_minor, fx_quote_id)
    if exposure > fresh["transaction_limit_minor"]: _fail("PAYMENT_CARD_LIMIT_EXCEEDED")
    return _post_credit(session, principal, facility=facility, card_id=fresh["card_id"], currency=fresh["currency"],
        amount_minor=amount_minor, exposure=exposure, merchant_ref=merchant_ref, operation_key=operation_key,
        payment_hash=digest, authorization_ref=authorization_ref)


@atomic_domain
def capture_credit_authorization(session, principal, authorization_id, *, amount_minor, operation_key):
    from .payment_instruments import _amount
    _amount(amount_minor)
    digest, prior = _operation(session, principal, operation_key, "CAPTURE", {"authorization_id": authorization_id, "amount_minor": amount_minor})
    if prior: return prior
    auth = session.get(CreditAuthorization, authorization_id)
    if not auth or (auth.user_id, auth.sandbox_id) != (principal.user_id, principal.sandbox_id): _fail("CREDIT_AUTH_NOT_OWNED", status=403)
    facility = _facility(session, principal, auth.credit_account_id)
    auth = session.scalar(select(CreditAuthorization).where(CreditAuthorization.id == auth.id).with_for_update().execution_options(populate_existing=True))
    # An already authorized merchant may settle its unexpired original scope
    # after lock/loss; it cannot enlarge, change currency or merchant.
    if auth.status not in {"AUTHORIZED", "PARTIALLY_CAPTURED"} or as_utc(auth.expires_at) <= as_utc(utcnow()) or amount_minor > auth.remaining_minor:
        _fail("CREDIT_AUTH_NOT_CAPTURABLE")
    # Round each partial allocation upward. If the unconsumed authorization is
    # then voided, a partial USD capture must not retain a rounded-down CNY
    # exposure. The final capture consumes the exact remaining locked pool.
    exposure = auth.remaining_exposure_minor if amount_minor == auth.remaining_minor else \
        (auth.remaining_exposure_minor * amount_minor + auth.remaining_minor-1) // auth.remaining_minor
    facility.authorization_exposure_minor -= exposure
    auth.remaining_minor -= amount_minor; auth.remaining_exposure_minor -= exposure
    auth.status = "CAPTURED" if auth.remaining_minor == 0 else "PARTIALLY_CAPTURED"
    result = _post_credit(session, principal, facility=facility, card_id=auth.card_id, currency=auth.currency,
        amount_minor=amount_minor, exposure=exposure, merchant_ref=auth.merchant_ref, operation_key=operation_key,
        payment_hash=digest, authorization_ref=auth.id)
    result = {**result, "authorization_remaining_minor": auth.remaining_minor,
        "capture_policy": "HONOR_UNEXPIRED_ORIGINAL_AMOUNT"}
    return _record(session, principal, operation_key, "CAPTURE", digest, result)


@atomic_domain
def void_credit_authorization(session, principal, authorization_id, *, operation_key):
    digest, prior = _operation(session, principal, operation_key, "VOID", {"authorization_id": authorization_id})
    if prior: return prior
    auth = session.get(CreditAuthorization, authorization_id)
    if not auth or (auth.user_id, auth.sandbox_id) != (principal.user_id, principal.sandbox_id): _fail("CREDIT_AUTH_NOT_OWNED", status=403)
    facility = _facility(session, principal, auth.credit_account_id)
    auth = session.scalar(select(CreditAuthorization).where(CreditAuthorization.id == auth.id).with_for_update().execution_options(populate_existing=True))
    released = auth.remaining_exposure_minor
    facility.authorization_exposure_minor -= released; facility.version += 1
    auth.remaining_exposure_minor = auth.remaining_minor = 0
    if auth.status != "CAPTURED": auth.status = "VOIDED"
    return _record(session, principal, operation_key, "VOID", digest,
        {"authorization_id": auth.id, "status": auth.status, "released_base_exposure_minor": released,
         "cash_debit_minor": 0, "replayed": False})


@atomic_domain
def expire_credit_authorizations(session, principal, *, limit=100, now=None, authorization_ids=None):
    """Bounded worker adapter; reads never implicitly release authorization."""
    require_profile_actor(session, principal, lock=True)
    if type(limit) is not int or not 1 <= limit <= 1000: _fail("CREDIT_EXPIRY_BATCH_INVALID", status=422)
    if authorization_ids is not None and (not isinstance(authorization_ids, (list, tuple))
            or not 1 <= len(authorization_ids) <= limit
            or any(not isinstance(value, str) or not 1 <= len(value) <= 36 for value in authorization_ids)
            or len(set(authorization_ids)) != len(authorization_ids)):
        _fail("CREDIT_EXPIRY_BATCH_INVALID", status=422)
    query = select(CreditAuthorization).where(CreditAuthorization.sandbox_id == principal.sandbox_id,
        CreditAuthorization.user_id == principal.user_id, CreditAuthorization.expires_at <= as_utc(now or utcnow()),
        CreditAuthorization.status.in_({"AUTHORIZED", "PARTIALLY_CAPTURED"}))
    if authorization_ids is not None:
        query = query.where(CreditAuthorization.id.in_(authorization_ids))
    rows = list(session.scalars(query.order_by(CreditAuthorization.expires_at, CreditAuthorization.id).limit(limit)))
    return [void_credit_authorization(session, principal, row.id, operation_key="expire:"+row.id) for row in rows]


@atomic_domain
def refund_credit_payment(session, principal, payment, *, operation_key, authorization_ref=None):
    from .payment_instruments import _journal
    facility = _facility(session, principal, payment.credit_account_id)
    prior = session.scalar(select(PaymentRefund).where(PaymentRefund.payment_id == payment.id))
    digest = canonical_hash({"payment_id": payment.id, "authorization_ref": authorization_ref})
    if prior:
        if prior.request_hash != digest: _fail("PAYMENT_IDEMPOTENCY_CONFLICT")
        return {**deepcopy(prior.receipt_json), "replayed": True}
    obligation = session.scalar(select(CreditObligation).where(CreditObligation.payment_id == payment.id).with_for_update())
    if not obligation or payment.status != "POSTED": _fail("CREDIT_REFUND_SOURCE_INVALID")
    balance = _balance(session, facility, payment.currency)
    reduction, exposure = obligation.remaining_minor, obligation.remaining_exposure_minor
    balance.principal_minor -= reduction; balance.credit_balance_minor += payment.amount_minor - reduction; balance.version += 1
    facility.posted_exposure_minor -= exposure; facility.version += 1
    obligation.remaining_minor = obligation.remaining_exposure_minor = 0
    if obligation.installment_id:
        from .credit_models import CreditInstallment
        installment = session.get(CreditInstallment, obligation.installment_id)
        installment.status = "REFUNDED"
    journal = _journal(session, principal, operation_key=operation_key, request_hash=digest, kind="CREDIT_REFUND",
        entries=[("CREDIT:" + facility.id, payment.currency, payment.amount_minor),
                 ("MERCHANT:" + payment.merchant_ref, payment.currency, -payment.amount_minor)])
    refund_id = new_id()
    receipt = {"schema_version": "payment-refund-receipt-v1", "refund_id": refund_id, "payment_id": payment.id,
        "account_id": None, "credit_account_id": facility.id, "reservation_id": None, "instrument_id": payment.instrument_id,
        "instrument_type": "CREDIT", "amount_minor": payment.amount_minor, "currency": payment.currency, "status": "POSTED",
        "cash_credit_minor": 0, "credit_reduction_minor": reduction, "credit_balance_added_minor": payment.amount_minor-reduction,
        "base_exposure_released_minor": exposure, "ledger_balanced": True, "operation_key": operation_key,
        "authorization_ref": authorization_ref, "replayed": False}
    session.add(PaymentRefund(id=refund_id, sandbox_id=principal.sandbox_id, user_id=principal.user_id, payment_id=payment.id,
        operation_key=operation_key, request_hash=digest, receipt_json=receipt))
    journal.result_json = receipt; payment.status = "REFUNDED"; session.flush()
    return receipt


def credit_view(session, principal, card_reference=None):
    require_profile_actor(session, principal)
    cards = list(session.scalars(select(CreditCard).where(CreditCard.sandbox_id == principal.sandbox_id,
        CreditCard.user_id == principal.user_id).order_by(CreditCard.created_at, CreditCard.id)))
    if card_reference:
        from .payment_instruments import resolve_source_instrument
        selected = resolve_source_instrument(session, principal, card_reference, purpose="READ")
        if selected["instrument_type"] != "CREDIT": _fail("CREDIT_INSTRUMENT_REQUIRED")
        cards = [card for card in cards if card.id == selected["card_id"]]
    else:
        cards = [card for card in cards if card.status != "REPLACED"]
    rows = []
    from .credit_pool_facts import current_credit_pool_contract
    for card in cards:
        facility = _facility(session, principal, card.credit_account_id, lock=False)
        balances = [_balance(session, facility, currency) for currency in ("CNY", "USD")]
        rows.append({"card_id": card.id, "card_number": card.card_number, "display_last4": card.card_number[-4:],
            "instrument_id": "credit:" + card.id, "instrument_type": "CREDIT", "status": card.status, "version": card.version,
            "credit_account_id": facility.id, "facility_version": facility.version, "base_currency": "CNY",
            "supported_currencies": ["CNY", "USD"], "shared_credit_limit": True,
            "base_credit_limit_minor": facility.base_credit_limit_minor, "used_base_exposure_minor": facility.posted_exposure_minor,
            "authorization_base_exposure_minor": facility.authorization_exposure_minor,
            "available_base_credit_minor": facility.base_credit_limit_minor-facility.posted_exposure_minor-facility.authorization_exposure_minor,
            "balances": [{"currency": b.currency, "principal_minor": b.principal_minor, "fees_minor": b.fees_minor,
                "interest_minor": b.interest_minor, "credit_balance_minor": b.credit_balance_minor,
                "outstanding_minor": b.principal_minor+b.interest_minor+b.fees_minor} for b in balances],
            "transaction_limit_minor": card.transaction_limit_minor, "policy": deepcopy(facility.policy_json),
            "credit_pool_contract": current_credit_pool_contract(facility),
            "credit_is_cash": False})
    from .credit_models import CreditApplication
    from .credit_read_common import finish_credit_read
    applications = [{"application_id": row.id, "kind": row.kind, "status": row.status, "version": row.version,
        "credit_account_id": row.credit_account_id, "requested_limit_minor": row.requested_limit_minor,
        "card_id": row.card_id} for row in session.scalars(select(CreditApplication).where(
            CreditApplication.sandbox_id == principal.sandbox_id, CreditApplication.user_id == principal.user_id).order_by(CreditApplication.created_at, CreditApplication.id))]
    if card_reference: applications = [row for row in applications if row["credit_account_id"] in {card["credit_account_id"] for card in rows}]
    return finish_credit_read(principal, "CREDIT_VIEW", {"schema_version": "credit-view-v1", "cards": rows,
        "applications": applications}, {"card_reference": card_reference})


def credit_bill_rows(session, principal, start, end, card_reference=None):
    """Economic rows only: immutable purchase/refund posting, never repayments.

    amount_minor uses the existing bill sign convention (expense negative).
    Currency is mandatory; caller must group, never sum CNY and USD.
    """
    require_profile_actor(session, principal)
    start, end = as_utc(start), as_utc(end)
    if start >= end: _fail("CREDIT_BILL_PERIOD_INVALID", status=422)
    selected = None
    if card_reference:
        from .payment_instruments import resolve_source_instrument
        selected = resolve_source_instrument(session, principal, card_reference, purpose="READ")
        if selected["instrument_type"] != "CREDIT": _fail("CREDIT_INSTRUMENT_REQUIRED")
    query = select(PaymentRecord).where(PaymentRecord.sandbox_id == principal.sandbox_id,
        PaymentRecord.user_id == principal.user_id, PaymentRecord.instrument_type == "CREDIT",
        PaymentRecord.created_at >= start, PaymentRecord.created_at < end)
    if selected: query = query.where(PaymentRecord.instrument_id == selected["instrument_id"])
    rows = []
    from .credit_receipts import credit_payment_posting_integrity, credit_refund_posting_integrity, credit_fee_posting_integrity
    for payment in session.scalars(query.order_by(PaymentRecord.created_at, PaymentRecord.id)):
        if not credit_payment_posting_integrity(session,principal,payment):_fail("CREDIT_BILL_POSTING_UNVERIFIED","信用消费账本来源未完整核验，未将其当作已确认消费。")
        rows.append({"posting_id": payment.id, "payment_id": payment.id, "journal_id": payment.journal_id,
            "instrument_id": payment.instrument_id, "card_id": payment.instrument_id[7:], "instrument_type": "CREDIT",
            "credit_account_id": payment.credit_account_id, "occurred_at": as_utc(payment.created_at).isoformat(),
            "amount_minor": -payment.amount_minor, "currency": payment.currency, "merchant": payment.merchant_ref,
            "category": "信用卡消费", "source_kind": "CREDIT_PURCHASE", "economic_kind": "CONSUMPTION",
            "posting_hash": canonical_hash(payment.receipt_json), "evidence_type": "IMMUTABLE_PAYMENT_RECEIPT"})
    refunds = select(PaymentRefund, PaymentRecord).join(PaymentRecord, PaymentRefund.payment_id == PaymentRecord.id).where(
        PaymentRecord.sandbox_id == principal.sandbox_id, PaymentRecord.user_id == principal.user_id,
        PaymentRecord.instrument_type == "CREDIT", PaymentRefund.created_at >= start, PaymentRefund.created_at < end)
    if selected: refunds = refunds.where(PaymentRecord.instrument_id == selected["instrument_id"])
    for refund, payment in session.execute(refunds):
        if not credit_refund_posting_integrity(session,principal,refund,payment):_fail("CREDIT_BILL_POSTING_UNVERIFIED","信用退款账本来源未完整核验。")
        rows.append({"posting_id": refund.id, "payment_id": payment.id, "instrument_id": payment.instrument_id,
            "card_id": payment.instrument_id[7:], "instrument_type": "CREDIT", "credit_account_id": payment.credit_account_id,
            "occurred_at": as_utc(refund.created_at).isoformat(), "amount_minor": payment.amount_minor,
            "currency": payment.currency, "merchant": payment.merchant_ref, "category": "信用卡退款",
            "source_kind": "CREDIT_REFUND", "economic_kind": "REFUND", "posting_hash": canonical_hash(refund.receipt_json),
            "evidence_type": "IMMUTABLE_REFUND_RECEIPT"})
    from .credit_models import CreditFeeCharge, CreditInstallment
    fee_query = select(CreditFeeCharge, CreditObligation, PaymentRecord).join(CreditInstallment,
        CreditFeeCharge.installment_id == CreditInstallment.id).join(CreditObligation,
        CreditInstallment.obligation_id == CreditObligation.id).join(PaymentRecord,
        CreditObligation.payment_id == PaymentRecord.id).where(PaymentRecord.sandbox_id == principal.sandbox_id,
        PaymentRecord.user_id == principal.user_id, CreditFeeCharge.created_at >= start, CreditFeeCharge.created_at < end)
    if selected: fee_query = fee_query.where(PaymentRecord.instrument_id == selected["instrument_id"])
    for fee, obligation, payment in session.execute(fee_query):
        if not credit_fee_posting_integrity(session,principal,fee):_fail("CREDIT_BILL_POSTING_UNVERIFIED","信用费用账本来源未完整核验。")
        evidence = {"fee_charge_id": fee.id, "amount_minor": fee.amount_minor, "currency": fee.currency,
            "journal_id": fee.journal_id, "policy": fee.policy_json, "installment_id": fee.installment_id}
        rows.append({"posting_id": fee.id, "payment_id": payment.id, "journal_id": fee.journal_id,
            "instrument_id": payment.instrument_id, "card_id": payment.instrument_id[7:], "instrument_type": "CREDIT",
            "credit_account_id": fee.credit_account_id, "occurred_at": as_utc(fee.created_at).isoformat(),
            "amount_minor": -fee.amount_minor, "currency": fee.currency, "merchant": "信用分期手续费",
            "category": "信用卡费用", "source_kind": "CREDIT_FEE", "economic_kind": "FEE",
            "posting_hash": canonical_hash(evidence), "evidence_type": "REGISTERED_FEE_POSTING"})
    return sorted(rows, key=lambda row: (row["occurred_at"], row["posting_id"]))


def credit_account_invariants(session, principal):
    """Read-only reconciliation adapter; never repairs or hides a mismatch."""
    from .credit_models import CreditFeeCharge, PaymentJournal, PaymentLedgerEntry
    require_profile_actor(session, principal)
    checks = []
    for facility in session.scalars(select(CreditAccount).where(CreditAccount.sandbox_id == principal.sandbox_id,
            CreditAccount.user_id == principal.user_id)):
        obligations = list(session.scalars(select(CreditObligation).where(CreditObligation.credit_account_id == facility.id)))
        fees = list(session.scalars(select(CreditFeeCharge).where(CreditFeeCharge.credit_account_id == facility.id)))
        auths = list(session.scalars(select(CreditAuthorization).where(CreditAuthorization.credit_account_id == facility.id)))
        posted = sum(item.remaining_exposure_minor for item in obligations+fees)
        authorized = sum(item.remaining_exposure_minor for item in auths)
        checks += [{"name": "POSTED_EXPOSURE", "credit_account_id": facility.id, "ok": posted == facility.posted_exposure_minor,
                    "expected_minor": posted, "actual_minor": facility.posted_exposure_minor},
                   {"name": "AUTHORIZATION_EXPOSURE", "credit_account_id": facility.id, "ok": authorized == facility.authorization_exposure_minor,
                    "expected_minor": authorized, "actual_minor": facility.authorization_exposure_minor},
                   {"name": "SHARED_LIMIT", "credit_account_id": facility.id,
                    "ok": facility.base_credit_limit_minor >= posted+authorized}]
        for currency in ("CNY", "USD"):
            balance = _balance(session, facility, currency)
            principal_amount = sum(item.remaining_minor for item in obligations if item.currency == currency)
            fee_amount = sum(item.remaining_minor for item in fees if item.currency == currency)
            checks.append({"name": "CURRENCY_DEBT", "credit_account_id": facility.id, "currency": currency,
                "ok": balance.principal_minor == principal_amount and balance.fees_minor == fee_amount and balance.interest_minor == 0,
                "principal_minor": principal_amount, "fees_minor": fee_amount})
    for journal in session.scalars(select(PaymentJournal).where(PaymentJournal.sandbox_id == principal.sandbox_id,
            PaymentJournal.user_id == principal.user_id)):
        entries = list(session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == journal.id)))
        checks.append({"name": "JOURNAL_BALANCED", "journal_id": journal.id,
            "ok": all(sum(item.delta_minor for item in entries if item.currency == currency) == 0 for currency in {item.currency for item in entries})})
    return {"schema_version": "credit-reconciliation-v1", "ok": all(item["ok"] for item in checks), "checks": checks,
        "bank_state_changed": False}
