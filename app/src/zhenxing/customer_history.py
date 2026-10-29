"""Actor-owned read-only history index, independent of legacy listing windows.

Every owned conversation is retained. An owned runtime PARENT task belongs to
exactly one canonical conversation, chosen from complete owned turn links, not
the currently loaded turn page or the thread's current task alone. Unlinked
parent tasks remain independently openable. No assistant answer, banking result,
source content or credential is used as a customer preview/search document.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
import secrets
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import load_only

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .models import Task
from .permissions import require_banking_actor
from .redaction import redact_credentials
from .runtime_models import RuntimeRecord
from .simulation import as_utc
from .customer_history_archive import visible_history


HISTORY_VERSION = "customer-history-v1"
_CURSOR_KEY = secrets.token_bytes(32)
_CURSOR_MAX_LENGTH = 4096


def _cursor_key():
    """Dedicated shared server secret, never a model/API/session credential.

    Production workers set the same stable key. The local unconfigured fallback
    is deliberately process-scoped: a restart invalidates paging and the client
    starts at page one. No read endpoint creates files or persists a key.
    """
    configured = os.getenv("ZX_HISTORY_CURSOR_SECRET")
    if configured is not None:
        if len(configured.encode("utf-8")) < 32:
            raise DomainError("HISTORY_CURSOR_NOT_CONFIGURED", "历史翻页服务配置尚未就绪。", 503)
        return configured.encode("utf-8")
    if os.getenv("ZX_HISTORY_CURSOR_REQUIRE_SHARED_KEY", "0") == "1":
        raise DomainError("HISTORY_CURSOR_NOT_CONFIGURED", "历史翻页服务配置尚未就绪。", 503)
    return _CURSOR_KEY


def _text(value, maximum=None):
    result = redact_credentials(value) if isinstance(value, str) else ""
    return result[:maximum] if maximum is not None else result


def _iso(value):
    return as_utc(value).isoformat()


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    return base64.b64decode(value + "="*((-len(value)) % 4), altchars=b"-_", validate=True)


def _invalid_cursor():
    return DomainError("HISTORY_CURSOR_INVALID", "历史翻页标识已失效，请从第一页重新查看。", 400)


def _encode_cursor(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _b64(raw) + "." + _b64(hmac.new(_cursor_key(), raw, hashlib.sha256).digest())


def _decode_cursor(token, *, actor, query, limit, digest):
    signing_key = _cursor_key()
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= _CURSOR_MAX_LENGTH:
            raise ValueError()
        encoded, signature = token.split(".")
        raw = _unb64(encoded)
        supplied = _unb64(signature)
        if not hmac.compare_digest(supplied, hmac.new(signing_key, raw, hashlib.sha256).digest()):
            raise ValueError()
        payload = json.loads(raw)
        if (not isinstance(payload, dict) or set(payload) != {"version", "actor", "query", "limit", "digest", "offset"}
                or payload["version"] != HISTORY_VERSION or payload["actor"] != actor
                or payload["query"] != query or type(payload["limit"]) is not int or payload["limit"] != limit
                or type(payload["offset"]) is not int or payload["offset"] <= 0
                or not isinstance(payload["digest"], str)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise _invalid_cursor() from None
    if not hmac.compare_digest(payload["digest"], digest):
        raise DomainError("HISTORY_CHANGED", "历史记录已更新，请从第一页重新查看，以免漏掉或重复显示。", 409)
    return payload["offset"]


def _credential_only_sql(session):
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import cast, literal
        from sqlalchemy.dialects.postgresql import JSONB
        # Cast only the selected scalar, not a legacy hundred-MB JSON document.
        return cast(ChatTurn.diagnostics_json["credential_only"], JSONB) == cast(literal("true"), JSONB)
    from sqlalchemy import func
    return func.json_type(ChatTurn.diagnostics_json, "$.credential_only") == "true"


def _owned_index(session, principal):
    """Full scoped source set before grouping/search/pagination, with no caps."""
    threads = list(session.scalars(select(ChatThread).options(load_only(ChatThread.id, ChatThread.title,
        ChatThread.current_task_id, ChatThread.created_at, ChatThread.updated_at)).where(ChatThread.user_id == principal.user_id,
        ChatThread.sandbox_id == principal.sandbox_id, visible_history(session, principal, ChatThread.created_at))))
    tasks = list(session.scalars(select(Task).options(load_only(Task.id, Task.message, Task.state,
        Task.created_at, Task.updated_at)).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(
        Task.user_id == principal.user_id, Task.sandbox_id == principal.sandbox_id, RuntimeRecord.kind == "PARENT",
        visible_history(session, principal, Task.created_at))))
    credential_only = _credential_only_sql(session)
    rows = session.execute(select(ChatTurn.id, ChatTurn.thread_id, ChatTurn.sequence, ChatTurn.message,
        ChatTurn.task_id, ChatTurn.created_at, ChatTurn.updated_at, credential_only.label("credential_only"))
        .join(ChatThread, ChatThread.id == ChatTurn.thread_id).where(
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatThread.user_id == principal.user_id, ChatThread.sandbox_id == principal.sandbox_id,
        visible_history(session, principal, ChatThread.created_at)).order_by(
        ChatTurn.created_at, ChatTurn.sequence, ChatTurn.id, ChatTurn.thread_id)).mappings()
    turns = [SimpleNamespace(**{key: row[key] for key in row if key != "credential_only"},
        diagnostics_json={"credential_only": row["credential_only"] is True}) for row in rows]
    threads_by_id = {thread.id: thread for thread in threads}
    tasks_by_id = {task.id: task for task in tasks}
    canonical = {}
    per_thread_turns = {thread.id: [] for thread in threads}
    for turn in turns:
        per_thread_turns[turn.thread_id].append(turn)
        # requested_task_id is a client selection, not proof that a failed or
        # queued turn was actually linked. It cannot claim canonical ownership.
        if turn.task_id in tasks_by_id:
            canonical.setdefault(turn.task_id, turn.thread_id)
    # A pointer is a compatibility fallback only. It cannot steal a task that
    # is already linked by any older turn in any owned conversation.
    for thread in sorted(threads, key=lambda row: (as_utc(row.created_at), row.id)):
        if thread.current_task_id in tasks_by_id:
            canonical.setdefault(thread.current_task_id, thread.id)
    grouped_tasks = {thread.id: [] for thread in threads}
    standalone = []
    for task in tasks:
        if task.id in canonical:
            grouped_tasks[canonical[task.id]].append(task)
        else:
            standalone.append(task)
    entries = []
    search_documents = {}
    for thread in threads:
        linked = grouped_tasks[thread.id]
        thread_turns = per_thread_turns[thread.id]
        latest_turn = max(thread_turns, key=lambda row: (row.sequence, as_utc(row.created_at), row.id)) if thread_turns else None
        latest_task = max(linked, key=lambda row: (as_utc(row.updated_at), row.id)) if linked else None
        counts = {}
        for task in linked:
            counts[task.state] = counts.get(task.state, 0) + 1
        credential_only = bool(latest_turn and isinstance(latest_turn.diagnostics_json, dict)
            and latest_turn.diagnostics_json.get("credential_only") is True)
        preview = ("安全验证信息已隐藏。" if credential_only else _text(latest_turn.message, 200)) if latest_turn else ""
        updated = max([as_utc(thread.updated_at)] + [as_utc(task.updated_at) for task in linked]
            + [as_utc(turn.updated_at) for turn in thread_turns])
        entry_id = "CONVERSATION:" + thread.id
        entries.append({"entry_id": entry_id, "kind": "CONVERSATION", "conversation_id": thread.id,
            "task_id": None, "title": _text(thread.title, 80), "preview": preview,
            "created_at": _iso(thread.created_at), "updated_at": _iso(updated), "task_count": len(linked),
            "task_state_counts": dict(sorted(counts.items())), "latest_task_state": latest_task.state if latest_task else None})
        search_documents[entry_id] = "\n".join([_text(thread.title)] + [
            _text(turn.message) for turn in thread_turns if not (isinstance(turn.diagnostics_json, dict)
                and turn.diagnostics_json.get("credential_only") is True)] + [_text(task.message) for task in linked]).casefold()
    for task in standalone:
        entry_id = "TASK:" + task.id
        entries.append({"entry_id": entry_id, "kind": "TASK", "conversation_id": None, "task_id": task.id,
            "title": _text(task.message.splitlines()[0] if task.message else "办理记录", 80),
            "preview": _text(task.message, 200), "created_at": _iso(task.created_at), "updated_at": _iso(task.updated_at),
            "task_count": 1, "task_state_counts": {task.state: 1}, "latest_task_state": task.state})
        search_documents[entry_id] = _text(task.message).casefold()
    # First stabilize tie-breaking by ID, then by recency (Python sort is stable).
    entries.sort(key=lambda row: row["entry_id"])
    entries.sort(key=lambda row: datetime.fromisoformat(row["updated_at"]), reverse=True)
    canonical_payload = {"items": entries, "search_documents": search_documents,
        "task_groups": {task_id: canonical.get(task_id) for task_id in sorted(tasks_by_id)}}
    digest = hashlib.sha256(json.dumps(canonical_payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return entries, search_documents, canonical, tasks_by_id, threads_by_id, digest


def customer_history(session, principal, *, q="", limit=30, cursor=None):
    role = require_banking_actor(session, principal)
    _cursor_key()  # Strict shared-key deployment configuration is fail-closed.
    if not isinstance(q, str) or len(q) > 120 or type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("HISTORY_QUERY_INVALID", "历史搜索或分页参数无效。", 422)
    if redact_credentials(q) != q:
        raise DomainError("HISTORY_QUERY_INVALID", "请使用业务关键词搜索，避免输入安全凭据。", 422)
    query = q.strip()
    actor = {"user_id": principal.user_id, "sandbox_id": principal.sandbox_id, "role": role}
    entries, documents, _canonical, _tasks, _threads, digest = _owned_index(session, principal)
    offset = _decode_cursor(cursor, actor=actor, query=query, limit=limit, digest=digest) if cursor is not None else 0
    matches = [entry for entry in entries if not query or query.casefold() in documents[entry["entry_id"]]]
    if offset > len(matches):
        raise _invalid_cursor()
    items = matches[offset:offset+limit]
    has_more = offset + len(items) < len(matches)
    next_cursor = _encode_cursor({"version": HISTORY_VERSION, "actor": actor, "query": query,
        "limit": limit, "digest": digest, "offset": offset+len(items)}) if has_more else None
    return {"schema_version": HISTORY_VERSION, "actor": actor, "query": query, "items": items,
        "has_more": has_more, "next_cursor": next_cursor}


def resolve_history_task(session, principal, task_id):
    """Precise canonical destination without depending on the loaded page."""
    role = require_banking_actor(session, principal)
    _entries, _documents, canonical, tasks, _threads, _digest = _owned_index(session, principal)
    if task_id not in tasks:
        raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
    conversation_id = canonical.get(task_id)
    return {"schema_version": HISTORY_VERSION, "actor": {"user_id": principal.user_id,
        "sandbox_id": principal.sandbox_id, "role": role}, "task_id": task_id,
        "kind": "CONVERSATION" if conversation_id else "TASK", "conversation_id": conversation_id,
        "entry_id": ("CONVERSATION:" + conversation_id) if conversation_id else "TASK:" + task_id}
