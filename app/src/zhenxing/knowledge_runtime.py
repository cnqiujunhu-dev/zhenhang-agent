"""Dialogue bridge to the separate local public-knowledge database."""
import json
import os
import threading

_lock = threading.Lock()
_stores = {}


def knowledge_factory():
    url = os.getenv("ZX_KNOWLEDGE_DATABASE_URL", "").strip()
    if not url:
        return None
    with _lock:
        if url not in _stores:
            from .knowledge_store import init_knowledge_store
            # The independent index runner owns schema/bootstrap. Customer
            # requests must not race DDL or silently create an empty corpus.
            _stores[url] = init_knowledge_store(url, create_schema=False)
        return _stores[url][1]


def directory_source(role, locale):
    from .capability_catalog import customer_directory
    from .customer_capability_data import CUSTOMER_DIRECTORY_VERSION
    rows = customer_directory(role, locale)
    return {"source_id": "directory:" + CUSTOMER_DIRECTORY_VERSION + ":" + role + ":" + locale,
        "kind": "CUSTOMER_CAPABILITY_DIRECTORY", "title": "当前开放服务" if locale != "en" else "Available services",
        "title_en": "Available services", "version": CUSTOMER_DIRECTORY_VERSION,
        "content": json.dumps(rows, ensure_ascii=False), "content_en": json.dumps(rows, ensure_ascii=False),
        "role": role, "bank_authoritative": False}


def retrieve_dialogue_knowledge(message, role):
    """Network embedding occurs here, outside any banking transaction."""
    try:
        factory = knowledge_factory()
        if factory is None:
            return [], {"knowledge_status": "UNCONFIGURED", "retrieval_mode": "BUILTIN_KNOWLEDGE"}
        from .retrieval_service import retrieve_knowledge
        result = retrieve_knowledge(factory, [role], message, limit=6)
        return result.sources, {"knowledge_status": result.status, "retrieval_mode": result.mode,
            "retrieval_policy":result.relevance_policy,
            "retrieval_trace_id": result.trace_id, "embedding_status": result.embedding_status,
            "embedding_error_code": result.embedding_error_code}
    except Exception:
        return [], {"knowledge_status": "UNAVAILABLE", "retrieval_mode": "BUILTIN_KNOWLEDGE",
            "knowledge_error_code": "KNOWLEDGE_STORE_UNAVAILABLE"}


def knowledge_sources_current(sources, role):
    rows = [row for row in sources if isinstance(row, dict) and row.get("chunk_id")]
    if not rows:
        return True
    try:
        factory = knowledge_factory()
        if factory is None:
            return False
        from .retrieval_service import sources_still_current
        with factory() as session:
            return sources_still_current(session, rows, principal_roles=[role])
    except Exception:
        return False
