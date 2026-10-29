"""Named registered public product rules, separate from bank result objects.

The cloud decides CONSULT versus a business request. This module only resolves
an exact registered product name or ID after that sealed decision, never intent
wording. Public literature does not supply live prices, holdings, eligibility,
personal suitability or any bank execution authority.
"""
from copy import deepcopy
import json
import re

from . import financial_product_catalog as registry
from .contracts import DomainError
from .planning import canonical_hash

VERSION = "named-public-wealth-source-v1"
SOURCE_KIND = "REGISTERED_PUBLIC_PRODUCT_RULES"
AUTHORITY = "PUBLIC_PRODUCT_LITERATURE_NOT_BANK_POSITION_PRICE_OR_SUITABILITY"
_BINDING_FIELDS = {"version", "conversation_id", "turn_id", "actor_scope_hash", "source_message_hash",
    "semantic_receipt_hash", "input_hash", "offered_context", "offered_hash", "product_key", "catalog_version",
    "bank_write_authorized"}


def _deny():
    raise DomainError("PUBLIC_PRODUCT_SOURCE_CHANGED", "这项产品说明暂时不能核对，请重新查询。", 409)


def named_key(message):
    """Canonical registered name/ID match only; no risk/price wording dictionary.

    No approximate match or arbitrarily first product. Names from uploaded
    documents, assistant/RAG history or a private quote are never a registry.
    """
    keys = set()
    for item in registry.product_catalog():
        names = (item["name_zh"], item["name_en"], item["product_key"])
        for name in names:
            pattern = re.escape(name)
            if name.isascii():
                pattern = r"(?<![A-Za-z0-9_-])" + pattern + r"(?![A-Za-z0-9_-])"
            if re.search(pattern, message, re.I):
                keys.add(item["product_key"])
    return next(iter(keys)) if len(keys) == 1 else None


def _public_content(key):
    item = registry.product_detail(key)
    if item is None:
        _deny()
    # These exact fields are PUBLIC literature. Explicit allowlist means added
    # future private fields cannot be accidentally exposed as public facts.
    fields = ("product_key", "catalog_version", "name_zh", "name_en",
              "illustrative_risk_level", "risk_level_is_current_bank_fact",
              "risk_note", "principal_guaranteed", "return_guaranteed", "guidance_only",
              "currency", "audience", "fees", "exit_conditions", "minimum", "availability")
    value = {key: deepcopy(item[key]) for key in fields}
    value["risk_note_en"] = registry.product_detail(key, "en")["risk_note"]
    english = registry.product_detail(key, "en")
    for field in ("audience", "fees", "exit_conditions", "minimum", "availability"):
        value[field + "_en"] = english[field]
    if (type(value["illustrative_risk_level"]) is not int
            or not 1 <= value["illustrative_risk_level"] <= 5
            or value["risk_level_is_current_bank_fact"] is not False
            or value["principal_guaranteed"] is not False
            or value["return_guaranteed"] is not False
            or value["guidance_only"] is not True):
        _deny()
    return value


def selected_product_keys(message, decision):
    query = decision.get('retrieval_query') or {}
    sources = query.get('sources') or []
    if query.get('subject_ref') == 'topic:WEALTH' and not query.get('excluded_subject_refs') and 2 <= len(sources) <= 3:
        keys = []
        for source in sources:
            if source.get('source') != 'CURRENT_MESSAGE':return []
            literal = source.get('source_text') or message[source['start']:source['end']]
            key = named_key(literal)
            if key is None:return []
            keys.append(key)
        return keys if len(keys) == len(set(keys)) else []
    key = named_key(message)
    return [key] if key else []


def select_named_public(session, principal, thread, turn, result, offered, *, _selected_key=None):
    from .semantic_proposal import _valid_turn, _validated_result
    thread, scope, epoch = _valid_turn(session, principal, thread, turn)
    receipt = _validated_result(result, offered, turn.message)
    if (turn.diagnostics_json.get("semantic_receipt") != receipt
            or offered.get("actor_scope_hash") != epoch or offered.get("thread_id") != thread.id
            or offered.get("turn_id") != turn.id):
        _deny()
    decision = receipt["decision"]
    if (decision["mode"] != "CONSULT" or decision["knowledge_topics"] not in (["WEALTH"], ["INVESTMENT"])
            or decision["action_kinds"] or decision["entity_refs"] or decision["focus_ref"] is not None
            or decision["changes"] or turn.reviewed_evidence_refs_json
            or scope["role"] not in {"CUSTOMER", "DEV_TEST", "DEMO_OWNER", "FAMILY_HELPER"}):
        return None
    selected_keys = selected_product_keys(turn.message, decision)
    key = (_selected_key if _selected_key in selected_keys else None) if _selected_key is not None else selected_keys[0] if len(selected_keys) == 1 else None
    if key is None:
        return None
    content = _public_content(key)
    binding = {"version": VERSION, "conversation_id": thread.id, "turn_id": turn.id,
        "actor_scope_hash": epoch, "source_message_hash": canonical_hash({"message": turn.message}),
        "semantic_receipt_hash": canonical_hash(receipt), "input_hash": receipt["input_hash"],
        "offered_context": deepcopy(offered), "offered_hash": canonical_hash(offered),
        "product_key": key, "catalog_version": registry.CATALOG_VERSION,
        "bank_write_authorized": False}
    return {"source_id": "public-product:" + key, "kind": SOURCE_KIND,
        "title": content["name_zh"], "title_en": content["name_en"],
        "reference": "public-product-catalog:" + key, "version": registry.CATALOG_VERSION,
        "authority": AUTHORITY, "content": json.dumps(content, ensure_ascii=False, sort_keys=True),
        "content_hash": canonical_hash(content), "source_binding": binding}


def source_current(session, principal, record):
    """Revalidate caller ownership/epoch, sealed decision, registry and exact body.

    No fresh bank product, holdings, card, wallet or quoted price is consulted.
    Invalid/deleted sources fail closed; no alternate product is selected.
    """
    from .conversation_models import ChatThread, ChatTurn
    from .semantic_proposal import _valid_turn
    from .semantic_router import prepare_semantic_input, semantic_input_hash, validate_semantic_decision
    try:
        b = record["source_binding"]
        if set(b) != _BINDING_FIELDS or b["version"] != VERSION or b["bank_write_authorized"] is not False:
            return False
        thread = session.get(ChatThread, b["conversation_id"])
        turn = session.get(ChatTurn, b["turn_id"])
        thread, _, epoch = _valid_turn(session, principal, thread, turn)
        receipt = turn.diagnostics_json.get("semantic_receipt")
        offered = b["offered_context"]
        params = {**offered["model"], "message": turn.message}
        if (canonical_hash(receipt) != b["semantic_receipt_hash"]
                or canonical_hash(offered) != b["offered_hash"]
                or canonical_hash({"message": turn.message}) != b["source_message_hash"]
                or epoch != b["actor_scope_hash"] or semantic_input_hash(**params) != b["input_hash"]
                or offered.get("actor_scope_hash") != epoch or offered.get("thread_id") != thread.id
                or offered.get("turn_id") != turn.id
                or not isinstance(receipt, dict) or receipt.get("status") != "READY"
                or receipt.get("input_hash") != b["input_hash"]
                or receipt.get("source_message_hash") != b["source_message_hash"]):
            return False
        prepared = prepare_semantic_input(**params)
        validate_semantic_decision(receipt["decision"], prepared)
        d = receipt["decision"]
        if (d["mode"] != "CONSULT" or d["knowledge_topics"] not in (["WEALTH"], ["INVESTMENT"])
                or d["entity_refs"] or d["focus_ref"] is not None or d["action_kinds"] or d["changes"]
                or turn.reviewed_evidence_refs_json or b["product_key"] not in selected_product_keys(turn.message, d)):
            return False
        content = _public_content(b["product_key"])
        expected = {"source_id": "public-product:" + b["product_key"], "kind": SOURCE_KIND,
            "title": content["name_zh"], "title_en": content["name_en"],
            "reference": "public-product-catalog:" + b["product_key"], "version": registry.CATALOG_VERSION,
            "authority": AUTHORITY, "content": json.dumps(content, ensure_ascii=False, sort_keys=True),
            "content_hash": canonical_hash(content), "source_binding": b}
        return b["catalog_version"] == registry.CATALOG_VERSION and record == expected
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return False


def exact_content(record):
    """Pure display check. Authentication/publication recheck remains mandatory."""
    try:
        content = json.loads(record["content"])
        key = content["product_key"]
        if (record["kind"] != SOURCE_KIND or record["authority"] != AUTHORITY
                or record["source_id"] != "public-product:" + key
                or record["reference"] != "public-product-catalog:" + key
                or record["version"] != registry.CATALOG_VERSION
                or record["content_hash"] != canonical_hash(content)
                or content != _public_content(key)):
            return None
        return content
    except (DomainError, ValueError, TypeError, KeyError):
        return None


def fact_catalog(records):
    result = {}
    if len(records) != 1:
        return result
    row = records[0]
    content = exact_content(row)
    if content is None:
        return result
    identifier = row["source_id"] + ".illustrative_risk_level"
    result[identifier] = {"fact_id": identifier, "source_id": row["source_id"],
        "field": "illustrative_risk_level", "currency": None, "unit": "PUBLIC_RISK_CATEGORY",
        "label": content["name_zh"] + "公版说明性风险分类",
        "label_en": content["name_en"] + " illustrative public risk category",
        "value": content["illustrative_risk_level"], "public_product": deepcopy(content)}
    return result


def concise_render(records, language="zh-CN"):
    if 2 <= len(records) <= 3:
        products = [exact_content(row) for row in records]
        if all(products) and len({row['product_key'] for row in products}) == len(products):
            en = language == 'en'
            return '\n\n'.join((f"{row['name_en']} · R{row['illustrative_risk_level']}. {row['risk_note_en']} Fees: {row['fees_en']} Exit: {row['exit_conditions_en']}"
                if en else f"{row['name_zh']} · R{row['illustrative_risk_level']}。{row['risk_note']}费用：{row['fees']}；赎回条件：{row['exit_conditions']}") for row in products)
    if len(records) != 1 or (content := exact_content(records[0])) is None:
        return ("I cannot verify that product's current public description. Which registered product do you mean?"
                if language == "en" else "这项产品的公开说明暂时不能核对。你想了解哪一款已登记产品？")
    risk = content["illustrative_risk_level"]
    if language == "en":
        return (f"“{content['name_en']}” has illustrative public risk category R{risk}. "
            + content["risk_note_en"]
            + " Capital and returns are not guaranteed; this is not your personal suitability decision.")
    return (f"“{content['name_zh']}”在已登记说明中为 R{risk}。" + content["risk_note"]
        + "本金和收益均不保证；这不是本人的风险适配结论。")


def render_risk_fact(fact, language="zh-CN"):
    """A risk category is not money/minor units, never a model-generated number."""
    content = fact.get("public_product")
    try:
        expected = _public_content(content["product_key"])
        sid = "public-product:" + content["product_key"]
        if (content != expected or fact.get("unit") != "PUBLIC_RISK_CATEGORY" or fact.get("currency") is not None
                or fact.get("source_id") != sid or fact.get("fact_id") != sid + ".illustrative_risk_level"
                or fact.get("field") != "illustrative_risk_level" or type(fact.get("value")) is not int
                or fact["value"] != content["illustrative_risk_level"]):
            raise ValueError("INVALID_PUBLIC_RISK_FACT")
        row = {"kind": SOURCE_KIND, "authority": AUTHORITY, "source_id": sid,
            "reference": "public-product-catalog:" + content["product_key"], "version": registry.CATALOG_VERSION,
            "content": json.dumps(content, ensure_ascii=False, sort_keys=True), "content_hash": canonical_hash(content)}
        return concise_render([row], language)
    except (DomainError, KeyError, TypeError):
        raise ValueError("INVALID_PUBLIC_RISK_FACT") from None


def public_source_fallback(records, language="zh-CN"):
    """Only the single selected registered record, never all retrieved chunks."""
    rows = [row for row in records if row.get("kind") == SOURCE_KIND
            or str(row.get("source_id", "")).startswith("public-product:")]
    if not rows:
        return None
    return concise_render(rows if len(records) == len(rows) else [], language)


def registered_wealth_policy():
    from .product_knowledge import ENTRIES, ENGLISH_ENTRIES, KNOWLEDGE_VERSION, KNOWLEDGE_ENTRY_VERSIONS
    row = next((entry for entry in ENTRIES if entry[0] == "wealth.risk-v1"), None)
    english = ENGLISH_ENTRIES.get("wealth.risk-v1")
    if row is None or english is None:
        return None
    return {"source_id": "kb:wealth.risk-v1", "title": row[1], "title_en": english[0],
        "content": row[3], "content_en": english[2], "kind": "PRODUCT_KNOWLEDGE",
        "reference": "product_knowledge:wealth.risk-v1",
        "version": KNOWLEDGE_ENTRY_VERSIONS.get("wealth.risk-v1", KNOWLEDGE_VERSION)}


def select_public_wealth_scope(session, principal, thread, turn, result, offered, records, locale="zh-CN"):
    """A bounded public-only CONSULT; business requests keep their bank workflow."""
    decision = result.decision.model_dump() if result.decision else {}
    if (decision.get("mode") != "CONSULT" or decision.get("knowledge_topics") not in (["WEALTH"], ["INVESTMENT"])
            or decision.get("action_kinds") or decision.get("entity_refs") or decision.get("focus_ref") is not None
            or decision.get("changes") or turn.reviewed_evidence_refs_json):
        return None
    from .product_knowledge import ENTRIES, ENGLISH_ENTRIES, KNOWLEDGE_VERSION, KNOWLEDGE_ENTRY_VERSIONS
    from .knowledge_runtime import directory_source
    from .permissions import require_banking_actor
    role = require_banking_actor(session, principal)
    row = next((entry for entry in ENTRIES if entry[0] == "scope.boundary-v1"), None)
    en = ENGLISH_ENTRIES.get("scope.boundary-v1")
    policy = registered_wealth_policy()
    if row is None or en is None or policy is None:
        return None
    boundary = {"source_id": "kb:scope.boundary-v1", "title": row[1], "title_en": en[0],
        "content": row[3], "content_en": en[2], "kind": "PRODUCT_KNOWLEDGE",
        "reference": "product_knowledge:scope.boundary-v1",
        "version": KNOWLEDGE_ENTRY_VERSIONS.get("scope.boundary-v1", KNOWLEDGE_VERSION)}
    from .risk_assessment_explanation import SUBJECT as RISK_SUBJECT, registered_questionnaire_source
    guide = registered_questionnaire_source()
    legacy = [directory_source(role, locale), boundary, policy]
    with_guide = records == [*legacy[:-1], guide, policy]
    # The source registry also publishes independently versioned relationship
    # policies. Validate the complete current registry output instead of
    # rejecting a legitimate added policy by the old fixed list length.
    from .semantic_knowledge import public_semantic_sources
    current = public_semantic_sources(session, decision['knowledge_topics'], role, locale)
    if records == current:
        with_guide = [row for row in records if row.get('source_id') == guide['source_id']] == [guide]
    elif records != legacy and not with_guide:
        _deny()
    if (decision.get('retrieval_query') or {}).get('subject_ref') == RISK_SUBJECT:
        if not with_guide:
            _deny()
        return {'scope': 'REGISTERED_RISK_QUESTIONNAIRE_GUIDE', 'records': [guide]}
    selected_keys = selected_product_keys(turn.message, decision)
    if len(selected_keys) > 1:
        return {'scope': 'NAMED_REGISTERED_PUBLIC_PRODUCTS', 'records': [
            select_named_public(session, principal, thread, turn, result, offered, _selected_key=key) for key in selected_keys]}
    selected = select_named_public(session, principal, thread, turn, result, offered)
    # The guide remains available in the complete public directory. Availability
    # alone does not select it for a missing-product risk question.
    include_guide = with_guide and (decision.get('retrieval_query') or {}).get('subject_ref') == 'topic:WEALTH'
    return {"scope": "NAMED_REGISTERED_PUBLIC_PRODUCT" if selected else
            'GENERAL_PUBLIC_WEALTH_RULES_WITH_GUIDE' if include_guide else "GENERAL_PUBLIC_WEALTH_RULES",
            "records": [selected] if selected else [policy, guide] if include_guide else [policy]}


def current_public_wealth_scope(session, principal, thread, turn, selector):
    """Publication recheck after model I/O; missing or changed scope cannot refill."""
    from .semantic_proposal import _valid_turn
    _valid_turn(session, principal, thread, turn)
    receipt = turn.diagnostics_json.get("semantic_receipt") or {}
    decision = receipt.get("decision") or {}
    if (receipt.get("status") != "READY" or decision.get("mode") != "CONSULT"
            or decision.get("knowledge_topics") not in (["WEALTH"], ["INVESTMENT"])
            or decision.get("action_kinds") or decision.get("entity_refs") or decision.get("focus_ref") is not None
            or decision.get("changes") or turn.reviewed_evidence_refs_json):
        _deny()
    if selector.get("public_wealth_scope") == "NAMED_REGISTERED_PUBLIC_PRODUCT":
        rows = selector.get("public_wealth_records")
        if not isinstance(rows, list) or len(rows) != 1 or not source_current(session, principal, rows[0]):
            _deny()
        return deepcopy(rows)
    if selector.get('public_wealth_scope') == 'NAMED_REGISTERED_PUBLIC_PRODUCTS':
        rows = selector.get('public_wealth_records')
        keys = selected_product_keys(turn.message, decision)
        if not isinstance(rows,list) or not 2 <= len(rows) <= 3 or len(rows) != len(keys) \
                or not all(source_current(session,principal,row) for row in rows) \
                or {row['source_binding']['product_key'] for row in rows} != set(keys):
            _deny()
        return deepcopy(rows)
    if selector.get("public_wealth_scope") == "GENERAL_PUBLIC_WEALTH_RULES":
        if named_key(turn.message) is not None or registered_wealth_policy() is None:
            _deny()
        return [registered_wealth_policy()]
    if selector.get('public_wealth_scope') in {'REGISTERED_RISK_QUESTIONNAIRE_GUIDE', 'GENERAL_PUBLIC_WEALTH_RULES_WITH_GUIDE'}:
        from .risk_assessment_explanation import SUBJECT as RISK_SUBJECT, registered_questionnaire_source
        guide = registered_questionnaire_source()
        if selector['public_wealth_scope'] == 'REGISTERED_RISK_QUESTIONNAIRE_GUIDE':
            if (decision.get('retrieval_query') or {}).get('subject_ref') != RISK_SUBJECT:
                _deny()
            expected = [guide]
        else:
            if named_key(turn.message) is not None or registered_wealth_policy() is None \
                    or (decision.get('retrieval_query') or {}).get('subject_ref') != 'topic:WEALTH':
                _deny()
            expected = [registered_wealth_policy(), guide]
        if selector.get('public_wealth_records') != expected:
            _deny()
        return expected
    _deny()
