"""One owned payment-instrument gate. No commits, network, LLM or implicit DDL.

Existing Card rows are DEBIT. CreditCard has a separate liability facility and
never masquerades as an Account with spendable cash. Posting callers must first
verify their task confirmation or bounded mandate in the same transaction.
"""
from copy import deepcopy
from datetime import datetime
import re

from sqlalchemy import func, inspect, select

from .authentication import as_utc
from .contracts import DomainError
from .credit_payment_contracts import CASH_ONLY_PURPOSES, PURPOSES, VERSION_POLICIES
from .credit_models import (CreditAccount, CreditCard, CustomerPaymentDefaults, PaymentCashReservation,
    PaymentInstrumentBinding, PaymentJournal, PaymentLedgerEntry, PaymentRecord, PaymentRefund)
from .customer_profile_common import require_profile_actor
from .models import Account, Card, Transaction, new_id, utcnow
from .planning import canonical_hash
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from .credit_atomic import atomic_domain


def _deny(code, message="付款工具、权限或资金范围已变化，请重新核对。", status=409):
    raise DomainError(code, message, status)


def _table(session, name):
    # Reuse the transaction connection. An engine-level inspector may close
    # SQLite's same pooled DBAPI connection and roll back a caller SAVEPOINT.
    return inspect(session.connection()).has_table(name)


def _amount(value):
    if type(value) is not int or not 0 < value <= 2**53-1:
        _deny("PAYMENT_AMOUNT_INVALID", "付款金额必须是有效的正整数最小货币单位。", 422)
    return value


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,119}", value):
        _deny("PAYMENT_OPERATION_KEY_INVALID", "需要稳定的付款业务标识。", 422)
    return value


def _scope(value):
    return canonical_hash({key: item for key, item in value.items() if key != "scope_hash"})


def _binding(session, instrument_id):
    return session.get(PaymentInstrumentBinding, instrument_id) if _table(session, "payment_instrument_bindings") else None


def _default(session, principal):
    return session.scalar(select(CustomerPaymentDefaults).where(CustomerPaymentDefaults.sandbox_id == principal.sandbox_id,
        CustomerPaymentDefaults.user_id == principal.user_id)) if _table(session, "customer_payment_defaults") else None


def cash_reserved_minor(session, principal, account_id, *, exclude_reservation_id=None):
    from .extensions import reserved_goal_cash_minor
    reserved = reserved_goal_cash_minor(session, principal.sandbox_id, principal.user_id, account_id)
    if _table(session, "payment_cash_reservations"):
        rows = session.scalars(select(PaymentCashReservation).where(PaymentCashReservation.sandbox_id == principal.sandbox_id,
            PaymentCashReservation.user_id == principal.user_id, PaymentCashReservation.account_id == account_id,
            PaymentCashReservation.status == "ACTIVE"))
        # An expired reservation remains reserved until its owner explicitly
        # releases it. A mere balance read must not mutate or free a mandate.
        reserved += sum(row.remaining_minor for row in rows if row.id != exclude_reservation_id)
    return reserved


def _snapshot(session, principal, kind, card, currency, *, purpose, selection_kind="EXPLICIT", default_version=None, lock=False):
    if currency not in {"CNY", "USD"} or purpose not in PURPOSES:
        _deny("PAYMENT_SCOPE_UNSUPPORTED", "付款币种或用途不受支持。", 422)
    if kind == "DEBIT":
        query = select(Account).where(Account.id == card.account_id, Account.user_id == principal.user_id,
            Account.sandbox_id == principal.sandbox_id)
        account = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        if account is None or account.currency != "CNY":
            _deny("PAYMENT_ACCOUNT_NOT_OWNED", status=403)
        if lock:
            card = session.scalar(select(Card).where(Card.id == card.id).with_for_update().execution_options(populate_existing=True))
        if card is None or card.account_id != account.id or (card.sandbox_id, card.user_id) != (principal.sandbox_id, principal.user_id):
            _deny("PAYMENT_BINDING_CHANGED")
        balance, balance_ref, account_version, wallet_id = account.balance_minor, account.id, account.version, None
        if currency == "USD":
            from .fx_models import DualCurrencyProfile, FxWallet
            profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.card_id == card.id,
                DualCurrencyProfile.sandbox_id == principal.sandbox_id, DualCurrencyProfile.user_id == principal.user_id))
            wallet = session.get(FxWallet, profile.usd_wallet_id) if profile else None
            if not profile or profile.status != "ACTIVE" or not wallet or wallet.status != "ACTIVE" \
                    or (wallet.sandbox_id, wallet.user_id, wallet.currency) != (principal.sandbox_id, principal.user_id, "USD"):
                _deny("PAYMENT_CURRENCY_UNAVAILABLE", "指定卡没有有效的本人美元资金钱包。")
            if lock:
                wallet = session.scalar(select(FxWallet).where(FxWallet.id == wallet.id).with_for_update().execution_options(populate_existing=True))
            from .fx_banking import usd_ref
            balance, balance_ref, account_version, wallet_id = wallet.balance_minor, usd_ref(wallet.id), wallet.version, wallet.id
        from .card_identifiers import card_identifier_view
        identity = card_identifier_view(session, card) or {}
        instrument_id = "debit:" + card.id
        binding = _binding(session, instrument_id)
        if binding and (binding.account_id != card.account_id or binding.card_id != card.id or binding.instrument_type != "DEBIT"
                or binding.credit_account_id is not None or (binding.sandbox_id,binding.user_id)!=(principal.sandbox_id,principal.user_id)):
            _deny("PAYMENT_BINDING_CHANGED")
        reserved = cash_reserved_minor(session, principal, account.id) if currency == "CNY" else 0
        result = {"schema_version": "payment-instrument-v1", "instrument_id": instrument_id, "instrument_type": "DEBIT",
            "card_id": card.id, "card_number": identity.get("card_number"), "display_last4": identity.get("display_last4", card.last4),
            "sandbox_id": principal.sandbox_id, "user_id": principal.user_id, "account_id": account.id,
            "credit_account_id": None, "wallet_id": wallet_id, "balance_ref": balance_ref, "currency": currency,
            "status": card.status, "card_version": card.version, "account_version": account_version,
            "binding_version": binding.version if binding else 0, "default_version": default_version,
            "selection_kind": selection_kind, "cash_balance_minor": balance, "available_balance_minor": max(0, balance-reserved),
            "transaction_limit_minor": card.limit_minor, "paymentable": card.status == "ACTIVE", "purpose": purpose}
    else:
        query = select(CreditAccount).where(CreditAccount.id == card.credit_account_id,
            CreditAccount.sandbox_id == principal.sandbox_id, CreditAccount.user_id == principal.user_id)
        account = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        if account is None:
            _deny("PAYMENT_ACCOUNT_NOT_OWNED", status=403)
        if lock:
            card = session.scalar(select(CreditCard).where(CreditCard.id == card.id).with_for_update().execution_options(populate_existing=True))
        if card is None or card.credit_account_id != account.id or (card.sandbox_id, card.user_id) != (principal.sandbox_id, principal.user_id):
            _deny("PAYMENT_BINDING_CHANGED")
        instrument_id = "credit:" + card.id
        binding = _binding(session, instrument_id)
        if binding and (binding.credit_account_id != account.id or binding.card_id != card.id or binding.instrument_type != "CREDIT"
                or binding.account_id is not None or (binding.sandbox_id,binding.user_id)!=(principal.sandbox_id,principal.user_id)):
            _deny("PAYMENT_BINDING_CHANGED")
        result = {"schema_version": "payment-instrument-v1", "instrument_id": instrument_id, "instrument_type": "CREDIT",
            "card_id": card.id, "card_number": card.card_number, "display_last4": card.card_number[-4:],
            "sandbox_id": principal.sandbox_id, "user_id": principal.user_id, "account_id": None,
            "credit_account_id": account.id, "wallet_id": None, "currency": currency, "status": card.status,
            "card_version": card.version, "account_version": account.version, "binding_version": binding.version if binding else 0,
            "default_version": default_version, "selection_kind": selection_kind, "base_currency": "CNY",
            "base_credit_limit_minor": account.base_credit_limit_minor, "posted_exposure_minor": account.posted_exposure_minor,
            "authorization_exposure_minor": account.authorization_exposure_minor,
            "available_credit_minor": max(0, account.base_credit_limit_minor-account.posted_exposure_minor-account.authorization_exposure_minor),
            "transaction_limit_minor": card.transaction_limit_minor,
            "paymentable": card.status == account.status == "ACTIVE" and purpose not in CASH_ONLY_PURPOSES, "purpose": purpose}
    result["scope_hash"] = _scope(result)
    return result


def resolve_source_instrument(session, principal, card_reference=None, currency="CNY", *, purpose="TRANSFER"):
    require_profile_actor(session, principal)
    default_version, selection_kind = None, "EXPLICIT"
    if card_reference is None:
        default = _default(session, principal)
        if default:
            card_reference, default_version, selection_kind = default.source_instrument_id, default.version, "DEFAULT"
        else:
            accounts = list(session.scalars(select(Account).where(Account.sandbox_id == principal.sandbox_id, Account.user_id == principal.user_id)))
            if len(accounts) != 1:
                _deny("PAYMENT_DEFAULT_REQUIRED", "请明确付款卡或先设置默认付款账户。")
            cards = list(session.scalars(select(Card).where(Card.account_id == accounts[0].id, Card.sandbox_id == principal.sandbox_id,
                Card.user_id == principal.user_id, Card.status != ARCHIVED_RESOURCE_STATUS)))
            primary = [row for row in cards if row.last4 == "4321"]
            if len(primary) != 1 and len(cards) != 1:
                _deny("PAYMENT_INSTRUMENT_AMBIGUOUS", "请明确唯一付款卡。")
            card_reference, selection_kind = "debit:" + (primary[0] if primary else cards[0]).id, "LEGACY_DEFAULT"
    if not isinstance(card_reference, str) or not card_reference.strip():
        _deny("PAYMENT_INSTRUMENT_REQUIRED", status=422)
    reference = card_reference.strip(); matches = []
    from .card_identifiers import resolve_owned_cards
    if reference.startswith("debit:"):
        matches = [("DEBIT", row) for row in session.scalars(select(Card).where(Card.id == reference[6:],
            Card.user_id == principal.user_id, Card.sandbox_id == principal.sandbox_id, Card.status != ARCHIVED_RESOURCE_STATUS))]
    elif reference.startswith("credit:"):
        matches = [("CREDIT", row) for row in session.scalars(select(CreditCard).where(CreditCard.id == reference[7:],
            CreditCard.user_id == principal.user_id, CreditCard.sandbox_id == principal.sandbox_id))] if _table(session, "credit_cards") else []
    elif reference.startswith("account:"):
        matches = [("DEBIT", row) for row in session.scalars(select(Card).where(Card.account_id == reference[8:],
            Card.user_id == principal.user_id, Card.sandbox_id == principal.sandbox_id, Card.status != ARCHIVED_RESOURCE_STATUS))]
    else:
        # A server-owned UUID is accepted only after the owner predicate. Human
        # card numbers/tails retain ambiguity across debit and credit kinds.
        direct = session.get(Card, reference)
        if direct and (direct.user_id, direct.sandbox_id) == (principal.user_id, principal.sandbox_id):
            matches.append(("DEBIT", direct))
        else:
            try: matches.extend(("DEBIT", row) for row in resolve_owned_cards(session, principal.sandbox_id, principal.user_id, reference))
            except DomainError: pass
        if _table(session, "credit_cards"):
            query = select(CreditCard).where(CreditCard.user_id == principal.user_id, CreditCard.sandbox_id == principal.sandbox_id)
            if len(reference) == 4: query = query.where(CreditCard.card_number.endswith(reference.lower()))
            else: query = query.where((CreditCard.card_number == reference.lower()) | (CreditCard.id == reference))
            matches.extend(("CREDIT", row) for row in session.scalars(query))
    if len(matches) != 1:
        _deny("PAYMENT_INSTRUMENT_AMBIGUOUS", "未找到唯一本人付款工具，请提供完整卡号。")
    kind, card = matches[0]
    result = _snapshot(session, principal, kind, card, currency, purpose=purpose,
        selection_kind=selection_kind, default_version=default_version)
    if kind == "CREDIT" and purpose in CASH_ONLY_PURPOSES:
        _deny("CREDIT_CARD_CASH_CHANNEL_FORBIDDEN", "信用额度不是现金，不能用于转账、理财或本次长期现金计划。", 403)
    if purpose != "READ" and not result["paymentable"]:
        from .planning_failure_facts import payment_instrument_unavailable
        raise payment_instrument_unavailable(principal, result)
    return result


def validate_instrument_snapshot(session, principal, snapshot, *, lock=True, purpose="TRANSFER", amount_minor=None, version_policy="STRICT"):
    if version_policy not in VERSION_POLICIES or not isinstance(snapshot, dict) or snapshot.get("scope_hash") != _scope(snapshot) \
            or snapshot.get("schema_version") != "payment-instrument-v1" \
            or (snapshot.get("sandbox_id"), snapshot.get("user_id")) != (principal.sandbox_id, principal.user_id):
        _deny("PAYMENT_INSTRUMENT_CHANGED")
    if lock:
        # All posting domains serialize the owner before cash/facility rows.
        # A durable executor may call validate before post; taking the owner
        # here prevents an Account->User inversion in that composition.
        require_profile_actor(session, principal, lock=True)
    current = resolve_source_instrument(session, principal, snapshot["instrument_id"], snapshot["currency"], purpose=purpose)
    if lock:
        card = session.get(Card if current["instrument_type"] == "DEBIT" else CreditCard, current["card_id"])
        current = _snapshot(session, principal, current["instrument_type"], card, current["currency"], purpose=purpose, lock=True,
            selection_kind=snapshot.get("selection_kind", "EXPLICIT"), default_version=snapshot.get("default_version"))
    keys = ("instrument_id", "instrument_type", "card_id", "account_id", "credit_account_id", "wallet_id", "currency", "card_version", "binding_version")
    if any(current.get(key) != snapshot.get(key) for key in keys):
        _deny("PAYMENT_INSTRUMENT_CHANGED")
    if snapshot.get("selection_kind") == "DEFAULT":
        default = _default(session, principal)
        if not default or default.version != snapshot.get("default_version") or default.source_instrument_id != snapshot["instrument_id"]:
            _deny("PAYMENT_DEFAULT_CHANGED")
    if version_policy == "STRICT" and current["account_version"] != snapshot["account_version"]:
        _deny("PAYMENT_ACCOUNT_VERSION_CHANGED")
    if purpose != "READ" and not current["paymentable"]:
        from .planning_failure_facts import payment_instrument_unavailable
        raise payment_instrument_unavailable(principal, current)
    if amount_minor is not None:
        _amount(amount_minor)
        if current["currency"] == "CNY" and amount_minor > current["transaction_limit_minor"]:
            _deny("PAYMENT_CARD_LIMIT_EXCEEDED", "金额超出当前卡片单笔消费限额。")
        if current["instrument_type"] == "DEBIT" and amount_minor > current["available_balance_minor"]:
            _deny("PAYMENT_INSUFFICIENT_FUNDS", "指定账户可支出资金不足，不会改用其他资金。")
    current["selection_kind"] = snapshot.get("selection_kind", "EXPLICIT")
    current["default_version"] = snapshot.get("default_version")
    current["scope_hash"] = _scope(current)
    return current


def resolve_recipient_instrument(session, username, card_reference=None, currency="CNY", *, caller_principal=None):
    from .access_models import LoginAccount
    from .services import Principal
    if caller_principal is not None: require_profile_actor(session, caller_principal)
    login = session.scalar(select(LoginAccount).where(LoginAccount.username == username, LoginAccount.status == "ACTIVE",
        LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"})))
    if login is None: _deny("PAYMENT_RECIPIENT_UNAVAILABLE", "收款账号不存在或当前不能收款。")
    owner = Principal(login.user_id, login.sandbox_id)
    default = _default(session, owner)
    used_default = card_reference is None and default is not None
    if card_reference is None and default:
        card_reference = default.recipient_instrument_id
    source = resolve_source_instrument(session, owner, card_reference, currency, purpose="READ")
    if source["instrument_type"] != "DEBIT" or source["status"] != "ACTIVE":
        _deny("PAYMENT_RECIPIENT_UNAVAILABLE", "普通转账只向本人指定的有效借记资金账户收款。")
    result = {key: source[key] for key in ("schema_version", "instrument_id", "instrument_type", "card_id", "display_last4",
        "sandbox_id", "user_id", "account_id", "wallet_id", "currency", "card_version", "binding_version")}
    result.update(username=login.username, login_account_id=login.id, login_version=login.version,
        selection_kind="DEFAULT" if used_default else "EXPLICIT",
        default_version=default.version if used_default else None)
    result["scope_hash"] = _scope(result)
    return result


def validate_recipient_snapshot(session, snapshot, *, lock=True, version_policy="CARD_BINDING"):
    if not isinstance(snapshot, dict) or snapshot.get("scope_hash") != _scope(snapshot) or version_policy not in VERSION_POLICIES:
        _deny("PAYMENT_RECIPIENT_CHANGED")
    reference = None if snapshot.get("selection_kind") == "DEFAULT" else snapshot.get("instrument_id")
    current = resolve_recipient_instrument(session, snapshot.get("username"), reference, snapshot.get("currency"))
    if lock:
        session.scalar(select(Account).where(Account.id == current["account_id"]).with_for_update().execution_options(populate_existing=True))
        session.scalar(select(Card).where(Card.id == current["card_id"]).with_for_update().execution_options(populate_existing=True))
        current = resolve_recipient_instrument(session, snapshot.get("username"), reference, snapshot.get("currency"))
    for key in ("instrument_id", "sandbox_id", "user_id", "account_id", "wallet_id", "currency", "card_version", "binding_version", "login_account_id", "login_version"):
        if current.get(key) != snapshot.get(key): _deny("PAYMENT_RECIPIENT_CHANGED")
    if snapshot.get("selection_kind") == "DEFAULT" and (current.get("default_version") != snapshot.get("default_version")):
        _deny("PAYMENT_RECIPIENT_CHANGED")
    return current


def recipient_instrument_view(snapshot):
    return {key: snapshot.get(key) for key in ("username", "display_last4", "instrument_type", "currency")}


@atomic_domain
def reserve_cash(session, principal, *, instrument_snapshot, amount_minor, reservation_key, owner_ref, expires_at=None):
    _amount(amount_minor); _key(reservation_key); require_profile_actor(session, principal, lock=True)
    digest = canonical_hash({"instrument_id": instrument_snapshot.get("instrument_id"), "account_id": instrument_snapshot.get("account_id"),
        "amount_minor": amount_minor, "owner_ref": owner_ref, "expires_at": as_utc(expires_at).isoformat() if expires_at else None})
    prior = session.scalar(select(PaymentCashReservation).where(PaymentCashReservation.sandbox_id == principal.sandbox_id,
        PaymentCashReservation.user_id == principal.user_id, PaymentCashReservation.reservation_key == reservation_key))
    if prior:
        if prior.request_hash != digest: _deny("PAYMENT_IDEMPOTENCY_CONFLICT")
        return reservation_view(prior)
    fresh = validate_instrument_snapshot(session, principal, instrument_snapshot, purpose="LIFE_PLAN")
    if fresh["instrument_type"] != "DEBIT" or fresh["currency"] != "CNY": _deny("PAYMENT_RESERVATION_CASH_ONLY")
    if fresh["available_balance_minor"] < amount_minor: _deny("PAYMENT_INSUFFICIENT_FUNDS")
    if expires_at is not None and as_utc(expires_at) <= as_utc(utcnow()): _deny("PAYMENT_RESERVATION_EXPIRED")
    if not isinstance(owner_ref, str) or not owner_ref or len(owner_ref) > 120: _deny("PAYMENT_RESERVATION_OWNER_REQUIRED", status=422)
    row = PaymentCashReservation(sandbox_id=principal.sandbox_id, user_id=principal.user_id, instrument_id=fresh["instrument_id"],
        account_id=fresh["account_id"], reservation_key=reservation_key, owner_ref=owner_ref, request_hash=digest,
        amount_minor=amount_minor, remaining_minor=amount_minor, expires_at=as_utc(expires_at) if expires_at else None)
    session.add(row); session.flush()
    return reservation_view(row)


def reservation_view(row):
    return {"schema_version": "payment-reservation-v1", "reservation_id": row.id, "account_id": row.account_id,
        "instrument_id": row.instrument_id, "amount_minor": row.amount_minor, "remaining_minor": row.remaining_minor,
        "status": row.status, "version": row.version, "currency": "CNY", "owner_ref": row.owner_ref}


@atomic_domain
def release_cash_reservation(session, principal, reservation_id, *, operation_key):
    _key(operation_key); require_profile_actor(session, principal, lock=True)
    digest = canonical_hash({"reservation_id": reservation_id, "operation": "RELEASE"})
    prior = session.scalar(select(PaymentJournal).where(PaymentJournal.sandbox_id == principal.sandbox_id,
        PaymentJournal.user_id == principal.user_id, PaymentJournal.operation_key == operation_key))
    if prior:
        if prior.kind != "RESERVATION_RELEASE" or prior.request_hash != digest: _deny("PAYMENT_IDEMPOTENCY_CONFLICT")
        return {**deepcopy(prior.result_json), "replayed": True}
    row = session.get(PaymentCashReservation, reservation_id)
    if not row or (row.user_id, row.sandbox_id) != (principal.user_id, principal.sandbox_id): _deny("PAYMENT_RESERVATION_NOT_OWNED", status=403)
    session.scalar(select(Account).where(Account.id == row.account_id).with_for_update())
    row = session.scalar(select(PaymentCashReservation).where(PaymentCashReservation.id == row.id).with_for_update().execution_options(populate_existing=True))
    if row.status == "ACTIVE": row.remaining_minor = 0; row.status = "RELEASED"; row.version += 1
    result = reservation_view(row)
    journal = _journal(session, principal, operation_key=operation_key, request_hash=digest, kind="RESERVATION_RELEASE", entries=[])
    journal.result_json = result; session.flush()
    return result


def _journal(session, principal, *, operation_key, request_hash, kind, entries):
    _key(operation_key)
    if session.scalar(select(PaymentJournal.id).where(PaymentJournal.sandbox_id == principal.sandbox_id,
            PaymentJournal.user_id == principal.user_id, PaymentJournal.operation_key == operation_key)):
        _deny("PAYMENT_IDEMPOTENCY_CONFLICT")
    if any(sum(delta for ref, currency, delta in entries if currency == selected) != 0 for selected in {currency for _, currency, _ in entries}):
        _deny("PAYMENT_LEDGER_NOT_BALANCED", status=500)
    journal = PaymentJournal(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        operation_key=operation_key, request_hash=request_hash, kind=kind, result_json={})
    session.add(journal); session.flush()
    for ref, currency, delta in entries:
        if delta: session.add(PaymentLedgerEntry(journal_id=journal.id, sandbox_id=principal.sandbox_id,
            user_id=principal.user_id, account_ref=ref, currency=currency, delta_minor=delta))
    session.flush()
    return journal


@atomic_domain
def post_merchant_payment(session, principal, *, instrument_snapshot, amount_minor, merchant_ref, operation_key,
        reservation_id=None, authorization_ref=None, channel="MERCHANT", fx_quote_id=None):
    _amount(amount_minor); _key(operation_key); require_profile_actor(session, principal, lock=True)
    material = {"instrument_id": instrument_snapshot.get("instrument_id"), "account_id": instrument_snapshot.get("account_id"),
        "credit_account_id": instrument_snapshot.get("credit_account_id"), "currency": instrument_snapshot.get("currency"),
        "amount_minor": amount_minor, "merchant_ref": merchant_ref, "reservation_id": reservation_id,
        "authorization_ref": authorization_ref, "channel": channel, "fx_quote_id": fx_quote_id}
    digest = canonical_hash(material)
    prior = session.scalar(select(PaymentRecord).where(PaymentRecord.sandbox_id == principal.sandbox_id,
        PaymentRecord.user_id == principal.user_id, PaymentRecord.operation_key == operation_key))
    if prior:
        if prior.request_hash != digest: _deny("PAYMENT_IDEMPOTENCY_CONFLICT")
        return {**deepcopy(prior.receipt_json), "replayed": True}
    if not isinstance(merchant_ref, str) or not merchant_ref or len(merchant_ref) > 120: _deny("PAYMENT_MERCHANT_REQUIRED", status=422)
    if channel not in {"MERCHANT", "LIFE_PLAN"}: _deny("PAYMENT_CHANNEL_UNSUPPORTED")
    if instrument_snapshot.get("instrument_type") == "CREDIT":
        if channel != "MERCHANT" or reservation_id: _deny("CREDIT_CARD_CASH_CHANNEL_FORBIDDEN", status=403)
        from .credit_service import post_credit_purchase
        return post_credit_purchase(session, principal, instrument_snapshot=instrument_snapshot, amount_minor=amount_minor,
            merchant_ref=merchant_ref, operation_key=operation_key, fx_quote_id=fx_quote_id, authorization_ref=authorization_ref)
    fresh = validate_instrument_snapshot(session, principal, instrument_snapshot, purpose=channel)
    if fresh["currency"] != "CNY": _deny("PAYMENT_MERCHANT_CNY_ONLY")
    if amount_minor > fresh["transaction_limit_minor"]: _deny("PAYMENT_CARD_LIMIT_EXCEEDED")
    reservation = None
    if reservation_id:
        reservation = session.scalar(select(PaymentCashReservation).where(PaymentCashReservation.id == reservation_id,
            PaymentCashReservation.sandbox_id == principal.sandbox_id, PaymentCashReservation.user_id == principal.user_id)
            .with_for_update().execution_options(populate_existing=True))
        if not reservation or reservation.status != "ACTIVE" or reservation.instrument_id != fresh["instrument_id"] \
                or reservation.account_id != fresh["account_id"] or reservation.remaining_minor < amount_minor \
                or reservation.expires_at and as_utc(reservation.expires_at) <= as_utc(utcnow()):
            _deny("PAYMENT_RESERVATION_CHANGED")
    account = session.get(Account, fresh["account_id"])
    available = account.balance_minor - cash_reserved_minor(session, principal, account.id, exclude_reservation_id=reservation_id)
    if available < amount_minor: _deny("PAYMENT_INSUFFICIENT_FUNDS")
    if reservation:
        reservation.remaining_minor -= amount_minor; reservation.version += 1
        if reservation.remaining_minor == 0: reservation.status = "CONSUMED"
    account.balance_minor -= amount_minor; account.version += 1
    journal = _journal(session, principal, operation_key=operation_key, request_hash=digest, kind="MERCHANT_PAYMENT",
        entries=[(account.id, "CNY", -amount_minor), ("MERCHANT:" + merchant_ref, "CNY", amount_minor)])
    payment_id = new_id(); posted_at = as_utc(utcnow())
    receipt = {"schema_version": "payment-receipt-v1", "payment_id": payment_id, "reservation_id": reservation_id,
        "operation_key": operation_key, "status": "POSTED", "instrument_id": fresh["instrument_id"], "instrument_type": "DEBIT",
        "account_id": account.id, "credit_account_id": None, "amount_minor": amount_minor, "currency": "CNY",
        "merchant_ref": merchant_ref, "authorization_ref": authorization_ref, "cash_debit_minor": amount_minor,
        "credit_debit_minor": 0, "base_exposure_minor": 0, "ledger_balanced": True, "posted_at": posted_at.isoformat(), "replayed": False}
    session.add(PaymentRecord(id=payment_id, sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        instrument_id=fresh["instrument_id"], instrument_type="DEBIT", account_id=account.id, reservation_id=reservation_id,
        amount_minor=amount_minor, currency="CNY", merchant_ref=merchant_ref, authorization_ref=authorization_ref,
        operation_key=operation_key, request_hash=digest, journal_id=journal.id, receipt_json=receipt))
    session.add(Transaction(sandbox_id=principal.sandbox_id, account_id=account.id, card_id=fresh["card_id"],
        occurred_at=posted_at, amount_minor=-amount_minor, merchant=merchant_ref, category="虚拟商户消费",
        flag="NORMAL", source_kind="MERCHANT_PAYMENT", operation_ref="merchant:" + payment_id))
    journal.result_json = receipt; session.flush()
    return receipt


@atomic_domain
def refund_merchant_payment(session, principal, payment_id, *, operation_key, authorization_ref=None):
    _key(operation_key); require_profile_actor(session, principal, lock=True)
    payment = session.get(PaymentRecord, payment_id)
    if not payment or (payment.sandbox_id, payment.user_id) != (principal.sandbox_id, principal.user_id):
        _deny("PAYMENT_NOT_OWNED", status=403)
    digest = canonical_hash({"payment_id": payment_id, "authorization_ref": authorization_ref})
    prior = session.scalar(select(PaymentRefund).where(PaymentRefund.payment_id == payment_id))
    if prior:
        if prior.request_hash != digest: _deny("PAYMENT_IDEMPOTENCY_CONFLICT")
        return {**deepcopy(prior.receipt_json), "replayed": True}
    if payment.instrument_type == "CREDIT":
        from .credit_service import refund_credit_payment
        return refund_credit_payment(session, principal, payment, operation_key=operation_key, authorization_ref=authorization_ref)
    account = session.scalar(select(Account).where(Account.id == payment.account_id, Account.sandbox_id == principal.sandbox_id,
        Account.user_id == principal.user_id).with_for_update().execution_options(populate_existing=True))
    if not account or account.currency != payment.currency: _deny("PAYMENT_REFUND_SOURCE_CHANGED")
    from .internal_banking import ACCOUNT_BALANCE_LIMIT_MINOR
    if account.balance_minor+payment.amount_minor > ACCOUNT_BALANCE_LIMIT_MINOR: _deny("PAYMENT_REFUND_CAPACITY_EXCEEDED")
    # Refunds go to the immutable original account, even after card loss,
    # replacement or an explicitly migrated card/account binding.
    account.balance_minor += payment.amount_minor; account.version += 1
    journal = _journal(session, principal, operation_key=operation_key, request_hash=digest, kind="MERCHANT_REFUND",
        entries=[(account.id, payment.currency, payment.amount_minor), ("MERCHANT:" + payment.merchant_ref, payment.currency, -payment.amount_minor)])
    refund_id = new_id()
    receipt = {"schema_version": "payment-refund-receipt-v1", "refund_id": refund_id, "payment_id": payment.id,
        "account_id": account.id, "reservation_id": payment.reservation_id, "instrument_id": payment.instrument_id,
        "instrument_type": "DEBIT", "amount_minor": payment.amount_minor, "currency": payment.currency, "status": "POSTED",
        "cash_credit_minor": payment.amount_minor, "credit_reduction_minor": 0, "ledger_balanced": True,
        "operation_key": operation_key, "authorization_ref": authorization_ref, "replayed": False}
    session.add(PaymentRefund(id=refund_id, sandbox_id=principal.sandbox_id, user_id=principal.user_id, payment_id=payment.id,
        operation_key=operation_key, request_hash=digest, receipt_json=receipt))
    session.add(Transaction(sandbox_id=principal.sandbox_id, account_id=account.id, card_id=None, occurred_at=utcnow(),
        amount_minor=payment.amount_minor, merchant=payment.merchant_ref, category="退款", flag="NORMAL",
        source_kind="MERCHANT_REFUND", operation_ref="merchant-refund:" + refund_id))
    payment.status = "REFUNDED"; journal.result_json = receipt; session.flush()
    return receipt
