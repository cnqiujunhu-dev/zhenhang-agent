"""Pinned, abstaining Laya capability observations. No plans, slots or permissions.

This adapter is isolated from ModelGateway and never changes CLOUD_ONLY routing.
A quality manifest describes an experiment; even a passing gate grants no tool
authority and does not enable adoption. Only explicit server read grounding can
retain a classifier label, and all normal banking permissions still apply.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .decision_models import LayaIdentity
from .intelligence import ground_proposal, rule_proposal
from .planning import canonical_hash

LABEL_KINDS = {"A": "BILL_ANALYSIS", "B": "CARD_STATUS", "C": "SUBSCRIPTION_LIST", "D": "WEALTH_COMPARE", "E": None}
LABELS = tuple(LABEL_KINDS)
QUESTIONS = {"read_capability": {
    "type": "choice",
    "instructions": "Classify the user's single current read-only bank information request. Choose E for a write, quotation, teaching, condition, refusal, multiple requests or another topic. Never infer an amount, recipient, account or authorization.",
    "criteria": {
        "A": "view or analyze the user's bank statement, transaction history or spending",
        "B": "view the user's bank card status or existing card limits, without changing them",
        "C": "view existing subscriptions or recurring debit agreements, without cancelling them",
        "D": "compare existing investment product information, without purchasing or redeeming",
        "E": "anything else: writes, quoted instructions, teaching, conditions, refusals, multiple requests or another topic",
    },
}}
QUESTIONS_HASH = canonical_hash({"version": "readonly-capability-choice-v1", "questions": QUESTIONS, "label_order": LABELS})
_UNSAFE = re.compile(r'[0-9“”"\'‘’<>]|[零〇一二两三四五六七八九十百千万亿]+(?:元|块|人民币|美元)|'
    r"转|付款|支付|收款|买入|购买|申购|赎回|卖出|锁卡|解锁|挂失|补卡|取消|关闭|停用|开启|授权|撤销|兑换|设置|调整|冻结|删除|领取|"
    r"网页|短信|截图|图片|附件|他说|她说|示例|例子|如果|假设|将来|以后|不要|不想|不是|别|仅解释|只解释|如何|怎么|为什么|"
    r"先|然后|再|接着|随后|同时|另外|顺便|他人|其他人|朋友的|家人|父亲|母亲|忽略|绕过|跳过|执行|照做|验证码|密码|口令|Bearer|API[_ -]?KEY", re.I)
_DIRECT_READ = re.compile(
    r"^(?:请|帮我|帮忙|麻烦|我想|想|我需要)?(?:分析|查看|查询|查|看看|看|比较|对比|核对)(?:一下|下)?"
    r"(?:我的|我)?(?:本月|上月|今年|年度|最近|现有|当前|目前|已开通的|已开通|已生效的|已生效)?"
    r"(?:银行卡|卡片|账单|消费流水|收支流水|支出记录|交易记录|订阅代扣|订阅|自动扣费订阅|自动续费订阅|自动续费|订阅和自动续费|理财产品|理财)"
    r"(?:的)?(?:状态和限额|现有限额|当前限额|风险等级|风险和期限|期限|风险|状态|限额|信息|报告|清单|协议)?$"
)


def policy_fingerprint() -> str:
    return canonical_hash({"adapter": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "grounding": hashlib.sha256(Path(__file__).with_name("intelligence.py").read_bytes()).hexdigest(),
        "questions_hash": QUESTIONS_HASH, "label_order": LABELS})


def deterministic_read_ground(message: str) -> str | None:
    """Narrow safety boundary, distinct from Laya's measured classification."""
    if not isinstance(message, str) or not 2 <= len(message) <= 120:
        return None
    text = unicodedata.normalize("NFKC", message).strip().rstrip("。？！?!")
    if _UNSAFE.search(text) or not _DIRECT_READ.fullmatch(text):
        return None
    proposal = ground_proposal(rule_proposal(text, fallback=False, reason="readonly-candidate-ground"), text)
    if proposal.clarification_questions or len(proposal.actions) != 1:
        return None
    action = proposal.actions[0]
    if action.kind not in set(LABEL_KINDS.values()) - {None} or action.depends_on \
            or action.slots.model_dump(exclude_none=True):
        return None
    return action.kind


class CandidateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["laya-readonly-candidate-v1"] = "laya-readonly-candidate-v1"
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    questions_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    calibration_dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    temperature: float = Field(gt=0, le=8, allow_inf_nan=False)
    min_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_margin: float = Field(ge=0, le=1, allow_inf_nan=False)
    timeout_ms: float = Field(ge=50, le=5000, allow_inf_nan=False)
    quality_gate_passed: bool = False
    adoption_enabled: Literal[False] = False
    calibration_method: Literal["DEVELOPMENT_TEMPERATURE_AND_SELECTIVE_THRESHOLD"] = "DEVELOPMENT_TEMPERATURE_AND_SELECTIVE_THRESHOLD"
    probability_accuracy_claim: Literal[False] = False


class CandidateObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    status: Literal["ABSTAIN", "EXPERIMENTAL_CANDIDATE"]
    reason: str
    candidate_kind: Literal["BILL_ANALYSIS", "CARD_STATUS", "SUBSCRIPTION_LIST", "WEALTH_COMPARE"] | None = None
    calibrated_score: float | None = None
    score_is_correctness_probability: Literal[False] = False
    sdk_inference_used: bool = False
    latency_ms: float | None = None
    mode: Literal["SHADOW"] = "SHADOW"
    authority: Literal["OBSERVATION_ONLY"] = "OBSERVATION_ONLY"
    adopted: Literal[False] = False


def normalize_scores(result: dict, temperature: float) -> dict[str, float]:
    answer = result["answers"]["read_capability"]
    raw = answer["probabilities"]
    if answer.get("type") != "choice" or answer.get("choice") not in LABELS or set(raw) != set(LABELS):
        raise ValueError("INVALID_CAPABILITY_RESPONSE")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in raw.values()) \
            or abs(sum(raw.values()) - 1) > 0.001:
        raise ValueError("INVALID_CAPABILITY_DISTRIBUTION")
    if answer["choice"] != max(raw, key=raw.get):
        raise ValueError("CHOICE_DISTRIBUTION_MISMATCH")
    if result.get("usage", {}).get("truncated") is not False or result.get("usage", {}).get("state_tokens_dropped") != 0:
        raise ValueError("UNVERIFIED_OR_TRUNCATED_INPUT")
    logits = {key: math.log(max(value, 1e-12)) / temperature for key, value in raw.items()}
    maximum = max(logits.values())
    values = {key: math.exp(value - maximum) for key, value in logits.items()}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()}


def score_candidate(message: str, result: dict, manifest: CandidateManifest, identity: LayaIdentity,
                    *, latency_ms: float, real_sdk: bool) -> CandidateObservation:
    def abstain(reason):
        return CandidateObservation(status="ABSTAIN", reason=reason, sdk_inference_used=real_sdk, latency_ms=latency_ms)
    if not math.isfinite(latency_ms) or latency_ms < 0 or latency_ms > manifest.timeout_ms:
        return abstain("DEADLINE_EXCEEDED")
    if not real_sdk:
        return abstain("REAL_PINNED_SDK_REQUIRED")
    if manifest.identity_sha256 != canonical_hash(identity.model_dump()) or manifest.questions_sha256 != QUESTIONS_HASH \
            or manifest.policy_sha256 != policy_fingerprint():
        return abstain("FINGERPRINT_MISMATCH")
    grounded = deterministic_read_ground(message)
    if grounded is None:
        return abstain("SERVER_READ_GROUNDING_ABSTAINED")
    try:
        scores = normalize_scores(result, manifest.temperature)
    except (KeyError, TypeError, ValueError):
        return abstain("INVALID_OR_TRUNCATED_RESPONSE")
    ranked = sorted(scores, key=scores.get, reverse=True)
    label = ranked[0]
    if label == "E":
        return abstain("CLASSIFIER_OTHER")
    if scores[label] < manifest.min_confidence or scores[label] - scores[ranked[1]] < manifest.min_margin:
        return abstain("LOW_CONFIDENCE_OR_MARGIN")
    if LABEL_KINDS[label] != grounded:
        return abstain("SERVER_GROUNDING_DISAGREEMENT")
    return CandidateObservation(status="EXPERIMENTAL_CANDIDATE", reason="PINNED_CLASSIFIER_AGREES_WITH_READ_GROUNDING",
        candidate_kind=grounded, calibrated_score=scores[label], sdk_inference_used=True, latency_ms=latency_ms)


class PinnedCapabilityBackend:
    """Explicit optional-runtime adapter; constructor never loads/downloads a model."""
    def __init__(self, sdk):
        from .decision_advisor import LocalLayaSDK
        if not isinstance(sdk, LocalLayaSDK) or not sdk.verification_complete:
            raise ValueError("VERIFIED_LOCAL_SDK_REQUIRED")
        self.sdk = sdk
        self.identity = sdk.identity

    def predict(self, message: str) -> dict:
        # Reuse the verified instance's lock; no simultaneous alternate head may
        # race its standard shadow observer or checkpoint configuration.
        with self.sdk._lock:
            return self.sdk.agent.system_one(message, QUESTIONS, lang="en", max_len=512, head_max_len=256)


class ReadonlyCandidateObserver:
    """Single-flight, bounded observation only; a timed-out worker cannot apply later."""
    def __init__(self, backend: PinnedCapabilityBackend, manifest: CandidateManifest):
        self.backend = backend
        self.manifest = manifest
        self._capacity = threading.BoundedSemaphore(1)

    def observe(self, message: str) -> CandidateObservation:
        if self.manifest.identity_sha256 != canonical_hash(self.backend.identity.model_dump()) \
                or self.manifest.questions_sha256 != QUESTIONS_HASH or self.manifest.policy_sha256 != policy_fingerprint():
            return CandidateObservation(status="ABSTAIN", reason="FINGERPRINT_MISMATCH")
        if deterministic_read_ground(message) is None:
            return CandidateObservation(status="ABSTAIN", reason="SERVER_READ_GROUNDING_ABSTAINED")
        if not self._capacity.acquire(blocking=False):
            return CandidateObservation(status="ABSTAIN", reason="OBSERVER_BUSY")
        done = threading.Event()
        result = {}
        started = time.perf_counter()
        def run():
            try:
                result["value"] = self.backend.predict(message)
            except Exception:
                result["failed"] = True
            finally:
                self._capacity.release()
                done.set()
        threading.Thread(target=run, name="laya-readonly-candidate", daemon=True).start()
        completed = done.wait(self.manifest.timeout_ms / 1000)
        elapsed = (time.perf_counter() - started) * 1000
        if not completed:
            return CandidateObservation(status="ABSTAIN", reason="DEADLINE_EXCEEDED", latency_ms=elapsed)
        if result.get("failed"):
            return CandidateObservation(status="ABSTAIN", reason="SDK_INFERENCE_ERROR", latency_ms=elapsed)
        return score_candidate(message, result["value"], self.manifest, self.backend.identity,
                               latency_ms=elapsed, real_sdk=isinstance(self.backend, PinnedCapabilityBackend))
