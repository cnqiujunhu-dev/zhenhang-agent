"""Finite public bill-v2 shape and bilingual wording, never source authority."""
from datetime import datetime
from decimal import Decimal

from .analytics import SHANGHAI
from .redaction import redact_credentials


VERSION = "owned-bill-analysis-v2"
LIMIT = 2**53 - 1


def _integer(value, *, signed=False):
    return type(value) is int and (-LIMIT if signed else 0) <= value <= LIMIT


def owned_bill_display_recorded(value):
    """Source/permission checks remain in checked_owned_bill_analysis."""
    try:
        if not isinstance(value, dict) or value.get("billing_schema_version") != VERSION or value.get("currency") != "CNY":
            return False
        diagnostics, scope, coverage = value.get("financial_insights"), value.get("billing_scope"), value.get("coverage")
        if not isinstance(diagnostics, dict) or diagnostics.get("version") != VERSION or diagnostics.get("read_only") is not True \
                or diagnostics.get("bank_state_changed") is not False or diagnostics.get("model_calculated_money") is not False \
                or not isinstance(scope, dict) or scope.get("scalar_fields_currency") != "CNY" or scope.get("currencies_combined") is not False \
                or not isinstance(coverage, dict) or coverage.get("status") not in {"COMPLETE", "PARTIAL", "OBSERVED_HISTORY", "UNKNOWN", "INSUFFICIENT"} \
                or diagnostics.get("coverage") != coverage or not _integer(value.get("transaction_count")):
            return False
        totals = value.get("totals_by_currency")
        if not isinstance(totals, list) or any(not isinstance(row, dict) for row in totals):
            return False
        if len({row.get("currency") for row in totals}) != len(totals):
            return False
        marker = (diagnostics.get('source_query') or {}).get('instrument_breakdown_version')
        if marker is not None or 'instrument_breakdown' in diagnostics:
            from .billing_evidence import INSTRUMENT_BREAKDOWN_VERSION, instrument_breakdown_valid
            if marker != INSTRUMENT_BREAKDOWN_VERSION or not instrument_breakdown_valid(
                    diagnostics.get('instrument_breakdown'), totals, known=coverage['status'] not in {'UNKNOWN', 'INSUFFICIENT'}):
                return False
        for row in totals:
            if row.get("currency") not in {"CNY", "USD"} or any(not _integer(row.get(key)) for key in (
                    "row_count", "consumption_minor", "fees_minor", "economic_spending_minor", "refund_minor", "income_minor",
                    "movement_in_minor", "movement_out_minor", "reclassified_principal_minor")):
                return False
            if not _integer(row.get("net_economic_spending_minor"), signed=True) or not _integer(row.get("net_economic_change_minor"), signed=True):
                return False
            if row.get("cash_flow_net_minor") is not None and not _integer(row["cash_flow_net_minor"], signed=True):
                return False
            categories = row.get("category_spending_minor")
            if not isinstance(categories, dict) or any(not isinstance(key, str) or not key or not _integer(amount) for key, amount in categories.items()):
                return False
            if row["economic_spending_minor"] != row["consumption_minor"] + row["fees_minor"] \
                    or sum(categories.values()) != row["economic_spending_minor"] \
                    or row["net_economic_spending_minor"] != row["economic_spending_minor"] - row["refund_minor"] \
                    or row["net_economic_change_minor"] != row["income_minor"] - row["net_economic_spending_minor"]:
                return False
        if sum(row["row_count"] for row in totals) != value["transaction_count"]:
            return False
        selected_card = scope.get("card_filter")
        if selected_card is not None and (not isinstance(selected_card, dict)
                or set(selected_card) != {"instrument_type", "display_last4"}
                or selected_card.get("instrument_type") not in {"DEBIT", "CREDIT"}
                or not isinstance(selected_card.get("display_last4"), str)
                or len(selected_card["display_last4"]) != 4):
            return False
        if scope.get("currency_filter") is not None and scope["currency_filter"] not in {"CNY", "USD"}:
            return False
        if scope.get("currency_filter") and any(row["currency"] != scope["currency_filter"] for row in totals):
            return False
        cny = next((row for row in totals if row["currency"] == "CNY"), {})
        for field, canonical in (("spending_minor", "economic_spending_minor"), ("refund_minor", "refund_minor"),
                ("income_minor", "income_minor"), ("movement_in_minor", "movement_in_minor"), ("movement_out_minor", "movement_out_minor")):
            if not _integer(value.get(field)) or value[field] != cny.get(canonical, 0):
                return False
        if value.get("category_spending_minor") != cny.get("category_spending_minor", {}) or not _integer(value.get("net_minor"), signed=True):
            return False
        if value["net_minor"] != cny.get("net_economic_change_minor", 0) + cny.get("movement_in_minor", 0) - cny.get("movement_out_minor", 0):
            return False
        category = value.get("category_filter")
        if category is not None and (not isinstance(category, dict) or category.get("version") != "bill-category-filter-v1"
                or category.get("basis") != "CATEGORY_EXACT_MATCH" or category.get("currency") != "SEPARATE_BY_CURRENCY"
                or not isinstance(category.get("category"), str) or not 1 <= len(category["category"]) <= 32
                or category.get("read_only") is not True or category.get("bank_state_changed") is not False
                or diagnostics.get("category_filter") != category):
            return False
        return True
    except (TypeError, KeyError, ValueError, AttributeError):
        return False


def _month(value):
    try:
        if not isinstance(value, dict) or value.get("kind") != "MONTH" or value.get("time_zone") != "Asia/Shanghai":
            return None
        start, end = (datetime.fromisoformat(value[key]) for key in ("start", "end"))
        if start.tzinfo is None or end.tzinfo is None:
            return None
        local = start.astimezone(SHANGHAI)
        if start != datetime(local.year, local.month, 1, tzinfo=SHANGHAI) or end != datetime(local.year + (local.month == 12), local.month % 12 + 1, 1, tzinfo=SHANGHAI):
            return None
        return start, end
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def owned_bill_fallback(value):
    if not owned_bill_display_recorded(value):
        return None
    meta = value.get("period_fallback")
    if not isinstance(meta, dict) or meta.get("version") != "bill-period-fallback-v1" or meta.get("status") != "APPLIED" \
            or meta.get("reason") not in {"REQUESTED_PERIOD_NOT_COVERED", "REQUESTED_PERIOD_ONLY_MOVEMENTS"} \
            or meta.get("read_only") is not True or meta.get("bank_state_changed") is not False \
            or type(meta.get("available_transaction_count")) is not int or meta["available_transaction_count"] != value["transaction_count"] \
            or value["financial_insights"].get("period_fallback") != meta:
        return None
    if not value["transaction_count"] and (not value.get("category_filter")
            or not _integer(meta.get("unfiltered_period_transaction_count")) or not meta["unfiltered_period_transaction_count"]):
        return None
    requested, displayed = _month(meta.get("requested")), _month(meta.get("displayed"))
    original, coverage = meta.get("requested_coverage"), value["coverage"]
    if requested is None or displayed is None or displayed[1] > requested[0] or not isinstance(original, dict):
        return None
    if coverage["status"] not in {"COMPLETE", "PARTIAL", "OBSERVED_HISTORY"}:
        return None
    if meta["reason"] == "REQUESTED_PERIOD_NOT_COVERED" and original.get("status") not in {"UNKNOWN", "INSUFFICIENT"}:
        return None
    if meta["reason"] == "REQUESTED_PERIOD_ONLY_MOVEMENTS":
        activity = meta.get("requested_activity")
        if not isinstance(activity, dict) or activity.get("schema_version") != "owned-bill-activity-v2" \
                or not _integer(activity.get("transaction_count")) or activity["transaction_count"] < 1 \
                or original.get("status") != "PARTIAL" or original.get("basis") != "LIVE_ACTIVITY_ONLY" \
                or not isinstance(activity.get("totals_by_currency"), list) or not activity["totals_by_currency"]:
            return None
        for row in activity["totals_by_currency"]:
            if not isinstance(row, dict) or row.get("currency") not in {"CNY", "USD"} or any(type(row.get(key)) is not int or row[key] != 0
                    for key in ("consumption_minor", "fees_minor", "refund_minor", "income_minor")):
                return None
    try:
        for data, keys, expected in ((value, ("period_start", "period_end"), displayed),
                (coverage, ("requested_start", "requested_end"), displayed), (original, ("requested_start", "requested_end"), requested)):
            if tuple(datetime.fromisoformat(data[key]) for key in keys) != expected:
                return None
    except (ValueError, TypeError, KeyError):
        return None
    return meta


def _money(value, currency, english):
    return f"{currency} {Decimal(value)/100:.2f}" if english else f"{Decimal(value)/100:.2f}" + ("元" if currency == "CNY" else "美元")


def owned_bill_summary(value, *, english=False):
    if not owned_bill_display_recorded(value):
        return ("The bill details need verification.", "No unsupported amounts are shown.") if english else ("账单资料还需要核验。", "不展示未经核对的金额。")
    coverage, data = value["coverage"], value["financial_insights"]
    if coverage["status"] in {"UNKNOWN", "INSUFFICIENT"}:
        if coverage.get("scope_status") == "USD_DEBIT_WALLET_SOURCE_UNAVAILABLE":
            return ("This debit card's USD-wallet statement is not covered.", "Missing wallet records are not zero spending.") if english else (
                "这张借记卡的美元钱包账单尚未接入当前来源。", "没有钱包流水不代表零消费。")
        return ("That period is not covered yet.", "An empty list is not proof of zero spending.") if english else ("这段期间的账单资料尚未覆盖。", "空清单不代表零消费。")
    fallback = owned_bill_fallback(value)
    if value.get("period_fallback") and fallback is None:
        return ("The alternative period needs verification.", "It is not shown as the requested month.") if english else ("替代账单的期间还需要核对。", "不会把其他月份说成所问月份。")
    intro = "Here is your recorded spending." if english else "账单整理好了，看看钱主要花在哪里。"
    scope = value["billing_scope"]
    card = scope.get("card_filter")
    if isinstance(card, dict):
        intro = f"Posted records for your card ending {card['display_last4']}, grouped by currency." if english else f"已整理尾号{card['display_last4']}卡片的已入账账单，各币种分开。"
    if fallback:
        requested = datetime.fromisoformat(fallback["requested"]["start"]).astimezone(SHANGHAI).strftime("%Y-%m")
        displayed = datetime.fromisoformat(fallback["displayed"]["start"]).astimezone(SHANGHAI).strftime("%Y-%m")
        intro = f"{requested} is not a complete spending statement; showing recorded {displayed} instead." if english else f"{requested}消费账单尚未完整覆盖，先看{displayed}的已有账单。"
    selected = value.get("category_filter")
    if selected:
        name = redact_credentials(selected["category"])
        intro += f" Only category {name}; not the full statement." if english else f"本次只看{name}分类，不是整期总额。"
    if scope.get("currency_filter"):
        intro += f" {scope['currency_filter']} only." if english else f"本次仅看{scope['currency_filter']}。"
    lines = []
    for total in value["totals_by_currency"]:
        currency = total["currency"]
        details = [("consumption_minor", "消费", "spending")]
        details += [(key, zh, en) for key, zh, en in (("fees_minor", "手续费", "fees"), ("refund_minor", "退款", "refunds")) if total[key]]
        if data.get("question") == "CASH_FLOW":
            details += [("income_minor", "收入", "income"), ("movement_in_minor", "资金转入", "money moved in"),
                        ("movement_out_minor", "资金转出", "money moved out")]
        parts = [f"{en} {_money(total[key],currency,True)}" if english else f"{zh}{_money(total[key],currency,False)}" for key, zh, en in details]
        lines.append(currency + (": " if english else "：") + (", " if english else "、").join(parts) + ("." if english else "。"))
    if not lines:
        lines.append("No recorded economic rows match this scope." if english else "本次范围没有匹配的已记录经济流水。")
    comparison = data.get("comparison")
    if data.get("question") == "SPENDING_CHANGE":
        if not isinstance(comparison, dict) or comparison.get("status") not in {"COMPLETE_PERIODS", "EQUAL_ELAPSED"}:
            lines.append("Coverage is insufficient for a reliable period comparison." if english else "两个期间覆盖不足，暂不能可靠比较增长或下降。")
        else:
            for row in comparison.get("by_currency", []):
                if not isinstance(row, dict) or row.get("currency") not in {"CNY", "USD"} or any(not _integer(row.get(key), signed=key=="spending_change_minor")
                        for key in ("current_spending_minor", "previous_spending_minor", "spending_change_minor")) \
                        or row["spending_change_minor"] != row["current_spending_minor"] - row["previous_spending_minor"]:
                    continue
                amount = _money(row["spending_change_minor"], row["currency"], english)
                lines.append(f"{row['currency']} comparable spending change: {amount}." if english else f"{row['currency']}可比期间消费变化：{amount}。")
            if comparison.get("status") == "EQUAL_ELAPSED":
                lines.append("Equal elapsed covered windows, not two full months." if english else "比较采用相同已覆盖时长，不是两个完整月份。")
    if coverage["status"] == "PARTIAL":
        lines.append("Only recorded coverage, not a full-period total." if english else "这里只统计已覆盖记录，不代表完整期间总额。")
    anomalies = value.get("anomalies")
    if isinstance(anomalies, list) and anomalies and all(isinstance(row, dict)
            and row.get("severity") == "NEEDS_USER_VERIFICATION" and row.get("fraud_confirmed") is False
            and row.get("automatic_action_authorized") is False for row in anomalies):
        lines.append(f"There are {len(anomalies)} items worth checking in the details; these are not confirmed fraud."
            if english else f"另有{len(anomalies)}条待核对提示，可以展开查看；它们不代表已确认异常扣款。")
    if data.get("question") == "CASH_FLOW":
        lines.append("This is the flow during the selected period, not your current account balance."
            if english else "这里是所选期间的资金进出，不是当前账户余额。")
    return intro, "\n".join(lines)
