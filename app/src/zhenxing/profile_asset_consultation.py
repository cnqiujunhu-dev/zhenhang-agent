"""Finite source-bound relation facts, not general-rule or numeric authority."""
from datetime import datetime

from .planning import canonical_hash

VERSION = "registered-profile-asset-read-rules-20261022-v1"
TIER = "PROFILE_TIER_AXES"
ASSET = "ASSET_VALUATION_SCOPE"
_POLICIES = {
    TIER: ("profile.tier-axes-v1", "客户等级、卡等级与风险测评的区别",
        "本服务中的客户等级、卡等级和理财风险测评分别登记，不是同一个等级，也不互相换算。客户等级是客户服务分层，卡等级属于具体卡片，风险测评用于判断理财产品是否适配；实际等级、状态和测评有效性须读取本人记录。客户等级或卡等级不能代替有效风险问卷，也不代表授信审批通过。",
        "Customer tier, card tier and investment risk assessment are separate registered dimensions in this service, not interchangeable levels. Customer tier is a service classification; card tier belongs to a particular card; risk assessment supports investment suitability. Actual levels, status and assessment validity come from the holder's records. A customer or card tier cannot replace a valid risk assessment or prove credit approval."),
    ASSET: ("assets.valuation-scope-v1", "持仓数量、市值与价格时点",
        "持仓数量、参考价格和参考市值是不同口径。股票股数与贵金属克数不能相加。只有币种一致、价格时点已明确且一致的已核验参考市值，才可按该时点形成参考合计；不同币种不能直接相加，不同时点或缺少价格时点不能当作同一时点总资产。登记快照不是实时行情，也不是可成交报价；参考市值不能代替未登记的本金成本或收益。",
        "Position quantity, reference price and reference market value are different measures. Stock shares and metal grams cannot be added. Verified reference market values can form a reference total only in the same currency and at the same explicitly recorded price time. Do not directly add currencies or present different or missing price times as one as-of total. Registered snapshots are not live prices or executable quotes. Reference value cannot replace missing cost basis or prove profit."),
}


def registered_policy(aspect):
    identifier, title, chinese, english = _POLICIES[aspect]
    return {"source_id": "kb:" + identifier, "title": title, "content": chinese,
        "title_en": ("Customer, card and risk dimensions" if aspect == TIER else "Position valuation scope"),
        "content_en": english, "kind": "PRODUCT_KNOWLEDGE", "reference": "product_knowledge:" + identifier,
        "version": VERSION}


def public_policies(topics):
    """Model-selected registered topic keys, never sentence matching."""
    keys = {str(topic).upper().replace("-", "_").replace(".", "_") for topic in topics}
    aspects = []
    if keys & {"WEALTH", "INVESTMENT", "CARD", "CARDS", "GENERAL", "CAPABILITIES"}: aspects.append(TIER)
    # A model can correctly classify stock/metal valuation as an investment
    # consultation rather than GENERAL. Keep this exact registered read policy
    # reachable from those two semantic topics too; unrelated topics stay out.
    if keys & {"WEALTH", "INVESTMENT", "GENERAL", "CAPABILITIES"}: aspects.append(ASSET)
    return [registered_policy(aspect) for aspect in aspects]


def _policy_present(records, aspect):
    expected = registered_policy(aspect)
    rows = [row for row in records if row.get("source_id") == expected["source_id"]]
    return len(rows) == 1 and all(rows[0].get(key) == value for key, value in expected.items())


def _make_fact(aspect, fields, selected):
    from .dialogue_result_aspects import UNIT, AUTHORITY
    sources = [row["source_id"] for row, _ in selected]
    policy = registered_policy(aspect)["source_id"]
    identifier = "facts:profile-asset-relation." + canonical_hash({"aspect": aspect, "sources": sources})[:24]
    return {"fact_id": identifier, "source_id": sources[0], "source_ids": [*sources, policy],
        "field": aspect, "unit": UNIT, "currency": "NONE", "required": True,
        "label": "等级口径" if aspect == TIER else "估值口径",
        "value": {"authority": AUTHORITY, "aspect": aspect, "fields": fields,
            "bank_state_changed": False, "rule_version": VERSION}}


def scoped_relation_facts(records, requested_rules):
    """Publish only the selected, proven relation requested as TERMS or rules.

    Publication still revalidates each selected source against the owned READ.
    Other domains, mixed questions, fees/eligibility/returns or altered policy
    cannot acquire coverage from a generic rule word or an untrusted paragraph.
    """
    from .dialogue_reference_facts import _registered_content, is_selected_source_id
    selected = [(row, _registered_content(row)) for row in records if is_selected_source_id(row.get("source_id"))]
    rules = set(requested_rules)
    if rules not in (set(), {"GENERAL"}) or not selected or any(content is None for _, content in selected):
        return {}, set()
    if any(not isinstance(content.get("domain"), str) for _, content in selected): return {}, set()
    domains = {content["domain"] for _, content in selected}
    if domains == {"MEMBERSHIP"} and _policy_present(records, TIER):
        # The relation is already a registered fact of these two dimensions.
        # TERMS does not need to masquerade as a generic RULES request. Require
        # its explicit source-bound aspect on every selected object, however;
        # merely querying two current levels must not inject a comparison.
        terms_requested = all("TERMS" in (row.get("source_binding") or {}).get("question_aspects", []) for row, _ in selected)
        if not rules and not terms_requested: return {}, set()
        fields = [{fact["field"]: fact["value"] for fact in content["facts"]} for _, content in selected]
        axis_fields = [entity.get("field") for row, _ in selected
            for entity in row["source_binding"]["catalog"]["entities"]
            if entity.get("entity_ref") == row["source_binding"]["resolution"]["entity_ref"]]
        if any(not isinstance(field, str) for field in axis_fields): return {}, set()
        axes = set(axis_fields)
        # Same verified membership read, not unrelated cached owners or dates.
        # A single level's unrelated GENERAL question is not a request to
        # compare dimensions. Require the semantic decision's distinct axes.
        if not axes <= {"customer_tier", "card_tier", "risk_profile"} or len(axes) < 2 \
                or len({content["result_ref"] for _, content in selected}) != 1 \
                or not all(row.get("tier_axes_independent") is True for row in fields):
            return {}, set()
        fact = _make_fact(TIER, {"tier_axes_independent": True}, selected)
        return {fact["fact_id"]: fact}, {"GENERAL"} if rules else set()
    asset_scope_requested = all({"VALUATION", "TIME_SCOPE"} <= set(
        (row.get("source_binding") or {}).get("question_aspects", [])) for row, _ in selected)
    # These explicitly selected valuation and as-of facts determine their
    # aggregation scope without an invented RULES tag. Quantity/status-only
    # selections do not request it; public policy and every original object
    # proof remain mandatory, including in the non-RULES path.
    if (rules == {"GENERAL"} or not rules and asset_scope_requested) \
            and domains == {"ASSETS"} and len(selected) > 1 and _policy_present(records, ASSET):
        positions = []
        identities = set()
        for row, content in selected:
            fields = {fact["field"]: fact["value"] for fact in content["facts"]}
            entity = next((item for item in row["source_binding"]["catalog"]["entities"]
                if item["entity_ref"] == row["source_binding"]["resolution"]["entity_ref"]), {})
            if entity.get("field") != "product_name" or not isinstance(entity.get("record_id"), str) or not entity["record_id"] \
                    or entity["record_id"] in identities or not isinstance(fields.get("currency"), str) \
                    or fields["currency"] not in {"CNY", "USD"} or not isinstance(fields.get("quantity_unit"), str) \
                    or fields["quantity_unit"] not in {"UNIT", "SHARE", "GRAM"}:
                return {}, set()
            market, observed = fields.get("market_value_minor"), fields.get("price_observed_at")
            if "market_value_minor" not in fields or market is not None and (
                    type(market) is not int or not 0 <= market <= 2**53-1) \
                    or "price_observed_at" not in fields or observed is not None and not isinstance(observed, str):
                return {}, set()
            if observed is not None:
                try:
                    if datetime.fromisoformat(observed.replace("Z", "+00:00")).tzinfo is None: return {}, set()
                except ValueError: return {}, set()
            identities.add(entity["record_id"])
            positions.append({"currency": fields["currency"], "quantity_unit": fields["quantity_unit"],
                "price_observed_at": fields.get("price_observed_at"), "market_value_minor": fields.get("market_value_minor"),
                "price_is_live": fields.get("price_is_live")})
        fact = _make_fact(ASSET, {"positions": positions}, selected)
        return {fact["fact_id"]: fact}, {"GENERAL"} if rules else set()
    return {}, set()


def render_relation(fact, language=None):
    from .dialogue_result_aspects import UNIT, AUTHORITY
    value = fact.get("value")
    if fact.get("unit") != UNIT or not isinstance(value, dict) or value.get("authority") != AUTHORITY \
            or value.get("bank_state_changed") is not False or value.get("rule_version") != VERSION:
        return None
    fields = value.get("fields")
    if not isinstance(fields, dict): return None
    english = language == "en"
    if value.get("aspect") == TIER and fields == {"tier_axes_independent": True}:
        return ("No. Customer tier, the tier of each card and investment risk assessment are separate dimensions. "
            "Customer tier does not replace a valid risk assessment or imply a higher risk tolerance or approved credit."
            if english else "不是一回事。客户等级、每张卡的卡等级和理财风险测评分开登记，不互相换算。客户等级不能代替有效风险测评，也不代表更能承受投资风险或已获批授信。")
    if value.get("aspect") != ASSET or not isinstance(fields.get("positions"), list) or len(fields["positions"]) < 2:
        return None
    positions = fields["positions"]
    if any(not isinstance(row, dict) or row.get("currency") not in {"CNY", "USD"} for row in positions): return None
    same_currency = len({row["currency"] for row in positions}) == 1
    times = []
    for row in positions:
        try:
            moment = datetime.fromisoformat(row["price_observed_at"].replace("Z", "+00:00"))
            times.append(moment if moment.tzinfo else None)
        except (ValueError, TypeError, KeyError, AttributeError): times.append(None)
    same_time = all(moment is not None for moment in times) and len(set(times)) == 1
    values_known = all(type(row.get("market_value_minor")) is int and 0 <= row["market_value_minor"] <= 2**53-1 for row in positions)
    if not same_currency:
        text = "These values use different currencies, so they cannot be added directly." if english else "这些记录的币种不同，不能直接相加。"
    elif not same_time:
        text = ("The currencies match, but the recorded price times differ or are missing. Do not treat their sum as one as-of asset total."
            if english else "这些记录的币种一致，但价格时点不同或没有完整登记，不能直接当成同一时点的资产合计。")
    elif not values_known:
        text = "Some reference market values are unavailable, so a complete total cannot be verified." if english else "部分参考市值缺失，暂时不能核出完整合计。"
    else:
        text = ("The verified reference values have the same currency and recorded price time, so they may form a reference total at that time."
            if english else "这些已核验参考市值的币种和价格时点一致，可以按该时点做参考合计。")
    return text + (" Stock shares and metal grams remain separate quantities. These records are not proof of live or executable prices, cost basis or profit."
        if english else "股票的股数和贵金属的克数仍须分开；这些记录不代表实时成交报价，也不能据此补出本金成本或收益。")


def compact_tier_aspects(facts, relations):
    """Drop only empty/repeated membership facets after the relation is proven.

    Keep every original source record and real status value. This is not a
    catch-all unknown-field suppression or a rewrite of the selected evidence.
    """
    relation = next((fact for fact in relations.values() if fact.get("field") == TIER), None)
    if relation is None: return facts
    sources = set(relation["source_ids"])
    result = {}
    for key, fact in facts.items():
        value = fact.get("value", {})
        fields = value.get("fields")
        if fact.get("source_id") in sources and value.get("domain") == "MEMBERSHIP" \
                and value.get("aspect") in {"STATUS", "TERMS"} and isinstance(fields, dict):
            if not fields: continue
            if value["aspect"] == "TERMS" and any(other.get("source_id") == fact["source_id"]
                    and other.get("value", {}).get("aspect") == "STATUS"
                    and all(other["value"].get("fields", {}).get(name) == item for name, item in fields.items())
                    for other in facts.values()):
                continue
        result[key] = fact
    return result
