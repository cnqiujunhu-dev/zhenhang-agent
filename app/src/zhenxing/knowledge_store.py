"""Local public knowledge lifecycle, separate from customer banking storage."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .knowledge_models import (KnowledgeAudit, KnowledgeBase, KnowledgeChunk, KnowledgeDocument,
                              KnowledgeIndexJob, KnowledgeRetrievalTrace,
                              KnowledgeRevision, knowledge_now)

PUBLIC_KINDS = {"PUBLIC_POLICY", "PRODUCT_TERMS", "CAPABILITY_GUIDE"}
PUBLIC_ROLES = {"CUSTOMER", "DEV_TEST", "FAMILY_HELPER", "ADMIN"}
RECIPE_VERSION = "sentence-window-v3-100"


class KnowledgeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise KnowledgeError("KNOWLEDGE_DATE_INVALID") from None
    if not isinstance(value, datetime):
        raise KnowledgeError("KNOWLEDGE_DATE_INVALID")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def init_knowledge_store(database_url=None, *, create_schema=True):
    url = database_url or os.getenv("ZX_KNOWLEDGE_DATABASE_URL")
    if not url:
        raise KnowledgeError("KNOWLEDGE_UNCONFIGURED")
    if not url.startswith(("postgresql", "sqlite:")):
        raise KnowledgeError("KNOWLEDGE_DATABASE_UNSUPPORTED")
    if url == os.getenv("ZX_DATABASE_URL"):
        raise KnowledgeError("KNOWLEDGE_BANK_DATABASE_FORBIDDEN")
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if create_schema:
        if engine.dialect.name == "postgresql":
            with engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        KnowledgeBase.metadata.create_all(engine)
        # Independent additive knowledge-schema upgrades, never bank metadata.
        columns = {column["name"] for column in inspect(engine).get_columns("knowledge_index_jobs")}
        additions = {"lease_token": "VARCHAR(36)", "heartbeat_at": "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"}
        with engine.begin() as connection:
            for name, kind in additions.items():
                if name not in columns:
                    conditional = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
                    connection.execute(text(f"ALTER TABLE knowledge_index_jobs ADD COLUMN {conditional}{name} {kind}"))
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _field(value, maximum, code):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise KnowledgeError(code)
    if re.search(r"(?i)sk-[a-z0-9_-]{8,}|Bearer\s+\S+|(?:zhenhang|xiaozhao)[a-f0-9]{8}|(?:密码|口令|验证码|password|api[_ -]?key|access[_ -]?token)\s*[:：=]\s*\S+", value):
        raise KnowledgeError("KNOWLEDGE_PRIVATE_CONTENT_FORBIDDEN")
    return value.strip()


def _public_metadata(metadata):
    if metadata is None:
        return {}
    if not isinstance(metadata, dict) or len(metadata) > 20:
        raise KnowledgeError("KNOWLEDGE_METADATA_INVALID")
    # These are public classification fields, never arbitrary customer payloads.
    allowed = {"category", "product_id", "topics", "tags", "description", "origin", "corpus_version", "capability_id"}
    if not set(metadata) <= allowed:
        raise KnowledgeError("KNOWLEDGE_PRIVATE_METADATA_FORBIDDEN")
    for key, value in metadata.items():
        if key in {"topics", "tags"}:
            if not isinstance(value, list) or len(value) > 20:
                raise KnowledgeError("KNOWLEDGE_METADATA_INVALID")
            for item in value:
                _field(item, 100, "KNOWLEDGE_METADATA_INVALID")
        else:
            _field(value, 2000 if key == "description" else 160, "KNOWLEDGE_METADATA_INVALID")
    import json
    try:
        serialized = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise KnowledgeError("KNOWLEDGE_METADATA_INVALID") from None
    if len(serialized) > 3000:
        raise KnowledgeError("KNOWLEDGE_METADATA_INVALID")
    return dict(metadata)


def _assert_public_content(content):
    if not isinstance(content, str) or not content.strip() or len(content) > 120_000:
        raise KnowledgeError("KNOWLEDGE_CONTENT_INVALID")
    # Ingestion is public-only. These identifiers/fields cannot be a corpus shortcut
    # around authenticated bank facts, nor can secrets be embedded to a cloud API.
    if re.search(r"(?i)(?:sk-[a-z0-9_-]{8,}|Bearer\s+\S+|(?:zhenhang|xiaozhao)[a-f0-9]{8}|(?:密码|口令|验证码|password|api[_ -]?key|access[_ -]?token)\s*[:：=]\s*\S+|(?:token_hash|sandbox_id|user_id|account_id|balance_minor|transaction_id|password_hash|api_key)\s*[\"']?\s*[:=])", content):
        raise KnowledgeError("KNOWLEDGE_PRIVATE_CONTENT_FORBIDDEN")
    return content.strip()


def upsert_document(session, *, document_key, title, source_uri, kind="PUBLIC_POLICY",
                    allowed_roles=None, language="zh-CN", metadata=None):
    document_key = _field(document_key, 160, "KNOWLEDGE_KEY_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", document_key):
        raise KnowledgeError("KNOWLEDGE_KEY_INVALID")
    if kind not in PUBLIC_KINDS or language not in {"zh-CN", "en"}:
        raise KnowledgeError("KNOWLEDGE_SCOPE_INVALID")
    roles = allowed_roles if allowed_roles is not None else sorted(PUBLIC_ROLES)
    if not isinstance(roles, (list, tuple)) or not roles or any(role not in PUBLIC_ROLES for role in roles):
        raise KnowledgeError("KNOWLEDGE_ROLES_INVALID")
    title = _field(title, 240, "KNOWLEDGE_TITLE_INVALID")
    source_uri = _field(source_uri, 500, "KNOWLEDGE_SOURCE_INVALID")
    # Source is metadata, not a downloader or executable command. No private URL
    # credentials/query tokens are accepted even though ingestion never fetches it.
    from urllib.parse import urlparse
    uri = urlparse(source_uri)
    if uri.scheme not in {"https", "knowledge", "product", "capability"} or uri.username or uri.password or uri.query or uri.fragment:
        raise KnowledgeError("KNOWLEDGE_SOURCE_INVALID")
    metadata = _public_metadata(metadata)
    document = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.document_key == document_key))
    if document:
        # Never silently mutate a published source while its old citations exist.
        existing = (document.title, document.source_uri, document.kind, document.language, document.allowed_roles, document.metadata_json)
        proposed = (title, source_uri, kind, language, sorted(set(roles)), metadata)
        if existing != proposed:
            raise KnowledgeError("KNOWLEDGE_DOCUMENT_EXISTS")
        return document
    document = KnowledgeDocument(document_key=document_key, title=title, source_uri=source_uri,
        kind=kind, language=language, allowed_roles=sorted(set(roles)), metadata_json=metadata)
    session.add(document)
    session.flush()
    return document


def get_document(session, document_id):
    document = session.get(KnowledgeDocument, document_id)
    if not document:
        document = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.document_key == document_id))
    if not document:
        raise KnowledgeError("KNOWLEDGE_DOCUMENT_NOT_FOUND")
    return document


def create_revision(session, document_id, content, version_label, effective_from=None, expires_at=None):
    document = get_document(session, document_id)
    content = _assert_public_content(content)
    version_label = _field(version_label, 100, "KNOWLEDGE_VERSION_INVALID")
    start, expiry = aware(effective_from) or knowledge_now(), aware(expires_at)
    if expiry is not None and expiry <= start:
        raise KnowledgeError("KNOWLEDGE_EFFECTIVITY_INVALID")
    if session.scalar(select(KnowledgeRevision.id).where(KnowledgeRevision.document_id == document.id,
                                                       KnowledgeRevision.version_label == version_label)):
        raise KnowledgeError("KNOWLEDGE_VERSION_EXISTS")
    revision = KnowledgeRevision(document_id=document.id, content=content, content_hash=content_hash(content),
        version_label=version_label, effective_from=start, expires_at=expiry)
    session.add(revision)
    session.flush()
    return revision


def chunk_content(content, *, maximum=100, overlap=20):
    """Cover every character without truncation, within the 128-token model budget.

    A 100-character ceiling is conservative for the chosen multilingual tokenizer;
    the embedding service still verifies its actual tokenizer limit before encoding.
    Prefer sentence/paragraph boundaries without dropping text between windows.
    """
    if not 80 <= maximum <= 100 or not 0 <= overlap < maximum // 2:
        raise KnowledgeError("KNOWLEDGE_CHUNK_RECIPE_INVALID")
    windows, start = [], 0
    while start < len(content):
        end = min(start + maximum, len(content))
        if end < len(content):
            possible = [match.end() for match in re.finditer(r"[。！？\n]|[.!?](?=\s)", content[start:end])]
            if possible and possible[-1] >= maximum // 2:
                end = start + possible[-1]
        windows.append(content[start:end])
        if end == len(content):
            break
        start = max(start + 1, end - overlap)
    return [window for window in windows if window.strip()]


def publish_revision(session, revision_id, expected_content_hash):
    revision = session.scalar(select(KnowledgeRevision).where(KnowledgeRevision.id == revision_id).with_for_update())
    if not revision:
        raise KnowledgeError("KNOWLEDGE_REVISION_NOT_FOUND")
    if revision.content_hash != expected_content_hash or content_hash(revision.content) != expected_content_hash:
        raise KnowledgeError("KNOWLEDGE_SOURCE_HASH_MISMATCH")
    _assert_public_content(revision.content)
    document = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == revision.document_id).with_for_update())
    if revision.state == "PUBLISHED" and document.state == "PUBLISHED" and document.active_revision_id == revision.id:
        return revision
    if revision.state != "DRAFT":
        raise KnowledgeError("KNOWLEDGE_REVISION_NOT_DRAFT")
    if revision.expires_at and aware(revision.expires_at) <= knowledge_now():
        raise KnowledgeError("KNOWLEDGE_REVISION_EXPIRED")
    for prior in session.scalars(select(KnowledgeRevision).where(KnowledgeRevision.document_id == document.id,
                                                               KnowledgeRevision.state == "PUBLISHED")):
        prior.state = "SUPERSEDED"
    revision.state, revision.published_at = "PUBLISHED", knowledge_now()
    document.state, document.active_revision_id = "PUBLISHED", revision.id
    document.updated_at, document.retired_reason = knowledge_now(), None
    for ordinal, content in enumerate(chunk_content(revision.content)):
        session.add(KnowledgeChunk(revision_id=revision.id, ordinal=ordinal, content=content,
                                   content_hash=content_hash(content), recipe_version=RECIPE_VERSION))
    session.flush()
    return revision


def retire_document(session, document_id, reason=""):
    document = get_document(session, document_id)
    if not isinstance(reason, str) or len(reason) > 240:
        raise KnowledgeError("KNOWLEDGE_REASON_INVALID")
    document.state, document.retired_reason, document.updated_at = "RETIRED", reason, knowledge_now()
    for revision in session.scalars(select(KnowledgeRevision).where(KnowledgeRevision.document_id == document.id,
                                                                 KnowledgeRevision.state == "PUBLISHED")):
        revision.state = "RETIRED"
    session.flush()
    return document


def revision_public(revision):
    return {"id": revision.id, "document_id": revision.document_id, "version_label": revision.version_label,
            "state": revision.state, "status": revision.state, "content_hash": revision.content_hash, "content": revision.content,
            "effective_from": aware(revision.effective_from).isoformat(),
            "expires_at": aware(revision.expires_at).isoformat() if revision.expires_at else None,
            "published_at": aware(revision.published_at).isoformat() if revision.published_at else None}


def document_public(session, document):
    revisions = list(session.scalars(select(KnowledgeRevision).where(KnowledgeRevision.document_id == document.id)
                                    .order_by(KnowledgeRevision.created_at.desc())))
    active = next((row for row in revisions if row.id == document.active_revision_id), None)
    chunks = list(session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id == active.id))) if active else []
    return {"id": document.id, "document_id": document.id, "document_key": document.document_key, "title": document.title,
            "source_uri": document.source_uri, "kind": document.kind, "language": document.language,
            "allowed_roles": document.allowed_roles, "metadata": document.metadata_json, "state": document.state, "status": document.state,
            "active_revision_id": document.active_revision_id, "retired_reason": document.retired_reason,
            "version_label": active.version_label if active else None, "chunk_count": len(chunks),
            "indexed_chunks": sum(1 for chunk in chunks if chunk.embedding is not None and chunk.embedding_content_hash == chunk.content_hash),
            "revisions": [revision_public(row) for row in revisions]}


def document_detail(session, document_id):
    return document_public(session, get_document(session, document_id))


def list_documents(session, *, include_drafts=True, limit=100, offset=0):
    if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
        raise KnowledgeError("KNOWLEDGE_PAGE_INVALID")
    statement = select(KnowledgeDocument).order_by(KnowledgeDocument.document_key).limit(limit).offset(offset)
    if not include_drafts:
        statement = statement.where(KnowledgeDocument.state == "PUBLISHED")
    return [document_public(session, row) for row in session.scalars(statement)]


def job_public(job):
    return {"id": job.id, "state": job.state, "status": job.state, "revision_ids": job.revision_ids,
            "requested_chunks": job.requested_chunks, "indexed_chunks": job.indexed_chunks,
            "model": job.model, "model_revision": job.model_revision, "dimension": job.dimension, "error_code": job.error_code,
            "created_at": aware(job.created_at).isoformat(),
            "heartbeat_at": aware(job.heartbeat_at).isoformat() if job.heartbeat_at else None,
            "finished_at": aware(job.finished_at).isoformat() if job.finished_at else None}


def index_status(session):
    from .embedding_gateway import embedding_config_status, validate_vector
    documents = list(session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.state == "PUBLISHED")))
    active_ids = [row.active_revision_id for row in documents if row.active_revision_id]
    chunks = list(session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.revision_id.in_(active_ids)))) if active_ids else []
    now = knowledge_now()
    valid_revisions = {row.id: row for row in session.scalars(select(KnowledgeRevision).where(KnowledgeRevision.id.in_(active_ids)))
                       if row.state == "PUBLISHED" and aware(row.effective_from) <= now and (not row.expires_at or aware(row.expires_at) > now)
                       and row.content_hash == content_hash(row.content)} if active_ids else {}
    expected_chunks = {identifier: chunk_content(row.content) for identifier, row in valid_revisions.items()}
    active_chunks = [row for row in chunks if row.revision_id in valid_revisions and row.content_hash == content_hash(row.content)
                     and row.recipe_version == RECIPE_VERSION and 0 <= row.ordinal < len(expected_chunks[row.revision_id])
                     and expected_chunks[row.revision_id][row.ordinal] == row.content]
    config = embedding_config_status()
    expected_revision = os.getenv("ZX_EMBEDDING_MODEL_REVISION", "").strip()
    expected_dimension = os.getenv("ZX_EMBEDDING_DIMENSION", "").strip()
    indexed = []
    for row in active_chunks:
        if row.embedding is None or row.embedding_content_hash != row.content_hash or row.embedding_model != config["model"] or expected_revision and row.embedding_revision != expected_revision:
            continue
        try:
            vector = validate_vector(row.embedding, row.embedding_model, row.embedding_revision)
        except ValueError:
            continue
        if vector.dimension == row.embedding_dimension and (not expected_dimension or expected_dimension.isdigit() and row.embedding_dimension == int(expected_dimension)):
            indexed.append(row)
    jobs = list(session.scalars(select(KnowledgeIndexJob).order_by(KnowledgeIndexJob.created_at.desc()).limit(20)))
    fingerprints = {}
    for row in indexed:
        key = (row.embedding_model, row.embedding_revision, row.embedding_dimension)
        fingerprints[key] = fingerprints.get(key, 0) + 1
    return {"embedding": config, "storage": "PGVECTOR" if session.bind.dialect.name == "postgresql" else "SQLITE_TEST_FIXTURE",
            "published_documents": len(documents), "searchable_documents": len(valid_revisions),
            "active_chunks": len(active_chunks), "indexed_chunks": len(indexed),
            "semantic_ready": session.bind.dialect.name == "postgresql" and config["status"] == "CONFIGURED" and bool(active_chunks) and len(indexed) == len(active_chunks),
            "jobs": [job_public(row) for row in jobs],
            "fingerprints": [{"model": key[0], "model_revision": key[1], "dimension": key[2], "indexed_chunks": count}
                             for key, count in sorted(fingerprints.items())],
            "retrieval_traces": session.scalar(select(func.count()).select_from(KnowledgeRetrievalTrace))}


knowledge_status = index_status


def record_knowledge_audit(session, *, actor_account_id, action, document_id=None,
                           revision_id=None, content_hash=None, reason=""):
    if action not in {"CREATE_DOCUMENT", "CREATE_REVISION", "PUBLISH", "RETIRE", "REINDEX"}:
        raise KnowledgeError("KNOWLEDGE_AUDIT_ACTION_INVALID")
    actor_account_id = _field(actor_account_id, 120, "KNOWLEDGE_AUDIT_ACTOR_INVALID")
    if content_hash is not None and not re.fullmatch(r"[a-f0-9]{64}", content_hash):
        raise KnowledgeError("KNOWLEDGE_SOURCE_HASH_INVALID")
    if not isinstance(reason, str) or len(reason) > 240 or re.search(r"(?i)sk-[a-z0-9]{16,}|Bearer\s+", reason):
        raise KnowledgeError("KNOWLEDGE_AUDIT_REASON_INVALID")
    row = KnowledgeAudit(actor_account_id=actor_account_id, action=action, document_id=document_id,
        revision_id=revision_id, content_hash=content_hash, reason=reason)
    session.add(row)
    session.flush()
    return row


def bootstrap_public_corpus(factory_or_session, seeds=None):
    if seeds is None:
        from .knowledge_corpus import corpus_seed
        seeds = corpus_seed()
    if not isinstance(seeds, list):
        raise KnowledgeError("KNOWLEDGE_CORPUS_INVALID")
    def apply(session):
        created, skipped = 0, 0
        for seed in seeds:
            key = seed.get("document_key", seed.get("document_id"))
            if session.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.document_key == key)):
                skipped += 1
                continue  # Never replace administrator revisions on restart.
            document = upsert_document(session, document_key=key, title=seed["title"],
                source_uri=seed.get("source_uri", seed.get("source")), kind=seed.get("kind", "PUBLIC_POLICY"),
                language=seed.get("language", seed.get("lang", "zh-CN")), metadata=seed.get("metadata"),
                allowed_roles=seed.get("allowed_roles"))
            revision = create_revision(session, document.id, seed.get("content", seed.get("body")),
                seed.get("version_label", seed.get("version")), seed.get("effective_from"), seed.get("expires_at"))
            if seed.get("status", "PUBLISHED") == "PUBLISHED":
                publish_revision(session, revision.id, revision.content_hash)
            created += 1
        return {"created": created, "skipped_existing": skipped}
    if isinstance(factory_or_session, Session):
        return apply(factory_or_session)
    with factory_or_session.begin() as session:
        return apply(session)
