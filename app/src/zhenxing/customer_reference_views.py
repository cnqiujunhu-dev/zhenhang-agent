"""Pure read-result reference displays; never a quote, grant or ledger rewrite."""
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
import re

from .contracts import DomainError
from .fx_rates import validate_market_rate


def add_spending_reference(view):
    from .intelligence import READ_KINDS, _intent_text
    from .language import english_spending_reference
    message = _intent_text(view.get("message", ""))
    # Ordinary quoted English is often a sentence to explain, not a request.
    message = re.sub(r"\"[^\"]*\"|(?<![A-Za-z0-9])'[^']*'(?![A-Za-z0-9])", "", message)
    if re.search(r"\b(?:translate|meaning|what\s+does|sentence|example|quoted|tutorial)\b", message, re.I):
        return
    requested = english_spending_reference(message) or bool(re.search(r"(?:消费|支出|账单).{0,30}(?:按美元|美元显示|美元参考|美元折算|折算.*美元)", message))
    if not requested:
        return
    analysis = view.get("analysis")
    if not isinstance(analysis, dict):
        return
    unavailable = {"status": "UNAVAILABLE", "reference_only": True, "currency": "USD", "original_currency": "CNY",
                   "value_minor": None, "original_minor": None, "reason": "VERIFIED_COVERAGE_AND_RATE_REQUIRED"}
    analysis["reference_display"] = unavailable
    coverage = analysis.get("coverage")
    nodes = view.get("nodes", [])
    if view.get("state") != "SUCCEEDED" or view.get("pending_confirmation") is not None \
            or (view.get("outcome") or {}).get("verified") is not True or not nodes \
            or any(node.get("action") not in READ_KINDS or node.get("status") != "SUCCEEDED" or (node.get("result") or {}).get("error") for node in nodes) \
            or not any(node.get("action") == "BILL_ANALYSIS" for node in nodes):
        return
    if not isinstance(coverage, dict) or coverage.get("status") not in {"COMPLETE", "PARTIAL", "OBSERVED_HISTORY"} \
            or coverage.get("basis") in {"LIVE_ACTIVITY_ONLY", "HISTORY_AND_LIVE_ACTIVITY"}:
        return
    total = analysis.get("spending_minor")
    if type(total) is not int or not 0 <= total <= 9007199254740991:
        return
    rate_node = next((node for node in nodes if node.get("action") == "FX_QUOTE"), None)
    if rate_node is None:
        return
    market = (rate_node.get("result") or {}).get("market_rate")
    if not isinstance(market, dict) or market.get("base_currency") != "USD" or market.get("quote_currency") != "CNY" \
            or market.get("provider") not in {"FRANKFURTER_ECB", "TEST_STUB"}:
        return
    observed = view.get("result_observed_at") or view.get("updated_at")
    try:
        moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return
        verified = validate_market_rate(market, now=moment)
        estimate = int((Decimal(total)/Decimal(verified["rate"])).to_integral_value(rounding=ROUND_FLOOR))
    except (DomainError, ValueError, TypeError, AttributeError):
        return
    if not 0 <= estimate <= 9007199254740991:
        return
    analysis["reference_display"] = {"status": "AVAILABLE", "reference_only": True,
        "currency": "USD", "original_currency": "CNY", "value_minor": estimate, "original_minor": total,
        "rate": verified["rate"], "provider": verified["provider"], "market_date": verified["market_date"],
        "fetched_at": verified["fetched_at"], "source_url": verified["source_url"],
        "rounding": "REFERENCE_MINOR_FLOOR", "fees_included": False, "executable": False,
        "bank_state_changed": False, "rate_basis": "RECORDED_QUERY_SNAPSHOT_NOT_GUARANTEED_CURRENT_PRICE"}


def reference_summary(analysis, *, english=False):
    from .customer_financial_narrative import amount
    if not isinstance(analysis, dict):
        return ""
    ref = analysis.get("reference_display")
    if not isinstance(ref, dict) or ref.get("status") != "AVAILABLE" or ref.get("reference_only") is not True \
            or ref.get("currency") != "USD" or ref.get("original_currency") != "CNY" \
            or type(ref.get("original_minor")) is not int or ref["original_minor"] != analysis.get("spending_minor") \
            or type(ref.get("value_minor")) is not int or ref["value_minor"] < 0:
        return ""
    value = amount(ref["value_minor"], "USD", english)
    return (f" Approximate spending equivalent: {value}, using the recorded rate. Original CNY records remain unchanged; issuer and channel fees are not included, and no exchange was executed."
            if english else f" 原人民币消费按记录汇率参考折算约{value}；未计发卡行或渠道费用，原账务不变，没有执行换汇。")
