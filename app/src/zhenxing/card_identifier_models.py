"""Persistent simulation identifiers; not card PANs and not payment credentials."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, event, inspect
from sqlalchemy.orm import Mapped, mapped_column

from .contracts import DomainError
from .database import Base
from .models import utcnow


class CardIdentifier(Base):
    __tablename__ = "card_identifiers"
    __table_args__ = (CheckConstraint("length(card_number)=16"),
                      CheckConstraint("issuer_prefix IN ('zhenhang','xiaozhao')"))
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), primary_key=True)
    card_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    issuer_prefix: Mapped[str] = mapped_column(String(8))
    legacy_last4: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(CardIdentifier, "before_update")
def _immutable_identifier(_mapper, _connection, target):
    state = inspect(target)
    if any(state.attrs[field].history.has_changes() for field in ("card_id", "card_number", "issuer_prefix", "legacy_last4", "created_at")):
        raise DomainError("CARD_IDENTIFIER_IMMUTABLE", "已生成平台卡号及旧尾号快照不能改写", 409)


CARD_IDENTIFIER_TABLES = {CardIdentifier.__tablename__}
