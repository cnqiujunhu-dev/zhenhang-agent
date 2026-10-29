"""Private image evidence. These tables never authorize a banking operation."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("sandbox_id", "user_id", "idempotency_key"),
        CheckConstraint("byte_size > 0 AND byte_size <= 5242880"),
        CheckConstraint("width > 0 AND height > 0 AND version > 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    original_sha256: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(30))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(128), default="image")
    blob_key: Mapped[str] = mapped_column(String(200))
    cloud_consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    extraction_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latest_review_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AttachmentJob(Base):
    __tablename__ = "attachment_jobs"
    __table_args__ = (UniqueConstraint("attachment_id"), CheckConstraint("attempts >= 0 AND attempts <= 2"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(60), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(60), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AttachmentReview(Base):
    __tablename__ = "attachment_reviews"
    __table_args__ = (UniqueConstraint("attachment_id", "attachment_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    attachment_version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(40), default="USER_VALIDATED_DOCUMENT")
    selected_fields_json: Mapped[list] = mapped_column(JSON)
    fields_json: Mapped[dict] = mapped_column(JSON)
    corrections_json: Mapped[dict] = mapped_column(JSON)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttachmentEvent(Base):
    __tablename__ = "attachment_events"
    __table_args__ = (UniqueConstraint("attachment_id", "seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[dict] = mapped_column(JSON)
    previous_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


ATTACHMENT_TABLES = {"attachments", "attachment_jobs", "attachment_reviews", "attachment_events"}
