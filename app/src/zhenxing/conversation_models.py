"""Durable, actor-scoped chat. Chat records never constitute bank confirmation."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "client_request_id", name="uq_chat_thread_client"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(80), default="与圳行Agent的对话")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)
    current_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    __table_args__ = (
        UniqueConstraint("thread_id", "client_request_id", name="uq_chat_turn_client"),
        UniqueConstraint("thread_id", "sequence", name="uq_chat_turn_sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    client_request_id: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    context_policy: Mapped[str] = mapped_column(String(20), default="AUTO")
    requested_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    expected_task_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_evidence_refs_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[list] = mapped_column(JSON, default=list)
    suggestions_json: Mapped[list] = mapped_column(JSON, default=list)
    response_meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    field_sources_json: Mapped[list] = mapped_column(JSON, default=list)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    task_relationship: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
