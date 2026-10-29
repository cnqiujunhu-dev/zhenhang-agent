"""Explicit, additive QA account creation with a reproducible business date.

No startup hooks, clock advancement, bank execution, commits, or updates to
existing accounts. Callers own authorization and transaction boundaries.
"""
from datetime import timedelta
import re

from sqlalchemy import select

from .access_models import LoginAccount
from .contracts import DomainError
from .models import User, utcnow
from .seed import seed_business_time


def create_acceptance_accounts(session, namespace, *, business_time, expired_subscription_role=None):
    """Create two customers and a four-person family, failing on any collision.

    Pass an aware datetime (including calendar_now() for current-day QA).
    Passwords use the user-requested username + 88 convention; return values
    contain no credentials. Each customer receives two cards/two subscriptions.
    """
    if not isinstance(namespace, str) or not re.fullmatch(r"qa_[a-z0-9_]{1,12}", namespace):
        raise DomainError("QA_NAMESPACE_INVALID", "验收账号前缀应为 qa_ 开头的短小写标识。", 422)
    clock = seed_business_time(business_time)
    if business_time is None:
        raise DomainError("QA_BUSINESS_TIME_REQUIRED", "请明确指定验收账套的业务日期。", 422)
    if expired_subscription_role not in {None, 'two'}:
        raise DomainError("QA_EXPIRED_ROLE_INVALID", "过期权益账套仅允许预先指定第二个普通验收客户。", 422)
    roles = (("one", "验收客户一", "young_professional"), ("two", "验收客户二", "investor"),
             ("father", "爸爸", "pensioner"), ("mother", "妈妈", "family"),
             ("son", "儿子", "young_professional"), ("daughter", "女儿", "young_professional"))
    usernames = [namespace + "_" + role for role, _, _ in roles]
    if session.scalar(select(LoginAccount.id).where(LoginAccount.username.in_(usernames)).limit(1)):
        raise DomainError("QA_NAMESPACE_EXISTS", "此验收账号组已经存在，请使用新的前缀。", 409)
    from .accounts import create_customer_account
    from .extensions_models import FamilyDelegation, FamilyMember
    from .family_account_context import bind_family_account_context
    account_clocks = {role: clock for role,_,_ in roles}
    subscription_cutoffs = {role: clock-timedelta(days=35) if role==expired_subscription_role else None for role,_,_ in roles}
    accounts = {role: create_customer_account(session, namespace + "_" + role,
        namespace + "_" + role + "88", profile, display_name=name, business_time=account_clocks[role],
        subscription_history_cutoff=subscription_cutoffs[role])
        for role, name, profile in roles}
    from .demo_carbon_funding import fund_demo_carbon_points
    for account in accounts.values():
        fund_demo_carbon_points(session, account.sandbox_id, account.user_id)
    now = utcnow()
    contexts = []
    for child, parent, scopes, expired in (
        ("son", "father", ["READ_BALANCE"], False),
        ("daughter", "father", ["READ_BALANCE", "PROPOSE_TRANSFER"], False),
        ("son", "mother", ["READ_BALANCE"], True),
        ("daughter", "mother", [], False),
    ):
        issuer, owner = accounts[child], accounts[parent]
        label = "儿子" if child == "son" else "女儿"
        helper = User(sandbox_id=owner.sandbox_id, display_name=label)
        session.add(helper); session.flush()
        member = FamilyMember(sandbox_id=owner.sandbox_id, owner_user_id=owner.user_id,
            helper_user_id=helper.id, display_name=label)
        session.add(member); session.flush()
        link = bind_family_account_context(session, issuer_account_id=issuer.id, parent_account_id=owner.id,
            helper_user_id=helper.id, member_id=member.id, relationship=label)
        grant = None
        if scopes:
            grant = FamilyDelegation(sandbox_id=owner.sandbox_id, owner_user_id=owner.user_id,
                grantee_user_id=helper.id, scopes_json=scopes, maximum_amount_minor=50000,
                limit_period="MONTH", status="ACTIVE", expires_at=now + timedelta(days=-1 if expired else 30), version=1)
            session.add(grant); session.flush()
        contexts.append({"child": child, "parent": parent, "link_id": link.id,
            "helper_user_id": helper.id, "delegation_id": grant.id if grant else None,
            "scopes": scopes, "expired": expired})
    return {"namespace": namespace, "business_time": clock.isoformat(),
        "expired_subscription_role": expired_subscription_role,
        "account_business_times": {role: value.isoformat() for role,value in account_clocks.items()},
        "subscription_history_cutoffs": {role: value.isoformat() if value else None for role,value in subscription_cutoffs.items()},
        "accounts": {role: {"username": account.username, "account_id": account.id,
            "user_id": account.user_id, "sandbox_id": account.sandbox_id} for role, account in accounts.items()},
        "family_contexts": contexts}
