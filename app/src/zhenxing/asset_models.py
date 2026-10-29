"""Separate, additive sample-asset metadata; existing bank tables stay untouched."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .models import Sandbox, User, new_id, utcnow


class AssetBase(DeclarativeBase):
    pass


class AssetSnapshotPrice(AssetBase):
    __tablename__ = "asset_snapshot_prices"
    __table_args__ = (
        UniqueConstraint("instrument_id", "revision"),
        CheckConstraint("price >= 0 AND version >= 1"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(String(100))
    revision: Mapped[str] = mapped_column(String(80))
    price: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    currency: Mapped[str] = mapped_column(String(3))
    quantity_unit: Mapped[str] = mapped_column(String(12))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(80), default="SYNTHETIC_SNAPSHOT_NOT_MARKET_DATA")
    version: Mapped[int] = mapped_column(Integer, default=1)


class AssetSamplePosition(AssetBase):
    __tablename__ = "asset_sample_positions"
    __table_args__ = (
        UniqueConstraint("sandbox_id", "user_id", "instrument_id"),
        CheckConstraint("asset_class IN ('STOCK','PRECIOUS_METAL')"),
        CheckConstraint("total_quantity >= 0 AND version >= 1"),
        CheckConstraint("available_quantity IS NULL OR available_quantity >= 0"),
        CheckConstraint("frozen_quantity IS NULL OR frozen_quantity >= 0"),
        CheckConstraint("remaining_cost_minor IS NULL OR remaining_cost_minor >= 0"),
        CheckConstraint("available_quantity IS NULL OR frozen_quantity IS NULL OR total_quantity = available_quantity + frozen_quantity"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Direct Column references work across the separate declarative metadata.
    sandbox_id: Mapped[str] = mapped_column(ForeignKey(Sandbox.id), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(User.id), index=True)
    instrument_id: Mapped[str] = mapped_column(String(100))
    product_name: Mapped[str] = mapped_column(String(100))
    asset_class: Mapped[str] = mapped_column(String(20))
    quantity_unit: Mapped[str] = mapped_column(String(12))
    total_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    available_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    frozen_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    remaining_cost_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey(AssetSnapshotPrice.id), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssetSampleBootstrap(AssetBase):
    __tablename__ = "asset_sample_bootstraps"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey(Sandbox.id))
    user_id: Mapped[str] = mapped_column(ForeignKey(User.id))
    revision: Mapped[str] = mapped_column(String(80))
    position_ids_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


ASSET_TABLES = frozenset(AssetBase.metadata.tables)
