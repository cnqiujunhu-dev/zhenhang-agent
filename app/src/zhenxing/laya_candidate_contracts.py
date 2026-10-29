"""Bounded transport observations. These schemas cannot carry plans or authority."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256 = r"^[a-f0-9]{64}$"


class ReadonlyCandidatePin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    identity_sha256: str = Field(pattern=SHA256)
    questions_sha256: str = Field(pattern=SHA256)
    policy_sha256: str = Field(pattern=SHA256)
    manifest_sha256: str = Field(pattern=SHA256)


class ReadonlyCandidateTransportObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    status: Literal["ABSTAIN", "EXPERIMENTAL_CANDIDATE"]
    reason: Literal["DEADLINE_EXCEEDED", "REAL_PINNED_SDK_REQUIRED", "FINGERPRINT_MISMATCH",
        "SERVER_READ_GROUNDING_ABSTAINED", "INVALID_OR_TRUNCATED_RESPONSE", "CLASSIFIER_OTHER",
        "LOW_CONFIDENCE_OR_MARGIN", "SERVER_GROUNDING_DISAGREEMENT",
        "PINNED_CLASSIFIER_AGREES_WITH_READ_GROUNDING", "OBSERVER_BUSY", "SDK_INFERENCE_ERROR"]
    candidate_kind: Literal["BILL_ANALYSIS", "CARD_STATUS", "SUBSCRIPTION_LIST", "WEALTH_COMPARE"] | None = None
    calibrated_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    score_is_correctness_probability: Literal[False] = False
    sdk_inference_used: bool = False
    latency_ms: float | None = Field(default=None, ge=0, le=60000, allow_inf_nan=False)
    mode: Literal["SHADOW"] = "SHADOW"
    authority: Literal["OBSERVATION_ONLY"] = "OBSERVATION_ONLY"
    adopted: Literal[False] = False

    @model_validator(mode="after")
    def coherent_observation(self):
        if self.status == "ABSTAIN" and (self.candidate_kind is not None or self.calibrated_score is not None):
            raise ValueError("Abstention cannot carry a candidate")
        if self.status == "EXPERIMENTAL_CANDIDATE" and (self.candidate_kind is None or self.calibrated_score is None
                or not self.sdk_inference_used or self.reason != "PINNED_CLASSIFIER_AGREES_WITH_READ_GROUNDING"):
            raise ValueError("Candidate requires a completed grounded SDK observation")
        return self


class ReadonlyCandidateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["laya-readonly-candidate-v1"] = "laya-readonly-candidate-v1"
    pin: ReadonlyCandidatePin
    source_sha256: str = Field(pattern=SHA256)
    inference_input_sha256: str = Field(pattern=SHA256)
    quality_gate_passed: Literal[False] = False
    adoption_enabled: Literal[False] = False
    observation: ReadonlyCandidateTransportObservation
