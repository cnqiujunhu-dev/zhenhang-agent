"""Bounded equivalent READ representations; no new intents or bank authority.

WRITE/mixed plans and all money/permission fields keep their original strict
contracts. Every selector keeps its user span and is later owner/source checked.
"""
from copy import deepcopy
import re

# Only genuinely set-valued, user-selected READ dimensions. Never amounts,
# currencies, permissions, versions, schedules, or competing WRITE commands.
LITERAL_SET_FIELDS = {"SUBSCRIPTION_LIST": {"merchant"}, "WEALTH_COMPARE": {"product_name"},
    "CARBON_VIEW": {"reward_name", "behavior_name"}, "BENEFITS_VIEW": {"benefit_name"},"MERCHANT_CATALOG":{"product_name"},
    "BILL_ANALYSIS":{"card_number","card_last4"},"CARD_STATUS":{"card_number","card_last4"},
    "CREDIT_VIEW":{"card_number","card_last4"},"CREDIT_STATEMENT":{"card_number","card_last4"}}
# Opt-in recorded-fact lenses, not a replacement for the existing fresh READ
# consumer. Catalog product/category rows are result facts, not write selectors.
RECORDED_FACT_LENSES = {"CARBON_VIEW": {("CARBON","reward_name")},
    "MERCHANT_CATALOG": {("MERCHANTS","merchant_product_id")},
    "BILL_ANALYSIS": {("BILLS","category_filter"),("BILLS","transaction")}}


def _pure_request(value, prepared):
    from .intelligence import READ_KINDS
    capabilities = {row["kind"]: row for row in prepared["capabilities"]}
    kinds = value.get("action_kinds", [])
    return value.get("mode") == "REQUEST" and kinds and all(kind in READ_KINDS
        and capabilities.get(kind, {}).get("read_only") is True for kind in kinds)


def _span_text(item, prepared):
    from .semantic_query_scope import _source_text
    text = _source_text(item.get("source_span"), prepared["message"])
    literal = item.get("source_text")
    if literal is not None and literal != text:
        raise ValueError("SEMANTIC_READ_SET_LITERAL_MISMATCH")
    return text


def _literal_sets(value, prepared, events):
    if not _pure_request(value, prepared) or value.get("read_targets"): return value
    groups = {}
    for item in value.get("changes", []):
        groups.setdefault((item["action_ref"], item["field"]), []).append(item)
    duplicate = [(key, items) for key, items in groups.items() if len(items) > 1]
    if not duplicate: return value
    # Rebase syntax is not repaired here: only explicit new independent READs.
    if value.get("query_reference") or value.get("focus_ref") or value.get("query_base") == "CURRENT": return value
    changes = deepcopy(value.get("changes", [])); targets = []; pending_events=[]
    used = set()
    for (ref, field), items in duplicate:
        match = re.fullmatch(r"new:(\d+)", ref)
        index = int(match[1]) if match else -1
        if not 0 <= index < len(value["action_kinds"]): return value
        kind = value["action_kinds"][index]
        if field not in LITERAL_SET_FIELDS.get(kind, set()) or index in used: return value
        if len(items) > 8 or any(item.get("operation", "SET") != "SET" or item.get("entity_ref")
                or item.get("selected_options") for item in items): return value
        texts = [_span_text(item, prepared) for item in items]
        if len(set(texts)) != len(texts): return value
        targets.append({"action_ref": ref, "field": field, "targets": [{key: deepcopy(item[key])
            for key in ("source_span", "source_text") if key in item} for item in items]})
        changes = [item for item in changes if (item["action_ref"], item["field"]) != (ref, field)]
        used.add(index)
        pending_events.append({"code": "READ_LITERAL_SELECTOR_SET", "action_ref": ref, "field": field, "count": len(items)})
    events.extend(pending_events)
    return {**value, "changes": changes, "read_targets": targets}


def _redundant_target_copies(value,prepared,events):
    """Remove only a complete byte/source-identical duplicate declaration.

    A subset, changed source, permission/amount, selected entity or conflicting
    copy remains an error. This never picks which of two objects should win.
    """
    if not _pure_request(value,prepared) or not value.get('read_targets') \
            or value.get('focus_ref') or value.get('query_reference') or value.get('query_base')=='CURRENT':return value
    changes=list(value.get('changes',[]));removed=[]
    for selection in value['read_targets']:
        ref=selection.get('action_ref');field=selection.get('field')
        match=re.fullmatch(r'new:([0-7])',ref or '')
        if not match or int(match[1])>=len(value['action_kinds']):continue
        kind=value['action_kinds'][int(match[1])]
        if field not in LITERAL_SET_FIELDS.get(kind,set()):continue
        copies=[row for row in changes if row.get('action_ref')==ref and row.get('field')==field]
        targets=selection.get('targets',[])
        if len(copies)<2 or len(copies)!=len(targets) \
                or any(row.get('operation','SET')!='SET' or row.get('entity_ref') or row.get('selected_options') for row in copies) \
                or any(row.get('entity_ref') or row.get('selected_options') for row in targets):continue
        keys=('source_span','source_text','entity_ref','selected_options')
        identities=lambda rows:[{key:row.get(key) for key in keys} for row in rows]
        if identities(copies)!=identities(targets):continue
        texts=[_span_text(row,prepared) for row in targets]
        if len(set(texts))!=len(texts):continue
        changes=[row for row in changes if row not in copies]
        removed.append({'code':'READ_REDUNDANT_TARGET_DECLARATION','action_ref':ref,'field':field,'count':len(copies)})
    if not removed:return value
    events.extend(removed)
    return {**value,'changes':changes}


def _recorded_question(value, prepared, events):
    if not _pure_request(value, prepared): return value
    frame = next((row for row in prepared["frames"] if row["frame_ref"] == value.get("focus_ref")), None)
    reference = value.get("query_reference") or {}
    from .intelligence import READ_KINDS
    aspects = value.get("question_aspects", [])
    if not frame or frame.get("state") != "SUCCEEDED" or frame.get("write_effect_possible") is not False \
            or frame.get("paused") or not frame.get("query_scope") or not aspects or "OPERATION_SCOPE" in aspects \
            or reference.get("data_kind", "RECORDED") != "RECORDED" or reference.get("selection") not in {"ALL", "SUBSET"} \
            or not frame.get("actions") or any(row["kind"] not in READ_KINDS for row in frame["actions"]) \
            or len(set(value["action_kinds"])) != 1 \
            or set(value["action_kinds"]) != {row["kind"] for row in frame["actions"]}: return value
    # This canonicalization cannot redirect an explicit UI selection or consume
    # memory updates / unrelated public lookup as if they were result facts.
    if prepared.get("selected_focus_ref") not in (None, frame["frame_ref"]) \
            or value.get("memory_updates") or value.get("retrieval_query") or value.get("presentation_focus") \
            or value.get("preference_rebase_ref") or value.get("query_base") == "INDEPENDENT": return value
    from .semantic_query_scope import _source_text, _explicit_members
    # Legacy ALL references need no narrowing span and remain fresh reads.
    if reference.get("source_span") is None:return value
    _source_text(reference["source_span"], prepared["message"])
    if value.get("query_base_source") is not None: _source_text(value["query_base_source"], prepared["message"])
    groups = frame["query_scope"]["collections"]
    group = next((row for row in groups if row["set_ref"] == reference.get("set_ref")), None)
    if not group or (group["domain"],group["field"]) not in RECORDED_FACT_LENSES.get(value["action_kinds"][0],set()): return value
    refs = value.get("entity_refs", []); pending_events=[]
    if refs and not set(refs) <= set(group["member_refs"]):
        if (group["domain"],group["field"]) != ("BILLS","transaction"): return value
        # A model can name the one category entity but copy the transaction
        # collection's set_ref. Repair only the unique COMPLETE same-frame
        # category collection, with explicit current category evidence.
        alternatives = [row for row in groups if row["domain"] == "BILLS" and row["field"] == "category_filter"
            and set(row["member_refs"]) == set(refs)
            and _explicit_members(refs, row, prepared, reference.get("source_span"))]
        if len(alternatives) != 1: return value
        group = alternatives[0]
        reference = {**reference, "set_ref": group["set_ref"]}
        pending_events.append({"code": "RECORDED_CATEGORY_COLLECTION_REFERENCE", "frame_ref": frame["frame_ref"]})
    members = group["member_refs"] if reference["selection"] == "ALL" else reference.get("member_refs") or refs
    if not members or not set(members) <= set(group["member_refs"]): return value
    if reference["selection"] == "ALL" and refs and set(refs) != set(members): return value
    if reference.get("member_refs") and (len(reference["member_refs"]) != len(set(reference["member_refs"]))
            or reference["selection"] == "ALL" and set(reference["member_refs"]) != set(members)
            or reference["selection"] == "SUBSET" and refs and set(refs) != set(members)):
        raise ValueError("SEMANTIC_READ_QUESTION_REFERENCE_CONFLICT")
    # No new filters may disappear. A repeated exact current category option
    # is redundant; all other scalar changes stay real READ requests.
    for change in value.get("changes", []):
        action_ref=re.fullmatch(r"new:(\d+)",change.get("action_ref",""))
        if set(value["action_kinds"]) != {"BILL_ANALYSIS"} or change.get("field") != "category_filter" \
                or change.get("operation", "SET") != "SET" or change.get("entity_ref") \
                or not action_ref or int(action_ref[1]) >= len(value["action_kinds"]): return value
        options = change.get("selected_options") or []
        source = _span_text(change, prepared)
        old = [row.get("slots", {}).get("category_filter") for row in frame["actions"]]
        if len(options) != 1 or not old or any(item != options[0] for item in old) or options[0] not in source: return value
    for selection in value.get("read_targets", []):
        action_refs = {row["action_ref"] for row in frame["actions"]} | {"new:"+str(i) for i in range(len(value["action_kinds"]))}
        items = selection.get("targets", [])
        if selection.get("action_ref") not in action_refs or selection.get("field") != group["field"] \
                or {item.get("entity_ref") for item in items} != set(members) \
                or len(items) != len(members) \
                or any(item.get("selected_options") for item in items): return value
        for item in items:
            _span_text(item, prepared)
            if not _explicit_members([item["entity_ref"]],group,prepared,item["source_span"]): return value
    result = {**value, "mode": "CONSULT", "action_kinds": [], "dependencies": [], "changes": [], "read_targets": [],
        "query_reference": reference}
    if result.get("rule_aspects"):
        result["question_aspects"] = list(dict.fromkeys([*aspects, "RULES"]))
    events.extend(pending_events)
    events.append({"code": "RECORDED_READ_FACT_QUESTION", "frame_ref": frame["frame_ref"], "read_kind_count": len(set(value["action_kinds"]))})
    return result


def normalize_read_ir(value, prepared):
    # Parse the complete envelope first. Unknown fields and malformed/financial
    # values cannot be swallowed by a permissive normalization lane.
    from .semantic_router import SemanticDecision
    parsed = SemanticDecision.model_validate(value.model_dump() if isinstance(value, SemanticDecision) else value)
    value = parsed.model_dump(exclude_none=True); events = []
    from .semantic_read_targets import validate_request_change_ownership
    if _pure_request(value, prepared):
        validate_request_change_ownership(value,prepared)
        dependencies=value.get("dependencies",[])
        if len(dependencies)!=len(value["action_kinds"]): raise ValueError("SEMANTIC_DAG_LENGTH_INVALID")
        if any(len(set(parents))!=len(parents) or any(type(parent) is not int or not 0<=parent<index for parent in parents)
                for index,parents in enumerate(dependencies)): raise ValueError("SEMANTIC_DAG_NOT_PRIOR_ONLY")
    from .card_balance_scope import normalize_card_currency_read
    value, currency_events = normalize_card_currency_read(value, prepared)
    events.extend(currency_events)
    from .semantic_independent_read import normalize_independent_read
    value, independent_events = normalize_independent_read(value, prepared)
    events.extend(independent_events)
    value = _redundant_target_copies(value,prepared,events)
    value = _literal_sets(value, prepared, events)
    from .semantic_recorded_scope import normalize_recorded_scope
    value, scoped_events = normalize_recorded_scope(value, prepared)
    events.extend(scoped_events)
    from .semantic_bill_query_context import complete_bill_query_context
    query_context=complete_bill_query_context(value,prepared)
    if query_context:
        events.append({"code":"COMPLETE_READ_QUERY_CONTEXT_NOT_OBJECT_SELECTOR",**query_context})
        return value,events
    value = _recorded_question(value, prepared, events)
    return value, events
