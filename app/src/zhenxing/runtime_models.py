from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class RuntimeRecord(Base):
    __tablename__ = "runtime_records"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    root_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="PARENT")
    plan_version: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuntimeTaskRequest(Base):
    """Durable request acceptance, separate from per-node execution idempotency."""
    __tablename__ = "runtime_task_requests"
    __table_args__ = (
        UniqueConstraint("sandbox_id", "user_id", "idempotency_key",
                         name="uq_runtime_task_request_scope_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuntimeOperation(Base):
    __tablename__ = "runtime_operations"
    __table_args__ = (UniqueConstraint("parent_task_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    child_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=True)
    operation_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    action_kind: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(160))
    args_json: Mapped[dict] = mapped_column(JSON, default=dict)
    depends_on_json: Mapped[list] = mapped_column(JSON, default=list)
    ordinal: Mapped[int] = mapped_column(Integer)
    plan_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="BLOCKED")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanSnapshot(Base):
    __tablename__ = "runtime_plan_snapshots"
    __table_args__ = (UniqueConstraint("task_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    plan_hash: Mapped[str] = mapped_column(String(64))
    proposal_json: Mapped[dict] = mapped_column(JSON)
    plan_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    confirmation_id: Mapped[str] = mapped_column(ForeignKey("confirmations.id"))
    node_key: Mapped[str] = mapped_column(String(80))
    plan_hash: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    salt: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
