"""Immutable same-owner cash movement, separate from outward transfers."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, event, inspect
from sqlalchemy.orm import Mapped, mapped_column

from .contracts import DomainError
from .credit_models import PaymentJournal  # Register the journal FK dependency.
from .database import Base
from .models import new_id, utcnow


class OwnAccountTransferRecord(Base):
    __tablename__="own_account_transfer_records"
    __table_args__=(UniqueConstraint("sandbox_id","user_id","operation_key"),
        CheckConstraint("currency='CNY' AND amount_minor>0 AND source_account_id<>destination_account_id"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    sandbox_id: Mapped[str]=mapped_column(ForeignKey("sandboxes.id"),index=True)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id"),index=True)
    task_id: Mapped[str|None]=mapped_column(ForeignKey("tasks.id"),nullable=True,index=True)
    source_account_id: Mapped[str]=mapped_column(ForeignKey("accounts.id"))
    destination_account_id: Mapped[str]=mapped_column(ForeignKey("accounts.id"))
    source_card_id: Mapped[str]=mapped_column(ForeignKey("cards.id"))
    destination_card_id: Mapped[str]=mapped_column(ForeignKey("cards.id"))
    amount_minor: Mapped[int]=mapped_column(Integer)
    currency: Mapped[str]=mapped_column(String(3),default="CNY")
    operation_key: Mapped[str]=mapped_column(String(120))
    request_hash: Mapped[str]=mapped_column(String(64))
    journal_id: Mapped[str]=mapped_column(ForeignKey("payment_journals.id"),unique=True)
    scope_json: Mapped[dict]=mapped_column(JSON)
    receipt_json: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


@event.listens_for(OwnAccountTransferRecord,"before_update")
def _immutable(_mapper,_connection,row):
    if any(attribute.history.has_changes() for attribute in inspect(row).attrs):
        raise DomainError("OWN_TRANSFER_RECEIPT_IMMUTABLE","已入账的本人划转回执不能改写。",409)


MODEL_TABLES=frozenset({OwnAccountTransferRecord.__tablename__})
