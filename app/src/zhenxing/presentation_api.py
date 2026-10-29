"""Customer business presentation and separately authorized admin chat evidence.

Legacy technical APIs are retained for compatibility. These projections do not
revoke their access, and they never alter plan/confirmation or signed evidence.
"""
from typing import Literal

from fastapi import Depends, Query, Request
from sqlalchemy import and_, func, or_, select

from .access_models import LoginAccount
from .accounts import require_account_role
from .admin_api import redact_observation
from .contracts import DomainError
from .conversation_api import ChatTurnRequest, CreateConversationRequest, RetryUnderstandingRequest
from .conversation_models import ChatThread, ChatTurn
from .conversation_service import (close_conversation, conversation_view, create_conversation,
    submit_turn, thread_view, turn_view)
from .customer_presentation import (customer_conversation_view, customer_task_view,
    customer_thread_view, customer_turn_view, validated_customer_read_coverage)
from .models import Task
from .permissions import require_banking_actor
from .product_knowledge import public_sources, sources_still_authorized
from .runtime import task_trace
from .runtime_models import RuntimeRecord
from .services import Principal
from .simulation import as_utc


_DIAGNOSTIC_KEYS = {
    "purpose", "authority", "routing_policy_version", "routing_mode", "actual_provider", "actual_model",
    "model_label", "model_api_call_count", "primary_attempt_count", "fallback", "failure_reasons",
    "reasoning_content_retained", "usage", "elapsed_ms", "selected_model", "response_model",
    "cited_source_ids", "rendered_fact_refs", "credential_only", "source_scope", "attempts",
}
_ATTEMPT_KEYS = {"provider", "model", "model_api_call_count", "error_code", "elapsed_ms", "http_status"}
_DROP_ADMIN_KEYS = {"reasoning", "chainofthought", "rawresponse", "rawprompt", "prompt", "headers",
                    "base64", "dataurl", "blobpath", "privatepath", "imagebytes", "messages"}


def _safe_admin_value(value):
    import re
    if isinstance(value, dict):
        value = {key: _safe_admin_value(item) for key, item in value.items()
                 if re.sub(r"[^a-z0-9]", "", str(key).lower()) not in _DROP_ADMIN_KEYS}
    elif isinstance(value, list):
        value = [_safe_admin_value(item) for item in value[:100]]
    return redact_observation(value)


def _diagnostics(value):
    value = value if isinstance(value, dict) else {}
    result = {key: value[key] for key in _DIAGNOSTIC_KEYS if key in value and key != "attempts"}
    if isinstance(value.get("attempts"), list):
        result["attempts"] = [{key: item[key] for key in _ATTEMPT_KEYS if key in item}
                              for item in value["attempts"][:16] if isinstance(item, dict)]
    return _safe_admin_value(result)


def _thread_summary(session, row):
    login = session.scalar(select(LoginAccount).where(LoginAccount.user_id == row.user_id,
                                                     LoginAccount.sandbox_id == row.sandbox_id))
    return _safe_admin_value({"conversation_id": row.id, "title": row.title, "status": row.status,
        "username": login.username if login else None, "user_id": row.user_id, "sandbox_id": row.sandbox_id,
        "current_task_id": row.current_task_id,
        "turn_count": session.scalar(select(func.count()).select_from(ChatTurn).where(ChatTurn.thread_id == row.id,
            ChatTurn.user_id == row.user_id, ChatTurn.sandbox_id == row.sandbox_id)),
        "created_at": as_utc(row.created_at).isoformat(), "updated_at": as_utc(row.updated_at).isoformat()})


def _admin_turn(session, row):
    authorized = sources_still_authorized(session, Principal(row.user_id, row.sandbox_id), row.sources_json)
    diagnostics = _diagnostics(row.diagnostics_json)
    if not authorized:
        diagnostics.pop("cited_source_ids", None)
        diagnostics.pop("rendered_fact_refs", None)
    return _safe_admin_value({"turn_id": row.id, "conversation_id": row.thread_id, "sequence": row.sequence,
        "message": row.message, "status": row.status, "mode": row.mode,
        "answer": row.answer if authorized else "此前授权资料的读取范围已失效，历史解释内容已隐藏。",
        "task_id": row.task_id, "requested_task_id": row.requested_task_id,
        "task_relationship": row.task_relationship, "sources_authorized": authorized,
        "sources": public_sources(row.sources_json) if authorized else [],
        "field_sources": row.field_sources_json if authorized else [], "diagnostics": diagnostics,
        "error_code": row.error_code, "created_at": as_utc(row.created_at).isoformat(),
        "updated_at": as_utc(row.updated_at).isoformat(),
        "completed_at": as_utc(row.completed_at).isoformat() if row.completed_at else None})


def _evidence_page(session, query, page, page_size, *, scope, task_id=None, conversation_id=None):
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = list(session.scalars(query.order_by(ChatTurn.created_at.desc(), ChatTurn.id)
                               .offset((page - 1) * page_size).limit(page_size)))
    thread_ids = {row.thread_id for row in rows}
    if conversation_id:
        thread_ids.add(conversation_id)
    threads = list(session.scalars(select(ChatThread).where(ChatThread.id.in_(thread_ids))
                                   .order_by(ChatThread.created_at.desc(), ChatThread.id))) if thread_ids else []
    result = {"scope": scope, "conversations": [_thread_summary(session, row) for row in threads],
              "items": [_admin_turn(session, row) for row in rows], "total": total,
              "page": page, "page_size": page_size, "read_only": True, "bank_confirmation_permitted": False}
    if task_id:
        result["task_id"] = task_id
    if conversation_id:
        result["conversation_id"] = conversation_id
    return result


def _authorized_stored_fallbacks(session, principal, conversation_id, turn_views, *, loaded_rows=None):
    """The existing turn_view has already checked live source permissions/TTL.

Match exact public records and actor/thread IDs before handing stored content to
the formatter. An invalidated view has no sources and cannot regain its answer.
"""
    views = {view["turn_id"]: view for view in turn_views}
    rows = loaded_rows if loaded_rows is not None else session.scalars(select(ChatTurn).where(ChatTurn.id.in_(views), ChatTurn.thread_id == conversation_id,
        ChatTurn.sandbox_id == principal.sandbox_id, ChatTurn.user_id == principal.user_id)) if views else []
    supplied = {}
    for row in rows:
        if row.id not in views or (row.thread_id, row.user_id, row.sandbox_id) != (conversation_id, principal.user_id, principal.sandbox_id):
            continue
        view = views[row.id]
        if row.status == "ANSWERED" and row.mode == "CONSULT" and row.answer == view.get("answer") \
                and public_sources(row.sources_json) == view.get("sources"):
            supplied[row.id] = row.sources_json
    return supplied


def _read_snapshot_markers(session, principal, conversation_id, projected):
    from .customer_read_history import customer_read_snapshot_status
    return {**projected, **customer_read_snapshot_status(session,principal,conversation_id,projected['turn_id'])}


def install_presentation_routes(app, get_session, get_principal):
    @app.get("/api/v3/customer/history")
    def history(q: str = Query(default="", max_length=120), limit: int = Query(default=30, ge=1, le=100),
                cursor: str | None = Query(default=None, min_length=1, max_length=4096),
                session=Depends(get_session), principal=Depends(get_principal)):
        from .customer_history import customer_history
        return customer_history(session, principal, q=q, limit=limit, cursor=cursor)

    @app.get("/api/v3/customer/history/tasks/{task_id}/resolve")
    def resolve_history(task_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        from .customer_history import resolve_history_task
        return resolve_history_task(session, principal, task_id)

    @app.get("/api/v3/customer/tasks")
    def customer_tasks(session=Depends(get_session), principal=Depends(get_principal)):
        require_banking_actor(session, principal)
        from .customer_history_archive import visible_history
        rows = session.scalars(select(Task).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(
            RuntimeRecord.kind == "PARENT", Task.sandbox_id == principal.sandbox_id,
            Task.user_id == principal.user_id, visible_history(session, principal, Task.created_at))
            .order_by(Task.updated_at.desc(), Task.id).limit(50))
        return [{"task_id": task.id, "state": task.state, "version": task.version, "message": task.message,
                 "intent_type": task.intent_type, "created_at": as_utc(task.created_at).isoformat(),
                 "updated_at": as_utc(task.updated_at).isoformat()} for task in rows]

    @app.get("/api/v3/customer/tasks/{task_id}")
    def customer_task(task_id: str, request: Request, session=Depends(get_session), principal=Depends(get_principal)):
        require_banking_actor(session, principal)
        from .customer_history_archive import require_visible_history
        require_visible_history(session, principal, Task, task_id)
        from .language import normalize_language
        requested = request.headers.get("Accept-Language")
        trace = task_trace(session, principal, task_id)
        from .credit_status_display import attach_credit_status_display
        trace = attach_credit_status_display(session, principal, session.get(Task, task_id), trace)
        read_coverage = validated_customer_read_coverage(session, principal, trace)
        from .customer_bill_fallback import prepare_customer_bill_supplement
        from .models import utcnow
        supplementary = prepare_customer_bill_supplement(session, principal, trace)
        from .conversation_service import current_transfer_pause
        paused_plan = current_transfer_pause(session, principal, session.get(Task, task_id))
        return customer_task_view(trace,
            reply_language=normalize_language(requested) if requested and requested.strip() != "*" else None,
            supplementary_bill=supplementary,
            supplementary_bill_observed_at=utcnow().isoformat() if supplementary else None,
            paused_plan=paused_plan, read_coverage_by_operation=read_coverage)

    @app.post("/api/v3/customer/conversations", status_code=201)
    def create(payload: CreateConversationRequest, session=Depends(get_session), principal=Depends(get_principal)):
        row = create_conversation(session, principal, payload.title, payload.client_request_id)
        session.commit()
        return customer_thread_view(thread_view(row))

    @app.get("/api/v3/customer/conversations")
    def conversations(session=Depends(get_session), principal=Depends(get_principal)):
        require_banking_actor(session, principal)
        rows = session.scalars(select(ChatThread).where(ChatThread.sandbox_id == principal.sandbox_id,
            ChatThread.user_id == principal.user_id).order_by(ChatThread.updated_at.desc(), ChatThread.id).limit(100))
        return {"items": [customer_thread_view(thread_view(row)) for row in rows]}

    @app.get("/api/v3/customer/conversations/acknowledgement")
    def acknowledgement(client_request_id: str = Query(min_length=1, max_length=120),
                        conversation_id: str | None = Query(default=None, min_length=1, max_length=36),
                        conversation_request_id: str | None = Query(default=None, min_length=1, max_length=120),
                        session=Depends(get_session), principal=Depends(get_principal)):
        """Lookup only: no registration, planning, submission, confirmation or commit.

        A missing record is observational uncertainty, not proof of rejection.
        Both actor IDs are present even when the caller supplies a thread ID.
        """
        import re
        require_banking_actor(session, principal)
        if bool(conversation_id) == bool(conversation_request_id) or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", key)
                for key in (client_request_id, conversation_request_id) if key is not None):
            raise DomainError("CHAT_ACK_LOOKUP_INVALID", "请提供当前身份的会话与消息受理标识", 422)
        query = select(ChatThread).where(ChatThread.sandbox_id == principal.sandbox_id,
                                         ChatThread.user_id == principal.user_id)
        query = query.where(ChatThread.id == conversation_id) if conversation_id else query.where(
            ChatThread.client_request_id == conversation_request_id)
        row = session.scalar(query)
        if row is None:
            raise DomainError("CHAT_ACK_LOOKUP_UNKNOWN", "当前身份尚未查到完整受理记录，结果仍未确定", 404)
        turn_id = session.scalar(select(ChatTurn.id).where(ChatTurn.thread_id == row.id,
            ChatTurn.sandbox_id == principal.sandbox_id, ChatTurn.user_id == principal.user_id,
            ChatTurn.client_request_id == client_request_id))
        from .conversation_history_projection import build_history_projection, customer_unavailable_turn
        projection = build_history_projection(session, principal, row.id, customer=True, turn_id=turn_id) if turn_id else None
        view = projection.view["turns"][0] if projection and projection.view["turns"] else None
        projected = customer_turn_view(view, fallback_sources=projection.fallback_sources.get(turn_id)) if view else None
        if projected is not None:
            projected = (customer_unavailable_turn(view) if turn_id in projection.unavailable_turn_ids
                else _read_snapshot_markers(session, principal, row.id, projected))
        return {"status": "ACCEPTED" if view else "UNDETERMINED", "client_request_id": client_request_id,
            "conversation": customer_thread_view(thread_view(row)),
            "turn": projected,
            "read_only": True, "bank_confirmation_permitted": False}

    @app.get("/api/v3/customer/conversations/{conversation_id}")
    def conversation(conversation_id: str, before_sequence: int | None = Query(default=None, ge=1),
                     history_projection: Literal["bounded-history-v2"] = "bounded-history-v2",
                     session=Depends(get_session), principal=Depends(get_principal)):
        from .read_projection_request import read_projection_request
        with read_projection_request(session, principal):
            projection = conversation_view(session, principal, conversation_id, before_sequence, customer_projection=True)
            projected=customer_conversation_view(projection.view, fallback_sources_by_turn_id=projection.fallback_sources)
            from .conversation_history_projection import customer_unavailable_turn
            original_turns = {turn['turn_id']: turn for turn in projection.view['turns']}
            projected['turns']=[(customer_unavailable_turn(original_turns[turn['turn_id']]) if turn['turn_id'] in projection.unavailable_turn_ids
                else _read_snapshot_markers(session,principal,conversation_id,turn)) for turn in projected['turns']]
            projected['history_projection']=projection.view['history_projection']
            return projected

    @app.get("/api/v3/customer/conversations/{conversation_id}/turns/{turn_id}/read-results")
    def read_results(conversation_id: str, turn_id: str,
                     session=Depends(get_session), principal=Depends(get_principal)):
        from .customer_read_history import customer_read_results
        return customer_read_results(session,principal,conversation_id,turn_id)

    @app.post("/api/v3/customer/conversations/{conversation_id}/turns", status_code=202)
    def post(conversation_id: str, payload: ChatTurnRequest,
             session=Depends(get_session), principal=Depends(get_principal)):
        row = submit_turn(session, principal, conversation_id, **payload.model_dump())
        session.commit()
        view = turn_view(session, principal, row, include_diagnostics=False)
        supplied = _authorized_stored_fallbacks(session, principal, conversation_id, [view], loaded_rows=[row])
        return _read_snapshot_markers(session,principal,conversation_id,
            customer_turn_view(view, fallback_sources=supplied.get(row.id)))

    @app.post("/api/v3/customer/conversations/{conversation_id}/close")
    def close(conversation_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        row = close_conversation(session, principal, conversation_id)
        session.commit()
        return {**customer_thread_view(thread_view(row)), "notice": "会话已关闭，未取消银行任务或回滚已完成业务。"}

    @app.post('/api/v3/customer/conversations/{conversation_id}/turns/{turn_id}/retry-understanding', status_code=202)
    def retry_semantics(conversation_id: str, turn_id: str, payload: RetryUnderstandingRequest,
                        session=Depends(get_session), principal=Depends(get_principal)):
        from .semantic_recovery import retry_understanding
        row=retry_understanding(session,principal,conversation_id,turn_id,payload.expected_retry_version)
        session.commit()
        return customer_turn_view(turn_view(session,principal,row,include_diagnostics=False))

    @app.get("/api/v2/admin/conversations")
    def admin_conversations(page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                            username: str | None = Query(default=None, max_length=32),
                            session=Depends(get_session), principal=Depends(get_principal)):
        require_account_role(session, principal, {"ADMIN"})
        query = select(ChatThread)
        if username:
            query = query.join(LoginAccount, and_(LoginAccount.user_id == ChatThread.user_id,
                LoginAccount.sandbox_id == ChatThread.sandbox_id)).where(LoginAccount.username == username.strip().lower())
        total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = session.scalars(query.order_by(ChatThread.updated_at.desc(), ChatThread.id)
                               .offset((page - 1) * page_size).limit(page_size))
        return {"items": [_thread_summary(session, row) for row in rows], "total": total,
                "page": page, "page_size": page_size, "read_only": True}

    @app.get("/api/v2/admin/conversations/{conversation_id}/evidence")
    def admin_conversation(conversation_id: str, page: int = Query(default=1, ge=1),
                           page_size: int = Query(default=25, ge=1, le=100),
                           session=Depends(get_session), principal=Depends(get_principal)):
        require_account_role(session, principal, {"ADMIN"})
        thread = session.get(ChatThread, conversation_id)
        if thread is None:
            raise DomainError("CHAT_CONVERSATION_NOT_FOUND", "会话不存在", 404)
        query = select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.user_id == thread.user_id,
                                       ChatTurn.sandbox_id == thread.sandbox_id)
        return _evidence_page(session, query, page, page_size, scope="ADMIN_CONVERSATION_EVIDENCE", conversation_id=thread.id)

    @app.get("/api/v2/admin/tasks/{task_id}/conversation-evidence")
    def admin_task_conversation(task_id: str, page: int = Query(default=1, ge=1),
                                page_size: int = Query(default=25, ge=1, le=100),
                                session=Depends(get_session), principal=Depends(get_principal)):
        require_account_role(session, principal, {"ADMIN"})
        task = session.get(Task, task_id)
        if task is None:
            raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
        record = session.get(RuntimeRecord, task.id)
        if record and record.kind == "CHILD":
            parent = session.get(Task, record.root_task_id)
            if parent is None or (parent.user_id, parent.sandbox_id) != (task.user_id, task.sandbox_id):
                raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
            task = parent
        query = select(ChatTurn).join(ChatThread, ChatThread.id == ChatTurn.thread_id).where(
            ChatTurn.sandbox_id == task.sandbox_id, ChatTurn.user_id == task.user_id,
            ChatThread.sandbox_id == task.sandbox_id, ChatThread.user_id == task.user_id,
            or_(ChatTurn.task_id == task.id, ChatTurn.requested_task_id == task.id))
        return _evidence_page(session, query, page, page_size,
                              scope="ADMIN_TASK_LINKED_CONVERSATION_EVIDENCE", task_id=task.id)
