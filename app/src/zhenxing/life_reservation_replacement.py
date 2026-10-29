"""One freshly confirmed budget change over an intact, unpaid life hold.

The private source seal is not a public DTO or new permission. Cash reservation
math and locking remain in the registered central payment service.
"""
from copy import deepcopy

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .planning import canonical_hash


SCOPE_VERSION = 'bounded-birthday-reserved-budget-v3'
VERSION = 'life-reservation-replacement-v1'
PUBLIC_VERSION = 'life-reservation-adjustment-v1'
PUBLIC_FIELDS = {'version', 'previous_reserved_minor', 'new_reserved_minor', 'additional_reserved_minor',
    'released_difference_minor', 'currency', 'schedule_preserved', 'synchronous_at_confirmation', 'cash_debit_minor'}
JOURNAL_KIND = 'LIFE_RESERVATION_ADJUSTMENT'
FIELDS = {'version', 'plan_id', 'plan_version', 'scope_hash', 'occurrence_id', 'mandate_id',
    'reservation_id', 'reservation_version', 'reserved_minor', 'currency', 'instrument_id', 'account_id',
    'reserve_at', 'order_at', 'login_account_id', 'login_version', 'actor_role'}
UNCHANGED = ('occasion_id', 'occasion_version', 'family_member_id', 'family_member_name', 'birthday', 'year',
    'timezone', 'account_id', 'currency', 'items', 'recipient', 'reserve_at', 'order_at', 'order_window_end',
    'schedule_policy', 'reserve_policy', 'delivery_start', 'delivery_end', 'expires_at', 'maximum_occurrences', 'recurring',
    'replacement_allowed', 'price_increase_allowed', 'cancellation_policy', 'failure_policy', 'unused_budget_policy')
INSTRUMENT = ('instrument_id', 'instrument_type', 'card_id', 'account_id', 'currency', 'sandbox_id',
    'user_id', 'card_version', 'binding_version', 'wallet_id', 'credit_account_id')


def _deny():
    raise DomainError('LIFE_PLAN_RESERVED_BUDGET_REPLACEMENT_CHANGED',
        '原计划的完整预留、未付款状态、身份或排期已变化；本次未替换，请重新核对。', 409)


def marker(scope):
    value = scope.get('reservation_replacement')
    if value is None and scope.get('schema_version') != SCOPE_VERSION:
        return None
    if (scope.get('schema_version') != SCOPE_VERSION or not isinstance(value, dict) or set(value) != FIELDS
            or value.get('version') != VERSION or value.get('currency') != 'CNY'
            or any(type(value.get(key)) is not int or value[key] < 1
                for key in ('plan_version', 'reservation_version', 'reserved_minor', 'login_version'))
            or any(not isinstance(value.get(key), str) or not value[key] for key in FIELDS - {
                'plan_version', 'reservation_version', 'reserved_minor', 'login_version'})):
        _deny()
    return value


def _login(session, principal, *, lock=False):
    from .accounts import require_account_role
    return require_account_role(session, principal, {'CUSTOMER', 'DEV_TEST'}, lock=lock)


def eligible_shape(old, scope):
    return (old.status == 'ACTIVE' and old.reservation_id is not None and scope['budget_minor'] != old.budget_minor
        and all(scope.get(key) == old.scope_json.get(key) for key in UNCHANGED)
        and all(scope['instrument'].get(key) == old.scope_json['instrument'].get(key) for key in INSTRUMENT))


def lock_actor_before_owner(session, principal, plan_id):
    """The registered login row precedes financial owner/account/card locks."""
    from .life_plan_models import LifePlan
    hint = session.scalar(select(LifePlan).where(LifePlan.id == plan_id,
        LifePlan.user_id == principal.user_id, LifePlan.sandbox_id == principal.sandbox_id))
    if hint is not None and marker(hint.scope_json) is not None:
        current = _login(session, principal, lock=True)
        sealed = marker(hint.scope_json)
        if (current.id, current.version, current.role) != (
                sealed['login_account_id'], sealed['login_version'], sealed['actor_role']):
            _deny()


def _source(session, principal, scope, now, *, lock):
    from .credit_models import PaymentCashReservation, PaymentRecord
    from .life_plan_models import LifePlanJob
    from .life_plan_service import replacement_source, execution_context, _aware
    old = replacement_source(session, principal, scope['replaces'], lock=lock)
    if (old.status != 'ACTIVE' or old.reservation_id is None or old.spent_minor != 0
            or old.reserved_minor != old.budget_minor or _aware(old.scope_json['order_at']) <= now):
        _deny()
    if any(scope.get(key) != old.scope_json.get(key) for key in UNCHANGED):
        _deny()
    if scope['budget_minor'] == old.budget_minor or any(scope['instrument'].get(key) != old.scope_json['instrument'].get(key)
            for key in INSTRUMENT):
        _deny()
    from .life_plan_models import LifePlanOccurrence
    occurrences = list(session.scalars(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id == old.id)))
    if len(occurrences) != 1:
        _deny()
    context = execution_context(session, principal, occurrences[0].id, now=now)
    occurrence, mandate = context['occurrence'], context['mandate']
    from .models import Confirmation
    from .runtime_models import AuthChallenge
    confirmation = session.get(Confirmation, mandate.confirmation_id)
    challenge = session.get(AuthChallenge, mandate.auth_challenge_id)
    authorized_at = as_utc(mandate.authorized_at)
    # Historical consumed nonces need to have been valid THEN, not to remain
    # unexpired now. The durable mandate/hold and this new CF are separate.
    if (confirmation is None or challenge is None or confirmation.status != 'CONSUMED'
            or authorized_at >= as_utc(confirmation.expires_at) or authorized_at >= as_utc(challenge.expires_at)
            or as_utc(challenge.verified_at) > authorized_at or as_utc(challenge.consumed_at) != authorized_at):
        _deny()
    hold = session.scalar(select(PaymentCashReservation).where(PaymentCashReservation.id == old.reservation_id)
        .with_for_update().execution_options(populate_existing=True) if lock else
        select(PaymentCashReservation).where(PaymentCashReservation.id == old.reservation_id)
        .execution_options(populate_existing=True))
    if (hold is None or hold.status != 'ACTIVE' or hold.amount_minor != old.budget_minor
            or hold.remaining_minor != old.budget_minor or hold.expires_at is None or as_utc(hold.expires_at) <= now
            or session.scalar(select(PaymentRecord.id).where((PaymentRecord.reservation_id == hold.id)
                | (PaymentRecord.authorization_ref == mandate.id)))):
        _deny()
    jobs = list(session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id)))
    reserves = [job for job in jobs if job.kind == 'RESERVE']
    orders = [job for job in jobs if job.kind == 'ORDER']
    if (any(job.kind not in {'RESERVE', 'ORDER', 'EXPIRE'} or job.plan_version != old.version
                or job.status not in {'SUCCEEDED', 'QUEUED'} for job in jobs)
            or len(orders) != len(old.scope_json['items'])
            or {job.item_index for job in orders} != set(range(len(orders)))
            or any(job.status != 'QUEUED' or as_utc(job.next_run_at) != as_utc(occurrence.order_at) for job in orders)
            or (old.scope_json.get('schema_version') != SCOPE_VERSION and
                (len(reserves) != 1 or reserves[0].status != 'SUCCEEDED'))
            or (old.scope_json.get('schema_version') == SCOPE_VERSION and reserves)):
        _deny()
    login = _login(session, principal)
    return old, occurrence, mandate, hold, login


def _seal(old, occurrence, mandate, hold, login):
    return {'version': VERSION, 'plan_id': old.id, 'plan_version': old.version, 'scope_hash': old.scope_hash,
        'occurrence_id': occurrence.id, 'mandate_id': mandate.id, 'reservation_id': hold.id,
        'reservation_version': hold.version, 'reserved_minor': hold.remaining_minor, 'currency': 'CNY',
        'instrument_id': hold.instrument_id, 'account_id': hold.account_id, 'reserve_at': old.scope_json['reserve_at'],
        'order_at': old.scope_json['order_at'], 'login_account_id': login.id, 'login_version': login.version,
        'actor_role': login.role}


def prepare_marker(session, principal, scope, now):
    """Called only for a replacement retaining an already elapsed reserve time."""
    old, occurrence, mandate, hold, login = _source(session, principal, scope, now, lock=False)
    return _seal(old, occurrence, mandate, hold, login)


def validate_source(session, principal, scope, now, *, lock=False):
    sealed = marker(scope)
    if sealed is None:
        return None
    values = _source(session, principal, scope, now, lock=lock)
    if _seal(*values) != sealed:
        _deny()
    return values


def public_adjustment(scope):
    sealed = marker(scope)
    if sealed is None:
        return None
    before, after = sealed['reserved_minor'], scope['budget_minor']
    return {'version': PUBLIC_VERSION, 'previous_reserved_minor': before, 'new_reserved_minor': after,
        'additional_reserved_minor': max(after - before, 0), 'released_difference_minor': max(before - after, 0),
        'currency': 'CNY', 'schedule_preserved': True, 'synchronous_at_confirmation': True, 'cash_debit_minor': 0}


def public_adjustment_valid(value):
    return (isinstance(value, dict) and set(value) == PUBLIC_FIELDS and value.get('version') == PUBLIC_VERSION
        and all(type(value.get(key)) is int and 0 <= value[key] <= 100000000 for key in
            ('previous_reserved_minor', 'new_reserved_minor', 'additional_reserved_minor', 'released_difference_minor', 'cash_debit_minor'))
        and value['previous_reserved_minor'] > 0 and value['new_reserved_minor'] > 0
        and value['previous_reserved_minor'] != value['new_reserved_minor']
        and value['additional_reserved_minor'] == max(value['new_reserved_minor'] - value['previous_reserved_minor'], 0)
        and value['released_difference_minor'] == max(value['previous_reserved_minor'] - value['new_reserved_minor'], 0)
        and value['cash_debit_minor'] == 0 and value.get('currency') == 'CNY'
        and value.get('schedule_preserved') is True and value.get('synchronous_at_confirmation') is True)


def journal_key(plan):
    return f'life:{plan.id}:v{plan.version}:reservation-adjustment'


def record_adjustment(session, principal, plan, occurrence, mandate):
    from .payment_instruments import _journal
    sealed = marker(plan.scope_json)
    private = {'version': VERSION, 'source': deepcopy(sealed), 'plan_id': plan.id, 'plan_version': plan.version,
        'scope_hash': plan.scope_hash, 'occurrence_id': occurrence.id, 'mandate_id': mandate.id,
        'reservation_id': plan.reservation_id, 'adjustment': public_adjustment(plan.scope_json)}
    journal = _journal(session, principal, operation_key=journal_key(plan), request_hash=canonical_hash(private),
        kind=JOURNAL_KIND, entries=[])
    journal.result_json = private
    session.flush()
    return deepcopy(private['adjustment'])


def receipt_integrity(session, principal, plan, mandate, result):
    from .credit_models import PaymentCashReservation, PaymentJournal, PaymentLedgerEntry
    from .life_plan_models import LifePlanOccurrence
    sealed = marker(plan.scope_json)
    if sealed is None:
        return 'reservation_adjustment' not in result
    rows = list(session.scalars(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id == plan.id)))
    if len(rows) != 1:
        return False
    occurrence = rows[0]
    journal = session.scalar(select(PaymentJournal).where(PaymentJournal.operation_key == journal_key(plan),
        PaymentJournal.user_id == principal.user_id, PaymentJournal.sandbox_id == principal.sandbox_id,
        PaymentJournal.kind == JOURNAL_KIND))
    expected = {'version': VERSION, 'source': deepcopy(sealed), 'plan_id': plan.id, 'plan_version': plan.version,
        'scope_hash': plan.scope_hash, 'occurrence_id': occurrence.id, 'mandate_id': mandate.id,
        'reservation_id': plan.reservation_id, 'adjustment': public_adjustment(plan.scope_json)}
    old = session.get(PaymentCashReservation, sealed['reservation_id'])
    new = session.get(PaymentCashReservation, plan.reservation_id)
    return bool(journal and journal.result_json == expected and journal.request_hash == canonical_hash(expected)
        and result.get('reservation_adjustment') == expected['adjustment']
        and not session.scalar(select(PaymentLedgerEntry.id).where(PaymentLedgerEntry.journal_id == journal.id))
        and old and old.status == 'RELEASED' and old.remaining_minor == 0 and old.version == sealed['reservation_version'] + 1
        and (old.user_id, old.sandbox_id) == (principal.user_id, principal.sandbox_id)
        and old.amount_minor == sealed['reserved_minor'] and old.instrument_id == sealed['instrument_id']
        and old.account_id == sealed['account_id'] and old.owner_ref == f"life:{sealed['occurrence_id']}"
        and old.reservation_key == f"life:{sealed['occurrence_id']}:v{sealed['plan_version']}:reserve"
        and new and new.id != old.id and new.amount_minor == plan.budget_minor
        and new.account_id == plan.account_id and new.instrument_id == plan.scope_json['instrument']['instrument_id']
        and (new.user_id, new.sandbox_id) == (principal.user_id, principal.sandbox_id)
        and new.status in {'ACTIVE', 'CONSUMED', 'RELEASED'} and new.remaining_minor == plan.reserved_minor
        and new.expires_at is not None and as_utc(new.expires_at) == as_utc(mandate.expires_at)
        and new.owner_ref == f'life:{occurrence.id}' and new.reservation_key == f'life:{occurrence.id}:v{plan.version}:reserve')
