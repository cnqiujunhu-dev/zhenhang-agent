"""Scheduled identity rechecks use disposable accounts, not the formal bank."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from test_internal_banking import internal_bank
from test_fx_runtime_integration import OCTOBER_NOW, TOMORROW_NINE
from zhenxing import fx_models  # Register the FX tables also read by bank reconciliation.
from zhenxing.authentication import create_challenge, verify_challenge
from zhenxing.banking_extension_models import ScopedScheduledTransfer
from zhenxing.banking_extensions import dispatch_extra_task, execute_due_schedules, prepare_extra_task
from zhenxing.card_identifiers import card_identifier_view
from zhenxing.contact_models import ContactAlias
from zhenxing.contracts import ConfirmTaskRequest, DomainError, FinancialIntentContract
from zhenxing.models import Card, Confirmation, LedgerEntry, Task, Transaction, Transfer, new_id
from zhenxing.platform import get_or_create_conversation
from zhenxing.planning import canonical_hash
from zhenxing.runtime_models import RuntimeRecord
from zhenxing.services import Principal
from zhenxing.simulation import BUSINESS_TIME, as_utc, inspect_simulation
from zhenxing.simulation_models import SimulationState


@pytest.fixture(autouse=True)
def registered_calendar(internal_bank, monkeypatch):
    # Override the legacy report fixture's posting clock only for this module's
    # registered-customer schedules. Authorization/OTP expiry still uses UTC now.
    monkeypatch.setattr("zhenxing.reporting_clock.calendar_now", lambda: OCTOBER_NOW)
    monkeypatch.setattr("zhenxing.operational_clock.utcnow", lambda: OCTOBER_NOW)


def prepare_schedule(db, owner, reference):
    task = Task(sandbox_id=owner.sandbox_id, user_id=owner.user_id,
                message=f"明天给{reference}转100元", intent_type="TRANSFER_SCHEDULE",
                state="NEED_CLARIFICATION", model_label="independent-test")
    task.fic_json = FinancialIntentContract(
        intent_id=new_id(), intent_version=1, intent_type=task.intent_type,
        actor_user_id=owner.user_id, sandbox_id=owner.sandbox_id,
        source_channel="WEB", slots={}, requested_capabilities=[],
        status="NEED_CLARIFICATION").model_dump()
    db.add(task)
    db.flush()
    db.add(RuntimeRecord(task_id=task.id, kind="CHILD"))
    conversation = get_or_create_conversation(db, owner.sandbox_id, owner.user_id)
    prepare_extra_task(db, task=task, conversation_id=conversation.id)
    assert task.state == "AWAITING_CONFIRMATION"
    return task


def registration_request(db, task, owner):
    confirmation = db.scalar(select(Confirmation).where(Confirmation.task_id == task.id))
    challenge = create_challenge(db, task=task, confirmation=confirmation, user_id=owner.user_id)
    verify_challenge(db, challenge_id=challenge["challenge_id"], user_id=owner.user_id,
                     sandbox_id=owner.sandbox_id, code=challenge["demo_code"])
    return ConfirmTaskRequest(confirmation_id=confirmation.id, plan_hash=task.plan_hash,
        expected_task_version=task.version, auth_challenge_id=challenge["challenge_id"])


def register(db, task, owner, request=None):
    dispatch_extra_task(db, task=task, principal=Principal(owner.user_id, owner.sandbox_id),
        request=request or registration_request(db, task, owner), idempotency_key=f"register:{task.id}")
    assert task.state == "WAITING"


def assert_registered_schedule_not_due(db, owner):
    row = db.scalar(select(ScopedScheduledTransfer))
    assert row.scope_json["clock_basis"] == "CALENDAR_REAL"
    assert as_utc(row.due_at) == TOMORROW_NINE
    assert execute_due_schedules(db, calendar_time=OCTOBER_NOW) == 0
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE-timedelta(microseconds=1)) == 0
    clock = db.scalar(select(SimulationState).where(SimulationState.sandbox_id == owner.sandbox_id))
    assert as_utc(clock.business_time) == BUSINESS_TIME


def balances_and_postings(db, source, target):
    return (source.balance_minor, target.balance_minor,
            db.scalar(select(func.count()).select_from(Transfer)),
            db.scalar(select(func.count()).select_from(LedgerEntry)),
            db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "TRANSFER")))


def saved_alias(db, owner, receiver, target, name="房东"):
    # This fixture represents an already confirmed payer-owned binding.
    row = ContactAlias(sandbox_id=owner.sandbox_id, user_id=owner.user_id, alias_name=name,
                       recipient_login_account_id=receiver.id, recipient_bank_account_id=target.id)
    db.add(row)
    db.flush()
    return row


def test_due_schedule_rejects_locked_explicit_recipient_card(internal_bank):
    db, owner, receiver, source, target, _admin = internal_bank
    card = db.scalar(select(Card).where(Card.user_id == receiver.user_id))
    number = card_identifier_view(db, card)["card_number"]
    task = prepare_schedule(db, owner, number)
    register(db, task, owner)
    before = balances_and_postings(db, source, target)
    card.status, card.version = "LOCKED", card.version + 1
    assert_registered_schedule_not_due(db, owner)
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 1
    assert task.state == "FAILED", "A locked explicitly approved recipient card must not be bypassed"
    assert balances_and_postings(db, source, target) == before
    assert db.scalar(select(ScopedScheduledTransfer)).last_error in {"RECIPIENT_CARD_CHANGED", "RECIPIENT_CARD_UNAVAILABLE"}


def test_due_schedule_rejects_rebound_personal_alias(internal_bank):
    db, owner, receiver, source, target, _admin = internal_bank
    alias = saved_alias(db, owner, receiver, target)
    task = prepare_schedule(db, owner, "房东")
    register(db, task, owner)
    before = balances_and_postings(db, source, target)
    alias.version += 1
    assert_registered_schedule_not_due(db, owner)
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 1
    assert task.state == "FAILED", "The alias version bound to this authorization is no longer current"
    assert db.scalar(select(ScopedScheduledTransfer)).last_error == "CONTACT_ALIAS_VERSION_CHANGED"
    assert balances_and_postings(db, source, target) == before


@pytest.mark.parametrize("change", ["card", "alias"])
def test_changed_identity_before_registration_does_not_consume_authorization(internal_bank, change):
    db, owner, receiver, source, target, _admin = internal_bank
    card = db.scalar(select(Card).where(Card.user_id == receiver.user_id))
    alias = saved_alias(db, owner, receiver, target)
    reference = card_identifier_view(db, card)["card_number"] if change == "card" else alias.alias_name
    task = prepare_schedule(db, owner, reference)
    request = registration_request(db, task, owner)
    if change == "card":
        # Even an active card with changed bank facts invalidates its version.
        card.version += 1
    else:
        alias.version += 1
    db.flush()
    before = balances_and_postings(db, source, target)
    with pytest.raises(DomainError) as rejected:
        with db.begin_nested():
            register(db, task, owner, request)
    assert rejected.value.code == ("RECIPIENT_CARD_CHANGED" if change == "card" else "CONTACT_ALIAS_VERSION_CHANGED")
    from zhenxing.runtime_models import AuthChallenge
    assert db.get(AuthChallenge, request.auth_challenge_id).status == "VERIFIED"
    assert db.get(Confirmation, request.confirmation_id).status == "PENDING"
    assert db.scalar(select(func.count()).select_from(ScopedScheduledTransfer)) == 0
    assert balances_and_postings(db, source, target) == before


@pytest.mark.parametrize("reference_kind", ["card", "alias", "username"])
def test_unchanged_recipient_identity_executes_once_with_both_bank_ledgers_balanced(internal_bank, reference_kind):
    db, owner, receiver, source, target, _admin = internal_bank
    card = db.scalar(select(Card).where(Card.user_id == receiver.user_id))
    alias = saved_alias(db, owner, receiver, target)
    reference = {"card": card_identifier_view(db, card)["card_number"],
                 "alias": alias.alias_name, "username": receiver.username}[reference_kind]
    task = prepare_schedule(db, owner, reference)
    pending = task.plan_json["nodes"][0]["recipient_context"]
    assert pending["requested_reference"] == reference
    assert pending["recipient_bank_account_id"] == target.id
    if reference_kind == "card":
        assert pending["recipient_card_id"] == card.id and pending["recipient_card_version"] == card.version
    elif reference_kind == "alias":
        assert pending["alias_read"]["version"] == alias.version
    else:
        assert "recipient_card_id" not in pending and "alias_read" not in pending
    register(db, task, owner)
    row = db.scalar(select(ScopedScheduledTransfer))
    assert row.scope_json["recipient_context"] == pending
    before = balances_and_postings(db, source, target)
    assert_registered_schedule_not_due(db, owner)
    assert balances_and_postings(db, source, target) == before
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 1 and task.state == "SUCCEEDED"
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 0
    after = balances_and_postings(db, source, target)
    assert after == (before[0] - 10000, before[1] + 10000, before[2] + 1, before[3] + 4, before[4] + 2)
    assert inspect_simulation(db, owner.sandbox_id)["reconciliation"]["balanced"]
    assert inspect_simulation(db, receiver.sandbox_id)["reconciliation"]["balanced"]


def test_historical_signed_internal_scope_without_context_keeps_account_only_compatibility(internal_bank, monkeypatch):
    db, owner, receiver, source, target, _admin = internal_bank
    # Construct a pre-calendar, independently signed fixture at its original
    # bank time. Never reinterpret its missing clock_basis as CALENDAR_REAL.
    with monkeypatch.context() as historical:
        historical.setattr("zhenxing.reporting_clock.calendar_now", lambda: BUSINESS_TIME)
        task = prepare_schedule(db, owner, receiver.username)
    # Reconstruct an older signed account-only fixture, before it is authorized.
    # No live API can edit a plan or replace a confirmation hash this way.
    obsolete = {"recipient_context", "clock_basis"}
    plan = {**task.plan_json, "nodes": [{k: v for k, v in task.plan_json["nodes"][0].items() if k not in obsolete}]}
    task.plan_json, task.plan_hash = plan, canonical_hash(plan)
    task.fic_json = {**task.fic_json, "slots": {k: ({**v, "source": "DERIVED_FROM_BUSINESS_CLOCK"} if k == "due_at" else v)
        for k, v in task.fic_json["slots"].items() if k not in obsolete}}
    confirmation = db.scalar(select(Confirmation).where(Confirmation.task_id == task.id))
    scope = {k: v for k, v in plan["nodes"][0].items() if k not in {"node_id", "tool_name", "read_only", "requires_confirmation"}}
    confirmation.plan_hash = task.plan_hash
    confirmation.view_hash = canonical_hash({"plan_hash": task.plan_hash, "scope": scope})
    db.flush()
    register(db, task, owner)
    row = db.scalar(select(ScopedScheduledTransfer))
    assert "recipient_context" not in row.scope_json and "clock_basis" not in row.scope_json
    assert as_utc(row.due_at) == BUSINESS_TIME.replace(day=1, month=10, hour=1)
    before = balances_and_postings(db, source, target)
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 0
    assert balances_and_postings(db, source, target) == before
    db.scalar(select(SimulationState).where(SimulationState.sandbox_id == owner.sandbox_id)).business_time += timedelta(days=1)
    db.flush()
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 1 and task.state == "SUCCEEDED"
    assert execute_due_schedules(db, calendar_time=TOMORROW_NINE) == 0
    assert (source.balance_minor, target.balance_minor) == (before[0] - 10000, before[1] + 10000)
    assert balances_and_postings(db, source, target) == (before[0]-10000, before[1]+10000, before[2]+1, before[3]+4, before[4]+2)
    assert inspect_simulation(db, owner.sandbox_id)["reconciliation"]["balanced"]
    assert inspect_simulation(db, receiver.sandbox_id)["reconciliation"]["balanced"]
