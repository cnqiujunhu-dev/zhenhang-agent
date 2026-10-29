from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class ScopedScheduledTransfer(Base):
    __tablename__ = "scheduled_transfers"
    __table_args__ = (UniqueConstraint("task_id"), CheckConstraint("amount_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    confirmation_id: Mapped[str] = mapped_column(ForeignKey("confirmations.id"))
    source_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"))
    amount_minor: Mapped[int] = mapped_column(Integer)
    memo: Mapped[str] = mapped_column(String(160), default="")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    business_key: Mapped[str] = mapped_column(String(120), unique=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    scope_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="WAITING", index=True)
    transfer_id: Mapped[str | None] = mapped_column(ForeignKey("transfers.id"), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AACollectionRequest(Base):
    __tablename__ = "aa_collection_requests"
    __table_args__ = (UniqueConstraint("task_id"), CheckConstraint("total_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    total_minor: Mapped[int] = mapped_column(Integer)
    participant_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="COLLECTING")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AAParticipant(Base):
    __tablename__ = "aa_participants"
    __table_args__ = (UniqueConstraint("request_id", "payee_id"), CheckConstraint("amount_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("aa_collection_requests.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"))
    display_name: Mapped[str] = mapped_column(String(80))
    ordinal: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    payment_key: Mapped[str] = mapped_column(String(120), unique=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AALedgerEntry(Base):
    __tablename__ = "aa_ledger_entries"
    __table_args__ = (UniqueConstraint("participant_id", "account_ref"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("aa_collection_requests.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("aa_participants.id"), index=True)
    account_ref: Mapped[str] = mapped_column(String(80))
    delta_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TransferDailyUsage(Base):
    """One owner/currency/day bucket, even when cards use different accounts."""
    __tablename__ = "transfer_daily_usage"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "business_date", "currency"),
                      CheckConstraint("posted_minor >= 0"), CheckConstraint("posted_count >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    business_date: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    posted_minor: Mapped[int] = mapped_column(Integer, default=0)
    posted_count: Mapped[int] = mapped_column(Integer, default=0)


class TransferRiskPosting(Base):
    __tablename__ = "transfer_risk_postings"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "business_key"), UniqueConstraint("transfer_id"),
                      CheckConstraint("amount_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.id"))
    business_key: Mapped[str] = mapped_column(String(120))
    business_date: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    amount_minor: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TransferBlacklist(Base):
    __tablename__ = "transfer_blacklist"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "target_ref"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    target_ref: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="BLOCKED")
    reason_code: Mapped[str] = mapped_column(String(80), default="RECIPIENT_BLACKLISTED")
    version: Mapped[int] = mapped_column(Integer, default=1)


class FxTransferRiskPosting(Base):
    __tablename__ = "fx_transfer_risk_postings"
    __table_args__ = (UniqueConstraint("fx_operation_id"), UniqueConstraint("sandbox_id", "user_id", "business_key"),
                      CheckConstraint("base_amount_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    fx_operation_id: Mapped[str] = mapped_column(ForeignKey("fx_operations.id"))
    business_key: Mapped[str] = mapped_column(String(120))
    business_date: Mapped[str] = mapped_column(String(10))
    base_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    base_amount_minor: Mapped[int] = mapped_column(Integer)
    quote_scope_hash: Mapped[str] = mapped_column(String(64))


class ScheduledTransferSeries(Base):
    __tablename__ = "scheduled_transfer_series"
    __table_args__ = (UniqueConstraint("task_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    schedule_id: Mapped[str] = mapped_column(ForeignKey("scheduled_transfers.id"), unique=True)
    scope_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduledTransferOccurrence(Base):
    __tablename__ = "scheduled_transfer_occurrences"
    __table_args__ = (UniqueConstraint("series_id", "ordinal"), UniqueConstraint("business_key"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    series_id: Mapped[str] = mapped_column(ForeignKey("scheduled_transfer_series.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    business_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    transfer_id: Mapped[str | None] = mapped_column(ForeignKey("transfers.id"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AARequestTerms(Base):
    __tablename__ = "aa_request_terms"
    request_id: Mapped[str] = mapped_column(ForeignKey("aa_collection_requests.id"), primary_key=True)
    collector_scope_json: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)


class AAParticipantIdentity(Base):
    __tablename__ = "aa_participant_identities"
    participant_id: Mapped[str] = mapped_column(ForeignKey("aa_participants.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("aa_collection_requests.id"), index=True)
    login_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"))
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    version: Mapped[int] = mapped_column(Integer, default=1)
    payment_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    payment_business_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    transfer_id: Mapped[str | None] = mapped_column(ForeignKey("transfers.id"), nullable=True)


BANKING_WORKFLOW_TABLES = {"transfer_daily_usage", "transfer_risk_postings", "transfer_blacklist",
                          "scheduled_transfer_series", "scheduled_transfer_occurrences",
                          "aa_request_terms", "aa_participant_identities", "fx_transfer_risk_postings"}

# Explicit metadata dependencies: standalone Base.create_all users must not
# depend on an unrelated test having imported the full application schema.
from . import fx_models as _fx_models  # noqa: E402,F401
from . import access_models as _access_models  # noqa: E402,F401
