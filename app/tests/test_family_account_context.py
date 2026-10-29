"""Bound family proxies keep child banking and carbon wallets independent."""
from copy import deepcopy
from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zhenxing.access_models import AccessAudit, LoginAccount
from zhenxing.contracts import DomainError
from zhenxing.database import Base
from zhenxing.extensions import family_view
from zhenxing.extensions_models import CarbonWallet, FamilyDelegation, FamilyMember
from zhenxing.family_account_context import (
    FAMILY_CONTEXT_TTL, bind_family_account_context, family_account_contexts,
    issue_family_account_context, validate_family_context_actor, validate_family_context_session,
)
from zhenxing.family_account_context_models import FamilyAccountLink, FamilyContextSession
from zhenxing.models import Account, DemoSession, Sandbox, User, utcnow
from zhenxing.services import Principal
from zhenxing.simulation import as_utc


@pytest.fixture
def bank(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'bound-family.db').as_posix()}")
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        accounts = {}
        for name in ("father", "mother", "son", "daughter", "other", "admin"):
            sandbox = Sandbox(); db.add(sandbox); db.flush()
            user = User(sandbox_id=sandbox.id, display_name=name); db.add(user); db.flush()
            login = LoginAccount(username=name, password_hash="fixture-not-a-login-secret",
                user_id=user.id, sandbox_id=sandbox.id, role="ADMIN" if name == "admin" else "CUSTOMER",
                status="ACTIVE", version=1)
            db.add(login); db.flush(); accounts[name] = login
            if name != "admin":
                db.add(Account(sandbox_id=sandbox.id, user_id=user.id, name=name, balance_minor=100_000))
                db.add(CarbonWallet(sandbox_id=sandbox.id, user_id=user.id, points=100))
        db.flush()
        parents = {}
        for parent_name in ("father", "mother"):
            parent = accounts[parent_name]
            for child_name in ("son", "daughter"):
                helper = User(sandbox_id=parent.sandbox_id, display_name=child_name)
                db.add(helper); db.flush()
                member = FamilyMember(sandbox_id=parent.sandbox_id, owner_user_id=parent.user_id,
                    helper_user_id=helper.id, display_name=child_name)
                db.add(member); db.flush()
                link = bind_family_account_context(db, issuer_account_id=accounts[child_name].id,
                    parent_account_id=parent.id, helper_user_id=helper.id, member_id=member.id,
                    relationship=parent_name)
                parents[(parent_name, child_name)] = (link, member, helper)
        father_link = parents[("father", "son")][0]
        db.add(FamilyDelegation(sandbox_id=father_link.parent_sandbox_id,
            owner_user_id=father_link.owner_user_id, grantee_user_id=father_link.helper_user_id,
            scopes_json=["READ_BALANCE", "PROPOSE_CARD_LOCK"], maximum_amount_minor=0,
            expires_at=utcnow() + timedelta(days=30)))
        mother_link = parents[("mother", "daughter")][0]
        db.add(FamilyDelegation(sandbox_id=mother_link.parent_sandbox_id,
            owner_user_id=mother_link.owner_user_id, grantee_user_id=mother_link.helper_user_id,
            scopes_json=["READ_BALANCE", "PROPOSE_TRANSFER"], maximum_amount_minor=50_000,
            limit_period="MONTH", expires_at=utcnow() + timedelta(days=30)))
        original = DemoSession(user_id=accounts["son"].user_id, sandbox_id=accounts["son"].sandbox_id,
            token_hash="original-session-hash", expires_at=utcnow() + timedelta(hours=4))
        db.add(original); db.commit()
        yield db, accounts, parents, original
    engine.dispose()


def principal(account):
    return Principal(account.user_id, account.sandbox_id, role=account.role)


def issue(bank, parent="father", child="son"):
    db, accounts, parents, _ = bank
    link = parents[(parent, child)][0]
    result = issue_family_account_context(db, principal(accounts[child]), link_id=link.id,
        expected_link_version=link.version)
    login = db.scalar(select(DemoSession).where(
        DemoSession.token_hash == hashlib.sha256(result["token"].encode()).hexdigest()))
    return result, login


def test_four_independent_customers_two_scoped_and_two_ungranted_contexts(bank):
    db, accounts, parents, original = bank
    before = [(r.user_id, r.balance_minor) for r in db.scalars(select(Account))]
    contexts = family_account_contexts(db, principal(accounts["son"]))
    assert contexts["scope"] == "OWN_BOUND_FAMILY_CONTEXTS"
    assert contexts["balances_disclosed"] is False
    assert len(contexts["contexts"]) == 2
    by_parent = {r["relationship"]: r for r in contexts["contexts"]}
    assert by_parent["father"]["scopes"] == ["PROPOSE_CARD_LOCK", "READ_BALANCE"]
    assert by_parent["mother"]["scopes"] == [] and not by_parent["mother"]["delegation_valid"]
    assert all("balance_minor" not in row and row["direct_bank_actions_allowed"] is False for row in by_parent.values())
    result, login = issue(bank)
    assert result["role"] == "FAMILY_HELPER" and result["registered"] is False
    assert result["account_id"] is None and result["main_account_session_preserved"] is True
    assert login.user_id != accounts["son"].user_id and login.sandbox_id == accounts["father"].sandbox_id
    assert db.get(DemoSession, original.id) is original
    assert db.scalar(select(CarbonWallet).where(CarbonWallet.user_id == accounts["son"].user_id)).points == 100
    assert db.scalar(select(LoginAccount).where(LoginAccount.user_id == login.user_id)) is None
    assert db.scalar(select(Account).where(Account.user_id == login.user_id)) is None
    assert [(r.user_id, r.balance_minor) for r in db.scalars(select(Account))] == before
    view = family_view(db, login.sandbox_id, login.user_id)
    assert view["permissions"]["can_read_owner_balance"]
    assert view["permissions"]["can_propose_card_lock"]
    assert not view["permissions"]["can_propose_transfer"]
    assert view["authorized_balances"] == [{"owner_name": "father", "balance_minor": 100_000}]


def test_ungranted_context_may_enter_but_has_no_balance_or_write_authority(bank):
    db, _, _, _ = bank
    result, login = issue(bank, "mother", "son")
    assert result["family_context"]["scopes"] == []
    view = family_view(db, login.sandbox_id, login.user_id)
    assert "authorized_balances" not in view
    assert not any(view["permissions"].values())
    proof = validate_family_context_session(db, login)
    assert proof["helper_user_id"] == login.user_id


@pytest.mark.parametrize("actor", ["other", "father", "admin"])
def test_context_cannot_be_selected_by_foreign_account_or_admin(bank, actor):
    db, accounts, parents, _ = bank
    with pytest.raises(DomainError) as caught:
        issue_family_account_context(db, principal(accounts[actor]), link_id=parents[("father", "son")][0].id,
            expected_link_version=1)
    assert caught.value.status_code in {403, 404}
    assert db.scalar(select(func.count()).select_from(FamilyContextSession)) == 0


@pytest.mark.parametrize("version", [0, 2, True, "1", None])
def test_issue_rejects_stale_or_invalid_link_version(bank, version):
    db, accounts, parents, _ = bank
    with pytest.raises(DomainError) as caught:
        issue_family_account_context(db, principal(accounts["son"]), link_id=parents[("father", "son")][0].id,
            expected_link_version=version)
    assert caught.value.code == "FAMILY_CONTEXT_CHANGED"


@pytest.mark.parametrize("change", ["issuer_disabled", "issuer_version", "issuer_role", "parent_disabled",
    "parent_version", "parent_role", "link_disabled", "link_version", "link_parent", "link_issuer", "link_sid",
    "member_owner", "member_helper", "member_sid", "helper_sid", "helper_account", "helper_login",
    "session_user", "session_sid", "session_expiry", "proof_missing", "proof_link", "proof_issuer",
    "proof_parent", "proof_member", "proof_helper", "proof_sid", "proof_issuer_version", "proof_parent_version",
    "proof_link_version", "proof_expiry", "proof_future_issued", "proof_expired", "proof_extended"])
def test_fresh_context_authentication_rejects_origin_and_proof_tampering(bank, change):
    db, accounts, parents, _ = bank
    _, login = issue(bank)
    link, member, helper = parents[("father", "son")]
    proof = db.get(FamilyContextSession, login.id)
    if change.endswith("_disabled"):
        {"issuer_disabled": accounts["son"], "parent_disabled": accounts["father"], "link_disabled": link}[change].status = "DISABLED"
    elif change in {"issuer_version", "parent_version", "link_version"}:
        {"issuer_version": accounts["son"], "parent_version": accounts["father"], "link_version": link}[change].version += 1
    elif change in {"issuer_role", "parent_role"}:
        accounts["son" if change == "issuer_role" else "father"].role = "ADMIN"
    elif change == "link_parent": link.parent_account_id = accounts["other"].id
    elif change == "link_issuer": link.issuer_account_id = accounts["other"].id
    elif change == "link_sid": link.parent_sandbox_id = accounts["mother"].sandbox_id
    elif change == "member_owner": member.owner_user_id = accounts["other"].user_id
    elif change == "member_helper": member.helper_user_id = parents[("mother", "son")][2].id
    elif change == "member_sid": member.sandbox_id = accounts["mother"].sandbox_id
    elif change == "helper_sid": helper.sandbox_id = accounts["son"].sandbox_id
    elif change == "helper_account": db.add(Account(user_id=helper.id, sandbox_id=helper.sandbox_id, name="unexpected", balance_minor=1))
    elif change == "helper_login": db.add(LoginAccount(username="unexpected", password_hash="fixture", user_id=helper.id, sandbox_id=helper.sandbox_id, role="CUSTOMER"))
    elif change == "session_user": login.user_id = accounts["son"].user_id
    elif change == "session_sid": login.sandbox_id = accounts["son"].sandbox_id
    elif change == "session_expiry": login.expires_at += timedelta(minutes=1)
    elif change == "proof_missing": db.delete(proof)
    elif change == "proof_link": proof.link_id = parents[("mother", "son")][0].id
    elif change == "proof_issuer": proof.issuer_account_id = accounts["other"].id
    elif change == "proof_parent": proof.parent_account_id = accounts["mother"].id
    elif change == "proof_member": proof.member_id = parents[("mother", "son")][1].id
    elif change == "proof_helper": proof.helper_user_id = parents[("mother", "son")][2].id
    elif change == "proof_sid": proof.parent_sandbox_id = accounts["mother"].sandbox_id
    elif change == "proof_issuer_version": proof.issuer_account_version += 1
    elif change == "proof_parent_version": proof.parent_account_version += 1
    elif change == "proof_link_version": proof.link_version += 1
    elif change == "proof_expiry": proof.expires_at += timedelta(seconds=1)
    elif change == "proof_future_issued": proof.issued_at = utcnow() + timedelta(minutes=1)
    elif change == "proof_expired":
        proof.expires_at = login.expires_at = utcnow() - timedelta(seconds=1)
        proof.issued_at = proof.expires_at - FAMILY_CONTEXT_TTL
    elif change == "proof_extended":
        proof.expires_at = login.expires_at = utcnow() + timedelta(hours=4)
    db.flush()
    with pytest.raises(DomainError) as caught:
        validate_family_context_session(db, login)
    assert caught.value.code == "FAMILY_CONTEXT_INVALID" and caught.value.status_code == 401


def test_current_actor_and_task_bound_origin_snapshot_are_separate_guards(bank):
    db, accounts, parents, _ = bank
    _, login = issue(bank)
    proof = validate_family_context_session(db, login)
    assert validate_family_context_actor(db, login.sandbox_id, login.user_id, expected_context=proof) == proof
    accounts["son"].version += 1; db.flush()
    current = validate_family_context_actor(db, login.sandbox_id, login.user_id)
    assert current["issuer_account_version"] != proof["issuer_account_version"]
    with pytest.raises(DomainError):
        validate_family_context_actor(db, login.sandbox_id, login.user_id, expected_context=proof)
    # Re-entering cannot turn an old queued task's proof into the new session's.
    _, new_login = issue(bank)
    new_proof = validate_family_context_session(db, new_login)
    assert validate_family_context_actor(db, new_login.sandbox_id, new_login.user_id, expected_context=new_proof) == new_proof
    with pytest.raises(DomainError):
        validate_family_context_actor(db, login.sandbox_id, login.user_id, expected_context=proof)


@pytest.mark.parametrize("mutation", ["extra", "missing", "other_session", "foreign_helper"])
def test_task_origin_snapshot_is_exact_and_owned(bank, mutation):
    db, _, _, _ = bank
    _, login = issue(bank)
    proof = deepcopy(validate_family_context_session(db, login))
    if mutation == "extra": proof["permissions"] = ["TRANSFER"]
    elif mutation == "missing": proof.pop("link_version")
    elif mutation == "other_session": proof["session_id"] = "missing-session"
    else:
        _, other = issue(bank, "mother", "daughter")
        proof = validate_family_context_session(db, other)
    with pytest.raises(DomainError):
        validate_family_context_actor(db, login.sandbox_id, login.user_id, expected_context=proof)


def test_customer_and_original_unbound_helper_sessions_keep_existing_semantics(bank):
    db, accounts, _, original = bank
    assert validate_family_context_session(db, original) is None
    assert validate_family_context_actor(db, original.sandbox_id, original.user_id) is None
    helper = User(sandbox_id=accounts["other"].sandbox_id, display_name="legacy"); db.add(helper); db.flush()
    db.add(FamilyMember(sandbox_id=helper.sandbox_id, owner_user_id=accounts["other"].user_id,
        helper_user_id=helper.id, display_name="legacy")); db.flush()
    legacy = DemoSession(user_id=helper.id, sandbox_id=helper.sandbox_id, token_hash="legacy", expires_at=utcnow()+timedelta(hours=4))
    db.add(legacy); db.flush()
    assert validate_family_context_session(db, legacy) is None
    assert validate_family_context_actor(db, helper.sandbox_id, helper.id) is None


def test_proof_on_main_customer_session_does_not_reclassify_customer(bank):
    db, _, _, original = bank
    _, login = issue(bank)
    proof = db.get(FamilyContextSession, login.id)
    proof.session_id = original.id; db.flush()
    with pytest.raises(DomainError): validate_family_context_session(db, original)
    with pytest.raises(DomainError): validate_family_context_session(db, login)


def test_binding_idempotence_never_repoints_or_restores_disabled_relation(bank):
    db, accounts, parents, _ = bank
    link, member, helper = parents[("father", "son")]
    count = db.scalar(select(func.count()).select_from(AccessAudit))
    assert bind_family_account_context(db, issuer_account_id=accounts["son"].id,
        parent_account_id=accounts["father"].id, helper_user_id=helper.id, member_id=member.id,
        relationship="father").id == link.id
    assert db.scalar(select(func.count()).select_from(AccessAudit)) == count
    with pytest.raises(DomainError) as caught:
        bind_family_account_context(db, issuer_account_id=accounts["son"].id,
            parent_account_id=accounts["father"].id, helper_user_id=parents[("father", "daughter")][2].id,
            member_id=parents[("father", "daughter")][1].id, relationship="father")
    assert caught.value.code == "FAMILY_CONTEXT_BINDING_CHANGED"
    link.status = "DISABLED"; db.flush()
    with pytest.raises(DomainError):
        bind_family_account_context(db, issuer_account_id=accounts["son"].id,
            parent_account_id=accounts["father"].id, helper_user_id=helper.id, member_id=member.id,
            relationship="father")
    assert link.status == "DISABLED"


def test_helper_logout_cascades_only_context_proof_not_main_session(bank):
    db, _, _, original = bank
    _, login = issue(bank)
    db.delete(login); db.flush(); db.expire_all()
    assert db.get(FamilyContextSession, login.id) is None
    assert db.get(DemoSession, original.id) is not None


def test_ttl_and_audit_contain_no_bearer_token(bank):
    db, _, _, _ = bank
    result, login = issue(bank)
    proof = db.get(FamilyContextSession, login.id)
    assert as_utc(proof.expires_at)-as_utc(proof.issued_at) == FAMILY_CONTEXT_TTL
    assert login.token_hash != result["token"]
    records = list(db.scalars(select(AccessAudit).where(AccessAudit.action == "FAMILY_CONTEXT_SESSION_ISSUED")))
    assert len(records) == 1 and result["token"] not in repr(records[0].payload_json)


def test_link_database_constraints_prevent_reusing_proxy_or_member(bank):
    db, accounts, parents, _ = bank
    source = parents[("father", "son")][0]
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(FamilyAccountLink(issuer_account_id=accounts["other"].id,
                issuer_user_id=accounts["other"].user_id, issuer_sandbox_id=accounts["other"].sandbox_id,
                parent_account_id=source.parent_account_id, owner_user_id=source.owner_user_id,
                parent_sandbox_id=source.parent_sandbox_id, helper_user_id=source.helper_user_id,
                member_id=source.member_id, relationship="father"))
            db.flush()
