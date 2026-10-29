from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from zhenxing.authentication import create_challenge, verify_challenge
from zhenxing.banking_extension_models import AACollectionRequest, AALedgerEntry, AAParticipant, ScopedScheduledTransfer
from zhenxing.banking_extensions import (
    cancel_schedules_for_task, dispatch_extra_task, execute_due_schedules,
    parse_schedule_date, prepare_extra_task, simulate_aa_payment,
)
from zhenxing.contracts import ConfirmTaskRequest, DomainError, FinancialIntentContract
from zhenxing.database import Base
from zhenxing.intelligence import canonical_action_message, rule_proposal
from zhenxing.models import Account, Confirmation, LedgerEntry, Task, Transaction, Transfer, new_id
from zhenxing.model_gateway import IntentDecision, ModelGateway, RuleIntentModel
from zhenxing.platform import get_or_create_conversation
from zhenxing.runtime_models import RuntimeRecord
from zhenxing.seed import create_demo_sandbox
from zhenxing.schema import ensure_schema
from zhenxing.services import Principal
from zhenxing.simulation import BUSINESS_TIME, as_utc, inspect_simulation
from zhenxing.simulation_models import SimulationState


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ensure_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def prepare(db, kind, message):
    sandbox, user, _card = create_demo_sandbox(db)
    conversation = get_or_create_conversation(db, sandbox.id, user.id)
    task = Task(sandbox_id=sandbox.id, user_id=user.id, message=message, intent_type=kind,
                state="NEED_CLARIFICATION", model_label="test")
    task.fic_json = FinancialIntentContract(intent_id=new_id(), intent_version=1, intent_type=kind,
        actor_user_id=user.id, sandbox_id=sandbox.id, source_channel="WEB", slots={},
        requested_capabilities=[], status="NEED_CLARIFICATION").model_dump()
    db.add(task)
    db.flush()
    db.add(RuntimeRecord(task_id=task.id, kind="CHILD"))
    prepare_extra_task(db, task=task, conversation_id=conversation.id)
    return task, Principal(user.id, sandbox.id)


def confirm(db, task, principal, *, auth=True, key="extra-operation"):
    confirmation = db.scalar(select(Confirmation).where(Confirmation.task_id == task.id))
    challenge_id = None
    if task.intent_type == "TRANSFER_SCHEDULE" and auth:
        challenge = create_challenge(db, task=task, confirmation=confirmation, user_id=principal.user_id)
        verify_challenge(db, challenge_id=challenge["challenge_id"], user_id=principal.user_id,
                         sandbox_id=principal.sandbox_id, code=challenge["demo_code"])
        challenge_id = challenge["challenge_id"]
    request = ConfirmTaskRequest(confirmation_id=confirmation.id, plan_hash=task.plan_hash,
                                expected_task_version=task.version, auth_challenge_id=challenge_id)
    dispatch_extra_task(db, task=task, principal=principal, request=request, idempotency_key=key)
    return request


def advance_only(db, sandbox_id, days=1):
    db.scalar(select(SimulationState).where(SimulationState.sandbox_id == sandbox_id)).business_time += timedelta(days=days)
    db.flush()


def test_schedule_is_scoped_authenticated_and_not_an_immediate_transfer(db):
    task, principal = prepare(db, "TRANSFER_SCHEDULE", "明天给房东转100元")
    account = db.scalar(select(Account).where(Account.sandbox_id == task.sandbox_id))
    before = account.balance_minor
    with pytest.raises(DomainError, match="二次验证"):
        confirm(db, task, principal, auth=False)
    request = confirm(db, task, principal)
    assert task.state == "WAITING" and account.balance_minor == before
    assert execute_due_schedules(db) == 0
    # Replaying registration is idempotent even after its confirmation expires.
    dispatch_extra_task(db, task=task, principal=principal, request=request, idempotency_key="extra-operation")
    assert db.scalar(select(func.count()).select_from(ScopedScheduledTransfer)) == 1
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 1
    assert task.state == "SUCCEEDED" and account.balance_minor == before - 10000
    assert execute_due_schedules(db) == 0
    assert db.scalar(select(func.count()).select_from(Transfer)) == 1
    assert db.scalar(select(func.sum(LedgerEntry.delta_minor))) == 0
    mirrored = db.scalar(select(Transaction).where(Transaction.source_kind == "TRANSFER"))
    assert mirrored.amount_minor == -10000 and mirrored.operation_ref.startswith("transfer:")
    assert inspect_simulation(db, task.sandbox_id)["reconciliation"]["balanced"] is True


def test_due_schedule_rechecks_balance_and_never_retries_a_known_failure(db):
    task, principal = prepare(db, "TRANSFER_SCHEDULE", "后天给妈妈转200元")
    confirm(db, task, principal)
    account = db.scalar(select(Account).where(Account.sandbox_id == task.sandbox_id))
    account.balance_minor = 100
    advance_only(db, task.sandbox_id, 2)
    assert execute_due_schedules(db) == 1
    assert task.state == "FAILED"
    assert task.result_json["error"] == "INSUFFICIENT_FUNDS"
    assert account.balance_minor == 100
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
    account.balance_minor = 500000
    assert execute_due_schedules(db) == 0


def test_schedule_can_be_cancelled_before_due_date(db):
    task, principal = prepare(db, "TRANSFER_SCHEDULE", "明天给房东转100元")
    confirm(db, task, principal)
    assert cancel_schedules_for_task(db, task.id) == 1
    assert cancel_schedules_for_task(db, task.id) == 0
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 0 and task.state == "CLOSED"


def test_schedule_survives_session_restart_and_rechecks_scope_integrity(db):
    task, principal = prepare(db, "TRANSFER_SCHEDULE", "明天给房东转100元")
    confirm(db, task, principal)
    task_id, sandbox_id = task.id, task.sandbox_id
    db.commit()
    with Session(db.bind) as restarted:
        advance_only(restarted, sandbox_id)
        assert execute_due_schedules(restarted) == 1
        restarted.commit()
    with Session(db.bind) as again:
        assert execute_due_schedules(again) == 0
        assert again.get(Task, task_id).state == "SUCCEEDED"
        assert again.scalar(select(func.count()).select_from(Transfer)) == 1


def test_modified_schedule_cannot_expand_confirmed_amount(db):
    task, principal = prepare(db, "TRANSFER_SCHEDULE", "明天给房东转100元")
    confirm(db, task, principal)
    db.scalar(select(ScopedScheduledTransfer)).amount_minor = 20000
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 1
    assert task.state == "FAILED" and task.result_json["error"] == "SCHEDULE_SCOPE_CHANGED"
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_past_or_invalid_dates_never_become_immediate_transfers(db):
    task, _ = prepare(db, "TRANSFER_SCHEDULE", "2026-09-01给房东转100元")
    assert task.state == "NEED_CLARIFICATION" and not task.plan_json
    assert parse_schedule_date("2026-02-30给房东转100元", BUSINESS_TIME) is None


def test_schedule_clock_is_fixed_and_next_week_date_is_unambiguous():
    assert parse_schedule_date("明天给房东转100元", BUSINESS_TIME).isoformat() == "2026-10-01T01:00:00+00:00"
    assert parse_schedule_date("下周五给妈妈转100元", BUSINESS_TIME).isoformat() == "2026-10-09T01:00:00+00:00"
    assert parse_schedule_date("2026-10-03 15:30给妈妈转100元", BUSINESS_TIME).isoformat() == "2026-10-03T07:30:00+00:00"
    assert parse_schedule_date("明天下午三点三十分给妈妈转100元", BUSINESS_TIME).isoformat() == "2026-10-01T07:30:00+00:00"
    proposal = rule_proposal("明天下午三点三十分给妈妈转100元")
    assert proposal.actions[0].slots.scheduled_at == "明天下午三点三十分"


def test_aa_remainder_and_payment_are_stateful_balanced_and_idempotent(db):
    task, principal = prepare(db, "AA_REQUEST", "AA总额100.01元，2人，参与者：妈妈、房东")
    account = db.scalar(select(Account).where(Account.sandbox_id == task.sandbox_id))
    before = account.balance_minor
    request = confirm(db, task, principal)
    assert task.state == "SUCCEEDED" and account.balance_minor == before
    row = db.scalar(select(AACollectionRequest))
    participants = list(db.scalars(select(AAParticipant).order_by(AAParticipant.ordinal)))
    assert [participant.amount_minor for participant in participants] == [5001, 5000]
    dispatch_extra_task(db, task=task, principal=principal, request=request, idempotency_key="extra-operation")
    assert db.scalar(select(func.count()).select_from(AACollectionRequest)) == 1
    first = simulate_aa_payment(db, row.id, participants[0].id, task.sandbox_id)
    assert first["status"] == "COLLECTING" and first["received_minor"] == 5001
    assert simulate_aa_payment(db, row.id, participants[0].id, task.sandbox_id) == first
    final = simulate_aa_payment(db, row.id, participants[1].id, task.sandbox_id)
    assert final["status"] == "COMPLETED" and final["received_minor"] == 10001
    assert account.balance_minor == before + 10001
    assert db.scalar(select(func.count()).select_from(AALedgerEntry)) == 4
    assert db.scalar(select(func.sum(AALedgerEntry.delta_minor))) == 0
    assert task.result_json["status"] == "COMPLETED" and task.result_json["received_minor"] == 10001
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "AA_COLLECTION")) == 2


def test_aa_cross_sandbox_payment_is_rejected(db):
    task, principal = prepare(db, "AA_REQUEST", "AA总额100元，2人，参与者：妈妈、房东")
    confirm(db, task, principal)
    row = db.scalar(select(AACollectionRequest))
    participant = db.scalar(select(AAParticipant))
    other_sandbox, _, _ = create_demo_sandbox(db)
    with pytest.raises(DomainError, match="不存在"):
        simulate_aa_payment(db, row.id, participant.id, other_sandbox.id)
    assert participant.status == "PENDING"


def test_aa_http_idempotency_keys_are_isolated_across_sandboxes(db):
    first, p1 = prepare(db, "AA_REQUEST", "AA总额100元，2人，参与者：妈妈、房东")
    confirm(db, first, p1, key="same-client-key")
    second, p2 = prepare(db, "AA_REQUEST", "AA总额100元，2人，参与者：妈妈、房东")
    confirm(db, second, p2, key="same-client-key")
    assert db.scalar(select(func.count()).select_from(AACollectionRequest)) == 2


@pytest.mark.parametrize("message", [
    "AA总额100元，3人，参与者：妈妈、房东", "AA总额100元，2人，参与者：妈妈、妈妈",
    "AA总额100元，三人，参与者：妈妈、房东",
    "AA总额100元，2人，参与者：妈妈、不存在", "AA总额100元，参与者：妈妈、小李",
])
def test_aa_ambiguous_or_incomplete_contacts_need_clarification(db, message):
    task, _ = prepare(db, "AA_REQUEST", message)
    assert task.state == "NEED_CLARIFICATION" and not task.plan_json


def test_future_transfer_proposal_uses_schedule_only_and_canonical_keeps_date():
    proposal = rule_proposal("明天给房东转100元")
    assert [action.kind for action in proposal.actions] == ["TRANSFER_SCHEDULE"]
    assert proposal.actions[0].slots.scheduled_at == "明天"
    assert canonical_action_message(proposal.actions[0]) == "明天 给房东转100.00元"
    assert rule_proposal("如果余额够，明天给房东转100元").actions == []
    assert rule_proposal("如果余额够，给房东转100元").actions == []


def test_legacy_classifier_cannot_route_future_request_to_immediate_debit():
    class ImmediateModel:
        def classify(self, _message):
            return IntentDecision("TRANSFER", "forced", 1.0)

    assert ModelGateway(ImmediateModel()).classify("明天给房东转100元").intent_type == "TRANSFER_SCHEDULE"
    assert RuleIntentModel().classify("AA总额100元，2人，参与者：妈妈、房东").intent_type == "AA_REQUEST"


def test_aa_proposal_roundtrips_names_count_and_amount():
    proposal = rule_proposal("AA总额100元，2人，参与者：妈妈、房东")
    assert len(proposal.actions) == 1
    item = proposal.actions[0]
    assert item.kind == "AA_REQUEST" and item.slots.participant_names == ["妈妈", "房东"]
    assert item.slots.participant_count == 2 and item.slots.amount_minor == 10000
    assert "参与者：妈妈、房东" in canonical_action_message(item)
