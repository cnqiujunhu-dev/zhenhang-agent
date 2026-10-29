"""Pure read envelopes; observed time is not a business-state version."""
from copy import deepcopy

from .authentication import as_utc
from .models import utcnow
from .planning import canonical_hash


READ_ACTIONS = frozenset({"CREDIT_VIEW", "CREDIT_STATEMENT"})


def credit_read_fingerprint(result):
    return canonical_hash({key: value for key, value in result.items() if key not in {"observed_at", "read_fingerprint"}})


def finish_credit_read(principal, action, payload, query):
    result = {**deepcopy(payload), "action": action, "query": deepcopy(query), "bank_state_changed": False,
        "owner_scope": canonical_hash({"sandbox_id": principal.sandbox_id, "user_id": principal.user_id}),
        "observed_at": as_utc(utcnow()).isoformat()}
    result["read_fingerprint"] = credit_read_fingerprint(result)
    return result
