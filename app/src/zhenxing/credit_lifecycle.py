"""Versioned card controls and explicit simulated application decisions."""
from copy import deepcopy

from sqlalchemy import select

from .credit_billing import owned_credit_card
from .credit_bootstrap import issue_credit_card
from .credit_models import CreditApplication, CreditCard, PaymentInstrumentBinding
from .credit_service import _facility, _fail, _operation, _record
from .planning import canonical_hash
from .credit_atomic import atomic_domain


@atomic_domain
def control_credit_card(session, principal, *, card_reference, action, operation_key, expected_card_version=None,
        limit_minor=None):
    card = owned_credit_card(session, principal, card_reference)
    digest, prior = _operation(session, principal, operation_key, "CARD_CONTROL", {"card_id": card.id,
        "action": action, "limit_minor": limit_minor})
    if prior: return prior
    _facility(session, principal, card.credit_account_id)
    card = session.scalar(select(CreditCard).where(CreditCard.id == card.id).with_for_update().execution_options(populate_existing=True))
    if expected_card_version is not None and card.version != expected_card_version: _fail("CREDIT_CARD_VERSION_CHANGED")
    previous, replacement = card.status, None
    if action == "LOCK" and card.status == "ACTIVE": card.status = "LOCKED"
    elif action == "UNLOCK" and card.status == "LOCKED": card.status = "ACTIVE"
    elif action == "REPORT_LOSS" and card.status in {"ACTIVE", "LOCKED", "ISSUED"}: card.status = "LOST"
    elif action == "ACTIVATE" and card.status == "ISSUED": card.status = "ACTIVE"
    elif action == "SET_LIMIT" and card.status == "ACTIVE":
        if type(limit_minor) is not int or not 10_000 <= limit_minor <= 2_000_000: _fail("CREDIT_CARD_LIMIT_INVALID", status=422)
        card.transaction_limit_minor = limit_minor
    elif action == "REQUEST_REPLACEMENT" and card.status == "LOST":
        replacement = issue_credit_card(session, _facility(session, principal, card.credit_account_id), status="ISSUED")
        card.status = "REPLACED"
    else: _fail("CREDIT_CARD_STATE_INVALID", "卡片状态不支持这项操作；挂失不能通过解锁恢复。")
    card.version += 1
    binding = session.get(PaymentInstrumentBinding, "credit:" + card.id)
    if binding: binding.version += 1
    result = {"schema_version": "credit-card-control-v1", "action": action, "card_id": card.id,
        "instrument_type": "CREDIT", "card_number": card.card_number, "previous_status": previous,
        "status": card.status, "version": card.version, "limit_minor": card.transaction_limit_minor,
        "replacement_card": ({"card_id": replacement.id, "card_number": replacement.card_number,
            "status": replacement.status, "requires_activation": True} if replacement else None),
        "cash_debit_minor": 0, "replayed": False}
    return _record(session, principal, operation_key, "CARD_CONTROL", digest, result)


@atomic_domain
def submit_credit_application(session, principal, *, kind, operation_key, card_reference=None, requested_limit_minor=None, credit_account_id=None):
    if kind not in {"CARD", "LIMIT"}: _fail("CREDIT_APPLICATION_KIND_INVALID", status=422)
    from .customer_profile_common import require_profile_actor
    require_profile_actor(session, principal, lock=True)
    if credit_account_id is not None and (kind != 'CARD' or card_reference is not None):
        _fail('CREDIT_APPLICATION_SCOPE_INVALID')
    digest, prior = _operation(session, principal, operation_key, "APPLICATION", {"kind": kind,
        "card_reference": card_reference, "requested_limit_minor": requested_limit_minor,
        **({'credit_account_id': credit_account_id} if credit_account_id is not None else {})})
    if prior: return prior
    cards = list(session.scalars(select(CreditCard).where(CreditCard.sandbox_id == principal.sandbox_id, CreditCard.user_id == principal.user_id)))
    if credit_account_id is not None:
        from .credit_preparation_contract import application_facility
        selected = application_facility(session, principal)
        if selected is None or selected.id != credit_account_id:
            _fail('CREDIT_APPLICATION_SCOPE_INVALID')
        card, facility = None, _facility(session, principal, credit_account_id)
    else:
        card = owned_credit_card(session, principal, card_reference) if cards or card_reference or kind == "LIMIT" else None
        facility = _facility(session, principal, card.credit_account_id) if card else None
    if kind == "LIMIT" and (type(requested_limit_minor) is not int or not 10_000 <= requested_limit_minor <= 10_000_000):
        _fail("CREDIT_LIMIT_REQUEST_INVALID", status=422)
    row = CreditApplication(sandbox_id=principal.sandbox_id, user_id=principal.user_id, kind=kind,
        credit_account_id=facility.id if facility else None, requested_limit_minor=requested_limit_minor,
        operation_key=operation_key, request_hash=digest)
    session.add(row); session.flush()
    result = {"schema_version": "credit-application-v1", "action": "CREDIT_APPLY" if kind == "CARD" else "CREDIT_LIMIT_REQUEST",
        "application_id": row.id, "kind": kind, "status": "PENDING_REVIEW", "requested_limit_minor": requested_limit_minor,
        "credit_account_id": facility.id if facility else None, "cash_debit_minor": 0, "credit_limit_changed": False,
        "adult_eligibility_verified": bool(facility and facility.policy_json.get("adult_eligible") is True),
        "decision_authority": "REGISTERED_SIMULATED_POLICY_REVIEW_NOT_LLM", "replayed": False}
    return _record(session, principal, operation_key, "APPLICATION", digest, result)


@atomic_domain
def decide_credit_application(session, principal, application_id, *, approved, operation_key,
        adult_eligible=False, policy_version="sim-credit-review-v1"):
    """Trusted ADMIN adapter only. Customer runtime cannot call this action."""
    from .access_models import LoginAccount
    from .permissions import actor_role
    from .services import Principal
    from .credit_service import _capacity
    from .payment_instruments import _key
    # ADMIN may not pass require_profile_actor; role gate is separate and exact.
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    if role != "ADMIN": _fail("CREDIT_REVIEW_ADMIN_REQUIRED", status=403)
    _key(operation_key)
    if type(approved) is not bool or policy_version != "sim-credit-review-v1": _fail("CREDIT_REVIEW_POLICY_INVALID", status=422)
    row = session.get(CreditApplication, application_id)
    if row is None: _fail("CREDIT_APPLICATION_NOT_FOUND", status=404)
    owner = Principal(row.user_id, row.sandbox_id)
    if type(adult_eligible) is not bool: _fail("CREDIT_REVIEW_POLICY_INVALID", status=422)
    digest = canonical_hash({"application_id": row.id, "approved": approved, "adult_eligible": adult_eligible, "policy_version": policy_version})
    from .customer_profile_common import require_profile_actor
    require_profile_actor(session, owner, lock=True)
    row = session.scalar(select(CreditApplication).where(CreditApplication.id == row.id).with_for_update().execution_options(populate_existing=True))
    if row.status != "PENDING_REVIEW":
        if row.decision_json.get("request_hash") != digest: _fail("CREDIT_APPLICATION_ALREADY_DECIDED")
        return {**deepcopy(row.decision_json), "replayed": True}
    facility = _facility(session, owner, row.credit_account_id) if row.credit_account_id else None
    issued = None
    if approved and row.kind == "CARD" and facility is None:
        if not adult_eligible: _fail("CREDIT_ADULT_ELIGIBILITY_REQUIRED", "首次审批必须由审核员明确核验成年样例条件。", 403)
        from .credit_bootstrap import initialize_adult_credit_sample
        from .credit_models import CreditAccount
        facility = session.scalar(select(CreditAccount).where(CreditAccount.sandbox_id == row.sandbox_id,
            CreditAccount.user_id == row.user_id, CreditAccount.facility_key == "PRIMARY"))
        if facility is None:
            facility, issued = initialize_adult_credit_sample(session, row.sandbox_id, row.user_id,
                eligibility_source="ADMIN_VERIFIED_ADULT_SAMPLE:"+principal.user_id, initial_card_status="ISSUED", application_id=row.id)
        row.credit_account_id = facility.id
    if approved and (facility is None or facility.policy_json.get("adult_eligible") is not True or not facility.policy_json.get("eligibility_source")):
        _fail("CREDIT_ADULT_ELIGIBILITY_REQUIRED", status=403)
    if approved and row.kind == "LIMIT":
        if row.requested_limit_minor < facility.posted_exposure_minor+facility.authorization_exposure_minor:
            _fail("CREDIT_LIMIT_BELOW_EXPOSURE")
        facility.base_credit_limit_minor = row.requested_limit_minor; facility.version += 1
    if approved and row.kind == "CARD":
        new_card = issued or issue_credit_card(session, facility, application_id=row.id)
        row.card_id = new_card.id
    row.status = "APPROVED" if approved else "REJECTED"; row.version += 1
    row.decision_json = {"application_id": row.id, "status": row.status, "card_id": row.card_id,
        "base_credit_limit_minor": facility.base_credit_limit_minor if facility else None, "request_hash": digest,
        "reviewer_user_id": principal.user_id, "policy_version": policy_version, "operation_key": operation_key,
        "requires_activation": bool(row.card_id), "adult_eligibility_verified": bool(facility and facility.policy_json.get("adult_eligible") is True), "replayed": False}
    session.flush()
    return deepcopy(row.decision_json)


def list_credit_applications(session, principal):
    from .permissions import actor_role
    if actor_role(session, principal.sandbox_id, principal.user_id) != "ADMIN": _fail("CREDIT_REVIEW_ADMIN_REQUIRED", status=403)
    from .access_models import LoginAccount
    usernames={row.user_id:row.username for row in session.scalars(select(LoginAccount))}
    return {"applications": [{"application_id": row.id, "kind": row.kind, "status": row.status,
        "username": usernames.get(row.user_id),
        "requested_limit_minor": row.requested_limit_minor, "card_id": row.card_id,
        "user_id": row.user_id, "version": row.version} for row in session.scalars(select(CreditApplication).order_by(CreditApplication.created_at).limit(200))]}
