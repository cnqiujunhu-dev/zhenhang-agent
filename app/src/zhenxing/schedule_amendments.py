"""Replace only unexecuted schedule occurrences after a new confirmation.

The previous signed plan and posted occurrences remain immutable. Preparing a
replacement never suspends the old mandate; the execution transaction locks and
rechecks the old revision, cancels its pending occurrences and registers the new
mandate atomically. A concurrent occurrence invalidates the prepared revision.
"""
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select

from .authentication import as_utc
from .banking_extension_models import ScopedScheduledTransfer, ScheduledTransferSeries, ScheduledTransferOccurrence
from .contracts import DomainError
from .models import Confirmation, Task
from .planning import canonical_hash

VERSION = 'schedule-replacement-v1'


def _deny():
    raise DomainError('SCHEDULE_REPLACEMENT_STALE', '原预约已执行、取消或发生变化，请重新查询后修改；本次未替换。', 409)


def replacement_snapshot(session, principal, schedule_id, *, lock=False):
    series_query = select(ScheduledTransferSeries).where(ScheduledTransferSeries.schedule_id == schedule_id,
        ScheduledTransferSeries.user_id == principal.user_id, ScheduledTransferSeries.sandbox_id == principal.sandbox_id)
    if lock: series_query = series_query.with_for_update().execution_options(populate_existing=True)
    series = session.scalar(series_query)
    row_query = select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.id == schedule_id,
        ScopedScheduledTransfer.user_id == principal.user_id, ScopedScheduledTransfer.sandbox_id == principal.sandbox_id)
    if lock: row_query = row_query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(row_query)
    if row is None: raise DomainError('SCHEDULE_NOT_FOUND', '未找到本人的转账安排，请先查询再选择。', 404)
    task_query = select(Task).where(Task.id == row.task_id)
    if lock: task_query = task_query.with_for_update().execution_options(populate_existing=True)
    task = session.scalar(task_query)
    confirmation = session.get(Confirmation, row.confirmation_id)
    if (row.status != 'WAITING' or task is None or task.state != 'WAITING'
            or task.plan_hash != row.plan_hash or canonical_hash(task.plan_json) != row.plan_hash
            or confirmation is None or confirmation.status != 'CONSUMED'
            or confirmation.plan_hash != row.plan_hash
            or (row.source_account_id,row.payee_id,row.amount_minor,row.memo)!=(row.scope_json.get('source_account_id'),
                row.scope_json.get('payee_id'),row.scope_json.get('amount_minor'),row.scope_json.get('memo'))
            or as_utc(row.due_at).isoformat()!=as_utc(datetime.fromisoformat(row.scope_json['due_at'])).isoformat()
            or confirmation.view_hash != canonical_hash({'plan_hash': row.plan_hash, 'scope': row.scope_json})):
        _deny()
    if series:
        if series.status != 'ACTIVE' or series.scope_json != row.scope_json: _deny()
        occurrences = list(session.scalars(select(ScheduledTransferOccurrence).where(
            ScheduledTransferOccurrence.series_id == series.id).order_by(ScheduledTransferOccurrence.ordinal)))
        pending = [o for o in occurrences if o.status == 'PENDING']
        if not pending or any(o.status not in {'POSTED', 'PENDING'} for o in occurrences): _deny()
        authorized=series.scope_json['recurrence']['dates']
        if len(occurrences)!=len(authorized) or any(o.ordinal!=index or o.business_key!=f'schedule:{task.id}:occurrence:{index}'
                or as_utc(o.due_at)!=as_utc(datetime.fromisoformat(authorized[index-1])) for index,o in enumerate(occurrences,1)):
            _deny()
    else:
        pending = []
    revision = {'schema_version': VERSION, 'schedule_id': row.id, 'task_id': task.id,
        'plan_hash': row.plan_hash, 'task_version': task.version,
        'series_version': series.version if series else None,
        'pending': [{'ordinal': o.ordinal, 'due_at': as_utc(o.due_at).isoformat(), 'business_key': o.business_key} for o in pending],
        'remaining_dates': [as_utc(o.due_at).isoformat() for o in pending] if series else [as_utc(row.due_at).isoformat()]}
    revision['revision_hash'] = canonical_hash(revision)
    return row, revision


def validate_replacement(session, principal, expected, *, lock=False):
    if not isinstance(expected, dict) or expected.get('schema_version') != VERSION: _deny()
    row, actual = replacement_snapshot(session, principal, expected.get('schedule_id'), lock=lock)
    if actual != expected: _deny()
    return row


def prepare_amendment(session, *, task, principal, conversation_id, values, source_context=None):
    from .banking_extensions import _plan, _schedule_clock, _validate_schedule_recipient_context, TRANSFER_LIMIT_MINOR, SCHEDULE_TOOL
    from .temporal_fields import temporal_field_clock
    from .transfer_authorization_dates import scheduled_instant
    from .transfer_recurrence import recurrence_scope
    old, revision = replacement_snapshot(session, principal, values['schedule_reference'])
    scope = deepcopy(old.scope_json)
    scope.pop('replaces_schedule', None)
    # Changing parties or payment instruments is a new payment mandate, not a
    # partial edit which may accidentally inherit an unrelated counterpart.
    immutable = {'payee_name', 'recipient_card_reference', 'recipient_alias', 'replace_alias', 'card_number', 'card_last4'}
    if any(values.get(key) is not None for key in immutable):
        raise DomainError('SCHEDULE_REPLACEMENT_PARTIES_FIXED', '修改预约保留原收付款账户；如需更换账户，请取消原安排后重新创建。', 422)
    if values.get('currency') not in {None, 'CNY'}:
        raise DomainError('SCHEDULE_CURRENCY_INVALID', '当前预约使用人民币，未更换币种。', 422)
    _validate_schedule_recipient_context(session, task.sandbox_id, task.user_id, scope)
    _, clock = _schedule_clock(session, task, basis=scope.get('clock_basis', 'BANK_CONTROLLED'))
    dates = [datetime.fromisoformat(value) for value in revision['remaining_dates']]
    if any(as_utc(value) <= as_utc(clock) for value in dates): _deny()
    if not any(values.get(key) is not None for key in {'amount_minor','memo','scheduled_at','schedule_frequency',
            'schedule_occurrence_count','schedule_end_at','schedule_occurrence_times','schedule_start_month','schedule_day_of_month'}):
        raise DomainError('SCHEDULE_CHANGE_REQUIRED', '这份预约需要修改金额、日期、频次还是备注？', 422)
    amount = values.get('amount_minor') or old.amount_minor
    if type(amount) is not int or not 0 < amount <= TRANSFER_LIMIT_MINOR:
        raise DomainError('TRANSFER_LIMIT_CHANGED', '修改金额超出当前转账限额。', 422)
    scope.update(amount_minor=amount, memo=values.get('memo') if values.get('memo') is not None else old.memo)
    if values.get('scheduled_at'):
        field_clock = temporal_field_clock(session, task, source_context, 'scheduled_at', values['scheduled_at'])
        first = scheduled_instant(values['scheduled_at'], field_clock or clock)
        if first is None or as_utc(first) <= as_utc(clock):
            raise DomainError('SCHEDULE_DATE_REQUIRED', '请明确新的未来执行日期。', 422)
    else: first = dates[0]
    prior = scope.get('recurrence') or {}
    if values.get('schedule_start_month') is not None or values.get('schedule_day_of_month') is not None:
        from .schedule_calendar_fields import monthly_first
        from .banking_extensions import LOCAL_TZ
        month=values.get('schedule_start_month') or first.astimezone(LOCAL_TZ).strftime('%Y-%m')
        day=values.get('schedule_day_of_month') or prior.get('anchor_day') or datetime.fromisoformat(scope['due_at']).astimezone(LOCAL_TZ).day
        selected_first=monthly_first(month,day)
        if values.get('scheduled_at') and first.astimezone(LOCAL_TZ).date()!=selected_first.date():
            raise DomainError('SCHEDULE_RECURRENCE_CONFLICT','首次日期和月度安排不一致，请核对。',422)
        if not values.get('scheduled_at'):
            local_time=first.astimezone(LOCAL_TZ)
            first=selected_first.replace(hour=local_time.hour,minute=local_time.minute,
                second=local_time.second,microsecond=local_time.microsecond)
        if as_utc(first)<=as_utc(clock):raise DomainError('SCHEDULE_DATE_PAST','修改日期必须晚于当前时间。',422)
    timing_fields = {'scheduled_at','schedule_frequency','schedule_occurrence_count','schedule_end_at','schedule_occurrence_times','schedule_start_month','schedule_day_of_month'}
    if any(values.get(key) is not None for key in timing_fields):
        frequency = values.get('schedule_frequency') or ('EXPLICIT' if values.get('schedule_occurrence_times') else prior.get('frequency','ONCE'))
        selected = {key: values[key] for key in timing_fields if values.get(key) is not None}
        selected['schedule_frequency'] = frequency
        if frequency=='MONTHLY' and values.get('schedule_day_of_month') is None and prior.get('anchor_day') is not None:
            selected['schedule_day_of_month']=prior['anchor_day']
        if frequency != 'ONCE' and not selected.get('schedule_end_at') and not selected.get('schedule_occurrence_count') and not selected.get('schedule_occurrence_times'):
            selected['schedule_occurrence_count'] = len(dates)
        end_clock = temporal_field_clock(session, task, source_context, 'schedule_end_at', values['schedule_end_at']) if values.get('schedule_end_at') else clock
        recurrence = recurrence_scope(selected, first, clock=end_clock or clock)
    elif prior:
        recurrence=deepcopy(prior)
        recurrence.update(occurrence_count=len(dates),dates=[as_utc(value).isoformat() for value in dates])
        if recurrence.get('frequency')=='MONTHLY' and recurrence.get('anchor_day') is None:
            from .banking_extensions import LOCAL_TZ
            recurrence['anchor_day']=datetime.fromisoformat(scope['due_at']).astimezone(LOCAL_TZ).day
    else: recurrence = None
    scope['due_at'] = as_utc(first).isoformat()
    if values.get('scheduled_at'):
        from .schedule_presentation import timing_policy
        scope['timing_policy'] = timing_policy(values['scheduled_at'])
    if recurrence: scope['recurrence'] = recurrence
    else: scope.pop('recurrence', None)
    payment = scope.get('transfer_payment_scope')
    if payment:
        from .transfer_payment_scope import validate_transfer_payment_scope
        payment.update(amount_minor=amount)
        payment['display']['amount_minor'] = amount
        payment['scope_hash'] = canonical_hash({key: value for key, value in payment.items() if key != 'scope_hash'})
        validate_transfer_payment_scope(session, principal, payment, scheduled=True, require_funds=False, lock=False)
    scope['replaces_schedule'] = revision
    return _plan(session, task, conversation_id, SCHEDULE_TOOL, 'schedule-transfer', scope, old.payee_id,
        '已准备修改后的预约，请核对金额和剩余日期。确认后替换原安排的未执行期次，已完成的转账保持不变。')


def complete_replacement(session, principal, scope):
    expected = scope.get('replaces_schedule')
    if expected is None: return
    old = validate_replacement(session, principal, expected, lock=True)
    from .banking_extensions import cancel_schedules_for_task
    from .audit import append_audit
    if cancel_schedules_for_task(session, old.task_id) != 1: _deny()
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=old.task_id,
        event_type='SCHEDULE_FUTURE_REPLACED', payload={'prior_schedule_id':old.id, 'prior_revision':expected['revision_hash']})
