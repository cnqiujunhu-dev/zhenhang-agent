"""Simulation FX accounting with distinct wallets and currency-balanced journals."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import re

from sqlalchemy import func, select

from .access_models import LoginAccount
from .contracts import DomainError
from .fx_models import DualCurrencyProfile, FxLedgerEntry, FxOperation, FxQuote, FxStatement, FxWallet
from .fx_rates import QUOTE_TTL, decimal_text, validate_market_rate
from .models import Account, Card, Transaction, new_id, utcnow
from .planning import canonical_hash
from .simulation import as_utc, business_now


FX_ROLES = {"CUSTOMER", "DEV_TEST"}
MAX_USD_MINOR = 500000
MAX_CNY_MINOR = 5000000
SEED_DUAL = {"qiujunhu": (350000, "5568"), "wangwei": (500000, "5588"), "test": (1000000, "5598")}


def usd_ref(wallet_id):
    return "USD_WALLET:" + wallet_id


def _login(session, sid, uid):
    row = session.scalar(select(LoginAccount).where(LoginAccount.sandbox_id == sid, LoginAccount.user_id == uid))
    if row is None or row.role not in FX_ROLES or row.status != "ACTIVE":
        raise DomainError("FX_ACTOR_FORBIDDEN", "外汇业务仅开放本人有效业务账号，管理员与协助人不能执行", 403)
    return row


def _registered_defaults(session, login):
    from sqlalchemy import inspect
    from .credit_models import CustomerPaymentDefaults
    if not inspect(session.connection()).has_table("customer_payment_defaults"):
        return None
    return session.scalar(select(CustomerPaymentDefaults).where(CustomerPaymentDefaults.sandbox_id == login.sandbox_id,
        CustomerPaymentDefaults.user_id == login.user_id))


def _cny(session, login, *, card=None, receiving=False):
    if card is not None:
        bank = session.get(Account, card.account_id)
        if bank and (card.user_id, card.sandbox_id, bank.user_id, bank.sandbox_id, bank.currency) == (
                login.user_id, login.sandbox_id, login.user_id, login.sandbox_id, "CNY"):
            return bank
        raise DomainError("FX_BINDING_CHANGED", "明确借记卡不属于该人民币账户。", 409)
    rows = list(session.scalars(select(Account).where(Account.sandbox_id == login.sandbox_id, Account.user_id == login.user_id)))
    if len(rows) != 1:
        from .payment_instruments import resolve_source_instrument, resolve_recipient_instrument
        from .services import Principal
        scope = resolve_recipient_instrument(session, login.username) if receiving else resolve_source_instrument(
            session, Principal(login.user_id, login.sandbox_id), None, "CNY", purpose="FX")
        bank = session.get(Account, scope.get("account_id"))
        if scope.get("instrument_type") == "DEBIT" and bank and (bank.user_id, bank.sandbox_id, bank.currency) == (login.user_id, login.sandbox_id, "CNY"):
            return bank
    if len(rows) != 1 or rows[0].currency != "CNY":
        raise DomainError("FX_CNY_ACCOUNT_UNAVAILABLE", "没有唯一人民币资金账户", 409)
    return rows[0]


def _profile(session, login):
    profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == login.user_id))
    if profile is None:
        return None, None, None
    wallet, card = session.get(FxWallet, profile.usd_wallet_id), session.get(Card, profile.card_id)
    bank = session.get(Account, card.account_id) if card else None
    if wallet is None or card is None or profile.sandbox_id != login.sandbox_id \
            or (wallet.sandbox_id, wallet.user_id, wallet.currency) != (login.sandbox_id, login.user_id, "USD") \
            or bank is None or (bank.sandbox_id, bank.user_id, bank.currency) != (login.sandbox_id, login.user_id, "CNY") \
            or (card.sandbox_id, card.user_id, card.account_id) != (login.sandbox_id, login.user_id, bank.id):
        raise DomainError("FX_BINDING_CHANGED", "双币卡与美元钱包绑定事实不一致", 409)
    return profile, wallet, card


def recipientcurrencies(session, login):
    if login.role not in FX_ROLES or login.status != "ACTIVE":
        return []
    _cny(session, login, receiving=True)
    profile, wallet, card = _profile(session, login)
    if _registered_defaults(session, login):
        from .payment_instruments import resolve_recipient_instrument
        try:
            resolve_recipient_instrument(session, login.username, None, "USD")
        except DomainError:
            return ["CNY"]
    return ["CNY", "USD"] if profile and profile.status == wallet.status == card.status == "ACTIVE" else ["CNY"]


def fxcardprojection(session, card):
    profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.card_id == card.id,
        DualCurrencyProfile.sandbox_id == card.sandbox_id, DualCurrencyProfile.user_id == card.user_id))
    bank = session.get(Account, card.account_id)
    balances = {"CNY": bank.balance_minor} if bank and (bank.sandbox_id, bank.user_id, bank.currency) == (card.sandbox_id, card.user_id, "CNY") else {}
    if profile is None:
        return {"currency_mode": "CNY_ONLY", "supported_currencies": ["CNY"], "balances_by_currency": balances, "fx_card": False}
    wallet = session.get(FxWallet, profile.usd_wallet_id)
    if wallet is None or (wallet.sandbox_id, wallet.user_id, wallet.currency) != (card.sandbox_id, card.user_id, "USD"):
        raise DomainError("FX_BINDING_CHANGED", "双币钱包投影不属于此卡", 409)
    return {"currency_mode": "CNY_USD", "supported_currencies": ["CNY", "USD"],
            "balances_by_currency": {**balances, "USD": wallet.balance_minor}, "fx_card": True,
            "usd_wallet_id": wallet.id, "fx_profile_version": profile.version, "usd_wallet_version": wallet.version,
            "limit_currency": "CNY"}


def _physical_outer_begin(session):
    if session.bind.dialect.name == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")


def _statement(session, operation, sid, uid, ref, currency, delta, merchant):
    direction = "OUTGOING" if delta < 0 else "INCOMING"
    op_ref = f"fx:{operation.id}:{currency}"
    existing = session.scalar(select(FxStatement).where(FxStatement.sandbox_id == sid, FxStatement.operation_ref == op_ref))
    if existing:
        if (existing.user_id, existing.account_ref, existing.currency, existing.amount_minor) != (uid, ref, currency, delta):
            raise DomainError("FX_STATEMENT_CONFLICT", "原外汇操作的现金镜像范围不一致", 409)
        return existing
    from .operational_clock import posting_now
    row = FxStatement(operation_id=operation.id, sandbox_id=sid, user_id=uid, account_ref=ref,
                      currency=currency, amount_minor=delta, direction=direction, merchant=merchant,
                      occurred_at=posting_now(session, sid, uid), operation_ref=op_ref)
    session.add(row)
    if currency == "CNY":
        prior = session.scalar(select(Transaction).where(Transaction.sandbox_id == sid, Transaction.operation_ref == op_ref))
        if prior:
            if (prior.account_id, prior.source_kind, prior.amount_minor) != (ref, "FX", delta):
                raise DomainError("FX_CNY_MIRROR_CONFLICT", "人民币原账户镜像发生冲突", 409)
        else:
            session.add(Transaction(sandbox_id=sid, account_id=ref, occurred_at=row.occurred_at, amount_minor=delta,
                merchant=merchant, category="外汇资金搬移", flag="NORMAL", source_kind="FX", operation_ref=op_ref))
    session.flush()
    return row


def bootstrap_dual_currency_accounts(session):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text
        session.execute(text("SELECT pg_advisory_xact_lock(9360600)"))
    created, existing = [], []
    # The advisory lock serializes bootstrap callers, not bank transfers. Take
    # the entire registry set in the same ID order as transfer settlement before
    # iterating configuration names, or two actors can form an inverse lock ring.
    locked_logins = {row.username: row for row in session.scalars(select(LoginAccount)
        .where(LoginAccount.username.in_(set(SEED_DUAL))).order_by(LoginAccount.id)
        .with_for_update().execution_options(populate_existing=True))}
    for name, (funding, tail) in SEED_DUAL.items():
        login = locked_logins.get(name)
        if login is None or login.role not in FX_ROLES:
            continue
        profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == login.user_id))
        if profile:
            _profile(session, login)
            existing.append(name)
            continue
        bank = _cny(session, login)
        if session.scalar(select(Card.id).where(Card.sandbox_id == login.sandbox_id, Card.user_id == login.user_id, Card.last4 == tail)):
            raise DomainError("FX_SEED_CARD_CONFLICT", "新增双币卡末四位与已有卡冲突，不覆盖旧卡", 409)
        if session.scalar(select(FxWallet.id).where(FxWallet.user_id == login.user_id, FxWallet.currency == "USD")):
            raise DomainError("FX_SEED_WALLET_CONFLICT", "已存在未绑定美元钱包，不重置余额", 409)
        wallet = FxWallet(sandbox_id=login.sandbox_id, user_id=login.user_id, balance_minor=0)
        # Compact newly-created customers already own two CNY cards. Bind the
        # secondary ACTIVE card instead of creating a third; never renumber,
        # reset, unlock or replace an existing card. Old one-card regression
        # sandboxes retain the dedicated-card path.
        from .resource_defaults import ARCHIVED_RESOURCE_STATUS, DEFAULT_CARD_COUNT
        cards = list(session.scalars(select(Card).where(Card.sandbox_id == login.sandbox_id,
            Card.user_id == login.user_id, Card.status != ARCHIVED_RESOURCE_STATUS)
            .order_by(Card.id).with_for_update().execution_options(populate_existing=True)))
        if len(cards) >= DEFAULT_CARD_COUNT:
            choices = [row for row in cards if row.last4 != "4321" and row.status == "ACTIVE"]
            if not choices:
                raise DomainError("FX_SEED_CARD_UNAVAILABLE", "无可用第二卡承接双币资金，未自动解锁或增卡", 409)
            card = next((row for row in choices if row.last4 == "6672"), choices[0])
            session.add(wallet)
        else:
            card = Card(sandbox_id=login.sandbox_id, user_id=login.user_id, account_id=bank.id, last4=tail,
                        status="ACTIVE", limit_minor=2000000)
            session.add_all([wallet, card])
        session.flush()
        session.add(DualCurrencyProfile(sandbox_id=login.sandbox_id, user_id=login.user_id, card_id=card.id, usd_wallet_id=wallet.id))
        op = FxOperation(sandbox_id=login.sandbox_id, user_id=login.user_id, kind="INITIAL_FUNDING", original_key=f"fx-seed-v1:{login.user_id}",
            requested_currency="USD", requested_amount_minor=funding, source_currency="USD", target_currency="USD",
            source_ref="SIM_FX_INITIAL_POOL", target_ref=usd_ref(wallet.id), debit_minor=funding, credit_minor=funding,
            receipt_json={"synthetic": True, "funding_source": "EXPLICIT_SIM_INITIAL_FUNDS", "fee_minor": 0, "fee_source": "SIM_POLICY"})
        session.add(op); session.flush()
        session.add_all([FxLedgerEntry(operation_id=op.id, sandbox_id=login.sandbox_id, user_id=login.user_id,
            account_ref=usd_ref(wallet.id), currency="USD", delta_minor=funding),
            FxLedgerEntry(operation_id=op.id, sandbox_id=login.sandbox_id, user_id=login.user_id,
            account_ref=f"FX_POOL:{op.id}:USD", currency="USD", delta_minor=-funding)])
        wallet.balance_minor = funding
        _statement(session, op, login.sandbox_id, login.user_id, usd_ref(wallet.id), "USD", funding, "平台初始化的美元资金")
        from .card_identifiers import ensure_card_identifier
        ensure_card_identifier(session, card)
        created.append(name)
    session.flush()
    return {"created": created, "existing": existing, "synthetic": True, "old_cny_balances_changed": False, "funding_source": "EXPLICIT_SIM_INITIAL_FUNDS"}


def _snapshot_side(session, login, currency, *, source=False, selected_card=None, selected_card_id=None, explicit_source=False):
    profile, wallet, dual_card = _profile(session, login)
    defaults = _registered_defaults(session, login)
    instrument = None
    selected = session.get(Card, selected_card_id) if selected_card_id else None
    if selected_card_id and selected is None:
        raise DomainError("FX_CARD_BINDING_CHANGED", "明确卡已不存在，未改用默认卡。", 409)
    if selected and (selected.user_id, selected.sandbox_id) != (login.user_id, login.sandbox_id):
        raise DomainError("FX_CARD_BINDING_CHANGED", "明确卡不属于该收付款主体。", 409)
    if currency == "USD":
        if not profile or profile.status != "ACTIVE" or wallet.status != "ACTIVE" or dual_card.status != "ACTIVE":
            raise DomainError("FX_USD_ACCOUNT_UNAVAILABLE", "指定美元收付能力暂不可用，未替换为人民币", 409)
        if selected_card_id and dual_card.id != selected_card_id:
            raise DomainError("FX_CARD_CURRENCY_MISMATCH", "明确指定卡不是此美元钱包的双币卡", 409)
        if selected_card and not selected_card_id and dual_card.last4 != selected_card:
            raise DomainError("FX_CARD_CURRENCY_MISMATCH", "指定卡不是此美元钱包的双币卡", 409)
        if defaults and not selected_card_id and not selected_card:
            from .payment_instruments import resolve_source_instrument, resolve_recipient_instrument
            from .services import Principal
            instrument = resolve_source_instrument(session, Principal(login.user_id, login.sandbox_id), None, "USD", purpose="FX") if source else resolve_recipient_instrument(session, login.username, None, "USD")
            if instrument["card_id"] != dual_card.id:
                raise DomainError("FX_CARD_CURRENCY_MISMATCH", "登记默认卡不支持本美元钱包，未自动换卡。", 409)
        entity, ref, card = wallet, usd_ref(wallet.id), dual_card
    elif currency == "CNY":
        card = selected
        if source:
            cards = list(session.scalars(select(Card).where(Card.sandbox_id == login.sandbox_id, Card.user_id == login.user_id)))
            if selected_card or selected_card_id:
                matching = [row for row in cards if row.id == selected_card_id] if selected_card_id else [row for row in cards if row.last4 == selected_card]
                if len(matching) != 1:
                    raise DomainError("FX_SOURCE_CARD_UNRESOLVED", "请指定唯一本人付款卡", 409)
                card = matching[0]
            elif defaults:
                from .payment_instruments import resolve_source_instrument
                from .services import Principal
                instrument = resolve_source_instrument(session, Principal(login.user_id, login.sandbox_id), None, "CNY", purpose="FX")
                card = session.get(Card, instrument["card_id"])
            elif profile and not explicit_source:
                card = dual_card
            else:
                card = next((row for row in cards if row.last4 == "4321"), cards[0] if len(cards) == 1 else None)
            if card is None or card.status != "ACTIVE":
                raise DomainError("FX_SOURCE_CARD_UNAVAILABLE", "付款卡已锁定、挂失或不可用，不能自动换卡规避", 409)
        elif card is None:
            from .payment_instruments import resolve_recipient_instrument
            instrument = resolve_recipient_instrument(session, login.username, None, "CNY")
            card = session.get(Card, instrument["card_id"])
        bank = _cny(session, login, card=card, receiving=not source)
        entity, ref = bank, bank.id
    else:
        raise DomainError("FX_CURRENCY_UNSUPPORTED", "本轮只支持CNY与USD", 422)
    bank = _cny(session, login, card=card, receiving=not source)
    if instrument is None:
        from .payment_instruments import resolve_source_instrument, resolve_recipient_instrument
        from .services import Principal
        instrument = resolve_source_instrument(session, Principal(login.user_id, login.sandbox_id), "debit:" + card.id,
            currency, purpose="FX") if source else resolve_recipient_instrument(session, login.username, "debit:" + card.id, currency)
    from .card_identifiers import card_identifier_view
    ident = card_identifier_view(session, card) if card else None
    return {"sandbox_id": login.sandbox_id, "user_id": login.user_id, "login_id": login.id, "username": login.username,
            "currency": currency, "account_ref": ref, "balance_version": entity.version, "bank_account_id": bank.id,
            "wallet_id": wallet.id if currency == "USD" else None, "profile_id": profile.id if currency == "USD" else None,
            "profile_version": profile.version if currency == "USD" else None,
            "card_id": card.id if card else None, "card_version": card.version if card else None,
            "card_last4": card.last4 if card else None,
            "payment_instrument_snapshot": instrument,
            "payment_instrument_role": "SOURCE" if source else "RECIPIENT",
            **({"card_number": ident["card_number"], "display_last4": ident["display_last4"]} if ident else {})}


def quote_public(quote):
    scope = quote.scope_json
    return {"quote_id": quote.id, "rate": decimal_text(quote.market_rate), "base_currency": "USD", "quote_currency": "CNY",
            "market_date": quote.market_date.isoformat(), "provider": quote.provider, "source_url": quote.source_url,
            "fetched_at": as_utc(quote.fetched_at).isoformat(), "expires_at": as_utc(quote.expires_at).isoformat(),
            "requested_currency": quote.requested_currency, "requested_amount_minor": quote.requested_amount_minor,
            "source_currency": quote.source_currency, "target_currency": quote.target_currency,
            "debit_minor": quote.debit_minor, "credit_minor": quote.credit_minor, "recipient_username": scope["target"]["username"],
            "source_card_last4": scope["source"]["card_last4"],
            "fee_minor": 0, "fee_source": "SIM_POLICY", "reference_only": True, "simulated_execution_price": True,
            "funding_notice": scope["funding_notice"], "settlement_notice": scope["settlement_notice"],
            "rounding": scope["rounding"], "cny_reference_minor": scope["cny_reference_minor"], "scope_hash": quote.scope_hash,
            **({"source_card_number": scope["source"]["card_number"], "source_card_display_last4": scope["source"]["display_last4"],
                "source_card_legacy_last4": scope["source"]["card_last4"]} if scope["source"].get("card_number") else {}),
            **({"recipient_card_number": scope["target"]["card_number"], "recipient_card_display_last4": scope["target"]["display_last4"]}
                if scope["target"].get("card_number") else {}),
            **({"declared_recipient_reference": scope["declared_recipient_context"]["requested_reference"]}
                if scope.get("declared_recipient_context") else {}),
            **({"alias_binding": scope["alias_binding"]} if scope.get("alias_binding") else {})}


def create_fx_quote(session, sandbox_id, user_id, kind, slots, market_rate):
    rate_data = validate_market_rate(market_rate)
    rate = Decimal(rate_data["rate"])
    actor = _login(session, sandbox_id, user_id)
    amount = slots.get("amount_minor")
    requested = slots.get("currency")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or requested not in {"CNY", "USD"}:
        raise DomainError("FX_AMOUNT_REQUIRED", "请明确金额和CNY/USD币种，金额使用该币种最小单位", 422)
    source_currency, target_currency = slots.get("source_currency"), slots.get("target_currency")
    if any(value is not None and value not in {"CNY", "USD"} for value in (source_currency, target_currency)):
        raise DomainError("FX_CURRENCY_UNSUPPORTED", "只支持CNY与USD，不替换用户指定币种", 422)
    profile, wallet, card = _profile(session, actor)
    source_card_id = None
    source_reference = slots.get("card_number") or slots.get("card_last4")
    defaults = _registered_defaults(session, actor)
    if source_reference:
        from .transfer_payment_scope import selected_source_reference
        from .payment_instruments import resolve_source_instrument
        from .services import Principal
        source_reference = selected_source_reference(session, Principal(user_id, sandbox_id), slots)
        selected_source = resolve_source_instrument(session, Principal(user_id, sandbox_id), source_reference, "CNY", purpose="FX")
        source_card_id = selected_source["card_id"]
    target_context = declared_target_context = None
    if kind == "TRANSFER":
        if requested != "USD":
            raise DomainError("FX_USD_REQUEST_REQUIRED", "FX转账只接受明确美元计价请求，人民币转账使用原领域", 422)
        name = slots.get("payee_name")
        from .fx_recipient_selection import fx_recipient_context
        try:
            target_context, declared_target_context = fx_recipient_context(session, sandbox_id, user_id, name,
                slots.get("recipient_card_reference"))
        except DomainError as error:
            # Preserve the public FX errors while retaining precise new card
            # and personal-alias errors. Existing clients must not reinterpret
            # a failed registered-account lookup as a new financial outcome.
            codes = {"CONTACT_REGISTERED_RECIPIENT_REQUIRED": "FX_REGISTERED_RECIPIENT_REQUIRED",
                     "RECIPIENT_ACCOUNT_UNAVAILABLE": "FX_RECIPIENT_UNAVAILABLE",
                     "SELF_TRANSFER_FORBIDDEN": "FX_SELF_TRANSFER_FORBIDDEN"}
            if error.code in codes:
                raise DomainError(codes[error.code], error.message, error.status_code) from error
            raise
        recipient = session.get(LoginAccount, target_context["recipient_login_account_id"])
        if recipient is None or recipient.role not in FX_ROLES or recipient.status != "ACTIVE":
            raise DomainError("FX_RECIPIENT_UNAVAILABLE", "收款账号不存在、停用或不能收款", 409)
        if recipient.user_id == actor.user_id:
            raise DomainError("FX_SELF_TRANSFER_FORBIDDEN", "本人换汇请使用换汇流程，不能向自己转账", 409)
        if recipient.sandbox_id == actor.sandbox_id:
            raise DomainError("FX_REGISTERED_SANDBOX_REQUIRED", "本轮具名银行账号应使用独立账套，未跨主体共用同一账套", 409)
        if source_currency is None:
            if defaults or source_reference:
                from .payment_instruments import resolve_source_instrument
                from .services import Principal
                selected_source = resolve_source_instrument(session, Principal(user_id, sandbox_id), source_reference, "CNY", purpose="FX")
                try:
                    usd_source = resolve_source_instrument(session, Principal(user_id, sandbox_id), source_reference, "USD", purpose="FX")
                    source_currency = "USD" if usd_source["available_balance_minor"] >= amount else "CNY"
                except DomainError as error:
                    if error.code != "PAYMENT_CURRENCY_UNAVAILABLE":
                        raise
                    source_currency = "CNY"
            else:
                if profile and card.status != "ACTIVE":
                    raise DomainError("FX_SOURCE_CARD_UNAVAILABLE", "双币付款卡不可用，未自动改人民币扣款", 409)
                source_currency = "USD" if profile and profile.status == wallet.status == "ACTIVE" and wallet.balance_minor >= amount else "CNY"
        selected_target_card = session.get(Card, target_context.get("recipient_card_id")) if target_context.get("recipient_card_id") else None
        target_profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == recipient.user_id))
        if selected_target_card:
            capabilities = ["CNY"]
            if target_profile and target_profile.card_id == selected_target_card.id:
                _target_profile, target_wallet, _target_card = _profile(session, recipient)
                if target_profile.status == target_wallet.status == selected_target_card.status == "ACTIVE":
                    capabilities.append("USD")
        else:
            capabilities = recipientcurrencies(session, recipient)
        target_currency = target_currency or ("USD" if "USD" in capabilities else "CNY")
        if target_currency not in capabilities:
            raise DomainError("FX_RECEIVER_CURRENCY_UNAVAILABLE", "收款账号不支持指定收款币种，未替换", 409)
        debit = amount if source_currency == "USD" else int((Decimal(amount)*rate).to_integral_value(rounding=ROUND_CEILING if target_currency == "USD" else ROUND_FLOOR))
        credit = amount if target_currency == "USD" else int((Decimal(amount)*rate).to_integral_value(rounding=ROUND_FLOOR))
        if source_currency == target_currency == "CNY":
            debit = credit
        rounding = "USD_EXACT_CNY_TARGET_FLOOR_SOURCE_CEIL"
    elif kind == "EXCHANGE":
        recipient = actor
        if source_currency is None or target_currency is None or source_currency == target_currency or requested not in {source_currency, target_currency}:
            raise DomainError("FX_EXCHANGE_DIRECTION_REQUIRED", "请明确CNY与USD不同币种间的换汇方向", 422)
        if requested == source_currency:
            debit = amount
            calculated = Decimal(amount)*rate if source_currency == "USD" else Decimal(amount)/rate
            credit = int(calculated.to_integral_value(rounding=ROUND_FLOOR))
            rounding = "SOURCE_FIXED_TARGET_FLOOR"
        else:
            credit = amount
            calculated = Decimal(amount)*rate if source_currency == "CNY" else Decimal(amount)/rate
            debit = int(calculated.to_integral_value(rounding=ROUND_CEILING))
            rounding = "TARGET_FIXED_SOURCE_CEIL"
        # An explicitly directed self-exchange selects the unique existing USD
        # wallet, not the default external receiving card and never a new one.
        if source_currency == "USD" and source_card_id is None and profile:
            source_card_id = profile.card_id
    else:
        raise DomainError("FX_KIND_UNKNOWN", "不支持此外汇资金动作", 422)
    if min(debit, credit) <= 0:
        raise DomainError("FX_BELOW_MINOR_UNIT", "换汇结果不足一分或一美分，未扣款", 409)
    usd_values = [value for currency, value in ((requested, amount), (source_currency, debit), (target_currency, credit)) if currency == "USD"]
    cny_reference = max([int((Decimal(value)*rate).to_integral_value(rounding=ROUND_CEILING)) for value in usd_values]
                        + [value for currency, value in ((source_currency, debit), (target_currency, credit)) if currency == "CNY"])
    if max(usd_values or [0]) > MAX_USD_MINOR or cny_reference > MAX_CNY_MINOR:
        raise DomainError("FX_TRANSFER_LIMIT", "超过5000美元或参考人民币5万元的单笔限额", 409)
    source_side = _snapshot_side(session, actor, source_currency, source=True, selected_card=slots.get("card_last4"),
                                 selected_card_id=source_card_id, explicit_source=slots.get("source_currency") == "CNY")
    target_card_id = (target_context or {}).get("recipient_card_id")
    if kind == "EXCHANGE" and target_currency == "USD" and profile:
        target_card_id = profile.card_id
    target_side = _snapshot_side(session, recipient, target_currency, selected_card_id=target_card_id)
    if declared_target_context:
        from .fx_recipient_selection import fx_recipient_instrument
        from .services import Principal
        selected_target = fx_recipient_instrument(session, Principal(user_id, sandbox_id), recipient.username,
            slots["recipient_card_reference"], target_currency)
        if (selected_target["card_id"], selected_target["account_id"], selected_target["currency"]) != (
                target_side["card_id"], target_side["bank_account_id"], target_currency):
            raise DomainError("FX_RECIPIENT_CARD_CONFLICT", "明确收款卡与实际收款币种账户不一致。", 409)
        target_side["payment_instrument_snapshot"] = selected_target
    source_entity = session.get(FxWallet, source_side["wallet_id"]) if source_currency == "USD" else session.get(Account, source_side["bank_account_id"])
    from .payment_instruments import cash_reserved_minor
    from .services import Principal
    spendable = source_entity.balance_minor - (cash_reserved_minor(session, Principal(user_id, sandbox_id), source_entity.id) if source_currency == "CNY" else 0)
    if spendable < debit:
        raise DomainError("FX_INSUFFICIENT_FUNDS", "指定资金来源余额不足，不合并或偷偷扣另一钱包", 409)
    source_card = session.get(Card, source_side["card_id"])
    if source_card and cny_reference > source_card.limit_minor:
        raise DomainError("FX_CARD_LIMIT", "参考人民币等值超过本付款卡限额", 409)
    now, quote_id = utcnow(), new_id()
    scope = {"quote_id": quote_id, "kind": kind, "requested_currency": requested, "requested_amount_minor": amount,
             "source": source_side, "target": target_side, "debit_minor": debit, "credit_minor": credit,
             "rate": rate_data["rate"], "market_date": rate_data["market_date"], "provider": rate_data["provider"],
             "fetched_at": rate_data["fetched_at"], "source_url": rate_data["source_url"], "expires_at": (now+QUOTE_TTL).isoformat(),
             "fee_minor": 0, "fee_source": "SIM_POLICY", "rounding": rounding, "cny_reference_minor": cny_reference,
             "funding_notice": "美元钱包直接扣款" if source_currency == "USD" else "人民币账户实际扣款；不是自动组合美元余额",
             "settlement_notice": "USD计价、人民币结算，不产生实际美元腿" if source_currency == target_currency == "CNY" else f"实际{target_currency}到账",
             "memo": str(slots.get("memo") or "")[:80]}
    if target_context:
        scope["recipient_context"] = target_context
    if declared_target_context:
        scope["declared_recipient_context"] = declared_target_context
    quote = FxQuote(id=quote_id, sandbox_id=sandbox_id, user_id=user_id, kind=kind, requested_currency=requested,
        requested_amount_minor=amount, source_currency=source_currency, target_currency=target_currency, debit_minor=debit, credit_minor=credit,
        market_rate=rate, market_date=date.fromisoformat(rate_data["market_date"]), provider=rate_data["provider"], source_url=rate_data["source_url"],
        fetched_at=datetime.fromisoformat(rate_data["fetched_at"]), expires_at=now+QUOTE_TTL, scope_json=scope, scope_hash=canonical_hash(scope))
    session.add(quote); session.flush()
    return quote


def lock_quote_resources(session, quote):
    scope = quote.scope_json
    if canonical_hash(scope) != quote.scope_hash or scope.get("quote_id") != quote.id \
            or (scope.get("kind"), scope.get("requested_currency"), scope.get("requested_amount_minor"), scope.get("debit_minor"), scope.get("credit_minor")) \
               != (quote.kind, quote.requested_currency, quote.requested_amount_minor, quote.debit_minor, quote.credit_minor) \
            or scope.get("rate") != decimal_text(quote.market_rate) or scope.get("expires_at") != as_utc(quote.expires_at).isoformat() \
            or scope["source"].get("currency") != quote.source_currency or scope["target"].get("currency") != quote.target_currency \
            or (scope["source"].get("sandbox_id"), scope["source"].get("user_id")) != (quote.sandbox_id, quote.user_id) \
            or scope.get("provider") != quote.provider or scope.get("market_date") != quote.market_date.isoformat() \
            or scope.get("source_url") != quote.source_url or scope.get("fetched_at") != as_utc(quote.fetched_at).isoformat() \
            or quote.fee_minor != 0 or quote.fee_source != "SIM_POLICY":
        raise DomainError("FX_QUOTE_INTEGRITY_FAILED", "报价范围或金额完整性校验失败", 409)
    if as_utc(quote.expires_at) <= utcnow():
        raise DomainError("FX_QUOTE_EXPIRED", "报价已到期，请重新生成方案并确认", 409)
    validate_market_rate({key: scope[key] for key in ("rate", "provider", "market_date", "fetched_at", "source_url")})
    sides = [scope["source"], scope["target"]]
    ids = {side["login_id"] for side in sides}
    logins = {row.id: row for row in session.scalars(select(LoginAccount).where(LoginAccount.id.in_(ids)).order_by(LoginAccount.id)
        .with_for_update().execution_options(populate_existing=True))}
    from .banking_locks import lock_financial_users
    lock_financial_users(session, {(side["sandbox_id"], side["user_id"]) for side in sides})
    cny_ids = {side["bank_account_id"] for side in sides}
    banks = {row.id: row for row in session.scalars(select(Account).where(Account.id.in_(cny_ids)).order_by(Account.id)
        .with_for_update().execution_options(populate_existing=True))}
    profile_ids = {side["profile_id"] for side in sides if side["profile_id"]}
    profiles = {row.id: row for row in session.scalars(select(DualCurrencyProfile).where(DualCurrencyProfile.id.in_(profile_ids)).order_by(DualCurrencyProfile.id)
        .with_for_update().execution_options(populate_existing=True))}
    wallet_ids = {side["wallet_id"] for side in sides if side["wallet_id"]}
    wallets = {row.id: row for row in session.scalars(select(FxWallet).where(FxWallet.id.in_(wallet_ids)).order_by(FxWallet.id)
        .with_for_update().execution_options(populate_existing=True))}
    card_ids = {side["card_id"] for side in sides if side["card_id"]}
    cards = {row.id: row for row in session.scalars(select(Card).where(Card.id.in_(card_ids)).order_by(Card.id)
        .with_for_update().execution_options(populate_existing=True))}
    for side in sides:
        login, bank = logins.get(side["login_id"]), banks.get(side["bank_account_id"])
        if login is None or login.status != "ACTIVE" or login.role not in FX_ROLES or (login.user_id, login.sandbox_id, login.username) != (side["user_id"], side["sandbox_id"], side["username"]) \
                or bank is None or (bank.user_id, bank.sandbox_id, bank.currency) != (side["user_id"], side["sandbox_id"], "CNY"):
            raise DomainError("FX_ACTOR_OR_ACCOUNT_CHANGED", "收付主体或人民币账户发生变化", 409)
        entity = wallets.get(side["wallet_id"]) if side["currency"] == "USD" else bank
        if entity is None or entity.version != side["balance_version"]:
            raise DomainError("FX_BALANCE_VERSION_CHANGED", "钱包或资金账户版本变化，请重新报价", 409)
        if side["currency"] == "USD":
            profile = profiles.get(side["profile_id"])
            if entity.status != "ACTIVE" or (entity.user_id, entity.sandbox_id, entity.currency) != (side["user_id"], side["sandbox_id"], "USD") \
                    or profile is None or profile.status != "ACTIVE" or profile.version != side["profile_version"] \
                    or (profile.card_id, profile.usd_wallet_id, profile.user_id, profile.sandbox_id) != (side["card_id"], entity.id, side["user_id"], side["sandbox_id"]):
                raise DomainError("FX_BINDING_CHANGED", "美元钱包与双币卡绑定或状态变化", 409)
        if side["card_id"]:
            card = cards.get(side["card_id"])
            if card is None or card.status != "ACTIVE" or card.version != side["card_version"] \
                    or (card.user_id, card.sandbox_id, card.account_id) != (side["user_id"], side["sandbox_id"], bank.id):
                raise DomainError("FX_CARD_CHANGED", "确认的银行卡已锁定、停用或发生变化", 409)
    source = wallets.get(sides[0]["wallet_id"]) if quote.source_currency == "USD" else banks[sides[0]["bank_account_id"]]
    target = wallets.get(sides[1]["wallet_id"]) if quote.target_currency == "USD" else banks[sides[1]["bank_account_id"]]
    from .payment_instruments import cash_reserved_minor, validate_instrument_snapshot, validate_recipient_snapshot
    from .services import Principal
    for side in sides:
        instrument = side.get("payment_instrument_snapshot")
        if instrument:
            if side.get("payment_instrument_role") == "SOURCE":
                try:
                    validate_instrument_snapshot(session, Principal(side["user_id"], side["sandbox_id"]), instrument,
                        purpose="FX", amount_minor=quote.debit_minor, version_policy="STRICT")
                except DomainError as error:
                    if error.code == "PAYMENT_INSUFFICIENT_FUNDS":
                        raise DomainError("FX_FUNDS_CHANGED", "当前可支出资金不足，未动用预留或其他账户。", 409) from None
                    if error.code == "PAYMENT_CARD_LIMIT_EXCEEDED":
                        raise DomainError("FX_CARD_LIMIT", "付款卡最新限额不足。", 409) from None
                    raise
            elif side.get("payment_instrument_role") == "RECIPIENT":
                validate_recipient_snapshot(session, instrument)
            else:
                raise DomainError("FX_BINDING_CHANGED", "收付款卡用途绑定缺失。", 409)
    reserved = cash_reserved_minor(session, Principal(source.user_id, source.sandbox_id), source.id) if quote.source_currency == "CNY" else 0
    if source.balance_minor - reserved < quote.debit_minor or target.balance_minor + quote.credit_minor > 2147483647:
        raise DomainError("FX_FUNDS_CHANGED", "可用资金不足、目标预留受影响或到账容量超限", 409)
    if sides[0]["card_id"] and cards[sides[0]["card_id"]].limit_minor < scope["cny_reference_minor"]:
        raise DomainError("FX_CARD_LIMIT", "付款卡最新限额不足", 409)
    return source, target


def fx_operation_integrity(session, operation):
    session.flush()
    entries = list(session.scalars(select(FxLedgerEntry).where(FxLedgerEntry.operation_id == operation.id)))
    statements = list(session.scalars(select(FxStatement).where(FxStatement.operation_id == operation.id)))
    net = {}
    for row in entries:
        key = row.sandbox_id + ":" + row.currency
        net[key] = net.get(key, 0) + row.delta_minor
    expected = [(operation.receiver_sandbox_id or operation.sandbox_id, operation.target_ref, operation.target_currency, operation.credit_minor)]
    if operation.kind != "INITIAL_FUNDING":
        expected.append((operation.sandbox_id, operation.source_ref, operation.source_currency, -operation.debit_minor))
    observed = sorted((row.sandbox_id, row.account_ref, row.currency, row.amount_minor) for row in statements)
    errors = []
    if observed != sorted(expected) or any(value != 0 for value in net.values()):
        errors.append("FX_JOURNAL_OR_STATEMENT_MISMATCH")
    expected_entry_count = 2 if operation.kind == "INITIAL_FUNDING" else 4
    if len(entries) != expected_entry_count:
        errors.append("FX_LEDGER_COUNT_MISMATCH")
    for sid, ref, currency, delta in expected:
        actual = sum(row.delta_minor for row in entries if row.sandbox_id == sid and row.account_ref == ref and row.currency == currency)
        if actual != delta:
            errors.append("FX_REAL_ACCOUNT_DELTA_MISMATCH")
    expected_ledger = []
    for sid, ref, currency, delta in expected:
        expected_ledger.extend([(sid, ref, currency, delta), (sid, f"FX_POOL:{operation.id}:{currency}", currency, -delta)])
    if sorted((row.sandbox_id, row.account_ref, row.currency, row.delta_minor) for row in entries) != sorted(expected_ledger):
        errors.append("FX_LEDGER_SHAPE_MISMATCH")
    for row in statements:
        if row.currency == "CNY":
            mirror = session.scalar(select(Transaction).where(Transaction.sandbox_id == row.sandbox_id, Transaction.operation_ref == row.operation_ref))
            if mirror is None or (mirror.account_id, mirror.source_kind, mirror.amount_minor) != (row.account_ref, "FX", row.amount_minor):
                errors.append("FX_CNY_MIRROR_MISMATCH")
    return {"operation_id": operation.id, "balanced": not errors, "errors": errors,
            "per_currency_sandbox_net_minor": net, "ledger_entry_count": len(entries), "statement_count": len(statements)}


def post_fx_operation(session, quote, original_key, task_id=None):
    if not isinstance(original_key, str) or not 1 <= len(original_key) <= 120:
        raise DomainError("FX_ORIGINAL_KEY_REQUIRED", "需要原始稳定业务键", 400)
    def existing():
        row = session.scalar(select(FxOperation).where(FxOperation.sandbox_id == quote.sandbox_id, FxOperation.original_key == original_key))
        if row and (row.quote_id != quote.id or row.user_id != quote.user_id or row.task_id != task_id):
            raise DomainError("FX_IDEMPOTENCY_CONFLICT", "原键绑定了不同外汇方案", 409)
        if row and not fx_operation_integrity(session, row)["balanced"]:
            raise DomainError("FX_ORIGINAL_OPERATION_INCOMPLETE", "原入账证据不完整，只能查单，不能重复扣款", 409)
        return row
    prior = existing()
    if prior:
        return prior
    from .fx_recipient_selection import validate_fx_recipient_contexts
    validate_fx_recipient_contexts(session, quote)
    _physical_outer_begin(session)
    with session.begin_nested():
        try:
            source, target = lock_quote_resources(session, quote)
        except DomainError as error:
            # A same-key caller can have seen no row before it waited for the
            # other transaction's wallet locks. After that commit, old wallet
            # versions are expected: query the immutable original, never debit.
            if error.code in {"FX_BALANCE_VERSION_CHANGED", "FX_QUOTE_EXPIRED", "FX_ACTOR_OR_ACCOUNT_CHANGED", "FX_CARD_CHANGED"}:
                prior = existing()
                if prior:
                    return prior
            raise
        prior = existing()
        if prior:
            return prior
        from .transfer_risk import check_fx_transfer_limits
        checked_transfer_limits = check_fx_transfer_limits(session, quote)
        scope = quote.scope_json
        target_side = scope["target"]
        op = FxOperation(sandbox_id=quote.sandbox_id, user_id=quote.user_id, task_id=task_id, kind=quote.kind,
            original_key=original_key, quote_id=quote.id, receiver_sandbox_id=target_side["sandbox_id"], receiver_user_id=target_side["user_id"],
            recipient_username=target_side["username"], requested_currency=quote.requested_currency, requested_amount_minor=quote.requested_amount_minor,
            source_currency=quote.source_currency, target_currency=quote.target_currency, source_ref=scope["source"]["account_ref"],
            target_ref=target_side["account_ref"], debit_minor=quote.debit_minor, credit_minor=quote.credit_minor,
            receipt_json={"synthetic": True, "fx_quote": quote_public(quote), "fee_minor": 0, "fee_source": "SIM_POLICY"})
        session.add(op); session.flush()
        for side, delta in ((scope["source"], -quote.debit_minor), (scope["target"], quote.credit_minor)):
            pool = f"FX_POOL:{op.id}:{side['currency']}"
            session.add_all([FxLedgerEntry(operation_id=op.id, sandbox_id=side["sandbox_id"], user_id=side["user_id"],
                account_ref=side["account_ref"], currency=side["currency"], delta_minor=delta),
                FxLedgerEntry(operation_id=op.id, sandbox_id=side["sandbox_id"], user_id=side["user_id"],
                account_ref=pool, currency=side["currency"], delta_minor=-delta)])
        source.balance_minor -= quote.debit_minor; source.version += 1
        target.balance_minor += quote.credit_minor; target.version += 1
        _statement(session, op, source.sandbox_id, source.user_id, scope["source"]["account_ref"], quote.source_currency, -quote.debit_minor, "受控外汇付款/换出")
        _statement(session, op, target.sandbox_id, target.user_id, scope["target"]["account_ref"], quote.target_currency, quote.credit_minor, "受控外汇收款/换入")
        session.flush()
        if not fx_operation_integrity(session, op)["balanced"]:
            raise DomainError("FX_POSTING_INCOMPLETE", "外汇双腿或镜像不完整，整笔不提交", 500)
        from .transfer_risk import record_fx_transfer_limit_posting
        record_fx_transfer_limit_posting(session, op, checked_transfer_limits)
        return op


def inspect_fx(session, sandbox_id, user_id=None):
    session.flush()
    query = select(FxWallet).where(FxWallet.sandbox_id == sandbox_id)
    if user_id:
        query = query.where(FxWallet.user_id == user_id)
    balances, mismatches = [], []
    for wallet in session.scalars(query):
        expected = session.scalar(select(func.coalesce(func.sum(FxLedgerEntry.delta_minor), 0)).where(FxLedgerEntry.sandbox_id == sandbox_id,
            FxLedgerEntry.user_id == wallet.user_id, FxLedgerEntry.currency == "USD", FxLedgerEntry.account_ref == usd_ref(wallet.id)))
        from sqlalchemy import inspect
        central_cash = 0
        if inspect(session.connection()).has_table("payment_ledger_entries"):
            from .credit_models import PaymentLedgerEntry
            central_cash = session.scalar(select(func.coalesce(func.sum(PaymentLedgerEntry.delta_minor), 0)).where(
                PaymentLedgerEntry.sandbox_id == sandbox_id, PaymentLedgerEntry.user_id == wallet.user_id,
                PaymentLedgerEntry.currency == "USD", PaymentLedgerEntry.account_ref == usd_ref(wallet.id)))
        expected += central_cash
        balances.append({"wallet_id": wallet.id, "currency": "USD", "actual_minor": wallet.balance_minor,
            "ledger_minor": expected, "central_payment_cash_minor": central_cash, "balanced": wallet.balance_minor == expected})
        if wallet.balance_minor != expected:
            mismatches.append({"kind": "USD_WALLET_LEDGER_MISMATCH", "wallet_id": wallet.id})
    op_ids = select(FxLedgerEntry.operation_id).where(FxLedgerEntry.sandbox_id == sandbox_id)
    if user_id:
        op_ids = op_ids.where(FxLedgerEntry.user_id == user_id)
    details = [fx_operation_integrity(session, op) for op in session.scalars(select(FxOperation).where(FxOperation.id.in_(op_ids.distinct())))]
    for row in details:
        if not row["balanced"]:
            mismatches.append({"operation_id": row["operation_id"], "errors": row["errors"]})
    local_net = {currency: amount for currency, amount in session.execute(select(FxLedgerEntry.currency, func.sum(FxLedgerEntry.delta_minor))
        .where(FxLedgerEntry.sandbox_id == sandbox_id).group_by(FxLedgerEntry.currency))}
    return {"sandbox_id": sandbox_id, "balances": balances, "per_currency_net_minor": local_net,
            "operations": details, "mismatches": mismatches,
            "balanced": not mismatches and all(value == 0 for value in local_net.values()), "synthetic": True}
