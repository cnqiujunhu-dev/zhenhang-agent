"""Publication recheck and typed read-result entities for natural consultation."""
from copy import deepcopy
from datetime import datetime

from .contracts import DomainError
from .planning import canonical_hash
from .customer_benefits import benefits_view
from .customer_membership import membership_view
from .customer_preferences import preferences_view
from .customer_profile_common import READ_ACTIONS, SCHEMA_VERSION, read_body_fingerprint


def profile_read_result_current(session, principal, action, result):
    """Rebuild under the current owner; no models, no mutation or alternate source."""
    try:
        if (action not in READ_ACTIONS or not isinstance(result, dict)
                or result.get("schema_version") != SCHEMA_VERSION or result.get("action") != action
                or result.get("bank_state_changed") is not False
                or not datetime.fromisoformat(result["observed_at"]).tzinfo
                or result.get("read_fingerprint") != read_body_fingerprint(result)):
            return False
        query = result.get("query") or {}
        if action == "MEMBERSHIP_VIEW":
            fresh = membership_view(session, principal)
        elif action == "BENEFITS_VIEW":
            fresh = benefits_view(session, principal, benefit_name=query.get("benefit_name"))
        else:
            fresh = preferences_view(session, principal, preference_kind=query.get("preference_kind"))
        return fresh["read_fingerprint"] == result["read_fingerprint"]
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return False


def profile_result_fact_entities(action, result):
    """Call only after profile_read_result_current in authenticated server context.

    Compatible with dialogue_references' internal entity shape. Raw record IDs
    stay server-side. These fields grant no writable target or finance authority.
    """
    if action not in READ_ACTIONS or result.get("read_fingerprint") != read_body_fingerprint(result):
        return []
    domain = {"MEMBERSHIP_VIEW": "MEMBERSHIP", "BENEFITS_VIEW": "BENEFITS", "PREFERENCE_VIEW": "PREFERENCES"}[action]
    entities = []
    def add(field, value, label, facts, version=None, ordinal=None):
        body = deepcopy(facts)
        body["facts_authority"] = "INFERRED_SUGGESTION_NOT_USER_PREFERENCE" if field == "preference_suggestion" else "OWNED_REGISTERED_PROFILE"
        entities.append({"read_kind": action, "domain": domain, "field": field, "value": value,
            "label": label, "ordinal": ordinal, "record_id": canonical_hash({"field": field, "value": value})[:36],
            "record_version": version, "live_record_hash": canonical_hash(body), "facts": body})
    if action == "MEMBERSHIP_VIEW":
        member = result["membership"]
        # Carry the server's existing relation flag into the selected-object
        # proof. It is not inferred from an equal-looking tier/risk number.
        axes = {"tier_axes_independent": result.get("tier_axes_independent")}
        add("customer_tier", member.get("tier_code") or "UNREGISTERED", "我的客户等级", {**member, **axes}, member.get("version"))
        for index, card in enumerate(result.get("cards", []), 1):
            add("card_tier", card["last4"], "尾号" + card["last4"] + "的卡等级",
                {**{key: value for key, value in card.items() if key != "card_id"}, **axes}, card.get("tier_version"), index)
        add("risk_profile", "我的风险测评", "我的风险测评", {**result["risk_profile"], **axes})
    elif action == "BENEFITS_VIEW":
        for index, activity in enumerate(result.get("activities", []), 1):
            add("benefit_name", activity["name"], activity["name"], activity, activity.get("rule_version"), index)
    else:
        scope_label={"TRAVEL":"我当前保存的出行偏好","CONSUMPTION":"我当前保存的消费偏好"}.get(
            (result.get("query") or {}).get("preference_kind"),"我当前保存的偏好")
        add("preference_collection", "我的偏好", scope_label, {
            "active_preferences": [{key: pref[key] for key in ("kind", "kind_label", "value", "version")}
                                   for pref in result.get("preferences", [])],
            "retracted_kinds": result.get("retracted_kinds", []),
            "inferred_suggestions_are_active_preferences": False})
        for pref in result.get("preferences", []):
            add("preference_kind", pref["kind"], pref["kind_label"], {**pref, "confirmed": True}, pref.get("version"))
        for item in result.get("suggestions", []):
            add("preference_suggestion", item["kind"] + ":" + item["value"], "未确认的偏好建议", item)
    return entities
