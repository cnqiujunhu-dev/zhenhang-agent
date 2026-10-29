"""Additive, repeatable profile migration and registered-project fixture seed.

The integration imports CUSTOMER_PROFILE_TABLES into its schema allowlist and
calls migrate_customer_profile_schema before serving. Nothing here upgrades a
real customer, changes risk, creates cards, or auto-confirms a preference.
"""
from datetime import timedelta

from sqlalchemy import inspect, select, text

from .authentication import as_utc
from .contracts import DomainError
from .database import Base
from .models import Card, Sandbox, User
from .simulation import business_now
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from .customer_profile_models import (
    CUSTOMER_PROFILE_TABLES, CustomerBenefitActivity, CustomerCardTier, CustomerMembership,
)


def migrate_customer_profile_schema(engine):
    tables = [table for table in Base.metadata.sorted_tables if table.name in CUSTOMER_PROFILE_TABLES]
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(1720261017)"))
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        for table in tables:
            if table.name in existing:
                columns = {column["name"] for column in inspector.get_columns(table.name)}
                missing = set(table.columns.keys()) - columns
                if missing:
                    raise RuntimeError(f"Customer profile schema drift: {table.name}: {sorted(missing)}")
        Base.metadata.create_all(connection, tables=tables, checkfirst=True)
    return {"schema_version": "customer-profile-v1", "tables": sorted(CUSTOMER_PROFILE_TABLES),
            "created_tables": sorted(CUSTOMER_PROFILE_TABLES - existing)}


def seed_customer_profile(session, sandbox_id, user_id, *, fixture=True):
    """Trusted project fixtures only; preserve existing assignments and values."""
    sandbox = session.scalar(select(Sandbox).where(Sandbox.id == sandbox_id).with_for_update())
    owner = session.scalar(select(User).where(User.id == user_id, User.sandbox_id == sandbox_id).with_for_update())
    if sandbox is None or owner is None:
        raise DomainError("PROFILE_OWNER_REQUIRED", "客户资料主体不存在。", 404)
    clock = as_utc(business_now(session, sandbox_id))
    membership = session.scalar(select(CustomerMembership).where(
        CustomerMembership.sandbox_id == sandbox_id, CustomerMembership.user_id == user_id))
    if membership is None:
        session.add(CustomerMembership(sandbox_id=sandbox_id, user_id=user_id, tier_code="SILVER" if fixture else "STANDARD",
            source="REGISTERED_PROJECT_PROFILE", effective_from=clock - timedelta(days=1)))
    cards = list(session.scalars(select(Card).where(Card.sandbox_id == sandbox_id,
        Card.user_id == user_id, Card.status != ARCHIVED_RESOURCE_STATUS).order_by(Card.last4, Card.id)))
    for index, card in enumerate(cards):
        if session.scalar(select(CustomerCardTier.id).where(CustomerCardTier.card_id == card.id)) is None:
            session.add(CustomerCardTier(sandbox_id=sandbox_id, user_id=user_id, card_id=card.id,
                tier_code="STANDARD" if index == 0 or not fixture else "GOLD", source="REGISTERED_PROJECT_CARD"))
    activities = (
        ("daily-living", "餐饮主题活动", "查看已登记餐饮主题活动及本人适用条件。",
         ["STANDARD", "SILVER", "GOLD"], ["STANDARD", "GOLD", "PLATINUM"]),
        ("green-travel", "绿色出行活动", "查看出行主题活动，核对客户等级与持卡条件。",
         ["SILVER", "GOLD"], ["GOLD", "PLATINUM"]),
        ("platinum-service", "白金卡专属活动", "查看白金卡活动规则与本人尚待满足的条件。",
         ["GOLD"], ["PLATINUM"]),
    )
    for key, name, description, customer_tiers, card_tiers in activities:
        existing = session.scalar(select(CustomerBenefitActivity.id).where(
            CustomerBenefitActivity.sandbox_id == sandbox_id, CustomerBenefitActivity.activity_key == key))
        if existing is None:
            session.add(CustomerBenefitActivity(sandbox_id=sandbox_id, activity_key=key, name=name,
                description=description, customer_tiers_json=list(customer_tiers), card_tiers_json=list(card_tiers),
                active_card_required=True, starts_at=clock - timedelta(days=1), ends_at=clock + timedelta(days=90)))
    session.flush()


def initialize_customer_profile(session, sandbox_id, user_id):
    """Normal new registration: ordinary customer and existing cards only."""
    return seed_customer_profile(session, sandbox_id, user_id, fixture=False)


def seed_named_customer_profiles(session):
    """Backfill only the explicitly named showcase accounts, never old QA rows."""
    from .accounts import SEED_USERNAMES
    from .demo_family_accounts import FAMILY_USERNAMES
    from .access_models import LoginAccount
    names = {*SEED_USERNAMES, *FAMILY_USERNAMES}
    rows = list(session.scalars(select(LoginAccount).where(LoginAccount.username.in_(names),
        LoginAccount.role != "ADMIN", LoginAccount.status == "ACTIVE").order_by(LoginAccount.username)))
    for account in rows:
        seed_customer_profile(session, account.sandbox_id, account.user_id, fixture=True)
    return {"initialized_usernames": [account.username for account in rows]}
