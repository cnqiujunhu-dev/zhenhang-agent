"""FIFO lease and explicit production/legacy entry selection.

Registry-backed production semantics never falls through to legacy rules on
model failure. FIFO leasing and retry rules match the former entry unchanged.
"""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased
from .authentication import as_utc
from .conversation_models import ChatThread, ChatTurn
from .models import utcnow

LEASE_SECONDS = 420


def process_chat_job(factory, gateway, worker_id=None):
    """One leased FIFO turn. Network explanation happens outside DB transaction."""
    identity, now = worker_id or "chat-" + str(uuid4()), utcnow()
    leased = None
    with factory.begin() as session:
        predecessor = aliased(ChatTurn)
        earlier_pending = exists(select(predecessor.id).where(predecessor.thread_id == ChatTurn.thread_id,
            predecessor.sequence < ChatTurn.sequence, predecessor.status.in_(["QUEUED", "LEASED"])))
        candidates = list(session.scalars(select(ChatTurn).where(or_(ChatTurn.status == "QUEUED",
            (ChatTurn.status == "LEASED") & (ChatTurn.lease_expires_at < now)), ~earlier_pending)
            .order_by(ChatTurn.created_at, ChatTurn.sequence).limit(20)))
        for candidate in candidates:
            thread = session.scalar(select(ChatThread).where(ChatThread.id == candidate.thread_id).with_for_update(skip_locked=True))
            if thread is None:
                continue
            turn = session.scalar(select(ChatTurn).where(ChatTurn.id == candidate.id).with_for_update().execution_options(populate_existing=True))
            if turn.status not in {"QUEUED", "LEASED"} or (turn.status == "LEASED" and as_utc(turn.lease_expires_at) > now):
                continue
            prior = session.scalar(select(ChatTurn.id).where(ChatTurn.thread_id == thread.id, ChatTurn.sequence < turn.sequence,
                                                             ChatTurn.status.in_(["QUEUED", "LEASED"])).limit(1))
            if prior:
                continue
            if thread.status != "ACTIVE" or turn.attempts >= 3:
                turn.status, turn.error_code, turn.completed_at = "FAILED", "CHAT_CLOSED_OR_RETRY_EXHAUSTED", now
                return True
            turn.status, turn.lease_owner, turn.lease_expires_at = "LEASED", identity, now + timedelta(seconds=LEASE_SECONDS)
            turn.attempts += 1
            leased = turn.id
            break
    if leased is None:
        return False
    if getattr(gateway,'registry',None) is not None:
        from .semantic_conversation import process_semantic_turn
        return process_semantic_turn(factory,gateway,leased,identity)
    from .conversation_service import _process_legacy_chat_turn
    return _process_legacy_chat_turn(factory, gateway, leased, identity)
