"""Versioned policy helpers and provider-neutral, permission-neutral selection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time

from sqlalchemy import update
from sqlalchemy.orm import Session

from .intelligence import READ_KINDS, domain_for_action, rule_proposal
from .routing_models import ModelRoutingPolicy, RoutingPolicy


class RoutingPolicyConflict(ValueError):
    pass


@dataclass(frozen=True)
class RegisteredModel:
    provider: str
    model: str
    adapter: object
    is_cloud: bool
    credential_configured: bool = False


class ProviderRegistry:
    def __init__(self):
        self._models: dict[tuple[str, str], RegisteredModel] = {}
        self._circuits: dict[tuple[str, str], dict] = {}
        self._circuit_lock = threading.Lock()

    def register(self, provider: str, model: str, adapter: object, *, is_cloud: bool,
                 credential_configured: bool = False):
        if not provider or not model or not callable(getattr(adapter, "propose", None)):
            raise ValueError("INVALID_PROVIDER_REGISTRATION")
        self._models[(provider, model)] = RegisteredModel(provider, model, adapter, is_cloud, credential_configured)

    def get(self, provider: str, model: str) -> RegisteredModel | None:
        return self._models.get((provider, model))

    def validate_policy(self, policy: RoutingPolicy):
        local = self.get("ollama", policy.local_model)
        if policy.mode != "CLOUD_ONLY" and (local is None or local.is_cloud):
            raise ValueError("UNREGISTERED_LOCAL_MODEL")
        cloud = self.get(policy.cloud_provider, policy.cloud_model)
        if cloud is None or not cloud.is_cloud:
            raise ValueError("UNREGISTERED_CLOUD_MODEL")
        if policy.cloud_enabled:
            if not cloud.credential_configured:
                raise ValueError("UNCONFIGURED_CLOUD_MODEL")
        if policy.cloud_secondary_model:
            secondary = self.get(policy.cloud_provider, policy.cloud_secondary_model)
            if secondary is None or not secondary.is_cloud or (policy.cloud_enabled and not secondary.credential_configured):
                raise ValueError("UNREGISTERED_SECONDARY_CLOUD_MODEL")

    def circuit_allows(self, candidate: RegisteredModel) -> bool:
        with self._circuit_lock:
            state = self._circuits.get((candidate.provider, candidate.model)) or {}
            return state.get("opened_until", 0) <= time.monotonic()

    def record_availability(self, candidate: RegisteredModel, error_code: str | None):
        # Model quality is NOT provider unavailability. Keep these separate.
        transport = {"MODEL_TIMEOUT", "MODEL_HTTP_ERROR", "MODEL_AUTH_ERROR", "MODEL_PAYMENT_REQUIRED", "MODEL_RATE_LIMIT"}
        with self._circuit_lock:
            key = (candidate.provider, candidate.model)
            state = self._circuits.setdefault(key, {"failures": 0, "opened_until": 0})
            if error_code not in transport:
                state.update(failures=0, opened_until=0)
            elif error_code in transport:
                state["failures"] += 1
                if state["failures"] >= 3:
                    state["opened_until"] = time.monotonic() + 30

    def circuit_snapshot(self, candidate: RegisteredModel) -> dict:
        with self._circuit_lock:
            state = self._circuits.get((candidate.provider, candidate.model)) or {}
            remaining = max(0, state.get("opened_until", 0) - time.monotonic())
            return {"circuit_status": "OPEN" if remaining else "CLOSED",
                    "availability_failures": state.get("failures", 0), "retry_after_seconds": round(remaining, 1),
                    "scope": "PROCESS_LOCAL_NOT_SHARED_PROVIDER_HEALTH"}

    def status(self) -> dict:
        providers = []
        for provider in sorted({row.provider for row in self._models.values()}):
            rows = [row for row in self._models.values() if row.provider == provider]
            providers.append({"provider_id": provider, "configured": not rows[0].is_cloud or any(row.credential_configured for row in rows),
                "credential_configured": any(row.credential_configured for row in rows),
                "is_cloud": rows[0].is_cloud, "circuit_status": "PER_MODEL_PROCESS_LOCAL",
                "models": [{"model": row.model,
                    "last_http_available": getattr(row.adapter, "last_http_available", None),
                    **self.circuit_snapshot(row)} for row in rows]})
        return {"providers": providers,
            "availability_meaning": "最近HTTP结果或未知；配置不代表当前可达，也不代表语义准确率。"}


@dataclass(frozen=True)
class RouteSelection:
    candidates: tuple[RegisteredModel, ...]
    reason: str
    preferred_provider: str
    preferred_model: str
    initially_degraded: bool = False


def select_route(registry: ProviderRegistry, policy: RoutingPolicy, message: str) -> RouteSelection:
    """Rule shapes estimate cost/complexity only; cannot grant or prune actions."""
    local = registry.get("ollama", policy.local_model)
    if local is not None and local.is_cloud:
        local = None
    cloud = registry.get(policy.cloud_provider, policy.cloud_model) if policy.cloud_enabled else None
    if cloud is not None and (not cloud.is_cloud or not cloud.credential_configured):
        cloud = None
    if policy.mode == "CLOUD_ONLY":
        secondary = registry.get(policy.cloud_provider, policy.cloud_secondary_model) if policy.cloud_enabled and policy.cloud_secondary_model else None
        rows = [row for row in (cloud, secondary) if row and row.is_cloud and row.credential_configured and registry.circuit_allows(row)]
        unique = tuple({(row.provider, row.model): row for row in rows}.values())
        return RouteSelection(unique, "STRICT_CLOUD_ONLY_NO_LOCAL_ALTERNATE", policy.cloud_provider, policy.cloud_model, not unique or cloud not in unique)
    prefer_cloud = policy.mode == "PREFER_CLOUD"
    reason = "LOCAL_ONLY_NO_CLOUD"
    if policy.mode == "AUTO":
        baseline = rule_proposal(message)
        domains = {domain_for_action(action.kind) for action in baseline.actions}
        extension = any(action.kind.startswith(("FAMILY_", "GOAL_")) for action in baseline.actions)
        complex_request = len(baseline.actions) > 1 or len(domains) > 1 or extension
        prefer_cloud = complex_request
        reason = "AUTO_COMPLEX_OR_FAMILY_GOAL" if complex_request else (
            "AUTO_SIMPLE_READ" if baseline.actions and all(action.kind in READ_KINDS for action in baseline.actions)
            else "AUTO_SINGLE_STEP_OR_UNKNOWN_LOCAL")
    elif policy.mode == "PREFER_CLOUD":
        reason = "ADMIN_PREFER_CLOUD"
    if policy.mode == "LOCAL_ONLY":
        return RouteSelection(tuple([local] if local else []), reason, "ollama", policy.local_model, local is None)
    preferred_provider, preferred_model = (policy.cloud_provider, policy.cloud_model) if prefer_cloud else ("ollama", policy.local_model)
    preferred, alternate = (cloud, local) if prefer_cloud else (local, cloud)
    rows = tuple(row for row in (preferred, alternate) if row is not None)
    degraded = preferred is None
    if degraded:
        reason += ":PREFERRED_NOT_CONFIGURED"
    return RouteSelection(rows, reason, preferred_provider, preferred_model, degraded)


def read_routing_policy(session: Session) -> RoutingPolicy:
    row = session.get(ModelRoutingPolicy, "global")
    if row is None:
        return RoutingPolicy()
    return RoutingPolicy.model_validate({**row.policy_json, "version": row.version})


def update_routing_policy(session: Session, expected_version: int, policy: RoutingPolicy | dict,
                          actor_id: str, registry: ProviderRegistry) -> RoutingPolicy:
    """Caller authenticates admin; CAS is atomic, with no commit hidden here."""
    validated = policy if isinstance(policy, RoutingPolicy) else RoutingPolicy.model_validate(policy)
    registry.validate_policy(validated)
    if not actor_id or len(actor_id) > 80:
        raise ValueError("INVALID_ADMIN_ACTOR")
    row = session.get(ModelRoutingPolicy, "global")
    if row is None:
        # Parent initializes this singleton at schema migration/startup.
        raise RoutingPolicyConflict("ROUTING_POLICY_NOT_INITIALIZED")
    updated = validated.model_copy(update={"version": expected_version + 1})
    result = session.execute(update(ModelRoutingPolicy).where(ModelRoutingPolicy.id == "global",
        ModelRoutingPolicy.version == expected_version).values(version=updated.version,
            policy_json=updated.model_dump(exclude={"version"}),
            updated_at=datetime.now(timezone.utc), updated_by=actor_id))
    if result.rowcount != 1:
        raise RoutingPolicyConflict("ROUTING_POLICY_VERSION_CONFLICT")
    session.expire(row)
    return updated


def initialize_routing_policy(session: Session, policy: RoutingPolicy | None = None):
    """Migration/startup convenience; does not overwrite an existing policy."""
    if session.get(ModelRoutingPolicy, "global") is None:
        selected = policy or RoutingPolicy()
        session.add(ModelRoutingPolicy(id="global", version=selected.version,
            policy_json=selected.model_dump(exclude={"version"}), updated_by=None))
        session.flush()
