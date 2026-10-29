"""Confirmed family labels, separate from bank identities and delegations."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class FamilyProfileLabel(Base):
    __tablename__ = "family_profile_labels"
    __table_args__ = (UniqueConstraint("member_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("extension_family_members.id"))
    display_name: Mapped[str] = mapped_column(String(40))
    relationship_label: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    source_reference: Mapped[str] = mapped_column(String(120))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


FAMILY_PROFILE_TABLES = {FamilyProfileLabel.__tablename__}
