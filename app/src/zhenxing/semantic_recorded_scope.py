"""Equivalent recorded READ questions, never new filters or bank authority.

Collection members select objects. A category aggregate is a separately
published answer fact, not a transaction or a sum of displayed transactions.
All selected facts still require the original owned catalogue/publication
revalidation before an answer can be returned.
"""
from copy import deepcopy


def _context(value, prepared):
    if value.get("mode") != "REQUEST" or value.get("query_base") != "CURRENT" \
            or value.get("changes") or value.get("read_targets") \
            or any(value.get(key) for key in ("memory_updates", "retrieval_query", "presentation_focus", "preference_rebase_ref")):
        return None
    frame = next((row for row in prepared.get("frames", []) if row.get("query_scope")), None)
    from .intelligence import READ_KINDS
    if not frame or frame.get("frame_ref") != value.get("focus_ref") \
            or prepared.get("selected_focus_ref") not in (None, frame["frame_ref"]) \
            or frame.get("state") != "SUCCEEDED" or frame.get("write_effect_possible") is not False \
            or frame.get("paused") or not frame.get("actions") \
            or any(row.get("kind") not in READ_KINDS for row in frame["actions"]):
        return None
    aspects = value.get("question_aspects", [])
    if not aspects or not set(aspects) <= {"SUMMARY", "CHARGE", "PRICE", "STATUS", "TIME_SCOPE"} \
            or value.get("rule_aspects") or value.get("knowledge_topics"):
        return None
    reference = value.get("query_reference") or {}
    if reference.get("selection") != "ALL" or reference.get("data_kind", "RECORDED") != "RECORDED":
        return None
    groups = frame["query_scope"]["collections"]
    group = next((row for row in groups if row["set_ref"] == reference.get("set_ref")), None)
    if not group or not group.get("member_refs"):
        return None
    if reference.get("member_refs") and (len(reference["member_refs"]) != len(set(reference["member_refs"]))
            or set(reference["member_refs"]) != set(group["member_refs"])):
        return None
    from .semantic_query_scope import _source_text
    if reference.get("source_span") is not None:
        _source_text(reference["source_span"], prepared["message"])
    if value.get("query_base_source") is not None:
        _source_text(value["query_base_source"], prepared["message"])
    return frame, group, groups


def normalize_recorded_scope(value, prepared):
    """Consume only exact typed scopes; the original decision is untouched."""
    context = _context(value, prepared)
    if context is None:
        return value, []
    frame, group, groups = context
    kinds, dependencies = value.get("action_kinds", []), value.get("dependencies", [])
    refs = value.get("entity_refs", [])
    if not refs or len(refs) != len(set(refs)):
        return value, []
    entities = {row["entity_ref"]: row for row in prepared.get("entities", [])}
    selected = [entities.get(ref) for ref in refs]
    if any(not row or row.get("frame_ref") != frame["frame_ref"] for row in selected):
        return value, []
    event = None
    target = group
    # Empty action lists can explicitly request explanation of an already
    # selected complete subscription set. No new kind/tool/DAG is invented.
    if kinds == [] and dependencies == [] and (group["domain"], group["field"]) == ("SUBSCRIPTIONS", "merchant") \
            and {row["kind"] for row in frame["actions"]} == {"SUBSCRIPTION_LIST"} \
            and set(refs) == set(group["member_refs"]) \
            and "CHARGE" in value.get("question_aspects", []) \
            and all(row.get("domain") == "SUBSCRIPTIONS" and row.get("field") == "merchant"
                and row.get("read_kind") == "SUBSCRIPTION_LIST" for row in selected):
        event = "EXPLICIT_RECORDED_COLLECTION_QUESTION_WITHOUT_NEW_ACTION"
    # A single, already narrowed BILL read publishes its full-scope category
    # aggregate independently of the (possibly truncated) transaction list.
    # Same frame + exactly one BILL operation + exact original category keeps
    # its period/card/currency scope. The actual catalogue and complete source
    # result are rechecked downstream; no displayed-detail arithmetic occurs.
    elif kinds == ["BILL_ANALYSIS"] and dependencies == [[]] \
            and len(frame["actions"]) == 1 and frame["actions"][0]["kind"] == "BILL_ANALYSIS" \
            and (group["domain"], group["field"]) == ("BILLS", "transaction") \
            and set(value.get("question_aspects", [])).intersection({"CHARGE", "PRICE"}):
        category = frame["actions"][0].get("slots", {}).get("category_filter")
        matching = [row for row in groups if (row["domain"], row["field"]) == ("BILLS", "category_filter")
            and set(row["member_refs"]) == set(refs)]
        if isinstance(category, str) and category and len(matching) == 1 \
                and all(row.get("domain") == "BILLS" and row.get("field") == "category_filter"
                    and row.get("read_kind") == "BILL_ANALYSIS" and row.get("registered_options") == [category]
                    for row in selected):
            target = matching[0]
            event = "CURRENT_BILL_CATEGORY_AGGREGATE_ANSWER_FACTS"
    if event is None:
        return value, []
    result = deepcopy(value)
    result.update(mode="CONSULT", action_kinds=[], dependencies=[], changes=[], read_targets=[],
        query_reference={**result["query_reference"], "set_ref": target["set_ref"], "selection": "ALL", "member_refs": []})
    return result, [{"code": event, "frame_ref": frame["frame_ref"],
        "object_set_ref": group["set_ref"], "answer_set_ref": target["set_ref"]}]


def normalize_read_navigation_scope(value, prepared):
    """Remove only exact duplicate current-READ navigation, not scope intent."""
    if value.get("mode") not in {"REQUEST", "CONSULT", "CONTINUE", "REVISE"} or "OPERATION_SCOPE" in value.get("question_aspects", []) \
            or value.get("query_base") != "CURRENT" \
            or value.get("operation_scope") not in (None, "CURRENT_REQUEST"):
        return value
    frame = next((row for row in prepared.get("frames", []) if row.get("query_scope")), None)
    if value.get('mode') in {'CONTINUE','REVISE'}:
        from .semantic_router import is_completed_read_followup
        if not frame or value.get('focus_ref')!=frame['frame_ref'] \
                or prepared.get('selected_focus_ref') not in (None,frame['frame_ref']) \
                or value.get('scope_frame_refs',[]) not in ([],[frame['frame_ref']]) \
                or not is_completed_read_followup(value,prepared):return value
        # This is the exact focus already selected by the model, not a scope
        # change. Keep every filter/reference; the normal read compiler still
        # checks the owned source and resolves the current requested period.
        return {**value,'scope_frame_refs':[],'operation_scope':None}
    from .intelligence import READ_KINDS
    if not frame or frame.get("frame_ref") != value.get("focus_ref") \
            or value.get("scope_frame_refs", []) not in ([], [frame["frame_ref"]]) \
            or frame.get("state") != "SUCCEEDED" or frame.get("write_effect_possible") is not False \
            or frame.get("paused") or not frame.get("actions") \
            or any(row.get("kind") not in READ_KINDS for row in frame["actions"]) \
            or any(kind not in READ_KINDS for kind in value.get("action_kinds", [])):
        return value
    reference = value.get("query_reference") or {}
    group = next((row for row in frame["query_scope"]["collections"] if row["set_ref"] == reference.get("set_ref")), None)
    if not group or reference.get("selection") not in {"ALL", "SUBSET"} \
            or reference.get("data_kind", "RECORDED") != "RECORDED":
        return value
    members = group["member_refs"] if reference["selection"] == "ALL" else reference.get("member_refs")
    refs = value.get("entity_refs", [])
    if not members or not refs or len(refs) != len(set(refs)) or set(refs) != set(members):
        return value
    return {**value, "scope_frame_refs": [], "operation_scope": None}
