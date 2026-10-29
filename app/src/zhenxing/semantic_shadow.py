"""Durable native-dialogue Laya observations, never banking authority.

Enqueue is database-only. A separate daemon claims each source once, performs
bounded observer I/O outside transactions, then checks the source again. No
reply, semantic receipt, task, confirmation or route is changed by this module.
The process seal authorizes observation work only; durable metrics validate the
stored source envelope rather than treating that seal as bank authorization.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import hmac
from math import isfinite
import secrets
import threading
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from .conversation_models import ChatThread, ChatTurn
from .decision_advisor import compare_advice, redact_advice_input, QUESTION_DEFINITIONS, QUESTION_ORDER, QUESTIONS_HASH, OPTIONS_ORDER, DOMAIN_ORDER
from .decision_models import DecisionAdvice, CoverageDifference
from .dialogue_state import dialogue_actor_scope
from .models import utcnow
from .planning import canonical_hash
from .redaction import redact_credentials
from .services import Principal
from .contracts import DomainError

KEY = "semantic_shadow_observation"
VERSION = "semantic-laya-shadow-v1"
_PROCESS_ID = uuid4().hex
_SEAL_KEY = secrets.token_bytes(32)
_DB_LOCK = threading.RLock()
_QUEUE_SECONDS = 30
_SUPPORTED = frozenset({"BILL_ANALYSIS", "TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST",
    "WEALTH_COMPARE", "WEALTH_BUY", "WEALTH_REDEEM", "CARD_STATUS", "CARD_LOCK",
    "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT",
    "SUBSCRIPTION_LIST", "SUBSCRIPTION_CANCEL"})
_FINAL = frozenset({"READY", "OFF", "SKIPPED", "TIMED_OUT", "UNAVAILABLE", "ERROR",
    "BUSY", "BUDGET_EXCEEDED", "INVALIDATED"})
_BINDING_FIELDS = frozenset({"turn_id", "thread_id", "user_id", "sandbox_id", "sequence",
    "source_hash", "request_hash", "receipt_hash", "actor_scope_hash", "semantic_mode",
    "action_kinds", "task_id"})
_MARKER_FIELDS = _BINDING_FIELDS | frozenset({"schema_version", "binding", "process_id", "process_seal",
    "status", "coverage", "agreement", "advice", "created_at", "queue_deadline_at", "lease_owner",
    "lease_expires_at", "attempt_count", "authority", "adopted", "bank_write_authorized",
    "main_model_calls_saved", "production_acceleration_claim", "completed_at"})


@contextmanager
def _transaction(factory):
    # PostgreSQL row locks protect cross-process claims. A short process-local
    # mutex also gives SQLite (which ignores FOR UPDATE) the same claim rule.
    # No mutex or transaction is held while invoking the observer.
    with _DB_LOCK:
        with factory.begin() as session:
            yield session


def _locks(session, turn_id):
    # Never lock ChatTurn before its ChatThread: the primary dialogue lease
    # acquires the same order. The preliminary lookup takes no row lock.
    thread_id = session.scalar(select(ChatTurn.thread_id).where(ChatTurn.id == turn_id))
    if thread_id is None:
        return None, None
    thread = session.scalar(select(ChatThread).where(ChatThread.id == thread_id).with_for_update())
    turn = session.scalar(select(ChatTurn).where(ChatTurn.id == turn_id).with_for_update())
    return thread, turn


def _source(session, thread, turn):
    from .semantic_router import SemanticDecision, VERSION as receipt_version
    if thread is None or turn is None or thread.status != "ACTIVE" or turn.status != "ANSWERED" \
            or turn.error_code or turn.thread_id != thread.id \
            or (turn.user_id, turn.sandbox_id) != (thread.user_id, thread.sandbox_id):
        return None
    diagnostics = turn.diagnostics_json or {}
    receipt = diagnostics.get("semantic_receipt")
    if diagnostics.get("semantic_processing_error") or diagnostics.get("input_redacted") \
            or diagnostics.get("credential_only") or redact_credentials(turn.message) != turn.message \
            or not isinstance(receipt, dict) or receipt.get("version") != receipt_version \
            or receipt.get("status") != "READY" or receipt.get("purpose") != "UNIFIED_SEMANTIC_UNDERSTANDING" \
            or receipt.get("authority") != "MODEL_SEMANTICS_NOT_BANK_AUTHORITY":
        return None
    try:
        decision = SemanticDecision.model_validate(receipt["decision"], strict=True)
        model = receipt["diagnostics"]
        if receipt["decision_hash"] != canonical_hash(decision.model_dump()) \
                or receipt["source_message_hash"] != canonical_hash({"message": turn.message}) \
                or model.get("model_api_call_count") != 1 or model.get("fallback") is not False:
            return None
        principal = Principal(turn.user_id, turn.sandbox_id)
        scope, scope_hash = dialogue_actor_scope(session, principal)
        if diagnostics.get("dialogue_actor_scope_hash") != scope_hash \
                or diagnostics.get("actor_role_at_submission") != scope["role"]:
            return None
        return {"turn_id": turn.id, "thread_id": thread.id, "user_id": turn.user_id,
            "sandbox_id": turn.sandbox_id, "sequence": turn.sequence,
            "source_hash": receipt["source_message_hash"], "request_hash": turn.request_hash,
            "receipt_hash": canonical_hash(receipt), "actor_scope_hash": scope_hash,
            "semantic_mode": decision.mode, "action_kinds": list(decision.action_kinds),
            "task_id": turn.task_id}, decision
    except (ValueError, KeyError, TypeError, DomainError):
        return None


def _sign(binding):
    return hmac.new(_SEAL_KEY, canonical_hash(binding).encode("ascii"), hashlib.sha256).hexdigest()


def _signed(marker):
    binding = marker.get("binding")
    return isinstance(binding, dict) and set(binding) == _BINDING_FIELDS \
        and marker.get("process_id") == _PROCESS_ID and isinstance(marker.get("process_seal"), str) \
        and hmac.compare_digest(marker["process_seal"], _sign(binding))


def _put(turn, marker):
    turn.diagnostics_json = {**(turn.diagnostics_json or {}), KEY: marker}


def _coverage(turn, decision):
    if decision.mode != "REQUEST":
        return {"status": "UNKNOWN", "reason": "SEMANTIC_MODE_NOT_STANDALONE_REQUEST"}
    if decision.focus_ref or decision.entity_refs or turn.requested_task_id or turn.reviewed_evidence_refs_json:
        return {"status": "UNKNOWN", "reason": "CONTEXT_REFERENCES_REQUIRE_PRIMARY"}
    if not decision.action_kinds or any(kind not in _SUPPORTED for kind in decision.action_kinds):
        return {"status": "UNKNOWN", "reason": "UNSUPPORTED_CAPABILITY"}
    return {"status": "MATCHABLE", "reason": "STANDALONE_SUPPORTED_REQUEST"}


def enqueue_turn(factory, advisor, turn_id, verified_result=None):
    """Zero-network enqueue; existing marker wins before result validation.

    Only the primary caller holding an authentic private semantic result may
    schedule inference. Serialized JSON alone cannot launch observer work.
    Returns True for a new marker, False for a duplicate or invalid source.
    """
    from .semantic_router import is_verified_semantic_result, serialize_semantic_receipt
    with _transaction(factory) as session:
        thread, turn = _locks(session, turn_id)
        if turn is None or isinstance((turn.diagnostics_json or {}).get(KEY), dict):
            return False
        source = _source(session, thread, turn)
        if source is None or not is_verified_semantic_result(verified_result):
            return False
        binding, decision = source
        if canonical_hash(serialize_semantic_receipt(verified_result)) != binding["receipt_hash"]:
            return False
        coverage = _coverage(turn, decision)
        marker = {"schema_version": VERSION, **binding, "binding": binding,
            "process_id": _PROCESS_ID, "process_seal": _sign(binding),
            "status": "QUEUED", "coverage": coverage,
            "agreement": CoverageDifference(agreement="UNKNOWN").model_dump(), "advice": None,
            "created_at": utcnow().isoformat(), "queue_deadline_at": (utcnow()+timedelta(seconds=_QUEUE_SECONDS)).isoformat(),
            "lease_owner": None, "lease_expires_at": None, "attempt_count": 0,
            "authority": "OBSERVATION_ONLY_NOT_BANK_PERMISSION", "adopted": False,
            "bank_write_authorized": False, "main_model_calls_saved": 0,
            "production_acceleration_claim": False}
        if coverage["status"] != "MATCHABLE":
            marker["status"] = "SKIPPED"
        elif redact_advice_input(turn.message) != turn.message:
            marker["status"] = "SKIPPED"
            marker["coverage"] = {"status": "UNKNOWN", "reason": "OBSERVER_REDACTION_REQUIRED"}
        elif getattr(getattr(advisor, "config", None), "mode", "OFF") == "OFF":
            marker["status"] = "OFF"
        elif len(turn.message) > 512 or len(turn.message.encode("utf-8")) > 1536:
            marker["status"] = "BUDGET_EXCEEDED"
            marker["coverage"] = {"status": "UNKNOWN", "reason": "OBSERVER_INPUT_BUDGET"}
        if marker["status"] != "QUEUED":
            marker["completed_at"] = utcnow().isoformat()
        _put(turn, marker)
        return True


def _expired(value):
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= utcnow()
    except (ValueError, TypeError):
        return True


def _invalidate(turn, marker, reason, status="INVALIDATED"):
    _put(turn, {**marker, "status": status, "coverage": {"status": "UNKNOWN", "reason": reason},
        "advice": None, "agreement": CoverageDifference(agreement="UNKNOWN").model_dump(),
        "completed_at": utcnow().isoformat()})


def process_one(factory, advisor, worker_id):
    """Claim one durable observation. I/O never occurs in a DB transaction."""
    with factory() as session:
        identifiers = list(session.scalars(select(ChatTurn.id).where(
            ChatTurn.diagnostics_json[KEY]["status"].as_string().in_(["QUEUED", "LEASED"]))
            .order_by(ChatTurn.created_at).limit(50)))
    claimed = None
    for turn_id in identifiers:
        with _transaction(factory) as session:
            thread, turn = _locks(session, turn_id)
            marker = (turn.diagnostics_json or {}).get(KEY) if turn else None
            if not isinstance(marker, dict) or marker.get("status") not in {"QUEUED", "LEASED"}:
                continue
            if marker["status"] == "LEASED":
                if _expired(marker.get("lease_expires_at")):
                    _invalidate(turn, marker, "LEASE_EXPIRED_NO_REISSUE", "TIMED_OUT")
                    return True
                continue
            if marker.get("process_id") != _PROCESS_ID:
                if _expired(marker.get("queue_deadline_at")):
                    _invalidate(turn, marker, "SOURCE_PROOF_EXPIRED", "SKIPPED")
                    return True
                continue  # Another live process owns this source proof.
            source = _source(session, thread, turn)
            if not _signed(marker) or source is None or source[0] != marker.get("binding"):
                _invalidate(turn, marker, "SOURCE_CHANGED_BEFORE_OBSERVATION")
                return True
            if _expired(marker.get("queue_deadline_at")):
                _invalidate(turn, marker, "SOURCE_PROOF_EXPIRED", "SKIPPED")
                return True
            timeout = float(getattr(getattr(advisor, "config", None), "timeout_ms", 1000)) / 1000
            marker = {**marker, "status": "LEASED", "lease_owner": worker_id,
                "lease_expires_at": (utcnow()+timedelta(seconds=min(max(timeout, 1), 5)+5)).isoformat(),
                "attempt_count": 1}
            _put(turn, marker)
            claimed = (turn.id, turn.message, deepcopy(marker))
            break
    if claimed is None:
        return False
    turn_id, message, original = claimed
    try:
        advice = advisor.get_advice(message)
        advice = DecisionAdvice.model_validate(advice.model_dump(), strict=True)
        configured_identity = getattr(getattr(advisor, "config", None), "identity", None)
        if advice.source_hash != original["source_hash"] \
                or advice.inference_input_hash != canonical_hash({"state": redact_advice_input(message)}) \
                or advice.input_redacted or advice.status == "READY" and advice.identity != configured_identity:
            raise ValueError("OBSERVER_SOURCE_MISMATCH")
        proposal = SimpleNamespace(actions=[SimpleNamespace(kind=kind) for kind in original["action_kinds"]])
        advice = compare_advice(advice, proposal)
    except Exception:
        advice = None
    with _transaction(factory) as session:
        thread, turn = _locks(session, turn_id)
        marker = (turn.diagnostics_json or {}).get(KEY) if turn else None
        if not isinstance(marker, dict) or marker.get("status") != "LEASED" or marker.get("lease_owner") != worker_id:
            return True
        source = _source(session, thread, turn)
        if not _signed(marker) or source is None or source[0] != original["binding"]:
            _invalidate(turn, marker, "SOURCE_CHANGED_DURING_OBSERVATION")
        elif _expired(marker.get("lease_expires_at")):
            _invalidate(turn, marker, "LEASE_EXPIRED_NO_REISSUE", "TIMED_OUT")
        elif advice is None:
            _invalidate(turn, marker, "OBSERVER_INVALID_RESPONSE", "ERROR")
        else:
            status = advice.status if advice.status in _FINAL else "TIMED_OUT"
            _put(turn, {**marker, "status": status, "advice": advice.model_dump(),
                "agreement": advice.coverage_difference.model_dump(), "completed_at": utcnow().isoformat()})
    return True


def validated_observation(session, turn):
    """Current-source actual SDK evidence for admin metrics; no work is issued.

    Cross-process readers recheck durable hashes and live actor scope, not the
    worker's process-local seal. This is observation evidence, not permission.
    """
    marker = (turn.diagnostics_json or {}).get(KEY)
    if not isinstance(marker, dict) or marker.get("schema_version") != VERSION or marker.get("status") != "READY" \
            or not set(marker) <= _MARKER_FIELDS \
            or marker.get("authority") != "OBSERVATION_ONLY_NOT_BANK_PERMISSION" \
            or marker.get("adopted") is not False or marker.get("bank_write_authorized") is not False \
            or type(marker.get("main_model_calls_saved")) is not int or marker["main_model_calls_saved"] != 0 \
            or marker.get("production_acceleration_claim") is not False:
        return None
    try:
        thread = session.get(ChatThread, turn.thread_id)
        source = _source(session, thread, turn)
        binding = marker.get("binding")
        if source is None or not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS \
                or source[0] != binding or any(marker.get(key) != value for key, value in binding.items()) \
                or marker.get("coverage") != _coverage(turn, source[1]) \
                or marker["coverage"]["status"] != "MATCHABLE" \
                or type(marker.get("attempt_count")) is not int or marker["attempt_count"] != 1:
            return None
        advice = DecisionAdvice.model_validate(marker["advice"], strict=True)
        if advice.status != "READY" or advice.mode != "SHADOW" or advice.laya_used is not True \
                or advice.backend != "LAYA_SDK_SIDECAR" or advice.identity is None or not advice.answers \
                or advice.input_redacted or advice.usage.truncated is not False or advice.usage.state_tokens_dropped != 0 \
                or advice.source_hash != binding["source_hash"] \
                or advice.inference_input_hash != canonical_hash({"state": turn.message}) \
                or advice.latency_ms is None or not isfinite(advice.latency_ms) or not isfinite(advice.timeout_ms) \
                or advice.latency_ms > advice.timeout_ms \
                or advice.question_order != list(QUESTION_ORDER) or advice.option_order != OPTIONS_ORDER \
                or advice.question_definitions != QUESTION_DEFINITIONS or advice.questions_hash != QUESTIONS_HASH \
                or [answer.question_id for answer in advice.answers] != list(QUESTION_ORDER) \
                or advice.coverage_difference.model_dump() != marker.get("agreement"):
            return None
        scores = {}
        for answer in advice.answers:
            if answer.probabilities != {"false": 1-answer.raw_p_true, "true": answer.raw_p_true}:
                return None
            scores[answer.question_id] = answer.raw_p_true
        if advice.candidate_domains != [domain for domain in DOMAIN_ORDER if scores["domain_"+domain.lower()] >= .5] \
                or advice.multi_step_advice != (scores["multi_step"] >= .5) \
                or advice.cross_domain_advice != (scores["cross_domain"] >= .5):
            return None
        expected = compare_advice(advice, SimpleNamespace(actions=[SimpleNamespace(kind=kind)
            for kind in source[1].action_kinds]))
        if any(getattr(advice, field) != getattr(expected, field) for field in
               ("primary_domains", "primary_multi_step", "primary_cross_domain", "unmapped_actions", "coverage_difference")):
            return None
        return deepcopy(marker)
    except Exception:
        return None


class _WorkerHandle:
    def __init__(self, factory, advisor):
        self._stop = threading.Event()
        self._worker_id = "semantic-laya-"+uuid4().hex
        def run():
            while not self._stop.is_set():
                try:
                    handled = process_one(factory, advisor, self._worker_id)
                except Exception:
                    handled = False  # Never fail or log primary customer text.
                if not handled:
                    self._stop.wait(.25)
        self._thread = threading.Thread(target=run, name="semantic-laya-observer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)


def start_worker(factory, advisor):
    """Separate observer loop; the main financial worker never waits on Laya."""
    if advisor is None or getattr(getattr(advisor, "config", None), "mode", "OFF") == "OFF":
        return None
    return _WorkerHandle(factory, advisor)
