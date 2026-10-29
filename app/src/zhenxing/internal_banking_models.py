"""Immutable server-owned links for named simulated bank counterparties."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import utcnow


class InternalPayeeBinding(Base):
    __tablename__ = "internal_payee_bindings"
    payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"), primary_key=True)
    recipient_login_account_id: Mapped[str] = mapped_column(ForeignKey("login_accounts.id"), index=True)
    recipient_bank_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    username_snapshot: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InternalTransferSettlement(Base):
    __tablename__ = "internal_transfer_settlements"
    __table_args__ = (CheckConstraint("amount_minor > 0"), CheckConstraint("sender_account_id <> receiver_account_id"))
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.id"), primary_key=True)
    sender_sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    sender_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    receiver_sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    receiver_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    receiver_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    sender_username: Mapped[str] = mapped_column(String(32))
    receiver_username: Mapped[str] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


INTERNAL_BANKING_TABLES = {InternalPayeeBinding.__tablename__, InternalTransferSettlement.__tablename__}
