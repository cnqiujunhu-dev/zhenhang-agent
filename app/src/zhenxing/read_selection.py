"""Versioned finite private READ scope, never a banking write authorization.

Legacy results without this marker remain with their original validators. New
scoped results bind actual owned domain facts, then recheck the exact same
scope for subsequent entity use. Historical READ presentation validates the
stored immutable result; it does not refresh its bank values.
"""
from copy import deepcopy
import re

from .contracts import DomainError
from .dialogue_state import dialogue_actor_scope
from .planning import canonical_hash
from .services import Principal
from .family_read_contract import SELECTOR_FIELDS as FAMILY_READ_FIELDS, SELECTOR_ROLES

VERSION = "controlled-domain-read-scope-v1"
KEY = "read_selection"
FIELDS = {"CARBON_VIEW": {"reward_name", "behavior_name"}, "WEALTH_COMPARE": {"product_name"},
          "FAMILY_VIEW": FAMILY_READ_FIELDS}
_FIELDS = {"schema_version", "read_kind", "selectors", "owner_hash", "actor_scope_hash", "selected_records",
           "source_hash", "read_only", "bank_write_authorized", "scope_hash"}
_TARGET_FIELDS = {"field", "record_id", "record_version", "record_hash"}
_DIMENSIONS = {"reward_name": ("REWARDS", "rewards", "reward_id", "reward_name"),
               "behavior_name": ("ACTIVITIES", "events", "behavior_id", "behavior_name"),
               "product_name": ("PRODUCTS", "products", "product_id", "name"),
               "owner_name": ("FAMILY_OWNERS", "actors", "user_id", "display_name"),
               "grantee_name": ("FAMILY_GRANTEES", "actors", "user_id", "display_name")}


def _deny():
    raise DomainError("READ_SELECTION_SOURCE_CHANGED", "原查询范围已变化，请重新查询。", 409)


def stable_projection(kind, value):
    """Only registered observation-clock paths; preserve expiry and all facts."""
    result = deepcopy(value)
    if kind == "WEALTH_COMPARE":
        result.pop("business_time", None)
        profile = result.get("risk_profile")
        if isinstance(profile, dict):
            profile.pop("business_time", None)
    return result


def validate_selectors(kind, selectors):
    if kind not in FIELDS or not isinstance(selectors, dict) or not selectors or not set(selectors) <= FIELDS[kind]:
        _deny()
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 100
           for value in selectors.values()):
        _deny()
    return deepcopy(selectors)


def make_read_selection(session, sandbox_id, user_id, kind, selectors, selected_records, result, *, principal=None):
    selectors = validate_selectors(kind, selectors)
    principal = principal or Principal(user_id, sandbox_id)
    if (principal.user_id, principal.sandbox_id) != (user_id, sandbox_id):
        _deny()
    _, epoch = dialogue_actor_scope(session, principal)
    value = {"schema_version": VERSION, "read_kind": kind, "selectors": selectors,
        "owner_hash": canonical_hash({"user_id": user_id, "sandbox_id": sandbox_id}), "actor_scope_hash": epoch,
        "selected_records": deepcopy(selected_records), "source_hash": canonical_hash(stable_projection(kind, result)),
        "read_only": True, "bank_write_authorized": False}
    return {**value, "scope_hash": canonical_hash(value)}


def _snapshot_selection(kind, result):
    """Pure exact stored-snapshot check; upstream still verifies task/source/owner."""
    if not isinstance(result, dict) or KEY not in result:
        return None
    try:
        value = result[KEY]
        if (not isinstance(value, dict) or set(value) != _FIELDS or value["schema_version"] != VERSION
                or value["read_kind"] != kind or value["read_only"] is not True or value["bank_write_authorized"] is not False):
            _deny()
        selectors = validate_selectors(kind, value["selectors"])
        if any(not isinstance(value[field], str) or not re.fullmatch(r"[a-f0-9]{64}", value[field])
               for field in ("owner_hash", "actor_scope_hash", "source_hash", "scope_hash")):
            _deny()
        body = {key: item for key, item in result.items() if key not in {KEY, "message"}}
        if value["source_hash"] != canonical_hash(stable_projection(kind, body)) \
                or value["scope_hash"] != canonical_hash({key: item for key, item in value.items() if key != "scope_hash"}):
            _deny()
        records = value["selected_records"]
        if not isinstance(records, list) or len(records) != len(selectors):
            _deny()
        seen = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != _TARGET_FIELDS or record["field"] not in selectors \
                    or record["field"] in seen or not isinstance(record["record_id"], str) or not 1 <= len(record["record_id"]) <= 100:
                _deny()
            field = record["field"]; seen.add(field)
            _, collection, id_key, name_key = _DIMENSIONS[field]
            rows = result.get(collection)
            if field in FAMILY_READ_FIELDS and isinstance(rows, list):
                rows = [row for row in rows if isinstance(row, dict) and row.get("role") == SELECTOR_ROLES[field]
                        and row.get(id_key) == record["record_id"]]
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                _deny()
            row = rows[0]
            name_matches=row.get(name_key)==selectors[field]
            if field=='product_name':
                from .wealth_identity import product_reference_matches
                name_matches=product_reference_matches(row,selectors[field])
            elif field in FAMILY_READ_FIELDS:
                from .family_read_contract import family_selector_matches
                name_matches=family_selector_matches(row,field,selectors[field],result.get("current_actor"))
            if row.get(id_key) != record["record_id"] or not name_matches \
                    or record["record_hash"] != canonical_hash(row):
                _deny()
            version = record["record_version"]
            if field == "behavior_name" or field in FAMILY_READ_FIELDS:
                if version is not None: _deny()
            elif type(version) is not int or version < 1 or version != row.get("version"):
                _deny()
        if seen != set(selectors): _deny()
        if kind == "CARBON_VIEW":
            if "point_ledger_net" in result: _deny()  # Not an owner fact.
            if "reward_name" not in selectors and result.get("rewards") != []: _deny()
            if "behavior_name" not in selectors and result.get("events") != []: _deny()
            if result.get("redemptions") != []: _deny()
        return deepcopy(value)
    except (ValueError, TypeError, KeyError, AttributeError):
        _deny()


def read_selection_parameters(session, principal, kind, result):
    value = _snapshot_selection(kind, result)
    if value is None:
        return None
    _, epoch = dialogue_actor_scope(session, principal)
    if value["owner_hash"] != canonical_hash({"user_id": principal.user_id, "sandbox_id": principal.sandbox_id}) \
            or value["actor_scope_hash"] != epoch:
        _deny()
    return deepcopy(value["selectors"])


def same_scoped_read_result(kind, stored, current):
    """No legacy shortcut: both must have a complete valid scoped snapshot."""
    if _snapshot_selection(kind, stored) is None or _snapshot_selection(kind, current) is None:
        _deny()
    body = lambda value: {key: item for key, item in value.items() if key != "message"}
    if stable_projection(kind, body(stored)) != stable_projection(kind, body(current)):
        _deny()
    return True


def validate_result_origin_selection(session, principal, kind, result, trace, node=None):
    """Independent v1 READ requirement; deleting result metadata is not legacy.

    Complete TaskTrace plus optional its exact current node are accepted. This
    supplements existing original-task/operation/source checks, not replaces
    them. All old unmarked producers retain their original handler semantics.
    Arbitrary rewriting of all database records and their independent hashes
    requires DB-administrator trust and is not claimed to be prevented here.
    """
    if kind not in FIELDS or not isinstance(trace, dict):
        return False
    from sqlalchemy import select
    from .models import Task, EvidenceSnapshot
    from .runtime_models import RuntimeRecord, RuntimeOperation, PlanSnapshot
    nodes = [node] if isinstance(node, dict) else [item for item in trace.get("nodes", [])
        if isinstance(item, dict) and item.get("action") == kind and item.get("result") == result]
    required = False
    for item in nodes:
        child = session.get(Task, item.get("child_task_id")) if item.get("child_task_id") else None
        if child is None:
            if KEY in result: _deny()
            continue
        evidence = list(session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.task_id == child.id,
                                                                       EvidenceSnapshot.sandbox_id == child.sandbox_id)))
        plans = child.plan_json.get("nodes", []) if isinstance(child.plan_json, dict) else []
        declared = any(isinstance(entry, dict) and entry.get("read_selection_version") == VERSION for entry in plans)
        declared = declared or any(row.source_version == VERSION or isinstance(row.payload_json, dict)
            and isinstance(row.payload_json.get(KEY), dict) and row.payload_json[KEY].get("schema_version") == VERSION for row in evidence)
        parent = session.get(Task, trace.get("task_id")) if trace.get("task_id") else None
        parent_record = session.get(RuntimeRecord, parent.id) if parent else None
        binding = (parent_record.metadata_json or {}).get("cloud_semantic_intent") if parent_record else None
        op = session.get(RuntimeOperation, item.get("operation_id")) if item.get("operation_id") else None
        if isinstance(binding, dict) and binding.get("read_entity_scope_projection") in {"selected-read-object-scope-v1", "selected-read-object-scope-v2"} \
                and op is not None and op.action_kind == kind:
            # This explicit version only exists after all four v1 consumers
            # were registered. An older bare product/reward slot cannot imply
            # a required marker. Preserve the original sealed contract/plan.
            args = op.args_json.get("slots")
            if isinstance(args, dict) and any(args.get(field) is not None for field in FIELDS[kind]) \
                    and (kind != "FAMILY_VIEW" or binding.get("read_entity_scope_projection") == "selected-read-object-scope-v2"):
                raw_binding = {key: value for key, value in binding.items() if key != "binding_hash"}
                if binding.get("binding_hash") != canonical_hash(raw_binding): _deny()
                declared = True
        # Original retained READ is an additional immutable snapshot anchor.
        # Its hash/turn proof is verified through the existing history loader,
        # without permission changes or rebuilding any historical bank facts.
        store = (parent_record.metadata_json or {}).get("conversation_read_snapshots") if parent_record else None
        retained = []
        if isinstance(store, dict) and isinstance(store.get("items"), list):
            for saved in store["items"]:
                if not isinstance(saved, dict): _deny()
                facts = saved.get("facts", [])
                if any(isinstance(fact, dict) and fact.get("operation_id") == item.get("operation_id") for fact in facts):
                    from .customer_read_history import _load
                    _, checked = _load(session, principal, saved.get("source_conversation_id"), saved.get("source_turn_id"))
                    retained.extend(fact for fact in checked["facts"] if fact.get("operation_id") == item.get("operation_id"))
            declared = declared or any(isinstance(fact.get("result", {}).get(KEY), dict)
                and fact["result"][KEY].get("schema_version") == VERSION for fact in retained)
        if not declared and KEY not in result:
            continue
        required = True
        scope = _snapshot_selection(kind, result)
        if scope is None: _deny()
        read_selection_parameters(session, principal, kind, result)
        if parent is None or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                or (child.user_id, child.sandbox_id) != (principal.user_id, principal.sandbox_id): _deny()
        record = session.get(RuntimeRecord, child.id)
        if not record or record.kind != "CHILD" or record.root_task_id != parent.id or not op \
                or record.metadata_json.get("operation_id") != op.id or op.parent_task_id != parent.id \
                or op.child_task_id != child.id or op.action_kind != kind or op.sandbox_id != principal.sandbox_id:
            _deny()
        from .intelligence import action_signature
        if op.fingerprint != canonical_hash(action_signature(op.args_json)) or op.args_json.get("kind") != kind \
                or child.plan_hash != canonical_hash(child.plan_json): _deny()
        # An immutable parent plan independently keeps this operation's exact
        # action arguments. Bare product_name on a genuinely older unfiltered
        # operation alone never creates a new marker requirement.
        snapshots = list(session.scalars(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id)))
        matching = [saved for saved in snapshots if any(isinstance(entry, dict) and entry.get("operation_id") == op.id
            and entry.get("args") == op.args_json for entry in (saved.plan_json or {}).get("nodes", []))]
        if not matching or any(saved.plan_hash != canonical_hash(saved.plan_json) for saved in matching): _deny()
        marked_nodes = [entry for entry in plans if isinstance(entry, dict) and entry.get("read_selection_version") == VERSION
                        and entry.get("read_selection_hash") == scope["scope_hash"]]
        if len(marked_nodes) != 1 or marked_nodes[0].get("args") != scope["selectors"]: _deny()
        marked_evidence = [row for row in evidence if row.source_version == VERSION and isinstance(row.payload_json, dict)
            and row.payload_json.get(KEY) == scope]
        if len(marked_evidence) != 1: _deny()
        snapshot_body = {key: value for key, value in marked_evidence[0].payload_json.items() if key != "message"}
        actual_body = {key: value for key, value in result.items() if key != "message"}
        if snapshot_body != actual_body: _deny()
    return required


def safe_selection_summary(kind, result):
    """For an already owned/original-evidence-verified BFF trace, not raw input.

    Only dimensions and their finite count leave this internal proof module.
    Epoch/owner/id/version/hash are never customer display fields. Invalid
    marked scope must not be reinterpreted as an unfiltered zero-result read.
    """
    if not isinstance(result, dict) or KEY not in result:
        return {"status": "UNFILTERED"}
    try:
        value = _snapshot_selection(kind, result)
        return {"status": "FILTERED", "read_kind": kind,
            "selected_dimensions": [_DIMENSIONS[field][0] for field in value["selectors"]],
            "selected_counts": {_DIMENSIONS[field][0]: 1 for field in value["selectors"]}}
    except DomainError:
        return {"status": "UNAVAILABLE_SCOPE"}
