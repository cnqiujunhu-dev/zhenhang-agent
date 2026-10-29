"""Owner-sandbox virtual commerce, never an external merchant receipt."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class VirtualMerchant(Base):
    __tablename__ = "virtual_merchants"
    __table_args__ = (UniqueConstraint("sandbox_id", "code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(40), default="VIRTUAL_MERCHANT_REGISTER")


class MerchantProduct(Base):
    __tablename__ = "merchant_products"
    __table_args__ = (UniqueConstraint("merchant_id", "sku"),
        CheckConstraint("price_minor > 0 AND stock >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("virtual_merchants.id"), index=True)
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    stock: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    refund_policy: Mapped[str] = mapped_column(String(40), default="BEFORE_FULFILMENT_ONLY")


class MerchantOrder(Base):
    __tablename__ = "merchant_orders"
    __table_args__ = (UniqueConstraint("sandbox_id", "operation_key"),
        CheckConstraint("quantity > 0 AND amount_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("virtual_merchants.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("merchant_products.id"))
    occurrence_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    recipient_json: Mapped[dict] = mapped_column(JSON)
    delivery_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="PAID")
    payment_receipt_json: Mapped[dict] = mapped_column(JSON)
    product_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_source: Mapped[str] = mapped_column(String(50), default="VIRTUAL_MERCHANT_ORDER_LEDGER")


class MerchantRefund(Base):
    __tablename__ = "merchant_refunds"
    __table_args__ = (UniqueConstraint("order_id"), UniqueConstraint("sandbox_id", "operation_key"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey("merchant_orders.id"))
    amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED")
    operation_key: Mapped[str] = mapped_column(String(120))
    receipt_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


MERCHANT_TABLES = {VirtualMerchant.__tablename__, MerchantProduct.__tablename__,
    MerchantOrder.__tablename__, MerchantRefund.__tablename__}
