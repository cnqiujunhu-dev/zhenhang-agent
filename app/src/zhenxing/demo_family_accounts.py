"""Versioned, non-resetting family customer data for the local bank environment."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, text

from .access_models import AccessAudit, LoginAccount
from .contracts import DomainError
from .extensions_models import FamilyDelegation, FamilyMember
from .family_account_context_models import FamilyAccountLink
from .models import Account, User, utcnow


DEMO_FAMILY_ACCOUNTS_REVISION = "demo-family-accounts-v1"
FAMILY_USERNAMES = ("family_father", "family_mother", "family_son", "family_daughter")
_ACCOUNT_ACTION = "DEMO_FAMILY_ACCOUNT_CREATED"
_RELATION_ACTION = "DEMO_FAMILY_RELATION_BOOTSTRAPPED"
_ACCOUNT_DEFAULTS = (
    ("family_father", "爸爸", "pensioner", 2026100901),
    ("family_mother", "妈妈", "family", 2026101002),
    ("family_son", "儿子", "young_professional", 2026101103),
    ("family_daughter", "女儿", "young_professional", 2026101204),
)
_RELATIONS = (
    ("family_son", "family_father", "儿子", "爸爸", ["READ_BALANCE", "PROPOSE_CARD_LOCK"], 0, "PER_PROPOSAL"),
    ("family_daughter", "family_mother", "女儿", "妈妈", ["READ_BALANCE", "PROPOSE_TRANSFER"], 50000, "MONTH"),
    ("family_daughter", "family_father", "女儿", "爸爸", [], 0, "PER_PROPOSAL"),
    ("family_son", "family_mother", "儿子", "妈妈", [], 0, "PER_PROPOSAL"),
)


def _inconsistent():
    return DomainError("DEMO_FAMILY_BOOTSTRAP_INCONSISTENT", "家庭初始资料不完整，请先核验身份关联", 409)


def _markers(session, target_id, action, key):
    return [row for row in session.scalars(select(AccessAudit).where(
        AccessAudit.target_account_id == target_id, AccessAudit.action == action))
        if isinstance(row.payload_json, dict) and row.payload_json.get("operation_key") == key]


def _validate_account(session, account, username, markers):
    if len(markers) != 1 or account.role != "CUSTOMER":
        raise _inconsistent()
    payload = markers[0].payload_json
    if payload.get("revision") != DEMO_FAMILY_ACCOUNTS_REVISION \
            or payload.get("username") != username \
            or payload.get("user_id") != account.user_id \
            or payload.get("sandbox_id") != account.sandbox_id:
        raise _inconsistent()
    if session.scalar(select(User.id).where(User.id == account.user_id,
        User.sandbox_id == account.sandbox_id)) is None or session.scalar(select(Account.id).where(
            Account.user_id == account.user_id, Account.sandbox_id == account.sandbox_id)) is None:
        raise _inconsistent()


def _validate_relation(session, link, issuer, parent, helper_name, relationship, marker):
    if link is None or (link.issuer_account_id, link.issuer_user_id, link.issuer_sandbox_id,
            link.parent_account_id, link.owner_user_id, link.parent_sandbox_id, link.relationship) != \
            (issuer.id, issuer.user_id, issuer.sandbox_id, parent.id, parent.user_id,
             parent.sandbox_id, relationship):
        raise _inconsistent()
    helper = session.get(User, link.helper_user_id)
    member = session.get(FamilyMember, link.member_id)
    if helper is None or member is None or helper.sandbox_id != parent.sandbox_id \
            or (member.sandbox_id, member.owner_user_id, member.helper_user_id, member.display_name) != \
            (parent.sandbox_id, parent.user_id, helper.id, helper_name) \
            or session.scalar(select(Account.id).where(Account.user_id == helper.id)) is not None \
            or session.scalar(select(LoginAccount.id).where(LoginAccount.user_id == helper.id)) is not None:
        raise _inconsistent()
    payload = marker.payload_json
    if payload.get("revision") != DEMO_FAMILY_ACCOUNTS_REVISION \
            or (payload.get("link_id"), payload.get("issuer_account_id"), payload.get("parent_account_id"),
                payload.get("helper_user_id"), payload.get("member_id")) != \
            (link.id, issuer.id, parent.id, helper.id, member.id):
        raise _inconsistent()
    # Existing delegation status, scope, limits and expiry are deliberately not
    # restored: users may revoke or edit them, including deleting the grant.


def bootstrap_demo_family_accounts(session):
    """Create four independent customers and four accountless helper links once.

    The two initial grants are trusted fixture defaults, not a bank action
    inferred from conversation. Every repeat preserves account/grant changes.
    The caller owns the transaction; a partial legacy marker is an error.
    """
    from .accounts import create_customer_account
    from .deployment_credentials import initial_customer_password
    from .family_account_context import bind_family_account_context

    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(9360412)"))
    accounts, created = {}, 0
    for username, display_name, profile, seed in _ACCOUNT_DEFAULTS:
        key = f"{DEMO_FAMILY_ACCOUNTS_REVISION}:account:{username}"
        account = session.scalar(select(LoginAccount).where(LoginAccount.username == username)
            .with_for_update().execution_options(populate_existing=True))
        if account is None:
            account = create_customer_account(session, username, initial_customer_password(username), profile,
                display_name=display_name, seed=seed, role="CUSTOMER")
            session.add(AccessAudit(target_account_id=account.id, action=_ACCOUNT_ACTION, payload_json={
                "revision": DEMO_FAMILY_ACCOUNTS_REVISION, "operation_key": key,
                "username": username, "user_id": account.user_id, "sandbox_id": account.sandbox_id,
                "authority": "EXPLICIT_LOCAL_USER_REQUEST", "credentials_reset_on_restart": False}))
            session.flush()
            created += 1
        _validate_account(session, account, username, _markers(session, account.id, _ACCOUNT_ACTION, key))
        accounts[username] = account
    if len({account.sandbox_id for account in accounts.values()}) != 4 \
            or len({account.user_id for account in accounts.values()}) != 4:
        raise _inconsistent()

    created_links, created_grants = 0, 0
    for child_name, parent_name, helper_name, relationship, scopes, limit, period in _RELATIONS:
        issuer, parent = accounts[child_name], accounts[parent_name]
        key = f"{DEMO_FAMILY_ACCOUNTS_REVISION}:relation:{issuer.id}:{parent.id}"
        link = session.scalar(select(FamilyAccountLink).where(
            FamilyAccountLink.issuer_account_id == issuer.id,
            FamilyAccountLink.parent_account_id == parent.id).with_for_update()
            .execution_options(populate_existing=True))
        markers = _markers(session, issuer.id, _RELATION_ACTION, key)
        if markers or link:
            if len(markers) != 1:
                raise _inconsistent()
            _validate_relation(session, link, issuer, parent, helper_name, relationship, markers[0])
            continue
        # Never attach a similarly named existing actor to a new login account.
        if session.scalar(select(FamilyMember.id).where(FamilyMember.owner_user_id == parent.user_id,
            FamilyMember.display_name == helper_name)) is not None \
                or issuer.status != "ACTIVE" or parent.status != "ACTIVE":
            raise _inconsistent()
        helper = User(sandbox_id=parent.sandbox_id, display_name=helper_name)
        session.add(helper)
        session.flush()
        member = FamilyMember(sandbox_id=parent.sandbox_id, owner_user_id=parent.user_id,
                              helper_user_id=helper.id, display_name=helper_name)
        session.add(member)
        session.flush()
        link = bind_family_account_context(session, issuer_account_id=issuer.id, parent_account_id=parent.id,
            helper_user_id=helper.id, member_id=member.id, relationship=relationship)
        grant = None
        if scopes:
            grant = FamilyDelegation(sandbox_id=parent.sandbox_id, owner_user_id=parent.user_id,
                grantee_user_id=helper.id, scopes_json=list(scopes), maximum_amount_minor=limit,
                limit_period=period, status="ACTIVE", expires_at=utcnow() + timedelta(days=365), version=1)
            session.add(grant)
            session.flush()
            created_grants += 1
        session.add(AccessAudit(target_account_id=issuer.id, action=_RELATION_ACTION, payload_json={
            "revision": DEMO_FAMILY_ACCOUNTS_REVISION, "operation_key": key,
            "authority": "EXPLICIT_LOCAL_USER_REQUEST", "link_id": link.id,
            "issuer_account_id": issuer.id, "parent_account_id": parent.id,
            "helper_user_id": helper.id, "member_id": member.id,
            "initial_delegation_id": grant.id if grant else None,
            "initial_scopes": list(scopes), "initial_maximum_amount_minor": limit,
            "initial_limit_period": period, "expiry_days": 365 if scopes else None,
            "regrant_on_restart": False, "direct_bank_write_allowed": False}))
        session.flush()
        created_links += 1
    return {"revision": DEMO_FAMILY_ACCOUNTS_REVISION, "seeded_usernames": list(FAMILY_USERNAMES),
            "created_accounts": created, "created_links": created_links, "created_initial_grants": created_grants}
