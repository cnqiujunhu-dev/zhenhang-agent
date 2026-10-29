"""A whole BILL query scope is not a writable transaction selector.

This finite role proof skips only member expansion. It preserves the model's
mode, all changes and the original ALL reference for the normal source-bound
READ compiler; it neither summarizes transactions nor permits a WRITE.
"""


def complete_bill_query_context(value, prepared):
    if value.get("mode") not in {"REQUEST", "CONTINUE", "REVISE"} or value.get("query_base") != "CURRENT" \
            or value.get("read_targets") or value.get("preference_rebase_ref"):
        return None
    frames = prepared.get("frames", [])
    frame = next((row for row in frames if row.get("query_scope")), None)
    if not frame or frame.get("frame_ref") != value.get("focus_ref") \
            or prepared.get("selected_focus_ref") not in (None, frame["frame_ref"]) \
            or frame.get("state") != "SUCCEEDED" or frame.get("write_effect_possible") is not False \
            or frame.get("paused") or len(frame.get("actions", [])) != 1 \
            or frame["actions"][0].get("kind") != "BILL_ANALYSIS":
        return None
    reference = value.get("query_reference") or {}
    if reference.get("selection") != "ALL" or reference.get("data_kind", "RECORDED") != "RECORDED":
        return None
    from .semantic_query_scope import _source_text
    for source in (reference.get("source_span"), value.get("query_base_source")):
        if source is not None:
            _source_text(source, prepared["message"])
    group = next((row for row in frame["query_scope"]["collections"] if row["set_ref"] == reference.get("set_ref")), None)
    if not group or group['domain']!='BILLS' or group['field'] not in {'transaction','bill_query_summary'}:
        return None
    members = group.get("member_refs", [])
    if not members or len(members) != len(set(members)):
        return None
    entities = {row["entity_ref"]: row for row in prepared.get("entities", [])}
    if any(ref not in entities or entities[ref].get("frame_ref") != frame["frame_ref"]
            or (entities[ref].get("domain"), entities[ref].get("field"), entities[ref].get("read_kind"))
                != ("BILLS", group['field'], "BILL_ANALYSIS") for ref in members):
        return None
    if group['field']=='bill_query_summary' and len(members)!=1:return None
    for refs in (value.get("entity_refs", []), reference.get("member_refs", [])):
        if refs and (len(refs) != len(set(refs)) or set(refs) != set(members)):
            return None  # A partial member selection is not complete context.
    kinds, dependencies = value.get("action_kinds", []), value.get("dependencies", [])
    request = value["mode"] == "REQUEST"
    if request and (kinds != ["BILL_ANALYSIS"] or dependencies != [[]]) \
            or not request and (kinds not in ([], ["BILL_ANALYSIS"]) or dependencies):
        return None
    action_ref = "new:0" if request else frame["actions"][0]["action_ref"]
    capability = next((row for row in prepared.get("capabilities", []) if row.get("kind") == "BILL_ANALYSIS"), {})
    if capability.get("read_only") is not True:
        return None
    for change in value.get("changes", []):
        if change.get("action_ref") != action_ref or change.get("field") == "transaction" \
                or change.get("field") not in capability.get("allowed_fields", []):
            return None
    # Field-change spans/options/duplicates remain the original compiler's
    # responsibility; no change is cleared or rewritten by this role proof.
    return {"role": "COMPLETE_BILL_QUERY_CONTEXT", "frame_ref": frame["frame_ref"],
        "set_ref": group["set_ref"], "member_count": len(members), "bank_write_authorized": False}
