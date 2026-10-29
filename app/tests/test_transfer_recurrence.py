from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_banking_extensions import db, confirm, advance_only
from zhenxing.banking_extension_models import ScheduledTransferSeries, ScheduledTransferOccurrence, ScopedScheduledTransfer
from zhenxing.banking_extensions import prepare_extra_task, execute_due_schedules, cancel_schedules_for_task
from zhenxing.contracts import FinancialIntentContract, DomainError
from zhenxing.models import Task, Transfer, Account, LedgerEntry, new_id
from zhenxing.platform import get_or_create_conversation
from zhenxing.seed import create_demo_sandbox
from zhenxing.runtime_models import RuntimeRecord
from zhenxing.services import Principal
from zhenxing.transfer_recurrence import occurrence_dates, recurrence_scope, series_view


def recurring(db, *, count=3, frequency="DAILY"):
    sandbox, user, _ = create_demo_sandbox(db)
    task = Task(sandbox_id=sandbox.id, user_id=user.id, message="明天给房东转100元", intent_type="TRANSFER_SCHEDULE",
                state="NEED_CLARIFICATION", model_label="isolated")
    task.fic_json = FinancialIntentContract(intent_id=new_id(), intent_version=1, intent_type=task.intent_type,
        actor_user_id=user.id, sandbox_id=sandbox.id, source_channel="WEB", slots={}, requested_capabilities=[],
        status="NEED_CLARIFICATION").model_dump()
    db.add(task); db.flush(); db.add(RuntimeRecord(task_id=task.id, kind="CHILD"))
    conversation = get_or_create_conversation(db, sandbox.id, user.id)
    prepare_extra_task(db, task=task, conversation_id=conversation.id, proposed_action={"kind": "TRANSFER_SCHEDULE",
        "slots": {"payee_name": "房东", "amount_minor": 10000, "scheduled_at": "明天",
                  "schedule_frequency": frequency, "schedule_occurrence_count": count}})
    assert task.state == "AWAITING_CONFIRMATION"
    return task, Principal(user.id, sandbox.id)


def test_month_end_uses_original_anchor_and_bounded_range_is_required():
    first = datetime(2026, 1, 31, 1, tzinfo=timezone.utc)
    assert [date.day for date in occurrence_dates(first, "MONTHLY", 3)] == [31, 28, 31]
    with pytest.raises(DomainError) as error:
        recurrence_scope({"schedule_frequency": "DAILY"}, first)
    assert error.value.code == "SCHEDULE_BOUNDED_RANGE_REQUIRED"
    with pytest.raises(DomainError): occurrence_dates(first, "UNREGISTERED", 3)
    with pytest.raises(DomainError): occurrence_dates(first, "DAILY", True)


def test_eight_monthly_occurrences_post_once_each_in_isolated_bank_clock(db):
    from datetime import timedelta
    from zhenxing.authentication import as_utc
    from zhenxing.simulation_models import SimulationState
    task,principal=recurring(db,count=8,frequency='MONTHLY')
    account=db.scalar(select(Account).where(Account.user_id==principal.user_id))
    before=account.balance_minor
    confirm(db,task,principal)
    series=db.scalar(select(ScheduledTransferSeries).where(ScheduledTransferSeries.task_id==task.id))
    clock=db.scalar(select(SimulationState).where(SimulationState.sandbox_id==task.sandbox_id))
    assert account.balance_minor==before
    for index,raw in enumerate(series.scope_json['recurrence']['dates'],1):
        clock.business_time=as_utc(datetime.fromisoformat(raw))+timedelta(seconds=1)
        db.flush()
        assert execute_due_schedules(db)==1
        assert execute_due_schedules(db)==0
        assert account.balance_minor==before-index*10000
        assert db.scalar(select(func.count()).select_from(Transfer))==index
    assert task.state=='SUCCEEDED'
    assert {row.status for row in db.scalars(select(ScheduledTransferOccurrence))}=={'POSTED'}
    assert db.scalar(select(func.sum(LedgerEntry.delta_minor)))==0


def test_three_occurrences_each_post_once_across_restart_and_preserve_distinct_receipts(db):
    task, principal = recurring(db)
    account = db.scalar(select(Account).where(Account.user_id == principal.user_id))
    before = account.balance_minor
    confirm(db, task, principal)
    assert account.balance_minor == before and task.state == "WAITING"
    assert db.scalar(select(func.count()).select_from(ScheduledTransferOccurrence)) == 3
    advance_only(db, task.sandbox_id, 3)
    assert execute_due_schedules(db) == 1
    assert task.state == "WAITING" and account.balance_minor == before - 10000
    task_id = task.id
    db.commit()
    with Session(db.bind) as restarted:
        assert execute_due_schedules(restarted) == 1
        restarted.commit()
    db.expire_all()
    assert execute_due_schedules(db) == 1
    assert execute_due_schedules(db) == 0
    assert db.get(Task, task_id).state == "SUCCEEDED"
    assert db.get(Account, account.id).balance_minor == before - 30000
    assert db.scalar(select(func.count()).select_from(Transfer)) == 3
    assert db.scalar(select(func.sum(LedgerEntry.delta_minor))) == 0
    assert {row.status for row in db.scalars(select(ScheduledTransferOccurrence))} == {"POSTED"}
    assert len({row.transfer_id for row in db.scalars(select(ScheduledTransferOccurrence))}) == 3


def test_insufficient_current_funds_marks_this_occurrence_failed_and_stops_remaining(db):
    task, principal = recurring(db)
    confirm(db, task, principal)
    account = db.scalar(select(Account).where(Account.user_id == principal.user_id))
    account.balance_minor = 1
    advance_only(db, task.sandbox_id, 3)
    assert execute_due_schedules(db) == 1
    rows = list(db.scalars(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal)))
    assert [row.status for row in rows] == ["FAILED", "CANCELLED", "CANCELLED"]
    assert rows[0].error_code == "INSUFFICIENT_FUNDS"
    assert task.state == "FAILED" and account.balance_minor == 1
    account.balance_minor = 1000000
    assert execute_due_schedules(db) == 0
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_cancel_after_first_occurrence_preserves_posting_and_cancels_only_future(db):
    task, principal = recurring(db)
    confirm(db, task, principal)
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 1
    assert cancel_schedules_for_task(db, task.id) == 1
    advance_only(db, task.sandbox_id, 10)
    assert execute_due_schedules(db) == 0
    rows = list(db.scalars(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal)))
    assert [row.status for row in rows] == ["POSTED", "CANCELLED", "CANCELLED"]
    assert db.scalar(select(func.count()).select_from(Transfer)) == 1
    assert task.state == "CLOSED"


def test_tampered_series_scope_or_occurrence_never_expands_authorization(db):
    task, principal = recurring(db)
    confirm(db, task, principal)
    series = db.scalar(select(ScheduledTransferSeries))
    changed = deepcopy(series.scope_json)
    changed["amount_minor"] = 20000
    series.scope_json = changed
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 1
    assert db.scalar(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal)).error_code == "SCHEDULE_SCOPE_CHANGED"
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0


@pytest.mark.parametrize("field,value", [("amount_minor", 20000), ("source_account_id", "untrusted"),
                                         ("payee_id", "untrusted")])
def test_series_signed_schedule_columns_cannot_be_replaced(db, field, value):
    task, principal = recurring(db)
    confirm(db, task, principal)
    row = db.scalar(select(ScopedScheduledTransfer))
    setattr(row, field, value)
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 1
    assert db.scalar(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal)).error_code == "SCHEDULE_SCOPE_CHANGED"
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_occurrence_ordinal_and_stable_bank_key_are_both_bound(db):
    task, principal = recurring(db)
    confirm(db, task, principal)
    occurrence = db.scalar(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal))
    occurrence.business_key = "different-bank-key"
    advance_only(db, task.sandbox_id)
    assert execute_due_schedules(db) == 1
    assert occurrence.status == "FAILED" and occurrence.error_code == "SCHEDULE_SCOPE_CHANGED"
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
