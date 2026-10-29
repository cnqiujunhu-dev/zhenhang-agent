"""Diagnostic only: send a second chat before any planning job is run."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import httpx
import pytest
from sqlalchemy import func, select

from test_conversation_service import chat, turn
from zhenxing.models import Task, Transfer, Confirmation
from zhenxing.runtime_models import RuntimeRecord, WorkflowJob, RuntimeOperation, PlanSnapshot
from zhenxing.runtime import run_one_job, task_trace
from zhenxing.model_gateway import ModelGateway, RuleIntentModel
from zhenxing.owned_user_dialogue import load_verified_dialogue
from zhenxing.contracts import DomainError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("ZX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ZX_KNOWLEDGE_DATABASE_URL", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Isolated queued-followup probe attempted network")
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)


@pytest.mark.parametrize("first_text,second_text", [
    ("帮我转帐1w给quyuwei", "转给quyuwei 1w人民币"),
    ("帮我转账", "给quyuwei"),
])
def test_observe_followup_before_planner(chat, record_property, first_text, second_text):
    first = turn(chat, first_text)
    assert first.status == "ANSWERED" and first.mode == "NEW_TASK"
    with chat[0]() as db:
        before = db.get(Task, first.task_id)
        assert before.state == "QUEUED"
        assert db.get(RuntimeRecord, first.task_id).plan_version == 0
    second = turn(chat, second_text)
    with chat[0]() as db:
        original = db.get(Task, first.task_id)
        records = list(db.scalars(select(RuntimeRecord).where(RuntimeRecord.kind == "PARENT")))
        payload = {
            "authority": "ISOLATED_CPU_DIAGNOSTIC_NO_PLANNER_OR_NETWORK",
            "first_input": first_text, "first_task_id": first.task_id,
            "second_input": second_text, "second_mode": second.mode,
            "second_task_id": second.task_id, "second_answer": second.answer,
            "original_task_state": original.state, "original_message": original.message,
            "parent_task_count": len(records),
            "input_revisions": [record.metadata_json.get("input_revision", 0) for record in records],
            "queued_plan_job_count": db.scalar(select(func.count()).select_from(WorkflowJob).where(
                WorkflowJob.kind == "PLAN", WorkflowJob.status == "QUEUED")),
            "reused_task": second.mode == "REVISE" and second.task_id == first.task_id,
            "transfer_count": db.scalar(select(func.count()).select_from(Transfer)),
            "confirmation_count": db.scalar(select(func.count()).select_from(Confirmation)),
        }
        record_property("queued_followup_probe", json.dumps(payload, ensure_ascii=False))
        print(json.dumps(payload, ensure_ascii=True))
        assert payload["transfer_count"] == payload["confirmation_count"] == 0
        assert payload["reused_task"] is True, payload
        assert payload["parent_task_count"] == payload["queued_plan_job_count"] == 1


def named_sender_and_recipient(chat):
    from zhenxing.accounts import create_customer_account
    from zhenxing.access_models import LoginAccount
    with chat[0].begin() as db:
        create_customer_account(db, "quyuwei", "quyuwei88", seed=302)
        db.add(LoginAccount(username="queued_cpu_sender", password_hash="not-used", user_id=chat[2].user_id,
            sandbox_id=chat[2].sandbox_id, role="CUSTOMER", status="ACTIVE", profile="young_professional"))


def test_during_inference_same_subject_supersedes_old_job_and_cannot_restore_old_confirmation(chat):
    named_sender_and_recipient(chat)
    first = turn(chat, "帮我转帐1w给quyuwei")
    entered, release = Event(), Event()
    class BlockingRule(RuleIntentModel):
        def propose(self, message, context=None):
            entered.set()
            assert release.wait(10), "CPU test did not release blocked inference"
            return super().propose(message, context)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old_work = executor.submit(run_one_job, chat[0], ModelGateway(BlockingRule()))
        try:
            assert entered.wait(10)
            with chat[0]() as db:
                assert db.get(Task, first.task_id).state == "UNDERSTANDING"
                assert db.get(RuntimeRecord, first.task_id).plan_version == 0
                old_job_id = db.scalar(select(WorkflowJob.id).where(WorkflowJob.task_id == first.task_id,
                    WorkflowJob.kind == "PLAN", WorkflowJob.status == "LEASED"))
            revised = turn(chat, "转给quyuwei 1w人民币")
            assert revised.mode == "REVISE" and revised.task_id == first.task_id, revised.answer
            with chat[0]() as db:
                assert db.get(WorkflowJob, old_job_id).status == "SUPERSEDED"
                assert db.get(RuntimeRecord, first.task_id).metadata_json["input_revision"] == 1
                assert db.scalar(select(func.count()).select_from(RuntimeRecord).where(RuntimeRecord.kind == "PARENT")) == 1
        finally:
            release.set()
        assert old_work.result(timeout=10)
    with chat[0]() as db:
        assert db.get(WorkflowJob, old_job_id).status == "SUPERSEDED"
        assert db.scalar(select(func.count()).select_from(PlanSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(Confirmation)) == 0
        assert db.scalar(select(func.count()).select_from(RuntimeOperation)) == 0
    assert run_one_job(chat[0], chat[1])
    with chat[0]() as db:
        result = task_trace(db, chat[2], first.task_id)
        assert result["state"] == "AWAITING_CONFIRMATION", result
        assert result["pending_confirmation"]["scope"]["payee_name"] == "quyuwei"
        assert result["pending_confirmation"]["scope"]["amount_minor"] == 1000000
        assert db.scalar(select(func.count()).select_from(PlanSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(Confirmation)) == 1
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0


@pytest.mark.parametrize("state", ["QUEUED", "UNDERSTANDING", "PLANNING"])
def test_preplan_restatement_keeps_one_owned_task(state, chat):
    first = turn(chat, "帮我转帐1w给quyuwei")
    with chat[0].begin() as db:
        db.get(Task, first.task_id).state = state
    second = turn(chat, "转给quyuwei 1w人民币")
    assert second.mode == "REVISE" and second.task_id == first.task_id, second.answer
    with chat[0]() as db:
        record = db.get(RuntimeRecord, first.task_id)
        proof = load_verified_dialogue(db, db.get(Task, first.task_id), record.metadata_json)
        assert proof.public()["original_user_messages"] == ["帮我转帐1w给quyuwei"]
        assert db.scalar(select(func.count()).select_from(WorkflowJob).where(
            WorkflowJob.kind == "PLAN", WorkflowJob.status == "QUEUED")) == 1


@pytest.mark.parametrize("mutation", ["raw_actions", "source_actor", "source_message", "thread_pointer", "input_revision", "task_version", "consumed_confirmation"])
def test_preplan_raw_user_proof_rejects_source_or_epoch_changes(chat, mutation):
    first = turn(chat, "帮我转帐1w给quyuwei")
    second = turn(chat, "转给quyuwei 1w人民币")
    assert second.mode == "REVISE" and second.task_id == first.task_id
    with chat[0].begin() as db:
        from zhenxing.conversation_models import ChatThread, ChatTurn
        parent = db.get(Task, first.task_id)
        record = db.get(RuntimeRecord, parent.id)
        source = db.get(ChatTurn, first.id)
        metadata = deepcopy(record.metadata_json)
        if mutation == "raw_actions":
            metadata["owned_user_dialogue"]["prior_actions"][0]["slots"]["amount_minor"] = 2000000
        elif mutation == "source_actor":
            source.diagnostics_json = {**source.diagnostics_json, "actor_role_at_submission": "ADMIN"}
        elif mutation == "source_message":
            source.message = "帮我转帐1w给wangwei"
        elif mutation == "thread_pointer":
            db.get(ChatThread, chat[3]).current_task_id = None
        elif mutation == "input_revision":
            metadata["input_revision"] += 1
        elif mutation == "task_version":
            parent.version = 0
        else:
            from datetime import timedelta
            from zhenxing.models import utcnow
            db.add(Confirmation(sandbox_id=chat[2].sandbox_id, user_id=chat[2].user_id,
                task_id=parent.id, node_key="unexpected-consumed", action_type="TRANSFER", plan_hash="c"*64,
                view_hash="d"*64, status="CONSUMED", consumed_at=utcnow(), expires_at=utcnow()+timedelta(hours=1)))
            db.flush()
        with pytest.raises(DomainError):
            load_verified_dialogue(db, parent, metadata)


@pytest.mark.parametrize("status", ["UNKNOWN", "AUTHORIZED", "RUNNING", "SUCCEEDED", "UNRECOGNIZED_STATE"])
def test_preplan_unknown_or_started_operation_data_blocks_restatement_without_duplication(chat, status):
    first = turn(chat, "帮我转帐1w给quyuwei")
    with chat[0].begin() as db:
        db.add(RuntimeOperation(parent_task_id=first.task_id, sandbox_id=chat[2].sandbox_id,
            operation_key="unexpected-preplan", fingerprint="a"*64, action_kind="TRANSFER", label="unexpected",
            ordinal=0, plan_version=0, status=status, idempotency_key="unexpected-preplan-operation"))
    second = turn(chat, "转给quyuwei 1w人民币")
    assert second.mode == "NEED_CONTEXT" and second.task_id == first.task_id
    with chat[0]() as db:
        assert db.get(RuntimeRecord, first.task_id).metadata_json.get("input_revision", 0) == 0
        assert db.scalar(select(func.count()).select_from(RuntimeRecord).where(RuntimeRecord.kind == "PARENT")) == 1
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0
