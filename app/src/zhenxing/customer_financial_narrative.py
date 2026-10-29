"""Short bilingual financial conclusions from typed, already-owned report facts.

No model calls, database access, arithmetic proposals or execution authority.
The client projection may omit internal source IDs; fact identity/value/unit and
the diagnostic authority flags still have to agree before numbers are narrated.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re

from .redaction import redact_credentials

CHINA_TIME = timezone(timedelta(hours=8))


def amount(value, currency="CNY", english=False):
    if type(value) is not int or currency not in {"CNY", "USD"}:
        return None
    return f"{currency} {Decimal(value)/100:.2f}" if english else f"{Decimal(value)/100:.2f}" + ("人民币元" if currency == "CNY" else "美元")


def date_label(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return None
        return instant.astimezone(CHINA_TIME).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        return None


def financial_summary(analysis, *, english=False):
    if not isinstance(analysis, dict):
        return None
    if analysis.get("billing_schema_version") == "owned-bill-analysis-v2":
        from .billing_presentation import owned_bill_summary
        return owned_bill_summary(analysis, english=english)
    coverage = analysis.get("coverage")
    if not isinstance(coverage, dict):
        return None  # historical reports keep their original presentation
    through = date_label(coverage.get("data_through"))
    cutoff = (f"Available records run through {through} (China time). " if english else f"资料目前截止{through}。") if through else ""
    if coverage.get("status") not in {"COMPLETE", "PARTIAL", "OBSERVED_HISTORY"}:
        return (("This period is not yet covered by available records." if english else "这段时间的账单资料尚未覆盖。"),
                cutoff + ("An empty list is not proof of zero spending, and no growth or saving claim can be made. You can ask for an earlier recorded period."
                          if english else "空清单不代表没有消费，不能据此判断增长或节省金额。可以继续查看已有记录的期间。"))
    data = analysis.get("financial_insights")
    if not isinstance(data, dict):
        return None
    if data.get("version") != "financial-diagnostics-v1" or data.get("currency") != "CNY" or data.get("unit") != "MINOR" \
            or data.get("read_only") is not True or data.get("bank_state_changed") is not False \
            or data.get("model_calculated_money") is not False:
        return ("The financial details need verification." if english else "财务分析资料还需要核验。",
                "No unsupported amount or conclusion is shown." if english else "当前不展示没有核验依据的金额或结论。")
    facts = {}
    for fact in data.get("facts", []) if isinstance(data.get("facts"), list) else []:
        if not isinstance(fact, dict) or type(fact.get("value")) is not int or fact.get("currency") != "CNY" or fact.get("unit") != "MINOR":
            continue
        field = fact.get("field")
        if isinstance(field, str) and re.fullmatch(r"[a-z_0-9]+", field) and fact.get("fact_id") == "facts:bill-diagnostics." + field:
            facts[field] = fact["value"]
    partial = ("Only recorded coverage is used; this is not a complete-period total. " if english else "仅按已覆盖记录统计，不代表完整期间总额。") if coverage.get("status") == "PARTIAL" else ""
    boundary = "No banking action was taken." if english else "本次未执行银行操作。"
    from .customer_bill_fallback import fallback_month_labels
    fallback_labels = fallback_month_labels(analysis, english=english)
    if fallback_labels:
        requested_month, displayed_month = fallback_labels
        intro = (f"The {requested_month} statement is not available yet. Here is your latest recorded statement for {displayed_month}."
                 if english else f"{requested_month}账单暂未更新，先给你看{displayed_month}的已有账单。")
        if analysis['period_fallback']['reason']=='REQUESTED_PERIOD_ONLY_MOVEMENTS':
            intro=(f'{requested_month} currently has recorded funds movements, not a complete spending statement. Here is your latest recorded spending statement for {displayed_month}.'
                if english else f'{requested_month}目前有资金流水，消费账单还未完整更新。先给你看{displayed_month}的已有消费账单。')
        count = analysis["transaction_count"]
        detail = f"{count} recorded transactions" if english else f"共{count}笔已记录流水"
        for field, label in (("spending_minor", "spending" if english else "消费"), ("income_minor", "income" if english else "收入")):
            value = facts.get(field)
            if type(value) is int and 0 <= value <= 9007199254740991 and value == analysis.get(field):
                detail += f"; {label}: {amount(value, english=english)}" if english else f"，{label}{amount(value)}"
        return intro, detail + ("." if english else "。")
    question = data.get("question")
    if question == "SPENDING_CHANGE":
        comparison = data.get("comparison")
        if not isinstance(comparison, dict) or comparison.get("status") not in {"COMPLETE_PERIODS", "EQUAL_ELAPSED"}:
            return ("There are not enough comparable records yet." if english else "目前还不能可靠比较两个期间。",
                    cutoff + ("The two periods are not both covered. I won't claim that spending grew or fell from incomplete data. "
                              if english else "两个期间的资料未同时覆盖，不能把不完整记录说成消费增长或下降。") + boundary)
        delta, left, right = (facts.get(field) for field in ("spending_change_minor", "comparison_current_spending_minor", "comparison_previous_spending_minor"))
        comparison_values = [comparison.get(field) for field in ("spending_change_minor", "current_spending_minor", "previous_spending_minor")]
        if type(delta) is not int or type(left) is not int or type(right) is not int or left < 0 or right < 0 \
                or any(type(value) is not int for value in comparison_values) \
                or delta != left-right or delta != comparison.get("spending_change_minor") \
                or left != comparison.get("current_spending_minor") or right != comparison.get("previous_spending_minor"):
            return ("The comparison needs verification." if english else "期间差异还需要核验。",
                    "No unsupported growth amount is shown." if english else "不展示没有核对一致的增长金额。")
        detail = (f"Recorded spending changed by {amount(delta, english=True)} over the disclosed comparable windows. "
                  if english else f"披露的可比区间内，已记录消费变化为{amount(delta)}。")
        if comparison.get("status") == "EQUAL_ELAPSED":
            detail += "The windows use equal elapsed time, not two complete months. " if english else "比较使用相同已覆盖时长，不是两个完整月份。"
        changes = []
        for row in comparison.get("category_changes", []) if isinstance(comparison.get("category_changes"), list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("fact_ref"), str):
                continue
            field = row["fact_ref"].removeprefix("facts:bill-diagnostics.")
            value = facts.get(field)
            if type(value) is int and type(row.get("change_minor")) is int and value == row["change_minor"] and isinstance(row.get("category"), str):
                category = redact_credentials(row["category"])[:60]
                changes.append(category + ": " + amount(value, english=True) if english else category + "：" + amount(value))
        if changes:
            detail += ("Largest recorded category differences: " if english else "主要类别变化：") + "；".join(changes[:2]) + ". "
        if right == 0:
            detail += "The baseline is zero, so no percentage growth is claimed. " if english else "对照基数为零，不计算增长百分比。"
        intro = ("The comparable records show higher spending." if delta > 0 else "The comparable records show lower spending." if delta < 0 else "Recorded spending is unchanged in the comparable windows.") if english else \
                ("可比记录显示消费增加。" if delta > 0 else "可比记录显示消费减少。" if delta < 0 else "可比区间的已记录消费没有变化。")
        return intro, detail + boundary
    if question == "SAVING_OPTIONS":
        candidates = [fact for fact in data.get("facts", []) if isinstance(fact, dict)
                      and isinstance(fact.get("field"), str) and fact["field"].startswith("review_category_") and fact["field"] in facts]
        labels = []
        for fact in candidates[:3]:
            label = fact.get("label_en" if english else "label")
            if isinstance(label, str):
                labels.append(redact_credentials(label)[:100] + ": " + amount(facts[fact["field"]], english=english))
        detail = ("You can first review these recorded expenses: " if english else "可以先核对这些已记录支出：") + "；".join(labels) if labels else \
                 ("The current records do not provide enough review candidates." if english else "现有资料还不足以列出可核对的支出方向。")
        return ("Here are spending areas to review, not promised savings." if english else "这些是可核对的支出方向，不是保证能省下的钱。",
                cutoff + partial + detail + (" No essential payment or subscription was changed." if english else "没有取消必要支出或订阅。"))
    if question == "CASH_FLOW":
        selected = [("spending_minor", "Spending" if english else "消费"),
                    ("movement_out_minor", "Movement outflows" if english else "资金搬移流出"),
                    ("movement_in_minor", "Movement inflows" if english else "资金搬移流入")]
        detail = "；".join(label + ": " + amount(facts[field], english=english) for field, label in selected if field in facts)
        return ("Spending and money movements are shown separately." if english else "消费和资金搬移已分开核对。",
                cutoff + partial + detail + (". Posted flow is not the current account balance. " if english else "。期间流水净额不是当前账户余额。") + boundary)
    if "spending_minor" in facts:
        return ("The recorded spending overview is ready." if english else "已记录支出的概览已整理好。",
                cutoff + partial + ("Spending in the disclosed coverage: " if english else "披露范围内的消费：")
                + amount(facts["spending_minor"], english=english) + (". Refunds and transfers remain separate. " if english else "。退款与转账仍分别统计。") + boundary)
    return None
