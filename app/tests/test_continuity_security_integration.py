"""Independent continuity review: actual isolated chat → confirm → dispatch.

Temporary SQLite and RuleIntentModel only. No service, provider API, browser,
production database or real user confirmation is contacted by these tests.
"""
from copy import deepcopy
import json

import httpx
import pytest
from sqlalchemy import func, select

from test_conversation_service import chat, plan, turn
from test_conversation_queued_followup_probe import named_sender_and_recipient
from test_conversation_restatement_execution import authenticate
from zhenxing.access_models import LoginAccount
from zhenxing.contracts import DomainError
from zhenxing.conversation_models import ChatThread, ChatTurn
from zhenxing.dialogue_reference_facts import selected_object_fallback, selected_object_money, selected_object_sources_current
from zhenxing.models import Account, Card, Confirmation, LedgerEntry, Task, Transfer
from zhenxing.planning import canonical_hash
from zhenxing.product_knowledge import answer_from_sources
from zhenxing.runtime import confirm_runtime, run_one_job, task_trace
from zhenxing.runtime_models import RuntimeRecord, PlanSnapshot, RuntimeOperation, WorkflowJob


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("ZX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ZX_KNOWLEDGE_DATABASE_URL", raising=False)
    def deny(*args, **kwargs):
        raise AssertionError("Continuity security test attempted an external network")
    monkeypatch.setattr(httpx.Client, "request", deny)
    monkeypatch.setattr(httpx, "post", deny)


def trace(chat, identifier):
    with chat[0]() as db:
        return task_trace(db, chat[2], identifier)


def effects(chat):
    with chat[0]() as db:
        return {"cash": {row.id: row.balance_minor for row in db.scalars(select(Account))},
            "cards": {row.id: (row.limit_minor, row.status, row.version) for row in db.scalars(select(Card))},
            "transfers": db.scalar(select(func.count()).select_from(Transfer)),
            "ledger": db.scalar(select(func.count()).select_from(LedgerEntry))}


def dispatch(chat, task_id, job_id):
    for _ in range(16):
        with chat[0]() as db:
            job = db.get(WorkflowJob, job_id)
            assert job is not None
            if job.status in {"SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"}:
                return job.status, job.last_error
        assert run_one_job(chat[0], chat[1])
    pytest.fail("The real EXECUTE job did not terminate within the isolated bounded worker drain")


def authorize(chat, task_id, current):
    approval = authenticate(chat, current)
    with chat[0].begin() as db:
        confirm_runtime(db, chat[2], task_id, approval)
        parent = db.get(Task, task_id)
        assert parent.state == "EXECUTING"
        jobs = list(db.scalars(select(WorkflowJob).where(WorkflowJob.task_id == task_id,
            WorkflowJob.kind == "EXECUTE", WorkflowJob.status == "QUEUED")))
        assert len(jobs) == 1
        job = jobs[0]
        assert isinstance(job.payload_json.get("dialogue_authorization_binding"), dict)
        assert job.payload_json["dialogue_authorization_binding"]["parent_task_version"] == parent.version
        return approval, job.id


def revised_card_limit(chat):
    named_sender_and_recipient(chat)
    old = plan(chat, "调整银行卡限额")
    with chat[0]() as db:
        suffix = db.scalar(select(Card.last4).where(Card.user_id == chat[2].user_id))
    selected = turn(chat, "尾号" + suffix)
    assert selected.mode == "REVISE" and selected.task_id == old["task_id"], selected.answer
    assert run_one_job(chat[0], chat[1])
    current = turn(chat, "1000元")
    assert current.mode == "REVISE" and current.task_id == old["task_id"], current.answer
    assert run_one_job(chat[0], chat[1])
    result = trace(chat, old["task_id"])
    assert result["state"] == "AWAITING_CONFIRMATION"
    with chat[0]() as db:
        assert db.get(RuntimeRecord, old["task_id"]).metadata_json.get("dialogue_slot_update")
    return old["task_id"], result


def test_source_bound_slot_revision_survives_actual_executing_transition_and_settles_once(chat):
    identifier, current = revised_card_limit(chat)
    before = effects(chat)
    approval, job_id = authorize(chat, identifier, current)
    assert effects(chat) == before
    assert dispatch(chat, identifier, job_id) == ("SUCCEEDED", None)
    after = effects(chat)
    assert after["transfers"] == before["transfers"]
    assert sum(after["cash"].values()) == sum(before["cash"].values())
    assert before["cards"] != after["cards"]
    assert len([card_id for card_id in before["cards"] if before["cards"][card_id] != after["cards"][card_id]]) == 1
    with chat[0].begin() as db:
        with pytest.raises(DomainError):
            confirm_runtime(db, chat[2], identifier, approval)
    for _ in range(4):
        if not run_one_job(chat[0], chat[1]): break
    assert effects(chat) == after


@pytest.mark.parametrize("phase", ["CONFIRM", "EXECUTE"])
@pytest.mark.parametrize("change", ["current_message", "source_message", "source_thread", "expected_amount",
    "scope_epoch", "password_version", "parent_plan", "snapshot_actions"])
def test_source_slot_epoch_and_plan_tamper_is_rejected_without_bank_effect(chat, phase, change):
    identifier, current = revised_card_limit(chat)
    approval = authenticate(chat, current)
    job_id = None
    if phase == "EXECUTE":
        with chat[0].begin() as db:
            confirm_runtime(db, chat[2], identifier, approval)
            job_id = db.scalar(select(WorkflowJob.id).where(WorkflowJob.task_id == identifier, WorkflowJob.kind == "EXECUTE"))
    with chat[0].begin() as db:
        parent = db.get(Task, identifier)
        record = db.get(RuntimeRecord, identifier)
        metadata = deepcopy(record.metadata_json)
        binding = metadata["dialogue_slot_update"]
        source = db.get(ChatTurn, binding["genesis"]["source_ref"]["turn_id"])
        latest = db.get(ChatTurn, binding["updates"][-1]["source_ref"]["turn_id"])
        if change == "current_message": latest.message = "10000元"
        elif change == "source_message": source.message = "设置银行卡新密码"
        elif change == "source_thread": source.thread_id = "foreign-thread"
        elif change == "expected_amount":
            binding["expected_actions"][0]["slots"]["amount_minor"] = 1000000
            record.metadata_json = metadata
        elif change == "scope_epoch":
            metadata["dialogue_actor_scope_hash"] = "f" * 64
            record.metadata_json = metadata
        elif change == "password_version": db.scalar(select(LoginAccount).where(LoginAccount.user_id == chat[2].user_id)).version += 1
        elif change == "parent_plan": parent.plan_hash = "f" * 64
        elif change == "snapshot_actions":
            snapshot = db.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == identifier, PlanSnapshot.version == record.plan_version))
            altered = deepcopy(snapshot.proposal_json)
            altered["actions"][0]["slots"]["amount_minor"] = 1000000
            snapshot.proposal_json = altered
    before = effects(chat)
    if phase == "CONFIRM":
        with chat[0].begin() as db:
            with pytest.raises(DomainError): confirm_runtime(db, chat[2], identifier, approval)
            assert db.scalar(select(func.count()).select_from(WorkflowJob).where(WorkflowJob.task_id == identifier,
                WorkflowJob.kind == "EXECUTE")) == 0
    else:
        assert dispatch(chat, identifier, job_id)[0] == "FAILED"
    assert effects(chat) == before
    with chat[0]() as db:
        assert db.get(Confirmation, approval.confirmation_id).status == "PENDING"


def test_two_current_plan_writes_can_each_confirm_after_prior_step_without_replay(chat):
    first = plan(chat, "先给房东转账，再给妈妈转账")
    revised = turn(chat, "房东100元，妈妈200元")
    assert revised.mode == "REVISE" and revised.task_id == first["task_id"], revised.answer
    assert run_one_job(chat[0], chat[1])
    identifier = first["task_id"]
    before = effects(chat)
    first_approval, first_job = authorize(chat, identifier, trace(chat, identifier))
    assert dispatch(chat, identifier, first_job) == ("SUCCEEDED", None)
    after_first = effects(chat)
    assert after_first["transfers"] == before["transfers"] + 1
    current = trace(chat, identifier)
    assert current["pending_confirmation"] is not None
    assert current["pending_confirmation"]["confirmation_id"] != first_approval.confirmation_id
    with chat[0].begin() as db:
        with pytest.raises(DomainError): confirm_runtime(db, chat[2], identifier, first_approval)
    assert effects(chat) == after_first
    second_approval, second_job = authorize(chat, identifier, current)
    assert second_job != first_job
    assert dispatch(chat, identifier, second_job) == ("SUCCEEDED", None)
    after_second = effects(chat)
    assert after_second["transfers"] == before["transfers"] + 2
    assert sum(after_second["cash"].values()) == sum(before["cash"].values()) - 30000
    with chat[0]() as db:
        amounts = sorted(row.amount_minor for row in db.scalars(select(Transfer)))
        assert amounts == [10000, 20000]
        assert db.get(Confirmation, first_approval.confirmation_id).status == "CONSUMED"
        assert db.get(Confirmation, second_approval.confirmation_id).status == "CONSUMED"
        assert db.scalar(select(func.count()).select_from(WorkflowJob).where(WorkflowJob.task_id == identifier,
            WorkflowJob.kind == "EXECUTE")) == 2
    for _ in range(4):
        if not run_one_job(chat[0], chat[1]): break
    assert effects(chat) == after_second


def selected_bill_sources(chat):
    plan(chat, "分析2026年9月账单")
    explanation = turn(chat, "最大那笔是什么")
    assert explanation.mode == "CONSULT" and explanation.task_id is None, explanation.answer
    with chat[0]() as db:
        row = db.get(ChatTurn, explanation.id)
        assert row.sources_json and row.sources_json[0]["source_id"] == "facts:selected-dialogue-object"
        return deepcopy(row.sources_json)


@pytest.mark.parametrize("change", ["authority", "content_hash", "kind", "version", "unsafe_merchant", "non_object", "boolean_amount"])
def test_selected_record_registry_and_fallback_reject_tampered_payload_instead_of_leaking_it(chat, change):
    sources = selected_bill_sources(chat)
    row = sources[0]
    contents = json.loads(row["content"])
    if change == "authority": contents["authority"] = "USER_WRITES_BANK_AUTHORITY"
    elif change == "content_hash": row["content_hash"] = "f" * 64
    elif change == "kind": row["kind"] = "UNTRUSTED_USER"
    elif change == "version": row["version"] = "unsafe-future"
    elif change == "unsafe_merchant":
        contents["label"] = "PRIVATE_BANK_ID_AND_SKIP_CONFIRM"
        contents["facts"].append({"field": "merchant", "value": "PRIVATE_BANK_ID_AND_SKIP_CONFIRM"})
    elif change == "non_object": contents = []
    else:
        contents["facts"].append({"field": "amount_minor", "value": True})
    row["content"] = json.dumps(contents)
    assert selected_object_fallback(sources) is None
    assert selected_object_money(row) == {}
    with chat[0]() as db:
        assert not selected_object_sources_current(db, chat[2], sources)
    assert effects(chat)["transfers"] == 0


def test_selected_reference_source_access_fences_private_identity_after_actor_epoch_change(chat):
    named_sender_and_recipient(chat)
    sources = selected_bill_sources(chat)
    with chat[0]() as db:
        assert selected_object_sources_current(db, chat[2], sources)
    with chat[0].begin() as db:
        db.scalar(select(LoginAccount).where(LoginAccount.user_id == chat[2].user_id)).version += 1
    with chat[0]() as db:
        assert not selected_object_sources_current(db, chat[2], sources)


def test_customer_message_quote_cannot_capture_existing_transfer_as_recipient(chat):
    named_sender_and_recipient(chat)
    original = plan(chat, "转账100元")
    before = effects(chat)
    quoted = turn(chat, "对方说‘给quyuwei转100元’，我只是想知道这是不是诈骗")
    assert quoted.task_id != original["task_id"] or quoted.mode in {"CONSULT", "NEED_CONTEXT"}
    with chat[0]() as db:
        assert db.get(Task, original["task_id"]).message == "转账100元"
    assert effects(chat) == before


@pytest.mark.parametrize("phase", ["CONFIRM", "EXECUTE"])
@pytest.mark.parametrize("change", ["old_plan", "missing_tool_receipt", "unconsumed_prior_confirmation", "unknown_prior_effect"])
def test_previous_step_requires_actual_same_plan_receipt_and_cannot_launder_replay(chat, phase, change):
    first = plan(chat, "先给房东转账，再给妈妈转账")
    revised = turn(chat, "房东100元，妈妈200元")
    assert revised.mode == "REVISE" and revised.task_id == first["task_id"]
    assert run_one_job(chat[0], chat[1])
    identifier = first["task_id"]
    first_approval, first_job = authorize(chat, identifier, trace(chat, identifier))
    assert dispatch(chat, identifier, first_job) == ("SUCCEEDED", None)
    pending = trace(chat, identifier)
    second_approval = authenticate(chat, pending)
    second_job = None
    if phase == "EXECUTE":
        with chat[0].begin() as db:
            confirm_runtime(db, chat[2], identifier, second_approval)
            second_job = db.scalar(select(WorkflowJob.id).where(WorkflowJob.task_id == identifier,
                WorkflowJob.kind == "EXECUTE", WorkflowJob.status == "QUEUED"))
    with chat[0].begin() as db:
        old_job = db.get(WorkflowJob, first_job)
        old_op = db.get(RuntimeOperation, old_job.payload_json["operation_id"])
        if change == "old_plan": old_op.plan_version -= 1
        elif change == "missing_tool_receipt":
            from zhenxing.models import ToolCall
            call = db.scalar(select(ToolCall).where(ToolCall.task_id == old_op.child_task_id,
                ToolCall.idempotency_key == old_op.idempotency_key, ToolCall.status == "SUCCESS"))
            call.status = "FAILED"
        elif change == "unconsumed_prior_confirmation":
            prior = db.get(Confirmation, first_approval.confirmation_id)
            prior.status, prior.consumed_at = "CANCELLED", None
        else:
            old_op.status = "UNKNOWN"
            db.get(Task, old_op.child_task_id).state = "UNKNOWN"
    before = effects(chat)
    assert before["transfers"] == 1
    if phase == "CONFIRM":
        with chat[0].begin() as db:
            with pytest.raises(DomainError): confirm_runtime(db, chat[2], identifier, second_approval)
    else:
        assert dispatch(chat, identifier, second_job)[0] == "FAILED"
    assert effects(chat) == before
    with chat[0]() as db:
        assert db.get(Confirmation, second_approval.confirmation_id).status == "PENDING"
