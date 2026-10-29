"""Bounded, transaction-owned maintenance of the existing credit book.

Calendar periods use Asia/Shanghai, with exclusive month-end boundaries. The
registered due-day interval is frozen when a currency cursor is initialized.
Billing changes obligation labels, never cash, principal or historical postings.
Delayed closes use remaining registered debt at close, not reconstructed debt
at a historical date. Existing statement snapshots are never rewritten.

Login suspension retains the existing domain authorization boundary: failures
are recorded with backoff, without impersonating an enabled customer. Card
lock/loss does not prevent READ-scoped statement closure or expiry release.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import exists, or_, select

from .authentication import as_utc
from .contracts import DomainError
from .credit_atomic import atomic_domain
from .credit_billing import close_credit_statement
from .credit_cycle_models import CreditCycleState
from .credit_models import CreditAccount, CreditAuthorization, CreditCard, CreditStatement
from .credit_service import expire_credit_authorizations
from .customer_profile_common import require_profile_actor
from .models import User, utcnow


CALENDAR_TIMEZONE = "Asia/Shanghai"
CALENDAR_POLICY_VERSION = "credit-calendar-month-v1"
SHANGHAI = ZoneInfo(CALENDAR_TIMEZONE)
_AUTH_ACTIVE = ("AUTHORIZED", "PARTIALLY_CAPTURED")


def next_month_boundary(value):
    """Next local month start in UTC; no fixed thirty-day approximation."""
    local = as_utc(value).astimezone(SHANGHAI)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return as_utc(datetime(year, month, 1, tzinfo=SHANGHAI))


def _principal(facility):
    return SimpleNamespace(user_id=facility.user_id, sandbox_id=facility.sandbox_id, family_context=None)


def _lock_owner(session, facility):
    # Maintenance never waits while holding another customer's maintenance
    # locks. This also avoids cross-worker lock inversion across batch items.
    return session.scalar(select(User).where(User.id == facility.user_id,
        User.sandbox_id == facility.sandbox_id).with_for_update(skip_locked=True))


def _batch_limit(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise DomainError("CREDIT_CYCLE_BATCH_INVALID", "信用卡周期维护批次必须为1至1000。", 422)
    return value


def _initial_policy(facility):
    # Invalid registered policy stays observable and retryable during closing;
    # it must not prevent persistence of a cursor or expiry of an authorization.
    return {"version": CALENDAR_POLICY_VERSION, "timezone": CALENDAR_TIMEZONE,
        "statement_due_days": facility.policy_json.get("statement_due_days", 20),
        "due_balance_basis": "REMAINING_REGISTERED_DEBT_AT_CLOSE",
        "automatic_repayment": False}


def _initialize_cursors(session, now, limit):
    missing = [~exists().where(CreditCycleState.credit_account_id == CreditAccount.id,
        CreditCycleState.currency == currency) for currency in ("CNY", "USD")]
    facilities = list(session.scalars(select(CreditAccount).where(or_(*missing))
        .order_by(CreditAccount.created_at, CreditAccount.id).limit(limit)))
    initialized = 0
    for facility in facilities:
        with session.begin_nested():
            if _lock_owner(session, facility) is None:
                continue
            facility = session.scalar(select(CreditAccount).where(CreditAccount.id == facility.id)
                .with_for_update().execution_options(populate_existing=True))
            created = False
            for currency in ("CNY", "USD"):
                prior = session.scalar(select(CreditCycleState.id).where(
                    CreditCycleState.credit_account_id == facility.id, CreditCycleState.currency == currency))
                if prior:
                    continue
                last = session.scalar(select(CreditStatement).where(
                    CreditStatement.credit_account_id == facility.id, CreditStatement.currency == currency)
                    .order_by(CreditStatement.period_end.desc(), CreditStatement.id).limit(1))
                start = as_utc(last.period_end if last else facility.created_at)
                end = next_month_boundary(start)
                session.add(CreditCycleState(credit_account_id=facility.id, currency=currency,
                    next_period_start=start, next_period_end=end, policy_json=_initial_policy(facility),
                    next_billing_attempt_at=end, next_expiry_attempt_at=now))
                created = True
            session.flush()
            initialized += int(created)
    return initialized


def _locked_state(session, state_id):
    return session.scalar(select(CreditCycleState).where(CreditCycleState.id == state_id)
        .with_for_update(skip_locked=True).execution_options(populate_existing=True))


def _record_failure(session, state_id, phase, now, error):
    # Exception messages may contain private data. Persist a bounded code only.
    state = _locked_state(session, state_id)
    if state is None:
        return
    counter = getattr(state, phase + "_failure_count") + 1
    code = error.code if isinstance(error, DomainError) else type(error).__name__
    setattr(state, phase + "_failure_count", counter)
    setattr(state, "last_" + phase + "_error_code", str(code)[:80])
    setattr(state, "next_" + phase + "_attempt_at",
        now + timedelta(seconds=min(3600, 60 * 2 ** min(counter - 1, 6))))
    session.flush()


def _close_one(session, state_id, now):
    state = session.get(CreditCycleState, state_id)
    facility = session.get(CreditAccount, state.credit_account_id)
    principal = _principal(facility)
    # All maintenance work follows User -> cursor -> facility -> authorization.
    if _lock_owner(session, facility) is None:
        return 0
    require_profile_actor(session, principal, lock=True)
    state = _locked_state(session, state_id)
    if state is None or as_utc(state.next_billing_attempt_at) > now or as_utc(state.next_period_end) > now:
        return 0
    policy = state.policy_json
    days = policy.get("statement_due_days")
    if policy.get("version") != CALENDAR_POLICY_VERSION or policy.get("timezone") != CALENDAR_TIMEZONE \
            or type(days) is not int or not 1 <= days <= 60:
        raise DomainError("CREDIT_CYCLE_POLICY_INVALID", "登记的信用卡账期政策需核对。", 409)
    card = session.scalar(select(CreditCard).where(CreditCard.credit_account_id == facility.id,
        CreditCard.sandbox_id == facility.sandbox_id, CreditCard.user_id == facility.user_id)
        .order_by((CreditCard.status == "REPLACED"), CreditCard.created_at, CreditCard.id).limit(1))
    if card is None:
        raise DomainError("CREDIT_CYCLE_CARD_REQUIRED", "授信账户缺少可核验的信用卡。", 409)
    start, end = as_utc(state.next_period_start), as_utc(state.next_period_end)
    key = "calendar:" + start.astimezone(SHANGHAI).strftime("%Y-%m")
    close_credit_statement(session, principal, "credit:" + card.id, currency=state.currency,
        cycle_key=key, start=start, end=end, due_at=end + timedelta(days=days), now=now)
    state.next_period_start, state.next_period_end = end, next_month_boundary(end)
    state.next_billing_attempt_at = state.next_period_end
    state.last_billing_success_at = now
    state.billing_failure_count = 0
    state.last_billing_error_code = None
    session.flush()
    return 1


def _expire_one(session, authorization_id, state_id, now):
    state = session.get(CreditCycleState, state_id)
    facility = session.get(CreditAccount, state.credit_account_id)
    principal = _principal(facility)
    if _lock_owner(session, facility) is None:
        return 0
    require_profile_actor(session, principal, lock=True)
    state = _locked_state(session, state_id)
    if state is None or as_utc(state.next_expiry_attempt_at) > now:
        return 0
    results = expire_credit_authorizations(session, principal, now=now, limit=1,
        authorization_ids=[authorization_id])
    if not results:
        return 0
    state.last_expiry_success_at = now
    state.expiry_failure_count = 0
    state.last_expiry_error_code = None
    state.next_expiry_attempt_at = now
    session.flush()
    return len(results)


@atomic_domain
def run_due_credit_cycles(session, *, now=None, facility_limit=25, statement_limit=25, authorization_limit=100):
    """Maintain bounded work; the caller commits or rolls back all effects.

    One currency cursor advances at most one calendar period per invocation.
    Oldest periods and authorizations run first, while failures back off for
    60 seconds up to one hour. Restart/replay uses durable cursors, statement
    uniqueness and the existing expiry operation key, never a process timer.
    """
    now = as_utc(now or utcnow())
    facility_limit, statement_limit, authorization_limit = map(_batch_limit,
        (facility_limit, statement_limit, authorization_limit))
    result = {"initialized_facilities": _initialize_cursors(session, now, facility_limit),
        "closed_statements": 0, "expired_authorizations": 0,
        "billing_failures": 0, "expiry_failures": 0}
    # Candidate queries do not lock cursor/auth rows ahead of their owner.
    states = list(session.scalars(select(CreditCycleState.id).where(
        CreditCycleState.next_period_end <= now, CreditCycleState.next_billing_attempt_at <= now)
        .order_by(CreditCycleState.next_period_end, CreditCycleState.id).limit(statement_limit)))
    for state_id in states:
        try:
            with session.begin_nested():
                closed = _close_one(session, state_id, now)
            result["closed_statements"] += closed
        except Exception as error:
            _record_failure(session, state_id, "billing", now, error)
            result["billing_failures"] += 1
    authorizations = list(session.execute(select(CreditAuthorization.id, CreditCycleState.id)
        .join(CreditCycleState, (CreditCycleState.credit_account_id == CreditAuthorization.credit_account_id)
            & (CreditCycleState.currency == CreditAuthorization.currency))
        .where(CreditAuthorization.status.in_(_AUTH_ACTIVE), CreditAuthorization.expires_at <= now,
            CreditCycleState.next_expiry_attempt_at <= now)
        .order_by(CreditAuthorization.expires_at, CreditAuthorization.id).limit(authorization_limit)))
    for authorization_id, state_id in authorizations:
        try:
            with session.begin_nested():
                expired = _expire_one(session, authorization_id, state_id, now)
            result["expired_authorizations"] += expired
        except Exception as error:
            _record_failure(session, state_id, "expiry", now, error)
            result["expiry_failures"] += 1
    return result
