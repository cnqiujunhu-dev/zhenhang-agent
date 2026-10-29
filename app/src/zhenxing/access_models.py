from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class LoginAccount(Base):
    __tablename__ = "login_accounts"
    __table_args__ = (CheckConstraint("role IN ('CUSTOMER','DEV_TEST','ADMIN')"),
                      CheckConstraint("status IN ('ACTIVE','DISABLED')"),
                      Index("uq_login_account_dev_test", "role", unique=True,
                            sqlite_where=text("role='DEV_TEST'"), postgresql_where=text("role='DEV_TEST'")))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    profile: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    username_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccessAudit(Base):
    __tablename__ = "access_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_account_id: Mapped[str | None] = mapped_column(ForeignKey("login_accounts.id"), nullable=True)
    target_account_id: Mapped[str | None] = mapped_column(ForeignKey("login_accounts.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioRowVersion(Base):
    __tablename__ = "scenario_row_versions"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "entity", "row_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    entity: Mapped[str] = mapped_column(String(30))
    row_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1)


class ScenarioEditEvent(Base):
    __tablename__ = "scenario_edit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    actor_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"))
    entity: Mapped[str] = mapped_column(String(30))
    row_id: Mapped[str] = mapped_column(String(36))
    previous_version: Mapped[int] = mapped_column(Integer)
    resulting_version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    changes_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioAdjustmentLedger(Base):
    __tablename__ = "scenario_adjustment_ledger"
    __table_args__ = (UniqueConstraint("edit_event_id", "account_ref"), CheckConstraint("delta_minor <> 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    edit_event_id: Mapped[str] = mapped_column(ForeignKey("scenario_edit_events.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    account_ref: Mapped[str] = mapped_column(String(80))
    delta_minor: Mapped[int] = mapped_column(Integer)


ACCESS_TABLES = {model.__tablename__ for model in (
    LoginAccount, LoginThrottle, AccessAudit, ScenarioRowVersion, ScenarioEditEvent, ScenarioAdjustmentLedger,
)}
