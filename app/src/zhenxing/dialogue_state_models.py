"""Durable actor-scoped dialogue navigation; never a banking grant."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import utcnow


class ConversationDialogueState(Base):
    __tablename__ = "conversation_dialogue_states"
    __table_args__ = (CheckConstraint("version >= 1"),)

    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    actor_scope_hash: Mapped[str] = mapped_column(String(64))
    focus_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


DIALOGUE_STATE_TABLES = {ConversationDialogueState.__tablename__}
