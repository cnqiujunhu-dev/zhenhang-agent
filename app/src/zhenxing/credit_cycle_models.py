"""Additive durable cursors for calendar billing and authorization maintenance."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class CreditCycleState(Base):
    __tablename__ = "credit_cycle_states"
    __table_args__ = (
        UniqueConstraint("credit_account_id", "currency"),
        CheckConstraint("currency IN ('CNY','USD') AND billing_failure_count>=0 AND expiry_failure_count>=0"),
        CheckConstraint("next_period_start<next_period_end"),
        Index("ix_credit_cycle_billing_due", "next_billing_attempt_at", "next_period_end"),
        Index("ix_credit_cycle_expiry_due", "next_expiry_attempt_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    next_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_json: Mapped[dict] = mapped_column(JSON)
    next_billing_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_expiry_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    billing_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    expiry_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_billing_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_expiry_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_billing_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_expiry_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


CREDIT_CYCLE_TABLES = {CreditCycleState.__tablename__}
