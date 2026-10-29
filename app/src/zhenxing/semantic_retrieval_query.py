"""Source-bound public retrieval queries, without another model call.

The semantic model selects a public subject and a semantic aspect. It cannot
supply arbitrary query prose: only registered public labels and fixed aspect
descriptors reach retrieval. User/history spans resolve an object, never copy
private amounts, bank results, claims, or permissions into public search.

Callers own authentication and must supply the final authorized/budgeted model
input. Recheck that input's actor epoch and selected sources before publishing
an answer. Missing plans retain the old raw-query behavior; invalid new plans
raise a finite diagnostic and must not be reported as successful rewriting.
"""
from copy import deepcopy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .planning import canonical_hash


VERSION = "public-retrieval-query-v1"
AUTHORITY = "PUBLIC_KNOWLEDGE_QUERY_NOT_BANK_AUTHORIZATION"
MAX_CATALOG_CHARACTERS = 14000
MAX_QUERY_CHARACTERS = 240
MAX_QUERY_BYTES = 720
WIRE_SUBJECT_FIELDS = ("ref", "topic", "label", "label_en", "source_hash")

# These are model-selected semantic facets, not matches against user wording.
ASPECT_TERMS = {
    "PRICE": ("价格 费用 收费 标准", "price fees charges public terms"),
    "ELIGIBILITY": ("适用 条件 资格", "eligibility conditions requirements"),
    "BENEFITS": ("权益 服务 内容", "benefits services scope"),
    "BILLING_PERIOD": ("账期 周期 到期", "billing period cycle expiry"),
    "AUTO_RENEWAL": ("自动续费 恢复 关闭", "automatic renewal restore disable"),
    "CANCELLATION": ("取消 关闭 自动续费 权益", "cancellation automatic renewal remaining benefits"),
    "REFUND": ("退款 退费 条件", "refund conditions policy"),
    "RISK": ("风险 保障 保证", "risk protection guarantees"),
    "PROCESS": ("流程 办理 确认", "process steps confirmation"),
    "LIMITS": ("限制 范围 权限", "limits scope permissions"),
    "GENERAL": ("公开 规则 说明", "public rules information"),
}
_TOPICS = {
    "GENERAL": ("公开服务规则", "public service rules", "scope.boundary-v1", ()),
    "TRANSFER": ("转账", "transfers", "transfer.confirm-v1", ("transfer",)),
    "BILLS": ("账单", "bills", "bills.period-v1", ("账期", "billing")),
    "CARDS": ("银行卡", "bank cards", "cards.identity-v1", ("卡片", "bank card")),
    "WEALTH": ("理财", "wealth products", "wealth.risk-v1", ("理财产品", "investments")),
    "SUBSCRIPTIONS": ("订阅会员", "subscriptions", "subscription.scope-v1", ("会员", "订阅", "subscription")),
    "FAMILY": ("家庭协助", "family assistance", "family.scope-v1", ("家庭授权", "家庭", "family delegation")),
    "CARBON": ("低碳积分", "carbon points", "carbon.synthetic-v1", ("低碳权益", "carbon benefits")),
    "FX": ("外汇兑换", "foreign exchange", "fx.quote-v1", ("外汇", "换汇", "FX", "currency exchange")),
    "FRAUD": ("反诈风险", "fraud risks", "transfer.unknown-v1", ("反诈", "fraud")),
}


class RetrievalSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    source: Literal["CURRENT_MESSAGE", "HISTORY_USER", "MEMORY_USER", "PUBLIC_ENTITY"]
    message_index: int | None = Field(default=None, ge=0, le=11, exclude_if=lambda value: value is None)
    start: int | None = Field(default=None, ge=0, le=1999, exclude_if=lambda value: value is None)
    end: int | None = Field(default=None, ge=1, le=2000, exclude_if=lambda value: value is None)
    public_ref: str | None = Field(default=None, min_length=1, max_length=100, exclude_if=lambda value: value is None)
    memory_ref: str | None = Field(default=None, min_length=1, max_length=100, exclude_if=lambda value: value is None)
    source_text: str | None = Field(default=None, min_length=1, max_length=160,
                                    exclude_if=lambda value: value is None)


class SemanticRetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    subject_ref: str = Field(min_length=1, max_length=100)
    sources: list[RetrievalSourceRef] = Field(min_length=1, max_length=3)
    aspect: Literal["PRICE", "ELIGIBILITY", "BENEFITS", "BILLING_PERIOD", "AUTO_RENEWAL",
                    "CANCELLATION", "REFUND", "RISK", "PROCESS", "LIMITS", "GENERAL"]
    language: Literal["zh-CN", "en"]
    excluded_subject_refs: list[str] = Field(default_factory=list, max_length=3,
                                            exclude_if=lambda value: not value)
    selected_public_refs: list[str] = Field(default_factory=list,max_length=16,
        exclude_if=lambda value:not value,
        description='Optional registered public capability refs asked about in this turn. Empty means a full capability overview. Not an action, bank object, permission or execution request.')


def complete_retrieval_topics(query,prepared,mode,topics):
    """Fill only redundant omitted topic metadata from the offered subject.

    The LLM still selects the subject and provenance; a conflicting explicit
    topic, unoffered subject, or invalid source remains a validation failure.
    """
    if mode!='CONSULT' or query is None:return topics
    ref=query.subject_ref if isinstance(query,SemanticRetrievalQuery) else query.get('subject_ref')
    supplied=prepare_public_retrieval_subjects(prepared.get('retrieval_subjects',[]))
    subject=next((r for r in supplied if r['ref']==ref),None)
    if not subject:return topics
    if not topics:return [subject['topic']]
    registered=next((r for r in public_retrieval_subjects() if r['ref']==ref),None)
    if registered and registered['kind']=='PUBLIC_POLICY' and subject['topic'] not in topics:
        # A public rule may span semantic topics (e.g. family and general
        # authorization). Preserve the topics; include its registered index.
        return [*topics,subject['topic']]
    return topics


def retrieval_source_wire_schema(prepared):
    """Closed, mutually exclusive model-facing source shapes.

    Optional fields from other source kinds are absent, not parallel nullable
    choices. Unavailable history/memory branches are not offered to the model.
    This presentation helper cannot relax the runtime provenance validator.
    """
    common = {"start": {"type": "integer", "minimum": 0, "maximum": 1999},
              "end": {"type": "integer", "minimum": 1, "maximum": 2000},
              "source_text": {"type": "string", "minLength": 1, "maxLength": 160,
                              "description": "Optional exact registered subject name copied from this source; never an amount or a generated phrase."}}

    def branch(kind, extra=None):
        properties = {"source": {"type": "string", "const": kind}, **deepcopy(common), **(extra or {})}
        return {"type": "object", "properties": properties,
                "required": [key for key in properties if key != "source_text"], "additionalProperties": False}

    result = [branch("CURRENT_MESSAGE")]
    indices = [index for index, row in enumerate(prepared.get("history", []))
               if isinstance(row, dict) and row.get("role") == "user"]
    if indices:
        result.append(branch("HISTORY_USER", {"message_index": {"type": "integer", "enum": indices}}))
    from .conversation_memory import prepare_memory_input
    memory = prepare_memory_input(prepared.get("memory", []))
    if memory:
        result.append(branch("MEMORY_USER", {"memory_ref": {"type": "string", "enum": [row["memory_ref"] for row in memory]}}))
    supplied = prepare_public_retrieval_subjects(prepared.get("retrieval_subjects", []))
    registry = {row["ref"]: row for row in public_retrieval_subjects()}
    refs = [row["ref"] for row in supplied if registry[row["ref"]]["kind"] == "PUBLIC_POLICY"]
    if refs:
        result.append({"type": "object", "properties": {"source": {"type": "string", "const": "PUBLIC_ENTITY"},
                       "public_ref": {"type": "string", "enum": refs}},
                       "required": ["source", "public_ref"], "additionalProperties": False})
    return {"oneOf": result}


def _public_name_span(text, subject):
    """One exact registry alias in one complete source; no intent inference."""
    if not isinstance(text, str) or not 0 < len(text) <= 2000:
        return None
    found = []
    for alias in subject["aliases"]:
        width = len(alias)
        for start in range(len(text) - width + 1):
            end = start + width
            literal = text[start:end]
            if literal.casefold() != alias.casefold():
                continue
            if alias.isascii() and ((start and text[start - 1].isalnum())
                                    or (end < len(text) and text[end].isalnum())):
                continue
            # The runtime source_text contract requires this exact literal to
            # occur once. Do not invent an ordinal for an ambiguous duplicate.
            if text.find(literal) != start or text.find(literal, start + 1) >= 0:
                continue
            found.append({"start": start, "end": end, "source_text": literal})
    return min(found, key=lambda row: (-len(row["source_text"]), row["start"], row["source_text"])) if found else None


def subject_source_wire_constraints(prepared):
    """Offer copyable valid provenance objects, conditioned on public subject.

    At most one current, one latest authorized history-user, and one latest
    memory source are offered per product. Policy subjects use only their
    fixed public entity. Missing/ambiguous names do not invent product sources.
    These constraints refine model output only: input and old receipt shapes
    remain unchanged, and runtime source/authority checks are still mandatory.
    """
    supplied = prepare_public_retrieval_subjects(prepared.get("retrieval_subjects", []))
    registry = {row["ref"]: row for row in public_retrieval_subjects()}
    from .conversation_memory import prepare_memory_input
    memory = prepare_memory_input(prepared.get("memory", []))
    history = prepared.get("history", [])
    if not isinstance(history, list) or len(history) > 12:
        _deny("RETRIEVAL_INPUT_INVALID")
    choices = {}
    for supplied_subject in supplied:
        subject = registry[supplied_subject["ref"]]
        if subject["kind"] == "PUBLIC_POLICY":
            choices[subject["ref"]] = [{"source": "PUBLIC_ENTITY", "public_ref": subject["ref"]}]
            continue
        sources = []
        current = _public_name_span(prepared.get("message"), subject)
        if current:
            sources.append({"source": "CURRENT_MESSAGE", **current})
        for index in range(len(history) - 1, -1, -1):
            entry = history[index]
            if not isinstance(entry, dict) or entry.get("role") != "user":
                continue
            span = _public_name_span(entry.get("content"), subject)
            if span:
                sources.append({"source": "HISTORY_USER", "message_index": index, **span})
                break
        for entry in sorted(memory, key=lambda row: (-row["source_sequence"], row["memory_ref"])):
            span = _public_name_span(entry["quote"], subject)
            if span:
                sources.append({"source": "MEMORY_USER", "memory_ref": entry["memory_ref"], **span})
                break
        if sources:
            choices[subject["ref"]] = sources
    if not choices:
        return [{"not": {}}]
    rules = [{"properties": {"subject_ref": {"enum": list(choices)}}}]
    comparison_sources = [source for ref, candidates in choices.items() if ref.startswith('wealth:')
                          for source in candidates if source.get('source') == 'CURRENT_MESSAGE']
    for ref, sources in choices.items():
        if ref == 'topic:WEALTH' and 2 <= len(comparison_sources) <= 3:
            rules.append({'if': {'properties': {'subject_ref': {'const': ref}}, 'required': ['subject_ref']},
                'then': {'properties': {'sources': {'anyOf': [{'const': sources}, {'const': comparison_sources}]}}}})
            continue
        rules.append({"if": {"properties": {"subject_ref": {"const": ref}}, "required": ["subject_ref"]},
                      "then": {"properties": {"sources": {"type": "array", "minItems": 1,
                               "maxItems": 3, "uniqueItems": True, "items": {"enum": sources}}}}})
    return rules


def retrieval_query_source_diagnostics(query, prepared):
    """Finite safe failure metadata; never echo quotes, amounts or free keys."""
    try:
        refs = {row["ref"] for row in prepare_public_retrieval_subjects(prepared.get("retrieval_subjects", []))}
        from .conversation_memory import prepare_memory_input
        memory_refs = {row["memory_ref"] for row in prepare_memory_input(prepared.get("memory", []))}
    except (ValueError, TypeError, KeyError, AttributeError):
        refs, memory_refs = set(), set()
    value = query.model_dump() if isinstance(query, SemanticRetrievalQuery) else query
    value = value if isinstance(value, dict) else {}
    subject = value.get("subject_ref")
    source_rows = value.get("sources")
    source_rows = source_rows if isinstance(source_rows, list) else []
    allowed = {"source", "start", "end", "source_text", "message_index", "memory_ref", "public_ref"}
    kinds = {"CURRENT_MESSAGE", "HISTORY_USER", "MEMORY_USER", "PUBLIC_ENTITY"}
    safe = []
    for source in source_rows[:3]:
        source = source if isinstance(source, dict) else {}
        kind, memory_ref = source.get("source"), source.get("memory_ref")
        safe.append({"source": kind if isinstance(kind, str) and kind in kinds else "UNKNOWN",
                     "memory_ref": memory_ref if isinstance(memory_ref, str) and memory_ref in memory_refs else None,
                     "fields": sorted(key for key in source if key in allowed),
                     "unexpected_field_count": sum(key not in allowed for key in source)})
    return {"subject_ref": subject if isinstance(subject, str) and subject in refs else None,
            "source_count": len(source_rows), "sources": safe}


def _deny(code):
    raise ValueError(code)


def _subject(ref, topic, label, english, aliases, kind, source_id, version, source):
    return {"ref": ref, "topic": topic, "label": label, "label_en": english,
            "aliases": sorted(set((label, english, *aliases))), "kind": kind,
            "source_id": source_id, "source_version": version, "source_hash": canonical_hash(source)}


def public_retrieval_subjects():
    """Only public registry labels; no account, live price or holdings read.

    Names identify public literature, not live availability or a quoted price.
    Live prices/availability still require their existing source validators.
    """
    from .product_knowledge import ENTRIES, ENGLISH_ENTRIES, KNOWLEDGE_VERSION, KNOWLEDGE_ENTRY_VERSIONS, DOMAIN_POLICY_IDS
    from .financial_product_catalog import product_catalog, CATALOG_VERSION
    from .subscription_commerce import CATALOG_DEFAULTS, MERCHANT_ALIASES, TERMS_VERSION
    entries = {row[0]: row for row in ENTRIES}
    rows = []
    for topic, (label, english, key, aliases) in _TOPICS.items():
        if key not in entries or key not in ENGLISH_ENTRIES:
            continue
        rows.append(_subject("topic:" + topic, topic, label, english, aliases, "PUBLIC_POLICY",
                             "kb:" + key, KNOWLEDGE_ENTRY_VERSIONS.get(key, KNOWLEDGE_VERSION),
                             [entries[key], ENGLISH_ENTRIES[key]]))
    # New rules stay under the existing finite semantic topics. These public
    # subjects contain no customer/card/account identity or current price.
    for ref, topic, label, english, aliases in (
            ("policy:credit", "CARDS", "信用卡", "credit cards", ("信用分期", "授信", "credit card")),
            ("policy:birthday-plan", "FAMILY", "生日计划", "birthday plans", ("生日安排", "birthday plan")),
            ("policy:merchant", "GENERAL", "商户商品", "merchant products", ("虚拟商户", "商品订单", "merchant orders"))):
        identifiers = DOMAIN_POLICY_IDS[topic]
        rows.append(_subject(ref, topic, label, english, aliases, "PUBLIC_POLICY", "kb:" + identifiers[0],
            KNOWLEDGE_ENTRY_VERSIONS[identifiers[0]], [[entries[key], ENGLISH_ENTRIES[key]] for key in identifiers]))
    from .capability_consultation_facts import SUBJECT, VERSION as CAPABILITY_VERSION, registration_source
    rows.append(_subject(SUBJECT, 'CARDS', '银行卡与信用卡可办理服务',
        'available bank-card and credit-card services', (), 'PUBLIC_POLICY',
        'directory:card-services', CAPABILITY_VERSION, registration_source()))
    rows.append(_subject('policy:family-permission','FAMILY','家庭协助权限','family assistance permissions',
        ('家庭委托权限','family delegation permissions'),'PUBLIC_POLICY','kb:family.scope-v1',
        KNOWLEDGE_ENTRY_VERSIONS.get('family.scope-v1',KNOWLEDGE_VERSION),
        [entries['family.scope-v1'],ENGLISH_ENTRIES['family.scope-v1']]))
    from .risk_assessment_explanation import SUBJECT as RISK_SUBJECT, registered_questionnaire_source
    guide = registered_questionnaire_source()
    rows.append(_subject(RISK_SUBJECT, 'WEALTH', guide['title'], guide['title_en'],
        (), 'PUBLIC_POLICY', guide['source_id'], guide['version'], guide))
    from .subscription_catalog_data import PUBLIC_PRODUCT_NAMES
    from .merchant_catalog_registry import DEFAULT_CATALOG
    for code,merchant,category,sku,name,_price in DEFAULT_CATALOG:
        rows.append(_subject('merchant:'+code.lower(),'GENERAL',name,name,(merchant+'的'+name,sku),
            'PUBLIC_PRODUCT','merchant-catalog:registered-products','merchant-product-ontology-v1',
            {'merchant_code':code,'sku':sku,'product_name':name}))
    known = PUBLIC_PRODUCT_NAMES
    for merchant, _price, _provider in CATALOG_DEFAULTS:
        if merchant not in known:
            continue
        key, english = known[merchant]
        aliases = tuple(alias for alias, value in MERCHANT_ALIASES.items() if value == merchant)
        rows.append(_subject("subscription:" + key, "SUBSCRIPTIONS", merchant, english, aliases,
                             "PUBLIC_PRODUCT", "subscription-catalog:registered-products", TERMS_VERSION,
                             {"merchant": merchant, "aliases": aliases, "version": TERMS_VERSION}))
    for product in product_catalog():
        rows.append(_subject("wealth:" + product["product_key"], "WEALTH", product["name_zh"],
                             product["name_en"], (product["product_key"], product["name_zh"] + "（仿真）"),
                             "PUBLIC_PRODUCT", product["source"], CATALOG_VERSION, product))
    return rows


def public_retrieval_wire_subjects():
    """Compact optional prompt catalogue, still bound to the full public source.

    The model needs subject labels and refs, not duplicated alias tables and
    publication metadata. The server restores those only from its registry.
    Existing receipts containing full rows remain valid and are not normalized.
    """
    return [{key: row[key] for key in WIRE_SUBJECT_FIELDS} for row in public_retrieval_subjects()]


def prepare_public_retrieval_subjects(value):
    """Canonical equality prevents callers/model output injecting private data.

    A bounded subset is permitted so callers may remove unavailable/unauthorized
    public entries. A rejected row is never repaired, guessed or added back.
    """
    if not isinstance(value, list) or len(value) > 32:
        _deny("RETRIEVAL_CATALOG_INVALID")
    actual = {row["ref"]: row for row in public_retrieval_subjects()}
    seen = set()
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("ref"), str) or row["ref"] in seen:
            _deny("RETRIEVAL_CATALOG_CHANGED")
        registered = actual.get(row["ref"])
        compact = {key: registered[key] for key in WIRE_SUBJECT_FIELDS} if registered else None
        if row not in (registered, compact):
            _deny("RETRIEVAL_CATALOG_CHANGED")
        seen.add(row["ref"])
    if len(json.dumps(value, ensure_ascii=False)) > MAX_CATALOG_CHARACTERS:
        _deny("RETRIEVAL_CATALOG_OVER_BUDGET")
    return deepcopy(value)


def _literal_matches(subject, value):
    return isinstance(value, str) and value.casefold() in {label.casefold() for label in subject["aliases"]}


def _mentions(value, label):
    """Exact registered-name grounding only; never infer mode or user intent."""
    folded, needle = value.casefold(), label.casefold()
    start = folded.find(needle)
    while start >= 0:
        end = start + len(needle)
        if not label.isascii() or ((start == 0 or not folded[start - 1].isalnum())
                                  and (end == len(folded) or not folded[end].isalnum())):
            return True
        start = folded.find(needle, start + 1)
    return False


def _validated(query, prepared, *, mode, knowledge_topics):
    try:
        plan = SemanticRetrievalQuery.model_validate(query.model_dump() if isinstance(query, SemanticRetrievalQuery) else query)
    except (ValidationError, TypeError, ValueError):
        _deny("RETRIEVAL_PLAN_INVALID")
    if mode != "CONSULT":
        _deny("RETRIEVAL_MODE_NOT_CONSULT")
    if "retrieval_subjects" not in prepared:
        _deny("RETRIEVAL_CATALOG_NOT_OFFERED")
    supplied = prepare_public_retrieval_subjects(prepared["retrieval_subjects"])
    registered = {row["ref"]: row for row in public_retrieval_subjects()}
    rows = [registered[row["ref"]] for row in supplied]
    subject = next((row for row in rows if row["ref"] == plan.subject_ref), None)
    if subject is None:
        _deny("RETRIEVAL_SUBJECT_NOT_OFFERED")
    if knowledge_topics is not None and subject["topic"] not in knowledge_topics:
        _deny("RETRIEVAL_TOPIC_MISMATCH")
    if plan.language != prepared.get("reply_language", prepared.get("locale", "zh-CN")):
        _deny("RETRIEVAL_LANGUAGE_MISMATCH")
    if plan.selected_public_refs:
        from .capability_consultation_facts import SUBJECT, offered_capability_options
        if subject['ref']!=SUBJECT:
            _deny('RETRIEVAL_PUBLIC_SELECTION_SUBJECT_INVALID')
        refs=plan.selected_public_refs
        allowed={row['ref'] for row in offered_capability_options(prepared)}
        if len(refs)!=len(set(refs)) or not set(refs)<=allowed:
            _deny('RETRIEVAL_PUBLIC_SELECTION_NOT_OFFERED')
    current = prepared.get("message")
    history = prepared.get("history", [])
    if not isinstance(current, str) or not 1 <= len(current) <= 2000 \
            or not isinstance(history, list) or len(history) > 12:
        _deny("RETRIEVAL_INPUT_INVALID")
    excluded = set(plan.excluded_subject_refs)
    available = {row["ref"]: row for row in rows}
    if len(excluded) != len(plan.excluded_subject_refs) or subject["ref"] in excluded \
            or any(ref not in available or available[ref]["kind"] != "PUBLIC_PRODUCT"
                   or not any(_mentions(current, alias) for alias in available[ref]["aliases"])
                   for ref in excluded):
        _deny("RETRIEVAL_EXCLUDED_SUBJECT_INVALID")
    memory = {}
    if any(ref.source == "MEMORY_USER" for ref in plan.sources):
        from .conversation_memory import prepare_memory_input
        try:
            memory = {row["memory_ref"]: row for row in prepare_memory_input(prepared.get("memory", []))}
        except (ValueError, TypeError, KeyError):
            _deny("RETRIEVAL_MEMORY_INVALID")
    comparison = []
    if subject['ref'] == 'topic:WEALTH' and not excluded and 2 <= len(plan.sources) <= 3:
        for source in plan.sources:
            if source.source != 'CURRENT_MESSAGE':
                comparison = []; break
            literal = source.source_text if source.source_text is not None else current[source.start:source.end]
            matched = [row for row in rows if row['kind'] == 'PUBLIC_PRODUCT' and row['ref'].startswith('wealth:')
                       and _literal_matches(row, literal)]
            if len(matched) != 1:
                comparison = []; break
            comparison.append(matched[0])
        if len({row['ref'] for row in comparison}) != len(plan.sources):
            comparison = []
    def grounded(literal):
        return any(_literal_matches(row, literal) for row in comparison) if comparison else _literal_matches(subject, literal)
    proofs, seen, current_selected, normalized_sources = [], set(), False, []
    for ref in plan.sources:
        supplied = ref.model_dump()
        duplicate = canonical_hash(supplied)
        if duplicate in seen:
            _deny("RETRIEVAL_SOURCE_DUPLICATE")
        seen.add(duplicate)
        if ref.source == "PUBLIC_ENTITY":
            if set(supplied) != {"source", "public_ref"} or ref.public_ref != subject["ref"]:
                _deny("RETRIEVAL_SOURCE_NOT_OFFERED")
            if subject["kind"] != "PUBLIC_POLICY":
                _deny("RETRIEVAL_PRODUCT_REQUIRES_USER_SOURCE")
            proofs.append({"reference": supplied, "source_hash": subject["source_hash"]})
            normalized_sources.append(ref)
            continue
        required = {"source", "start", "end"}
        if ref.source == "HISTORY_USER":
            required.add("message_index")
        elif ref.source == "MEMORY_USER":
            required.add("memory_ref")
        if set(supplied) not in (required, required | {"source_text"}):
            _deny("RETRIEVAL_SOURCE_SPAN_INVALID")
        if ref.source == "CURRENT_MESSAGE":
            text = current
            current_selected = True
        elif ref.source == "MEMORY_USER":
            if ref.memory_ref not in memory:
                _deny("RETRIEVAL_MEMORY_NOT_OFFERED")
            text = memory[ref.memory_ref]["quote"]
        else:
            if ref.message_index >= len(history):
                _deny("RETRIEVAL_SOURCE_NOT_OFFERED")
            row = history[ref.message_index]
            if not isinstance(row, dict) or row.get("role") != "user":
                _deny("RETRIEVAL_HISTORY_NOT_USER")
            text = row.get("content")
        if not isinstance(text, str) or len(text) > 2000:
            _deny("RETRIEVAL_SOURCE_SPAN_INVALID")
        if ref.source_text is not None:
            if not grounded(ref.source_text):
                _deny("RETRIEVAL_SUBJECT_NOT_GROUNDED")
            first = text.find(ref.source_text)
            if first < 0 or text.find(ref.source_text, first + 1) >= 0:
                _deny("RETRIEVAL_SOURCE_LITERAL_NOT_UNIQUE")
            # Correct only coordinates in the already selected complete
            # source; never scan another history round or invent a subject.
            ref = ref.model_copy(update={"start": first, "end": first + len(ref.source_text)})
            supplied = ref.model_dump()
        if not 0 <= ref.start < ref.end <= len(text):
            _deny("RETRIEVAL_SOURCE_SPAN_INVALID")
        if not grounded(text[ref.start:ref.end]):
            _deny("RETRIEVAL_SUBJECT_NOT_GROUNDED")
        # Preserve the complete quote, source sequence, kind and non-authority
        # in the hash. Only the exact registered public name enters the query.
        source = memory[ref.memory_ref] if ref.source == "MEMORY_USER" else {"role": "user", "content": text}
        proofs.append({"reference": supplied, "source_hash": canonical_hash(source)})
        normalized_sources.append(ref)
    if len({canonical_hash(proof["reference"]) for proof in proofs}) != len(proofs):
        _deny("RETRIEVAL_SOURCE_DUPLICATE")
    if not current_selected and subject["kind"] == "PUBLIC_PRODUCT":
        other = [row for row in rows if row["kind"] == "PUBLIC_PRODUCT" and row["ref"] != subject["ref"]
                 and row["ref"] not in excluded
                 and any(_mentions(current, alias) for alias in row["aliases"])]
        if other:
            _deny("RETRIEVAL_CURRENT_SUBJECT_CHANGED")
    plan = plan.model_copy(update={"sources": normalized_sources})
    binding = {"version": VERSION, "subject": subject, "source_proofs": proofs,
               "current_message_hash": canonical_hash(current), "role": prepared.get("role"),
               "plan": plan.model_dump(), "authority": AUTHORITY}
    if comparison:
        binding['comparison_subjects'] = comparison
    return plan, subject, binding


def validate_retrieval_query(query, prepared, *, mode="CONSULT", knowledge_topics=None):
    """Hook for semantic decision validation; no plan leaves old hashes alone."""
    if query is None:
        return None
    return _validated(query, prepared, mode=mode, knowledge_topics=knowledge_topics)[0]


def compile_retrieval_query(query, prepared, *, mode="CONSULT", knowledge_topics=None):
    if query is None:
        return {"query": prepared["message"], "diagnostics": {"version": VERSION,
                "status": "ORIGINAL_QUERY_NO_PLAN", "bank_write_authorized": False}}
    plan, subject, binding = _validated(query, prepared, mode=mode, knowledge_topics=knowledge_topics)
    english = plan.language == "en"
    selected = binding.get('comparison_subjects') or [subject]
    text = ' '.join(row["label_en" if english else "label"] for row in selected) + " " + ASPECT_TERMS[plan.aspect][int(english)]
    if len(text) > MAX_QUERY_CHARACTERS or len(text.encode("utf-8")) > MAX_QUERY_BYTES:
        _deny("RETRIEVAL_QUERY_OVER_BUDGET")
    return {"query": text, "diagnostics": {"version": VERSION, "status": "REWRITTEN_PUBLIC_QUERY",
            "authority": AUTHORITY, "bank_write_authorized": False, "subject_ref": subject["ref"],
            "aspect": plan.aspect, "query_hash": canonical_hash(text),
            "source_binding_hash": canonical_hash(binding), "source_count": len(plan.sources),
            "query_characters": len(text), "query_utf8_bytes": len(text.encode("utf-8"))}}


def revalidate_retrieval_query(query, prepared, fresh_prepared, *, mode="CONSULT", knowledge_topics=None):
    """Caller must separately compare current actor epoch/ownership.

    Fresh input must be rebuilt through the authorized history/source service,
    not copied from the original receipt. Changed/dropped selected rounds fail.
    """
    if query is None:
        return True
    try:
        prior = _validated(query, prepared, mode=mode, knowledge_topics=knowledge_topics)[2]
        current = _validated(query, fresh_prepared, mode=mode, knowledge_topics=knowledge_topics)[2]
        return canonical_hash(prior) == canonical_hash(current)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
