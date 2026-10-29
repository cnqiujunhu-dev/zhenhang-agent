"""Shared owner checks and fingerprints; no inference, seeding or writes."""
from copy import deepcopy

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .models import User, utcnow
from .permissions import require_banking_actor
from .planning import canonical_hash
from .operational_clock import posting_now, registered_customer


SCHEMA_VERSION = "customer-profile-v1"
PREFERENCE_KINDS = {"CONSUMPTION": "消费偏好", "TRAVEL": "出行偏好"}
CUSTOMER_TIERS = {"STANDARD": "普通客户", "SILVER": "银级客户", "GOLD": "金级客户"}
CARD_TIERS = {"STANDARD": "普通卡", "GOLD": "金卡", "PLATINUM": "白金卡"}
READ_ACTIONS = {"MEMBERSHIP_VIEW", "BENEFITS_VIEW", "PREFERENCE_VIEW"}
WRITE_ACTIONS = {"PREFERENCE_SET", "PREFERENCE_REMOVE"}
ACTION_TOOLS = {
    "MEMBERSHIP_VIEW": "membership.view.v1", "BENEFITS_VIEW": "benefits.view.v1",
    "PREFERENCE_VIEW": "preference.view.v1", "PREFERENCE_SET": "preference.set.v1",
    "PREFERENCE_REMOVE": "preference.remove.v1",
}
TOOL_DEFINITIONS = {
    tool: {"read_only": action in READ_ACTIONS, "risk_level": "L1", "owner": "customer_profile"}
    for action, tool in ACTION_TOOLS.items()
}


def require_profile_actor(session, principal, *, lock=False):
    role = require_banking_actor(session, principal)
    if role not in {"CUSTOMER", "DEMO_OWNER", "DEV_TEST"} or getattr(principal, "family_context", None):
        raise DomainError("PROFILE_OWNER_REQUIRED", "请使用本人身份管理客户资料与偏好。", 403)
    query = select(User).where(User.id == principal.user_id, User.sandbox_id == principal.sandbox_id)
    owner = session.scalar(query.with_for_update() if lock else query)
    if owner is None:
        raise DomainError("PROFILE_OWNER_REQUIRED", "当前客户资料不属于本人。", 403)
    return owner


def profile_clock(session, principal):
    """Registered customers use now; isolated legacy fixtures keep their clock."""
    return as_utc(posting_now(session, principal.sandbox_id, principal.user_id))


def profile_time_basis(session, principal):
    return ("CURRENT_UTC" if registered_customer(session, principal.sandbox_id, principal.user_id)
            else "CONTROLLED_BUSINESS_CLOCK")


def read_body_fingerprint(result):
    # Observation/evaluation timestamps describe when a projection was read.
    # Actual eligibility, rule dates and source versions still participate, so
    # crossing an expiry or changing a card invalidates its earlier facts.
    ignored = {"observed_at", "eligibility_as_of", "read_fingerprint", "message"}
    return canonical_hash({key: value for key, value in result.items() if key not in ignored})


def finish_read(session, principal, action, payload, source_versions):
    result = {
        "schema_version": SCHEMA_VERSION, "action": action,
        "observed_at": as_utc(utcnow()).isoformat(),
        "owner_scope": canonical_hash({"sandbox_id": principal.sandbox_id, "user_id": principal.user_id}),
        "source_versions": deepcopy(source_versions), "bank_state_changed": False,
        "authority": "OWNED_REGISTERED_PROFILE_FACTS_NOT_WRITE_AUTHORIZATION",
        **deepcopy(payload),
    }
    result["read_fingerprint"] = read_body_fingerprint(result)
    return result


def preference_kind(value, *, optional=False):
    if value is None and optional:
        return None
    if value not in PREFERENCE_KINDS:
        raise DomainError("PREFERENCE_KIND_REQUIRED", "请明确消费偏好或出行偏好。", 422)
    return value
