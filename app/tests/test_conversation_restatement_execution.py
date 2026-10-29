"""Disposable SQLite confirmation-to-settlement regressions; no external API."""
from copy import deepcopy

import httpx
import pytest
from sqlalchemy import func, select

from test_conversation_service import chat, turn
from test_conversation_queued_followup_probe import named_sender_and_recipient
from zhenxing.authentication import create_challenge, verify_challenge
from zhenxing.contracts import ConfirmTaskRequest, DomainError
from zhenxing.models import Account, Confirmation, Task, Transfer, LedgerEntry
from zhenxing.runtime import confirm_runtime, run_one_job, task_trace
from zhenxing.runtime_models import RuntimeRecord, WorkflowJob, RuntimeOperation, AuthChallenge


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("ZX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ZX_KNOWLEDGE_DATABASE_URL", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("SQLite settlement test attempted external network")
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)


def trace(chat, task_id):
    with chat[0]() as db:
        return task_trace(db, chat[2], task_id)


def authenticate(chat, current):
    pending = current["pending_confirmation"]
    with chat[0].begin() as db:
        child = db.get(Task, pending["task_id"])
        confirmation = db.get(Confirmation, pending["confirmation_id"])
        challenge = create_challenge(db, task=child, confirmation=confirmation, user_id=chat[2].user_id)
        verified = verify_challenge(db, challenge_id=challenge["challenge_id"], user_id=chat[2].user_id,
            sandbox_id=chat[2].sandbox_id, code=challenge["demo_code"])
        assert verified["verified"] is True
    return ConfirmTaskRequest(confirmation_id=pending["confirmation_id"], plan_hash=pending["plan_hash"],
        expected_task_version=pending["expected_task_version"], auth_challenge_id=challenge["challenge_id"])


def restated(chat, amount=300):
    named_sender_and_recipient(chat)
    first = turn(chat, f"给quyuwei转账{amount}元")
    assert run_one_job(chat[0], chat[1])
    original = trace(chat, first.task_id)
    assert original["state"] == "AWAITING_CONFIRMATION"
    second = turn(chat, f"转给quyuwei {amount}人民币")
    assert second.mode == "REVISE" and second.task_id == first.task_id
    assert run_one_job(chat[0], chat[1])
    current = trace(chat, first.task_id)
    assert current["state"] == "AWAITING_CONFIRMATION"
    return first.task_id, original, current


def balances(chat):
    with chat[0]() as db:
        return {row.id: row.balance_minor for row in db.scalars(select(Account))}


@pytest.mark.parametrize("correct_to_300", [False, True])
def test_owned_complete_restatement_reaches_current_confirmed_internal_settlement(chat, correct_to_300):
    task_id, original, current = restated(chat, 10000 if correct_to_300 else 300)
    if correct_to_300:
        old_auth = authenticate(chat, current)
        revised = turn(chat, "改成300元")
        assert revised.mode == "REVISE" and revised.task_id == task_id
        assert run_one_job(chat[0], chat[1])
        current = trace(chat, task_id)
        with chat[0].begin() as db:
            assert db.get(Confirmation, old_auth.confirmation_id).status == "CANCELLED"
            assert db.get(AuthChallenge, old_auth.auth_challenge_id).status == "REVOKED"
            with pytest.raises(DomainError):
                confirm_runtime(db, chat[2], task_id, old_auth)
    before = balances(chat)
    approval = authenticate(chat, current)
    with chat[0].begin() as db:
        confirm_runtime(db, chat[2], task_id, approval)
    assert trace(chat, task_id)["state"] == "EXECUTING"
    assert run_one_job(chat[0], chat[1])
    result = trace(chat, task_id)
    assert result["state"] == "SUCCEEDED", result
    with chat[0]() as db:
        transfer = db.scalar(select(Transfer))
        assert transfer.amount_minor == 30000
        ledger = list(db.scalars(select(LedgerEntry).where(LedgerEntry.transfer_id == transfer.id)))
        assert len(ledger) == 4 and sum(row.delta_minor for row in ledger) == 0
        assert sorted(balances(chat)[key] - value for key, value in before.items()) == [-30000, 30000]
        assert db.get(Confirmation, approval.confirmation_id).status == "CONSUMED"
        assert db.get(AuthChallenge, approval.auth_challenge_id).status == "CONSUMED"
        assert db.get(Confirmation, original["pending_confirmation"]["confirmation_id"]).status == "CANCELLED"
    after = balances(chat)
    assert not run_one_job(chat[0], chat[1])
    with chat[0].begin() as db:
        with pytest.raises(DomainError):
            confirm_runtime(db, chat[2], task_id, approval)
        assert db.scalar(select(func.count()).select_from(Transfer)) == 1
    assert balances(chat) == after


@pytest.mark.parametrize("mutation", ["missing_grant", "wrong_grant", "wrong_child", "wrong_plan", "wrong_version",
    "wrong_actor", "login_version", "old_nonce", "consumed_confirmation", "old_unknown_operation", "client_phase_flag", "missing_acceptance"])
def test_owned_execution_phase_never_bypasses_current_confirmation_or_auth_source(chat, mutation):
    task_id, original, current = restated(chat)
    before = balances(chat)
    approval = authenticate(chat, current)
    with chat[0].begin() as db:
        confirm_runtime(db, chat[2], task_id, approval)
        parent = db.get(Task, task_id)
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == task_id, WorkflowJob.kind == "EXECUTE"))
        operation = db.get(RuntimeOperation, job.payload_json["operation_id"])
        if mutation == "missing_grant":
            db.get(AuthChallenge, approval.auth_challenge_id).status = "REVOKED"
        elif mutation == "wrong_grant":
            job.payload_json = {**job.payload_json, "request": {**job.payload_json["request"], "auth_challenge_id": "wrong-grant"}}
        elif mutation == "wrong_child":
            operation.child_task_id = original["pending_confirmation"]["task_id"]
        elif mutation == "wrong_plan":
            parent.plan_hash = "a"*64
        elif mutation == "wrong_version":
            db.get(Task, operation.child_task_id).version += 1
        elif mutation == "wrong_actor":
            from zhenxing.access_models import LoginAccount
            parent.user_id = db.scalar(select(LoginAccount.user_id).where(LoginAccount.username == "quyuwei"))
        elif mutation == "login_version":
            from zhenxing.access_models import LoginAccount
            db.scalar(select(LoginAccount).where(LoginAccount.user_id == chat[2].user_id)).version += 1
        elif mutation == "old_nonce":
            old = original["pending_confirmation"]
            job.payload_json = {**job.payload_json, "request": {**job.payload_json["request"],
                "confirmation_id": old["confirmation_id"], "plan_hash": old["plan_hash"], "expected_task_version": old["expected_task_version"]}}
        elif mutation == "consumed_confirmation":
            from zhenxing.models import utcnow
            confirmation = db.get(Confirmation, approval.confirmation_id)
            confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
        elif mutation == "old_unknown_operation":
            old = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task_id,
                RuntimeOperation.id != operation.id))
            old.status = "UNKNOWN"
        elif mutation == "client_phase_flag":
            job.payload_json = {**job.payload_json, "dialogue_authorization_binding": True}
        else:
            from zhenxing.models import AuditEvent
            accepted = db.scalar(select(AuditEvent).where(AuditEvent.task_id == task_id,
                AuditEvent.event_type == "ACTION_AUTHORIZED"))
            accepted.event_type = "ISOLATED_REMOVED_ACCEPTANCE"
    assert run_one_job(chat[0], chat[1])
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0
        current_job = db.scalar(select(WorkflowJob).where(WorkflowJob.task_id == task_id, WorkflowJob.kind == "EXECUTE"))
        assert current_job.status == "FAILED"
    assert balances(chat) == before


def test_unplanned_restatement_raw_source_proof_survives_authorized_current_dispatch(chat):
    named_sender_and_recipient(chat)
    first = turn(chat, "给quyuwei转账300元")
    second = turn(chat, "转给quyuwei 300人民币")
    assert second.mode == "REVISE" and second.task_id == first.task_id
    assert run_one_job(chat[0], chat[1])
    current = trace(chat, first.task_id)
    assert current["state"] == "AWAITING_CONFIRMATION"
    approval = authenticate(chat, current)
    before = balances(chat)
    with chat[0].begin() as db:
        confirm_runtime(db, chat[2], first.task_id, approval)
    assert run_one_job(chat[0], chat[1])
    assert trace(chat, first.task_id)["state"] == "SUCCEEDED"
    assert sorted(balances(chat)[key] - value for key, value in before.items()) == [-30000, 30000]
    with chat[0]() as db:
        transfer = db.scalar(select(Transfer))
        ledger = list(db.scalars(select(LedgerEntry).where(LedgerEntry.transfer_id == transfer.id)))
        assert transfer.amount_minor == 30000 and len(ledger) == 4 and sum(row.delta_minor for row in ledger) == 0


def test_client_shaped_execution_context_never_unlocks_executing_history(chat):
    from zhenxing.owned_user_dialogue import load_verified_dialogue
    from zhenxing.conversation_provenance import validate_current_continuation
    task_id, original, current = restated(chat)
    approval = authenticate(chat, current)
    with chat[0].begin() as db:
        confirm_runtime(db, chat[2], task_id, approval)
        parent = db.get(Task, task_id)
        record = db.get(RuntimeRecord, parent.id)
        with pytest.raises(DomainError):
            load_verified_dialogue(db, parent, record.metadata_json,
                execution_context={"phase": "EXECUTE", "authorized": True})
        with pytest.raises(DomainError):
            validate_current_continuation(db, parent)
    reply = turn(chat, "改成400元")
    assert reply.mode == "NEED_CONTEXT"
    with chat[0]() as db:
        assert db.get(Task, task_id).state == "EXECUTING"
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0
