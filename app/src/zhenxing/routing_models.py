"""Server-owned model routing contracts; no field grants banking authority."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .decision_models import ProposalDiagnostics, ProposalWithAdvice


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["model-routing-v1"] = "model-routing-v1"
    version: int = Field(default=1, ge=1)
    mode: Literal["AUTO", "PREFER_CLOUD", "LOCAL_ONLY", "CLOUD_ONLY"] = "LOCAL_ONLY"
    local_model: str = Field(default="gemma3:12b", min_length=1, max_length=160)
    cloud_provider: Literal["deepseek"] = "deepseek"
    cloud_model: str = Field(default="deepseek-flash", min_length=1, max_length=160)
    cloud_enabled: bool = False
    cloud_secondary_model: str | None = Field(default=None, min_length=1, max_length=160)
    standard_read_fastpath: bool = False
    max_http_attempts: int = Field(default=3, ge=1, le=3)
    max_elapsed_seconds: int = Field(default=360, ge=1, le=360)


class ModelRoutingPolicy(Base):
    __tablename__ = "model_routing_policies"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="global")
    version: Mapped[int] = mapped_column(Integer, default=1)
    policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    uncached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class RoutingAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: int = Field(ge=1, le=3)
    provider: str
    model: str
    response_model: str | None = None
    http_issued: bool | None
    http_status: int | None = None
    elapsed_ms: float = Field(ge=0)
    status: Literal["SUCCEEDED", "FAILED"]
    error_code: str | None = None
    raw_action_count: int | None = Field(default=None, ge=0)
    raw_action_kinds: list[str] = Field(default_factory=list)
    grounded_action_count: int | None = Field(default=None, ge=0)
    filter_reason_codes: list[str] = Field(default_factory=list)
    usage: ModelUsage = Field(default_factory=ModelUsage)


class RoutingDiagnostics(ProposalDiagnostics):
    routing_policy_version: int | None = None
    routing_mode: Literal["AUTO", "PREFER_CLOUD", "LOCAL_ONLY", "CLOUD_ONLY"]
    route_reason: str
    selected_provider: str | None = None
    selected_model: str | None = None
    actual_provider: str = "rule"
    actual_model: str = "rule-intent-v3"
    degraded: bool = False
    attempts: list[RoutingAttempt] = Field(default_factory=list)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    # Numeric token counts may exist; raw chain-of-thought never does.
    reasoning_content_retained: Literal[False] = False
    plan_source: str = "MODEL_PROPOSAL"
    skipped_primary_model: bool = False
    skip_reason: str | None = None
    laya_contribution: Literal["NONE", "SHADOW_OBSERVATION", "CALIBRATED_READ_CLASSIFICATION"] = "NONE"


class RoutedProposalWithAdvice(ProposalWithAdvice):
    # Preserve route evidence in full-envelope serialization as well as the
    # runtime's direct diagnostics.model_dump() persistence path.
    diagnostics: RoutingDiagnostics
