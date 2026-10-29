"""Bounded long-lived plans, independent of short-lived dialogue tasks."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class LifePlan(Base):
    __tablename__ = "life_plans"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "request_key"),
        CheckConstraint("budget_minor > 0 AND reserved_minor >= 0 AND spent_minor >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    occasion_id: Mapped[str] = mapped_column(ForeignKey("family_occasions.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(100))
    request_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    budget_minor: Mapped[int] = mapped_column(Integer)
    reserved_minor: Mapped[int] = mapped_column(Integer, default=0)
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    spent_minor: Mapped[int] = mapped_column(Integer, default=0)
    scope_json: Mapped[dict] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    pause_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LifePlanMandate(Base):
    __tablename__ = "life_plan_mandates"
    __table_args__ = (UniqueConstraint("plan_id", "plan_version"), UniqueConstraint("confirmation_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("life_plans.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    scope_hash: Mapped[str] = mapped_column(String(64))
    scope_json: Mapped[dict] = mapped_column(JSON)
    confirmation_id: Mapped[str] = mapped_column(ForeignKey("confirmations.id"))
    auth_challenge_id: Mapped[str] = mapped_column(ForeignKey("auth_challenges.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LifePlanOccurrence(Base):
    __tablename__ = "life_plan_occurrences"
    __table_args__ = (UniqueConstraint("plan_id", "occurrence_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("life_plans.id"), index=True)
    occurrence_key: Mapped[str] = mapped_column(String(20))
    mandate_id: Mapped[str] = mapped_column(ForeignKey("life_plan_mandates.id"))
    reserve_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    order_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="WAITING_RESERVE")
    version: Mapped[int] = mapped_column(Integer, default=1)


class LifePlanJob(Base):
    __tablename__ = "life_plan_jobs"
    __table_args__ = (UniqueConstraint("dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("life_plan_occurrences.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    item_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    dedupe_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


LIFE_PLAN_TABLES = {LifePlan.__tablename__, LifePlanMandate.__tablename__, LifePlanOccurrence.__tablename__,
    LifePlanJob.__tablename__}
