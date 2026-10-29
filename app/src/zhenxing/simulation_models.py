from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id


class SimulationState(Base):
    """Persistent bank business clock; it never supplies security timestamps."""

    __tablename__ = "simulation_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(
        ForeignKey("sandboxes.id"), unique=True, index=True
    )
    profile: Mapped[str] = mapped_column(String(40))
    seed_version: Mapped[str] = mapped_column(String(40))
    seed: Mapped[int] = mapped_column(Integer)
    business_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opening_balance_minor: Mapped[int] = mapped_column(Integer)
    fault_mode: Mapped[str] = mapped_column(String(40), default="NONE")
