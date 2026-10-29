from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .database import Base
from .models import Account, Confirmation, Holding, InvestmentLedgerEntry, InvestmentOrder, InvestmentProduct, LedgerEntry, Payee, Transaction, Transfer
from . import runtime_models  # noqa: F401
from . import simulation_models  # noqa: F401
from . import banking_extension_models  # noqa: F401
from . import wealth_models  # noqa: F401
from . import runtime_health_models  # noqa: F401
from . import extensions_models  # noqa: F401
from . import access_models  # noqa: F401
from . import routing_models  # noqa: F401
from . import internal_banking_models  # noqa: F401
from . import fx_models  # noqa: F401
from . import card_identifier_models  # noqa: F401
from . import contact_models  # noqa: F401
from . import conversation_models  # noqa: F401
from . import attachment_models  # noqa: F401
from . import family_account_context_models  # noqa: F401
from . import dialogue_state_models  # noqa: F401
from . import customer_profile_models  # noqa: F401
from . import credit_models  # noqa: F401
from . import occasion_models  # noqa: F401
from . import merchant_models  # noqa: F401
from . import life_plan_models  # noqa: F401
from . import own_account_transfer_models  # noqa: F401
from . import credit_cycle_models  # noqa: F401
from . import family_profile_models  # noqa: F401
from .simulation_models import SimulationState
from .planning import canonical_hash


SCHEMA_VERSION = 23
VERSION_4_TABLES = {
    "runtime_records", "runtime_operations", "runtime_plan_snapshots", "workflow_jobs",
    "auth_challenges", "simulation_states",
    "scheduled_transfers", "aa_collection_requests", "aa_participants", "aa_ledger_entries",
    "risk_assessment_records", "runtime_task_requests",
    "runtime_worker_heartbeats",
    "extension_fraud_labels", "extension_family_members", "extension_family_delegations", "extension_family_proposals",
    "extension_cashflow_goals", "extension_goal_ledger", "extension_carbon_pools", "extension_carbon_wallets",
    "extension_carbon_behaviors", "extension_carbon_credits", "extension_carbon_point_ledger",
    "extension_carbon_rewards", "extension_carbon_redemptions",
}
VERSION_4_TABLES |= access_models.ACCESS_TABLES | {"model_routing_policies"}
VERSION_4_TABLES |= internal_banking_models.INTERNAL_BANKING_TABLES
VERSION_4_TABLES |= fx_models.FX_TABLES
VERSION_4_TABLES |= card_identifier_models.CARD_IDENTIFIER_TABLES | contact_models.CONTACT_TABLES
VERSION_4_TABLES |= {"chat_threads", "chat_turns"} | attachment_models.ATTACHMENT_TABLES
VERSION_4_TABLES |= family_account_context_models.FAMILY_ACCOUNT_CONTEXT_TABLES
VERSION_4_TABLES |= dialogue_state_models.DIALOGUE_STATE_TABLES
VERSION_4_TABLES |= {"subscription_products", "subscription_payments", "subscription_ledger_entries"}
VERSION_4_TABLES |= customer_profile_models.CUSTOMER_PROFILE_TABLES
VERSION_4_TABLES |= credit_models.CREDIT_TABLES | banking_extension_models.BANKING_WORKFLOW_TABLES
VERSION_4_TABLES |= occasion_models.OCCASION_TABLES | merchant_models.MERCHANT_TABLES | life_plan_models.LIFE_PLAN_TABLES
VERSION_4_TABLES |= own_account_transfer_models.MODEL_TABLES
VERSION_4_TABLES |= credit_cycle_models.CREDIT_CYCLE_TABLES | family_profile_models.FAMILY_PROFILE_TABLES


def _apply_version_3(connection, dialect_name: str) -> None:
    if dialect_name != "postgresql":
        return
    constraints = (
        "ALTER TABLE accounts ADD CONSTRAINT ck_accounts_balance_nonnegative CHECK (balance_minor >= 0)",
        "ALTER TABLE transfers ADD CONSTRAINT ck_transfers_amount_positive CHECK (amount_minor > 0)",
        "ALTER TABLE investment_orders ADD CONSTRAINT ck_investment_orders_positive CHECK (amount_minor > 0 AND units > 0)",
        "ALTER TABLE cards ADD CONSTRAINT ck_cards_limit_range CHECK (limit_minor BETWEEN 10000 AND 2000000)",
        "ALTER TABLE ledger_entries ADD CONSTRAINT ck_ledger_entries_delta_nonzero CHECK (delta_minor <> 0)",
        "ALTER TABLE investment_ledger_entries ADD CONSTRAINT ck_investment_ledger_entries_delta_nonzero CHECK (delta_minor <> 0)",
    )
    for statement in constraints:
        connection.execute(text(statement))


def _missing_schema(engine: Engine) -> dict[str, list[str]]:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing[table.name] = ["<table>"]
            continue
        actual_columns = {item["name"] for item in inspector.get_columns(table.name)}
        absent = sorted(set(table.columns.keys()) - actual_columns)
        if absent:
            missing[table.name] = absent
    return missing


def _backup_sqlite(database_path: str, version: int = 2) -> Path:
    source = Path(database_path).resolve()
    backup = source.with_name(
        f"{source.name}.pre-v{version}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}.bak"
    )
    with sqlite3.connect(source) as existing, sqlite3.connect(backup) as destination:
        existing.backup(destination)
    return backup


def _ensure_schema(engine: Engine) -> None:
    """Bootstrap fresh databases and upgrade the original synthetic SQLite demo."""
    preexisting_postgres = False
    if engine.dialect.name == "postgresql":
        existing_tables = set(inspect(engine).get_table_names())
        preexisting_postgres = bool(existing_tables)
        if existing_tables and (missing := _missing_schema(engine)):
            unexplained = {name: fields for name, fields in missing.items()
                           if not (name in VERSION_4_TABLES and fields == ["<table>"])
                           and not (name == "extension_family_delegations" and set(fields) <= {"limit_period"})
                           and not (name == "extension_family_proposals" and set(fields) <= {"business_created_at", "quota_month"})
                           and not (name == "transactions" and set(fields) <= {"source_kind", "operation_ref"})}
            if set(missing.get("subscriptions", [])) <= {"entitlement_end_at", "auto_renew", "billing_months", "last_paid_at", "last_payment_id"}:
                unexplained.pop("subscriptions", None)
            if set(missing.get("chat_turns", [])) <= {"suggestions_json", "response_meta_json"}:
                unexplained.pop("chat_turns", None)
            if unexplained:
                raise RuntimeError(f"PostgreSQL schema drift detected; migration required: {unexplained}")
    if engine.dialect.name == "sqlite":
        tables = set(inspect(engine).get_table_names())
        if tables and (access_models.ACCESS_TABLES | {"model_routing_policies"} | internal_banking_models.INTERNAL_BANKING_TABLES | fx_models.FX_TABLES | card_identifier_models.CARD_IDENTIFIER_TABLES | contact_models.CONTACT_TABLES | {"chat_threads", "chat_turns"} | attachment_models.ATTACHMENT_TABLES | family_account_context_models.FAMILY_ACCOUNT_CONTEXT_TABLES | dialogue_state_models.DIALOGUE_STATE_TABLES | customer_profile_models.CUSTOMER_PROFILE_TABLES | credit_models.CREDIT_TABLES | banking_extension_models.BANKING_WORKFLOW_TABLES | occasion_models.OCCASION_TABLES | merchant_models.MERCHANT_TABLES | life_plan_models.LIFE_PLAN_TABLES | own_account_transfer_models.MODEL_TABLES) - tables \
                and engine.url.database not in {None, ":memory:"}:
            _backup_sqlite(engine.url.database, version=SCHEMA_VERSION)
        if "confirmations" in tables:
            confirmation_columns = {column["name"] for column in inspect(engine).get_columns("confirmations")}
            card_columns = {column["name"] for column in inspect(engine).get_columns("cards")}
            needs_upgrade = "user_id" not in confirmation_columns or "limit_minor" not in card_columns
            if needs_upgrade:
                database_path = engine.url.database
                if database_path is None or database_path == ":memory:":
                    raise RuntimeError("Old in-memory demo schema cannot be migrated safely")
                _backup_sqlite(database_path)
                with engine.begin() as connection:
                    if "limit_minor" not in card_columns:
                        connection.execute(text(
                            "ALTER TABLE cards ADD COLUMN limit_minor INTEGER NOT NULL DEFAULT 1000000"
                        ))
                    if "user_id" not in confirmation_columns:
                        connection.execute(text("""
                            CREATE TABLE confirmations_v2 (
                                id VARCHAR(36) NOT NULL PRIMARY KEY,
                                sandbox_id VARCHAR(36) NOT NULL REFERENCES sandboxes(id),
                                task_id VARCHAR(36) NOT NULL REFERENCES tasks(id),
                                user_id VARCHAR(36) NOT NULL REFERENCES users(id),
                                node_key VARCHAR(80) NOT NULL,
                                action_type VARCHAR(100) NOT NULL,
                                target_id VARCHAR(36),
                                plan_hash VARCHAR(64) NOT NULL,
                                view_hash VARCHAR(64) NOT NULL,
                                status VARCHAR(20) NOT NULL,
                                expires_at DATETIME NOT NULL,
                                consumed_at DATETIME,
                                UNIQUE (task_id, node_key)
                            )
                        """))
                        rows = connection.execute(text("""
                            SELECT c.id, c.sandbox_id, c.task_id, t.user_id, c.card_id,
                                   c.plan_hash, c.status, c.expires_at, c.consumed_at,
                                   card.last4 AS card_last4
                            FROM confirmations c
                            JOIN tasks t ON t.id = c.task_id
                            JOIN cards card ON card.id = c.card_id
                        """)).mappings().all()
                        old_count = connection.scalar(text("SELECT COUNT(*) FROM confirmations"))
                        if old_count != len(rows):
                            raise RuntimeError("Legacy confirmations contain orphan references; migration stopped")
                        for row in rows:
                            connection.execute(text("""
                                INSERT INTO confirmations_v2
                                (id, sandbox_id, task_id, user_id, node_key, action_type,
                                 target_id, plan_hash, view_hash, status, expires_at, consumed_at)
                                VALUES (:id, :sandbox_id, :task_id, :user_id, 'lock-card',
                                        'card.lock.v1', :card_id, :plan_hash, :view_hash,
                                        :status, :expires_at, :consumed_at)
                            """), {**dict(row), "view_hash": canonical_hash({
                                "plan_hash": row["plan_hash"], "card_last4": row["card_last4"],
                                "action": "TEMPORARY_LOCK",
                            })})
                        connection.execute(text("DROP TABLE confirmations"))
                        connection.execute(text("ALTER TABLE confirmations_v2 RENAME TO confirmations"))
                        connection.execute(text(
                            "CREATE INDEX IF NOT EXISTS ix_confirmations_sandbox_id ON confirmations (sandbox_id)"
                        ))

    existing = set(inspect(engine).get_table_names())
    if "subscriptions" in existing:
        present = {item["name"] for item in inspect(engine).get_columns("subscriptions")}
        absent = {"entitlement_end_at", "auto_renew", "billing_months", "last_paid_at", "last_payment_id"} - present
        if absent:
            if engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"}:
                _backup_sqlite(engine.url.database, version=20)
            with engine.begin() as connection:
                timestamp = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
                for name in ("entitlement_end_at", "last_paid_at"):
                    if name in absent:
                        connection.execute(text(f"ALTER TABLE subscriptions ADD COLUMN {name} {timestamp}"))
                if "auto_renew" in absent:
                    column = "BOOLEAN NOT NULL DEFAULT TRUE" if engine.dialect.name=="postgresql" else "BOOLEAN NOT NULL DEFAULT 1"
                    off = "FALSE" if engine.dialect.name=="postgresql" else "0"
                    connection.execute(text(f"ALTER TABLE subscriptions ADD COLUMN auto_renew {column}"))
                    connection.execute(text(f"UPDATE subscriptions SET auto_renew={off} WHERE status<>'ACTIVE'"))
                if "last_payment_id" in absent:
                    connection.execute(text("ALTER TABLE subscriptions ADD COLUMN last_payment_id VARCHAR(36)"))
                if "billing_months" in absent:
                    connection.execute(text("ALTER TABLE subscriptions ADD COLUMN billing_months INTEGER NOT NULL DEFAULT 1"))
            # Existing next_due_at describes a mandate, not proof of paid rights.
            # Preserve it and leave legacy entitlement/payment fields NULL.
    if "extension_family_proposals" in existing:
        present = {item["name"] for item in inspect(engine).get_columns("extension_family_proposals")}
        absent = {"business_created_at", "quota_month"} - present
        if absent:
            if engine.dialect.name == "sqlite" and engine.url.database != ":memory:":
                _backup_sqlite(engine.url.database, version=11)
            with engine.begin() as connection:
                if "business_created_at" in absent:
                    column_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
                    connection.execute(text(f"ALTER TABLE extension_family_proposals ADD COLUMN business_created_at {column_type}"))
                if "quota_month" in absent:
                    connection.execute(text("ALTER TABLE extension_family_proposals ADD COLUMN quota_month VARCHAR(7)"))
            # Old records did not retain an authoritative business month. Keep
            # it NULL rather than fabricate a clock or omit them from quota.
    if "extension_family_delegations" in existing and "limit_period" not in {item["name"] for item in inspect(engine).get_columns("extension_family_delegations")}:
        if engine.dialect.name == "sqlite" and engine.url.database != ":memory:":
            _backup_sqlite(engine.url.database, version=11)
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE extension_family_delegations ADD COLUMN limit_period VARCHAR(20) NOT NULL DEFAULT 'PER_PROPOSAL'"))
    if "transactions" in existing:
        columns = {item["name"] for item in inspect(engine).get_columns("transactions")}
        if {"source_kind", "operation_ref"} - columns:
            if engine.dialect.name == "sqlite" and engine.url.database != ":memory:":
                _backup_sqlite(engine.url.database, version=6)
            with engine.begin() as connection:
                if "source_kind" not in columns:
                    connection.execute(text("ALTER TABLE transactions ADD COLUMN source_kind VARCHAR(30) NOT NULL DEFAULT 'HISTORY'"))
                if "operation_ref" not in columns:
                    connection.execute(text("ALTER TABLE transactions ADD COLUMN operation_ref VARCHAR(120)"))
    if "chat_turns" in existing:
        chat_columns = {item["name"] for item in inspect(engine).get_columns("chat_turns")}
        absent_chat = {"suggestions_json", "response_meta_json"} - chat_columns
        if absent_chat:
            if engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"}:
                _backup_sqlite(engine.url.database, version=17)
            with engine.begin() as connection:
                if "suggestions_json" in absent_chat:
                    connection.execute(text("ALTER TABLE chat_turns ADD COLUMN suggestions_json JSON NOT NULL DEFAULT '[]'"))
                if "response_meta_json" in absent_chat:
                    connection.execute(text("ALTER TABLE chat_turns ADD COLUMN response_meta_json JSON NOT NULL DEFAULT '{}'"))
    from .family_profile_schema import migrate_family_profile_members
    migrate_family_profile_members(engine,backup=_backup_sqlite)
    Base.metadata.create_all(engine)
    missing = _missing_schema(engine)
    if missing:
        raise RuntimeError(f"Database schema drift detected: {missing}")
    with engine.begin() as connection:
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_cash_operation_ref ON transactions(sandbox_id, operation_ref) WHERE operation_ref IS NOT NULL"))
        # Adopt existing synthetic sandboxes without resetting their business data.
        accounts = connection.execute(select(Account.id, Account.sandbox_id, Account.balance_minor)
            .outerjoin(SimulationState, SimulationState.sandbox_id == Account.sandbox_id)
            .where(SimulationState.id.is_(None))).all()
        adoption_opening = {}
        for account_id, sandbox_id, balance in accounts:
            history = connection.scalar(select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.account_id == account_id, Transaction.source_kind == "HISTORY"))
            transfers = connection.scalar(select(func.coalesce(func.sum(LedgerEntry.delta_minor), 0)).where(
                LedgerEntry.account_ref == account_id))
            investments = connection.scalar(select(func.coalesce(func.sum(InvestmentLedgerEntry.delta_minor), 0)).where(
                InvestmentLedgerEntry.account_ref == account_id))
            payments = connection.scalar(select(func.coalesce(func.sum(credit_models.PaymentLedgerEntry.delta_minor), 0)).where(
                credit_models.PaymentLedgerEntry.account_ref == account_id,
                credit_models.PaymentLedgerEntry.currency == "CNY"))
            from .payment_reconciliation import accounting_integer
            payments = accounting_integer(payments)
            adoption_opening[sandbox_id] = adoption_opening.get(sandbox_id, 0) + balance-history-transfers-investments-payments
        # A simulation clock is sandbox-scoped, not one per newly independent
        # debit Account. Adoption totals all cash without duplicating the clock.
        for sandbox_id, opening in adoption_opening.items():
            connection.execute(SimulationState.__table__.insert().values(
                id=str(uuid4()), sandbox_id=sandbox_id, profile="baseline", seed_version="legacy-adopted-v1",
                seed=0, business_time=datetime(2026, 9, 30, 4, tzinfo=timezone.utc),
                opening_balance_minor=opening, fault_mode="NONE"))
        # Earlier releases wrote financial ledgers before statement mirrors existed.
        # Adopt those immutable postings without touching balances or original ledgers.
        posted = connection.execute(select(Transfer.id, Transfer.sandbox_id, Transfer.source_account_id,
            Transfer.amount_minor, Transfer.created_at, Payee.display_name)
            .join(Payee, Payee.id == Transfer.payee_id).where(Transfer.status == "POSTED")).all()
        for operation_id, sandbox_id, account_id, amount, occurred_at, name in posted:
            ref = f"transfer:{operation_id}"
            if connection.scalar(select(Transaction.id).where(Transaction.sandbox_id == sandbox_id,
                                                                Transaction.operation_ref == ref)):
                continue
            connection.execute(Transaction.__table__.insert().values(id=str(uuid4()), sandbox_id=sandbox_id,
                account_id=account_id, card_id=None, occurred_at=occurred_at, amount_minor=-amount,
                merchant=f"转账至{name}"[:200], category="转账", flag="NORMAL", source_kind="TRANSFER", operation_ref=ref))
        orders = connection.execute(select(InvestmentOrder.id, InvestmentOrder.sandbox_id, InvestmentOrder.account_id,
            InvestmentOrder.amount_minor, InvestmentOrder.order_type, InvestmentOrder.created_at, InvestmentProduct.name)
            .join(InvestmentProduct, InvestmentProduct.id == InvestmentOrder.product_id)
            .where(InvestmentOrder.status == "POSTED")).all()
        for operation_id, sandbox_id, account_id, amount, direction, occurred_at, name in orders:
            ref = f"investment:{operation_id}"
            if connection.scalar(select(Transaction.id).where(Transaction.sandbox_id == sandbox_id,
                                                                Transaction.operation_ref == ref)):
                continue
            connection.execute(Transaction.__table__.insert().values(id=str(uuid4()), sandbox_id=sandbox_id,
                account_id=account_id, card_id=None, occurred_at=occurred_at,
                amount_minor=-amount if direction == "BUY" else amount, merchant=name[:200],
                category="投资交易", flag="NORMAL", source_kind="INVESTMENT", operation_ref=ref))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, applied_at VARCHAR(40) NOT NULL
            )
        """))
        current = connection.scalar(text("SELECT MAX(version) FROM schema_migrations"))
        if preexisting_postgres and current is None:
            raise RuntimeError("Existing PostgreSQL schema has no migration history")
        if current is not None and current not in {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, SCHEMA_VERSION}:
            raise RuntimeError(f"Unsupported database schema version: {current}")
        if current != SCHEMA_VERSION:
            if current is None or current < 3:
                _apply_version_3(connection, engine.dialect.name)
            connection.execute(text(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (:version, :applied_at)"
            ), {"version": SCHEMA_VERSION, "applied_at": datetime.now(timezone.utc).isoformat()})
        from .extensions import ensure_extension_fixtures
        from .extensions_models import CarbonWallet
        owners = list(connection.execute(select(Account.sandbox_id, Account.user_id).distinct().outerjoin(
            CarbonWallet, (CarbonWallet.sandbox_id == Account.sandbox_id) & (CarbonWallet.user_id == Account.user_id))
            .where(CarbonWallet.id.is_(None))))
        with Session(bind=connection) as migration_session:
            from .subscription_commerce import ensure_subscription_catalog
            ensure_subscription_catalog(migration_session)
            for sandbox_id, user_id in owners:
                ensure_extension_fixtures(migration_session, sandbox_id, user_id)
            migration_session.flush()


def ensure_schema(engine: Engine) -> None:
    from .asset_schema import ensure_asset_schema
    if engine.dialect.name != "postgresql":
        _ensure_schema(engine)
        ensure_asset_schema(engine)
        return
    # API and Worker can start together; a session advisory lock serializes migration.
    with engine.connect() as guard:
        guard.execute(text("SELECT pg_advisory_lock(96300930)"))
        guard.commit()
        try:
            _ensure_schema(engine)
            ensure_asset_schema(engine)
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(96300930)"))
            guard.commit()
