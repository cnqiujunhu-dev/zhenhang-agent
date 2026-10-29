"""Independent public knowledge tables; never part of the banking metadata."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, UserDefinedType


def knowledge_now():
    return datetime.now(timezone.utc)


def knowledge_id():
    return str(uuid4())


class KnowledgeBase(DeclarativeBase):
    pass


class PGVector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kwargs):
        return "vector"


class KnowledgeVector(TypeDecorator):
    """pgvector on PostgreSQL; JSON only for explicit SQLite test fixtures."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(PGVector() if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value, separators=(",", ":")) if dialect.name == "postgresql" else value

    def process_result_value(self, value, dialect):
        return json.loads(value) if isinstance(value, str) else value


class KnowledgeDocument(KnowledgeBase):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=knowledge_id)
    document_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    source_uri: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    allowed_roles: Mapped[list] = mapped_column(JSON)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)


class KnowledgeRevision(KnowledgeBase):
    __tablename__ = "knowledge_revisions"
    __table_args__ = (UniqueConstraint("document_id", "version_label"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=knowledge_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    version_label: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeChunk(KnowledgeBase):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("revision_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=knowledge_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("knowledge_revisions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    recipe_version: Mapped[str] = mapped_column(String(40), default="sentence-window-v3-100")
    embedding: Mapped[list | None] = mapped_column(KnowledgeVector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    embedding_revision: Mapped[str | None] = mapped_column(String(160), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeIndexJob(KnowledgeBase):
    __tablename__ = "knowledge_index_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=knowledge_id)
    state: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    revision_ids: Mapped[list] = mapped_column(JSON)
    requested_chunks: Mapped[int] = mapped_column(Integer, default=0)
    indexed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model_revision: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeRetrievalTrace(KnowledgeBase):
    __tablename__ = "knowledge_retrieval_traces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=knowledge_id)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    # No raw customer message, owner ID, bank facts or embedding request secrets.
    roles: Mapped[list] = mapped_column(JSON)
    mode: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    source_refs: Mapped[list] = mapped_column(JSON)
    candidate_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)


class KnowledgeAudit(KnowledgeBase):
    __tablename__ = "knowledge_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=knowledge_id)
    actor_account_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40), index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=knowledge_now)
