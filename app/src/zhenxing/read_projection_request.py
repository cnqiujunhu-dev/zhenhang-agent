"""Bounded pure-input reuse inside one explicitly scoped history GET only.

No permission, audit conclusion, bank result or failure is cached here.  A
ContextVar keeps simultaneous sync request threads isolated; the session and
complete principal fingerprint additionally prevent accidental nested reuse.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field

from .planning import canonical_hash


_LIMIT = 64


def _owner(session, principal):
    return (id(session), principal.user_id, principal.sandbox_id, principal.role,
            principal.agent_id, canonical_hash(principal.family_context))


@dataclass
class _RequestInputs:
    owner: tuple
    values: dict = field(default_factory=dict)


_CURRENT: ContextVar[_RequestInputs | None] = ContextVar("history_read_projection_inputs", default=None)


@contextmanager
def read_projection_request(session, principal):
    owner = _owner(session, principal)
    current = _CURRENT.get()
    if current is not None and current.owner == owner:
        yield
        return
    token = _CURRENT.set(_RequestInputs(owner))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def prepare_read_input_once(session, principal, params, *, source_key):
    from .semantic_router import prepare_semantic_input

    current = _CURRENT.get()
    if current is None or current.owner != _owner(session, principal):
        return prepare_semantic_input(**params)
    key = canonical_hash({"source": source_key, "params": params})
    if key not in current.values:
        prepared = prepare_semantic_input(**params)  # Never retain exceptions.
        if len(current.values) < _LIMIT:
            current.values[key] = deepcopy(prepared)
        return prepared
    return deepcopy(current.values[key])
