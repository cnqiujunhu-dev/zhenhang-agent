"""Per-currency billing, cash-backed repayments and debt reclassification.

Sample policy is explicit and zero-interest/zero-installment-fee. It must not
be represented as an external bank offer or as personal credit scoring.
"""
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import select

from .authentication import as_utc
from .credit_models import (CreditCard, CreditFeeAllocation, CreditFeeCharge, CreditInstallment, CreditObligation, CreditRepayment,
    CreditRepaymentAllocation, CreditStatement)
from .credit_service import _balance, _facility, _fail, _operation, _record
from .customer_profile_common import require_profile_actor
from .models import Account, Transaction, new_id, utcnow
from .planning import canonical_hash
from .credit_atomic import atomic_domain


def owned_credit_card(session, principal, card_reference=None):
    require_profile_actor(session, principal)
    if card_reference:
        from .payment_instruments import resolve_source_instrument
        snapshot = resolve_source_instrument(session, principal, card_reference, purpose="READ")
        if snapshot["instrument_type"] != "CREDIT": _fail("CREDIT_INSTRUMENT_REQUIRED")
        return session.get(CreditCard, snapshot["card_id"])
    cards = list(session.scalars(select(CreditCard).where(CreditCard.sandbox_id == principal.sandbox_id,
        CreditCard.user_id == principal.user_id, CreditCard.status != "REPLACED")))
    if len(cards) != 1: _fail("CREDIT_CARD_REFERENCE_REQUIRED", "请明确要办理的信用卡。", 422)
    return cards[0]


def _obligations(session, facility, currency):
    return list(session.scalars(select(CreditObligation).where(CreditObligation.credit_account_id == facility.id,
        CreditObligation.currency == currency).order_by(CreditObligation.created_at, CreditObligation.id)))


def _due_obligation(session, obligation, as_of, *, include_unbilled=False):
    if not obligation.installment_id:
        return obligation.remaining_minor if obligation.statement_id or include_unbilled else 0
    installment = session.get(CreditInstallment, obligation.installment_id)
    if not installment or installment.status not in {"ACTIVE", "SETTLED"}: return 0
    scheduled = sum(item["principal_minor"] for item in installment.schedule_json
        if as_utc(datetime.fromisoformat(item["due_at"])) <= as_utc(as_of))
    already_paid = installment.principal_minor - obligation.remaining_minor
    return max(0, min(obligation.remaining_minor, scheduled-already_paid))


def _fees(session, facility, currency):
    return list(session.scalars(select(CreditFeeCharge).where(CreditFeeCharge.credit_account_id == facility.id,
        CreditFeeCharge.currency == currency).order_by(CreditFeeCharge.created_at, CreditFeeCharge.id)))


def _due_fee(session, fee, as_of):
    item = session.get(CreditInstallment, fee.installment_id)
    if not item or item.status == "REFUNDED": return fee.remaining_minor
    scheduled = sum(part["fee_minor"] for part in item.schedule_json if as_utc(datetime.fromisoformat(part["due_at"])) <= as_utc(as_of))
    return max(0, min(fee.remaining_minor, scheduled-(fee.amount_minor-fee.remaining_minor)))


def installment_pricing(facility, principal_minor, periods):
    policy = facility.policy_json
    rate = policy.get("installment_total_fee_bps")
    if type(periods) is not int or periods not in policy.get("installment_periods", []) \
            or type(rate) is not int or not 0 <= rate <= 1000 or not policy.get("installment_fee_policy_version"):
        _fail("CREDIT_INSTALLMENT_POLICY_UNSUPPORTED", status=422)
    total = (principal_minor*rate+9999)//10000
    return {"fee_minor": total, "total_fee_bps": rate, "fee_policy_version": policy["installment_fee_policy_version"],
        "policy_hash": canonical_hash(policy), "fee_refund_policy": policy.get("installment_fee_refund_policy"),
        "fee_fx_basis": policy.get("installment_fee_fx_basis")}


def installment_terms(facility, principal_minor, periods, *, now=None):
    pricing = installment_pricing(facility, principal_minor, periods)
    now = as_utc(now or utcnow()); part, remainder = divmod(principal_minor, periods)
    fee_part, fee_remainder = divmod(pricing["fee_minor"], periods)
    return {"principal_minor": principal_minor, "periods": periods, "pricing": pricing,
        "prepared_at": now.isoformat(), "valid_until": (now+timedelta(minutes=5)).isoformat(),
        "schedule": [{"period": index+1, "principal_minor": part+(1 if index < remainder else 0),
            "fee_minor": fee_part+(1 if index < fee_remainder else 0),
            "due_at": (now+timedelta(days=30*(index+1))).isoformat()} for index in range(periods)]}


_CONSUMPTION_DEFAULT = object()


def statement_view(session, principal, card_reference=None, *, currency=None,
        identity_projection_version='credit-statement-card-identity-v1',
        consumption_projection_version=_CONSUMPTION_DEFAULT):
    from .credit_statement_identity import MARKER, VERSION
    if identity_projection_version is not None and (type(identity_projection_version) is not str
            or identity_projection_version != VERSION):
        _fail('CREDIT_STATEMENT_IDENTITY_VERSION_INVALID', status=422)
    from .credit_consumption import VERSION as DETAILS_VERSION, MARKER as DETAILS_MARKER, consumption_details
    if consumption_projection_version is _CONSUMPTION_DEFAULT:
        consumption_projection_version = DETAILS_VERSION if identity_projection_version is not None else None
    if consumption_projection_version is not None and consumption_projection_version != DETAILS_VERSION:
        _fail('CREDIT_CONSUMPTION_VERSION_INVALID', status=422)
    card = owned_credit_card(session, principal, card_reference)
    facility = _facility(session, principal, card.credit_account_id, lock=False)
    if currency not in {None, "CNY", "USD"}: _fail("CREDIT_CURRENCY_INVALID", status=422)
    now = as_utc(utcnow()); statements = []
    for curr in ([currency] if currency else ["CNY", "USD"]):
        balance = _balance(session, facility, curr)
        obligations = _obligations(session, facility, curr)
        due = sum(_due_obligation(session, item, now) for item in obligations) + sum(_due_fee(session, fee, now) for fee in _fees(session, facility, curr))
        floor = facility.policy_json.get("minimum_payment_floor_minor", {}).get(curr, 0)
        minimum = min(due, max(floor, (due*facility.policy_json.get("minimum_payment_bps", 1000)+9999)//10000)) if due else 0
        archived = list(session.scalars(select(CreditStatement).where(CreditStatement.credit_account_id == facility.id,
            CreditStatement.currency == curr).order_by(CreditStatement.period_end.desc())))
        statements.append({"currency": curr, "outstanding_minor": balance.principal_minor+balance.interest_minor+balance.fees_minor,
            "current_due_minor": due, "minimum_due_minor": minimum, "principal_minor": balance.principal_minor,
            "unbilled_principal_minor": sum(item.remaining_minor for item in obligations if not item.statement_id and not item.installment_id),
            "interest_minor": balance.interest_minor, "fees_minor": balance.fees_minor,
            "credit_balance_minor": balance.credit_balance_minor,
            "statements": [{"statement_id": row.id, "cycle_key": row.cycle_key,
                "due_at": as_utc(row.due_at).isoformat(), "snapshot": deepcopy(row.snapshot_json), "snapshot_hash": row.snapshot_hash}
                for row in archived],
            "obligations": [{"obligation_id": row.id, "payment_id": row.payment_id, "remaining_minor": row.remaining_minor,
                "installment_id": row.installment_id, "current_due_minor": _due_obligation(session, row, now)} for row in obligations],
            "minimum_payment_explanation": "最低应还不是全额还清；未还欠款仍占授信。分期按登记的总费用政策逐期披露，不将总费率冒充年化利率。"})
        if consumption_projection_version is not None:
            statements[-1]['consumption_details'] = consumption_details(session,principal,card,facility,curr)
    from .credit_read_common import finish_credit_read
    installments = []
    for item in session.scalars(select(CreditInstallment).where(CreditInstallment.credit_account_id == facility.id).order_by(CreditInstallment.created_at, CreditInstallment.id)):
        if currency and item.currency != currency: continue
        obligation = session.get(CreditObligation, item.obligation_id)
        fees = session.scalar(select(CreditFeeCharge).where(CreditFeeCharge.installment_id == item.id))
        installments.append({"installment_id": item.id, "currency": item.currency, "status": item.status,
            "principal_minor": item.principal_minor, "remaining_principal_minor": obligation.remaining_minor,
            "fee_minor": item.fee_minor, "remaining_fee_minor": fees.remaining_minor if fees else 0,
            "periods": item.periods, "schedule": deepcopy(item.schedule_json), "policy_hash": item.policy_hash})
    from .credit_pool_facts import current_credit_pool_contract
    payload = {"schema_version": "credit-statement-v1", "card_id": card.id,
        "card_number": card.card_number, "credit_account_id": facility.id, "facility_version": facility.version,
        "credit_pool_contract": current_credit_pool_contract(facility),
        "statements_by_currency": statements, "installments": installments, "policy": deepcopy(facility.policy_json)}
    # Absence is the exact old result format, used only for its own fresh
    # comparison. Never backfill a marker/entity/hash onto a persisted result.
    if identity_projection_version is not None:
        payload[MARKER] = identity_projection_version
    if consumption_projection_version is not None:
        payload[DETAILS_MARKER] = consumption_projection_version
    return finish_credit_read(principal, "CREDIT_STATEMENT", payload,
        {"card_reference": card_reference, "currency": currency})


@atomic_domain
def close_credit_statement(session, principal, card_reference=None, *, currency, cycle_key, start, end, due_at, now=None):
    """Explicit scheduler/admin adapter; ordinary statement reads never close."""
    require_profile_actor(session, principal, lock=True)
    card = owned_credit_card(session, principal, card_reference)
    facility = _facility(session, principal, card.credit_account_id)
    start, end, due_at = map(as_utc, (start, end, due_at))
    closed_at = as_utc(now or utcnow())
    if not start < end <= due_at or end > closed_at: _fail("CREDIT_STATEMENT_PERIOD_INVALID", status=422)
    if currency not in {"CNY", "USD"} or not isinstance(cycle_key, str) or not 1 <= len(cycle_key) <= 30:
        _fail("CREDIT_STATEMENT_PERIOD_INVALID", status=422)
    prior = session.scalar(select(CreditStatement).where(CreditStatement.credit_account_id == facility.id,
        CreditStatement.currency == currency, CreditStatement.cycle_key == cycle_key))
    if prior:
        if (as_utc(prior.period_start), as_utc(prior.period_end), as_utc(prior.due_at)) != (start, end, due_at):
            _fail("CREDIT_STATEMENT_IDEMPOTENCY_CONFLICT")
        return {"statement_id": prior.id, **deepcopy(prior.snapshot_json), "replayed": True}
    overlap = session.scalar(select(CreditStatement.id).where(CreditStatement.credit_account_id == facility.id,
        CreditStatement.currency == currency, CreditStatement.period_start < end, CreditStatement.period_end > start))
    if overlap: _fail("CREDIT_STATEMENT_OVERLAP")
    from .credit_service import credit_bill_rows
    rows = [row for row in credit_bill_rows(session, principal, start, end) if row["currency"] == currency
        and row["credit_account_id"] == facility.id]
    obligations = _obligations(session, facility, currency)
    snapshot = {"currency": currency, "cycle_key": cycle_key, "rows": rows,
        "closed_at": closed_at.isoformat(), "due_balance_basis": "REMAINING_REGISTERED_DEBT_AT_CLOSE",
        "period_start": start.isoformat(), "period_end": end.isoformat(), "due_at": due_at.isoformat(),
        "due_minor": sum(_due_obligation(session, item, end, include_unbilled=True) for item in obligations if as_utc(item.created_at) < end)
            +sum(_due_fee(session, fee, end) for fee in _fees(session, facility, currency) if as_utc(fee.created_at) < end),
        "policy": deepcopy(facility.policy_json), "authority": "CLOSED_POSTING_SNAPSHOT"}
    row = CreditStatement(credit_account_id=facility.id, currency=currency, cycle_key=cycle_key, period_start=start,
        period_end=end, due_at=due_at, snapshot_json=snapshot, snapshot_hash=canonical_hash(snapshot))
    session.add(row); session.flush()
    for obligation in obligations:
        if as_utc(obligation.created_at) < end and not obligation.statement_id:
            obligation.statement_id = row.id
    return {"statement_id": row.id, **snapshot, "replayed": False}


@atomic_domain
def repay_credit(session, principal, *, card_reference=None, source_snapshot, currency="CNY", amount_minor,
        operation_key, expected_facility_version=None, fx_quote_id=None, _obligation_id=None):
    """Same-currency debit repayment; optional separately confirmed CNY→USD quote."""
    from .payment_instruments import _amount, _journal, cash_reserved_minor, validate_instrument_snapshot
    from .credit_service import _exposure
    _amount(amount_minor)
    card = owned_credit_card(session, principal, card_reference)
    material = {"card_id": card.id, "source_instrument_id": source_snapshot.get("instrument_id"),
        "currency": currency, "amount_minor": amount_minor, "fx_quote_id": fx_quote_id, "obligation_id": _obligation_id}
    digest, prior = _operation(session, principal, operation_key, "REPAY", material)
    if prior: return prior
    source = validate_instrument_snapshot(session, principal, source_snapshot, purpose="REPAYMENT")
    if source["instrument_type"] != "DEBIT": _fail("CREDIT_REPAYMENT_CASH_REQUIRED")
    cash_debit = amount_minor
    fx = source["currency"] == "CNY" and currency == "USD"
    if fx: cash_debit = _exposure(session, principal, "USD", amount_minor, fx_quote_id)
    elif source["currency"] != currency or fx_quote_id: _fail("CREDIT_REPAYMENT_CURRENCY_MISMATCH")
    facility = _facility(session, principal, card.credit_account_id)
    if expected_facility_version is not None and facility.version != expected_facility_version: _fail("CREDIT_VERSION_CHANGED")
    balance = _balance(session, facility, currency)
    debt = balance.principal_minor + balance.fees_minor + balance.interest_minor
    if amount_minor > debt: _fail("CREDIT_REPAYMENT_EXCEEDS_DEBT", "还款金额超过该币种未还欠款，未扣款。")
    if source["available_balance_minor"] < cash_debit: _fail("PAYMENT_INSUFFICIENT_FUNDS")
    # Repayment/returns remain allowed for locked/lost credit cards; source debit
    # controls remain enforced by the unified payment gate.
    if source["currency"] == "CNY":
        cash = session.get(Account, source["account_id"])
        cash.balance_minor -= cash_debit; cash.version += 1
    else:
        from .fx_models import FxWallet
        cash = session.get(FxWallet, source["wallet_id"])
        cash.balance_minor -= cash_debit; cash.version += 1
    remaining, released, paid_fees = amount_minor, 0, 0
    repayment_id = new_id()
    repayment = CreditRepayment(id=repayment_id, sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        credit_account_id=facility.id, operation_key=operation_key, request_hash=digest, receipt_json={})
    session.add(repayment); session.flush()
    allocations = []
    for fee in _fees(session, facility, currency):
        if _obligation_id is not None:
            target = session.get(CreditObligation, _obligation_id)
            if target is None or fee.installment_id != target.installment_id: continue
        paid = min(remaining, fee.remaining_minor)
        if not paid: continue
        freed = fee.remaining_exposure_minor if paid == fee.remaining_minor else fee.remaining_exposure_minor*paid//fee.remaining_minor
        fee.remaining_minor -= paid; fee.remaining_exposure_minor -= freed
        remaining -= paid; released += freed; paid_fees += paid
        session.add(CreditFeeAllocation(repayment_id=repayment_id, fee_charge_id=fee.id, amount_minor=paid, base_exposure_minor=freed))
        allocations.append({"fee_charge_id": fee.id, "amount_minor": paid, "base_exposure_minor": freed})
        if not remaining: break
    for obligation in _obligations(session, facility, currency):
        if _obligation_id is not None and obligation.id != _obligation_id: continue
        paid = min(remaining, obligation.remaining_minor)
        if not paid: continue
        freed = obligation.remaining_exposure_minor if paid == obligation.remaining_minor else obligation.remaining_exposure_minor * paid // obligation.remaining_minor
        obligation.remaining_minor -= paid; obligation.remaining_exposure_minor -= freed
        remaining -= paid; released += freed
        session.add(CreditRepaymentAllocation(repayment_id=repayment_id, obligation_id=obligation.id,
            amount_minor=paid, base_exposure_minor=freed))
        allocations.append({"obligation_id": obligation.id, "amount_minor": paid, "base_exposure_minor": freed})
        if obligation.installment_id and obligation.remaining_minor == 0:
            session.get(CreditInstallment, obligation.installment_id).status = "SETTLED"
        if remaining == 0: break
    if remaining: _fail("CREDIT_BOOK_RECONCILIATION_REQUIRED", status=500)
    balance.principal_minor -= amount_minor-paid_fees; balance.fees_minor -= paid_fees; balance.version += 1
    facility.posted_exposure_minor -= released; facility.version += 1
    entries = [(source["balance_ref"], source["currency"], -cash_debit), ("CREDIT:" + facility.id, currency, amount_minor)]
    if fx: entries += [("FX_CLEARING:CNY", "CNY", cash_debit), ("FX_CLEARING:USD", "USD", -amount_minor)]
    journal = _journal(session, principal, operation_key=operation_key, request_hash=digest, kind="CREDIT_REPAYMENT", entries=entries)
    receipt = {"schema_version": "credit-repayment-v1", "action": "CREDIT_REPAY", "repayment_id": repayment_id,
        "status": "POSTED", "card_id": card.id, "credit_account_id": facility.id, "account_id": source["account_id"],
        "source_instrument_id": source["instrument_id"], "currency": currency, "amount_minor": amount_minor,
        "cash_currency": source["currency"], "cash_debit_minor": cash_debit, "fx_quote_id": fx_quote_id,
        "base_exposure_released_minor": released, "remaining_debt_minor": debt-amount_minor,
        "allocations": allocations, "journal_id": journal.id, "ledger_balanced": True, "replayed": False}
    # Mirror only CNY cash leg in legacy transaction table; movement, not expense.
    if source["currency"] == "CNY":
        session.add(Transaction(sandbox_id=principal.sandbox_id, account_id=source["account_id"], card_id=source["card_id"],
            occurred_at=utcnow(), amount_minor=-cash_debit, merchant="信用卡还款", category="资金划转", flag="NORMAL",
            source_kind="CREDIT_REPAYMENT", operation_ref="credit-repayment:" + repayment_id))
    repayment.receipt_json = receipt; journal.result_json = receipt
    return _record(session, principal, operation_key, "REPAY", digest, receipt)


@atomic_domain
def create_credit_installment(session, principal, *, payment_id, periods, operation_key, expected_facility_version=None,
        expected_policy_hash=None, expected_terms=None):
    from .credit_models import PaymentRecord
    payment = session.get(PaymentRecord, payment_id)
    if not payment or (payment.sandbox_id, payment.user_id, payment.instrument_type) != (principal.sandbox_id, principal.user_id, "CREDIT"):
        _fail("CREDIT_PAYMENT_NOT_OWNED", status=403)
    digest, prior = _operation(session, principal, operation_key, "INSTALLMENT", {"payment_id": payment_id, "periods": periods})
    if prior: return prior
    facility = _facility(session, principal, payment.credit_account_id)
    if expected_facility_version is not None and facility.version != expected_facility_version: _fail("CREDIT_VERSION_CHANGED")
    if type(periods) is not int or periods not in facility.policy_json.get("installment_periods", []): _fail("CREDIT_INSTALLMENT_PERIODS_INVALID", status=422)
    obligation = session.scalar(select(CreditObligation).where(CreditObligation.payment_id == payment_id).with_for_update())
    if not obligation or obligation.remaining_minor <= 0 or obligation.installment_id or payment.status != "POSTED":
        _fail("CREDIT_INSTALLMENT_NOT_ELIGIBLE")
    principal_amount = obligation.remaining_minor
    pricing = installment_pricing(facility, principal_amount, periods)
    if expected_policy_hash is not None and pricing["policy_hash"] != expected_policy_hash: _fail("CREDIT_POLICY_CHANGED")
    fee_total = pricing["fee_minor"]
    terms = installment_terms(facility, principal_amount, periods)
    if expected_terms is not None:
        try:
            prepared = as_utc(datetime.fromisoformat(expected_terms["prepared_at"]))
            terms = installment_terms(facility, principal_amount, periods, now=prepared)
            if expected_terms != terms or as_utc(datetime.fromisoformat(terms["valid_until"])) <= as_utc(utcnow()):
                _fail("CREDIT_INSTALLMENT_TERMS_CHANGED")
        except (KeyError, TypeError, ValueError): _fail("CREDIT_INSTALLMENT_TERMS_CHANGED")
    schedule = terms["schedule"]
    item = CreditInstallment(credit_account_id=facility.id, obligation_id=obligation.id, currency=payment.currency,
        operation_key=operation_key, request_hash=digest,
        principal_minor=principal_amount, periods=periods, fee_minor=fee_total, schedule_json=schedule,
        policy_hash=canonical_hash(facility.policy_json))
    session.add(item); session.flush()
    if fee_total:
        from .credit_service import _capacity
        from .payment_instruments import _journal
        # Fee exposure uses the registered original locked-settlement exposure
        # ratio (with conservative minor-unit rounding), not a model rate or a
        # claim about today's market rate. The fee itself remains USD/CNY.
        quoted = payment.receipt_json.get("quoted_base_exposure_minor", payment.amount_minor if payment.currency == "CNY" else None)
        if type(quoted) is not int or quoted <= 0: _fail("CREDIT_FEE_PRICING_PROOF_REQUIRED")
        fee_exposure = (fee_total*quoted+payment.amount_minor-1)//payment.amount_minor
        _capacity(facility, fee_exposure)
        fee_journal = _journal(session, principal, operation_key="credit-fee:"+canonical_hash({"key": operation_key}), request_hash=digest,
            kind="CREDIT_INSTALLMENT_FEE", entries=[("CREDIT:"+facility.id, payment.currency, -fee_total),
                ("FEE_REVENUE:INSTALLMENT", payment.currency, fee_total)])
        session.add(CreditFeeCharge(credit_account_id=facility.id, installment_id=item.id, currency=payment.currency,
            amount_minor=fee_total, remaining_minor=fee_total, base_exposure_minor=fee_exposure,
            remaining_exposure_minor=fee_exposure, journal_id=fee_journal.id, policy_json=pricing))
        balance = _balance(session, facility, payment.currency)
        balance.fees_minor += fee_total; balance.version += 1
        facility.posted_exposure_minor += fee_exposure
    obligation.installment_id = item.id; facility.version += 1
    receipt = {"schema_version": "credit-installment-v1", "action": "CREDIT_INSTALLMENT", "installment_id": item.id,
        "status": "ACTIVE", "payment_id": payment_id, "currency": payment.currency, "principal_minor": principal_amount,
        "periods": periods, "fee_minor": fee_total, "schedule": schedule, "new_consumption_minor": 0,
        "pricing": pricing, "cash_debit_minor": 0, "principal_exposure_unchanged": True, "replayed": False}
    return _record(session, principal, operation_key, "INSTALLMENT", digest, receipt)


@atomic_domain
def settle_credit_installment(session, principal, *, installment_id, source_snapshot, operation_key,
        expected_facility_version=None, fx_quote_id=None, card_reference=None):
    item = session.get(CreditInstallment, installment_id)
    if not item: _fail("CREDIT_INSTALLMENT_NOT_OWNED", status=403)
    facility = _facility(session, principal, item.credit_account_id, lock=False)
    obligation = session.get(CreditObligation, item.obligation_id)
    digest, prior = _operation(session, principal, operation_key, "SETTLE_INSTALLMENT", {"installment_id": installment_id,
        "source_instrument_id": source_snapshot.get("instrument_id"), "fx_quote_id": fx_quote_id})
    if prior: return prior
    if item.status != "ACTIVE" or not obligation or obligation.remaining_minor <= 0: _fail("CREDIT_INSTALLMENT_NOT_ACTIVE")
    # Selective repayment must target this debt, not consume unrelated FIFO debt.
    result = _repay_selected_installment(session, principal, item, obligation, source_snapshot,
        operation_key, expected_facility_version, fx_quote_id, card_reference)
    result = {**result, "action": "CREDIT_SETTLE_INSTALLMENT", "installment_id": item.id,
        "early_settlement_fee_minor": 0, "status": "SETTLED"}
    return _record(session, principal, operation_key, "SETTLE_INSTALLMENT", digest, result)


def _repay_selected_installment(session, principal, item, obligation, source_snapshot, key, version, quote_id, card_reference):
    # Reuse the complete repayment machinery while explicitly choosing the
    # obligation allocation. Temporarily changing rows is forbidden; the helper
    # passes a finite obligation filter instead.
    return _repay_target(session, principal, item, obligation, source_snapshot, key, version, quote_id, card_reference)


def _repay_target(session, principal, item, obligation, source_snapshot, key, version, quote_id, card_reference):
    # Current single obligation path is intentionally explicit in the common
    # function, preserving cash/refund reconciliation and idempotent receipts.
    fees = sum(fee.remaining_minor for fee in _fees(session, _facility(session, principal, item.credit_account_id, lock=False), item.currency)
        if fee.installment_id == item.id)
    from .credit_models import PaymentRecord
    reference = card_reference or session.get(PaymentRecord, obligation.payment_id).instrument_id
    if owned_credit_card(session, principal, reference).credit_account_id != item.credit_account_id: _fail("CREDIT_INSTALLMENT_NOT_OWNED", status=403)
    return repay_credit(session, principal, card_reference=reference, source_snapshot=source_snapshot,
        currency=item.currency, amount_minor=obligation.remaining_minor+fees,
        operation_key="settle-repay:"+canonical_hash({"key": key}),
        expected_facility_version=version, fx_quote_id=quote_id, _obligation_id=obligation.id)
