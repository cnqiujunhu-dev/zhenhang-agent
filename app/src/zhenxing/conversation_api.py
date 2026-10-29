"""v3 chat contracts; these endpoints never accept bank confirmation fields."""
from typing import Literal

from fastapi import Depends, Query
from pydantic import Field
from sqlalchemy import select

from .contracts import StrictModel
from .conversation_models import ChatThread
from .conversation_service import (close_conversation, conversation_view, create_conversation,
    submit_turn, thread_view, turn_view)
from .permissions import require_banking_actor
from .customer_history_archive import visible_history, require_visible_history


class ReviewedEvidenceRef(StrictModel):
    review_id: str = Field(min_length=36, max_length=36)
    attachment_version: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CreateConversationRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=120)


class RecipientChoiceRequest(StrictModel):
    origin_turn_id: str = Field(min_length=36, max_length=36)
    choice_set_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    choice_token: str = Field(min_length=24, max_length=80)


class ChatTurnRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    client_request_id: str = Field(min_length=1, max_length=120)
    current_task_id: str | None = Field(default=None, min_length=36, max_length=36)
    expected_task_version: int | None = Field(default=None, ge=1, strict=True)
    explicit_task_selection: bool = Field(default=False, strict=True)
    context_policy: Literal["AUTO", "INDEPENDENT"] = "AUTO"
    reviewed_evidence_refs: list[ReviewedEvidenceRef] = Field(default_factory=list, max_length=8)
    reply_language: Literal["zh-CN", "en"] | None = None
    recipient_choice: RecipientChoiceRequest | None = None


class RetryUnderstandingRequest(StrictModel):
    expected_retry_version: int = Field(ge=0, le=1, strict=True)


def install_conversation_routes(app, get_session, get_principal):
    @app.post("/api/v3/conversations/{conversation_id}/turns/{turn_id}/recipient-choices")
    def recipient_choices(conversation_id: str, turn_id: str,
            session=Depends(get_session), principal=Depends(get_principal)):
        from .recipient_choices import issue_choices
        require_visible_history(session, principal, ChatThread, conversation_id)
        result = issue_choices(session, principal, conversation_id, turn_id)
        session.commit()
        return result

    @app.post("/api/v3/conversations", status_code=201)
    def create(payload: CreateConversationRequest, session=Depends(get_session), principal=Depends(get_principal)):
        row = create_conversation(session, principal, payload.title, payload.client_request_id)
        session.commit()
        return thread_view(row)

    @app.get("/api/v3/conversations")
    def listing(session=Depends(get_session), principal=Depends(get_principal)):
        require_banking_actor(session, principal)
        rows = session.scalars(select(ChatThread).where(ChatThread.sandbox_id == principal.sandbox_id,
            ChatThread.user_id == principal.user_id, visible_history(session, principal, ChatThread.created_at))
            .order_by(ChatThread.updated_at.desc(), ChatThread.id).limit(100))
        return {"items": [thread_view(row) for row in rows]}

    @app.get("/api/v3/conversations/{conversation_id}")
    def get(conversation_id: str, before_sequence: int | None = Query(default=None, ge=1),
            history_projection: Literal["bounded-history-v2"] = "bounded-history-v2",
            session=Depends(get_session), principal=Depends(get_principal)):
        from .read_projection_request import read_projection_request
        require_visible_history(session, principal, ChatThread, conversation_id)
        with read_projection_request(session, principal):
            return conversation_view(session, principal, conversation_id, before_sequence)

    @app.get("/api/v3/conversations/{conversation_id}/turns/{turn_id}/audit")
    def audit(conversation_id: str, turn_id: str,
              version: Literal["stored-turn-field-v1"] = Query(),
              field: Literal["reviewed_evidence_refs_json", "sources_json", "suggestions_json", "response_meta_json",
                  "field_sources_json", "diagnostics_json"] = Query(),
              offset: int = Query(default=0, ge=0), length: int = Query(default=65536, ge=1, le=65536),
              observation: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
              session=Depends(get_session), principal=Depends(get_principal)):
        from .conversation_history_projection import stored_turn_audit_chunk
        return stored_turn_audit_chunk(session, principal, conversation_id, turn_id, version=version,
            field=field, offset=offset, length=length, observation=observation)

    @app.post("/api/v3/conversations/{conversation_id}/turns", status_code=202)
    def post(conversation_id: str, payload: ChatTurnRequest, session=Depends(get_session), principal=Depends(get_principal)):
        require_visible_history(session, principal, ChatThread, conversation_id)
        row = submit_turn(session, principal, conversation_id, **payload.model_dump())
        session.commit()
        return turn_view(session, principal, row)

    @app.post("/api/v3/conversations/{conversation_id}/close")
    def close(conversation_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        row = close_conversation(session, principal, conversation_id)
        session.commit()
        return {**thread_view(row), "notice": "会话已关闭，未取消银行任务或回滚已完成业务。"}
