"""Code-reviewed capabilities, not an open-ended model/tool discovery mechanism."""
from .intelligence import ACTION_LABELS, READ_KINDS, domain_for_action

CATALOG_VERSION = "capability-catalog-20261018-credit-life-v6"
EXTENSION_INTENTS = {
    "FRAUD": "FRAUD_ANALYSIS", "FAMILY": "FAMILY_ASSISTANCE",
    "GOALS": "GOAL_CASHFLOW", "CARBON": "CARBON_BENEFITS",
}
ACTION_TO_TOOL = {
    "TRANSFER": "transfer.commit.v1", "TRANSFER_SCHEDULE": "transfer.schedule.v1", "AA_REQUEST": "transfer.aa.v1",
    "BILL_ANALYSIS": "bill.analyze.v1", "CARD_STATUS": "card.status.v1",
    **{kind: "card.manage.v1" for kind in ("CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT")},
    "WEALTH_COMPARE": "wealth.compare.v1", "WEALTH_BUY": "wealth.order.v1", "WEALTH_REDEEM": "wealth.order.v1",
    "ASSET_POSITIONS_READ": "asset.positions.v1", "MEMBERSHIP_VIEW": "membership.view.v1",
    "BENEFITS_VIEW": "benefits.view.v1", "PREFERENCE_VIEW": "preference.view.v1",
    "PREFERENCE_SET": "preference.set.v1", "PREFERENCE_REMOVE": "preference.remove.v1",
    "SUBSCRIPTION_LIST": "subscription.list.v1", "SUBSCRIPTION_CANCEL": "subscription.cancel.v1",
    "SUBSCRIPTION_START": "subscription.start.v1", "SUBSCRIPTION_RENEW": "subscription.renew.v1",
    "FRAUD_ANALYZE": "fraud.analyze.v1", "FAMILY_VIEW": "family.view.v1",
    "FAMILY_GRANT": "family.grant.v1", "FAMILY_REVOKE": "family.revoke.v1", "FAMILY_PROPOSE": "family.propose.v1",
    "GOAL_VIEW": "goal.view.v1", "GOAL_CREATE": "goal.create.v1", "GOAL_RESERVE": "goal.reserve.v1", "GOAL_RELEASE": "goal.release.v1",
    "CARBON_VIEW": "carbon.view.v1", "CARBON_CLAIM": "carbon.claim.v1", "CARBON_REDEEM": "carbon.redeem.v1",
    "FX_QUOTE": "fx.quote.v1", "FX_TRANSFER": "fx.transfer.v1", "FX_EXCHANGE": "fx.exchange.v1",
    "CONTACT_BIND": "contact.bind.v1",
}


from .upgraded_capabilities import CAPABILITIES as UPGRADED_CAPABILITIES
ACTION_TO_TOOL.update({kind: value[3] for kind, value in UPGRADED_CAPABILITIES.items()})


def action_intent(kind: str) -> str:
    if kind in UPGRADED_CAPABILITIES:
        return UPGRADED_CAPABILITIES[kind][2]
    if kind == "ASSET_POSITIONS_READ":
        return "WEALTH"
    if kind in {"MEMBERSHIP_VIEW", "BENEFITS_VIEW"}:
        return "CUSTOMER_BENEFITS"
    if kind.startswith("PREFERENCE_"):
        return "CUSTOMER_PREFERENCES"
    if kind == "CONTACT_BIND":
        return "CONTACT_MANAGEMENT"
    if kind in {"FX_QUOTE", "FX_TRANSFER", "FX_EXCHANGE"}:
        return {"FX_QUOTE": "FOREX_QUERY", "FX_TRANSFER": "FOREX_TRANSFER", "FX_EXCHANGE": "FOREIGN_EXCHANGE"}[kind]
    domain = domain_for_action(kind)
    if domain in EXTENSION_INTENTS:
        return EXTENSION_INTENTS[domain]
    return {"TRANSFER": "TRANSFER", "TRANSFER_SCHEDULE": "TRANSFER_SCHEDULE", "AA_REQUEST": "AA_REQUEST",
            "BILL_ANALYSIS": "BILL_ANALYSIS"}.get(kind, {"CARDS": "CARD_MANAGEMENT", "WEALTH": "WEALTH",
                                                         "SUBSCRIPTIONS": "SUBSCRIPTION_MANAGEMENT"}.get(domain, "UNSUPPORTED"))


def catalog(role: str = "DEMO_OWNER") -> dict:
    from .platform import TOOL_REGISTRY
    entries = [{"kind": kind, "label": label, "domain": domain_for_action(kind), "tool": ACTION_TO_TOOL[kind],
                "read_only": kind in READ_KINDS, "tool_version": "v1", "requires_confirmation": kind not in READ_KINDS}
               for kind, label in ACTION_LABELS.items()
               if role != "ADMIN" and ACTION_TO_TOOL.get(kind) in TOOL_REGISTRY and (role != "FAMILY_HELPER" or kind in {"FAMILY_VIEW", "FAMILY_PROPOSE"})]
    return {"version": CATALOG_VERSION, "composition": "MULTI_LABEL_DOMAINS", "capabilities": entries,
            "cross_domain_is_composition": True, "arbitrary_external_tools_enabled": False}


def customer_directory(role: str = "DEMO_OWNER", locale: str = "zh-CN") -> list[dict]:
    """Customer-language service index, intersected with actual role/tool scope.

    Descriptions can support discovery and retrieval; they cannot grant access,
    supply banking facts or authorize an action. Unknown roles fail closed.
    """
    from .customer_capability_data import CUSTOMER_SERVICES, CUSTOMER_DIRECTORY_VERSION, FAMILY_HELPER_DESCRIPTION
    if role not in {"DEMO_OWNER", "CUSTOMER", "DEV_TEST", "FAMILY_HELPER"}:
        return []
    language = "en" if locale == "en" else "zh-CN"
    allowed = {entry["kind"]: entry for entry in catalog(role)["capabilities"]}
    result = []
    for service in CUSTOMER_SERVICES:
        kinds = [kind for kind in service["actionKinds"] if kind in allowed]
        if service["id"] == "composition":
            # Composition describes combining already-available services, never
            # a role escalation or an independent, executable capability.
            if role == "FAMILY_HELPER" or len({entry["domain"] for entry in allowed.values()}) < 2:
                continue
            kinds = list(allowed)
        elif not kinds:
            continue
        wording = FAMILY_HELPER_DESCRIPTION[language] if role == "FAMILY_HELPER" and service["id"] == "family" else service[language]
        result.append({"id": service["id"], "version": CUSTOMER_DIRECTORY_VERSION,
                       "title": wording["title"], "description": wording["description"],
                       "prompts": list(wording["prompts"]),
                       "actionKinds": kinds, "boundaries": list(wording["boundaries"]),
                       "neededFields": list(wording["neededFields"]),
                       "toolRefs": sorted({allowed[kind]["tool"] for kind in kinds}),
                       "availability": "AVAILABLE", "locale": language,
                       "read_only": all(kind in READ_KINDS for kind in kinds),
                       "requires_confirmation": any(kind not in READ_KINDS for kind in kinds)})
    return result


def suggestion_catalog(role: str = "DEMO_OWNER", locale: str = "zh-CN") -> dict[str, dict]:
    """Allowlisted suggestion IDs, not model-authored prompts or action slots."""
    from .customer_capability_data import CUSTOMER_SUGGESTIONS
    if role not in {"DEMO_OWNER", "CUSTOMER", "DEV_TEST", "FAMILY_HELPER"}:
        return {}
    allowed = {entry["kind"] for entry in catalog(role)["capabilities"]}
    language = "en" if locale == "en" else "zh-CN"
    result = {}
    for identifier, capability_id, kind, behavior, chinese, english in CUSTOMER_SUGGESTIONS:
        if kind not in allowed:
            continue
        read_only = kind in READ_KINDS
        # Registry changes must never silently turn an automatic query into a
        # write. Use conservative fill-only behavior if classification changes.
        if behavior == "SEND_READ_ONLY" and not read_only:
            behavior = "FILL_COMPOSER"
        prompt = english if language == "en" else chinese
        result[identifier] = {"id": identifier, "label": prompt, "prompt": prompt,
                              "behavior": behavior, "capability_id": capability_id,
                              "read_only": read_only}
    return result
