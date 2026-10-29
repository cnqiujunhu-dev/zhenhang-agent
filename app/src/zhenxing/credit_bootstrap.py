"""Explicit, idempotent payment upgrade. Never invoked by a read or schema DDL.

Caller owns backup, rollout scope and transaction. Existing cash is split, not
seeded a second time; USD and historical transactions are never rewritten.
"""
from copy import deepcopy
import secrets

from sqlalchemy import select

from .contracts import DomainError
from .credit_models import (CardAccountBindingHistory, CreditAccount, CreditCard, CreditCurrencyBalance,
    CustomerPaymentDefaults, PaymentInstrumentBinding, PaymentUpgradeRecord)
from .models import Account, Card, new_id
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from .credit_atomic import atomic_domain


UPGRADE_REVISION = "independent-debit-dual-credit-v1"
DEFAULT_CREDIT_LIMIT_MINOR = 3_000_000
CREDIT_POLICY = {"policy_version": "sim-credit-v1", "base_currency": "CNY",
    "supported_currencies": ["CNY", "USD"], "minimum_payment_bps": 1000,
    "minimum_payment_floor_minor": {"CNY": 1000, "USD": 100},
    "installment_periods": [3, 6, 12], "installment_total_fee_bps": 120,
    "installment_fee_policy_version": "sim-installment-total-fee-v1", "installment_fee_refund_policy": "RETAIN_DISCLOSED_ORIGINATION_FEE",
    "installment_fee_fx_basis": "ORIGINAL_LOCKED_SETTLEMENT_EXPOSURE_RATIO",
    "interest_bps": 0, "statement_due_days": 20,
    "existing_authorization_capture_policy": "HONOR_UNEXPIRED_ORIGINAL_AMOUNT",
    "authority": "EXPLICIT_SIMULATED_POLICY_NOT_CREDIT_SCORING"}


def _number(session, *, sandbox_id, user_id):
    # Same public namespace as legacy identifiers, separate registered union.
    from .card_identifier_models import CardIdentifier
    from .card_identifiers import XIAOZHAO_USERS
    from .access_models import LoginAccount
    username = session.scalar(select(LoginAccount.username).where(
        LoginAccount.user_id == user_id, LoginAccount.sandbox_id == sandbox_id))
    prefix = 'xiaozhao' if username in XIAOZHAO_USERS else 'zhenhang'
    for _ in range(20):
        number = prefix + secrets.token_hex(4)
        if session.scalar(select(CreditCard.id).where(CreditCard.card_number == number)) is None \
                and session.scalar(select(CardIdentifier.card_id).where(CardIdentifier.card_number == number)) is None:
            return number
    raise DomainError("CREDIT_CARD_NUMBER_COLLISION", "未能生成唯一平台卡号，请重试。", 503)


def issue_credit_card(session, account, *, status="ISSUED", application_id=None):
    card = CreditCard(sandbox_id=account.sandbox_id, user_id=account.user_id, credit_account_id=account.id,
        card_number=_number(session,sandbox_id=account.sandbox_id,user_id=account.user_id), status=status, application_id=application_id)
    session.add(card); session.flush()
    session.add(PaymentInstrumentBinding(instrument_id="credit:" + card.id, sandbox_id=account.sandbox_id,
        user_id=account.user_id, instrument_type="CREDIT", card_id=card.id, card_number=card.card_number,
        credit_account_id=account.id))
    session.flush()
    return card


@atomic_domain
def initialize_customer_payment_services(session, sandbox_id, user_id, *, initial_credit_limit_minor=DEFAULT_CREDIT_LIMIT_MINOR,
        adult_eligible=False, eligibility_source=None):
    """Explicit registration/rollout entry; no commit and no broad user scan."""
    from .access_models import LoginAccount
    from .models import User
    from .payment_instruments import _journal, cash_reserved_minor
    from .planning import canonical_hash
    from .services import Principal
    # User lock serializes both first-time migration and replay in PostgreSQL.
    owner = session.scalar(select(User).where(User.id == user_id, User.sandbox_id == sandbox_id).with_for_update())
    login = session.scalar(select(LoginAccount).where(LoginAccount.user_id == user_id,
        LoginAccount.sandbox_id == sandbox_id, LoginAccount.status == "ACTIVE"))
    if owner is None or login is None or login.role not in {"CUSTOMER", "DEV_TEST"}:
        raise DomainError("PAYMENT_UPGRADE_BUSINESS_CUSTOMER_REQUIRED", "只为明确的本人业务客户初始化付款服务。", 403)
    prior = session.scalar(select(PaymentUpgradeRecord).where(PaymentUpgradeRecord.sandbox_id == sandbox_id,
        PaymentUpgradeRecord.user_id == user_id, PaymentUpgradeRecord.revision == UPGRADE_REVISION))
    if prior:
        return {**deepcopy(prior.result_json), "replayed": True}
    if type(initial_credit_limit_minor) is not int or not 10_000 <= initial_credit_limit_minor <= 10_000_000:
        raise DomainError("CREDIT_INITIAL_POLICY_INVALID", "样例额度政策不在允许范围内。", 422)
    if type(adult_eligible) is not bool or adult_eligible and (not isinstance(eligibility_source, str) or not 1 <= len(eligibility_source) <= 120):
        raise DomainError("CREDIT_ADULT_ELIGIBILITY_REQUIRED", "信用卡初始化需要服务端登记的成年样例资格来源。", 422)
    from .models import Task
    in_flight = session.scalar(select(Task.id).where(Task.sandbox_id == sandbox_id, Task.user_id == user_id,
        Task.state.in_({"EXECUTING", "UNKNOWN"}), Task.intent_type.in_({"TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST",
        "FOREIGN_EXCHANGE", "FOREX_TRANSFER", "WEALTH", "SUBSCRIPTION_MANAGEMENT", "CARD_MANAGEMENT", "GOAL_CASHFLOW"})))
    if in_flight:
        raise DomainError("PAYMENT_UPGRADE_UNRESOLVED_EFFECT", "当前有执行中或结果未明的资金/卡片动作，需要先核实后再迁移。", 409)
    accounts = list(session.scalars(select(Account).where(Account.sandbox_id == sandbox_id,
        Account.user_id == user_id).order_by(Account.id).with_for_update()))
    cards = list(session.scalars(select(Card).where(Card.sandbox_id == sandbox_id, Card.user_id == user_id,
        Card.status != ARCHIVED_RESOURCE_STATUS).order_by(Card.id).with_for_update()))
    if len(cards) != 2 or len(accounts) != 1 or any(card.account_id != accounts[0].id for card in cards) \
            or accounts[0].currency != "CNY" or accounts[0].balance_minor < 0:
        raise DomainError("PAYMENT_UPGRADE_REQUIRES_REVIEW", "现有账户结构不是可自动拆分的双借记卡单账户结构。", 409)
    # Legacy primary has stable seed tail4321. Other real identifiers retain
    # deterministic original card order, recorded in immutable migration data.
    primary = next((card for card in cards if card.last4 == "4321"), cards[0])
    secondary = next(card for card in cards if card.id != primary.id)
    original = accounts[0]; before = original.balance_minor
    secondary_cash = before * 30 // 100
    principal = Principal(user_id, sandbox_id)
    if before - secondary_cash < cash_reserved_minor(session, principal, original.id):
        raise DomainError("PAYMENT_UPGRADE_RESERVED_CASH", "主账户预留资金需要先核对，未自动划分。", 409)
    record = PaymentUpgradeRecord(sandbox_id=sandbox_id, user_id=user_id, revision=UPGRADE_REVISION,
        before_json={"account_id": original.id, "balance_minor": before, "currency": "CNY",
            "primary_card_id": primary.id, "secondary_card_id": secondary.id}, result_json={})
    session.add(record); session.flush()
    second = Account(sandbox_id=sandbox_id, user_id=user_id, name="副卡独立资金账户", balance_minor=secondary_cash, currency="CNY")
    session.add(second); session.flush()
    original.balance_minor -= secondary_cash; original.version += 1
    secondary.account_id = second.id; secondary.version += 1
    session.add(CardAccountBindingHistory(sandbox_id=sandbox_id, user_id=user_id, card_id=secondary.id,
        previous_account_id=original.id, current_account_id=second.id, migration_id=record.id))
    from .card_identifiers import card_identifier_view, ensure_card_identifier
    for card in (primary, secondary):
        ensure_card_identifier(session, card)
        identity = card_identifier_view(session, card)
        if not identity or not identity.get("card_number"):
            raise DomainError("PAYMENT_UPGRADE_CARD_ID_REQUIRED", "卡号未完成登记，不能迁移付款绑定。", 409)
        session.add(PaymentInstrumentBinding(instrument_id="debit:" + card.id, sandbox_id=sandbox_id,
            user_id=user_id, instrument_type="DEBIT", card_id=card.id, card_number=identity["card_number"], account_id=card.account_id))
    session.add(CustomerPaymentDefaults(sandbox_id=sandbox_id, user_id=user_id,
        source_instrument_id="debit:" + primary.id, recipient_instrument_id="debit:" + primary.id))
    facility, credit_card = None, None
    if adult_eligible:
        facility, credit_card = initialize_adult_credit_sample(session, sandbox_id, user_id,
            eligibility_source=eligibility_source, initial_credit_limit_minor=initial_credit_limit_minor)
    journal = _journal(session, principal, operation_key="upgrade:" + record.id,
        request_hash=canonical_hash(record.before_json), kind="INTERNAL_ACCOUNT_SPLIT",
        entries=[(original.id, "CNY", -secondary_cash), (second.id, "CNY", secondary_cash)])
    result = {"schema_version": "payment-upgrade-v1", "migration_id": record.id, "journal_id": journal.id,
        "revision": UPGRADE_REVISION, "before_total_minor": before, "after_total_minor": before, "currency": "CNY",
        "primary_account_id": original.id, "secondary_account_id": second.id,
        "primary_balance_minor": original.balance_minor, "secondary_balance_minor": second.balance_minor,
        "debit_card_ids": [primary.id, secondary.id], "credit_card_ids": [credit_card.id] if credit_card else [],
        "credit_account_id": facility.id if facility else None,
        "base_credit_limit_minor": initial_credit_limit_minor if facility else None,
        "adult_eligible": adult_eligible, "eligibility_source": eligibility_source,
        "credit_is_cash": False, "ledger_balanced": True, "replayed": False}
    from .payment_reconciliation import cash_account_book_totals
    cash_books = cash_account_book_totals(session, sandbox_id, [original.id, second.id])
    result["cash_account_baselines"] = {account.id: {"balance_minor": account.balance_minor, **cash_books[account.id]}
        for account in (original, second)}
    record.result_json = result; journal.result_json = result; session.flush()
    return result


upgrade_customer_payment_accounts = initialize_customer_payment_services


@atomic_domain
def initialize_adult_credit_sample(session, sandbox_id, user_id, *, eligibility_source,
        initial_credit_limit_minor=DEFAULT_CREDIT_LIMIT_MINOR, initial_card_status="ACTIVE", application_id=None):
    """Privileged registration/seed adapter, never an LLM/user-request slot."""
    from .access_models import LoginAccount
    from .models import User
    owner = session.scalar(select(User).where(User.id == user_id, User.sandbox_id == sandbox_id).with_for_update())
    login = session.scalar(select(LoginAccount).where(LoginAccount.user_id == user_id, LoginAccount.sandbox_id == sandbox_id,
        LoginAccount.status == "ACTIVE", LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"})))
    if not owner or not login or not isinstance(eligibility_source, str) or not 1 <= len(eligibility_source) <= 120:
        raise DomainError("CREDIT_ADULT_ELIGIBILITY_REQUIRED", "需要本人业务身份与服务端登记的成年样例资格来源。", 403)
    if type(initial_credit_limit_minor) is not int or not 10_000 <= initial_credit_limit_minor <= 10_000_000:
        raise DomainError("CREDIT_INITIAL_POLICY_INVALID", "样例额度政策不在允许范围内。", 422)
    facility = session.scalar(select(CreditAccount).where(CreditAccount.user_id == user_id,
        CreditAccount.sandbox_id == sandbox_id, CreditAccount.facility_key == "PRIMARY"))
    if facility:
        card = session.scalar(select(CreditCard).where(CreditCard.credit_account_id == facility.id).order_by(CreditCard.created_at, CreditCard.id))
        return facility, card
    facility = CreditAccount(sandbox_id=sandbox_id, user_id=user_id, base_credit_limit_minor=initial_credit_limit_minor,
        policy_json={**deepcopy(CREDIT_POLICY), "adult_eligible": True, "eligibility_source": eligibility_source})
    session.add(facility); session.flush()
    for currency in ("CNY", "USD"):
        session.add(CreditCurrencyBalance(credit_account_id=facility.id, currency=currency))
    if initial_card_status not in {"ACTIVE", "ISSUED"}:
        raise DomainError("CREDIT_INITIAL_POLICY_INVALID", "信用卡初始化状态无效。", 422)
    card = issue_credit_card(session, facility, status=initial_card_status, application_id=application_id)
    return facility, card


@atomic_domain
def seed_payment_upgrades_for_usernames(session, usernames, *, adult_usernames=(), eligibility_source="REGISTERED_ADULT_SAMPLE_POLICY"):
    """Explicit rollout allowlist only; never silently includes QA or ADMIN."""
    from .access_models import LoginAccount
    if not isinstance(usernames, (list, tuple, set, frozenset)) or not all(isinstance(name, str) and name for name in usernames):
        raise DomainError("PAYMENT_SEED_SCOPE_REQUIRED", "升级名单必须明确列出业务账号。", 422)
    names = sorted(set(usernames)); adults = set(adult_usernames)
    if not adults.issubset(names): raise DomainError("PAYMENT_SEED_SCOPE_REQUIRED", "成年样例名单必须在本次升级范围内。", 422)
    results = []
    for name in names:
        login = session.scalar(select(LoginAccount).where(LoginAccount.username == name, LoginAccount.status == "ACTIVE",
            LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"})))
        if login is None: raise DomainError("PAYMENT_SEED_BUSINESS_ACCOUNT_REQUIRED", f"升级名单中账号{name}不是有效业务客户。", 409)
        result = initialize_customer_payment_services(session, login.sandbox_id, login.user_id,
            adult_eligible=name in adults, eligibility_source=eligibility_source if name in adults else None)
        if name in adults and result.get("credit_account_id") is None:
            facility, card = initialize_adult_credit_sample(session, login.sandbox_id, login.user_id,
                eligibility_source=eligibility_source)
            result = {**result, "credit_account_id": facility.id, "credit_card_ids": [card.id],
                "adult_eligible": True, "eligibility_source": eligibility_source, "adult_credit_initialized_after_split": True}
        results.append({"username": name, **result})
    return {"revision": UPGRADE_REVISION, "results": results, "count": len(results)}
