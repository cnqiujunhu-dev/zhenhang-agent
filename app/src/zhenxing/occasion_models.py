"""Minimal, explicitly confirmed family occasions; no inferred birth dates."""
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class FamilyOccasion(Base):
    __tablename__ = "family_occasions"
    __table_args__ = (UniqueConstraint("sandbox_id", "owner_user_id", "family_member_id", "kind"),
        CheckConstraint("month BETWEEN 1 AND 12 AND day BETWEEN 1 AND 31"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    family_member_id: Mapped[str] = mapped_column(ForeignKey("extension_family_members.id"))
    kind: Mapped[str] = mapped_column(String(20), default="BIRTHDAY")
    month: Mapped[int] = mapped_column(Integer)
    day: Mapped[int] = mapped_column(Integer)
    calendar: Mapped[str] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    leap_day_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lunar_leap_month: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_solar_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    adult_subject_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="USER_CONFIRMED")
    source_reference: Mapped[str] = mapped_column(String(120))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


OCCASION_TABLES = {FamilyOccasion.__tablename__}
