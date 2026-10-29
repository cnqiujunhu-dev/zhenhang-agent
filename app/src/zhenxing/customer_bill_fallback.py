"""Customer-only validation and owned supplemental statement reads.

Saved tasks, model replies and signed results remain unchanged. An alternative
statement must disclose its actual month; it is never authority for a write.
"""
from datetime import datetime

from .analytics import SHANGHAI


def movement_only_activity(analysis):
    """A bounded sparse CNY flow summary, never a claim of zero consumption.

    The read helper independently proves this against fresh owned rows. This
    shape gate only permits a distinctly disclosed supplementary statement.
    """
    if isinstance(analysis, dict) and analysis.get("billing_schema_version") == "owned-bill-analysis-v2":
        from .billing_presentation import owned_bill_display_recorded
        if not owned_bill_display_recorded(analysis) or analysis.get("period_fallback"):
            return None
        coverage = analysis["coverage"]
        totals = analysis["totals_by_currency"]
        if coverage.get("status") != "PARTIAL" or coverage.get("basis") != "LIVE_ACTIVITY_ONLY" or not analysis["transaction_count"] or not totals:
            return None
        if any(any(row[key] != 0 for key in ("consumption_minor", "fees_minor", "refund_minor", "income_minor")) for row in totals):
            return None
        return {"schema_version": "owned-bill-activity-v2", "transaction_count": analysis["transaction_count"], "totals_by_currency": totals}
    if not isinstance(analysis, dict) or analysis.get("period_fallback"):
        return None
    coverage=analysis.get("coverage")
    if not isinstance(coverage,dict) or coverage.get("status")!="PARTIAL" or coverage.get("basis")!="LIVE_ACTIVITY_ONLY":
        return None
    keys=("transaction_count","spending_minor","income_minor","refund_minor","movement_in_minor","movement_out_minor")
    if any(type(analysis.get(key)) is not int or not 0<=analysis[key]<=9007199254740991 for key in keys) \
            or analysis['transaction_count']<1 or any(analysis[key]!=0 for key in ('spending_minor','income_minor','refund_minor')) \
            or analysis['movement_in_minor']+analysis['movement_out_minor']<=0:
        return None
    period={"start":analysis.get('period_start'),"end":analysis.get('period_end'),"kind":"MONTH","time_zone":"Asia/Shanghai"}
    if not _month(period) or coverage.get('requested_start')!=period['start'] or coverage.get('requested_end')!=period['end']:
        return None
    return {'currency':'CNY',**{key:analysis[key] for key in keys},
        'period_start':period['start'],'period_end':period['end']}


def matches_supplement_source(original, supplement):
    meta=checked_bill_fallback(supplement)
    if not meta or not isinstance(original,dict) or not isinstance(original.get('coverage'),dict):return False
    requested=meta['requested']
    if original.get('period_start')!=requested['start'] or original.get('period_end')!=requested['end']:return False
    if meta['reason']=='REQUESTED_PERIOD_ONLY_MOVEMENTS':
        return movement_only_activity(original)==meta.get('requested_activity')
    return type(original.get('transaction_count')) is int and original['transaction_count']==0 \
        and original['coverage'].get('status') in {'INSUFFICIENT','UNKNOWN'}


def _month(value):
    if not isinstance(value, dict) or value.get("kind") != "MONTH" or value.get("time_zone") != "Asia/Shanghai":
        return None
    try:
        start = datetime.fromisoformat(value["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(value["end"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            return None
        local = start.astimezone(SHANGHAI)
        expected_start = datetime(local.year, local.month, 1, tzinfo=SHANGHAI)
        expected_end = datetime(local.year + (local.month == 12), local.month % 12 + 1, 1, tzinfo=SHANGHAI)
        return (start, end, local) if start == expected_start and end == expected_end else None
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        return None


def _verified_empty_category(analysis, meta, facts):
    """Zero matching rows in an actual populated month is not missing data.

    This exception requires both independently disclosed scopes: the populated
    source month and the exact selected category. It never validates a generic
    empty fallback or changes the originally requested/displayed period.
    """
    selected = analysis.get("category_filter")
    return bool(
        isinstance(selected, dict) and selected.get("version") == "bill-category-filter-v1"
        and selected.get("basis") == "CATEGORY_EXACT_MATCH" and selected.get("currency") == "CNY"
        and isinstance(selected.get("category"), str) and 0 < len(selected["category"]) <= 32
        and selected.get("read_only") is True and selected.get("bank_state_changed") is False
        and isinstance(facts, dict) and facts.get("category_filter") == selected
        and type(meta.get("unfiltered_period_transaction_count")) is int
        and 0 < meta["unfiltered_period_transaction_count"] <= 9007199254740991
        and analysis.get("category_spending_minor") == {}
        and analysis.get("statement_transactions") == []
        and all(type(analysis.get(field)) is int and analysis[field] == 0 for field in (
            "spending_minor", "income_minor", "refund_minor", "movement_in_minor", "movement_out_minor", "net_minor"))
    )


def checked_bill_fallback(analysis):
    if not isinstance(analysis, dict):
        return None
    if analysis.get("billing_schema_version") == "owned-bill-analysis-v2":
        from .billing_presentation import owned_bill_fallback
        return owned_bill_fallback(analysis)
    meta = analysis.get("period_fallback")
    coverage = analysis.get("coverage")
    facts = analysis.get("financial_insights")
    count = analysis.get("transaction_count")
    if (not isinstance(meta, dict) or meta.get("version") != "bill-period-fallback-v1"
            or meta.get("status") != "APPLIED" or meta.get("reason") not in {"REQUESTED_PERIOD_NOT_COVERED","REQUESTED_PERIOD_ONLY_MOVEMENTS"}
            or meta.get("read_only") is not True or meta.get("bank_state_changed") is not False
            or type(count) is not int or not 0 <= count <= 9007199254740991
            or type(meta.get("available_transaction_count")) is not int or meta["available_transaction_count"] != count
            or not isinstance(coverage, dict) or coverage.get("status") not in {"COMPLETE", "PARTIAL", "OBSERVED_HISTORY"}
            or not isinstance(facts, dict) or facts.get("version") != "financial-diagnostics-v1"
            or facts.get("currency") != "CNY" or facts.get("unit") != "MINOR"
            or facts.get("read_only") is not True or facts.get("bank_state_changed") is not False
            or facts.get("model_calculated_money") is not False):
        return None
    if count == 0 and not _verified_empty_category(analysis, meta, facts):
        return None
    for field in ("spending_minor", "income_minor", "refund_minor", "movement_in_minor", "movement_out_minor", "net_minor"):
        value = analysis.get(field)
        if type(value) is not int or abs(value) > 9007199254740991 or (field != "net_minor" and value < 0):
            return None
    categories = analysis.get("category_spending_minor")
    if not isinstance(categories, dict) or any(type(value) is not int or value < 0 for value in categories.values()) \
            or sum(categories.values()) != analysis["spending_minor"]:
        return None
    if isinstance(facts.get("coverage"), dict) and facts["coverage"] != coverage:
        return None
    if isinstance(facts.get("period_fallback"), dict) and facts["period_fallback"] != meta:
        return None
    requested, displayed = _month(meta.get("requested")), _month(meta.get("displayed"))
    original = meta.get("requested_coverage")
    if not requested or not displayed or displayed[1] > requested[0] or not isinstance(original, dict):
        return None
    if meta['reason']=='REQUESTED_PERIOD_ONLY_MOVEMENTS':
        activity=meta.get('requested_activity')
        if not isinstance(activity,dict) or activity.get('currency')!='CNY' \
                or movement_only_activity({**activity,'coverage':original})!=activity \
                or activity.get('period_start')!=meta['requested']['start'] or activity.get('period_end')!=meta['requested']['end']:
            return None
    elif original.get('status') not in {'INSUFFICIENT','UNKNOWN'}:return None
    try:
        for data, start_key, end_key, period in (
            (analysis, "period_start", "period_end", displayed),
            (coverage, "requested_start", "requested_end", displayed),
            (original, "requested_start", "requested_end", requested),
        ):
            actual_start = datetime.fromisoformat(data[start_key].replace("Z", "+00:00"))
            actual_end = datetime.fromisoformat(data[end_key].replace("Z", "+00:00"))
            if actual_start.tzinfo is None or actual_end.tzinfo is None or (actual_start, actual_end) != period[:2]:
                return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return meta


def fallback_month_labels(analysis, *, english=False):
    meta = checked_bill_fallback(analysis)
    if not meta:
        return None
    requested, displayed = _month(meta["requested"])[2], _month(meta["displayed"])[2]
    if english:
        months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
        return tuple(f"{months[item.month-1]} {item.year}" for item in (requested, displayed))
    return tuple(f"{item.year}年{item.month}月" for item in (requested, displayed))


def prepare_customer_bill_supplement(session, principal, trace):
    """Owned GET-only alternative for one saved, completed, uncovered query."""
    from .bill_period_fallback import read_recent_bill_fallback
    from .permissions import require_banking_actor, require_sandbox_owner

    analysis = trace.get("analysis")
    nodes = trace.get("nodes")
    if (trace.get("state") != "SUCCEEDED" or trace.get("pending_confirmation") is not None
            or (trace.get("outcome") or {}).get("verified") is not True
            or not isinstance(nodes, list) or len(nodes) != 1 or nodes[0].get("action") != "BILL_ANALYSIS"
            or nodes[0].get("status") != "SUCCEEDED" or (nodes[0].get("result") or {}).get("error")
            or not isinstance(analysis, dict) or type(analysis.get("transaction_count")) is not int
            or checked_bill_fallback(analysis)
            or not isinstance(analysis.get("coverage"), dict)
            or not (analysis['transaction_count']==0 and analysis['coverage'].get('status') in {'INSUFFICIENT','UNKNOWN'}
                or movement_only_activity(analysis))
            or not isinstance(trace.get("message"), str)):
        return None
    if require_banking_actor(session, principal) == "FAMILY_HELPER":
        return None
    account = require_sandbox_owner(session, principal)
    alternative = read_recent_bill_fallback(session, principal.sandbox_id, account.id,
        trace["message"], original_analysis=analysis,include_movement_only=True)
    value = alternative.model_dump(mode="json") if alternative else None
    return value if checked_bill_fallback(value) else None
