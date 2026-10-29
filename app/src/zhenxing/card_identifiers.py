"""Exact card references with ownership and ambiguity preserved.

Issuer prefixes are simulation branding, never real-bank attribution. Mapping is
separate from Card.last4/version/status, so historical confirmations remain valid.
"""
from __future__ import annotations

import re
import secrets

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .access_models import LoginAccount
from .card_identifier_models import CardIdentifier
from .contracts import DomainError
from .models import Account, Card
from .resource_defaults import ARCHIVED_RESOURCE_STATUS


CARD_NUMBER_PATTERN = r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}"
XIAOZHAO_USERS = frozenset({"chenyue", "zhanghao", "linxinyi"})
MAX_COLLISION_ATTEMPTS = 20


def normalize_card_number(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(CARD_NUMBER_PATTERN, value.strip()) is None:
        raise DomainError("INVALID_SIM_CARD_NUMBER", "请提供完整16字符平台卡号，不能截断、模糊匹配或插入字符", 422)
    return value.strip().lower()


def _prefixes(session):
    # A plain read is intentional: no Card->Login FOR UPDATE lock inversion.
    return {(user_id, sandbox_id): "xiaozhao" if username in XIAOZHAO_USERS else "zhenhang"
            for user_id, sandbox_id, username in session.execute(select(LoginAccount.user_id, LoginAccount.sandbox_id, LoginAccount.username))}


def _valid_mapping(row):
    if re.fullmatch(CARD_NUMBER_PATTERN, row.card_number) is None or row.card_number != row.card_number.lower() \
            or row.card_number[:8] != row.issuer_prefix or len(row.legacy_last4) != 4:
        raise DomainError("CARD_IDENTIFIER_INTEGRITY_FAILED", "持久平台卡号映射格式损坏，未返回猜测号码", 409)
    return row


def _physical_outer_begin(session):
    if session.bind.dialect.name == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")


def _mint_for_locked_card(session, card, prefix):
    existing = session.get(CardIdentifier, card.id)
    if existing:
        return _valid_mapping(existing), False
    for _attempt in range(MAX_COLLISION_ATTEMPTS):
        suffix = secrets.token_hex(4)
        if re.fullmatch(r"[0-9a-f]{8}", suffix) is None:
            raise DomainError("CARD_IDENTIFIER_GENERATOR_INVALID", "平台卡号随机源输出无效", 500)
        candidate = prefix + suffix
        _physical_outer_begin(session)
        try:
            with session.begin_nested():
                result = CardIdentifier(card_id=card.id, card_number=candidate, issuer_prefix=prefix, legacy_last4=card.last4)
                session.add(result)
                session.flush()
            return result, True
        except IntegrityError:
            existing = session.get(CardIdentifier, card.id)
            if existing:
                return _valid_mapping(existing), False
            collided = session.scalar(select(CardIdentifier.card_id).where(CardIdentifier.card_number == candidate))
            if collided is None:
                raise  # FK/schema failure is not a harmless number collision.
    raise DomainError("CARD_IDENTIFIER_COLLISION_LIMIT", "平台卡号碰撞重试耗尽，未改旧卡", 503)


def ensure_card_identifier(session, card: Card) -> CardIdentifier:
    if card.id is None:
        session.flush()
    # Read branding before taking card locks; generated mappings never rebrand.
    existing = session.get(CardIdentifier, card.id)
    if existing:
        return _valid_mapping(existing)
    prefixes = _prefixes(session)
    locked = session.scalar(select(Card).where(Card.id == card.id).with_for_update().execution_options(populate_existing=True))
    if locked is None:
        raise DomainError("CARD_NOT_FOUND", "卡不存在，不能创建映射", 404)
    result, _created = _mint_for_locked_card(session, locked, prefixes.get((locked.user_id, locked.sandbox_id), "zhenhang"))
    return result


def bootstrap_card_identifiers(session, user_id=None):
    prefixes = _prefixes(session)
    query = select(Card)
    if user_id is not None:
        query = query.where(Card.user_id == user_id)
    cards = list(session.scalars(query.order_by(Card.id).with_for_update().execution_options(populate_existing=True)))
    created = 0
    for card in cards:
        _row, is_new = _mint_for_locked_card(session, card, prefixes.get((card.user_id, card.sandbox_id), "zhenhang"))
        created += int(is_new)
    return {"created_count": created, "existing_count": len(cards)-created, "synthetic": True, "old_card_fields_changed": False}


def card_identifier_view(session, card: Card):
    row = session.get(CardIdentifier, card.id)
    if row is None:
        return None
    row = _valid_mapping(row)
    return {"card_number": row.card_number, "display_last4": row.card_number[-4:],
            "legacy_last4": row.legacy_last4, "issuer_prefix": row.issuer_prefix}


def lookup_card_number(session, number, principal=None):
    normalized = normalize_card_number(number)
    query = select(Card).join(CardIdentifier, CardIdentifier.card_id == Card.id).join(Account, Account.id == Card.account_id).where(
        CardIdentifier.card_number == normalized, Account.currency == "CNY", Account.user_id == Card.user_id,
        Account.sandbox_id == Card.sandbox_id, Card.status != ARCHIVED_RESOURCE_STATUS)
    if principal is not None:
        query = query.where(Card.user_id == principal.user_id, Card.sandbox_id == principal.sandbox_id)
    else:
        # Target resolution exposes only active registered business ownership.
        query = query.join(LoginAccount, (LoginAccount.user_id == Card.user_id) & (LoginAccount.sandbox_id == Card.sandbox_id)) \
            .where(LoginAccount.status == "ACTIVE", LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"}), Card.status == "ACTIVE")
    return session.scalar(query)


def resolve_owned_cards(session, sandbox_id, user_id, reference):
    if not isinstance(reference, str):
        raise DomainError("INVALID_CARD_REFERENCE", "请明确本人完整平台卡号或4位尾码", 422)
    reference = reference.strip()
    if re.fullmatch(CARD_NUMBER_PATTERN, reference):
        query = select(Card).join(CardIdentifier, CardIdentifier.card_id == Card.id).where(CardIdentifier.card_number == reference.lower())
    elif re.fullmatch(r"[0-9a-fA-F]{4}", reference):
        suffix = reference.lower()
        query = select(Card).outerjoin(CardIdentifier, CardIdentifier.card_id == Card.id)
        new_match = CardIdentifier.card_number.endswith(suffix)
        # Numeric old tails remain meaningful independently of the new suffix.
        query = query.where(or_(new_match, Card.last4 == reference) if reference.isdigit() else new_match)
    else:
        raise DomainError("INVALID_CARD_REFERENCE", "不接受相似号码、任意截断或非4位尾码", 422)
    return list(session.scalars(query.join(Account, Account.id == Card.account_id).where(Card.sandbox_id == sandbox_id, Card.user_id == user_id,
        Account.user_id == user_id, Account.sandbox_id == sandbox_id, Account.currency == "CNY",
        Card.status != ARCHIVED_RESOURCE_STATUS).order_by(Card.id)).unique())
