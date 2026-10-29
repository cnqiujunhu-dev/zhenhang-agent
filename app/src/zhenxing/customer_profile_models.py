"""Independent customer membership, card-tier and explicit-preference records."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class CustomerMembership(Base):
    __tablename__ = "customer_memberships"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    tier_code: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    source: Mapped[str] = mapped_column(String(60), default="REGISTERED_PROJECT_PROFILE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerCardTier(Base):
    __tablename__ = "customer_card_tiers"
    __table_args__ = (UniqueConstraint("card_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"))
    tier_code: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(60), default="REGISTERED_PROJECT_CARD")
    version: Mapped[int] = mapped_column(Integer, default=1)


class CustomerBenefitActivity(Base):
    __tablename__ = "customer_benefit_activities"
    __table_args__ = (UniqueConstraint("sandbox_id", "activity_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    activity_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    customer_tiers_json: Mapped[list] = mapped_column(JSON, default=list)
    card_tiers_json: Mapped[list] = mapped_column(JSON, default=list)
    active_card_required: Mapped[bool] = mapped_column(default=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    rule_version: Mapped[int] = mapped_column(Integer, default=1)


class CustomerExplicitPreference(Base):
    __tablename__ = "customer_explicit_preferences"
    __table_args__ = (
        UniqueConstraint("sandbox_id", "user_id", "kind"),
        CheckConstraint("version >= 1"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(24))
    value: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_text: Mapped[str] = mapped_column(Text)
    source_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomerPreferenceChange(Base):
    __tablename__ = "customer_preference_changes"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    operation: Mapped[str] = mapped_column(String(12))
    previous_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


CUSTOMER_PROFILE_TABLES = {
    model.__tablename__ for model in (
        CustomerMembership, CustomerCardTier, CustomerBenefitActivity,
        CustomerExplicitPreference, CustomerPreferenceChange,
    )
}
