"""Versioned, owner-bound shared evidence; references never grant authority."""
from copy import deepcopy
import json
import re

from .contracts import DomainError
from .planning import canonical_hash


OFFERED_V1 = "semantic-offered-context-v1"
OFFERED_V2 = "semantic-offered-context-v2"
BUNDLE_KEY = "semantic_source_bundle_v2"
BUNDLE_VERSION = "semantic-source-bundle-v2"
REF_VERSION = "semantic-source-bundle-ref-v2"
SELECTED_VERSION = "semantic-selected-result-source-v2"
SELECTOR_VERSION = "semantic-knowledge-selection-v2"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024


def _fits_bundle(value, *, reserve=0):
    canonical=len(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))
    stored=len(json.dumps(value).encode('utf-8'))  # SQLAlchemy's default JSON serializer.
    return max(canonical,stored)+reserve<=MAX_BUNDLE_BYTES


def _deny():
    raise DomainError("SEMANTIC_SOURCE_CHANGED", "原查询资料的来源需要重新核对；不会据此办理。", 409)


def add_catalog(offered, catalog):
    if offered.get("schema_version") != OFFERED_V2:
        _deny()
    digest = canonical_hash(catalog)
    pool = offered.setdefault("catalog_pool", {})
    if digest not in pool:
        pool[digest] = deepcopy(catalog)
    elif pool[digest] != catalog:
        _deny()
    return digest


def entity_catalog(offered, entry):
    """One closed adapter for v1 inline catalogs and v2 content references."""
    if not isinstance(offered, dict) or not isinstance(entry, dict):
        _deny()
    version = offered.get("schema_version")
    if version == OFFERED_V1:
        return entry.get("catalog")
    if version != OFFERED_V2 or "catalog" in entry:
        _deny()
    ref = entry.get("catalog_ref")
    pool = offered.get("catalog_pool")
    if not isinstance(ref, str) or not re.fullmatch(r"[a-f0-9]{64}", ref) or not isinstance(pool, dict):
        _deny()
    value = pool.get(ref)
    if not isinstance(value, dict) or canonical_hash(value) != ref:
        _deny()
    actual = entry.get("actual_entity") or entry.get("entity")
    if not isinstance(actual, dict) or actual not in value.get("entities", []):
        _deny()
    return value


def catalog_entry(offered, entry):
    """Ephemeral adapter for legacy domain identity validators, never stored."""
    return {**entry, "catalog": entity_catalog(offered, entry)}


def compact_catalog(catalog, actual):
    """Small display proof; publication still verifies the complete catalog."""
    if actual not in catalog.get("entities", []):
        _deny()
    refs = actual.get("fact_refs", [])
    return {"catalog_hash": catalog["catalog_hash"], "selected_task_id": catalog["selected_task_id"],
        "entities": [deepcopy(actual)], "fact_refs": [deepcopy(fact) for fact in catalog.get("fact_refs", [])
            if fact.get("entity_ref") == actual.get("entity_ref") and fact.get("fact_ref") in refs]}


def bundle_reference(bundle):
    return {"version": REF_VERSION, "conversation_id": bundle["conversation_id"],
        "source_turn_id": bundle["source_turn_id"], "bundle_hash": bundle["bundle_hash"]}


def capture_bundle(session, principal, thread, turn, offered, *, public_records=()):
    from .semantic_proposal import _valid_turn
    thread, _scope, epoch = _valid_turn(session, principal, thread, turn)
    if offered.get("schema_version") != OFFERED_V2 or offered.get("actor_scope_hash") != epoch \
            or offered.get("thread_id") != thread.id or offered.get("turn_id") != turn.id:
        _deny()
    receipt = (turn.diagnostics_json or {}).get("semantic_receipt")
    if not isinstance(receipt, dict) or receipt.get("status") != "READY":
        _deny()
    public_descriptors=[]
    for record in public_records:
        stored=deepcopy({key:value for key,value in record.items() if key!='source_binding'})
        binding=record.get('source_binding')
        shared=isinstance(binding,dict) and 'offered_context' in binding
        if isinstance(binding,dict):
            if shared and binding['offered_context']!=offered:_deny()
            stored['source_binding']=deepcopy({key:value for key,value in binding.items() if key!='offered_context'})
        public_descriptors.append({'record':stored,'offered_shared':shared})
    body = {"schema_version": BUNDLE_VERSION, "owner": {"user_id": principal.user_id, "sandbox_id": principal.sandbox_id},
        "conversation_id": thread.id, "source_turn_id": turn.id, "source_sequence": turn.sequence,
        "actor_scope_hash": epoch, "source_message_hash": canonical_hash({"message": turn.message}),
        "source_receipt_hash": canonical_hash(receipt), "input_hash": receipt.get("input_hash"),
        "offered_hash": canonical_hash(offered), "offered_context": offered,
        "public_descriptors":public_descriptors,"bank_write_authorized": False}
    if not _fits_bundle(body,reserve=100):
        raise DomainError("SEMANTIC_SOURCE_CONTEXT_TOO_LARGE",
            "此前选中资料的来源过大，暂时无法完整核实。请重新查询具体对象；尚未据此办理。", 409)
    body = {**deepcopy(body), "bundle_hash": canonical_hash(body)}
    previous = (turn.diagnostics_json or {}).get(BUNDLE_KEY)
    if previous is not None and previous != body:
        _deny()
    if previous is None:
        turn.diagnostics_json = {**(turn.diagnostics_json or {}), BUNDLE_KEY: body}
    return bundle_reference(body)


def load_bundle(session, principal, thread, turn, reference):
    """Fresh current owner/epoch first, then immutable content and membership."""
    from .semantic_proposal import _valid_turn
    thread, _scope, epoch = _valid_turn(session, principal, thread, turn)
    if not isinstance(reference, dict) or set(reference) != {"version", "conversation_id", "source_turn_id", "bundle_hash"} \
            or reference.get("version") != REF_VERSION or reference.get("conversation_id") != thread.id \
            or reference.get("source_turn_id") != turn.id:
        _deny()
    body = (turn.diagnostics_json or {}).get(BUNDLE_KEY)
    if not isinstance(body, dict) or body.get("schema_version") != BUNDLE_VERSION \
            or set(body)!={'schema_version','owner','conversation_id','source_turn_id','source_sequence','actor_scope_hash',
                'source_message_hash','source_receipt_hash','input_hash','offered_hash','offered_context','public_descriptors',
                'bank_write_authorized','bundle_hash'} \
            or body.get("owner") != {"user_id": principal.user_id, "sandbox_id": principal.sandbox_id} \
            or body.get("conversation_id") != thread.id or body.get("source_turn_id") != turn.id \
            or body.get("source_sequence") != turn.sequence or body.get("actor_scope_hash") != epoch \
            or body.get("bank_write_authorized") is not False:
        _deny()
    payload = {key: value for key, value in body.items() if key != "bundle_hash"}
    if not _fits_bundle(body):_deny()
    if body.get("bundle_hash") != reference.get("bundle_hash") or canonical_hash(payload) != body["bundle_hash"]:
        _deny()
    receipt = (turn.diagnostics_json or {}).get("semantic_receipt")
    offered = body.get("offered_context")
    if not isinstance(receipt, dict) or receipt.get("status") != "READY" or not isinstance(offered, dict) \
            or offered.get("schema_version") != OFFERED_V2 or offered.get("actor_scope_hash") != epoch \
            or offered.get("thread_id") != thread.id or offered.get("turn_id") != turn.id \
            or canonical_hash(receipt) != body.get("source_receipt_hash") \
            or canonical_hash(offered) != body.get("offered_hash") \
            or canonical_hash({"message": turn.message}) != body.get("source_message_hash") \
            or receipt.get("input_hash") != body.get("input_hash"):
        _deny()
    from .semantic_router import prepare_semantic_input, validate_semantic_decision
    prepared = prepare_semantic_input(**{**offered["model"], "message": turn.message})
    if canonical_hash(prepared) != body["input_hash"]:
        _deny()
    validate_semantic_decision(receipt["decision"], prepared)
    return offered, receipt


def public_records_from_bundle(turn, offered):
    descriptors=(turn.diagnostics_json or {}).get(BUNDLE_KEY,{}).get('public_descriptors')
    if not isinstance(descriptors,list):_deny()
    records=[]
    for descriptor in descriptors:
        if not isinstance(descriptor,dict) or set(descriptor)!={'record','offered_shared'} \
                or type(descriptor['offered_shared']) is not bool or not isinstance(descriptor['record'],dict):_deny()
        record=deepcopy(descriptor['record'])
        if descriptor['offered_shared']:
            if not isinstance(record.get('source_binding'),dict) or 'offered_context' in record['source_binding']:_deny()
            record['source_binding']={**record['source_binding'],'offered_context':offered}
        records.append(record)
    return records
