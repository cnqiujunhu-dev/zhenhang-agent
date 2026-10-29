from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from calendar import monthrange
from statistics import median
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import SHANGHAI, as_utc, is_movement, load_bill_transactions
from .contracts import DomainError
from .models import Account, Subscription, Transaction
from .resource_defaults import ARCHIVED_RESOURCE_STATUS


def own_currency_breakdown(session, sandbox_id, user_id, start, end, clock):
    """USD cash legs remain their own denomination, never added to CNY totals."""
    from .fx_models import FxStatement, FxWallet
    wallet = session.scalar(select(FxWallet).where(FxWallet.sandbox_id == sandbox_id, FxWallet.user_id == user_id))
    if wallet is None:
        return {"currencies": ["CNY"], "mixed_currency_totals": False, "notice": "本报告为人民币账户流水。"}
    rows = list(session.scalars(select(FxStatement).where(FxStatement.sandbox_id == sandbox_id,
        FxStatement.user_id == user_id, FxStatement.currency == "USD", FxStatement.occurred_at >= start,
        FxStatement.occurred_at < end, FxStatement.occurred_at <= clock).order_by(FxStatement.occurred_at)))
    return {"currencies": ["CNY", "USD"], "mixed_currency_totals": False,
        "notice": "人民币报告与美元资金流水分币种展示；初始资金、转账和换汇不当经营性收入或收益。",
        "USD": {"currency": "USD", "current_balance_minor": wallet.balance_minor,
            "current_balance_scope": "CURRENT_NOT_PERIOD_CLOSING", "transaction_count": len(rows),
            "movement_in_minor": sum(row.amount_minor for row in rows if row.amount_minor > 0),
            "movement_out_minor": -sum(row.amount_minor for row in rows if row.amount_minor < 0),
            "net_minor": sum(row.amount_minor for row in rows),
            "transactions": [{"operation_id": row.operation_id, "amount_minor": row.amount_minor,
                "currency": "USD", "occurred_at": as_utc(row.occurred_at).isoformat(), "merchant": row.merchant,
                "direction": row.direction, "cash_flow_type": "MOVEMENT"} for row in rows]}}


REPORT_VERSION = "bank-report-v1"
ENGLISH_MONTHS = {name: index for index, names in enumerate([
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
    ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
    ("october", "oct"), ("november", "nov"), ("december", "dec")], 1) for name in names}


def period_boundaries(period: str, year: int, month: int | None = None) -> tuple[datetime, datetime]:
    if period not in {"month", "year"} or not 2000 <= year <= 2100:
        raise DomainError("INVALID_REPORT_PERIOD", "请选择2000至2100年的月报或年报", 422)
    if period == "month":
        if month is None or not 1 <= month <= 12:
            raise DomainError("INVALID_REPORT_PERIOD", "月份应为1至12", 422)
        start = datetime(year, month, 1, tzinfo=SHANGHAI)
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=SHANGHAI)
    else:
        start = datetime(year, 1, 1, tzinfo=SHANGHAI)
        end = datetime(year + 1, 1, 1, tzinfo=SHANGHAI)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def parse_report_period(message: str, business_now: datetime) -> tuple[datetime, datetime] | None:
    """Resolve in the supplied reporting calendar (production supplies real now)."""
    local = as_utc(business_now).astimezone(SHANGHAI)
    lowered = message.casefold()
    rolling = re.search(r"(?:最近|过去|近)\s*(\d{1,4}|[零〇一二两三四五六七八九十百千万]{1,5})\s*(?:个)?\s*(天|日|月)", message)
    english_rolling = re.search(r"\b(?:past|last|recent)\s+(\d{1,4}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(days?|months?)\b", lowered)
    if rolling or english_rolling:
        if rolling:
            from .intelligence import _chinese_integer
            amount = int(rolling[1]) if rolling[1].isdigit() else _chinese_integer(rolling[1])
            monthly = rolling[2] == "月"
        else:
            amounts = {word: value for value, word in enumerate(("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"), 1)}
            amount = int(english_rolling[1]) if english_rolling[1].isdigit() else amounts[english_rolling[1]]
            monthly = english_rolling[2].startswith("month")
        if not 1 <= amount <= (120 if monthly else 3660):
            raise DomainError("INVALID_REPORT_PERIOD", "请指定有效的近期报告范围", 422)
        if monthly:
            total = local.year*12+local.month-1-amount
            year, zero_month = divmod(total, 12)
            start = local.replace(year=year, month=zero_month+1, day=min(local.day, monthrange(year, zero_month+1)[1]))
        else:
            start = local-timedelta(days=amount)
        if not 2000 <= start.year <= 2100:
            raise DomainError("INVALID_REPORT_PERIOD", "请选择2000至2100年的报告范围", 422)
        return start.astimezone(timezone.utc), as_utc(business_now)+timedelta(microseconds=1)
    english = next((match for match in re.finditer(r"\b("+"|".join(ENGLISH_MONTHS)+r")\b(?:\s+(\d{4}))?", lowered)
                    if not (match[1] == "may" and (re.match(r"\s+(?:i|we|you|be|need|want|check|show)\b", lowered[match.end():])
                            or re.search(r"\b(?:i|we|you)\s*$", lowered[:match.start()])))), None)
    if english:
        preceding_year = re.search(r"\b(20\d{2}|2100)\s*$", lowered[:english.start()])
        return period_boundaries("month", int(english[2]) if english[2] else (int(preceding_year[1]) if preceding_year else local.year-("last year" in lowered)), ENGLISH_MONTHS[english[1]])
    explicit = re.search(r"(?<!\d)(\d{4})\s*(?:年|[-/])\s*(\d{1,2})\s*(?:月|(?=\D|$))", message)
    if explicit:
        return period_boundaries("month", int(explicit[1]), int(explicit[2]))
    month_only = re.search(r"(?<!\d)(\d{1,2})\s*月", message)
    if month_only:
        explicit_year = re.search(r"(?<!\d)(\d{4})\s*年", message)
        year = int(explicit_year[1]) if explicit_year else local.year - ("去年" in message)
        return period_boundaries("month", year, int(month_only[1]))
    chinese_month = re.search(r"(?<![零〇一二两三四五六七八九十百千万])([零〇一二两三四五六七八九十百]{1,5})\s*月份?", message)
    if chinese_month:
        from .intelligence import _chinese_integer
        explicit_year = re.search(r"(?<!\d)(\d{4})\s*年", message)
        year = int(explicit_year[1]) if explicit_year else local.year - ("去年" in message)
        return period_boundaries("month", year, _chinese_integer(chinese_month[1]))
    if any(word in message for word in ("上月", "上个月")) or re.search(r"\b(?:last|previous)\s+month\b", lowered):
        month = local.month - 1 or 12
        return period_boundaries("month", local.year - (local.month == 1), month)
    if any(word in message for word in ("本月", "这个月", "当月", "月报")) or re.search(r"\b(?:this|current)\s+month\b|\bmonthly\s+(?:bill|report|spending)\b", lowered):
        return period_boundaries("month", local.year, local.month)
    explicit_year = re.search(r"(?<!\d)(\d{4})\s*年", message)
    if explicit_year:
        return period_boundaries("year", int(explicit_year[1]))
    # A numeric calendar year may govern the whole-year noun without 年.
    # Do not reinterpret a card-tail or a longer identifier as that year.
    for annual in re.finditer(r"(?<![0-9A-Za-z_])(\d{4})\s*全年", message):
        if re.search(r"(?:尾号|末四位|ending(?:\s+in)?)\s*$", message[:annual.start()], re.I):
            continue
        return period_boundaries("year", int(annual[1]))
    english_year = re.search(r"\b(20\d{2}|2100)\b", message)
    if english_year and re.search(r"\b(?:year|annual|bill|report|spending)\b", lowered):
        return period_boundaries("year", int(english_year[1]))
    if "去年" in message or "上年" in message or "last year" in lowered or "previous year" in lowered:
        return period_boundaries("year", local.year - 1)
    if any(word in message for word in ("今年", "本年", "年度", "年报")) or re.search(r"\b(?:this|current)\s+year\b|\bannual\s+(?:bill|report|spending)\b", lowered):
        return period_boundaries("year", local.year)
    return None


def bill_request_text(message: str) -> str:
    """Only user report instructions supply dates; other actions and quoted data do not."""
    from .intelligence import _ACTION_PATTERNS, _intent_text
    from .financial_diagnostics import financial_question
    intent = _intent_text(message)
    chunks = [part.strip() for part in re.split(r"[，,。；;！？!?\n]|然后|接着|随后|(?:再|并)(?=分析|查看|给|向|创建|预留)", intent) if part.strip()]
    bill_indices = {index for index, part in enumerate(chunks) if _ACTION_PATTERNS["BILL_ANALYSIS"].search(part)
                    or re.search(r"账单|帐单|支出|消费|花费|花得|钱.*(?:少|去哪)|\b(?:bills?|spending|expenses?|cash\s*flow)\b", part, re.I)
                    or financial_question(part) == "CASH_FLOW"}
    other_indices = {index for index, part in enumerate(chunks) if index not in bill_indices
        and any(pattern.search(part) for kind, pattern in _ACTION_PATTERNS.items() if kind != "BILL_ANALYSIS")}
    if not bill_indices:
        # A typed BILL_ANALYSIS followup may be only a period/category. Keep
        # its trusted text when no other action claims it; requiring the user
        # to repeat the domain noun erased explicit periods before inheritance.
        # Dates belonging to another action or to quoted material stay excluded.
        return "；".join(chunks) if chunks and not other_indices else "分析账单"
    if not other_indices:
        return "；".join(chunks)
    selected, in_bill = [], False
    for index, part in enumerate(chunks):
        if index in bill_indices:
            in_bill = True
        elif index in other_indices:
            in_bill = False
        if in_bill:
            selected.append(part)
    return "；".join(selected)


def positive_bill_period_text(source: str) -> str:
    """Remove only explicitly excluded date phrases before period selection.

    The original request is retained by the workflow/fallback metadata. This
    parser view prevents 'this month, not last month' from selecting last month
    merely because its date-token branch used to run first. Other actions and
    quoted source dates are already removed by bill_request_text.
    """
    month = r"(?:上个月|上月|这个月|本月|当月|下个月|下月|(?:20\d{2}年)?(?:\d{1,2}|[一二三四五六七八九十]{1,3})月份?)"
    source = re.sub(r"(?:不要|不看|不查|别看|别查|不用|排除)(?:查询|查看|分析|显示|提供)?\s*(?:我的|本人(?:的)?)?\s*" + month, " ", source)
    english_month = r"(?:this|current|last|previous|next)\s+month|(?:january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept?|october|oct|november|nov|december|dec)(?:\s+20\d{2})?"
    return re.sub(r"\b(?:not|don't(?:\s+(?:show|include|read|query))?|do\s+not(?:\s+(?:show|include|read|query))?|excluding|exclude|without|no)\s+(?:(?:my|the|any)\s+)?(?:" + english_month + r")\b", " ", source, flags=re.I)


def comparative_report_periods(message: str, calendar_time: datetime) -> list[tuple[datetime, datetime]]:
    """Two-period comparisons retain their actual periods; no implicit fallback."""
    if not re.search(r"对比|比较|相比|环比|比(?=\d|[一二三四五六七八九十]|上月|上个|本月)|\b(?:compare|comparison|versus|vs|differ|difference)\b", message, re.I):
        return []
    source = positive_bill_period_text(bill_request_text(message))
    chunks = re.split(r"；|与|和|相比|对比|比较|比(?=\d|[一二三四五六七八九十]|上月|上个|本月)|\b(?:and|versus|vs|with|to|from)\b", source, flags=re.I)
    # 'this month compared with last month' and compact numeric months are
    # separated before parsing; years are inherited only when explicitly given.
    inherited_year = None
    found = []
    for chunk in chunks:
        explicit_year = re.search(r"(?<!\d)(20\d{2}|2100)(?!\d)", chunk)
        if explicit_year:
            inherited_year = explicit_year[1]
        elif inherited_year and re.search(r"\d{1,2}\s*月", chunk):
            chunk = inherited_year+"年"+chunk
        elif inherited_year and re.search(r"\b(?:"+"|".join(ENGLISH_MONTHS)+r")\b", chunk, re.I):
            chunk = re.sub(r"\b("+"|".join(ENGLISH_MONTHS)+r")\b", lambda match: match[0]+" "+inherited_year, chunk, count=1, flags=re.I)
        period = parse_report_period(chunk, calendar_time)
        if period and period not in found:
            found.append(period)
    return found


def bill_request_period(message: str, business_clock: datetime) -> tuple[datetime, datetime] | None:
    source = positive_bill_period_text(bill_request_text(message))
    comparison = comparative_report_periods(message, business_clock)
    if len(comparison) == 2:
        return comparison[0]
    if len(comparison) > 2:
        raise DomainError("AMBIGUOUS_REPORT_PERIOD", "一次账单比较请指定两个期间", 422)
    periods = {period for part in source.split("；") if (period := parse_report_period(part, business_clock)) is not None}
    if len(periods) > 1:
        raise DomainError("AMBIGUOUS_REPORT_PERIOD", "一次账单步骤请指定单一月份或年度；多个期间请分别查询", 422)
    return next(iter(periods)) if periods else None


def owned_account(session: Session, sandbox_id: str, user_id: str) -> Account:
    account = session.scalar(select(Account).where(Account.sandbox_id == sandbox_id, Account.user_id == user_id))
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "未找到当前用户的账户", 404)
    return account


def generate_bill_report(
    session: Session, sandbox_id: str, user_id: str,
    *, period: str = "month", year: int | None = None, month: int | None = None,
    calendar_time: datetime | None = None, card_reference: str | None = None, currency: str | None = None,
) -> dict:
    from .reporting_clock import load_reporting_transactions, report_clock
    from .financial_diagnostics import analyze_financial_request
    account = owned_account(session, sandbox_id, user_id)
    from .billing_diagnostics import upgraded_bill_owner, owned_bill_report
    if upgraded_bill_owner(session, sandbox_id, account.id) is not None:
        from .reporting_clock import owned_economic_clock
        from .services import Principal
        clocks = owned_economic_clock(session, Principal(user_id, sandbox_id), calendar_time=calendar_time)
    else:
        clocks = report_clock(session, sandbox_id, account.id, calendar_time=calendar_time)
    clock = clocks.data_through
    local_clock = clocks.calendar_time.astimezone(SHANGHAI)
    selected_year = year if year is not None else local_clock.year
    selected_month = month if month is not None else local_clock.month
    start, end = period_boundaries(period, selected_year, selected_month if period == "month" else None)
    if upgraded_bill_owner(session, sandbox_id, account.id) is not None:
        return owned_bill_report(session, sandbox_id, account.id, period_kind=period, year=selected_year,
            month=selected_month, start=start, end=end, calendar_time=clocks.calendar_time,
            card_reference=card_reference, currency=currency)
    if card_reference is not None or currency is not None and currency != "CNY":
        raise DomainError("BILL_FILTER_SOURCE_UNAVAILABLE", "当前旧账单不支持这个卡片或币种范围。", 422)
    transactions = load_reporting_transactions(session, sandbox_id, account.id, clocks, start=start, end=end)
    analysis = analyze_financial_request(session, sandbox_id, account.id, "分析账单", period=(start, end), calendar_time=clocks.calendar_time)
    rows = [{
        "transaction_id": item.id, "occurred_at": as_utc(item.occurred_at).isoformat(),
        "amount_minor": item.amount_minor, "merchant": item.merchant,
        "category": item.category, "flag": item.flag,
        "source_kind": item.source_kind, "operation_ref": item.operation_ref,
        "cash_flow_type": "MOVEMENT" if is_movement(item) else "OPERATING",
    } for item in transactions]
    category_total = sum(analysis.category_spending_minor.values())
    income_category_total = sum(analysis.category_income_minor.values())
    transaction_net = sum(item.amount_minor for item in transactions)
    return {
        "report_version": REPORT_VERSION, "synthetic": True,
        "account_id": account.id, "currency": account.currency,
        "business_time": clock.isoformat(),
        "calendar_time": clocks.calendar_time.isoformat(), "coverage": analysis.coverage,
        "period": {
            "kind": period, "year": selected_year,
            "month": selected_month if period == "month" else None,
            "label": f"{selected_year}年{selected_month}月" if period == "month" else f"{selected_year}年",
            "start": start.isoformat(), "end": end.isoformat(),
            "time_zone": "Asia/Shanghai", "boundary": "[start,end)",
            "partial": start <= clock < end,
            "status": "NOT_STARTED" if clocks.calendar_time < start else ("IN_PROGRESS" if clocks.calendar_time < end else "CLOSED"),
            "data_through": min(clock, end).isoformat() if clock >= start else None,
        },
        "summary": analysis.model_dump(),
        "currency_breakdown": own_currency_breakdown(session, sandbox_id, user_id, start, end, clock),
        "transactions": rows,
        "reconciliation": {
            "transaction_net_minor": transaction_net,
            "reported_net_minor": analysis.net_minor,
            "category_spending_minor": category_total,
            "reported_spending_minor": analysis.spending_minor,
            "category_income_minor": income_category_total,
            "reported_income_minor": analysis.income_minor,
            "balanced": (transaction_net == analysis.net_minor and category_total == analysis.spending_minor
                         and income_category_total == analysis.income_minor),
            "scope": "PERIOD_TRANSACTION_FLOW",
            "explanation": "本期收支核对以流水为准，不把期内净额等同于账户当前余额。",
        },
    }


def analyze_recurring_subscriptions(session: Session, sandbox_id: str, user_id: str) -> dict:
    """Infer recurring debits from evidence; authority is always queried separately."""
    account = owned_account(session, sandbox_id, user_id)
    from .reporting_clock import load_reporting_transactions, report_clock
    from .subscription_timing import subscription_timing
    clocks = report_clock(session, sandbox_id, account.id)
    clock = clocks.data_through
    transactions = load_reporting_transactions(session, sandbox_id, account.id, clocks)
    negative_by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.amount_minor < 0 and not is_movement(transaction):
            negative_by_merchant[transaction.merchant].append(transaction)
    subscriptions = list(session.scalars(select(Subscription).where(
        Subscription.sandbox_id == sandbox_id, Subscription.user_id == user_id,
        Subscription.status != ARCHIVED_RESOURCE_STATUS,
    ).order_by(Subscription.merchant)))
    authority = [{
        "subscription_id": row.id, "merchant": row.merchant, "amount_minor": row.amount_minor,
        "source_type": row.source_type, "status": row.status,
        "next_due_at": as_utc(row.next_due_at).isoformat(),
        **subscription_timing(row, calendar_time=clocks.calendar_time, data_through=clock),
        "is_bank_mandate": row.source_type == "BANK_MANDATE",
        "can_cancel_bank_mandate": row.source_type == "BANK_MANDATE" and row.status == "ACTIVE",
        "evidence_source": "AUTHORITATIVE_SIMULATION_BANK",
    } for row in subscriptions]
    candidates = []
    possible_duplicates = []
    excluded_frequent_merchants = []
    cycle_ranges = [("WEEKLY", 5, 9), ("MONTHLY", 25, 35), ("QUARTERLY", 80, 100), ("YEARLY", 335, 395)]
    for merchant in sorted(negative_by_merchant):
        rows = sorted(negative_by_merchant[merchant], key=lambda item: as_utc(item.occurred_at))
        same_day: dict[tuple[str, int], list[Transaction]] = defaultdict(list)
        for row in rows:
            same_day[(as_utc(row.occurred_at).astimezone(SHANGHAI).date().isoformat(), row.amount_minor)].append(row)
        duplicate_groups = [group for group in same_day.values() if len(group) > 1]
        for group in duplicate_groups:
            possible_duplicates.append({
                "merchant": merchant, "amount_minor": -group[0].amount_minor,
                "local_day": as_utc(group[0].occurred_at).astimezone(SHANGHAI).date().isoformat(),
                "transaction_ids": [row.id for row in group],
                "reason": "同日同商户同金额多次扣费，需要核验是否重复或独立消费。",
            })
        # A duplicated debit on one date contributes one occurrence to the cycle,
        # while its receipt identifiers remain visible for review.
        unique_rows = [group[0] for group in same_day.values()]
        unique_rows.sort(key=lambda item: as_utc(item.occurred_at))
        if len(unique_rows) < 3:
            continue
        amounts = [-row.amount_minor for row in unique_rows]
        amount_median = int(median(amounts))
        if max(abs(amount - amount_median) for amount in amounts) / amount_median > 0.10:
            continue
        intervals = [(as_utc(right.occurred_at) - as_utc(left.occurred_at)).total_seconds() / 86400
                     for left, right in zip(unique_rows, unique_rows[1:])]
        interval_median = median(intervals)
        if interval_median < 5:
            excluded_frequent_merchants.append(merchant)
            continue
        cycle = next((name for name, low, high in cycle_ranges
                      if low <= interval_median <= high
                      and sum(low <= days <= high for days in intervals) / len(intervals) >= 0.8), None)
        if cycle is None:
            continue
        matches = [row for row in authority if row["merchant"] == merchant and row["amount_minor"] == amount_median]
        match = matches[0] if len(matches) == 1 else None
        candidates.append({
            "merchant": merchant, "amount_minor": amount_median,
            "cycle": cycle, "median_interval_days": round(interval_median, 2),
            "occurrence_count": len(unique_rows),
            "observed_spending_minor": sum(-row.amount_minor for row in rows),
            "first_occurred_at": as_utc(unique_rows[0].occurred_at).isoformat(),
            "last_occurred_at": as_utc(unique_rows[-1].occurred_at).isoformat(),
            "transaction_ids": [row.id for row in rows],
            "evidence_source": "TRANSACTION_PATTERN",
            "candidate_only": True,
            "has_possible_duplicates": bool(duplicate_groups),
            "authoritative_subscription": match,
            "is_bank_mandate": bool(match and match["is_bank_mandate"]),
            "can_cancel_bank_mandate": bool(match and match["can_cancel_bank_mandate"]),
            "reason": "至少三次负金额流水，金额偏差不超过10%，至少80%的相邻间隔符合周期。",
        })
    return {
        "report_version": REPORT_VERSION, "synthetic": True,
        "business_time": clock.isoformat(), "time_zone": "Asia/Shanghai",
        "calendar_time": clocks.calendar_time.isoformat(), "data_through": clock.isoformat(),
        "coverage": clocks.coverage(None, None),
        "read_only": True, "bank_state_changed": False,
        "candidates": candidates, "possible_duplicates": possible_duplicates,
        "authoritative_subscriptions": authority,
        "excluded_frequent_merchants": excluded_frequent_merchants,
        "explanation": "流水规律仅形成周期扣费候选；银行代扣与可取消能力由协议表确认，本报告不改变任何订阅或协议。",
    }
