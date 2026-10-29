"""Simulation-only USD wallets, fixed quotes and currency-separated journals."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class FxWallet(Base):
    __tablename__ = "fx_wallets"
    __table_args__ = (UniqueConstraint("user_id", "currency"), CheckConstraint("currency='USD' AND balance_minor>=0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DualCurrencyProfile(Base):
    __tablename__ = "dual_currency_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), unique=True)
    usd_wallet_id: Mapped[str] = mapped_column(ForeignKey("fx_wallets.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FxQuote(Base):
    __tablename__ = "fx_quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    requested_currency: Mapped[str] = mapped_column(String(3))
    requested_amount_minor: Mapped[int] = mapped_column(BigInteger)
    source_currency: Mapped[str] = mapped_column(String(3))
    target_currency: Mapped[str] = mapped_column(String(3))
    debit_minor: Mapped[int] = mapped_column(BigInteger)
    credit_minor: Mapped[int] = mapped_column(BigInteger)
    market_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    market_date: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str] = mapped_column(String(180))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    fee_source: Mapped[str] = mapped_column(String(30), default="SIM_POLICY")
    scope_json: Mapped[dict] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FxOperation(Base):
    __tablename__ = "fx_operations"
    __table_args__ = (UniqueConstraint("sandbox_id", "original_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20))
    original_key: Mapped[str] = mapped_column(String(120))
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("fx_quotes.id"), nullable=True)
    receiver_sandbox_id: Mapped[str | None] = mapped_column(ForeignKey("sandboxes.id"), nullable=True, index=True)
    receiver_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    recipient_username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_currency: Mapped[str] = mapped_column(String(3))
    requested_amount_minor: Mapped[int] = mapped_column(BigInteger)
    source_currency: Mapped[str] = mapped_column(String(3))
    target_currency: Mapped[str] = mapped_column(String(3))
    source_ref: Mapped[str] = mapped_column(String(80))
    target_ref: Mapped[str] = mapped_column(String(80))
    debit_minor: Mapped[int] = mapped_column(BigInteger)
    credit_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="POSTED")
    receipt_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FxLedgerEntry(Base):
    __tablename__ = "fx_ledger_entries"
    __table_args__ = (UniqueConstraint("operation_id", "sandbox_id", "currency", "account_ref"), CheckConstraint("delta_minor<>0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_id: Mapped[str] = mapped_column(ForeignKey("fx_operations.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_ref: Mapped[str] = mapped_column(String(80), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    delta_minor: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FxStatement(Base):
    __tablename__ = "fx_statements"
    __table_args__ = (UniqueConstraint("sandbox_id", "operation_ref"), CheckConstraint("amount_minor<>0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_id: Mapped[str] = mapped_column(ForeignKey("fx_operations.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_ref: Mapped[str] = mapped_column(String(80), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(12))
    merchant: Mapped[str] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operation_ref: Mapped[str] = mapped_column(String(120))


FX_TABLES = {model.__tablename__ for model in (FxWallet, DualCurrencyProfile, FxQuote, FxOperation, FxLedgerEntry, FxStatement)}
