"""One field contract for family READ navigation and the actual bank query."""

KIND = "FAMILY_VIEW"
DOMAIN = "FAMILY"
TOOL = "family.view.v1"
SELECTOR_FIELDS = frozenset({"owner_name", "grantee_name"})
SELECTOR_ROLES = {"owner_name": "OWNER", "grantee_name": "FAMILY_HELPER"}
SELF_REFERENCES = frozenset({"我", "本人", "自己", "我自己", "i", "me", "myself", "my"})


def family_read_arguments(slots):
    """Extract only registered, present selectors; CLEAR leaves an unfiltered read."""
    return {field: slots[field] for field in sorted(SELECTOR_FIELDS) if slots.get(field) is not None}


def family_selector_matches(actor, field, reference, current_actor):
    """Resolve a literal self-reference only to this authenticated READ actor.

    This runs after the LLM selects a READ field. It cannot select an action,
    switch family contexts, grant a permission, or interpret another person's
    pronouns. Both live query and sealed result validation use this contract.
    """
    if not isinstance(reference, str) or field not in SELECTOR_ROLES or not isinstance(actor, dict) \
            or actor.get("role") != SELECTOR_ROLES[field]:
        return False
    if reference.casefold() in SELF_REFERENCES:
        return bool(isinstance(current_actor, dict) and current_actor.get("user_id")
            and actor.get("user_id") == current_actor["user_id"] and current_actor.get("role") == SELECTOR_ROLES[field])
    return actor.get("display_name") == reference
