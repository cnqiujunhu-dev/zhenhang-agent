"""Read-only reporting calendar, independent of bank and security clocks.

Never advance a bank clock to make a report look current. A test may pass an
explicit calendar_time; HTTP customers cannot supply or activate that override.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select

from .analytics import MOVEMENT_SOURCES, SHANGHAI, as_utc, load_bill_transactions
from .models import Account, Transaction, utcnow
from .simulation import business_now


def calendar_now() -> datetime:
    return as_utc(utcnow())


@dataclass(frozen=True)
class ReportClock:
    calendar_time: datetime
    data_through: datetime
    data_start: datetime | None
    historical_data_through: datetime
    historical_data_start: datetime | None = None
    live_activity_through: datetime | None = None
    live_activity_start: datetime | None = None

    def coverage(self, start: datetime | None, end: datetime | None) -> dict:
        now, through, first = self.calendar_time, self.data_through, self.data_start
        historical = self.historical_data_through
        historical_start = self.historical_data_start
        live_start, live_end = self.live_activity_start, self.live_activity_through
        has_new_activity = bool(live_start and live_end and live_end > historical)
        base = {"calendar_time": now.isoformat(), "calendar_date": now.astimezone(SHANGHAI).date().isoformat(),
                "time_zone": "Asia/Shanghai", "data_through": through.isoformat(),
                "historical_data_through": historical.isoformat(),
                "historical_data_start": historical_start.isoformat() if historical_start else None,
                "live_activity_through": live_end.isoformat() if live_end else None,
                "live_activity_is_full_import": False,
                "data_start": first.isoformat() if first else None,
                "requested_start": as_utc(start).isoformat() if start else None,
                "requested_end": as_utc(end).isoformat() if end else None,
                "data_through_inclusive": True,
                "boundary": "[effective_start,effective_end)",
                "read_only": True, "bank_state_changed": False, "zero_is_full_period_total": False}
        if first is None:
            return {**base, "status": "UNKNOWN", "basis": "UNKNOWN", "effective_start": None, "effective_end": None,
                    "notice": "没有可核验的账单覆盖起点，不能把缺失资料当成零消费。"}
        if start is None:
            return {**base, "status": "OBSERVED_HISTORY", "basis": "HISTORY_AND_LIVE_ACTIVITY" if has_new_activity else "HISTORICAL_LEDGER", "effective_start": first.isoformat(),
                    "effective_end": through.isoformat(), "notice": "仅统计现有已入账流水，不保证覆盖所有历史期间。"}
        start, end = as_utc(start), as_utc(end)
        has_period_activity = bool(has_new_activity and live_start < end and live_end >= start and end > historical)
        if has_period_activity:
            basis = "LIVE_ACTIVITY_ONLY" if start > historical else "HISTORY_AND_LIVE_ACTIVITY"
            effective_start = max(start, live_start) if basis == "LIVE_ACTIVITY_ONLY" else max(start, first)
            return {**base, "status": "PARTIAL", "basis": basis,
                    "effective_start": effective_start.isoformat(),
                    "effective_end": min(end, live_end+timedelta(microseconds=1)).isoformat(),
                    "notice": "仅包含已入账的新活动；较早导入的账务截止未推进，不能据此认定其他消费已完整覆盖或为零。"}
        if historical_start is None:
            return {**base, "status": "UNKNOWN", "basis": "UNKNOWN", "effective_start": None, "effective_end": None,
                    "notice": "当前仅有分币种活动记录，尚无可核验的人民币历史覆盖起点。"}
        effective_start, effective_end = max(start, historical_start), min(end, historical+timedelta(microseconds=1))
        if effective_end <= effective_start:
            return {**base, "status": "INSUFFICIENT", "basis": "HISTORICAL_LEDGER", "effective_start": None, "effective_end": None,
                    "notice": "所选期间尚无已覆盖的账单资料；空清单不代表零消费。"}
        complete = historical_start <= start and historical+timedelta(microseconds=1) >= end
        return {**base, "status": "COMPLETE" if complete else "PARTIAL", "basis": "HISTORICAL_LEDGER",
                "effective_start": effective_start.isoformat(), "effective_end": effective_end.isoformat(),
                "zero_is_full_period_total": complete,
                "notice": "所选期间的现有流水已覆盖。" if complete else "仅统计已覆盖期间，不能作为完整期间总额或直接与完整期间比较。"}


def report_clock(session, sandbox_id: str, account_id: str, *, calendar_time: datetime | None = None) -> ReportClock:
    now = as_utc(calendar_time) if calendar_time is not None else calendar_now()
    historical = min(as_utc(business_now(session, sandbox_id)), now)
    live_dates = list(session.scalars(select(Transaction.occurred_at).where(
        Transaction.sandbox_id == sandbox_id, Transaction.account_id == account_id,
        Transaction.source_kind.in_(MOVEMENT_SOURCES), Transaction.occurred_at <= now)))
    from .fx_models import FxStatement
    owner = session.scalar(select(Account.user_id).where(Account.id == account_id, Account.sandbox_id == sandbox_id))
    if owner is not None:
        live_dates += list(session.scalars(select(FxStatement.occurred_at).where(
            FxStatement.sandbox_id == sandbox_id, FxStatement.user_id == owner, FxStatement.occurred_at <= now)))
    live_dates = [as_utc(value) for value in live_dates]
    live = max(live_dates) if live_dates else None
    new_dates = [value for value in live_dates if value > historical]
    through = max(historical, live) if live else historical
    # A first posted row is a conservative lower bound, not a synthetic claim
    # that every date before the row has been imported. Ownership is mandatory.
    first = session.scalar(select(func.min(Transaction.occurred_at)).where(
        Transaction.sandbox_id == sandbox_id, Transaction.account_id == account_id,
        or_(Transaction.occurred_at <= historical,
            and_(Transaction.source_kind.in_(MOVEMENT_SOURCES), Transaction.occurred_at <= now))))
    # USD-only posted flows can establish sparse activity, not CNY completeness.
    first_values = ([as_utc(first)] if first else []) + live_dates
    historical_start = session.scalar(select(func.min(Transaction.occurred_at)).where(
        Transaction.sandbox_id == sandbox_id, Transaction.account_id == account_id,
        Transaction.occurred_at <= historical))
    return ReportClock(now, through, min(first_values) if first_values else None,
                       historical, as_utc(historical_start) if historical_start else None, live, min(new_dates) if new_dates else None)


def load_reporting_transactions(session, sandbox_id: str, account_id: str, clock: ReportClock,
                                *, start: datetime | None = None, end: datetime | None = None):
    """Fresh movements do not unlock unimported future HISTORY consumption."""
    rows = load_bill_transactions(session, sandbox_id, account_id, start=start, end=end,
                                  business_time=clock.data_through)
    return [row for row in rows if row.source_kind in MOVEMENT_SOURCES
            or as_utc(row.occurred_at) <= clock.historical_data_through]


def owned_economic_clock(session, principal, *, calendar_time=None, evidence=None):
    """All owned cash history + sparse registered activity; no clock mutation.

    Credit is the complete *internal* posting ledger, not evidence that a
    missing external-bank feed has been imported. Live purchases/refunds do
    not advance the historical cash watermark or unlock future HISTORY rows.
    """
    from .billing_evidence import collect_owned_billing_evidence
    now = as_utc(calendar_time) if calendar_time is not None else calendar_now()
    from .simulation_models import SimulationState
    state = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == principal.sandbox_id))
    # Fresh internal-only customers have no imported historical coverage. Do
    # not create a simulation row or claim today's empty history is complete.
    historical = min(as_utc(state.business_time), now) if state else datetime(1970, 1, 1, tzinfo=SHANGHAI)
    if evidence is None:
        evidence = collect_owned_billing_evidence(session, principal,
            datetime(1970, 1, 1, tzinfo=SHANGHAI), now + timedelta(microseconds=1),
            business_time=now, historical_through=historical)
    dates = [as_utc(datetime.fromisoformat(row["occurred_at"])) for row in evidence["rows"]]
    old = [as_utc(datetime.fromisoformat(row["occurred_at"])) for row in evidence["rows"]
        if row["evidence_type"] == "OWNED_ACCOUNT_TRANSACTION"
        and as_utc(datetime.fromisoformat(row["occurred_at"])) <= historical]
    live = [date for date in dates if date > historical]
    return ReportClock(now, max([historical, *live]), min(dates) if dates else None, historical,
        min(old) if old else None, max(live) if live else None, min(live) if live else None)
