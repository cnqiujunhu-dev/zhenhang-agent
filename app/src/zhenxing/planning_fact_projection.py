"""Purpose-limited outbound facts, never an intent or authorization compiler.

The existing collector still performs its bounded owner-scoped reads. This
projection reduces the model payload only. Incomplete or unresolved requests
retain the previous bounded context instead of inferring missing permissions,
amounts, targets or a reduced capability list.
"""
from .capability_catalog import ACTION_TO_TOOL
from .intelligence import _clauses, _intent_text, rule_proposal
from .platform import TOOL_REGISTRY


PROJECTION_VERSION = "planning-outbound-purpose-v1"
BASE_KEYS = frozenset({"version", "authority", "actor_role", "available_actions", "capability_catalog_version"})
MONEY_KEYS = frozenset({"cash", "cards", "currencies", "protected_budgets", "goals"})
RECIPIENT_KEYS = frozenset({"known_payees", "personal_recipient_aliases"})
ACTION_FACT_KEYS = {
    "BILL_ANALYSIS": frozenset({"cash", "cards", "subscriptions", "currencies"}),
    **{kind: frozenset({"cards", "currencies"}) for kind in (
        "CARD_STATUS", "CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT")},
    **{kind: MONEY_KEYS | RECIPIENT_KEYS for kind in ("TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST")},
    "WEALTH_COMPARE": frozenset({"risk_profile", "currencies"}),
    **{kind: MONEY_KEYS | {"risk_profile"} for kind in ("WEALTH_BUY", "WEALTH_REDEEM")},
    **{kind: frozenset({"subscriptions", "currencies"}) for kind in ("SUBSCRIPTION_LIST", "SUBSCRIPTION_CANCEL")},
    **{kind: MONEY_KEYS | {"subscriptions"} for kind in ("SUBSCRIPTION_START", "SUBSCRIPTION_RENEW")},
    "FRAUD_ANALYZE": RECIPIENT_KEYS | {"cards", "currencies"},
    "FAMILY_VIEW": frozenset({"family_helpers"}),
    "FAMILY_GRANT": MONEY_KEYS | {"family_helpers"},
    "FAMILY_REVOKE": frozenset({"family_helpers"}),
    "FAMILY_PROPOSE": MONEY_KEYS | RECIPIENT_KEYS | {"family_helpers"},
    **{kind: MONEY_KEYS for kind in ("GOAL_VIEW", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE")},
    "CARBON_VIEW": frozenset({"carbon_behaviors", "carbon_rewards"}),
    "CARBON_CLAIM": frozenset({"carbon_behaviors"}),
    "CARBON_REDEEM": frozenset({"carbon_rewards"}),
    "FX_QUOTE": frozenset({"currencies"}),
    "FX_TRANSFER": MONEY_KEYS | RECIPIENT_KEYS,
    "FX_EXCHANGE": MONEY_KEYS,
    "CONTACT_BIND": RECIPIENT_KEYS,
    # These services read their own owner-scoped source through fresh tools.
    # The legacy collector has no current holdings, tiers, eligibility or
    # explicit-preference facts. Never substitute cash/cards or inferred
    # spending patterns, and never inherit an old preference value as input.
    **{kind: frozenset() for kind in ("ASSET_POSITIONS_READ", "MEMBERSHIP_VIEW", "BENEFITS_VIEW",
        "PREFERENCE_VIEW", "PREFERENCE_SET", "PREFERENCE_REMOVE")},
}
CURRENCY_POLICY_KEYS = frozenset({"supported", "rate_source", "goals_currency", "foreign_schedules_aa_family_supported"})
from .upgraded_capabilities import CAPABILITIES as UPGRADED_CAPABILITIES
# New domains load owner-scoped fresh facts through their tools. Do not repurpose
# an old account balance or a birthday mandate snapshot as current credit data.
ACTION_FACT_KEYS.update({kind: frozenset() for kind in UPGRADED_CAPABILITIES})
REQUIRED_SLOTS = {
    "TRANSFER": ("payee_name", "amount_minor"),
    "TRANSFER_SCHEDULE": ("payee_name", "amount_minor", "scheduled_at"),
    "AA_REQUEST": ("amount_minor", "participant_names"),
    "CARD_SET_LIMIT": ("card_last4", "amount_minor"),
    **{kind: ("card_last4",) for kind in ("CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT")},
    "WEALTH_BUY": ("product_name", "amount_minor"), "WEALTH_REDEEM": ("product_name", "amount_minor"),
    "SUBSCRIPTION_CANCEL": ("merchant",),
    "SUBSCRIPTION_START": ("merchant","subscription_months"),
    "SUBSCRIPTION_RENEW": ("merchant","subscription_months"),
    "FAMILY_GRANT": ("grantee_name", "scopes", "maximum_amount_minor", "expires_days", "limit_period"),
    "FAMILY_REVOKE": ("grantee_name",),
    "GOAL_CREATE": ("goal_name", "target_minor"),
    "GOAL_RESERVE": ("goal_name", "amount_minor"), "GOAL_RELEASE": ("goal_name", "amount_minor"),
    "CARBON_CLAIM": ("behavior_name",), "CARBON_REDEEM": ("reward_name",),
    "FX_TRANSFER": ("payee_name", "amount_minor", "currency"),
    "FX_EXCHANGE": ("amount_minor", "currency", "source_currency", "target_currency"),
    "CONTACT_BIND": ("payee_name", "recipient_alias"),
    "ASSET_POSITIONS_READ": ("asset_class",),
    "PREFERENCE_SET": ("preference_kind", "preference_value"),
    "PREFERENCE_REMOVE": ("preference_kind",),
}


def _complete(action):
    values = action.slots.model_dump()
    fields = REQUIRED_SLOTS.get(action.kind, ())
    if action.kind.startswith("CARD_") and values.get("card_number"):
        fields = ("card_number", "amount_minor") if action.kind == "CARD_SET_LIMIT" else ("card_number",)
    if action.kind in {"WEALTH_BUY", "WEALTH_REDEEM"} and values.get("units"):
        fields = ("product_name", "units")
    if action.kind == "FAMILY_PROPOSE":
        if values.get("proposal_kind") == "TRANSFER":
            fields = ("proposal_kind", "payee_name", "amount_minor")
        elif values.get("proposal_kind") == "CARD_LOCK":
            fields = ("proposal_kind", "card_number" if values.get("card_number") else "card_last4")
        else:
            return False
    return all(values.get(field) is not None and values.get(field) != [] for field in fields)


def _trusted_message(message):
    # The existing source mask recognizes source labels at a sentence start.
    # Apply it to intact sentences as well: a Chinese source after ';' must not
    # become a purpose just because the global source-prefix regex did not hit.
    # Keep quoted blocks intact so their embedded separators/actions stay data.
    pairs = {"“": "”", "「": "」", '"': '"', "‘": "’", "'": "'", "`": "`"}
    closing, start, pieces = None, 0, []
    for index, character in enumerate(message):
        in_word = character in {"'", "’"} and index > 0 and index + 1 < len(message) and message[index - 1].isalnum() and message[index + 1].isalnum()
        if in_word:
            continue
        if closing:
            if character == closing:
                closing = None
        elif character in pairs:
            closing = pairs[character]
        elif character in ";；。\n":
            pieces.extend((_intent_text(message[start:index]), character))
            start = index + 1
    if closing:
        raise ValueError("AMBIGUOUS_SOURCE_BOUNDARY")
    pieces.append(_intent_text(message[start:]))
    return "".join(pieces)


def _purpose(message):
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        return (), "CURRENT_INPUT_UNAVAILABLE"
    # Reuse the registered deterministic baseline, including its source mask,
    # original-text grounding and negation/condition guards. Never use a model
    # suggestion or a bare topic match as a purpose hint.
    trusted = _trusted_message(message)
    proposal = rule_proposal(trusted, fallback=False, reason="fact-purpose-only")
    if not proposal.actions or proposal.clarification_questions:
        return (), "GROUNDING_UNRESOLVED"
    if any(not _complete(action) for action in proposal.actions):
        return (), "MISSING_REQUIRED_SLOTS"
    clauses = _clauses(trusted)
    if len(clauses) > 8:
        return (), "CLASSIFICATION_BUDGET"
    for clause in clauses:
        if not _intent_text(clause).strip(" \t\r\n，,。；;：:！？!?"):
            continue  # Labelled/quoted source alone is not a second user goal.
        leaf = rule_proposal(clause, fallback=False, reason="fact-purpose-clause-only")
        if not leaf.actions or leaf.clarification_questions or any(not _complete(action) for action in leaf.actions):
            return (), "UNCLASSIFIED_CLAUSE"
    kinds = tuple(dict.fromkeys(action.kind for action in proposal.actions))
    if any(kind not in ACTION_FACT_KEYS or ACTION_TO_TOOL.get(kind) not in TOOL_REGISTRY for kind in kinds):
        return (), "UNREGISTERED_PURPOSE"
    return kinds, None


def requires_prior_planning_context(context, message):
    """A v3 USER_CURRENT envelope is provenance, not inherited private context.

    Only the exact current envelope shape is accepted here. Old continuation
    contracts, field sources, reviewed document refs or unknown authorities
    retain the prior bounded facts; this function grants no bank permission.
    """
    if context is None:
        return False
    if not isinstance(context, dict) or context.get("authority") != "USER_CURRENT":
        return True
    keys = {"authority", "conversation_id", "turn_id", "original_user_message", "reviewed_evidence_refs"}
    if set(context) != keys or context.get("original_user_message") != message or context.get("reviewed_evidence_refs") != []:
        return True
    return any(not isinstance(context.get(key), str) or not context[key].strip() for key in ("conversation_id", "turn_id"))


def project_planning_facts(facts, message=None, *, preserve_context=False):
    """Return an outbound copy; preserve all kept values and capability metadata."""
    mode, reason, kinds = "BOUNDED_FALLBACK", None, ()
    if facts.get("actor_role") == "FAMILY_HELPER":
        mode, reason = "ROLE_MINIMAL", "HELPER_NO_OWNER_FACTS"
        result = {key: value for key, value in facts.items() if key in BASE_KEYS}
    else:
        if preserve_context:
            reason = "CONTINUATION_OR_REVISION"
        else:
            try:
                kinds, reason = _purpose(message)
            except (TypeError, ValueError):
                reason = "GROUNDING_UNRESOLVED"
        allowed = facts.get("available_actions")
        if kinds and (not isinstance(allowed, list) or any(kind not in allowed for kind in kinds)):
            kinds, reason = (), "ACTION_NOT_AVAILABLE"
        if reason is not None:
            result = dict(facts)
        else:
            mode = "GROUNDED_PURPOSE_UNION"
            keep = BASE_KEYS | frozenset().union(*(ACTION_FACT_KEYS[kind] for kind in kinds))
            result = {key: value for key, value in facts.items() if key in keep}
            currency = result.get("currencies")
            if isinstance(currency, dict) and not set(kinds) & {"FX_TRANSFER", "FX_EXCHANGE"}:
                currency_keep = CURRENCY_POLICY_KEYS | ({"dual_card"} if any(kind.startswith("CARD_") for kind in kinds) else set())
                result["currencies"] = {key: value for key, value in currency.items() if key in currency_keep}
    result["fact_projection"] = {"version": PROJECTION_VERSION, "mode": mode, "reason": reason,
        "purpose_actions": list(kinds), "retained_keys": sorted(key for key in result if key != "fact_projection"),
        "database_reads_reduced": False}
    return result
