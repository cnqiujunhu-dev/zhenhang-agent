"""Real isolated bank rows: replacements require a new bounded confirmation."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, func

from test_banking_extensions import db, confirm, advance_only
from test_transfer_recurrence import recurring
from zhenxing.banking_extension_models import ScopedScheduledTransfer, ScheduledTransferOccurrence, ScheduledTransferSeries
from zhenxing.banking_extensions import prepare_extra_task, execute_due_schedules, cancel_schedules_for_task
from zhenxing.contracts import DomainError, FinancialIntentContract
from zhenxing.models import Task, Transfer, Account, Confirmation, new_id
from zhenxing.runtime_models import RuntimeRecord
from zhenxing.platform import get_or_create_conversation
from zhenxing.schedule_amendments import replacement_snapshot
from zhenxing.schedule_presentation import schedule_projection, timing_policy


def amendment(db, old_task, principal, **changes):
    row = db.scalar(select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.task_id == old_task.id))
    task = Task(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        message='把这份预约以后的金额改成120元', intent_type='TRANSFER_SCHEDULE', state='NEED_CLARIFICATION', model_label='test')
    task.fic_json = FinancialIntentContract(intent_id=new_id(), intent_version=1, intent_type=task.intent_type,
        actor_user_id=principal.user_id, sandbox_id=principal.sandbox_id, source_channel='WEB', slots={},
        requested_capabilities=[], status='NEED_CLARIFICATION').model_dump()
    db.add(task); db.flush(); db.add(RuntimeRecord(task_id=task.id, kind='CHILD'))
    conversation = get_or_create_conversation(db, principal.sandbox_id, principal.user_id)
    prepare_extra_task(db, task=task, conversation_id=conversation.id,
        proposed_action={'kind':'TRANSFER_SCHEDULE','slots':{'schedule_reference':row.id, **changes}})
    return task


def test_new_confirmation_atomically_replaces_future_only_and_replay_posts_once(db):
    old, principal = recurring(db)
    confirm(db, old, principal)
    advance_only(db, principal.sandbox_id)
    execute_due_schedules(db)
    original = db.scalar(select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.task_id == old.id))
    old_plan = deepcopy(old.plan_json)
    new = amendment(db, old, principal, amount_minor=12000)
    assert original.status == 'WAITING' and new.state == 'AWAITING_CONFIRMATION'
    with pytest.raises(DomainError): confirm(db,new,principal,auth=False,key='replace-no-otp')
    assert original.status == 'WAITING'
    request = confirm(db,new,principal,key='replace')
    assert original.status == 'CANCELLED' and old.plan_json == old_plan
    series = db.scalar(select(ScheduledTransferSeries).where(ScheduledTransferSeries.task_id == old.id))
    occurrences = list(db.scalars(select(ScheduledTransferOccurrence).where(ScheduledTransferOccurrence.series_id==series.id)
        .order_by(ScheduledTransferOccurrence.ordinal)))
    assert [o.status for o in occurrences] == ['POSTED','CANCELLED','CANCELLED']
    from zhenxing.banking_extensions import dispatch_extra_task
    dispatch_extra_task(db,task=new,principal=principal,request=request,idempotency_key='replace')
    assert db.scalar(select(func.count()).select_from(ScopedScheduledTransfer)) == 2
    advance_only(db,principal.sandbox_id,3)
    assert execute_due_schedules(db)==1 and execute_due_schedules(db)==1 and execute_due_schedules(db)==0
    assert sorted(db.scalars(select(Transfer.amount_minor))) == [10000,12000,12000]
    assert new.state == 'SUCCEEDED'


@pytest.mark.parametrize('change',['executes','cancelled'])
def test_state_change_between_preview_and_confirmation_rejects_replacement(db,change):
    old,principal=recurring(db);confirm(db,old,principal)
    new=amendment(db,old,principal,amount_minor=12000)
    if change=='executes':
        advance_only(db,principal.sandbox_id);execute_due_schedules(db)
    else:cancel_schedules_for_task(db,old.id)
    with pytest.raises(DomainError) as error:confirm(db,new,principal,key='stale')
    assert error.value.code=='SCHEDULE_REPLACEMENT_STALE'
    assert db.scalar(select(func.count()).select_from(ScopedScheduledTransfer))==1


def test_other_owner_cannot_read_or_replace_schedule(db):
    old,principal=recurring(db);confirm(db,old,principal)
    _,other=recurring(db)
    row=db.scalar(select(ScopedScheduledTransfer))
    with pytest.raises(DomainError) as error:replacement_snapshot(db,other,row.id)
    assert error.value.code=='SCHEDULE_NOT_FOUND'


def test_cancel_before_due_prevents_future_payment_and_balance_fallback(db):
    old,principal=recurring(db);confirm(db,old,principal)
    cancel_schedules_for_task(db,old.id)
    advance_only(db,principal.sandbox_id,10)
    assert execute_due_schedules(db)==0
    assert db.scalar(select(func.count()).select_from(Transfer))==0


def test_changing_monthly_amount_keeps_remaining_original_month_end_dates(db):
    old,principal=recurring(db,count=4,frequency='MONTHLY');confirm(db,old,principal)
    row=db.scalar(select(ScopedScheduledTransfer))
    old_dates=row.scope_json['recurrence']['dates']
    new=amendment(db,old,principal,amount_minor=13000)
    assert new.plan_json['nodes'][0]['recurrence']['dates']==old_dates
    assert new.plan_json['nodes'][0]['recurrence']['frequency']=='MONTHLY'
    assert new.plan_json['nodes'][0]['recurrence']['month_end_policy']=='CLAMP_ORIGINAL_DAY_NO_DRIFT'


def test_new_time_and_count_are_explicitly_bounded_and_keep_source(db):
    old,principal=recurring(db);confirm(db,old,principal)
    row=db.scalar(select(ScopedScheduledTransfer))
    new=amendment(db,old,principal,scheduled_at='后天',schedule_frequency='WEEKLY',schedule_occurrence_count=2)
    scope=new.plan_json['nodes'][0]
    assert scope['source_account_id']==row.source_account_id and scope['payee_id']==row.payee_id
    dates=scope['recurrence']['dates']
    from datetime import datetime
    assert len(dates)==2 and datetime.fromisoformat(dates[1])-datetime.fromisoformat(dates[0])==timedelta(days=7)
    assert scope['timing_policy']['time_origin']=='DEFAULT_09_00'


def test_result_distinguishes_external_debit_from_arrival(db):
    old,principal=recurring(db,count=1);confirm(db,old,principal)
    advance_only(db,principal.sandbox_id);execute_due_schedules(db)
    row=db.scalar(select(ScopedScheduledTransfer));series=db.scalar(select(ScheduledTransferSeries))
    projection=schedule_projection(db,row,series)
    assert projection['debit_status']=='POSTED' and projection['posted_occurrences']==1
    assert projection['settlement_status']=='UNVERIFIED'
    occurrence=projection['series']['occurrences'][0]
    assert occurrence['status']=='POSTED'
    from zhenxing.schedule_presentation import posting_facts
    assert posting_facts(db,occurrence['transfer_id'])['settlement_status']=='UNVERIFIED'
    assert timing_policy('明天')['time_origin']=='DEFAULT_09_00'
    assert timing_policy('明天10点')['time_origin']=='USER_TIME'
    assert timing_policy('明天10点')['arrival_time_guaranteed'] is False


def test_separate_month_day_fields_preserve_calendar_anchor_even_after_short_month():
    from zhenxing.schedule_calendar_fields import start_month,day_of_month,monthly_first
    from zhenxing.transfer_recurrence import occurrence_dates
    assert start_month('从2027年1月开始')=='2027-01' and day_of_month('每月31日')==31
    first=monthly_first('2027-02',31)
    assert [day.day for day in occurrence_dates(first,'MONTHLY',3,anchor_day=31)]==[28,31,30]
    for value in ['31或30日','0日','32日','大约三十日']:
        with pytest.raises(ValueError):day_of_month(value)


def test_relative_start_month_uses_original_message_clock_and_china_day_boundary():
    from datetime import datetime,timezone
    from zhenxing.schedule_calendar_fields import start_month
    clock=datetime(2026,12,31,16,0,tzinfo=timezone.utc)  # Jan 1 2027 in Shanghai
    assert start_month('下个月',clock)=='2027-02'
    assert start_month('明年1月',clock)=='2028-01'
    assert start_month('今年12月',clock)=='2027-12'
    with pytest.raises(ValueError):start_month('明年1月')
    with pytest.raises(ValueError):start_month('下个月或者明年1月',clock)


def test_monthly_anchor_uses_shanghai_date_not_previous_utc_date():
    from datetime import datetime,timezone
    from zhenxing.transfer_recurrence import occurrence_dates
    from zhenxing.banking_extensions import LOCAL_TZ
    first=datetime(2027,1,30,16,tzinfo=timezone.utc)  # Jan 31 00:00 Shanghai
    assert [d.astimezone(LOCAL_TZ).day for d in occurrence_dates(first,'MONTHLY',3)]==[31,28,31]


def test_conflicting_first_day_and_monthly_anchor_do_not_register_an_early_occurrence():
    from datetime import datetime,timezone
    from zhenxing.transfer_recurrence import recurrence_scope
    with pytest.raises(DomainError) as error:
        recurrence_scope({'schedule_frequency':'MONTHLY','schedule_occurrence_count':3,'schedule_day_of_month':31},
            datetime(2027,1,15,1,tzinfo=timezone.utc))
    assert error.value.code=='SCHEDULE_RECURRENCE_CONFLICT'


def test_only_one_of_two_prepared_replacements_can_commit(db):
    old,principal=recurring(db);confirm(db,old,principal)
    one=amendment(db,old,principal,amount_minor=12000)
    two=amendment(db,old,principal,amount_minor=13000)
    confirm(db,one,principal,key='replace-a')
    with pytest.raises(DomainError) as error:confirm(db,two,principal,key='replace-b')
    assert error.value.code=='SCHEDULE_REPLACEMENT_STALE'
    active=list(db.scalars(select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.status=='WAITING')))
    assert len(active)==1 and active[0].amount_minor==12000


def test_amount_only_then_new_start_month_preserve_31_day_anchor_and_time(db):
    from datetime import datetime,timezone
    from zhenxing.banking_extensions import LOCAL_TZ
    old,principal=recurring(db)
    # Reprepare an unconfirmed task with a real bounded month-end request.
    db.delete(db.scalar(select(Confirmation).where(Confirmation.task_id==old.id)))
    from zhenxing.models import TaskNode
    for node in db.scalars(select(TaskNode).where(TaskNode.task_id==old.id)):db.delete(node)
    old.state='NEED_CLARIFICATION';db.flush()
    prepare_extra_task(db,task=old,conversation_id=get_or_create_conversation(db,principal.sandbox_id,principal.user_id).id,
        proposed_action={'kind':'TRANSFER_SCHEDULE','slots':{'payee_name':'房东','amount_minor':10000,
        'scheduled_at':'2027-01-31T14:30:00+08:00','schedule_frequency':'MONTHLY','schedule_occurrence_count':4}})
    confirm(db,old,principal)
    one=amendment(db,old,principal,amount_minor=12000);confirm(db,one,principal,key='change-amount')
    first_scope=one.plan_json['nodes'][0]
    assert first_scope['recurrence']['frequency']=='MONTHLY' and first_scope['recurrence']['anchor_day']==31
    two=amendment(db,one,principal,schedule_start_month='2027-02')
    second_scope=two.plan_json['nodes'][0]
    dates=[datetime.fromisoformat(v).astimezone(LOCAL_TZ) for v in second_scope['recurrence']['dates']]
    assert [d.day for d in dates]==[28,31,30,31]
    assert {(d.hour,d.minute) for d in dates}=={(14,30)}
    assert second_scope['timing_policy']['time_origin']=='USER_TIME'
