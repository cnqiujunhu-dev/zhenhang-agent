"""Public corpus indexing with cloud I/O strictly outside storage transactions."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .embedding_gateway import EmbeddingResult, embed_texts, validate_vector
from .knowledge_models import (KnowledgeChunk, KnowledgeDocument, KnowledgeIndexJob,
                              KnowledgeRevision, knowledge_now)
from .knowledge_models import knowledge_id
from .knowledge_store import KnowledgeError, aware, content_hash, job_public


@dataclass(frozen=True)
class IndexItem:
    chunk_id: str
    revision_id: str
    content: str
    content_hash: str
    revision_hash: str


def create_index_job(session, revision_ids=None):
    recover_stale_index_jobs(session)
    documents = list(session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.state == "PUBLISHED")))
    active = {row.active_revision_id for row in documents if row.active_revision_id}
    if revision_ids is None:
        revision_ids = sorted(active)
    if not isinstance(revision_ids, (list, tuple)) or len(revision_ids) > 500 or any(not isinstance(identifier, str) for identifier in revision_ids):
        raise KnowledgeError("KNOWLEDGE_INDEX_SCOPE_INVALID")
    identifiers = sorted(set(revision_ids))
    if not set(identifiers) <= active:
        raise KnowledgeError("KNOWLEDGE_INDEX_REVISION_NOT_PUBLISHED")
    job = KnowledgeIndexJob(revision_ids=identifiers)
    session.add(job)
    session.flush()
    return job


def recover_stale_index_jobs(session, *, max_age_seconds=180, now=None):
    """Expire crashed workers, never silently rerun or erase their evidence."""
    if type(max_age_seconds) is not int or not 60 <= max_age_seconds <= 86400:
        raise KnowledgeError("KNOWLEDGE_INDEX_LEASE_DURATION_INVALID")
    now = aware(now) if now else knowledge_now()
    cutoff, recovered = now - timedelta(seconds=max_age_seconds), []
    statement = select(KnowledgeIndexJob).where(KnowledgeIndexJob.state == "RUNNING").with_for_update(skip_locked=True)
    for job in session.scalars(statement):
        heartbeat = aware(job.heartbeat_at or job.started_at or job.created_at)
        if heartbeat < cutoff:
            job.state, job.error_code, job.finished_at = "INTERRUPTED", "KNOWLEDGE_INDEX_LEASE_EXPIRED", now
            job.lease_token = None
            recovered.append(job.id)
    session.flush()
    return {"recovered_job_ids": recovered, "recovered_count": len(recovered)}


def heartbeat_index_job(session, job_id, lease_token):
    job = session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.id == job_id).with_for_update())
    if not job or job.state != "RUNNING" or not lease_token or job.lease_token != lease_token:
        raise KnowledgeError("KNOWLEDGE_INDEX_LEASE_LOST")
    job.heartbeat_at = knowledge_now()
    return job


def retry_index_job(session, job_id):
    """An explicit retry creates a fresh job; the failed/interrupted job remains."""
    recover_stale_index_jobs(session)
    job = session.get(KnowledgeIndexJob, job_id)
    if not job:
        raise KnowledgeError("KNOWLEDGE_INDEX_JOB_NOT_FOUND")
    if job.state not in {"FAILED", "UNCONFIGURED", "INTERRUPTED", "STALE"}:
        raise KnowledgeError("KNOWLEDGE_INDEX_RETRY_NOT_TERMINAL")
    return create_index_job(session, list(job.revision_ids))


def prepare_index_batch(session, job_id):
    job = session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.id == job_id).with_for_update())
    if not job:
        raise KnowledgeError("KNOWLEDGE_INDEX_JOB_NOT_FOUND")
    if job.state != "QUEUED":
        return job, None  # Another worker's RUNNING lease is not our empty batch.
    items = []
    for revision_id in job.revision_ids:
        revision = session.get(KnowledgeRevision, revision_id)
        document = session.get(KnowledgeDocument, revision.document_id) if revision else None
        if not revision or not document or document.state != "PUBLISHED" or document.active_revision_id != revision.id or revision.state != "PUBLISHED" or content_hash(revision.content) != revision.content_hash or revision.expires_at and aware(revision.expires_at) <= knowledge_now():
            job.state, job.error_code, job.finished_at = "STALE", "KNOWLEDGE_INDEX_SOURCE_CHANGED", knowledge_now()
            return job, []
        for chunk in session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id == revision_id).order_by(KnowledgeChunk.ordinal)):
            if content_hash(chunk.content) != chunk.content_hash:
                job.state, job.error_code, job.finished_at = "STALE", "KNOWLEDGE_INDEX_SOURCE_CHANGED", knowledge_now()
                return job, []
            items.append(IndexItem(chunk.id, revision.id, chunk.content, chunk.content_hash, revision.content_hash))
    if len(items) > 5000:
        raise KnowledgeError("KNOWLEDGE_INDEX_BATCH_TOO_LARGE")
    job.state, job.started_at, job.requested_chunks = "RUNNING", knowledge_now(), len(items)
    job.lease_token, job.heartbeat_at = knowledge_id(), knowledge_now()
    return job, items


def apply_index_batch(session, job_id, items, result, *, lease_token=None):
    job = session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.id == job_id).with_for_update())
    if not job or job.state != "RUNNING" or not lease_token or job.lease_token != lease_token:
        raise KnowledgeError("KNOWLEDGE_INDEX_JOB_NOT_RUNNING")
    if result.status != "READY":
        job.state = "UNCONFIGURED" if result.status == "UNCONFIGURED" else "FAILED"
        job.error_code, job.finished_at = result.error_code or "EMBEDDING_FAILED", knowledge_now()
        job.lease_token = None
        return job
    if len(items) != len(result.vectors):
        raise KnowledgeError("KNOWLEDGE_INDEX_VECTOR_COUNT_INVALID")
    validated = []
    for item, vector in zip(items, result.vectors):
        vector = validate_vector(vector.values, vector.model, vector.model_revision)
        if vector.model != result.model or vector.dimension != result.dimension or result.model_revision not in {None, vector.model_revision}:
            raise KnowledgeError("KNOWLEDGE_INDEX_VECTOR_MODEL_INVALID")
        revision = session.get(KnowledgeRevision, item.revision_id)
        document = session.get(KnowledgeDocument, revision.document_id) if revision else None
        chunk = session.get(KnowledgeChunk, item.chunk_id)
        if not revision or not document or not chunk or document.state != "PUBLISHED" or document.active_revision_id != item.revision_id or revision.state != "PUBLISHED" or revision.content_hash != item.revision_hash or content_hash(revision.content) != item.revision_hash or chunk.content_hash != item.content_hash or content_hash(chunk.content) != item.content_hash or revision.expires_at and aware(revision.expires_at) <= knowledge_now():
            job.state, job.error_code, job.finished_at = "STALE", "KNOWLEDGE_INDEX_SOURCE_CHANGED", knowledge_now()
            job.lease_token = None
            return job  # No partial old-revision activation.
        validated.append((chunk, vector))
    for chunk, vector in validated:
        chunk.embedding = list(vector.values)
        chunk.embedding_model, chunk.embedding_dimension = vector.model, vector.dimension
        chunk.embedding_revision = vector.model_revision
        chunk.embedding_content_hash, chunk.indexed_at = chunk.content_hash, knowledge_now()
    job.state, job.model, job.dimension = "SUCCEEDED", result.model, result.dimension
    job.model_revision = result.model_revision or (validated[0][1].model_revision if validated else None)
    job.indexed_chunks, job.finished_at, job.error_code = len(validated), knowledge_now(), None
    job.lease_token = None
    session.flush()
    return job


def ensure_vector_index(engine, *, model, dimension, model_revision="unspecified"):
    """Per-model/dimension partial HNSW; >2000 dims keep exact pgvector search."""
    validate_vector([1.0] * dimension, model, model_revision)
    if engine.dialect.name != "postgresql" or dimension > 2000:
        return {"status": "EXACT_ONLY", "dimension": dimension}
    name = "knowledge_hnsw_" + hashlib.sha256(f"{model}:{model_revision}:{dimension}".encode()).hexdigest()[:16]
    # Both interpolated values are validated; no user request text enters DDL.
    escaped_model = model.replace("'", "''")
    sql = f"CREATE INDEX IF NOT EXISTS {name} ON knowledge_chunks USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) WHERE embedding_dimension = {dimension} AND embedding_model = '{escaped_model}' AND embedding_revision = '{model_revision}'"
    with engine.begin() as connection:
        connection.exec_driver_sql(sql)
    return {"status": "HNSW_READY", "dimension": dimension, "index": name}


def run_index_job(factory, job_id, *, embedder=None):
    embedder = embedder or embed_texts
    with factory.begin() as session:
        job, items = prepare_index_batch(session, job_id)
        if job.state != "RUNNING" or items is None:
            return job_public(job)
        lease_token = job.lease_token
    # The previous session AND transaction are closed before any cloud request.
    if not items:
        result = EmbeddingResult("ERROR", error_code="KNOWLEDGE_INDEX_EMPTY")
    else:
        all_vectors, result = [], None
        for offset in range(0, len(items), 32):
            try:
                batch = embedder([item.content for item in items[offset:offset + 32]])
            except Exception:
                result = EmbeddingResult("ERROR", error_code="EMBEDDING_JOB_UNEXPECTED_ERROR")
                break  # Persist a safe terminal state, never exception/provider secrets.
            if not isinstance(batch, EmbeddingResult):
                result = EmbeddingResult("ERROR", error_code="EMBEDDING_JOB_RESULT_INVALID")
                break
            if batch.status != "READY":
                result = batch
                break
            if result and (batch.model != result.model or batch.dimension != result.dimension or batch.model_revision != result.model_revision or batch.max_tokens != result.max_tokens):
                result = EmbeddingResult("ERROR", error_code="EMBEDDING_BATCH_MODEL_MISMATCH")
                break
            result = batch
            all_vectors.extend(batch.vectors)
            try:
                with factory.begin() as session:
                    heartbeat_index_job(session, job_id, lease_token)
            except KnowledgeError:
                with factory() as session:
                    return job_public(session.get(KnowledgeIndexJob, job_id))
        else:
            result = EmbeddingResult("READY", vectors=tuple(all_vectors), model=result.model, dimension=result.dimension,
                                     model_revision=result.model_revision, max_tokens=result.max_tokens)
    try:
        with factory.begin() as session:
            job = apply_index_batch(session, job_id, items, result, lease_token=lease_token)
            public = job_public(job)
        if public["state"] == "SUCCEEDED":
            try:
                public["vector_index"] = ensure_vector_index(factory.kw["bind"], model=public["model"], dimension=public["dimension"], model_revision=public["model_revision"])
            except SQLAlchemyError:
                # Real vectors remain usable for exact distance queries. An HNSW
                # DDL failure must not be advertised as approximate-index ready.
                public["vector_index"] = {"status": "EXACT_ONLY_INDEX_ERROR", "error_code": "VECTOR_INDEX_CREATE_FAILED"}
        return public
    except KnowledgeError as error:
        with factory.begin() as session:
            job = session.get(KnowledgeIndexJob, job_id)
            if job and job.state == "RUNNING" and job.lease_token == lease_token:
                job.state, job.error_code, job.finished_at = "FAILED", error.code, knowledge_now()
                job.lease_token = None
                return job_public(job)
            if job:
                return job_public(job)
        raise
