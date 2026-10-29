"""Bound helper queues are pinned to server provenance, with no live API."""
from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from test_family_account_context import bank, issue, principal
from zhenxing.contracts import ConfirmTaskRequest, DomainError
from zhenxing.conversation_models import ChatTurn
from zhenxing.conversation_service import create_conversation, process_chat_job, submit_turn, turn_view
from zhenxing.extensions import owner_review_family_proposal, validate_family_origin
from zhenxing.extensions_models import FamilyDelegation, FamilyProposal
from zhenxing.family_account_context import validate_family_context_session
from zhenxing.family_account_context_models import FamilyContextSession
from zhenxing.intelligence import IntentProposal, ProposedAction
from zhenxing.models import Account, Card, DemoSession, Task, ToolCall, User, utcnow
from zhenxing.runtime import (accept_task, clarify_runtime, confirm_runtime, run_one_job,
    task_trace, validate_runtime_family_context)
from zhenxing.runtime_models import RuntimeOperation, RuntimeRecord, WorkflowJob
from zhenxing.services import Principal


def helper(bank):
    db, _accounts, _links, _original = bank
    result, login = issue(bank)
    proof = validate_family_context_session(db, login)
    return Principal(login.user_id, login.sandbox_id, role="FAMILY_HELPER", family_context=proof), login


def invalidate(bank, login, change):
    db, accounts, links, _original = bank
    link, member, _proxy = links[("father", "son")]
    if change == "issuer_version": accounts["son"].version += 1
    elif change == "parent_version": accounts["father"].version += 1
    elif change == "issuer_disabled": accounts["son"].status = "DISABLED"
    elif change == "parent_disabled": accounts["father"].status = "DISABLED"
    elif change == "issuer_role": accounts["son"].role = "ADMIN"
    elif change == "link_version": link.version += 1
    elif change == "link_revoked": link.status = "DISABLED"
    elif change == "member_changed": member.helper_user_id = accounts["other"].user_id
    elif change == "session_revoked": db.delete(login)
    elif change == "expired":
        proof = db.get(FamilyContextSession, login.id)
        proof.issued_at -= timedelta(hours=1)
        proof.expires_at -= timedelta(hours=1)
        login.expires_at = proof.expires_at
    else: raise AssertionError(change)
    db.flush()


CHANGES = ["issuer_version", "parent_version", "issuer_disabled", "parent_disabled",
    "issuer_role", "link_version", "link_revoked", "member_changed", "session_revoked", "expired"]


def factory(bank):
    return sessionmaker(bind=bank[0].get_bind(), expire_on_commit=False)


class ReadGateway:
    def __init__(self, during_inference=None):
        self.calls = 0
        self.during_inference = during_inference

    def propose(self, message, context=None):
        self.calls += 1
        if self.during_inference:
            self.during_inference()
        return IntentProposal(actions=[ProposedAction(kind="FAMILY_VIEW")], model_label="ISOLATED_TEST")


def test_accept_and_chat_pin_exact_authenticated_session(bank):
    db = bank[0]
    actor, login = helper(bank)
    task = accept_task(db, actor, "查询家庭授权", idempotency_key="family-bound-accept")
    thread = create_conversation(db, actor)
    turn = submit_turn(db, actor, thread.id, message="查看父亲余额", client_request_id="family-bound-chat")
    assert db.get(RuntimeRecord, task.id).metadata_json["family_context_origin"] == actor.family_context
    assert turn.diagnostics_json["family_context_origin"] == actor.family_context
    assert turn.diagnostics_json["authority"] == "SERVER_CONVERSATION_POLICY"
    assert actor.family_context["session_id"] == login.id


@pytest.mark.parametrize("entry", ["task", "chat"])
def test_bound_helper_without_authenticated_proof_cannot_start_work(bank, entry):
    db = bank[0]
    actor, _login = helper(bank)
    forged = Principal(actor.user_id, actor.sandbox_id, role="FAMILY_HELPER")
    thread = create_conversation(db, actor)
    with pytest.raises(DomainError) as caught:
        if entry == "task": accept_task(db, forged, "查询家庭授权")
        else: submit_turn(db, forged, thread.id, message="查询家庭授权", client_request_id="no-proof")
    assert caught.value.code == "FAMILY_CONTEXT_INVALID"
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(ChatTurn)) == 0


@pytest.mark.parametrize("change", CHANGES)
def test_old_runtime_snapshot_rejected_without_altering_funds(bank, change):
    db, accounts, _links, _original = bank
    actor, login = helper(bank)
    task = accept_task(db, actor, "查询家庭授权")
    before = [(row.id, row.balance_minor) for row in db.scalars(select(Account))]
    invalidate(bank, login, change)
    with pytest.raises(DomainError) as caught:
        validate_runtime_family_context(db, task)
    assert caught.value.code == "FAMILY_CONTEXT_INVALID"
    assert [(row.id, row.balance_minor) for row in db.scalars(select(Account))] == before
    assert accounts["son"].user_id != actor.user_id


def test_reissued_valid_helper_session_cannot_repair_an_old_queue(bank):
    db = bank[0]
    actor, login = helper(bank)
    old = accept_task(db, actor, "查看父亲余额", idempotency_key="old-session-request")
    db.delete(login); db.flush()
    replacement, _new_login = helper(bank)
    with pytest.raises(DomainError) as caught:
        accept_task(db, replacement, "查看父亲余额", idempotency_key="old-session-request")
    assert caught.value.code == "FAMILY_CONTEXT_INVALID"
    fresh = accept_task(db, replacement, "查询当前委托", idempotency_key="fresh-session-request")
    assert validate_runtime_family_context(db, fresh) == replacement.family_context
    assert old.id != fresh.id


@pytest.mark.parametrize("queue", ["task", "chat"])
def test_worker_rejects_revoked_session_before_model_or_tools(bank, queue):
    db = bank[0]
    actor, login = helper(bank)
    if queue == "task":
        row = accept_task(db, actor, "查看父亲余额")
    else:
        thread = create_conversation(db, actor)
        row = submit_turn(db, actor, thread.id, message="查看父亲余额", client_request_id="revoked-chat")
    row_id = row.id
    db.commit()
    db.delete(login); db.commit()
    gateway = ReadGateway()
    assert (run_one_job if queue == "task" else process_chat_job)(factory(bank), gateway)
    db.expire_all()
    if queue == "task":
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == row_id))
        assert job.status == "FAILED" and job.last_error == "FAMILY_CONTEXT_INVALID"
        assert db.get(Task, row_id).state == "FAILED"
    else:
        turn = db.get(ChatTurn, row_id)
        assert turn.status == "FAILED" and turn.error_code == "FAMILY_CONTEXT_INVALID"
    assert gateway.calls == 0
    assert db.scalar(select(func.count()).select_from(ToolCall)) == 0


def test_planner_rechecks_original_session_after_inference(bank):
    db = bank[0]
    actor, login = helper(bank)
    row = accept_task(db, actor, "查询家庭授权")
    row_id = row.id
    db.commit()
    def revoke():
        with factory(bank).begin() as tx:
            tx.delete(tx.get(DemoSession, login.id))
    gateway = ReadGateway(revoke)
    assert run_one_job(factory(bank), gateway)
    db.expire_all()
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == row_id))
    assert job.status == "FAILED" and job.last_error == "FAMILY_CONTEXT_INVALID"
    assert gateway.calls == 1
    assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0


@pytest.mark.parametrize("entry", ["trace", "clarify", "confirm"])
def test_task_entrypoints_revalidate_old_snapshot(bank, entry):
    db = bank[0]
    actor, login = helper(bank)
    task = accept_task(db, actor, "查询家庭授权")
    db.delete(login); db.flush()
    replacement, _new_login = helper(bank)
    with pytest.raises(DomainError) as caught:
        if entry == "trace": task_trace(db, replacement, task.id)
        elif entry == "clarify": clarify_runtime(db, replacement, task.id, "查询余额", task.version)
        else: confirm_runtime(db, replacement, task.id, ConfirmTaskRequest(
            confirmation_id="unused", plan_hash="a" * 64, expected_task_version=1))
    assert caught.value.code == "FAMILY_CONTEXT_INVALID"


def pending_proposal(bank):
    db, accounts, links, _original = bank
    actor, login = helper(bank)
    source = accept_task(db, actor, "协助提案，锁定父亲尾号1234卡片")
    parent = accounts["father"]
    account = db.scalar(select(Account).where(Account.user_id == parent.user_id))
    card = Card(sandbox_id=parent.sandbox_id, user_id=parent.user_id, account_id=account.id, last4="1234")
    db.add(card); db.flush()
    delegation = db.scalar(select(FamilyDelegation).where(FamilyDelegation.grantee_user_id == actor.user_id))
    args = {"source_helper_task_id": source.id,
        "source_helper_context_hash": hashlib.sha256(source.message.encode()).hexdigest(),
        "delegation_id": delegation.id, "delegation_version": delegation.version,
        "owner_user_id": parent.user_id, "grantee_user_id": actor.user_id,
        "proposal_kind": "CARD_LOCK", "card_id": card.id, "card_last4": card.last4,
        "card_version": card.version, "requested_card_reference": card.last4}
    proposal = FamilyProposal(sandbox_id=parent.sandbox_id, owner_user_id=parent.user_id,
        grantee_user_id=actor.user_id, delegation_id=delegation.id, proposal_kind="CARD_LOCK",
        args_json=args, operation_key="isolated-bound-proposal")
    db.add(proposal); db.flush()
    return principal(parent), actor, login, source, proposal, card


def test_owner_review_rejects_revoked_helper_source(bank):
    db = bank[0]
    owner, _actor, login, _source, proposal, card = pending_proposal(bank)
    db.delete(login); db.flush()
    with pytest.raises(DomainError) as caught:
        owner_review_family_proposal(db, owner, proposal.id, "APPROVE", proposal.version)
    assert caught.value.code == "FAMILY_CONTEXT_INVALID"
    assert proposal.status == "PENDING_OWNER_APPROVAL" and card.status == "ACTIVE"


def test_owner_review_then_source_revocation_blocks_owner_plan(bank):
    db = bank[0]
    owner, _actor, login, _source, proposal, card = pending_proposal(bank)
    result = owner_review_family_proposal(db, owner, proposal.id, "APPROVE", proposal.version)
    task = db.get(Task, result["owner_task_id"])
    assert validate_family_origin(db, task)["valid"]
    db.delete(login); db.flush()
    with pytest.raises(DomainError) as caught:
        validate_family_origin(db, task)
    assert caught.value.code == "FAMILY_CONTEXT_INVALID"
    assert card.status == "ACTIVE"


def test_unbound_legacy_helper_keeps_task_compatibility(bank):
    db, accounts, _links, _original = bank
    from zhenxing.extensions_models import FamilyMember
    parent = accounts["father"]
    user = User(sandbox_id=parent.sandbox_id, display_name="legacy")
    db.add(user); db.flush()
    db.add(FamilyMember(sandbox_id=parent.sandbox_id, owner_user_id=parent.user_id,
        helper_user_id=user.id, display_name="legacy")); db.flush()
    actor = Principal(user.id, parent.sandbox_id, role="FAMILY_HELPER")
    task = accept_task(db, actor, "查询家庭授权")
    assert "family_context_origin" not in db.get(RuntimeRecord, task.id).metadata_json
    assert validate_runtime_family_context(db, task) is None


def test_independent_child_customer_never_requires_parent_context(bank):
    db, accounts, _links, _original = bank
    actor = principal(accounts["son"])
    task = accept_task(db, actor, "查询我的积分")
    assert validate_runtime_family_context(db, task) is None
    assert task.sandbox_id == accounts["son"].sandbox_id


def test_valid_bound_helper_read_runs_and_returns_only_delegated_balance(bank):
    db, accounts, _links, _original = bank
    actor, _login = helper(bank)
    row = accept_task(db, actor, "查询家庭授权")
    task_id = row.id
    db.commit()
    gateway = ReadGateway()
    assert run_one_job(factory(bank), gateway)
    db.expire_all()
    assert db.get(Task, task_id).state == "SUCCEEDED"
    trace = task_trace(db, actor, task_id)
    result = trace["nodes"][0]["result"]
    assert result["authorized_balances"] == [{"owner_name": "father", "balance_minor": 100_000}]
    assert trace["nodes"][0]["action"] == "FAMILY_VIEW"
    assert gateway.calls == 1
    assert db.scalar(select(Account).where(Account.user_id == accounts["son"].user_id)).balance_minor == 100_000


def test_execute_queue_rejects_revoked_source_before_dispatch(bank):
    db = bank[0]
    actor, login = helper(bank)
    row = accept_task(db, actor, "查询家庭授权")
    task_id = row.id
    plan = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == task_id))
    plan.status = "CANCELLED"
    row.state = "EXECUTING"
    db.add(WorkflowJob(task_id=row.id, sandbox_id=row.sandbox_id, kind="EXECUTE",
        dedupe_key="bound-revoked-execution", payload_json={"operation_id": "never-dispatched"}))
    db.commit()
    db.delete(login); db.commit()
    gateway = ReadGateway()
    assert run_one_job(factory(bank), gateway)
    db.expire_all()
    execution = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == task_id, WorkflowJob.kind == "EXECUTE"))
    assert execution.status == "FAILED" and execution.last_error == "FAMILY_CONTEXT_INVALID"
    assert gateway.calls == 0
    assert db.scalar(select(func.count()).select_from(ToolCall)) == 0


def test_new_session_cannot_revive_private_old_chat_answer(bank):
    db = bank[0]
    actor, login = helper(bank)
    thread = create_conversation(db, actor)
    row = submit_turn(db, actor, thread.id, message="父亲余额多少", client_request_id="private-history")
    row.status, row.mode, row.answer = "ANSWERED", "CONSULT", "父亲余额为1000元。"
    db.flush()
    view = turn_view(db, actor, row)
    assert view["answer"] == row.answer
    assert "family_context_origin" not in view["diagnostics"]
    assert "family_context_origin" in row.diagnostics_json
    db.delete(login); db.flush()
    replacement, _new_login = helper(bank)
    view = turn_view(db, replacement, row)
    assert "1000" not in view["answer"]
    assert view["sources"] == []
