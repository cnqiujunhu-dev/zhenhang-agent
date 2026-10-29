"""Presentation-only renewal timing; never advance or execute a mandate."""
from datetime import datetime

from . import reporting_clock
from .simulation import as_utc, business_now


def calendar_now():
    """Read the current trusted calendar provider, not a captured import.

    Explicit isolated clock overrides must not leak into later requests just
    because this module was first imported while the override was installed.
    HTTP customers cannot supply or activate that override.
    """
    return reporting_clock.calendar_now()


def subscription_calendar_context(session, sandbox_id, *, calendar_time=None):
    now = as_utc(calendar_time) if calendar_time is not None else calendar_now()
    try:
        through = min(as_utc(business_now(session, sandbox_id)), now)
    except ValueError:
        through = None  # an absent watermark is not a current-data guarantee
    return now, through


def subscription_timing(item, *, calendar_time, data_through):
    now = as_utc(calendar_time)
    due = getattr(item, "next_due_at", None)
    if getattr(item, "status", None) != "ACTIVE":
        status = "RECORDED_INACTIVE"
    elif not isinstance(due, datetime):
        status = "UNKNOWN"
    else:
        status = "PAST_RECORDED_DATE" if as_utc(due) < now else "UPCOMING"
    return {"due_status": status, "calendar_time": now.isoformat(),
            "data_through": as_utc(data_through).isoformat() if isinstance(data_through, datetime) else None,
            "timing_is_execution_proof": False}
