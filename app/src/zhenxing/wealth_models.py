from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class RiskAssessmentRecord(Base):
    """An independently submitted user form, outside model execution tools."""

    __tablename__ = "risk_assessment_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    risk_profile_id: Mapped[str] = mapped_column(ForeignKey("risk_profiles.id"), index=True)
    questionnaire_version: Mapped[str] = mapped_column(String(40))
    answers_json: Mapped[dict] = mapped_column(JSON)
    score: Mapped[int] = mapped_column(Integer)
    risk_level: Mapped[int] = mapped_column(Integer)
    submission_source: Mapped[str] = mapped_column(String(40), default="INDEPENDENT_USER_FORM")
    business_assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    security_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
