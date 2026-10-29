"""Explicit family contexts never turn a child's own customer account into a helper.

Only the returned short-lived token identifies the accountless proxy in a parent's
bank scope.  The caller's original customer session is neither changed nor deleted.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import secrets

from sqlalchemy import select

from .access_models import AccessAudit, LoginAccount
from .contracts import DomainError
from .extensions_models import FamilyDelegation, FamilyMember
from .family_account_context_models import FamilyAccountLink, FamilyContextSession
from .models import Account, DemoSession, User, utcnow
from .simulation import as_utc


FAMILY_CONTEXT_TTL = timedelta(minutes=30)
CUSTOMER_ROLES = {"CUSTOMER", "DEV_TEST"}
FAMILY_CONTEXT_ACTIONS = ("FAMILY_VIEW", "FAMILY_PROPOSE")


def _fresh(session, model, row_id, *, lock=False):
    query = select(model).where(model.id == row_id).execution_options(populate_existing=True)
    return session.scalar(query.with_for_update() if lock else query)


def _invalid():
    raise DomainError("FAMILY_CONTEXT_INVALID", "家庭协助身份已失效，请回到自己的账户重新进入", 401)


def _check_link(session, link, *, lock=False):
    if link is None or link.status != "ACTIVE":
        _invalid()
    issuer = _fresh(session, LoginAccount, link.issuer_account_id, lock=lock)
    parent = _fresh(session, LoginAccount, link.parent_account_id, lock=lock)
    member = _fresh(session, FamilyMember, link.member_id, lock=lock)
    helper = _fresh(session, User, link.helper_user_id, lock=lock)
    if issuer is None or parent is None or issuer.status != "ACTIVE" or parent.status != "ACTIVE" \
            or issuer.role not in CUSTOMER_ROLES or parent.role not in CUSTOMER_ROLES \
            or issuer.id == parent.id or issuer.sandbox_id == parent.sandbox_id \
            or (issuer.user_id, issuer.sandbox_id) != (link.issuer_user_id, link.issuer_sandbox_id) \
            or (parent.user_id, parent.sandbox_id) != (link.owner_user_id, link.parent_sandbox_id) \
            or member is None or helper is None \
            or (member.sandbox_id, member.owner_user_id, member.helper_user_id) != \
                (parent.sandbox_id, parent.user_id, helper.id) \
            or helper.sandbox_id != parent.sandbox_id or helper.id == parent.user_id:
        _invalid()
    # Possession of a relationship is not permission to operate either person's
    # banking account.  In particular, a proxy must remain accountless.
    if session.scalar(select(LoginAccount.id).where(LoginAccount.user_id == helper.id)) is not None \
            or session.scalar(select(Account.id).where(Account.user_id == helper.id)) is not None \
            or session.scalar(select(Account.id).where(Account.sandbox_id == issuer.sandbox_id,
                Account.user_id == issuer.user_id)) is None \
            or session.scalar(select(Account.id).where(Account.sandbox_id == parent.sandbox_id,
                Account.user_id == parent.user_id)) is None:
        _invalid()
    return issuer, parent, member


def _main_account(session, principal, *, lock=False):
    query = select(LoginAccount).where(LoginAccount.user_id == principal.user_id,
        LoginAccount.sandbox_id == principal.sandbox_id).execution_options(populate_existing=True)
    account = session.scalar(query.with_for_update() if lock else query)
    if account is None or account.status != "ACTIVE" or account.role not in CUSTOMER_ROLES \
            or principal.role != account.role:
        raise DomainError("FAMILY_MAIN_ACCOUNT_REQUIRED", "请从自己的有效客户账户进入家庭协助", 403)
    return account


def bind_family_account_context(session, *, issuer_account_id, parent_account_id,
                                helper_user_id, member_id, relationship, actor_account_id=None):
    """Trusted bootstrap binding only; never call from a free-form user request.

    Existing bindings are returned unchanged only when every identity matches.
    They are never silently reassigned or reactivated.
    """
    if not isinstance(relationship, str) or not relationship.strip() or len(relationship.strip()) > 40:
        raise DomainError("FAMILY_RELATIONSHIP_REQUIRED", "请提供家庭关系称呼", 422)
    issuer = _fresh(session, LoginAccount, issuer_account_id, lock=True)
    parent = _fresh(session, LoginAccount, parent_account_id, lock=True)
    existing = session.scalar(select(FamilyAccountLink).where(
        FamilyAccountLink.issuer_account_id == issuer_account_id,
        FamilyAccountLink.parent_account_id == parent_account_id).with_for_update()
        .execution_options(populate_existing=True))
    if existing:
        if existing.helper_user_id != helper_user_id or existing.member_id != member_id \
                or existing.relationship != relationship.strip():
            raise DomainError("FAMILY_CONTEXT_BINDING_CHANGED", "已有家庭身份不能被重新指向其他成员", 409)
        _check_link(session, existing, lock=True)
        return existing
    if issuer is None or parent is None:
        _invalid()
    link = FamilyAccountLink(issuer_account_id=issuer.id, issuer_user_id=issuer.user_id,
        issuer_sandbox_id=issuer.sandbox_id, parent_account_id=parent.id,
        owner_user_id=parent.user_id, parent_sandbox_id=parent.sandbox_id,
        helper_user_id=helper_user_id, member_id=member_id, relationship=relationship.strip(),
        status="ACTIVE", version=1)
    _check_link(session, link, lock=True)
    session.add(link)
    session.flush()
    session.add(AccessAudit(actor_account_id=actor_account_id, target_account_id=issuer.id,
        action="FAMILY_ACCOUNT_CONTEXT_BOUND", payload_json={"link_id": link.id,
        "parent_account_id": parent.id, "member_id": member_id, "relationship": link.relationship,
        "bank_authorization_granted": False}))
    return link


def _projection(session, link, issuer, parent, member):
    delegation = session.scalar(select(FamilyDelegation).where(
        FamilyDelegation.sandbox_id == parent.sandbox_id,
        FamilyDelegation.owner_user_id == parent.user_id,
        FamilyDelegation.grantee_user_id == link.helper_user_id)
        .execution_options(populate_existing=True))
    valid = bool(delegation and delegation.status == "ACTIVE" and as_utc(delegation.expires_at) > utcnow())
    parent_user = _fresh(session, User, parent.user_id)
    return {"link_id": link.id, "link_version": link.version, "relationship": link.relationship,
        "owner_name": parent_user.display_name, "helper_name": member.display_name,
        "scopes": sorted(delegation.scopes_json) if valid else [],
        "delegation_valid": valid, "role": "FAMILY_HELPER",
        "allowed_actions": list(FAMILY_CONTEXT_ACTIONS), "direct_bank_actions_allowed": False}


def family_account_contexts(session, principal):
    account = _main_account(session, principal)
    contexts = []
    for link in session.scalars(select(FamilyAccountLink).where(
            FamilyAccountLink.issuer_account_id == account.id).order_by(FamilyAccountLink.created_at, FamilyAccountLink.id)
            .execution_options(populate_existing=True)):
        try:
            issuer, parent, member = _check_link(session, link)
        except DomainError:
            continue
        contexts.append(_projection(session, link, issuer, parent, member))
    return {"scope": "OWN_BOUND_FAMILY_CONTEXTS", "contexts": contexts,
        "current_role": account.role, "balances_disclosed": False,
        "main_account_session_preserved": True}


def issue_family_account_context(session, principal, *, link_id, expected_link_version):
    account = _main_account(session, principal, lock=True)
    link = session.scalar(select(FamilyAccountLink).where(FamilyAccountLink.id == link_id,
        FamilyAccountLink.issuer_account_id == account.id).with_for_update()
        .execution_options(populate_existing=True))
    if link is None:
        raise DomainError("FAMILY_CONTEXT_NOT_FOUND", "没有可进入的家庭协助身份", 404)
    if type(expected_link_version) is not int or link.version != expected_link_version:
        raise DomainError("FAMILY_CONTEXT_CHANGED", "家庭关系已变化，请刷新后重新进入", 409)
    issuer, parent, member = _check_link(session, link, lock=True)
    now = utcnow()
    expires = now + FAMILY_CONTEXT_TTL
    token = secrets.token_urlsafe(32)
    login = DemoSession(sandbox_id=link.parent_sandbox_id, user_id=link.helper_user_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires)
    session.add(login)
    session.flush()
    session.add(FamilyContextSession(session_id=login.id, link_id=link.id,
        issuer_account_id=issuer.id, issuer_account_version=issuer.version,
        parent_account_id=parent.id, parent_account_version=parent.version,
        link_version=link.version, member_id=member.id, helper_user_id=link.helper_user_id,
        parent_sandbox_id=link.parent_sandbox_id, issued_at=now, expires_at=expires))
    session.add(AccessAudit(actor_account_id=issuer.id, target_account_id=issuer.id,
        action="FAMILY_CONTEXT_SESSION_ISSUED", payload_json={"link_id": link.id,
        "session_id": login.id, "bank_actions_executable": False, "ttl_seconds": 1800}))
    session.flush()
    return {"token": token, "expires_at": expires.isoformat(), "role": "FAMILY_HELPER",
        "user_id": login.user_id, "sandbox_id": login.sandbox_id,
        "username": issuer.username, "account_id": None, "profile": None,
        "registered": False, "family_context": _projection(session, link, issuer, parent, member),
        "main_account_session_preserved": True}


def validate_family_context_actor(session, sandbox_id, user_id, *, expected_context=None):
    """Fresh binding checks for role/worker guards, independent of bearer sessions."""
    link = session.scalar(select(FamilyAccountLink).where(FamilyAccountLink.helper_user_id == user_id)
        .execution_options(populate_existing=True))
    if link is None:
        if expected_context is not None:
            _invalid()
        return None  # Existing unbound token-only helpers retain legacy behavior.
    if link.parent_sandbox_id != sandbox_id:
        _invalid()
    issuer, parent, member = _check_link(session, link)
    current = {"link_id": link.id, "link_version": link.version, "issuer_account_id": issuer.id,
        "issuer_account_version": issuer.version, "parent_account_id": parent.id,
        "parent_account_version": parent.version, "member_id": member.id,
        "helper_user_id": link.helper_user_id, "parent_sandbox_id": link.parent_sandbox_id}
    if expected_context is not None:
        if not isinstance(expected_context, dict) or not isinstance(expected_context.get("session_id"), str):
            _invalid()
        login = _fresh(session, DemoSession, expected_context["session_id"])
        if login is None:
            _invalid()
        verified = validate_family_context_session(session, login)
        if verified != expected_context or verified.get("helper_user_id") != user_id \
                or verified.get("parent_sandbox_id") != sandbox_id:
            _invalid()
        return verified
    return current


def validate_family_context_session(session, login):
    """Validate provenance before authenticating any bound helper bearer token.

    Missing provenance for a linked helper fails closed, not legacy fallback.
    A provenance row on an ordinary customer session is also invalid.
    """
    proof = session.scalar(select(FamilyContextSession).where(FamilyContextSession.session_id == login.id)
        .execution_options(populate_existing=True))
    link = session.scalar(select(FamilyAccountLink).where(FamilyAccountLink.helper_user_id == login.user_id)
        .execution_options(populate_existing=True))
    if proof is None and link is None:
        return None
    if proof is None or link is None:
        _invalid()
    issuer, parent, member = _check_link(session, link)
    now = utcnow()
    if (login.user_id, login.sandbox_id, proof.link_id, proof.helper_user_id, proof.parent_sandbox_id,
            proof.member_id, proof.issuer_account_id, proof.parent_account_id,
            proof.issuer_account_version, proof.parent_account_version, proof.link_version) != \
            (link.helper_user_id, link.parent_sandbox_id, link.id, link.helper_user_id, link.parent_sandbox_id,
            member.id, issuer.id, parent.id, issuer.version, parent.version, link.version) \
            or as_utc(login.expires_at) != as_utc(proof.expires_at) \
            or not as_utc(proof.issued_at) <= now < as_utc(proof.expires_at) \
            or as_utc(proof.expires_at) - as_utc(proof.issued_at) != FAMILY_CONTEXT_TTL:
        _invalid()
    return {"session_id": login.id, "role": "FAMILY_HELPER", "issuer_account_id": issuer.id,
        "issuer_account_version": issuer.version, "parent_account_id": parent.id,
        "parent_account_version": parent.version, "link_id": link.id, "link_version": link.version,
        "helper_user_id": link.helper_user_id, "parent_sandbox_id": link.parent_sandbox_id,
        "member_id": member.id}
