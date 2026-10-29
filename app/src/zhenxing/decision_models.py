"""Versioned observation contracts. These objects never grant tool permissions."""
from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .intelligence import IntentProposal
from .laya_candidate_contracts import ReadonlyCandidateEnvelope, ReadonlyCandidatePin


BankDomain = Literal["BILL", "TRANSFER", "WEALTH", "CARD", "SUBSCRIPTION"]
AdviceStatus = Literal["OFF", "READY", "UNAVAILABLE", "ERROR", "TIMED_OUT", "PENDING", "BUSY", "BUDGET_EXCEEDED", "SKIPPED"]


class ObservationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayaIdentity(ObservationModel):
    sdk_version: str = Field(min_length=1, max_length=40)
    sdk_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    sdk_source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=160)
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tokenizer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    language_route: Literal["multilingual"] = "multilingual"
    device: Literal["cpu"] = "cpu"


class DecisionAnswer(ObservationModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question_id: str
    raw_p_true: float = Field(ge=0, le=1, allow_inf_nan=False)
    raw_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    raw_answer_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    raw_act_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    probabilities: dict[str, float]
    distribution_source: Literal["DERIVED_FROM_NOUL_PTRUE"] = "DERIVED_FROM_NOUL_PTRUE"


class DecisionUsage(ObservationModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    state_tokens: int | None = Field(default=None, ge=0)
    state_tokens_dropped: int | None = Field(default=None, ge=0)
    truncated: bool | None = None


class CalibrationState(ObservationModel):
    status: Literal["NOT_CALIBRATED"] = "NOT_CALIBRATED"
    artifact_id: None = None
    method: None = None
    evaluation_dataset_sha256: None = None
    calibrated_correctness_probability: None = None


class CoverageDifference(ObservationModel):
    agreement: Literal["UNAVAILABLE", "UNKNOWN", "AGREE", "DISAGREE"] = "UNAVAILABLE"
    candidate_domains_missing_from_proposal: list[BankDomain] = Field(default_factory=list)
    proposal_domains_not_suggested: list[BankDomain] = Field(default_factory=list)
    multi_step_disagreement: bool | None = None
    cross_domain_disagreement: bool | None = None
    interpretation: str = "分歧是人工评测候选，不能作为追加动作、裁剪能力或授权依据。"


class DecisionAdvice(ObservationModel):
    schema_version: Literal["laya-shadow-v1"] = "laya-shadow-v1"
    advice_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: Literal["OFF", "SHADOW"]
    status: AdviceStatus
    backend: Literal["NOT_CONFIGURED", "LAYA_SDK_SIDECAR", "TEST_STUB"] = "NOT_CONFIGURED"
    laya_used: bool = False
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    inference_input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_redacted: bool = False
    language: str = "zh"
    question_set_version: str = "bank-domains-bilingual-v3"
    questions_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    question_order: list[str]
    option_order: dict[str, list[str]]
    question_definitions: dict
    identity: LayaIdentity | None = None
    answers: list[DecisionAnswer] = Field(default_factory=list)
    candidate_domains: list[BankDomain] = Field(default_factory=list)
    primary_domains: list[BankDomain] = Field(default_factory=list)
    unmapped_actions: list[str] = Field(default_factory=list)
    multi_step_advice: bool | None = None
    cross_domain_advice: bool | None = None
    primary_multi_step: bool | None = None
    primary_cross_domain: bool | None = None
    exploratory_threshold: float = 0.5
    threshold_basis: Literal["SHADOW_EXPLORATORY_NOT_CALIBRATED"] = "SHADOW_EXPLORATORY_NOT_CALIBRATED"
    calibration: CalibrationState = Field(default_factory=CalibrationState)
    coverage_difference: CoverageDifference = Field(default_factory=CoverageDifference)
    usage: DecisionUsage = Field(default_factory=DecisionUsage)
    latency_ms: float | None = Field(default=None, ge=0)
    timeout_ms: float = Field(gt=0)
    max_len: int = Field(gt=0)
    head_max_len: int = Field(gt=0)
    error_code: str | None = None
    readonly_candidate: ReadonlyCandidateEnvelope | None = None
    readonly_candidate_error: Literal["CANDIDATE_SCHEMA_OR_FINGERPRINT_MISMATCH", "REAL_SIDECAR_REQUIRED"] | None = None


class ProposalDiagnostics(ObservationModel):
    primary_attempt_count: int | None = Field(default=None, ge=0)
    model_api_call_count: int | None = Field(default=None, ge=0)
    elapsed_ms: float | None = Field(default=None, ge=0)
    failure_reasons: list[str] = Field(default_factory=list)
    partial_omission_kinds: list[str] = Field(default_factory=list)
    # Recorded at this request's compiler gate; never derived from a later policy.
    standard_read_gate: Literal["NO_ROUTING_REGISTRY", "NO_SERVER_POLICY", "INVALID_SERVER_POLICY",
        "DISABLED_BY_POLICY", "REVIEWED_EVIDENCE_PRESENT", "NO_EXACT_AUTHORIZED_MATCH",
        "MATCHED", "INVALID_COMPILER_CONTEXT", "CUSTOMER_DIALOGUE_REQUIRES_MODEL"] | None = None


class ProposalWithAdvice(ObservationModel):
    proposal: IntentProposal
    advice: DecisionAdvice
    diagnostics: ProposalDiagnostics


class SidecarAdviceRequest(ObservationModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: str = Field(min_length=1, max_length=4000)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    questions_hash: str
    max_len: int = Field(ge=256, le=8192)
    head_max_len: int = Field(ge=64, le=512)
    readonly_candidate_pin: ReadonlyCandidatePin | None = None


class SidecarReadonlyCandidateRequest(ObservationModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: str = Field(min_length=1, max_length=4000)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    readonly_candidate_pin: ReadonlyCandidatePin
