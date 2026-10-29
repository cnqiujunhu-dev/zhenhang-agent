"""Owned, versioned risk-suitability evidence; never transaction authority.

The observation clock remains in the enclosing risk_profile.business_time so
the existing scoped READ projection can ignore that clock, and only that clock.
The snapshot, its source IDs, expiry, validity and hashes remain stable facts.
"""
from copy import deepcopy
from collections import Counter
from datetime import datetime
import re

from .planning import canonical_hash

KEY = "wealth_suitability_snapshot"
VERSION = "wealth-suitability-snapshot-v1"
FACT_VERSION = "wealth-suitability-fact-v1"
PROFILE_POLICY_VERSION = "risk-assessment-v3"
ASPECT = "WEALTH_SUITABILITY"
GROUP_RENDER_VERSION = "wealth-suitability-grouped-render-v1"
AUTHORITY = "OWNED_VERIFIED_RISK_SUITABILITY_NOT_TRADE_AUTHORIZATION"
PROFILE_FIELDS = ("profile_id", "assessment_id", "test_override_event_id", "risk_level", "valid", "status",
    "expires_at", "assessment_source", "questionnaire_version", "assessment_policy_version", "clock")
PRODUCT_FIELDS = ("product_id", "version", "risk_level", "unit_price_minor", "status", "suitable", "suitability_reason_codes")
_SNAPSHOT_FIELDS = {"schema_version", "owner_hash", "profile", "profile_source_hash", "read_only",
    "bank_write_authorized", "snapshot_hash"}
_FACT_FIELDS = {"schema_version", "snapshot", "observed_at", "product", "authority", "bank_write_authorized", "fact_hash"}
_HEX = re.compile(r"[a-f0-9]{64}")


def _time(value):
    if not isinstance(value, str):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    return result


def _identifier(value, *, optional=False):
    return value is None and optional or isinstance(value, str) and 0 < len(value) <= 120


def _profile(value):
    if not isinstance(value, dict) or set(value) != set(PROFILE_FIELDS):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if any(not _identifier(value[key], optional=True) for key in ("profile_id", "assessment_id", "test_override_event_id")) \
            or type(value["valid"]) is not bool or value["status"] not in {"VALID", "REASSESSMENT_REQUIRED", "EXPIRED", "MISSING"} \
            or value["clock"] not in {"CURRENT_UTC", "SIMULATION_BUSINESS_TIME"} \
            or value["assessment_source"] not in {"INDEPENDENT_USER_FORM", "DEV_TEST_OVERRIDE", "SYNTHETIC_SEED", "NONE"} \
            or not _identifier(value["questionnaire_version"], optional=True) \
            or value["assessment_policy_version"] != PROFILE_POLICY_VERSION:
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    level = value["risk_level"]
    if value["profile_id"] is None:
        if level is not None or value["expires_at"] is not None or value["valid"] or value["status"] != "MISSING" \
                or value["assessment_source"] != "NONE" or value["assessment_id"] is not None:
            raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    elif type(level) is not int or not 1 <= level <= 5 or not _time(value["expires_at"]):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if value["valid"] is not (value["status"] == "VALID"):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if value["assessment_source"] == "NONE" and value["profile_id"] is not None:
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if value["assessment_source"] == "SYNTHETIC_SEED" and (value["assessment_id"] is not None
            or value["questionnaire_version"] is not None or value["test_override_event_id"] is not None):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if value["assessment_source"] == "INDEPENDENT_USER_FORM" and (value["assessment_id"] is None or value["questionnaire_version"] is None):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if value["valid"] and value["assessment_source"] == "INDEPENDENT_USER_FORM" \
            and value["questionnaire_version"] != PROFILE_POLICY_VERSION:
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if value["assessment_source"] == "DEV_TEST_OVERRIDE" and value["test_override_event_id"] is None:
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    return deepcopy(value)


def make_snapshot(profile, sandbox_id, user_id):
    """Only the server READ producer calls this with its owned profile view."""
    value = _profile({key: deepcopy(profile.get(key)) for key in PROFILE_FIELDS})
    body = {"schema_version": VERSION, "owner_hash": canonical_hash({"sandbox_id": sandbox_id, "user_id": user_id}),
        "profile": value, "profile_source_hash": canonical_hash(value), "read_only": True, "bank_write_authorized": False}
    return {**body, "snapshot_hash": canonical_hash(body)}


def checked_snapshot(value):
    if not isinstance(value, dict) or set(value) != _SNAPSHOT_FIELDS or value["schema_version"] != VERSION \
            or value["read_only"] is not True or value["bank_write_authorized"] is not False \
            or not isinstance(value["owner_hash"], str) or not _HEX.fullmatch(value["owner_hash"]):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    profile = _profile(value["profile"])
    if value["profile_source_hash"] != canonical_hash(profile) \
            or value["snapshot_hash"] != canonical_hash({key: item for key, item in value.items() if key != "snapshot_hash"}):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    return deepcopy(value)


def checked_profile_snapshot(profile, current, sandbox_id, user_id):
    """Current owner/source/expiry recheck, without changing the saved clock."""
    snapshot = checked_snapshot(profile[KEY])
    if snapshot != make_snapshot(profile, sandbox_id, user_id) or snapshot != make_snapshot(current, sandbox_id, user_id):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    observed = _time(profile["business_time"])
    if observed > _time(current["business_time"]) or snapshot["profile"]["valid"] \
            and observed >= _time(snapshot["profile"]["expires_at"]):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    return snapshot


def make_product_fact(profile, product):
    snapshot = checked_snapshot(profile[KEY])
    body = {"schema_version": FACT_VERSION, "snapshot": snapshot, "observed_at": profile["business_time"],
        "product": {key: deepcopy(product.get(key)) for key in PRODUCT_FIELDS},
        "authority": AUTHORITY, "bank_write_authorized": False}
    result = {**body, "fact_hash": canonical_hash(body)}
    checked_product_fact(result)
    return result


def checked_product_fact(value):
    if not isinstance(value, dict) or set(value) != _FACT_FIELDS or value["schema_version"] != FACT_VERSION \
            or value["authority"] != AUTHORITY or value["bank_write_authorized"] is not False \
            or value["fact_hash"] != canonical_hash({key: item for key, item in value.items() if key != "fact_hash"}):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    snapshot = checked_snapshot(value["snapshot"])
    profile = snapshot["profile"]
    observed = _time(value["observed_at"])
    if profile["valid"] and observed >= _time(profile["expires_at"]):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    if profile["status"] == "EXPIRED" and observed < _time(profile["expires_at"]):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    product = value["product"]
    if not isinstance(product, dict) or set(product) != set(PRODUCT_FIELDS) \
            or not _identifier(product["product_id"]) or type(product["version"]) is not int or product["version"] < 1 \
            or type(product["risk_level"]) is not int or not 1 <= product["risk_level"] <= 5 \
            or type(product["unit_price_minor"]) is not int or not _identifier(product["status"]) \
            or type(product["suitable"]) is not bool or not isinstance(product["suitability_reason_codes"], list):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    reasons = []
    if not profile["valid"]:
        from .wealth_risk_state import suitability_rejection_reason
        reasons.append(suitability_rejection_reason(profile))
    elif product["risk_level"] > profile["risk_level"]:
        reasons.append("PRODUCT_RISK_EXCEEDS_PROFILE")
    if product["status"] != "AVAILABLE" or product["unit_price_minor"] <= 0:
        reasons.append("PRODUCT_UNAVAILABLE")
    if product["suitability_reason_codes"] != reasons or product["suitable"] is not (not reasons):
        raise ValueError("INVALID_WEALTH_SUITABILITY_FACT")
    return deepcopy(value)


def render_product_fact(value, label, language=None):
    try:
        value = checked_product_fact(value)
    except (ValueError, TypeError, KeyError):
        return None
    profile, product = value["snapshot"]["profile"], value["product"]
    english = language == "en"
    risk = f"R{product['risk_level']}"
    if profile["valid"]:
        current = f"R{profile['risk_level']}"
        text = (f"{label}: your current valid risk assessment is {current}; the product is {risk}. " if english else
            f"{label}：你当前有效的风险测评为{current}，本产品风险为{risk}。")
    else:
        state = profile["status"]
        detail = ({"MISSING": "no risk assessment is registered", "EXPIRED": "your risk assessment has expired",
            "REASSESSMENT_REQUIRED": "your risk assessment needs a new independent submission"}.get(state)) if english else (
            {"MISSING": "尚无风险测评", "EXPIRED": "风险测评已到期", "REASSESSMENT_REQUIRED": "风险测评待本人重新独立提交"}.get(state))
        text = f"{label}: {detail}; the product is {risk}. " if english else f"{label}：当前{detail}，本产品风险为{risk}。"
    reasons = product["suitability_reason_codes"]
    if "PRODUCT_RISK_EXCEEDS_PROFILE" in reasons:
        text += ("Its risk exceeds your assessed level, so it is not suitable and cannot be bought directly. " if english else
            "产品风险超过你当前测评等级，不适配，不能直接买入。")
    elif not profile["valid"]:
        text += ("Current suitability is not established; complete your own assessment before a purchase. " if english else
            "尚不能确认当前风险适配，买入前须由你本人完成有效测评。")
    elif product["suitable"]:
        text += ("The current risk and product checks allow further purchase checks, not a completed purchase. " if english else
            "当前风险与产品适配核验通过，仅可继续交易核验，不代表已买入。")
    if "PRODUCT_UNAVAILABLE" in reasons:
        text += ("The product is unavailable for purchase. " if english else "产品当前不可办理买入。")
    if product["status"] == "AVAILABLE":
        text += ("An available product is not automatically suitable for you. " if english else "产品状态“可用”不等于适合你或可以直接购买。")
    text += ("Any purchase still needs current amount, emergency-reserve, price and funding-account checks and your confirmation; returns are not guaranteed." if english else
        "实际买入仍须核对金额、应急金、价格与资金账户，并由你本人确认；不保本、不保证收益。")
    return text


def render_product_group(items, language=None):
    """Display only one complete, already selected same-READ fact group.

    Any ineligible item makes the whole group use its original renderer. This
    function does not read a question, source catalog, principal or database,
    and neither creates facts nor changes the caller's fact/source references.
    """
    if not isinstance(items,list) or not 2<=len(items)<=40:return None
    checked=[];contexts=set();products=set()
    try:
        for item in items:
            if not isinstance(item,dict) or set(item)!={'label','result_ref','record_version','suitability'} \
                    or not _identifier(item['label']) or not _identifier(item['result_ref']):return None
            value=checked_product_fact(item['suitability']);snapshot=value['snapshot'];profile=snapshot['profile'];product=value['product']
            if profile['valid'] is not True or profile['status']!='VALID' or product['status']!='AVAILABLE' \
                    or product['suitability_reason_codes'] not in ([],['PRODUCT_RISK_EXCEEDS_PROFILE']) \
                    or type(item['record_version']) is not int or item['record_version']!=product['version'] \
                    or product['product_id'] in products:return None
            products.add(product['product_id'])
            contexts.add((snapshot['owner_hash'],snapshot['profile_source_hash'],snapshot['snapshot_hash'],
                value['observed_at'],item['result_ref']))
            checked.append((item['label'],value))
    except (ValueError,TypeError,KeyError,AttributeError):
        return None
    if len(contexts)!=1:return None
    english=language=='en';profile=checked[0][1]['snapshot']['profile']
    lines=[(f"Your current valid risk assessment is R{profile['risk_level']}." if english else
        f"你当前有效的风险测评为R{profile['risk_level']}。")]
    def bands(rows):
        grouped={}
        for label,value in rows:
            grouped.setdefault(value['product']['risk_level'],[]).append(label)
        return ('; ' if english else '；').join(
            ('R'+str(level)+': '+', '.join(grouped[level])) if english else
            ('R'+str(level)+'—'+'、'.join(grouped[level])) for level in sorted(grouped,reverse=True))
    unsuitable=[row for row in checked if row[1]['product']['suitable'] is False]
    suitable=[row for row in checked if row[1]['product']['suitable'] is True]
    if unsuitable:
        lines.append(('Not suitable; cannot be bought directly: '+bands(unsuitable)+'. These risks exceed your assessed level.' if english else
            '不适配，不能直接买入：'+bands(unsuitable)+'。这些产品风险高于你的测评等级。'))
    if suitable:
        lines.append(('Risk-suitability checks passed; further transaction checks are still required: '+bands(suitable)+'.' if english else
            '风险适配核验通过，可继续交易核验：'+bands(suitable)+'。'))
    lines.append(('Product availability is not personal suitability or permission to buy directly. Any purchase still needs current amount, '
        'emergency-reserve, price and funding-account checks and your own confirmation; principal and returns are not guaranteed.' if english else
        '产品状态“可用”不等于个人适配或可以直接购买。实际买入仍须核对金额、应急金、价格与资金账户，并由你本人确认；不保本、不保证收益。'))
    return '\n'.join(lines)


def covered_wealth_rules(records, contents, typed_facts, registered_policy):
    """Exact current policy + complete per-object facts; not an action-name test."""
    if registered_policy is None or registered_policy not in records or not contents \
            or any(content.get("domain") != "WEALTH" for content in contents):
        return set()
    # Multiple product entities normally share one READ result_ref. Preserve
    # the object label/version multiplicity, not merely the result identifier.
    expected = [(content["result_ref"], content["label"], content["record_version"]) for content in contents]
    actual = []
    sources = []
    for fact in typed_facts.values():
        value = fact.get("value") or {}
        if value.get("aspect") != ASPECT:
            continue
        try:
            checked_product_fact(value["fields"]["suitability"])
        except (ValueError, TypeError, KeyError):
            return set()
        actual.append((value.get("result_ref"), value.get("label"), value.get("record_version")))
        sources.append(fact.get("source_id"))
    return {"RISK", "ELIGIBILITY"} if Counter(actual) == Counter(expected) and len(set(sources)) == len(expected) else set()
