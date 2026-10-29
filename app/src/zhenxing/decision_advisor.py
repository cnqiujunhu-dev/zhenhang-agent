"""Optional Laya observations, isolated from planning authority and banking writes.

The host environment imports neither torch nor Laya. Real inference is available
only in an explicitly started CPU sidecar with verified local artifacts.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from .decision_models import CoverageDifference, DecisionAdvice, DecisionAnswer, DecisionUsage, LayaIdentity, SidecarAdviceRequest, SidecarReadonlyCandidateRequest
from .laya_candidate_contracts import ReadonlyCandidateEnvelope, ReadonlyCandidatePin
from .planning import canonical_hash


DOMAIN_ORDER = ("BILL", "TRANSFER", "WEALTH", "CARD", "SUBSCRIPTION")
# Mirror the loopback sidecar's wire limits without importing its tokenizer.
# Token-count validation remains sidecar-only; the host refuses char/byte
# overflow before allocating a worker or issuing an HTTP request.
SIDECAR_MAX_CHARS = 512
SIDECAR_MAX_UTF8_BYTES = 1536
QUESTION_DEFINITIONS = {
    "domain_bill": {"type": "noul", "instructions": "用户现在请求哪类服务？是否要求查询或分析本人的账单、消费流水？",
        "criteria": {"true": "用户要求查看或分析本人账单、消费流水、收支", "false": "用户没有请求查看或分析本人账单流水，只是解释、引用、假设或其他业务"}},
    "domain_transfer": {"type": "noul", "instructions": "用户现在是否请求办理转账、预约转账或AA收款？",
        "criteria": {"true": "用户明确要求现在办理转账、预约转账或AA收款", "false": "没有要求办理转账；否定、取消、教学、引用诈骗文字均不是转账请求"}},
    "domain_wealth": {"type": "noul", "instructions": "用户现在是否请求比较、购买或赎回自己的理财产品？",
        "criteria": {"true": "用户要求比较现有理财产品，或者申购、赎回自己的理财", "false": "没有要求理财产品比较或交易；仅了解风险、否定购买或其他业务"}},
    "domain_card": {"type": "noul", "instructions": "用户现在是否要求查询或管理自己的银行卡？",
        "criteria": {"true": "用户要求查看卡状态、锁卡、解锁、挂失、补卡或设置卡限额", "false": "没有要求查看或管理卡；仅否定、条件设想、引用或其他业务"}},
    "domain_subscription": {"type": "noul", "instructions": "用户现在是否要求查询或取消订阅、周期扣费协议？",
        "criteria": {"true": "用户要求查询已开通的订阅或自动扣费，或者取消协议", "false": "没有要求查询或取消订阅扣费协议，只有其他业务或教学引用"}},
    "multi_step": {"type": "noul", "instructions": "用户要求现在完成多少个独立的银行业务动作？是否两个或更多？",
        "criteria": {"true": "用户明确要求执行两个或更多独立动作，例如两笔转账、先看账单再转账", "false": "零个或只有一个动作；否定、教学和假设不是业务动作"}},
    "cross_domain": {"type": "noul", "instructions": "用户现在要求的动作是否横跨账单、转账、理财、卡片、订阅中的两个或更多领域？",
        "criteria": {"true": "用户要求的动作涉及至少两个不同领域，例如看账单并转账", "false": "用户只要求一个领域的动作，或者仅讨论、否定和假设"}},
}
# Model-facing labels are intentionally neutral. The bilingual encoder reads
# Chinese user state with concise English heads; development probes showed the
# longer Chinese yes/no formulations suffered severe label sensitivity.
_BILINGUAL_HEADS = {
    "domain_bill": ("Does the user ask to view or analyze their bank statement or transaction history?",
        "view bank statements, transaction history or spending analysis", "another request, explanation only or no bank statement request"),
    "domain_transfer": ("Does the user request a money transfer or scheduled transfer?",
        "send money to another person, schedule a transfer or collect shared expenses", "another request, explanation, quoted text or refusal to transfer"),
    "domain_wealth": ("Does the user ask to compare, purchase or redeem investment products?",
        "compare investment products, purchase an investment or redeem a holding", "another request or explanation without investment comparison or trading"),
    "domain_card": ("Does the user ask to view or manage their bank card?",
        "view bank card status, lock, unlock, report lost, replace or set card limits", "another request or no current bank card service request"),
    "domain_subscription": ("Does the user ask to view or cancel subscriptions or recurring debit agreements?",
        "view subscriptions, recurring payments or cancel a subscription agreement", "another request or no subscription service request"),
    "multi_step": ("How many independent banking actions does the user explicitly request now? Are there two or more?",
        "two or more independent actions such as two transfers or view bills then transfer", "zero or one action, not counting refusals, explanations or conditions"),
    "cross_domain": ("Does the user explicitly request actions in two or more different banking service domains?",
        "two or more of statements, transfers, investments, cards and subscriptions", "only one domain or no banking action"),
}
QUESTION_DEFINITIONS = {key: {"type": "noul", "instructions": instruction,
    "criteria": {"true": positive, "false": negative}, "labels": {"true": "A", "false": "B"}}
    for key, (instruction, positive, negative) in _BILINGUAL_HEADS.items()}
QUESTION_ORDER = tuple(QUESTION_DEFINITIONS)
OPTIONS_ORDER = {key: ["false", "true"] for key in QUESTION_ORDER}
QUESTIONS_HASH = canonical_hash({"version": "bank-domains-bilingual-v3", "questions": QUESTION_DEFINITIONS,
                                 "question_order": QUESTION_ORDER, "option_order": OPTIONS_ORDER})


def action_domain(kind: str) -> str | None:
    """Domain membership, not an allowed-action registry. Unknowns stay visible."""
    if kind.startswith("BILL_"):
        return "BILL"
    if kind.startswith("TRANSFER") or kind.startswith("AA_"):
        return "TRANSFER"
    if kind.startswith("WEALTH_"):
        return "WEALTH"
    if kind.startswith("CARD_"):
        return "CARD"
    if kind.startswith("SUBSCRIPTION_"):
        return "SUBSCRIPTION"
    return None


def compare_advice(advice: DecisionAdvice, proposal) -> DecisionAdvice:
    domains = {action_domain(action.kind) for action in proposal.actions} - {None}
    unmapped = [action.kind for action in proposal.actions if action_domain(action.kind) is None]
    result = advice.model_copy(deep=True)
    result.primary_domains = [domain for domain in DOMAIN_ORDER if domain in domains]
    result.primary_multi_step = len(proposal.actions) > 1
    result.primary_cross_domain = None if unmapped else len(domains) > 1
    result.unmapped_actions = unmapped
    if result.status != "READY":
        return result
    if result.usage.truncated is not False or result.usage.state_tokens_dropped != 0 or unmapped:
        result.coverage_difference = CoverageDifference(agreement="UNKNOWN")
        return result
    candidate = set(result.candidate_domains)
    missing = [domain for domain in DOMAIN_ORDER if domain in candidate - domains]
    extra = [domain for domain in DOMAIN_ORDER if domain in domains - candidate]
    multi = result.multi_step_advice != result.primary_multi_step
    cross = result.cross_domain_advice != result.primary_cross_domain
    result.coverage_difference = CoverageDifference(
        agreement="DISAGREE" if missing or extra or multi or cross else "AGREE",
        candidate_domains_missing_from_proposal=missing, proposal_domains_not_suggested=extra,
        multi_step_disagreement=multi, cross_domain_disagreement=cross)
    return result


class AdviceBackend(Protocol):
    is_test_stub: bool

    def predict(self, state: str, *, identity: LayaIdentity, timeout: float,
                max_len: int, head_max_len: int, source_hash: str) -> dict: ...


class HttpLayaBackend:
    is_test_stub = False

    def __init__(self, base_url: str, api_key: str | None = None, *, readonly_candidate_manifest=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.readonly_candidate_pin = None
        if readonly_candidate_manifest is not None:
            self.readonly_candidate_pin = ReadonlyCandidatePin(identity_sha256=readonly_candidate_manifest.identity_sha256,
                questions_sha256=readonly_candidate_manifest.questions_sha256, policy_sha256=readonly_candidate_manifest.policy_sha256,
                manifest_sha256=canonical_hash(readonly_candidate_manifest.model_dump()))

    def predict(self, state, *, identity, timeout, max_len, head_max_len, source_hash):
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"state": state, "source_hash": source_hash, "questions_hash": QUESTIONS_HASH,
                "max_len": max_len, "head_max_len": head_max_len}
        if self.readonly_candidate_pin is not None:
            body["readonly_candidate_pin"] = self.readonly_candidate_pin.model_dump()
        response = httpx.post(f"{self.base_url}/v1/decision-advice", headers=headers, json=body, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("identity") != identity.model_dump() or payload.get("questions_hash") != QUESTIONS_HASH \
                or payload.get("source_hash") != source_hash:
            raise ValueError("ADVISOR_IDENTITY_MISMATCH")
        candidate = payload.get("readonly_candidate")
        if candidate is not None:
            try:
                if self.readonly_candidate_pin is None:
                    raise ValueError("UNPINNED_CANDIDATE")
                envelope = ReadonlyCandidateEnvelope.model_validate(candidate)
                if envelope.pin != self.readonly_candidate_pin or envelope.pin.identity_sha256 != canonical_hash(identity.model_dump()) \
                        or envelope.source_sha256 != source_hash or envelope.inference_input_sha256 != canonical_hash({"state": state}):
                    raise ValueError("CANDIDATE_FINGERPRINT_MISMATCH")
                payload["readonly_candidate"] = envelope.model_dump()
            except (ValueError, TypeError):
                payload.pop("readonly_candidate", None)
                payload["readonly_candidate_error"] = "CANDIDATE_SCHEMA_OR_FINGERPRINT_MISMATCH"
        return payload


@dataclass(frozen=True)
class AdvisorConfig:
    mode: str = "OFF"
    identity: LayaIdentity | None = None
    timeout_ms: float = 1000
    max_len: int = 1024
    head_max_len: int = 192
    max_input_chars: int = SIDECAR_MAX_CHARS
    max_inflight: int = 1
    configuration_error: str | None = None


class AdviceTicket:
    def __init__(self, initial: DecisionAdvice, timeout_ms: float):
        self.initial = initial
        self.started_at = time.perf_counter()
        self.timeout_ms = timeout_ms
        self.done = threading.Event()
        self.value: DecisionAdvice | None = None

    def finish(self, value):
        self.value = value
        self.done.set()

    def collect(self) -> DecisionAdvice:
        """Never wait for Laya on the incumbent planning path."""
        if self.done.is_set():
            if self.value.latency_ms is not None and self.value.latency_ms > self.timeout_ms:
                return self.initial.model_copy(update={"status": "TIMED_OUT", "latency_ms": self.value.latency_ms,
                    "error_code": "ADVISOR_TIMEOUT"})
            return self.value
        elapsed = (time.perf_counter() - self.started_at) * 1000
        status = "TIMED_OUT" if elapsed >= self.timeout_ms else "PENDING"
        return self.initial.model_copy(update={"status": status, "latency_ms": elapsed,
            "error_code": "ADVISOR_TIMEOUT" if status == "TIMED_OUT" else "SHADOW_NOT_FINISHED_WITH_PRIMARY"})


def redact_advice_input(message: str) -> str:
    from .redaction import redact_credentials
    return redact_credentials(message)


class DecisionAdvisor:
    def __init__(self, config: AdvisorConfig | None = None, backend: AdviceBackend | None = None):
        self.config = config or AdvisorConfig()
        self.backend = backend
        self._semaphore = threading.BoundedSemaphore(max(1, self.config.max_inflight))

    @classmethod
    def from_environment(cls):
        mode = os.getenv("ZX_LAYA_MODE", "OFF").upper()
        if mode == "OFF":
            return cls()
        if mode != "SHADOW":
            return cls(AdvisorConfig(mode="SHADOW", configuration_error="UNSUPPORTED_ADVISOR_MODE"))
        identity = None
        error = None
        try:
            path = os.getenv("ZX_LAYA_MANIFEST")
            if not path:
                raise ValueError("MISSING_PINNED_IDENTITY")
            identity = LayaIdentity.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            error = "MISSING_OR_INVALID_PINNED_IDENTITY"
        url = os.getenv("ZX_LAYA_BASE_URL")
        candidate_manifest = None
        candidate_path = os.getenv("ZX_LAYA_CANDIDATE_MANIFEST")
        if candidate_path:
            try:
                from .laya_readonly_candidate import CandidateManifest, QUESTIONS_HASH as candidate_questions_hash, policy_fingerprint
                candidate_manifest = CandidateManifest.model_validate_json(Path(candidate_path).read_text(encoding="utf-8"))
                if identity is None or candidate_manifest.identity_sha256 != canonical_hash(identity.model_dump()) \
                        or candidate_manifest.questions_sha256 != candidate_questions_hash or candidate_manifest.policy_sha256 != policy_fingerprint() \
                        or candidate_manifest.adoption_enabled or candidate_manifest.quality_gate_passed:
                    raise ValueError("INVALID_OPTIONAL_CANDIDATE_PIN")
            except (OSError, ValueError):
                candidate_manifest = None  # Invalid optional candidate never disables the existing seven-head observer.
        backend = HttpLayaBackend(url, os.getenv("ZX_LAYA_API_KEY"), readonly_candidate_manifest=candidate_manifest) if url else None
        return cls(AdvisorConfig(mode="SHADOW", identity=identity, configuration_error=error), backend)

    def _initial(self, message: str) -> tuple[DecisionAdvice, str]:
        state = redact_advice_input(message)
        initial = DecisionAdvice(mode="OFF" if self.config.mode == "OFF" else "SHADOW",
            status="OFF" if self.config.mode == "OFF" else "PENDING",
            source_hash=canonical_hash({"message": message}), inference_input_hash=canonical_hash({"state": state}),
            input_redacted=state != message, identity=self.config.identity,
            backend=("TEST_STUB" if getattr(self.backend, "is_test_stub", False) else "LAYA_SDK_SIDECAR")
                    if self.config.mode == "SHADOW" and self.backend else "NOT_CONFIGURED",
            question_order=list(QUESTION_ORDER), option_order=OPTIONS_ORDER,
            question_definitions=QUESTION_DEFINITIONS, questions_hash=QUESTIONS_HASH,
            timeout_ms=self.config.timeout_ms, max_len=self.config.max_len, head_max_len=self.config.head_max_len)
        return initial, state

    def _infer(self, initial: DecisionAdvice, state: str) -> DecisionAdvice:
        started = time.perf_counter()
        backend_label = "TEST_STUB" if getattr(self.backend, "is_test_stub", False) else "LAYA_SDK_SIDECAR"
        try:
            payload = self.backend.predict(state, identity=self.config.identity, timeout=self.config.timeout_ms / 1000,
                max_len=self.config.max_len, head_max_len=self.config.head_max_len, source_hash=initial.source_hash)
            answers = []
            raw = payload["result"]["answers"]
            for question_id in QUESTION_ORDER:
                answer = raw[question_id]
                if answer.get("type") != "noul" or type(answer.get("noul")) not in (int, float):
                    raise ValueError("INVALID_TYPED_ADVICE")
                p = answer["noul"]
                answers.append(DecisionAnswer(question_id=question_id, raw_p_true=p,
                    raw_confidence=answer.get("confidence"), raw_answer_confidence=answer.get("answer_confidence"),
                    raw_act_probability=answer.get("action", {}).get("act_probability"),
                    probabilities={"false": 1-p, "true": p}))
            usage = payload["result"].get("usage", {})
            measured_usage = DecisionUsage(**{key: usage.get(key) for key in DecisionUsage.model_fields})
            scores = {answer.question_id: answer.raw_p_true for answer in answers}
            candidates = [domain for domain in DOMAIN_ORDER if scores[f"domain_{domain.lower()}"] >= 0.5]
            readonly_candidate = None
            candidate_error = payload.get("readonly_candidate_error")
            if candidate_error not in (None, "CANDIDATE_SCHEMA_OR_FINGERPRINT_MISMATCH"):
                candidate_error = "CANDIDATE_SCHEMA_OR_FINGERPRINT_MISMATCH"
            if payload.get("readonly_candidate") is not None:
                try:
                    if backend_label != "LAYA_SDK_SIDECAR":
                        candidate_error = "REAL_SIDECAR_REQUIRED"
                    else:
                        readonly_candidate = ReadonlyCandidateEnvelope.model_validate(payload["readonly_candidate"])
                except (ValueError, TypeError):
                    candidate_error = "CANDIDATE_SCHEMA_OR_FINGERPRINT_MISMATCH"
            return initial.model_copy(update={"status": "READY", "backend": backend_label,
                "laya_used": backend_label == "LAYA_SDK_SIDECAR", "answers": answers,
                "candidate_domains": candidates, "multi_step_advice": scores["multi_step"] >= 0.5,
                "cross_domain_advice": scores["cross_domain"] >= 0.5, "usage": measured_usage,
                "latency_ms": (time.perf_counter()-started)*1000,
                "readonly_candidate": readonly_candidate, "readonly_candidate_error": candidate_error})
        except Exception as error:
            busy = isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 503 \
                and error.response.headers.get("Retry-After") == "1"
            code = "ADVISOR_CAPACITY" if busy else "ADVISOR_TIMEOUT" if isinstance(error, httpx.TimeoutException) else (
                "ADVISOR_HTTP_ERROR" if isinstance(error, httpx.HTTPError) else (
                "ADVISOR_IDENTITY_MISMATCH" if str(error) == "ADVISOR_IDENTITY_MISMATCH" else "ADVISOR_INVALID_RESPONSE"))
            return initial.model_copy(update={"status": "BUSY" if busy else "TIMED_OUT" if code == "ADVISOR_TIMEOUT" else "ERROR",
                "backend": backend_label, "error_code": code, "latency_ms": (time.perf_counter()-started)*1000})

    def skip_exact_read(self, message: str) -> DecisionAdvice:
        """Record a server-compiled read without launching observation work.

        The caller must already have validated the server fast path. Backend
        denotes configuration only; no inference, timing or savings is claimed.
        """
        initial, _state = self._initial(message)
        if initial.mode == "OFF":
            return initial
        return initial.model_copy(update={"status": "SKIPPED", "error_code": "EXACT_READ_NO_SHADOW_REQUIRED"})

    def start(self, message: str, context: dict | None = None) -> AdviceTicket:
        initial, state = self._initial(message)
        ticket = AdviceTicket(initial, self.config.timeout_ms)
        if self.config.mode == "OFF":
            ticket.finish(initial)
            return ticket
        if self.config.configuration_error or self.config.identity is None or self.backend is None:
            ticket.finish(initial.model_copy(update={"status": "UNAVAILABLE",
                "error_code": self.config.configuration_error or "MISSING_PINNED_BACKEND"}))
            return ticket
        if len(state) > min(self.config.max_input_chars, SIDECAR_MAX_CHARS) \
                or len(state.encode("utf-8")) > SIDECAR_MAX_UTF8_BYTES \
                or not 64 <= self.config.head_max_len < self.config.max_len <= 1024 \
                or self.config.head_max_len > 256:
            ticket.finish(initial.model_copy(update={"status": "BUDGET_EXCEEDED", "error_code": "ADVISOR_INPUT_BUDGET"}))
            return ticket
        if not self._semaphore.acquire(blocking=False):
            ticket.finish(initial.model_copy(update={"status": "BUSY", "error_code": "ADVISOR_CAPACITY"}))
            return ticket
        def run():
            try:
                ticket.finish(self._infer(initial, state))
            finally:
                self._semaphore.release()
        threading.Thread(target=run, name="laya-shadow-observer", daemon=True).start()
        return ticket

    def get_advice(self, message: str, context: dict | None = None) -> DecisionAdvice:
        """For an independent observer job; incumbent callers use start/collect."""
        ticket = self.start(message, context)
        ticket.done.wait(self.config.timeout_ms / 1000)
        return ticket.collect()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalLayaSDK:
    """Optional sidecar-only SDK. No automatic Hub download is permitted here."""
    def __init__(self, model_dir: str, identity: LayaIdentity):
        directory = Path(model_dir).resolve(strict=True)
        tokenizer_file = "tokenizer/tokenizer.json" if (directory/"tokenizer/tokenizer.json").is_file() else "tokenizer.json"
        for filename, expected in (("model.safetensors", identity.weights_sha256),
                                   ("rl_agent_config.json", identity.config_sha256),
                                   (tokenizer_file, identity.tokenizer_sha256),
                                   *identity.artifact_sha256.items()):
            path = (directory/filename).resolve(strict=True)
            if not path.is_relative_to(directory) or not path.is_file() or _file_sha256(path) != expected:
                raise ValueError("LOCAL_CHECKPOINT_FINGERPRINT_MISMATCH")
        # A VCS pin or independently recorded pinned-tree source digest is
        # required. A local wheel's version string alone is insufficient.
        distribution = importlib.metadata.distribution("laya")
        provenance = json.loads(distribution.read_text("direct_url.json") or "{}")
        vcs_verified = provenance.get("vcs_info", {}).get("commit_id") == identity.sdk_revision
        source_verified = False
        if identity.sdk_source_sha256 and provenance.get("dir_info") is not None:
            installed_dir = Path(distribution.locate_file("laya")).resolve(strict=True)
            source_verified = sdk_source_digest(installed_dir) == identity.sdk_source_sha256
        if distribution.version != identity.sdk_version or not (vcs_verified or source_verified):
            raise ValueError("SDK_PROVENANCE_MISMATCH")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["USE_TF"] = "0"
        import laya  # optional, deliberately absent from the host dependency set
        self.identity = identity
        self.agent = laya.load(str(directory), device="cpu", expected_sha256={
            "model.safetensors": identity.weights_sha256, "rl_agent_config.json": identity.config_sha256})
        if str(self.agent.device).split(":")[0] != "cpu":
            raise ValueError("SIDECAR_DEVICE_NOT_CPU")
        self.verification_complete = True
        self._lock = threading.Lock()

    def predict(self, state: str, *, max_len: int, head_max_len: int):
        with self._lock:
            return self.agent.system_one(state, QUESTION_DEFINITIONS, lang="en", max_len=max_len, head_max_len=head_max_len)


def sdk_source_digest(directory: Path) -> str:
    """Same canonical digest for the reviewed Git tree and installed wheel."""
    files = {path.relative_to(directory).as_posix(): _file_sha256(path)
             for path in sorted(directory.rglob("*.py")) if "__pycache__" not in path.parts}
    if not files:
        raise ValueError("EMPTY_SDK_SOURCE_TREE")
    return canonical_hash(files)


def create_sidecar_app(sdk: LocalLayaSDK, *, readonly_candidate_manifest=None):
    """Explicit factory, not an auto-started service. Real SDK construction is required."""
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    import secrets
    if not isinstance(sdk, LocalLayaSDK) or not getattr(sdk, "verification_complete", False):
        raise ValueError("SIDECAR_REQUIRES_VERIFIED_REAL_SDK")
    app = FastAPI(title="圳行Agent Laya CPU observer")
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        # Framework input diagnostics must not echo a credential-bearing state.
        return JSONResponse(status_code=422, content={"detail": "Observer request failed validation"})
    candidate_observer = candidate_pin = None
    if readonly_candidate_manifest is not None:
        from .laya_readonly_candidate import PinnedCapabilityBackend, ReadonlyCandidateObserver, QUESTIONS_HASH as candidate_questions_hash, policy_fingerprint
        if readonly_candidate_manifest.identity_sha256 != canonical_hash(sdk.identity.model_dump()) \
                or readonly_candidate_manifest.questions_sha256 != candidate_questions_hash \
                or readonly_candidate_manifest.policy_sha256 != policy_fingerprint() or readonly_candidate_manifest.adoption_enabled \
                or readonly_candidate_manifest.quality_gate_passed:
            raise ValueError("CANDIDATE_MANIFEST_FINGERPRINT_OR_GATE_MISMATCH")
        candidate_observer = ReadonlyCandidateObserver(PinnedCapabilityBackend(sdk), readonly_candidate_manifest)
        candidate_pin = ReadonlyCandidatePin(identity_sha256=readonly_candidate_manifest.identity_sha256,
            questions_sha256=readonly_candidate_manifest.questions_sha256, policy_sha256=readonly_candidate_manifest.policy_sha256,
            manifest_sha256=canonical_hash(readonly_candidate_manifest.model_dump()))
    service_capacity = threading.BoundedSemaphore(1)

    def authorize(authorization):
        key = os.getenv("ZX_LAYA_SIDECAR_API_KEY")
        if key and not secrets.compare_digest(authorization or "", f"Bearer {key}"):
            raise HTTPException(401, "Observer authentication required")

    def validate_candidate_input(payload):
        if redact_advice_input(payload.state) != payload.state:
            raise HTTPException(422, "Credential input is not accepted")
        if len(payload.state) > SIDECAR_MAX_CHARS or len(payload.state.encode("utf-8")) > SIDECAR_MAX_UTF8_BYTES \
                or getattr(payload, "max_len", 512) > 1024 or getattr(payload, "head_max_len", 256) > 256:
            raise HTTPException(422, "Observer input budget exceeded")
        tokenizer = sdk.agent.tok
        state = payload.state
        mask_token = getattr(tokenizer, "mask_token", None)
        if mask_token:
            state = state.replace(mask_token, " ")
        tokens = tokenizer(state, add_special_tokens=False, truncation=False)["input_ids"]
        if len(tokens) > 128:
            raise HTTPException(422, "Observer state token budget exceeded")
        if payload.readonly_candidate_pin is not None and payload.readonly_candidate_pin != candidate_pin:
            raise HTTPException(422, "Candidate fingerprint mismatch")

    def candidate_envelope(payload):
        if candidate_observer is None or payload.readonly_candidate_pin is None:
            return None
        observation = candidate_observer.observe(payload.state)
        return ReadonlyCandidateEnvelope(pin=candidate_pin, source_sha256=payload.source_hash,
            inference_input_sha256=canonical_hash({"state": payload.state}), observation=observation.model_dump()).model_dump()

    @app.get("/health")
    def health():
        return {"status": "ready", "device": "cpu", "identity": sdk.identity.model_dump(),
            "candidate_schema": "laya-readonly-candidate-v1" if candidate_observer else None,
            "candidate_pin": candidate_pin.model_dump() if candidate_pin else None,
            "gated": False, "candidate_adoption_enabled": False, "singleflight": True,
            "state_budget": {"max_chars": SIDECAR_MAX_CHARS, "max_utf8_bytes": SIDECAR_MAX_UTF8_BYTES, "max_state_tokens": 128,
                             "max_total_tokens_per_head": 1024, "max_head_tokens": 256},
            "default_shadow_deadline_ms": 1000}
    @app.post("/v1/decision-advice")
    def predict(payload: SidecarAdviceRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        validate_candidate_input(payload)
        if payload.questions_hash != QUESTIONS_HASH or payload.head_max_len >= payload.max_len:
            raise HTTPException(422, "Question identity or token budget mismatch")
        if not service_capacity.acquire(blocking=False):
            raise HTTPException(503, "Observer busy", headers={"Retry-After": "1"})
        try:
            result = sdk.predict(payload.state, max_len=payload.max_len, head_max_len=payload.head_max_len)
            response = {"identity": sdk.identity.model_dump(), "source_hash": payload.source_hash,
                        "questions_hash": QUESTIONS_HASH, "result": result}
            candidate = candidate_envelope(payload)
            if candidate is not None:
                response["readonly_candidate"] = candidate
            return response
        finally:
            service_capacity.release()

    @app.post("/v1/readonly-candidate")
    def readonly_candidate(payload: SidecarReadonlyCandidateRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        validate_candidate_input(payload)
        if candidate_observer is None or payload.readonly_candidate_pin is None:
            raise HTTPException(503, "Pinned candidate is not configured")
        if not service_capacity.acquire(blocking=False):
            raise HTTPException(503, "Observer busy", headers={"Retry-After": "1"})
        try:
            candidate = candidate_envelope(payload)
            return {"readonly_candidate": candidate, "standard_question_heads_executed": 0,
                    "candidate_choice_questions_completed": int(candidate["observation"]["sdk_inference_used"]), "candidate_options": 5}
        finally:
            service_capacity.release()
    return app
