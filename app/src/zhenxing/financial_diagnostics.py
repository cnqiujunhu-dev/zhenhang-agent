"""Integer, actor-owned financial facts for questions, not model arithmetic."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from .analytics import SHANGHAI, as_utc, is_movement, is_refund, summarize_transactions
from .reporting_clock import load_reporting_transactions, report_clock
from .contracts import DomainError


DIAGNOSTICS_VERSION = "financial-diagnostics-v1"
LARGEST_VERSION = "full-recorded-bill-largest-expense-v1"
CATEGORY_LABELS_EN = {"餐饮": "Dining", "购物": "Shopping", "娱乐": "Entertainment", "订阅": "Subscriptions",
    "会员": "Memberships", "日常": "Everyday essentials", "交通": "Transport", "生活缴费": "Utilities",
    "住房": "Housing", "工资": "Salary", "养老金": "Pension", "教育": "Education", "医疗": "Health care",
    "保险": "Insurance", "退款": "Refunds", "投资收益": "Investment income", "待核验": "Needs verification"}


def financial_question(message: str) -> str:
    # A primary comparison remains a comparison when the user also asks us to
    # separate transfers. Movement facts stay available without stealing intent.
    if re.search(r"(?:消费|支出|花费|开销).{0,30}(?:为什么|为啥|为何|相比|对比|比较|比).{0,40}(?:多|高|少|变化|增加|下降)|(?:compare|difference).{0,45}(?:spending|expenses)|(?:spending|expenses).{0,45}(?:vs|versus|higher|more|compared)", message, re.I):
        return "SPENDING_CHANGE"
    if re.search(r"钱.*(?:少|去哪|花在|花到|花去|怎么没)|余额.*(?:下降|减少)|(?:本月|这个月|当月).{0,8}花.{0,6}(?:哪里|哪儿|哪了)|消费还是转账|资金搬移|(?:where|why).*(?:money|balance)|spending\s+or\s+transfers|cash\s*flow", message, re.I):
        return "CASH_FLOW"
    if re.search(r"少花|省钱|节省|少一点|可控|减少.*支出|(?:save|reduce|cut).*(?:spending|expenses|cost)|where\s+can\s+I\s+save", message, re.I):
        return "SAVING_OPTIONS"
    if re.search(r"为什么.*(?:多|高)|为何|对比|比较|相比|环比|变化|比(?=\d|[一二三四五六七八九十]|上月|上个|本月)|(?:why|compare|increase|higher|more|change|differ).*(?:spend|bill|expenses|month)|spend.*(?:more|higher)|\b(?:vs|versus|difference|differ)\b", message, re.I):
        return "SPENDING_CHANGE"
    return "OVERVIEW"


def _previous_period(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    from .reports import period_boundaries
    local = as_utc(start).astimezone(SHANGHAI)
    if (start, end) == period_boundaries("year", local.year):
        return period_boundaries("year", local.year-1)
    if (start, end) != period_boundaries("month", local.year, local.month):
        return as_utc(start)-(as_utc(end)-as_utc(start)), as_utc(start)
    month = local.month-1 or 12
    return period_boundaries("month", local.year-(local.month == 1), month)


def _fact(field: str, value: int, label: str, label_en: str, currency="CNY") -> dict:
    return {"fact_id": "facts:bill-diagnostics." + field, "source_id": "facts:bill-diagnostics",
            "field": field, "value": value, "currency": currency, "unit": "MINOR",
            "label": label, "label_en": label_en}


def _largest_expense_snapshot(session,sandbox_id,account_id,rows,start,end,category_filter,clock,coverage):
    """Bank-side integers over the complete recorded window, not UI recent50."""
    from .models import Account
    from .planning import canonical_hash
    from .redaction import redact_credentials
    account=session.get(Account,account_id)
    if not account or account.sandbox_id!=sandbox_id or account.currency!="CNY":
        raise DomainError("BILL_LARGEST_SCOPE_CHANGED","账单账户或币种不匹配，请重新查询。",409)
    selected=[row for row in rows if category_filter is None or row.category==category_filter]
    if any(row.sandbox_id!=sandbox_id or row.account_id!=account.id for row in selected):
        raise DomainError("BILL_LARGEST_SCOPE_CHANGED","账单来源不属于当前账户。",409)
    records=[{"transaction_id":row.id,"amount_minor":row.amount_minor,"currency":"CNY",
        "merchant":redact_credentials(row.merchant),"category":redact_credentials(row.category),
        "occurred_at":as_utc(row.occurred_at).isoformat(),"source_kind":row.source_kind,"flag":row.flag}
        for row in selected]
    expenses=[row for row in selected if row.amount_minor<0 and not is_movement(row) and not is_refund(row)]
    maximum=max((-row.amount_minor for row in expenses),default=None)
    largest=[row for row in expenses if -row.amount_minor==maximum]
    largest.sort(key=lambda row:(as_utc(row.occurred_at),row.id),reverse=True)
    records_by_id={record["transaction_id"]:record for record in records}
    value={"version":LARGEST_VERSION,"authority":"BANK_FULL_RECORDED_WINDOW_NOT_UI_SLICE_OR_MODEL_CALCULATION",
        "owner_scope_hash":canonical_hash({"sandbox_id":sandbox_id,"user_id":account.user_id,"account_id":account.id,"currency":"CNY"}),
        "period_start":as_utc(start).isoformat() if start else None,"period_end":as_utc(end).isoformat() if end else None,
        "category_filter":category_filter,"currency":"CNY","unit":"MINOR","source_complete":True,
        "completeness_basis":"ALL_RECORDED_ROWS_WITHIN_VERIFIED_SCOPE_NOT_COMPLETE_CALENDAR_IMPORT",
        "calendar_time":as_utc(clock.calendar_time).isoformat(),"data_through":as_utc(clock.data_through).isoformat(),
        "historical_data_through":as_utc(clock.historical_data_through).isoformat(),"coverage":coverage,
        "recorded_row_count":len(selected),"expense_count":len(expenses),"source_rows_hash":canonical_hash(sorted(records,key=lambda row:row["transaction_id"])),
        "maximum_expense_minor":maximum,"tie_count":len(largest),"status":"FOUND" if largest else "NO_RECORDED_EXPENSE",
        "records":[{key:records_by_id[row.id][key] for key in ("transaction_id","amount_minor","currency","merchant","category","occurred_at")}
            for row in largest],
        "read_only":True,"bank_state_changed":False,"model_calculated_money":False}
    value["snapshot_hash"]=canonical_hash(value)
    return value


def checked_largest_expense_snapshot(session,sandbox_id,user_id,analysis):
    """Recompute the exact owned recorded window and reject stale/tampered data."""
    from sqlalchemy import select
    from .models import Account
    from .planning import canonical_hash
    if isinstance(analysis, dict) and analysis.get("billing_schema_version") == "owned-bill-analysis-v2":
        from .billing_diagnostics import checked_owned_bill_analysis
        return checked_owned_bill_analysis(session, sandbox_id, user_id, analysis)["financial_insights"]["largest_expense"]
    value=(analysis.get("financial_insights") or {}).get("largest_expense") if isinstance(analysis,dict) else None
    if not isinstance(value,dict) or value.get("version")!=LARGEST_VERSION:return None
    def deny():raise DomainError("BILL_LARGEST_SOURCE_CHANGED","这份账单的最大支出来源已变化，请重新查询。",409)
    if value.get("snapshot_hash")!=canonical_hash({key:item for key,item in value.items() if key!="snapshot_hash"}):deny()
    accounts=list(session.scalars(select(Account).where(Account.sandbox_id==sandbox_id,Account.user_id==user_id,Account.currency=="CNY")))
    matched=[account for account in accounts if canonical_hash({"sandbox_id":sandbox_id,"user_id":user_id,"account_id":account.id,"currency":"CNY"})==value.get("owner_scope_hash")]
    if len(matched)!=1:deny()
    account=matched[0]
    if any(value.get(key)!=analysis.get(key) for key in ("period_start","period_end")):deny()
    category=(analysis.get("category_filter") or {}).get("category") if analysis.get("category_filter") else None
    if category!=value.get("category_filter"):deny()
    try:
        start=datetime.fromisoformat(value["period_start"]) if value["period_start"] else None
        end=datetime.fromisoformat(value["period_end"]) if value["period_end"] else None
        calendar=datetime.fromisoformat(value["calendar_time"])
        if calendar.tzinfo is None or start and start.tzinfo is None or end and end.tzinfo is None:deny()
        clock=report_clock(session,sandbox_id,account.id,calendar_time=calendar)
        rows=load_reporting_transactions(session,sandbox_id,account.id,clock,start=start,end=end)
        expected=_largest_expense_snapshot(session,sandbox_id,account.id,rows,start,end,category,clock,clock.coverage(start,end))
    except (ValueError,TypeError,KeyError):deny()
    if expected!=value:deny()
    return value


def analyze_financial_request(session, sandbox_id: str, account_id: str, message: str,
                              *, period=None, calendar_time: datetime | None = None,
                              allow_recent_fallback: bool = False,
                              include_statement_transactions: bool = False,
                              category_filter: str | None = None,
                              card_reference: str | None = None, currency: str | None = None,
                              instrument_breakdown_version=None, query_context_version=None):
    """Return backward-compatible BillAnalysis with optional verifiable metadata."""
    from .billing_diagnostics import upgraded_bill_owner, analyze_owned_bill
    if upgraded_bill_owner(session, sandbox_id, account_id) is not None:
        return analyze_owned_bill(session, sandbox_id, account_id, message, period=period, calendar_time=calendar_time,
            allow_recent_fallback=allow_recent_fallback, include_statement_transactions=include_statement_transactions,
            category_filter=category_filter, card_reference=card_reference, currency=currency,
            instrument_breakdown_version=instrument_breakdown_version, query_context_version=query_context_version)
    if card_reference is not None or currency is not None and currency != "CNY":
        raise DomainError("BILL_FILTER_SOURCE_UNAVAILABLE", "当前旧账单不支持这个卡片或币种范围，请先查看原有人民币账单。", 422)
    from .reports import bill_request_period, bill_request_text, period_boundaries
    clock = report_clock(session, sandbox_id, account_id, calendar_time=calendar_time)
    question = financial_question(bill_request_text(message))
    if period is None:
        period = bill_request_period(message, clock.calendar_time)
    if period is None and question == "SPENDING_CHANGE":
        local = clock.calendar_time.astimezone(SHANGHAI)
        period = period_boundaries("month", local.year, local.month)
    start, end = period if period else (None, None)
    window_rows = {}
    def summarize_window(window_start, window_end):
        key = (window_start, window_end)
        if key not in window_rows:
            window_rows[key] = load_reporting_transactions(session, sandbox_id, account_id, clock, start=window_start, end=window_end)
        rows = window_rows[key]
        if category_filter is not None:
            rows = [row for row in rows if row.category == category_filter]
        return summarize_transactions(rows, start=window_start, end=window_end)
    current = summarize_window(start, end)
    coverage = clock.coverage(start, end)
    facts: list[dict] = []
    insights: list[dict] = []
    candidates: list[dict] = []
    comparison = {"status": "NOT_REQUESTED", "current": None, "previous": None,
                  "spending_change_minor": None, "growth_claim_allowed": False,
                  "zero_baseline": False, "category_changes": []}
    available = coverage["status"] not in {"UNKNOWN", "INSUFFICIENT"}
    if not available:
        insights.append({"code": "PERIOD_NOT_COVERED", "fact_refs": [], "severity": "NOTICE"})
    else:
        labels = [("spending_minor", "已覆盖流水消费", "Spending in covered records"),
                  ("income_minor", "已覆盖流水收入", "Income in covered records"),
                  ("refund_minor", "已覆盖流水退款", "Refunds in covered records"),
                  ("movement_in_minor", "资金搬移流入", "Movement inflows"),
                  ("movement_out_minor", "资金搬移流出", "Movement outflows"),
                  ("net_minor", "期间流水净额（非当前余额）", "Net posted flow (not current balance)")]
        facts.extend(_fact(field, getattr(current, field), label, label_en) for field, label, label_en in labels)
        if coverage["status"] == "PARTIAL":
            insights.append({"code": "PARTIAL_COVERAGE", "fact_refs": [], "severity": "NOTICE"})
        if question == "CASH_FLOW":
            insights.append({"code": "SPENDING_AND_MOVEMENT_SEPARATE", "severity": "INFO",
                             "fact_refs": ["facts:bill-diagnostics."+field for field in
                                           ("spending_minor", "movement_out_minor", "movement_in_minor", "net_minor")]})
        elif question == "SAVING_OPTIONS":
            # These are review candidates, never a guaranteed saving or a reason
            # to stop an essential payment. No cancellation is performed here.
            for category, amount in sorted(current.category_spending_minor.items(), key=lambda item: (-item[1], item[0])):
                if re.search(r"餐饮|购物|娱乐|订阅|会员|日常|dining|shopping|entertainment|subscription", category, re.I):
                    field = "review_category_" + hashlib.sha256(category.encode()).hexdigest()[:12]
                    facts.append(_fact(field, amount, category+"已入账消费（检查候选）", "Posted spending to review: "+CATEGORY_LABELS_EN.get(category, category)))
                    candidates.append({"category": category, "amount_minor": amount, "fact_ref": "facts:bill-diagnostics."+field,
                                       "currency": "CNY", "unit": "MINOR", "action": "REVIEW_ONLY", "guaranteed_saving_minor": None})
            insights.append({"code": "REVIEW_SPENDING_NOT_PROMISED_SAVING", "severity": "INFO",
                             "fact_refs": [row["fact_ref"] for row in candidates[:3]]})
        else:
            insights.append({"code": "POSTED_FLOW_OVERVIEW", "severity": "INFO",
                             "fact_refs": ["facts:bill-diagnostics.spending_minor", "facts:bill-diagnostics.net_minor"]})
    if question == "SPENDING_CHANGE" and period:
        previous = _previous_period(start, end)
        # Explicit comparative requests may supply two distinct calendar periods.
        from .reports import comparative_report_periods
        requested = comparative_report_periods(message, clock.calendar_time)
        if len(requested) == 2:
            previous = next(item for item in requested if item != period)
        previous_coverage = clock.coverage(*previous)
        comparison.update(current={"start": start.isoformat(), "end": end.isoformat()},
                          previous={"start": previous[0].isoformat(), "end": previous[1].isoformat()},
                          status="INSUFFICIENT", previous_coverage=previous_coverage)
        # The bank watermark is inclusive whereas report ranges are half-open.
        # Include rows posted exactly at the watermark in both comparable arms.
        effective_end = datetime.fromisoformat(coverage["effective_end"]) if coverage.get("effective_end") else min(end, clock.historical_data_through+timedelta(microseconds=1))
        current_duration = effective_end-start
        previous_duration = previous[1]-previous[0]
        previous_available = min(previous[1], clock.historical_data_through+timedelta(microseconds=1))-previous[0]
        sparse_live_basis = coverage.get("basis") in {"LIVE_ACTIVITY_ONLY", "HISTORY_AND_LIVE_ACTIVITY"} or previous_coverage.get("basis") in {"LIVE_ACTIVITY_ONLY", "HISTORY_AND_LIVE_ACTIVITY"}
        # Only equal elapsed, covered windows are comparable. Month lengths may
        # differ: complete month-vs-month is explicitly labelled calendar totals.
        if sparse_live_basis:
            compare_end, previous_end, status = effective_end, previous[1], "INSUFFICIENT"
            comparison["reason"] = "LIVE_ACTIVITY_NOT_FULL_IMPORT"
        elif coverage["status"] == "COMPLETE" and previous_coverage["status"] == "COMPLETE":
            compare_end, previous_end, status = end, previous[1], "COMPLETE_PERIODS"
        elif available and clock.historical_data_start and clock.historical_data_start <= start and clock.historical_data_start <= previous[0] and min(current_duration, previous_duration, previous_available) > timedelta(0):
            comparable_duration = min(current_duration, previous_duration, previous_available)
            compare_end, previous_end, status = start+comparable_duration, previous[0]+comparable_duration, "EQUAL_ELAPSED"
            if clock.coverage(previous[0], previous_end)["status"] != "COMPLETE":
                status = "INSUFFICIENT"
        else:
            compare_end, previous_end, status = effective_end, previous[1], "INSUFFICIENT"
        if status != "INSUFFICIENT":
            left = summarize_window(start, compare_end)
            right = summarize_window(previous[0], previous_end)
            delta = left.spending_minor-right.spending_minor
            comparison.update(status=status, current={"start": start.isoformat(), "end": compare_end.isoformat()},
                              previous={"start": previous[0].isoformat(), "end": previous_end.isoformat()},
                              current_spending_minor=left.spending_minor, previous_spending_minor=right.spending_minor,
                              spending_change_minor=delta, zero_baseline=right.spending_minor == 0,
                              growth_claim_allowed=right.spending_minor > 0)
            facts.extend([_fact("comparison_current_spending_minor", left.spending_minor, "可比期间消费", "Comparable current spending"),
                          _fact("comparison_previous_spending_minor", right.spending_minor, "对照期间消费", "Comparable previous spending"),
                          _fact("spending_change_minor", delta, "消费金额变化（当前减对照）", "Spending change (current minus previous)")])
            changes = []
            for category in sorted(set(left.category_spending_minor) | set(right.category_spending_minor)):
                amount = left.category_spending_minor.get(category, 0)-right.category_spending_minor.get(category, 0)
                if amount:
                    field = "category_change_"+hashlib.sha256(category.encode()).hexdigest()[:12]
                    facts.append(_fact(field, amount, category+"消费变化", "Spending change: "+CATEGORY_LABELS_EN.get(category, category)))
                    changes.append({"category": category, "change_minor": amount, "fact_ref": "facts:bill-diagnostics."+field})
            comparison["category_changes"] = sorted(changes, key=lambda row: (-abs(row["change_minor"]), row["category"]))
            insights.append({"code": "SPENDING_CHANGE" if delta else "SPENDING_UNCHANGED", "severity": "INFO",
                             "fact_refs": ["facts:bill-diagnostics.spending_change_minor"] + [row["fact_ref"] for row in comparison["category_changes"][:2]]})
        else:
            insights.append({"code": "COMPARISON_NOT_COVERED", "fact_refs": [], "severity": "NOTICE"})
    diagnostics = {"version": DIAGNOSTICS_VERSION, "question": question, "currency": "CNY",
                   "unit": "MINOR", "facts": facts, "insights": insights, "comparison": comparison,
                   "read_only": True, "bank_state_changed": False, "model_calculated_money": False,
                   "money_scope": "POSTED_CNY_FLOW_NOT_CURRENT_BALANCE", "coverage": coverage}
    diagnostics["historical_balance_change_known"] = False
    diagnostics["review_candidates"] = candidates
    diagnostics["category_labels_en"] = {category: CATEGORY_LABELS_EN.get(category, category)
        for category in set(current.category_spending_minor) | set(current.category_income_minor)}
    category_metadata = None
    if category_filter is not None:
        from .bill_category_filter import FILTER_VERSION
        if not isinstance(category_filter, str) or not 1 <= len(category_filter) <= 32:
            raise DomainError("BILL_CATEGORY_INVALID", "请明确一个受支持的账单类别。", 422)
        known_categories = {row.category for row in load_reporting_transactions(session, sandbox_id, account_id, clock)}
        if category_filter not in known_categories:
            names = "、".join(sorted(known_categories))
            raise DomainError("BILL_CATEGORY_UNKNOWN", f"已有账单里暂没有{category_filter}类别。可以查看：{names}。" if names else "目前还没有可按类别筛选的账单记录。", 422)
        category_metadata = {"version": FILTER_VERSION, "basis": "CATEGORY_EXACT_MATCH", "category": category_filter,
                             "currency": "CNY", "read_only": True, "bank_state_changed": False}
        diagnostics["category_filter"] = category_metadata
    statement_rows = []
    diagnostics["largest_expense"]=_largest_expense_snapshot(session,sandbox_id,account_id,
        window_rows[(start,end)],start,end,category_filter,clock,coverage)
    if include_statement_transactions:
        from .redaction import redact_credentials
        visible_rows = [row for row in window_rows[(start, end)] if category_filter is None or row.category == category_filter]
        for row in visible_rows[:50]:
            statement_rows.append({"transaction_id": row.id, "amount_minor": row.amount_minor, "currency": "CNY",
                "merchant": redact_credentials(row.merchant), "category": redact_credentials(row.category),
                "occurred_at": as_utc(row.occurred_at).isoformat(), "cash_flow_type": (
                    "MOVEMENT" if is_movement(row) else "REFUND" if is_refund(row) else
                    "EXPENSE" if row.amount_minor < 0 else "INCOME")})
    result = current.model_copy(update={"coverage": coverage, "financial_insights": diagnostics,
        "category_filter": category_metadata,
        "statement_transactions": statement_rows,
        "statement_transactions_truncated": bool(include_statement_transactions and current.transaction_count > 50)})
    if allow_recent_fallback:
        alternative = read_recent_bill_fallback(session, sandbox_id, account_id, message,
            original_analysis=result, calendar_time=clock.calendar_time)
        if alternative is not None:
            if category_filter is not None:
                selected = analyze_financial_request(session, sandbox_id, account_id, "分析已有账单",
                    period=(datetime.fromisoformat(alternative.period_start), datetime.fromisoformat(alternative.period_end)),
                    calendar_time=clock.calendar_time, allow_recent_fallback=False,
                    include_statement_transactions=include_statement_transactions, category_filter=category_filter)
                metadata = dict(alternative.period_fallback)
                metadata["available_transaction_count"] = selected.transaction_count
                metadata["unfiltered_period_transaction_count"] = alternative.transaction_count
                selected.financial_insights["period_fallback"] = metadata
                return selected.model_copy(update={"period_fallback": metadata})
            return alternative
    return result


# Imported at the end: its analysis import is intentionally inside the call,
# keeping the pure default diagnostics usable without a recursive fallback.
from .bill_period_fallback import read_recent_bill_fallback
