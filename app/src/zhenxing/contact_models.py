"""Payer-scoped, confirmed recipient identities; never a global user rename."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class ContactAlias(Base):
    __tablename__ = "contact_aliases"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "alias_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    alias_name: Mapped[str] = mapped_column(String(32))
    recipient_login_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"))
    recipient_bank_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    recipient_card_id: Mapped[str | None] = mapped_column(ForeignKey("cards.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContactAliasProposal(Base):
    __tablename__ = "contact_alias_proposals"
    __table_args__ = (UniqueConstraint("task_id", "alias_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    alias_name: Mapped[str] = mapped_column(String(32))
    recipient_login_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"))
    recipient_bank_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    recipient_card_id: Mapped[str | None] = mapped_column(ForeignKey("cards.id"), nullable=True)
    recipient_username: Mapped[str] = mapped_column(String(32))
    requested_reference: Mapped[str] = mapped_column(String(80))
    expected_alias_id: Mapped[str | None] = mapped_column(ForeignKey("contact_aliases.id"), nullable=True)
    expected_alias_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_kind: Mapped[str] = mapped_column(String(20))
    scope_json: Mapped[dict] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContactAliasEvent(Base):
    __tablename__ = "contact_alias_events"
    __table_args__ = (UniqueConstraint("proposal_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    alias_id: Mapped[str] = mapped_column(ForeignKey("contact_aliases.id"))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("contact_alias_proposals.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    action: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


CONTACT_TABLES = {model.__tablename__ for model in (ContactAlias, ContactAliasProposal, ContactAliasEvent)}
