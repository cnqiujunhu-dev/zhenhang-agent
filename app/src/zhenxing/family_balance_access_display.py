"""A finite negative READ display, never a grant or an inferred zero balance."""
VERSION = "family-balance-negative-authorization-v1"


def negative_balance_authorization(result, coverage):
    """Call after original READ/owner/epoch checks for this exact selection.

    Missing balances alone prove nothing. Require an explicit, consistent
    negative permission and one uniquely selected known owner. Preserve the
    intentional absence of private balance fields; never manufacture [].
    """
    expected = {"status": "FILTERED", "read_kind": "FAMILY_VIEW",
        "selected_dimensions": ["FAMILY_OWNERS"], "selected_counts": {"FAMILY_OWNERS": 1}}
    if not isinstance(result, dict) or coverage != expected or "authorized_balances" in result:
        return None
    current, actors, permissions = result.get("current_actor"), result.get("actors"), result.get("permissions")
    if not isinstance(current, dict) or current.get("role") != "FAMILY_HELPER" \
            or not isinstance(permissions, dict) or permissions.get("can_read_owner_balance") is not False \
            or not isinstance(actors, list) or len(actors) != 2 or any(not isinstance(actor, dict) for actor in actors) \
            or sum(actor == current for actor in actors) != 1:
        return None
    if any(not isinstance(actor.get("role"), str) or actor["role"] not in {"OWNER", "FAMILY_HELPER"} or not isinstance(actor.get("user_id"), str)
            or not actor["user_id"] or not isinstance(actor.get("display_name"), str) or not actor["display_name"].strip()
            for actor in actors) or len({actor["user_id"] for actor in actors}) != len(actors):
        return None
    owners = [actor for actor in actors if actor["role"] == "OWNER"]
    if len(owners) != 1: return None
    owner = owners[0]
    delegations, proposals = result.get("delegations"), result.get("proposals")
    if not isinstance(delegations, list) or not isinstance(proposals, list) \
            or any(not isinstance(proposal, dict) for proposal in proposals): return None
    for row in delegations:
        if not isinstance(row, dict) or row.get("owner_user_id") != owner["user_id"] \
                or row.get("grantee_user_id") != current["user_id"] or type(row.get("valid")) is not bool \
                or not isinstance(row.get("scopes"), list) or any(not isinstance(scope, str) for scope in row["scopes"]) \
                or row["valid"] and "READ_BALANCE" in row["scopes"]:
            return None
    return {"schema_version": VERSION, "status": "NOT_AUTHORIZED", "owner_name": owner["display_name"]}


def valid_negative_balance_authorization(result, coverage):
    """Validate the exact authenticated DTO marker and the same negative facts."""
    if not isinstance(coverage, dict): return False
    marker = coverage.get("balance_authorization")
    base = {key: value for key, value in coverage.items() if key != "balance_authorization"}
    expected = negative_balance_authorization(result, base)
    return expected is not None and marker == expected
