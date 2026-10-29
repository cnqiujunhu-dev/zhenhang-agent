"""Owned read-only recent statement selection, never a new bank clock or write.

An explicitly selected/limited period is not widened. The fallback is an
alternative statement for an ordinary relative current-month query when that
requested month has no eligible rows and no imported coverage. It cannot feed
card locking, comparisons, payment decisions or historical result mutation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re

from sqlalchemy import and_, func, or_, select

from .analytics import MOVEMENT_SOURCES, SHANGHAI, as_utc
from .contracts import BillAnalysis
from .models import Account, Transaction
from .reporting_clock import load_reporting_transactions, report_clock


FALLBACK_VERSION = "bill-period-fallback-v1"


def _period_ref(start, end):
    return {"start": as_utc(start).isoformat(), "end": as_utc(end).isoformat(),
        "kind": "MONTH", "time_zone": "Asia/Shanghai"}


def allows_recent_statement(message: str) -> bool:
    """Finite read-range policy, not intent parsing or financial authorization."""
    from .reports import bill_request_text
    from .financial_intent import foreign_financial_request
    from .intelligence import _intent_text, _private_read_veto
    if _private_read_veto(message) or foreign_financial_request(_intent_text(message)):
        return False
    source = bill_request_text(message)
    if re.search(r"\b(?:his|her|their|someone\s+else['’]?s|another\s+user['’]?s)\b[^.;\n]{0,35}\b(?:bills?|statements?|spending|expenses?)\b", source, re.I):
        return False
    if re.search(r"(?:不要|别|不想|不需要|不允许|拒绝)[^。；;\n]{0,8}(?:查|读取|访问|查看|分析)[^。；;\n]{0,25}(?:账单|帐单|流水|支出|消费)", source):
        return False
    if not re.search(r"本月|这个月|这月|当月|\b(?:this|current)\s+month(?:['’]s)?\b", source, re.I):
        return False
    # A named/numeric period is an explicit selection, even if it happens to
    # denote the same calendar month as 'this month'. Do not silently widen it.
    if re.search(r"(?<!\d)\d{1,2}\s*月|[一二三四五六七八九十]{1,3}\s*月|\b(?:20\d{2}|2100)[-/年]\s*\d{0,2}", source):
        return False
    if re.search(r"\b(?:january|jan|february|feb|march|mar|april|apr|may(?!\s+(?:i|we|you)\b)|june|jun|july|jul|august|aug|september|sept?|october|oct|november|nov|december|dec)\b", source, re.I):
        return False
    if re.search(r"(?:只|仅|仅仅|只要|只需要)(?:查|看|查询|查看|分析|统计|显示|提供|要)?[^。；;\n]{0,20}(?:本月|这个月|这月|当月)"
        r"|(?:不要|不看|不查|别看|别查|不需要|不用|拒绝)[^。；;\n]{0,20}(?:历史|以前|此前|之前|其他|其它|上月|上个月|往月|往期)"
        r"|\b(?:only|exclusively|strictly|just)\b[^.;\n]{0,35}\b(?:this|current)\s+month\b"
        r"|\b(?:this|current)\s+month(?:['’]s)?\b[^.;\n]{0,25}\b(?:only|exclusively|strictly)\b"
        r"|\bnot\s+(?:last|previous|other|earlier|past)\s+months?\b"
        r"|\b(?:do\s+not|don['’]?t|without|no|exclude)\b[^.;\n]{0,30}\b(?:histor(?:y|ical)|earlier|old(?:er)?|past|previous|other\s+months?)\b", source, re.I):
        return False
    # A comparative goal must retain its two original arms. We do not swap in
    # another month and tell the customer that their requested increase is real.
    if re.search(r"对比|比较|相比|环比|比上月|比上个月|\b(?:compare|comparison|versus|vs|compared)\b", source, re.I):
        return False
    return True


def allow_fallback_for_bill_task(session, task, report_source: str, source_context=None) -> bool:
    """Pure read DAG only: an alternative period never drives write decisions."""
    from .intelligence import READ_KINDS, rule_proposal
    from .models import Task
    from .runtime_models import RuntimeOperation, RuntimeRecord
    source_context = source_context if isinstance(source_context, dict) else {}
    root_id = source_context.get("source_root_task_id")
    if root_id is None:
        # Legacy single-task entry has no enclosing DAG to prove safe; any
        # proposed write keeps the requested analysis strict.
        return not any(action.kind not in READ_KINDS or action.kind.startswith("FAMILY_")
            for action in rule_proposal(report_source).actions)
    parent = session.get(Task, root_id)
    if parent is None or (parent.sandbox_id, parent.user_id) != (task.sandbox_id, task.user_id):
        return False
    record = session.get(RuntimeRecord, parent.id)
    if record is None or record.kind != "PARENT" or record.metadata_json.get("trusted_compensation") or record.metadata_json.get("family_origin"):
        return False
    operations = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.parent_task_id == parent.id, RuntimeOperation.sandbox_id == parent.sandbox_id,
        RuntimeOperation.active == 1)))
    return bool(operations) and all(operation.action_kind in READ_KINDS and not operation.action_kind.startswith("FAMILY_")
        for operation in operations)


def read_recent_bill_fallback(session, sandbox_id: str, account_id: str, message: str,
                              *, original_analysis=None, calendar_time: datetime | None = None,
                              include_movement_only: bool = False):
    """Return an alternative BillAnalysis or None, without changing any record.

The authenticated caller supplies its owned account. Old saved analysis is
    copied/validated and its original calendar bounds are retained. A historic
    'this month' is never reinterpreted as today's month during a read projection.
    """
    if not allows_recent_statement(message):
        return None
    account = session.scalar(select(Account).where(Account.id == account_id, Account.sandbox_id == sandbox_id))
    if account is None or account.currency != "CNY":
        return None
    from .financial_diagnostics import analyze_financial_request
    from .reports import period_boundaries
    if original_analysis is None:
        original = analyze_financial_request(session, sandbox_id, account_id, message,
            calendar_time=calendar_time, allow_recent_fallback=False)
    else:
        try:
            original = BillAnalysis.model_validate(deepcopy(original_analysis.model_dump()
                if hasattr(original_analysis, "model_dump") else original_analysis))
        except (ValueError, TypeError):
            return None
    original_coverage = original.coverage
    if original.billing_schema_version == "owned-bill-analysis-v2":
        from .billing_diagnostics import read_recent_owned_bill_fallback
        return read_recent_owned_bill_fallback(session, sandbox_id, account_id, message, original,
            include_movement_only=include_movement_only)
    from .customer_bill_fallback import movement_only_activity
    activity = movement_only_activity(original.model_dump(mode="json")) if include_movement_only else None
    empty_uncovered = original.transaction_count == 0 and isinstance(original_coverage, dict) \
        and original_coverage.get("status") in {"INSUFFICIENT", "UNKNOWN"}
    if (original.period_fallback or not (empty_uncovered or activity)
            or not original.period_start or not original.period_end):
        return None
    try:
        requested_start, requested_end = as_utc(datetime.fromisoformat(original.period_start)), as_utc(datetime.fromisoformat(original.period_end))
        original_calendar = as_utc(datetime.fromisoformat(original_coverage["calendar_time"]))
    except (ValueError, TypeError, KeyError):
        return None
    local_request_clock = original_calendar.astimezone(SHANGHAI)
    if (requested_start, requested_end) != period_boundaries("month", local_request_clock.year, local_request_clock.month):
        return None
    current_clock = report_clock(session, sandbox_id, account_id, calendar_time=calendar_time)
    # A later imported/posted row in the original requested month is not a
    # reason to use an older alternative. The normal query can now show it.
    current_coverage = current_clock.coverage(requested_start, requested_end)
    current_rows = load_reporting_transactions(session, sandbox_id, account_id, current_clock,
        start=requested_start, end=requested_end)
    if activity:
        # This is a separate customer read, not replacement of a saved October
        # report. Prove every currently owned row is a funds movement, and that
        # its counts/totals exactly match the original immutable query.
        from .analytics import is_movement
        if (current_coverage.get("status") != "PARTIAL" or current_coverage.get("basis") != "LIVE_ACTIVITY_ONLY"
                or len(current_rows) != activity["transaction_count"] or not all(is_movement(row) for row in current_rows)
                or sum(max(row.amount_minor, 0) for row in current_rows) != activity["movement_in_minor"]
                or sum(max(-row.amount_minor, 0) for row in current_rows) != activity["movement_out_minor"]):
            return None
    elif current_coverage["status"] not in {"INSUFFICIENT", "UNKNOWN"} or current_rows:
        return None
    latest = session.scalar(select(func.max(Transaction.occurred_at)).where(
        Transaction.sandbox_id == sandbox_id, Transaction.account_id == account_id,
        Transaction.occurred_at < requested_start,
        or_(Transaction.occurred_at <= current_clock.historical_data_through,
            and_(Transaction.source_kind.in_(MOVEMENT_SOURCES), Transaction.occurred_at <= current_clock.calendar_time))))
    if latest is None:
        return None
    local_latest = as_utc(latest).astimezone(SHANGHAI)
    displayed = period_boundaries("month", local_latest.year, local_latest.month)
    # Literal period argument prevents recursion or reinterpretation of the
    # original question. This is a pure alternative overview, not a comparison.
    alternative = analyze_financial_request(session, sandbox_id, account_id, "分析已有账单",
        period=displayed, calendar_time=current_clock.calendar_time, allow_recent_fallback=False,
        include_statement_transactions=True)
    if alternative.transaction_count <= 0 or not alternative.coverage or alternative.coverage.get("status") in {"UNKNOWN", "INSUFFICIENT"}:
        return None
    metadata = {"version": FALLBACK_VERSION, "status": "APPLIED", "reason": "REQUESTED_PERIOD_NOT_COVERED",
        "requested": _period_ref(requested_start, requested_end), "displayed": _period_ref(*displayed),
        "requested_coverage": deepcopy(original_coverage), "available_transaction_count": alternative.transaction_count,
        "read_only": True, "bank_state_changed": False}
    if activity:
        metadata.update(reason="REQUESTED_PERIOD_ONLY_MOVEMENTS", requested_activity=activity)
    insights = deepcopy(alternative.financial_insights)
    insights["requested_question"] = (original.financial_insights or {}).get("question", "OVERVIEW")
    insights["comparison"] = deepcopy((original.financial_insights or {}).get("comparison") or insights["comparison"])
    insights["period_fallback"] = metadata
    insights["insights"] = [{"code": "RECENT_STATEMENT_INSTEAD_OF_UNCOVERED_REQUEST", "fact_refs": [], "severity": "INFO"}] + insights["insights"]
    return alternative.model_copy(update={"period_fallback": metadata, "financial_insights": insights})
