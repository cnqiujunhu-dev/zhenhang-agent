"""Independent customer accounts bound to narrowly scoped family proxy actors."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class FamilyAccountLink(Base):
    __tablename__ = "family_account_links"
    __table_args__ = (
        UniqueConstraint("issuer_account_id", "parent_account_id"),
        UniqueConstraint("helper_user_id"),
        UniqueConstraint("member_id"),
        CheckConstraint("status IN ('ACTIVE','DISABLED')"),
        CheckConstraint("version > 0"),
        CheckConstraint("issuer_account_id <> parent_account_id"),
        CheckConstraint("issuer_sandbox_id <> parent_sandbox_id"),
        CheckConstraint("owner_user_id <> helper_user_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    issuer_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"), index=True)
    issuer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    issuer_sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    parent_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    parent_sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    helper_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    member_id: Mapped[str] = mapped_column(ForeignKey("extension_family_members.id"))
    relationship: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FamilyContextSession(Base):
    __tablename__ = "family_context_sessions"
    __table_args__ = (
        CheckConstraint("issuer_account_version > 0 AND parent_account_version > 0 AND link_version > 0"),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("demo_sessions.id", ondelete="CASCADE"), primary_key=True)
    link_id: Mapped[str] = mapped_column(ForeignKey("family_account_links.id"), index=True)
    issuer_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"))
    issuer_account_version: Mapped[int] = mapped_column(Integer)
    parent_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"))
    parent_account_version: Mapped[int] = mapped_column(Integer)
    link_version: Mapped[int] = mapped_column(Integer)
    member_id: Mapped[str] = mapped_column(ForeignKey("extension_family_members.id"))
    helper_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    parent_sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


FAMILY_ACCOUNT_CONTEXT_TABLES = {FamilyAccountLink.__tablename__, FamilyContextSession.__tablename__}
