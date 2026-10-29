"""Role-gated public-knowledge administration, isolated from the banking DB."""
from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Literal

from fastapi import Depends, Query
from pydantic import Field, field_validator

from .accounts import require_account_role
from .contracts import DomainError, StrictModel
from .financial_product_catalog import CATALOG_VERSION, product_catalog, product_detail

_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+|\b(?:zhenhang|xiaozhao)[0-9a-f]{8}\b|"
                     r"(?:密码|口令|验证码|api[_ -]?key|password|access[_ -]?token)\s*[:：=]\s*\S+", re.I)


def _public_text(value: str) -> str:
    value = value.strip()
    if not value or _SECRET.search(value):
        raise ValueError("知识资料必须是公开内容，不能包含口令、密钥、验证码或私人卡号")
    return value


class KnowledgeDraft(StrictModel):
    document_key: str = Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]+$")
    title: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=1, max_length=300)
    kind: Literal["PUBLIC_POLICY", "PRODUCT_TERMS", "CAPABILITY_GUIDE"] = "PUBLIC_POLICY"
    language: Literal["zh-CN", "en"] = "zh-CN"
    content: str = Field(min_length=1, max_length=60_000)
    version_label: str = Field(min_length=1, max_length=80)
    effective_from: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("title", "content", "version_label", "source_uri")
    @classmethod
    def public(cls, value):
        return _public_text(value)

    @field_validator("source_uri")
    @classmethod
    def source_reference(cls, value):
        # References are labels, never URLs for server fetch or executable links.
        if re.search(r"^(?:javascript|data|file):|://[^/\s]*@|[\r\n]", value, re.I):
            raise ValueError("资料来源只能是非执行的公开引用")
        return value


class KnowledgeRevisionDraft(StrictModel):
    content: str = Field(min_length=1, max_length=60_000)
    version_label: str = Field(min_length=1, max_length=80)
    effective_from: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("content", "version_label")
    @classmethod
    def public(cls, value):
        return _public_text(value)


class KnowledgePublish(StrictModel):
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeRetire(StrictModel):
    reason: str = Field(min_length=4, max_length=200)

    @field_validator("reason")
    @classmethod
    def public(cls, value):
        return _public_text(value)


class KnowledgeIndexRequest(StrictModel):
    revision_ids: list[str] | None = Field(default=None, max_length=100)


class KnowledgeSearch(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    language: Literal["zh-CN", "en"] = "zh-CN"
    limit: int = Field(default=6, ge=1, le=12, strict=True)

    @field_validator("query")
    @classmethod
    def public(cls, value):
        return _public_text(value)


@lru_cache(maxsize=4)
def _knowledge_factory_for_url(url: str):
    from .knowledge_store import init_knowledge_store
    return init_knowledge_store(url, create_schema=False)[1]


def _configured_factory():
    import os
    url = os.environ.get("ZX_KNOWLEDGE_DATABASE_URL", "").strip()
    if not url:
        raise DomainError("KNOWLEDGE_NOT_CONFIGURED", "知识服务尚未配置", 503)
    try:
        return _knowledge_factory_for_url(url)
    except Exception:
        raise DomainError("KNOWLEDGE_UNAVAILABLE", "知识服务暂不可用", 503) from None


def probe_local_embedding_health(*, client=None):
    """Read only the allowlisted local /health endpoint; never send secrets."""
    import os
    from urllib.parse import urlparse
    import httpx
    from .embedding_gateway import embedding_config_status
    from .knowledge_models import knowledge_now
    config = embedding_config_status()
    observed = {"status": "UNCONFIGURED", "checked_at": knowledge_now().isoformat(),
                "model": None, "model_revision": None, "dimension": None, "error_code": None}
    if config["status"] != "CONFIGURED": return observed
    if config["provider"] != "LOCAL_HTTP":
        return {**observed, "status": "NOT_PROBED", "error_code": "LOCAL_HEALTH_NOT_APPLICABLE"}
    parsed = urlparse(os.environ.get("ZX_EMBEDDING_BASE_URL", ""))
    try: port = parsed.port
    except ValueError: return {**observed, "status": "ERROR", "error_code": "EMBEDDING_ENDPOINT_INVALID"}
    allowed = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or parsed.hostname == "embedding" and port == 8000
    if parsed.scheme != "http" or not allowed or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/v1", "/embeddings", "/v1/embeddings"}:
        return {**observed, "status": "ERROR", "error_code": "EMBEDDING_ENDPOINT_INVALID"}
    endpoint = f"{parsed.scheme}://{parsed.netloc}/health"
    owned = client is None
    connection = client or httpx.Client(timeout=httpx.Timeout(4, connect=2), follow_redirects=False)
    try:
        with connection.stream("GET", endpoint) as response:
            if response.status_code != 200:
                return {**observed, "status": "UNAVAILABLE", "error_code": "EMBEDDING_HEALTH_HTTP_ERROR"}
            data, length = [], 0
            for piece in response.iter_bytes():
                length += len(piece)
                if length > 16000: raise ValueError
                data.append(piece)
            import json
            payload = json.loads(b"".join(data))
        if not isinstance(payload, dict) or payload.get("model") != config["model"] or payload.get("status") not in {"STARTING", "READY", "ERROR"} or payload.get("chat_model") is not False or payload.get("generation_supported") is not False:
            raise ValueError
        dimension = payload.get("dimension")
        revision = payload.get("model_revision")
        if type(dimension) is not int or not 1 <= dimension <= 4096: raise ValueError
        if payload["status"] == "READY" and (not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision)): raise ValueError
        expected_dimension, expected_revision = os.getenv("ZX_EMBEDDING_DIMENSION", "").strip(), os.getenv("ZX_EMBEDDING_MODEL_REVISION", "").strip()
        if expected_dimension and (not expected_dimension.isdigit() or int(expected_dimension) != dimension): raise ValueError
        if expected_revision and revision != expected_revision: raise ValueError
        safe_errors = {"MODEL_ARTIFACTS_MISSING", "MODEL_ARTIFACT_CHANGED", "MODEL_PROBE_INVALID", "MODEL_TOKENIZER_BUDGET_UNKNOWN", "MODEL_DOWNLOAD_OR_LOAD_FAILED"}
        return {**observed, "status": payload["status"], "model": config["model"], "dimension": dimension,
                "model_revision": revision if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{64}", revision) else None,
                "error_code": payload.get("error_code") if payload.get("error_code") in safe_errors else "EMBEDDING_HEALTH_ERROR" if payload["status"] == "ERROR" else None}
    except (ValueError, TypeError, KeyError):
        return {**observed, "status": "ERROR", "error_code": "EMBEDDING_HEALTH_RESPONSE_INVALID"}
    except httpx.HTTPError:
        return {**observed, "status": "UNAVAILABLE", "error_code": "EMBEDDING_HEALTH_TRANSPORT"}
    finally:
        if owned: connection.close()


def install_knowledge_routes(app, get_session, get_principal, knowledge_factory=None):
    """The optional factory is for isolated tests; no bank session is written."""
    def authorized(session, principal, admin=False):
        return require_account_role(session, principal, {"ADMIN"} if admin else
                                    {"CUSTOMER", "DEV_TEST", "FAMILY_HELPER", "ADMIN"})

    def factory():
        return knowledge_factory or _configured_factory()

    def checked(action):
        try:
            with factory().begin() as knowledge:
                return action(knowledge)
        except DomainError:
            raise
        except (ValueError, KeyError):
            raise DomainError("KNOWLEDGE_INVALID_CHANGE", "知识变更未通过核验，请重新读取当前版本", 409) from None
        except Exception:
            raise DomainError("KNOWLEDGE_UNAVAILABLE", "知识服务暂不可用，未确认本次变更结果", 503) from None

    def audit(knowledge, actor, action, *, document_id=None, revision_id=None, reason="", content_hash=None):
        from .knowledge_store import record_knowledge_audit
        record_knowledge_audit(knowledge, actor_account_id=actor.id, action=action,
                              document_id=document_id, revision_id=revision_id, reason=reason, content_hash=content_hash)

    @app.get("/api/v3/customer/knowledge/products")
    def products(language: Literal["zh-CN", "en"] = "zh-CN",
                 session=Depends(get_session), principal=Depends(get_principal)):
        authorized(session, principal)
        return {"items": product_catalog(language), "catalog_version": CATALOG_VERSION,
                "read_only": True, "bank_confirmation_permitted": False}

    @app.get("/api/v3/customer/knowledge/products/{product_key}")
    def product(product_key: str, language: Literal["zh-CN", "en"] = "zh-CN",
                session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal)
        item = product_detail(product_key, language)
        if item is None:
            raise DomainError("PRODUCT_LITERATURE_NOT_FOUND", "产品说明不存在", 404)
        def terms(knowledge):
            from sqlalchemy import select
            from .knowledge_models import KnowledgeDocument, KnowledgeRevision, knowledge_now
            from .knowledge_store import aware, content_hash
            doc = knowledge.scalar(select(KnowledgeDocument).where(KnowledgeDocument.document_key == item["terms_document_key"]))
            if doc is None or doc.state != "PUBLISHED" or actor.role not in doc.allowed_roles or doc.kind != "PRODUCT_TERMS":
                return None
            revision = knowledge.get(KnowledgeRevision, doc.active_revision_id) if doc.active_revision_id else None
            now = knowledge_now()
            if revision is None or revision.state != "PUBLISHED" or revision.document_id != doc.id or aware(revision.effective_from) > now \
                    or revision.expires_at and aware(revision.expires_at) <= now or content_hash(revision.content) != revision.content_hash:
                return None
            return {"source_id": "knowledge:" + doc.document_key, "title": doc.title,
                    "source_uri": doc.source_uri, "version": revision.version_label,
                    "content_hash": revision.content_hash, "content": revision.content,
                    "effective_from": aware(revision.effective_from).isoformat(),
                    "expires_at": aware(revision.expires_at).isoformat() if revision.expires_at else None,
                    "guidance_only": True, "execution_authority": False}
        return {"product": item, "public_terms": checked(terms),
                "read_only": True, "bank_confirmation_permitted": False}

    @app.get("/api/v2/admin/knowledge/status")
    def status(session=Depends(get_session), principal=Depends(get_principal)):
        authorized(session, principal, True)
        from .knowledge_store import knowledge_status
        def read(knowledge):
            from sqlalchemy import inspect, select
            from .knowledge_index_runner import runner_status
            from .knowledge_models import KnowledgeRetrievalTrace
            from .retrieval_service import _candidates
            from .embedding_gateway import validate_vector
            result = knowledge_status(knowledge)
            result["worker"] = runner_status(knowledge) if inspect(knowledge.connection()).has_table("knowledge_runner_state") else {"state": "NOT_OBSERVED", "online": False, "production": knowledge.bind.dialect.name == "postgresql"}
            recent = knowledge.scalar(select(KnowledgeRetrievalTrace).order_by(KnowledgeRetrievalTrace.created_at.desc(), KnowledgeRetrievalTrace.id).limit(1))
            result["last_retrieval_mode"] = recent.mode if recent else "NOT_OBSERVED"
            fingerprints = set()
            invalid = False
            for _doc, _revision, chunk in _candidates(knowledge, ["CUSTOMER", "DEV_TEST", "ADMIN", "FAMILY_HELPER"]):
                if chunk.embedding is None: continue
                try:
                    vector = validate_vector(chunk.embedding, chunk.embedding_model, chunk.embedding_revision)
                    fingerprints.add((vector.model, vector.model_revision, vector.dimension))
                except (ValueError, TypeError): invalid = True
            result["_index_fingerprints"] = fingerprints
            result["_index_invalid"] = invalid
            return result
        result = checked(read)
        session.rollback()  # Local health HTTP must not hold bank authentication reads.
        health = probe_local_embedding_health()
        signatures, invalid = result.pop("_index_fingerprints"), result.pop("_index_invalid")
        local = result["embedding"]["provider"] == "LOCAL_HTTP"
        ready = result["semantic_ready"] and not invalid
        if local:
            ready = ready and health["status"] == "READY" and signatures == {(health["model"], health["model_revision"], health["dimension"])}
        result["semantic_ready"] = bool(ready)
        result["embedding_health"] = health
        failed = any(job["state"] in {"FAILED", "STALE", "UNCONFIGURED"} for job in result["jobs"][:1])
        result["index_state"] = "READY" if ready else "EMPTY" if not result["active_chunks"] else "ERROR" if invalid or local and health["status"] in {"ERROR", "UNAVAILABLE"} or failed else "PENDING"
        return result

    @app.get("/api/v2/admin/knowledge/documents")
    def documents(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                  session=Depends(get_session), principal=Depends(get_principal)):
        authorized(session, principal, True)
        from .knowledge_store import list_documents
        def read(knowledge):
            from .knowledge_models import KnowledgeDocument
            from sqlalchemy import func, select
            items = list_documents(knowledge, limit=page_size, offset=(page - 1) * page_size)
            total = knowledge.scalar(select(func.count()).select_from(KnowledgeDocument))
            return {"items": items, "total": total,
                    "page": page, "page_size": page_size, "bank_confirmation_permitted": False}
        return checked(read)

    @app.get("/api/v2/admin/knowledge/audit")
    def knowledge_audit(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                        session=Depends(get_session), principal=Depends(get_principal)):
        authorized(session, principal, True)
        def read(knowledge):
            from sqlalchemy import func, select
            from .knowledge_models import KnowledgeAudit
            from .knowledge_store import aware
            total = knowledge.scalar(select(func.count()).select_from(KnowledgeAudit))
            rows = knowledge.scalars(select(KnowledgeAudit).order_by(KnowledgeAudit.created_at.desc(), KnowledgeAudit.id)
                                     .offset((page - 1) * page_size).limit(page_size))
            return {"items": [{"audit_id": row.id, "action": row.action, "document_id": row.document_id,
                               "revision_id": row.revision_id, "content_hash": row.content_hash,
                               "reason": row.reason, "created_at": aware(row.created_at).isoformat()} for row in rows],
                    "total": total, "page": page, "page_size": page_size, "read_only": True}
        return checked(read)

    @app.get("/api/v2/admin/knowledge/documents/{document_id}")
    def document(document_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        authorized(session, principal, True)
        from .knowledge_store import document_detail
        return checked(lambda knowledge: document_detail(knowledge, document_id))

    @app.post("/api/v2/admin/knowledge/documents", status_code=201)
    def create(payload: KnowledgeDraft, session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        from .knowledge_store import create_revision, document_detail, upsert_document
        def change(knowledge):
            # Creating a key must not silently edit an already-published source.
            from .knowledge_models import KnowledgeDocument
            from sqlalchemy import select
            existing = knowledge.scalar(select(KnowledgeDocument).where(KnowledgeDocument.document_key == payload.document_key))
            if existing is not None:
                raise DomainError("KNOWLEDGE_DOCUMENT_EXISTS", "资料编号已存在，请新增修订版本", 409)
            doc = upsert_document(knowledge, document_key=payload.document_key, title=payload.title,
                                  source_uri=payload.source_uri, kind=payload.kind, language=payload.language,
                                  allowed_roles=["CUSTOMER", "DEV_TEST", "FAMILY_HELPER", "ADMIN"],
                                  metadata={"description": "Admin-reviewed public explanatory knowledge; no execution authority."})
            revision = create_revision(knowledge, doc.id, payload.content, payload.version_label,
                                       effective_from=payload.effective_from, expires_at=payload.expires_at)
            audit(knowledge, actor, "CREATE_DOCUMENT", document_id=doc.id, revision_id=revision.id, content_hash=revision.content_hash)
            return document_detail(knowledge, doc.id)
        return checked(change)

    @app.post("/api/v2/admin/knowledge/documents/{document_id}/revisions", status_code=201)
    def revision(document_id: str, payload: KnowledgeRevisionDraft,
                 session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        from .knowledge_store import create_revision, document_detail
        def change(knowledge):
            row = create_revision(knowledge, document_id, payload.content, payload.version_label,
                                  effective_from=payload.effective_from, expires_at=payload.expires_at)
            audit(knowledge, actor, "CREATE_REVISION", document_id=document_id, revision_id=row.id, content_hash=row.content_hash)
            return document_detail(knowledge, document_id)
        return checked(change)

    @app.post("/api/v2/admin/knowledge/revisions/{revision_id}/publish")
    def publish(revision_id: str, payload: KnowledgePublish,
                session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        from .knowledge_store import publish_revision, document_detail
        def change(knowledge):
            row = publish_revision(knowledge, revision_id, payload.expected_content_hash)
            audit(knowledge, actor, "PUBLISH", document_id=row.document_id, revision_id=row.id, content_hash=row.content_hash)
            return document_detail(knowledge, row.document_id)
        return checked(change)

    @app.post("/api/v2/admin/knowledge/documents/{document_id}/retire")
    def retire(document_id: str, payload: KnowledgeRetire,
               session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        from .knowledge_store import retire_document, document_detail
        def change(knowledge):
            retire_document(knowledge, document_id, payload.reason)
            audit(knowledge, actor, "RETIRE", document_id=document_id, reason=payload.reason)
            return document_detail(knowledge, document_id)
        return checked(change)

    @app.post("/api/v2/admin/knowledge/index-jobs", status_code=202)
    def rebuild(payload: KnowledgeIndexRequest,
                session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        from .knowledge_ingest import create_index_job
        def change(knowledge):
            job = create_index_job(knowledge, revision_ids=payload.revision_ids)
            audit(knowledge, actor, "REINDEX")
            return {"job_id": job.id, "status": job.state, "bank_confirmation_permitted": False}
        result = checked(change)
        # Durable QUEUED state is executed by the independent index worker.
        # API restart no longer loses an in-process BackgroundTasks callback.
        session.rollback()
        return result

    @app.post("/api/v2/admin/knowledge/index-jobs/{job_id}/retry", status_code=202)
    def retry_index(job_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        from .knowledge_ingest import retry_index_job
        def change(knowledge):
            row = retry_index_job(knowledge, job_id)
            audit(knowledge, actor, "REINDEX", reason="Explicit retry " + job_id + " as new job " + row.id)
            return {"job_id": row.id, "prior_job_id": job_id, "status": row.state, "bank_confirmation_permitted": False}
        return checked(change)

    @app.get("/api/v2/admin/knowledge/index-jobs/{job_id}")
    def index_details(job_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        authorized(session, principal, True)
        def read(knowledge):
            from sqlalchemy import inspect, select
            from .knowledge_models import KnowledgeIndexJob
            from .knowledge_index_runner import KnowledgeIndexAttempt
            from .knowledge_store import aware, job_public
            row = knowledge.get(KnowledgeIndexJob, job_id)
            if row is None: raise DomainError("KNOWLEDGE_INDEX_NOT_FOUND", "索引任务不存在", 404)
            attempts = list(knowledge.scalars(select(KnowledgeIndexAttempt).where(KnowledgeIndexAttempt.job_id == job_id)
                            .order_by(KnowledgeIndexAttempt.started_at.desc()).limit(20))) if inspect(knowledge.connection()).has_table("knowledge_index_attempts") else []
            return {"job": job_public(row), "attempts": [{"attempt_id": attempt.id, "state": attempt.state,
                "started_at": aware(attempt.started_at).isoformat(), "finished_at": aware(attempt.finished_at).isoformat() if attempt.finished_at else None,
                "error_code": attempt.error_code} for attempt in attempts], "read_only": True}
        return checked(read)

    @app.post("/api/v2/admin/knowledge/search")
    def search(payload: KnowledgeSearch, session=Depends(get_session), principal=Depends(get_principal)):
        actor = authorized(session, principal, True)
        role = actor.role
        session.rollback()  # Do not hold the bank's auth read across inference.
        from .retrieval_service import retrieve_knowledge
        # The high-level service computes embeddings before opening its DB
        # transaction. Never keep an administration transaction across inference.
        try:
            return retrieve_knowledge(factory(), [role], payload.query, limit=payload.limit).public()
        except DomainError:
            raise
        except Exception:
            raise DomainError("KNOWLEDGE_UNAVAILABLE", "知识检索暂不可用", 503) from None
