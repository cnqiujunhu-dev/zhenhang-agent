"""Optional model prose around one verified, server-rendered statement fact."""
import os
import re

from .planning import canonical_hash


PHRASE_VERSION = "bill-light-facts-v3"
REMARK_PROTOCOL_VERSION = "bill-light-grounded-single-sentence-v3"
SAFE_CATEGORIES = {"餐饮": "Dining", "购物": "Shopping", "娱乐": "Entertainment", "交通": "Transport",
    "日常": "Everyday essentials", "生活缴费": "Utilities", "订阅": "Subscriptions", "会员": "Memberships"}
PHRASES = {
    "highest": {"zh-CN": "这份{currency}已记录账单里，{category}占比最高（{share}）",
        "en": "In these recorded {currency} expenses, {category} has the highest share ({share})"},
    "lowest": {"zh-CN": "这份{currency}已记录账单里，{category}占比最低（{share}）",
        "en": "In these recorded {currency} expenses, {category} has the lowest share ({share})"},
}
FIXED_FALLBACKS = {
    "highest": {"zh-CN": "，这份账单的主角登场了。", "en": ", taking the spotlight in this statement."},
    "lowest": {"zh-CN": "，这次是账单里的安静配角。", "en": ", a quiet supporting part of this statement."},
}


def _analysis(trace):
    value = trace.get("analysis") if isinstance(trace, dict) else None
    return value if isinstance(value, dict) and value.get("billing_schema_version") == "owned-bill-analysis-v2" else None


def bill_comment_policy(trace):
    """Only v2 gains this deployment policy; old v1 fingerprints stay exact."""
    if _analysis(trace) is None:
        return None
    nodes, outcome = trace.get("nodes"), trace.get("outcome")
    if (trace.get("state") != "SUCCEEDED" or trace.get("actor_role") not in {"CUSTOMER", "DEMO_OWNER", "DEV_TEST"}
            or trace.get("pending_confirmation") or not isinstance(outcome, dict)
            or outcome.get("verified") is not True or outcome.get("kind") != "QUERY_RESULT" or outcome.get("state") != "SUCCEEDED"
            or not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)
            or nodes[0].get("action") != "BILL_ANALYSIS" or nodes[0].get("status") != "SUCCEEDED"
            or not isinstance(nodes[0].get("result"), dict) or nodes[0]["result"].get("error")):
        return None
    style = os.getenv("ZX_BILL_COMMENT_STYLE", "LIGHT").strip().upper()
    if style not in {"FORMAL", "LIGHT", "OFF"}:
        style = "OFF"
    return {"style": style, "phrase_version": PHRASE_VERSION, "remark_protocol_version": REMARK_PROTOCOL_VERSION,
        "phrase_catalog_hash": canonical_hash({"templates": PHRASES, "category_labels": SAFE_CATEGORIES,
            "fixed_fallbacks": FIXED_FALLBACKS})}


def fixed_remark_after_rejected_prose(reply, supplied):
    """Select only the SAME eligible fact; no model text is retained or repaired."""
    policy = supplied.get("bill_comment_policy") or {}
    remark = getattr(reply, "bill_remark", None)
    if policy.get("style") != "LIGHT" or remark is None or supplied.get("needed_questions"):
        return None
    light = {row["fact_id"]: row for row in supplied.get("facts", []) if row.get("selection_group") == "BILL_LIGHT"}
    selected = set(getattr(reply, "fact_refs", ())) & set(light)
    reference = getattr(remark, "fact_ref", None)
    if len(selected) != 1 or reference not in selected:
        return None
    source = light[reference]
    rank = (source.get("source_basis") or {}).get("rank")
    if rank not in FIXED_FALLBACKS:
        return None
    language = "en" if supplied.get("reply_language") == "en" else "zh-CN"
    return reference, source["text"] + FIXED_FALLBACKS[rank][language]


def bill_remark_parts(remark, supplied):
    """Validate prose, never let the model rewrite a category/share/ranking.

    The open prose lane is confined to a short, impersonal statement metaphor.
    It has no financial, comparison, personal-profile or execution authority.
    General narration quantity/authority guards also inspect the returned prose.
    """
    def deny(code):
        raise ValueError("CUSTOMER_REPLY_BILL_REMARK_" + code)
    value = remark.model_dump() if hasattr(remark, "model_dump") else remark
    policy = supplied.get("bill_comment_policy")
    if not isinstance(value, dict) or not isinstance(policy, dict) or policy.get("style") != "LIGHT":
        deny("NOT_ENABLED")
    source = next((item for item in supplied["facts"] if item.get("selection_group") == "BILL_LIGHT"
        and item["fact_id"] == value.get("fact_ref")), None)
    if source is None:
        deny("SOURCE_NOT_PROVIDED")
    template = value.get("template")
    marker = "{{" + source["fact_id"] + "}}"
    if (not isinstance(template, str) or not template.startswith(marker) or template.count(marker) != 1
            or len(template) > 220 or "\n" in template or "\r" in template):
        deny("TEMPLATE_INVALID")
    prose = template[len(marker):].strip()
    if "{{" in prose or "}}" in prose or not prose.strip("，,。.!！:：;； —-"):
        deny("TEMPLATE_INVALID")
    if re.search(r"[。.!?！？]", prose.rstrip("。.!！")) or prose.endswith(("..", "。。", "!!", "！！")):
        deny("MULTIPLE_SENTENCES")
    # No fixed joke catalog: safe prose can vary, but cannot add a new fact.
    if re.search(r"\d|[%％]|[¥￥$]|百分|占比|比例|金额|最高|最低|最多|最少|一半|过半|[一二两三四五六七八九十]成|"
            r"超过|低于|高于|多于|少于|相比|相较|比起|多数|少数|一共|全部|仅有|只有|领先|落后|上涨|下降|增长|下滑|"
            r"\b(?:percent|share|ratio|highest|lowest|more|less|most|least|half|quarter|double|triple|twice|majority|minority|CNY|USD|RMB)\b", prose, re.I):
        deny("ADDED_FACT")
    if any(label.casefold() in prose.casefold() for label in [*SAFE_CATEGORIES, *SAFE_CATEGORIES.values()]):
        deny("ADDED_CATEGORY")
    if re.search(r"你|您|本人|用户|健康|医疗|身体|治病|胖|瘦|穷|负债|债务|败家|抠门|浪费|羞辱|丢人|蠢|傻|笨|人格|自律|"
            r"品味|习惯|性格|情绪|幸福|生活方式|废物|垃圾|恶心|贱|操|滚蛋|"
            r"保证|稳赚|省钱|节省|建议|应该|必须|锁卡|冻结|诈骗|欺诈|取消|付款|余额|额度|利息|收益|还款|"
            r"\b(?:you|your|he|she|their|health|medical|cure|fat|poor|broke|debt|stingy|stupid|idiot|shame|personality|disciplined|"
            r"taste|habit|character|lifestyle|mental|happier|fuck|shit|asshole|bitch|"
            r"guarantee|profit|save|should|must|freeze|fraud|cancel|payment|balance|limit|interest|repay)\b", prose, re.I):
        deny("UNSAFE_PROSE")
    if source["source_basis"]["rank"] == "lowest" and re.search(r"C位|主角|冠军|\b(?:headliner|champion|star)\b", prose, re.I):
        deny("RANK_CONTRADICTION")
    if source["source_basis"]["rank"] == "highest" and re.search(r"垫底|配角|\b(?:supporting\s+role|last\s+place)\b", prose, re.I):
        deny("RANK_CONTRADICTION")
    return source["fact_id"], prose, source["text"] + template[len(marker):]


def _share(amount, total, *, english):
    # Half-up to one decimal place, with explicit bounds for tiny tails.
    tenths = (amount * 1000 + total // 2) // total
    if tenths == 0:
        return "under 0.1%" if english else "不足0.1%"
    if tenths == 1000 and amount < total:
        return "over 99.9%" if english else "超过99.9%"
    return ("about " if english else "约") + f"{tenths // 10}.{tenths % 10}%"


def bill_light_facts(trace, language):
    """Pure display projection after the existing owned narration/source gates.

    No DB read, inference of personal preference, new permission, or action.
    Unknown/filtered/zero/tied scopes do not acquire a guessed champion.
    """
    policy = bill_comment_policy(trace)
    if policy is None or policy["style"] != "LIGHT":
        return []
    from .billing_presentation import owned_bill_display_recorded, owned_bill_fallback
    from .customer_response import _recorded
    nodes, outcome, analysis = trace.get("nodes"), trace.get("outcome"), _analysis(trace)
    if (trace.get("state") != "SUCCEEDED" or trace.get("actor_role") not in {"CUSTOMER", "DEMO_OWNER", "DEV_TEST"}
            or trace.get("pending_confirmation") or not isinstance(outcome, dict)
            or outcome.get("verified") is not True or outcome.get("kind") != "QUERY_RESULT"
            or outcome.get("state") != "SUCCEEDED"
            or not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)
            or nodes[0].get("action") != "BILL_ANALYSIS" or not _recorded(nodes[0], trace)
            or not owned_bill_display_recorded(analysis) or analysis.get("category_filter")
            or analysis.get("period_fallback") and not owned_bill_fallback(analysis)
            or analysis["coverage"]["status"] not in {"COMPLETE", "PARTIAL", "OBSERVED_HISTORY"}):
        return []
    from .billing_diagnostics import _signed_body
    # This optional narration input is built on the private server trace.
    # Public DTO shape alone is not a source proof or a new permission.
    if (not isinstance(analysis["financial_insights"].get("owner_scope_hash"), str)
            or not analysis["financial_insights"]["owner_scope_hash"]
            or analysis["financial_insights"].get("read_fingerprint") != canonical_hash(_signed_body(analysis))):
        return []
    groups = analysis["totals_by_currency"]
    # The compatibility/default currency is CNY. Never compare CNY with USD;
    # use USD only when it is the sole recorded currency.
    totals = next((row for row in groups if row["currency"] == "CNY"), groups[0] if len(groups) == 1 else None)
    if totals is None or totals["economic_spending_minor"] <= 0:
        return []
    categories = {name: amount for name, amount in totals["category_spending_minor"].items() if amount > 0}
    if len(categories) < 2:
        return []
    english = language == "en"
    from .customer_query_layout import semantic_field
    facts = []
    for rank, target in (("highest", max(categories.values())), ("lowest", min(categories.values()))):
        matched = [name for name, amount in categories.items() if amount == target]
        if len(matched) != 1 or matched[0] not in SAFE_CATEGORIES:
            continue
        category = matched[0]
        text = PHRASES[rank]["en" if english else "zh-CN"].format(currency=totals["currency"],
            category=SAFE_CATEGORIES[category] if english else category,
            share=_share(target, totals["economic_spending_minor"], english=english))
        facts.append({"fact_id": "bill_light_" + rank, "label": "Verified statement anchor for one optional model-written light remark",
            "text": text, "required": False, "selection_group": "BILL_LIGHT",
            "semantic_fields": [semantic_field(nodes[0], "category_share", record=category,
                period=[analysis.get("period_start"), analysis.get("period_end")], currency=totals["currency"])],
            "source_basis": {"rank": rank, "currency": totals["currency"], "category": category,
                "category_minor": target, "denominator_minor": totals["economic_spending_minor"],
                "rounding": "ONE_DECIMAL_HALF_UP_WITH_EXPLICIT_TINY_TAIL_BOUND", "phrase_version": PHRASE_VERSION}})
    return facts
