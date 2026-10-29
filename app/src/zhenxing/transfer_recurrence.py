"""Bounded, explicit recurring authorizations; every occurrence is single-shot."""
import calendar
import re
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select

from .authentication import as_utc
from .banking_extension_models import ScheduledTransferOccurrence, ScheduledTransferSeries, ScopedScheduledTransfer
from .contracts import DomainError
from .models import Confirmation, Task, utcnow
from .planning import canonical_hash
from .simulation_models import SimulationState


def occurrence_dates(first, frequency, count, end=None, *, anchor_day=None):
    if not isinstance(first,datetime) or first.tzinfo is None:
        raise DomainError('SCHEDULE_RECURRENCE_INVALID','预约日期需要明确时区。',422)
    if frequency not in {"ONCE", "DAILY", "WEEKLY", "MONTHLY"} or type(count) is not int or not 1 <= count <= 366:
        raise DomainError("SCHEDULE_RECURRENCE_INVALID", "请选择单次、每天、每周或每月，并明确不超过366次的范围。", 422)
    if anchor_day is not None and (frequency!='MONTHLY' or type(anchor_day)is not int or not 1<=anchor_day<=31):
        raise DomainError('SCHEDULE_MONTH_DAY_INVALID','月度执行日须为1至31日。',422)
    dates = []
    for index in range(1 if frequency == "ONCE" else count):
        if frequency == "MONTHLY":
            from .banking_extensions import LOCAL_TZ
            local_first=as_utc(first).astimezone(LOCAL_TZ)
            month_index = local_first.year * 12 + local_first.month - 1 + index
            year, month = divmod(month_index, 12)
            if not 1<=year<=9999:
                raise DomainError('SCHEDULE_RECURRENCE_INVALID','预约日期超出可处理范围。',422)
            due = local_first.replace(year=year, month=month + 1,
                day=min(anchor_day or local_first.day, calendar.monthrange(year, month + 1)[1])).astimezone(first.tzinfo)
        else:
            due = first + timedelta(days=index * (7 if frequency == "WEEKLY" else 1))
        if end is not None and as_utc(due) > as_utc(end):
            break
        dates.append(due)
    if not dates:
        raise DomainError("SCHEDULE_RECURRENCE_EMPTY", "结束时间早于首次执行时间。", 422)
    return dates


def explicit_occurrence_dates(raw,first=None,*,allow_single=False):
    """Closed datetime-list field, never a natural-language action classifier."""
    if not isinstance(raw,str) or not raw.strip() or len(raw)>1000:
        raise DomainError('SCHEDULE_EXPLICIT_TIMES_REQUIRED','请逐一给出2至12个带时区的完整执行时点。',422)
    parts=[part.strip() for part in re.split(r'[,，、;；\n]',raw)]
    if not (1 if allow_single else 2)<=len(parts)<=12 or any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})',p) for p in parts):
        raise DomainError('SCHEDULE_EXPLICIT_TIMES_REQUIRED','请逐一给出2至12个带时区的完整执行时点。',422)
    try:dates=[as_utc(datetime.fromisoformat(part)) for part in parts]
    except ValueError:raise DomainError('SCHEDULE_EXPLICIT_TIMES_INVALID','指定执行日期无效，未登记预约。',422) from None
    if any(right<=left for left,right in zip(dates,dates[1:])) or dates[-1]-dates[0]>timedelta(days=366):
        raise DomainError('SCHEDULE_EXPLICIT_TIMES_INVALID','指定时点须按先后排序、不重复，且跨度不超过366天。',422)
    if first is not None and dates[0]!=as_utc(first):
        raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','首次预约日期与完整时点清单不一致，未登记预约。',422)
    return dates


def recurrence_scope(values, first, *, clock=None):
    frequency = values.get("schedule_frequency") or ('EXPLICIT' if values.get('schedule_occurrence_times') else "ONCE")
    if values.get('schedule_frequency') is None and values.get('schedule_occurrence_times'):
        selected=explicit_occurrence_dates(values['schedule_occurrence_times'],first,
            allow_single=values.get('schedule_occurrence_count') in {None,1})
        if len(selected)==1:frequency='ONCE'
    if frequency=='ONCE' and values.get('schedule_occurrence_times'):
        # A model-selected single execution can use the closed datetime-list
        # field with one exact source instant. It is still one authorization,
        # never a truncated multi-date or unbounded recurring plan.
        dates=explicit_occurrence_dates(values['schedule_occurrence_times'],first,allow_single=True)
        if len(dates)!=1 or values.get('schedule_occurrence_count') not in {None,1} or values.get('schedule_end_at') is not None:
            raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','单次预约不能包含多个时点或周期结束范围。',422)
        return None
    if frequency=='EXPLICIT':
        dates=explicit_occurrence_dates(values.get('schedule_occurrence_times'),first)
        if values.get('schedule_occurrence_count') not in {None,len(dates)} or values.get('schedule_end_at') is not None:
            raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','指定时点清单与次数或结束范围冲突。',422)
        return {'version':'bounded-transfer-recurrence-v1','frequency':'EXPLICIT','occurrence_count':len(dates),
            'dates':[value.isoformat() for value in dates],'failure_policy':'STOP_REMAINING','month_end_policy':'EXPLICIT_USER_TIMES'}
    if values.get('schedule_occurrence_times'):
        raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','具体时点清单不能混合其它周期频率。',422)
    if frequency == "ONCE":
        if values.get("schedule_occurrence_count") not in {None, 1} or values.get("schedule_end_at") is not None:
            raise DomainError("SCHEDULE_RECURRENCE_CONFLICT", "单次预约不能同时带多次或周期结束范围。", 422)
        return None
    count = values.get("schedule_occurrence_count")
    end = values.get("schedule_end_at")
    if end is not None:
        from .schedule_calendar_fields import schedule_deadline
        end = schedule_deadline(end, first if clock is None else clock)
        if end is None:
            raise DomainError("SCHEDULE_END_REQUIRED", "请明确唯一的周期结束日期；未识别的范围不会登记扣款。", 422)
    if count is None and end is None:
        raise DomainError("SCHEDULE_BOUNDED_RANGE_REQUIRED", "这个预约安排到什么时候结束，或一共执行几次？", 422)
    anchor_day=values.get('schedule_day_of_month')
    dates = occurrence_dates(first, frequency, 366 if count is None else count, end,anchor_day=anchor_day)
    if dates[0]!=first:
        raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','首次日期与每月执行日不一致，请核对新的每月执行日。',422)
    if count is None and len(dates) == 366 and dates[-1] < end:
        raise DomainError("SCHEDULE_RECURRENCE_TOO_LONG", "一个周期授权最多366次，请缩短时间范围。", 422)
    return {"version": "bounded-transfer-recurrence-v1", "frequency": frequency,
            "occurrence_count": len(dates), "dates": [date.isoformat() for date in dates],
            "failure_policy": "STOP_REMAINING", "month_end_policy": "CLAMP_ORIGINAL_DAY_NO_DRIFT",
            **({'anchor_day':anchor_day} if anchor_day is not None else {})}


def register_series(session, schedule):
    recurrence = schedule.scope_json.get("recurrence")
    if recurrence is None:
        return None
    series = ScheduledTransferSeries(sandbox_id=schedule.sandbox_id, user_id=schedule.user_id,
        task_id=schedule.task_id, schedule_id=schedule.id, scope_json=deepcopy(schedule.scope_json), next_due_at=schedule.due_at)
    session.add(series)
    session.flush()
    for ordinal, due in enumerate(recurrence["dates"], 1):
        session.add(ScheduledTransferOccurrence(series_id=series.id, ordinal=ordinal,
            due_at=datetime.fromisoformat(due), business_key=f"schedule:{schedule.task_id}:occurrence:{ordinal}"))
    session.flush()
    return series


def series_view(session, series):
    from .schedule_presentation import posting_facts
    occurrences = list(session.scalars(select(ScheduledTransferOccurrence).where(
        ScheduledTransferOccurrence.series_id == series.id).order_by(ScheduledTransferOccurrence.ordinal)))
    return {"series_id": series.id, "status": series.status, "version": series.version,
        "frequency": series.scope_json["recurrence"]["frequency"],
        "failure_policy": "STOP_REMAINING", "month_end_policy":series.scope_json['recurrence'].get('month_end_policy'),
        "total_occurrences":len(occurrences), "remaining_occurrences":sum(row.status=='PENDING' for row in occurrences),
        "end_at":as_utc(occurrences[-1].due_at).isoformat() if occurrences else None,
        "occurrences": [{"ordinal": row.ordinal,
            "due_at": as_utc(row.due_at).isoformat(), "status": row.status,
            "transfer_id": row.transfer_id, "error": row.error_code,
            **posting_facts(session,row.transfer_id)} for row in occurrences]}


def cancel_series(session, task_id):
    series = session.scalar(select(ScheduledTransferSeries).where(ScheduledTransferSeries.task_id == task_id)
                            .with_for_update().execution_options(populate_existing=True))
    if series is None:
        return None
    if series.status == "ACTIVE":
        series.status, series.next_due_at, series.version = "CANCELLED", None, series.version + 1
        for occurrence in session.scalars(select(ScheduledTransferOccurrence).where(
                ScheduledTransferOccurrence.series_id == series.id, ScheduledTransferOccurrence.status == "PENDING")
                .with_for_update()):
            occurrence.status, occurrence.completed_at = "CANCELLED", utcnow()
    return series


def execute_due_series(session, *, calendar_time=None):
    from .reporting_clock import calendar_now
    current = as_utc(calendar_time) if calendar_time is not None else calendar_now()
    basis = ScheduledTransferSeries.scope_json["clock_basis"].as_string()
    series = session.scalar(select(ScheduledTransferSeries).join(SimulationState,
        SimulationState.sandbox_id == ScheduledTransferSeries.sandbox_id).where(ScheduledTransferSeries.status == "ACTIVE",
        or_(and_(basis == "CALENDAR_REAL", ScheduledTransferSeries.next_due_at <= current),
            and_(basis == "BANK_CONTROLLED", ScheduledTransferSeries.next_due_at <= SimulationState.business_time),
            and_(basis.is_not(None), basis.not_in({"CALENDAR_REAL", "BANK_CONTROLLED"}))))
        .order_by(ScheduledTransferSeries.next_due_at, ScheduledTransferSeries.id).limit(1).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True))
    if series is None:
        return 0
    occurrence = session.scalar(select(ScheduledTransferOccurrence).where(ScheduledTransferOccurrence.series_id == series.id,
        ScheduledTransferOccurrence.status == "PENDING").order_by(ScheduledTransferOccurrence.ordinal).limit(1).with_for_update())
    schedule = session.scalar(select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.id == series.schedule_id)
        .with_for_update().execution_options(populate_existing=True))
    task = session.scalar(select(Task).where(Task.id == series.task_id).with_for_update().execution_options(populate_existing=True))
    if occurrence is None or task is None or schedule is None or task.state != "WAITING":
        raise DomainError("SCHEDULE_SERIES_SOURCE_CHANGED", "周期预约来源状态已变化。", 409)
    try:
        scope = series.scope_json
        confirmation = session.get(Confirmation, schedule.confirmation_id)
        if (scope != schedule.scope_json or task.plan_hash != schedule.plan_hash or canonical_hash(task.plan_json) != schedule.plan_hash
                or confirmation is None or confirmation.status != "CONSUMED" or confirmation.plan_hash != schedule.plan_hash
                or confirmation.view_hash != canonical_hash({"plan_hash": schedule.plan_hash, "scope": scope})
                or scope.get("clock_basis") not in {"CALENDAR_REAL", "BANK_CONTROLLED"}
                or (schedule.source_account_id, schedule.payee_id, schedule.amount_minor, schedule.memo) != (
                    scope.get("source_account_id"), scope.get("payee_id"), scope.get("amount_minor"), scope.get("memo"))
                or as_utc(schedule.due_at) != as_utc(datetime.fromisoformat(scope["due_at"]))
                or series.next_due_at is None or as_utc(series.next_due_at)!=as_utc(occurrence.due_at)
                or not 1 <= occurrence.ordinal <= len(scope["recurrence"]["dates"])
                or occurrence.business_key != f"schedule:{task.id}:occurrence:{occurrence.ordinal}"
                or as_utc(datetime.fromisoformat(scope["recurrence"]["dates"][occurrence.ordinal - 1])) != as_utc(occurrence.due_at)):
            raise DomainError("SCHEDULE_SCOPE_CHANGED", "周期发生实例不在原授权范围中。", 409)
        from .banking_extensions import _validate_schedule_recipient_context
        _validate_schedule_recipient_context(session, series.sandbox_id, series.user_id, scope)
        from .models import Payee
        from .internal_banking import lock_transfer_accounts, recipient_scope, commit_transfer_money
        payee = session.get(Payee, schedule.payee_id)
        if payee is None:
            raise DomainError("TRANSFER_TARGET_CHANGED", "周期收款对象已不可用。", 409)
        source = lock_transfer_accounts(session, series.sandbox_id, series.user_id, schedule.source_account_id, payee)
        if scope.get("transfer_payment_scope"):
            from .services import Principal
            from .transfer_payment_scope import validate_transfer_payment_scope
            validate_transfer_payment_scope(session, Principal(series.user_id, series.sandbox_id),
                scope["transfer_payment_scope"], scheduled=True)
        from .extensions import evaluate_transfer_risk
        if evaluate_transfer_risk(session, task=task, context={"payee_id": payee.id, "for_commit": True})["decision"] != "ALLOW":
            raise DomainError("FRAUD_TRANSFER_BLOCKED", "当前风险核验未通过。", 403)
        destination = {key: scope.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")}
        if recipient_scope(session, payee) != destination:
            raise DomainError("RECIPIENT_SCOPE_CHANGED", "收款账户发生变化。", 409)
        payment_scope = scope.get("transfer_payment_scope")
        transfer = commit_transfer_money(session, source, payee, scope["amount_minor"], scope["memo"], occurrence.business_key, destination,
            source_card_id=(payment_scope["source"].get("card_id") if payment_scope else None),
            recipient_card_id=((payment_scope.get("recipient") or {}).get("card_id") if payment_scope else None))
        occurrence.status, occurrence.transfer_id = "POSTED", transfer.id
    except DomainError as error:
        occurrence.status, occurrence.error_code = "FAILED", error.code
        series.status, series.next_due_at = "FAILED", None
        for pending in session.scalars(select(ScheduledTransferOccurrence).where(
                ScheduledTransferOccurrence.series_id == series.id, ScheduledTransferOccurrence.status == "PENDING")):
            if pending.id != occurrence.id:
                pending.status, pending.error_code = "CANCELLED", "SERIES_STOPPED_AFTER_FAILURE"
    occurrence.completed_at = utcnow()
    session.flush()
    if series.status == "ACTIVE":
        following = session.scalar(select(ScheduledTransferOccurrence).where(ScheduledTransferOccurrence.series_id == series.id,
            ScheduledTransferOccurrence.status == "PENDING").order_by(ScheduledTransferOccurrence.ordinal).limit(1))
        series.next_due_at = following.due_at if following else None
        if following is None:
            series.status = "COMPLETED"
    series.version += 1
    result = series_view(session, series)
    internal = series.scope_json.get('transfer_channel') == 'INTERNAL_BANK'
    result.update(message=("周期转账已完成。" if internal else "本计划的款项已转出，到账结果待核实。") if series.status == "COMPLETED" else
        "本期转账未成功，后续安排已停止；已完成的转账不撤回。" if series.status == "FAILED" else
        ("本期转账已完成，等待下一期。" if internal else "本期已转出，到账结果待核实；下一期仍按原安排执行。"),
        amount_minor=schedule.amount_minor, simulated=True)
    task.result_json = result
    if series.status in {"FAILED", "COMPLETED"}:
        from .models import TaskNode
        from .workflow import finish_node, transition_task
        node = session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == "schedule-transfer"))
        if series.status == "COMPLETED":
            finish_node(session, task, node, result)
        else:
            node.status, node.result_json = "FAILED", result
            transition_task(task, "FAILED")
        schedule.status, schedule.completed_at = series.status, utcnow()
    from .audit import append_audit
    append_audit(session, sandbox_id=series.sandbox_id, task_id=task.id, event_type="SCHEDULE_OCCURRENCE_FINISHED",
        payload={"series_id": series.id, "ordinal": occurrence.ordinal, "status": occurrence.status,
                 "transfer_id": occurrence.transfer_id, "error": occurrence.error_code})
    session.flush()
    return 1
