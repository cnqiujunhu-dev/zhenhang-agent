"""Question-shaped REQUEST replies from the existing verified read result.

No bank tool, intent parser, quote or new permission lives here. The database
adapter audits the stored semantic/plan/child chain; the pure consumer reuses
the same typed fact renderer as CONSULT. Opaque evidence stays in the backend.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import re
import secrets

from sqlalchemy import select

from .planning import canonical_hash

KEY = "customer_read_question_facts"
VERSION = "customer-read-question-facts-v1"
COST_VERSION = "customer-read-question-cost-facts-v2"
SUBSCRIPTION_VERSION = "customer-read-question-subscription-authorization-v3"
SUBSCRIPTION_STATUS_VERSION = "customer-read-question-subscription-status-v4"
ASSET_DETAIL_VERSION = "customer-read-question-asset-details-v5"
ASSET_DETAIL_ASPECTS = frozenset({'PRICE', 'VALUATION', 'TIME_SCOPE', 'PROFIT_LOSS'})
SNAPSHOT_ASPECTS = {
    "SUBSCRIPTION_LIST": frozenset({"SUMMARY", "CHARGE", "STATUS", "PRICE"}),
    "CARBON_VIEW": frozenset({"SUMMARY", "POINTS", "SETTLEMENT", "ELIGIBILITY"}),
    "MEMBERSHIP_VIEW": frozenset({"SUMMARY", "STATUS"}),
    "BENEFITS_VIEW": frozenset({"SUMMARY", "STATUS", "ELIGIBILITY", "BENEFITS"}),
    "PREFERENCE_VIEW": frozenset({"SUMMARY", "STATUS", "PREFERENCES"}),
}
SUPPORTED_ASPECTS = frozenset({"ACCOUNT_RELATIONSHIP", "HOLDINGS", "QUANTITY", "COST_BASIS"}).union(ASSET_DETAIL_ASPECTS, *SNAPSHOT_ASPECTS.values())
READ_KINDS = frozenset({"CARD_STATUS", "ASSET_POSITIONS_READ", *SNAPSHOT_ASPECTS})
QUANTITY_FIELDS = ("total_quantity", "available_quantity", "frozen_quantity")
_DISPLAY_SEAL_KEY = secrets.token_bytes(32)


def _source_seal(payload):
    return hmac.new(_DISPLAY_SEAL_KEY, canonical_hash(payload).encode("ascii"), hashlib.sha256).hexdigest()


def valid_source_seal(value):
    """Authentication bit only; never a nonce, secret or authorization grant."""
    if not isinstance(value, dict):
        return False
    payload = deepcopy(value)
    seal = payload.pop("source_seal", None)
    try:
        return isinstance(seal, str) and hmac.compare_digest(seal, _source_seal(payload))
    except (TypeError, ValueError):
        return False


def _snapshot_objects(kind, result):
    """Finite adapters to CONSULT's registered fields, never a bank refresh."""
    from .dialogue_result_aspects import ASPECT_FIELDS
    objects = []
    def add(identity, label, domain, fields):
        if not isinstance(identity, str) or not identity or not isinstance(label, str) or not label.strip():
            raise ValueError("READ_OBJECT_IDENTITY_MISSING")
        objects.append({"identity": kind + ":" + identity, "label": label, "domain": domain, "fields": deepcopy(fields)})
    if kind == "SUBSCRIPTION_LIST":
        rows = result.get("subscriptions")
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("READ_COLLECTION_MISSING")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("READ_OBJECT_MISSING")
            name = row.get("merchant")
            fields = {key: deepcopy(row[key]) for key in (*ASPECT_FIELDS["STATUS"], *ASPECT_FIELDS["CHARGE"]) if key in row}
            if row.get('source_type') not in {None, 'BANK_MANDATE', 'MERCHANT_SIM', 'EXTERNAL_UNCONNECTED'}:
                raise ValueError('READ_SUBSCRIPTION_AUTHORITY_UNSUPPORTED')
            fields['source_type'] = row.get('source_type')
            # The registered subscription capability is CNY-only. A recorded
            # debit is not catalog_monthly_price_minor or a public PRICE fact.
            if row.get("currency", "CNY") != "CNY":
                raise ValueError("READ_CURRENCY_UNSUPPORTED")
            fields["currency"] = "CNY"
            fields["billing_months"] = row.get("billing_months") if type(row.get("billing_months")) is int and 1 <= row["billing_months"] <= 12 else None
            fields["amount_minor"] = row.get("amount_minor") if type(row.get("amount_minor")) is int and 0 <= row["amount_minor"] <= 2**53-1 else None
            for field in ("auto_renew", "entitled"):
                fields[field] = row.get(field) if type(row.get(field)) is bool else None
            fields["status"] = row.get("status") if row.get("status") in {"ACTIVE", "CANCELLED"} else None
            add(row.get("subscription_id") or name, name, "SUBSCRIPTIONS", fields)
    elif kind == "CARBON_VIEW":
        from .carbon_settlement import valid_reward_settlement
        wallet = result.get("wallet")
        if isinstance(wallet, dict):
            value = wallet.get("points")
            add("wallet", "我的低碳积分", "CARBON", {"points": value if type(value) is int and 0 <= value <= 2**53-1 else None})
        for collection, identity, label, keys in (("rewards", "reward_id", "reward_name", ("cost_points", "stock", "can_redeem", "settlement")),
                ("events", "behavior_id", "behavior_name", ("estimated_points", "status"))):
            rows = result.get(collection, [])
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("READ_COLLECTION_MISSING")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("READ_OBJECT_MISSING")
                fields = {key: deepcopy(row[key]) for key in keys if key in row}
                if "settlement" in fields and not valid_reward_settlement(fields["settlement"]):
                    raise ValueError("READ_SETTLEMENT_UNVERIFIED")
                for key in ("cost_points", "stock", "estimated_points"):
                    if key in fields and (type(fields[key]) is not int or not 0 <= fields[key] <= 2**53-1):
                        fields[key] = None
                if "can_redeem" in fields and type(fields["can_redeem"]) is not bool:
                    fields["can_redeem"] = None
                add(row.get(identity), row.get(label), "CARBON", fields)
    else:
        from .customer_profile_common import SCHEMA_VERSION, read_body_fingerprint
        from .customer_profile_facts import profile_result_fact_entities
        if result.get("schema_version") != SCHEMA_VERSION or result.get("action") != kind \
                or result.get("bank_state_changed") is not False or result.get("read_fingerprint") != read_body_fingerprint(result):
            raise ValueError("PROFILE_READ_SOURCE_CHANGED")
        # The same pure entity projection is safe after the immutable stored
        # result audit below; this is not a claim that a new bank read occurred.
        for entity in profile_result_fact_entities(kind, result):
            if kind == "PREFERENCE_VIEW" and entity["field"] != "preference_collection":
                continue  # Inferred suggestions never become saved preferences.
            identity = entity["record_id"]
            if kind == "PREFERENCE_VIEW":
                identity += ":" + canonical_hash(result.get("query") or {})
            add(identity, entity["label"], entity["domain"], entity["facts"])
    return objects


def _projection(trace, *, include_cost=False, include_asset_details=False):
    """Exact display-relevant fields survive the BFF's diagnostic stripping."""
    if not isinstance(trace, dict) or trace.get("state") != "SUCCEEDED" or trace.get("pending_confirmation") \
            or (trace.get("outcome") or {}).get("verified") is not True \
            or (trace.get("outcome") or {}).get("kind") != "QUERY_RESULT":
        return None
    nodes = trace.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 30:
        return None
    projected = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("action") not in READ_KINDS or node.get("status") != "SUCCEEDED" \
                or not isinstance(node.get("result"), dict):
            return None
        result = node["result"]
        kind = node["action"]
        if kind in SNAPSHOT_ASPECTS:
            try:
                objects = _snapshot_objects(kind, result)
            except (TypeError, ValueError, KeyError, AttributeError):
                return None
            projected.append({"node_id": node.get("node_id"), "operation_id": node.get("operation_id"),
                "child_task_id": node.get("child_task_id"), "action": kind, "rows": objects})
            continue
        rows = result.get("cards" if kind == "CARD_STATUS" else "positions")
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
            return None
        keys = (("card_id", "last4", "display_last4", "status", "balance_scope_refs") if kind == "CARD_STATUS" else
            ("position_id", "instrument_id", "product_name", "asset_class", "quantity_unit", *QUANTITY_FIELDS))
        if include_cost and kind == "ASSET_POSITIONS_READ":
            keys += ("remaining_cost_minor", "currency")
        if include_asset_details and kind == "ASSET_POSITIONS_READ":
            keys += ('currency', 'market_value_minor', 'reference_price', 'price_observed_at',
                'price_effective_at', 'price_source', 'price_is_live', 'synthetic',
                'remaining_cost_minor', 'average_cost', 'unrealized_pnl_minor', 'return_pct')
        projected.append({"node_id": node.get("node_id"), "operation_id": node.get("operation_id"),
            "child_task_id": node.get("child_task_id"), "action": kind,
            "rows": [{key: deepcopy(row[key]) for key in keys if key in row} for row in rows]})
        if kind == "CARD_STATUS" and "credit_cards" in result:
            credits = result["credit_cards"]
            if not isinstance(credits, list) or len(credits) > 100 or any(not isinstance(row, dict) for row in credits):
                return None
            projected[-1]["credit_rows"] = [{key: deepcopy(row[key]) for key in ("card_id", "status", "display_last4") if key in row} for row in credits]
    if any(not isinstance(node.get(key), str) or not node[key] for node in projected
            for key in ("node_id", "operation_id", "child_task_id")) \
            or len({node["operation_id"] for node in projected}) != len(projected):
        return None
    return {"task_id": trace.get("task_id"), "task_version": trace.get("version"), "nodes": projected}


def _deduplicated(projection, kind, identity):
    rows, conflict = {}, False
    for node in projection["nodes"]:
        if node["action"] != kind:
            continue
        for row in node["rows"]:
            identifier = row.get(identity)
            if not isinstance(identifier, str) or not identifier:
                conflict = True
                continue
            if identifier in rows and rows[identifier] != row:
                conflict = True
            rows[identifier] = row
    return list(rows.values()), conflict


def _card_count_summary(projection, english):
    """Count exact queried instruments, never balances or currency wallets.

    Missing legacy credit collections are unknown, not zero. Both the credit
    identities and debit identities are inside the authenticated projection.
    """
    nodes = [node for node in projection["nodes"] if node["action"] == "CARD_STATUS"]
    if not nodes or any("credit_rows" not in node for node in nodes):
        return None
    counts, identities = {}, set()
    for kind, collection, states in (("DEBIT", "rows", {"ACTIVE", "LOCKED", "LOST", "CLOSED"}),
            ("CREDIT", "credit_rows", {"ACTIVE", "LOCKED", "LOST", "CLOSED", "ISSUED", "REPLACED"})):
        seen = {}
        for node in nodes:
            for row in node[collection]:
                identifier = row.get("card_id")
                if not isinstance(identifier, str) or not identifier or row.get("status") not in states \
                        or identifier in seen and seen[identifier] != row:
                    return None
                seen[identifier] = row
        if identities.intersection(seen):
            return None
        identities.update(seen)
        counts[kind] = len(seen)
    total = sum(counts.values())
    return (f"This query found {total} cards: {counts['DEBIT']} debit and {counts['CREDIT']} credit."
        if english else f"本次查到{total}张卡：{counts['DEBIT']}张借记卡、{counts['CREDIT']}张信用卡。")


def _typed_facts(projection, aspects, *, subscription_authority=False, subscription_status=False):
    from .dialogue_result_aspects import ASPECT_FIELDS, AUTHORITY, UNIT
    facts = []
    if "ACCOUNT_RELATIONSHIP" in aspects and any(node["action"] == "CARD_STATUS" for node in projection["nodes"]):
        rows, conflict = _deduplicated(projection, "CARD_STATUS", "card_id")
        cards = []
        for row in rows:
            scopes = row.get("balance_scope_refs")
            if row.get("status") not in {"ACTIVE", "LOCKED", "LOST", "CLOSED"} \
                    or not isinstance(scopes, dict) or any(not isinstance(currency, str) for currency in scopes):
                conflict = True
                continue
            # Compare only backend-owned same-currency scope references. Cash
            # amounts are deliberately absent from this entire projection.
            cards.append({"label": "银行卡", "scopes": {currency: deepcopy(value) for currency, value in scopes.items()
                if currency in {"CNY", "USD"} and isinstance(value, str)
                and re.fullmatch(re.escape(currency) + r":[a-f0-9]{64}", value)}, "result_ref": row["card_id"]})
        facts.append({"fact_id": "request.card_account_relationship", "field": "ACCOUNT_RELATIONSHIP", "unit": UNIT,
            "currency": "NONE", "value": {"authority": AUTHORITY, "aspect": "ACCOUNT_RELATIONSHIP",
                "cards": [] if conflict else cards, "bank_state_changed": False}})
    if set(aspects) & {"HOLDINGS", "QUANTITY"} and any(node["action"] == "ASSET_POSITIONS_READ" for node in projection["nodes"]):
        rows, conflict = _deduplicated(projection, "ASSET_POSITIONS_READ", "position_id")
        if conflict:
            rows = []  # Never select one of two contradictory copies.
        for index, row in enumerate(rows):
            expected_unit = {"WEALTH": "UNIT", "STOCK": "SHARE", "PRECIOUS_METAL": "GRAM"}.get(row.get("asset_class"))
            unit_valid = expected_unit is not None and row.get("quantity_unit") == expected_unit
            fields = {"quantity_unit": expected_unit if unit_valid else None}
            for name in QUANTITY_FIELDS:
                value = row.get(name)
                try:
                    valid = unit_valid and isinstance(value, str) and bool(value) and Decimal(value).is_finite() and Decimal(value) >= 0
                except (InvalidOperation, ValueError):
                    valid = False
                fields[name] = value if valid else None
            facts.append({"fact_id": "request.position_quantity." + str(index), "field": "QUANTITY", "unit": UNIT,
                "currency": "NONE", "value": {"authority": AUTHORITY, "aspect": "QUANTITY", "domain": "ASSETS",
                    "label": row.get("product_name") if isinstance(row.get("product_name"), str) else "持仓标的",
                    "fields": fields, "bank_state_changed": False}})
    if "COST_BASIS" in aspects and any(node["action"] == "ASSET_POSITIONS_READ" for node in projection["nodes"]):
        rows, conflict = _deduplicated(projection, "ASSET_POSITIONS_READ", "position_id")
        if conflict:rows=[]
        for index, row in enumerate(rows):
            fields={key:deepcopy(row[key]) for key in ("remaining_cost_minor","currency") if key in row}
            facts.append({"fact_id":"request.position_cost."+str(index),"field":"COST_BASIS","unit":UNIT,
                "currency":"NONE","value":{"authority":AUTHORITY,"aspect":"COST_BASIS","domain":"ASSETS",
                    "label":row.get("product_name") if isinstance(row.get("product_name"),str) else "持仓标的",
                    "fields":fields,"bank_state_changed":False}})
        if not rows:
            facts.append({"fact_id":"request.position_cost.unavailable","field":"COST_BASIS","unit":UNIT,
                "currency":"NONE","value":{"authority":AUTHORITY,"aspect":"COST_BASIS","domain":"ASSETS",
                    "label":"本次查询范围","fields":{},"bank_state_changed":False}})
    if set(aspects) & ASSET_DETAIL_ASPECTS and any(node['action'] == 'ASSET_POSITIONS_READ' for node in projection['nodes']):
        rows, conflict = _deduplicated(projection, 'ASSET_POSITIONS_READ', 'position_id')
        if conflict:rows=[]
        for index, row in enumerate(rows):
            fields = {}
            for aspect in aspects:
                if aspect not in ASSET_DETAIL_ASPECTS:
                    continue
                names = ASPECT_FIELDS[aspect] if aspect != 'TIME_SCOPE' else ('price_observed_at', 'price_effective_at', 'price_source')
                for name in names:
                    if name in row:fields[name]=deepcopy(row[name])
            for name in ('currency', 'quantity_unit', 'synthetic', 'price_is_live'):
                if name in row:fields[name]=deepcopy(row[name])
            facts.append({'fact_id':'request.position_details.'+str(index),'field':'ASSET_DETAILS','unit':UNIT,
                'currency':'NONE','value':{'authority':AUTHORITY,'aspect':'ASSET_DETAILS','domain':'ASSETS',
                    'label':row.get('product_name') if isinstance(row.get('product_name'),str) else '持仓标的',
                    'fields':fields,'requested_aspects':[aspect for aspect in aspects if aspect in ASSET_DETAIL_ASPECTS],
                    'bank_state_changed':False}})
        if not rows:
            facts.append({'fact_id':'request.position_details.unavailable','field':'ASSET_DETAILS','unit':UNIT,
                'currency':'NONE','value':{'authority':AUTHORITY,'aspect':'ASSET_DETAILS','domain':'ASSETS',
                    'label':'本次查询范围','fields':{},'bank_state_changed':False}})
    for kind, allowed in SNAPSHOT_ASPECTS.items():
        chosen = [aspect for aspect in aspects if aspect in allowed]
        nodes = [node for node in projection["nodes"] if node["action"] == kind]
        if not chosen or not nodes:
            continue
        domain = {"SUBSCRIPTION_LIST": "SUBSCRIPTIONS", "CARBON_VIEW": "CARBON", "MEMBERSHIP_VIEW": "MEMBERSHIP",
            "BENEFITS_VIEW": "BENEFITS", "PREFERENCE_VIEW": "PREFERENCES"}[kind]
        first_fact = len(facts)
        objects, conflict = {}, False
        for node in nodes:
            for obj in node["rows"]:
                if obj["identity"] in objects and objects[obj["identity"]] != obj:
                    conflict = True
                objects[obj["identity"]] = obj
        if conflict:
            objects = {}
        if not objects:
            # A missing/empty/conflicting collection supplies no amount,
            # active setting, eligibility or successful historical effect.
            objects["missing"] = {"label": "本次查询范围", "domain": domain, "fields": {}}
        for index, obj in enumerate(objects.values()):
            source = obj["fields"]
            if subscription_status and kind == 'SUBSCRIPTION_LIST':
                # A new display policy only. Original v1/v3 fact payloads and
                # result/source fingerprints remain unchanged. Historical
                # next_due_at stays in the audited projection, not prose.
                names = ('source_type', 'status', 'auto_renew', 'entitled', 'entitlement_end_at')
                if 'SUMMARY' in chosen:
                    names += ('amount_minor', 'billing_months', 'currency')
                fields = {name: deepcopy(source[name]) for name in names if name in source}
                facts.append({'fact_id': 'request.' + kind.lower() + '.' + str(index),
                    'field': 'SUBSCRIPTION_AUTHORIZATION', 'unit': UNIT, 'currency': 'NONE',
                    'value': {'authority': AUTHORITY, 'aspect': 'SUBSCRIPTION_AUTHORIZATION',
                        'domain': obj['domain'], 'label': obj['label'], 'fields': fields,
                        'requested_aspects': list(chosen), 'bank_state_changed': False}})
                continue
            names = set()
            for aspect in chosen:
                selected_aspect = "PREFERENCES" if kind == "PREFERENCE_VIEW" and aspect == "STATUS" else aspect
                if selected_aspect == "SUMMARY":
                    names.update(name for fields in ASPECT_FIELDS.values() for name in fields)
                else:
                    names.update(ASPECT_FIELDS.get(selected_aspect, ()))
            if subscription_authority and kind == 'SUBSCRIPTION_LIST' and set(chosen) & {'CHARGE', 'PRICE'}:
                names.update((*ASPECT_FIELDS['STATUS'], *ASPECT_FIELDS['CHARGE']))
                names.add('source_type')
            fields = {name: deepcopy(value) for name, value in source.items() if name in names}
            if kind == "CARBON_VIEW" and not fields:
                continue  # A wallet has no reward-settlement policy, for example.
            if kind == "SUBSCRIPTION_LIST" and chosen == ["PRICE"] and not subscription_authority:
                fields = {}  # Public price requires separate PUBLIC_TERMS/catalog proof.
            elif "currency" in source:
                fields["currency"] = source["currency"]
            if kind == "SUBSCRIPTION_LIST" and "PRICE" in chosen and not subscription_authority:
                fields["unit_price_minor"] = None  # Never silently answer PRICE with CHARGE.
            if subscription_authority and kind == 'SUBSCRIPTION_LIST' and set(chosen) & {'CHARGE', 'PRICE'}:
                fields['subscription_authorization_version'] = 'owned-subscription-charge-v1'
            facts.append({"fact_id": "request." + kind.lower() + "." + str(index), "field": chosen[0] if len(chosen) == 1 else "SUMMARY",
                "unit": UNIT, "currency": "NONE", "value": {"authority": AUTHORITY, "aspect": chosen[0] if len(chosen) == 1 else "SUMMARY",
                    "domain": obj["domain"], "label": obj["label"], "fields": fields, "bank_state_changed": False}})
        if len(facts) == first_fact:
            facts.append({"fact_id": "request." + kind.lower() + ".unavailable", "field": chosen[0], "unit": UNIT,
                "currency": "NONE", "value": {"authority": AUTHORITY, "aspect": chosen[0], "domain": domain,
                    "label": "本次查询范围", "fields": {}, "bank_state_changed": False}})
    return facts


def attach_read_question_facts(session, principal, parent, trace):
    """Audit stored source/results only; no extra bank-data refresh or writes."""
    trace = {key: value for key, value in trace.items() if key != KEY}
    projection = _projection(trace)
    if projection is None:
        return trace
    from .runtime_models import RuntimeRecord, PlanSnapshot
    record = session.get(RuntimeRecord, parent.id)
    binding = (record.metadata_json or {}).get("cloud_semantic_intent") if record else None
    decision = binding.get("decision") if isinstance(binding, dict) else None
    aspects = decision.get("question_aspects", []) if isinstance(decision, dict) else []
    if not isinstance(aspects, list) or any(not isinstance(aspect, str) for aspect in aspects):
        return trace
    selected = [aspect for aspect in dict.fromkeys(aspects) if aspect in SUPPORTED_ASPECTS]
    if not selected:
        return trace
    version = COST_VERSION if "COST_BASIS" in selected else VERSION
    asset_details = bool(set(selected) & ASSET_DETAIL_ASPECTS and any(node['action'] == 'ASSET_POSITIONS_READ' for node in projection['nodes']))
    if asset_details:
        version = ASSET_DETAIL_VERSION
    if not asset_details and set(selected) & {'CHARGE', 'PRICE'} and any(node['action'] == 'SUBSCRIPTION_LIST' for node in projection['nodes']):
        version = SUBSCRIPTION_VERSION
    elif not asset_details and set(selected) & {'STATUS', 'SUMMARY'} and any(node['action'] == 'SUBSCRIPTION_LIST' for node in projection['nodes']):
        version = SUBSCRIPTION_STATUS_VERSION
    if 'COST_BASIS' in selected or asset_details:
        projection = _projection(trace, include_cost='COST_BASIS' in selected, include_asset_details=asset_details)
    unavailable = {"schema_version": version, "status": "UNAVAILABLE", "reason": "SOURCE_OR_ACCESS_CHANGED"}
    try:
        if (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                or trace.get("task_id") != parent.id or trace.get("version") != parent.version or parent.state != "SUCCEEDED" \
                or trace.get("audit_chain_valid") is not True or not record or record.kind != "PARENT" or not record.plan_version:
            raise ValueError("SOURCE_CHANGED")
        from .semantic_proposal import validate_semantic_source_snapshot
        snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
            PlanSnapshot.version == record.plan_version))
        proof = validate_semantic_source_snapshot(session, principal, parent, record.metadata_json, snapshot)
        if not proof or any(action.get("kind") not in READ_KINDS for action in proof["proposal_json"]["actions"]):
            raise ValueError("SOURCE_CHANGED")
        # Reuse the immutable READ chain audit. For these two read kinds it
        # loads only existing Task/Operation/Plan/actor records, not bank tools.
        from .customer_read_history import _live_read, _load, STORE_KEY, TURN_PROOF_KEY
        from .models import Task
        from .conversation_models import ChatTurn
        from .simulation import as_utc
        source = session.get(ChatTurn, binding["source_turn_id"])
        if record.metadata_json.get("customer_read_snapshot_unavailable"):
            raise ValueError("SOURCE_CHANGED")
        retained = None
        if STORE_KEY in record.metadata_json or source and TURN_PROOF_KEY in (source.diagnostics_json or {}):
            _original, retained = _load(session, principal, binding["conversation_id"], binding["source_turn_id"])
            if retained["task_version"] != parent.version:
                raise ValueError("SOURCE_CHANGED")
        original_facts = {fact["operation_id"]: fact for fact in retained["facts"]} if retained else None
        if original_facts is not None and set(original_facts) != {node["operation_id"] for node in trace["nodes"]}:
            raise ValueError("SOURCE_CHANGED")
        for node in trace["nodes"]:
            child = session.get(Task, node["child_task_id"])
            fact = {"task_id": parent.id, "task_version": parent.version,
                "node_id": node["node_id"], "operation_id": node["operation_id"], "child_task_id": node["child_task_id"],
                "action": node["action"], "status": node["status"], "result": node["result"],
                "analysis": child.analysis_json or None if child else None,
                "observed_at": as_utc(child.updated_at).isoformat() if child else None}
            if original_facts is not None and any(original_facts[node["operation_id"]].get(key) != value for key, value in fact.items()):
                raise ValueError("SOURCE_CHANGED")
            _live_read(session, principal, parent, snapshot, fact)
        subscription_authority = version == SUBSCRIPTION_VERSION or asset_details and bool(set(selected) & {'CHARGE','PRICE'})
        subscription_status = version == SUBSCRIPTION_STATUS_VERSION or asset_details and not subscription_authority and bool(set(selected) & {'STATUS','SUMMARY'})
        facts = _typed_facts(projection, selected, subscription_authority=subscription_authority,
            subscription_status=subscription_status)
        value = {"schema_version": version, "status": "READY", "task_id": parent.id, "task_version": parent.version,
            "binding_hash": proof["binding_hash"], "result_projection_hash": canonical_hash(projection),
            "question_aspects": selected, "facts": facts, "bank_state_changed": False}
        value["payload_hash"] = canonical_hash(value)
        value["source_seal"] = _source_seal(value)
        return {**trace, KEY: value}
    except Exception:
        # Losing an explanation must not fail/reissue the successful READ or
        # turn stale metadata into an affirmative relationship/quantity claim.
        return {**trace, KEY: unavailable}


def read_question_response(trace, language="zh-CN"):
    """Pure, narrow display consumer, using CONSULT's exact typed renderer."""
    try:
        value = trace.get(KEY)
        subscription_version = isinstance(value, dict) and value.get('schema_version') == SUBSCRIPTION_VERSION
        subscription_status_version = isinstance(value, dict) and value.get('schema_version') == SUBSCRIPTION_STATUS_VERSION
        cost_version = isinstance(value,dict) and (value.get("schema_version") == COST_VERSION
            or (subscription_version or subscription_status_version or value.get('schema_version') == ASSET_DETAIL_VERSION)
                and 'COST_BASIS' in value.get('question_aspects', []))
        asset_details = isinstance(value,dict) and value.get('schema_version') == ASSET_DETAIL_VERSION
        projection = _projection(trace, include_cost=cost_version, include_asset_details=asset_details)
        if not isinstance(value, dict) or value.get("schema_version") not in {VERSION,COST_VERSION,SUBSCRIPTION_VERSION,SUBSCRIPTION_STATUS_VERSION,ASSET_DETAIL_VERSION} or value.get("status") != "READY" \
                or projection is None or value.get("bank_state_changed") is not False \
                or value.get("task_id") != trace.get("task_id") or value.get("task_version") != trace.get("version"):
            return None
        payload = deepcopy(value)
        if not valid_source_seal(value):
            return None
        payload.pop("source_seal", None)
        if payload.pop("payload_hash", None) != canonical_hash(payload) or value.get("result_projection_hash") != canonical_hash(projection):
            return None
        aspects = value.get("question_aspects")
        mixed_subscription_authority = asset_details and isinstance(aspects,list) and bool(set(aspects) & {'CHARGE','PRICE'})
        mixed_subscription_status = asset_details and isinstance(aspects,list) and not mixed_subscription_authority and bool(set(aspects) & {'STATUS','SUMMARY'})
        if not isinstance(aspects, list) or not aspects or any(aspect not in SUPPORTED_ASPECTS for aspect in aspects) \
                or cost_version != ("COST_BASIS" in aspects) \
                or asset_details and (not set(aspects) & ASSET_DETAIL_ASPECTS or not any(node['action']=='ASSET_POSITIONS_READ' for node in projection['nodes'])) \
                or subscription_version and (not set(aspects) & {'CHARGE', 'PRICE'} or not any(node['action']=='SUBSCRIPTION_LIST' for node in projection['nodes'])) \
                or subscription_status_version and (not set(aspects) & {'STATUS', 'SUMMARY'} or set(aspects) & {'CHARGE', 'PRICE'}
                    or not any(node['action']=='SUBSCRIPTION_LIST' for node in projection['nodes'])) \
                or len(set(aspects)) != len(aspects) or value.get("facts") != _typed_facts(projection, aspects,
                    subscription_authority=subscription_version or mixed_subscription_authority,
                    subscription_status=subscription_status_version or mixed_subscription_status):
            return None
        from .dialogue_result_aspects import render_query_fact
        rendered = [(text, fact["field"] == "QUANTITY") for fact in value["facts"]
            if (text := render_query_fact(fact, language))]
        english = language == "en"
        ending = []
        quantity_count = sum(is_quantity for _text, is_quantity in rendered)
        object_count = len(rendered)
        positions_only = all(node["action"] == "ASSET_POSITIONS_READ" for node in projection["nodes"])
        if set(aspects) & {"HOLDINGS", "QUANTITY"} and any(node["action"] == "ASSET_POSITIONS_READ" for node in projection["nodes"]):
            if not any(fact["field"] == "QUANTITY" for fact in value["facts"]):
                ending.append("The queried scope has no verifiable position quantities; missing values are not zero." if english else
                    "本次查询范围没有可核实的持仓数量；缺失资料不会当作零持仓。")
            ending.append("Each instrument keeps its own unit; wealth units, stock shares and metal grams cannot be added together." if english else
                "数量按标的分别计算：理财用份、股票用股、贵金属用克，不能把不同标的和单位的数量相加。")
        def coverage(count):
            if positions_only:
                return (f"Another {count} position(s) are not expanded in this reply; view each result card. No cross-instrument quantity total is implied." if english else
                    f"其余{count}项持仓数量未在这段文字中展开，请在结果卡逐项查看；未合计为总数量。")
            return (f"Another {count} queried object(s) are not expanded here; view each result card." if english else
                f"其余{count}项查询对象未在这段文字中展开，请在结果卡逐项查看。")
        # Never cut a typed quantity/value or its unit in half. Reserve the
        # counting boundary and an explicit omitted-record notice before
        # packing whole fact paragraphs; an oversized paragraph is omitted.
        card_count = _card_count_summary(projection, english)
        lines, shown_objects = ([card_count] if card_count else []), 0
        reserve = [*ending, *([coverage(object_count)] if object_count else [])]
        for text, is_quantity in rendered:
            if len("\n".join([*lines, text, *reserve])) <= 2300:
                lines.append(text)
                shown_objects += 1
        if object_count > shown_objects:
            lines.append(coverage(object_count - shown_objects))
        lines.extend(ending)
        if not lines:
            return None
        return {"intro": "Here is what the verified query establishes." if english else "按本次已核验的查询结果说明：",
            "detail": "\n".join(lines), "detail_kind": ({"CARD_STATUS": "CARDS", "ASSET_POSITIONS_READ": "ASSETS",
                "SUBSCRIPTION_LIST": "SUBSCRIPTIONS", "CARBON_VIEW": "CARBON", "MEMBERSHIP_VIEW": "BENEFITS", "BENEFITS_VIEW": "BENEFITS",
                "PREFERENCE_VIEW": "PREFERENCES"}[projection["nodes"][0]["action"]] if len({node["action"] for node in projection["nodes"]}) == 1 else "MIXED"),
            "state_kind": "QUERY_RESULT", "read_only": True, "reply_language": language}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
