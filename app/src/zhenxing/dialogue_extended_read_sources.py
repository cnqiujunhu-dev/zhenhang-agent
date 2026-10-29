"""Finite domain-owned READ proof adapters; no new permission or execution."""
from .contracts import DomainError


DOMAINS = {"AA_VIEW": "AA", "TRANSFER_SCHEDULE_VIEW": "SCHEDULES", "CREDIT_VIEW": "CREDIT", "CREDIT_STATEMENT": "CREDIT",
    "OCCASION_MEMBER_VIEW": "OCCASIONS",
    "OCCASION_VIEW": "OCCASIONS", "LIFE_PLAN_VIEW": "LIFE_PLANS", "MERCHANT_CATALOG": "MERCHANTS",
    "MERCHANT_ORDERS": "MERCHANT_ORDERS"}
READ_KINDS = frozenset(DOMAINS)


def _deny():
    raise DomainError("DIALOGUE_REFERENCE_SOURCE_CHANGED", "查询结果的对象、来源或权限已变化，请重新查询。", 409)


def extended_read_entities(session, principal, kind, result):
    if kind == "TRANSFER_SCHEDULE_VIEW":
        from .scheduled_transfer_reads import result_current, result_entities
        entities = result_entities(result_current(session, principal, result))
    elif kind == "AA_VIEW":
        from .aa_obligations import aa_read_result_current, aa_result_fact_entities
        current = aa_read_result_current(session, principal, result)
        entities = aa_result_fact_entities(current)
    elif kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
        from .credit_facts import credit_read_result_current, credit_result_fact_entities
        if credit_read_result_current(session, principal, kind, result) is not True:
            _deny()
        entities = credit_result_fact_entities(kind, result)
    elif kind in {"OCCASION_MEMBER_VIEW", "OCCASION_VIEW", "LIFE_PLAN_VIEW", "MERCHANT_CATALOG", "MERCHANT_ORDERS"}:
        from .life_plan_facts import life_read_result_current, life_result_fact_entities
        if life_read_result_current(session, principal, kind, result) is not True:
            _deny()
        entities = life_result_fact_entities(kind, result)
    else:
        _deny()
    if not isinstance(entities, list) or any(not isinstance(row, dict) or row.get("read_kind") != kind
            or row.get("domain") != DOMAINS[kind] or not isinstance(row.get("field"), str)
            or not isinstance(row.get("value"), str) or not isinstance(row.get("facts"), dict)
            or not isinstance(row.get("live_record_hash"), str) for row in entities):
        _deny()
    return entities


def extended_identity_fields(entity):
    """Exposed selector names only; actual WRITE always uses a separate gate."""
    from .bill_query_context import identity_fields
    if identity_fields(entity):
        return identity_fields(entity)
    kind, field = entity.get("read_kind"), entity.get("field")
    if kind == 'TRANSFER_SCHEDULE_VIEW' and field == 'schedule_reference':
        return ['schedule_reference']
    if kind == "AA_VIEW" and field == "aa_obligation_ref":
        return ["aa_obligation_ref"]
    if kind=='AA_VIEW' and field=='aa_collection_ref':return ['aa_collection_ref']
    if kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
        from .credit_statement_identity import FIELD, KIND
        if kind == 'CREDIT_STATEMENT' and field == FIELD and entity.get('kind') == KIND:
            return ['card_reference']
        return {"credit_card_status": ["card_number", "card_last4", "card_reference"],
            "credit_payment": ["payment_id"], "credit_installment": ["installment_id"],
            "credit_application": ["application_id"]}.get(field, [])
    return {("OCCASION_MEMBER_VIEW", "family_member_id"): ["family_member_id"],
        ("OCCASION_VIEW", "occasion_id"): ["occasion_id", "family_member_id"],
        ("LIFE_PLAN_VIEW", "life_plan_id"): ["life_plan_id","life_plan_name"],
        ("MERCHANT_CATALOG", "merchant_product_id"): ["merchant_product_id"]}.get((kind, field), [])


def has_identity_route(entity, field, action_kind):
    from .bill_query_context import identity_fields, has_identity_route as bill_identity_route
    if isinstance(entity, dict) and identity_fields(entity):
        return bill_identity_route(entity, field, action_kind)
    kind = entity.get("read_kind") if isinstance(entity, dict) else None
    if kind=='TRANSFER_SCHEDULE_VIEW':
        return action_kind=='TRANSFER_SCHEDULE' and field==entity.get('field')=='schedule_reference'
    if kind=='AA_VIEW':
        if action_kind=='AA_VIEW':return field==entity.get('field') and field in {'aa_collection_ref','aa_obligation_ref'}
        return action_kind=='AA_PAY' and field==entity.get('field')=='aa_obligation_ref' and (entity.get('facts')or{}).get('paymentable') is True
    from .credit_statement_identity import FIELD, CONSUMERS
    if kind == 'CREDIT_STATEMENT' and entity.get('field') == FIELD:
        return action_kind in CONSUMERS and field == 'card_reference' and field in extended_identity_fields(entity)
    if kind in READ_KINDS:
        return field in extended_identity_fields(entity)
    if kind == "CARD_STATUS" and action_kind == "BILL_ANALYSIS":
        return field in {"card_number", "card_last4"}
    if kind == 'CARD_STATUS' and action_kind == 'CARD_STATUS' and field == 'card_last4':
        return entity.get('domain') == 'CARDS' and entity.get('field') == 'card_number'
    if kind=="CARD_STATUS" and field=="destination_card_reference":
        return action_kind=="OWN_ACCOUNT_TRANSFER"
    return (kind == "CARD_STATUS" and field in {"source_card", "card_number", "card_last4", "card_reference"}
            and action_kind in {"TRANSFER", "TRANSFER_SCHEDULE", "AA_PAY", "CREDIT_REPAY", "CREDIT_SETTLE_INSTALLMENT",
                                "LIFE_PLAN_AUTHORIZE", "MERCHANT_PURCHASE","OWN_ACCOUNT_TRANSFER"})


def _original_entity(session, principal, entry):
    from .models import Task
    catalog = entry.get("catalog") or {}
    entity = entry.get("actual_entity") or entry.get("entity")
    if (not isinstance(entity, dict) or entity not in catalog.get("entities", [])
            or entry.get("value", entity.get("value")) != entity.get("value")):
        _deny()
    sources = [row for row in catalog.get("sources", []) if row.get("result_ref") == entity.get("result_ref")]
    if len(sources) != 1:
        _deny()
    child = session.get(Task, sources[0].get("child_task_id"))
    if child is None or (child.user_id, child.sandbox_id) != (principal.user_id, principal.sandbox_id):
        _deny()
    kind = entity.get("read_kind")
    if kind == "AA_VIEW":
        from .aa_obligations import aa_result_fact_entities
        originals = aa_result_fact_entities(child.result_json)
    elif kind == 'TRANSFER_SCHEDULE_VIEW':
        from .scheduled_transfer_reads import result_entities
        originals = result_entities(child.result_json)
    elif kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
        from .credit_facts import credit_result_fact_entities
        originals = credit_result_fact_entities(kind, child.result_json)
    elif kind in {"OCCASION_MEMBER_VIEW", "OCCASION_VIEW", "LIFE_PLAN_VIEW", "MERCHANT_CATALOG", "MERCHANT_ORDERS"}:
        from .life_plan_facts import life_result_fact_entities
        originals = life_result_fact_entities(kind, child.result_json)
    elif kind == "CARD_STATUS":
        originals = []
        for row in child.result_json.get("cards", []):
            if row.get("card_id") == entity.get("record_id") and (row.get("card_number") == entity.get("value")
                    or entity.get("field") == "card_last4" and row.get("last4") == entity.get("value")):
                return entity, child
        _deny()
    else:
        _deny()
    if not any(all(entity.get(key) == value for key, value in row.items()) for row in originals):
        _deny()
    return entity, child


def immutable_identity_value(session, principal, entry, field, action_kind, *, payment_parent_task_id=None, audit_only=False):
    """Caller first audits the exact immutable catalog lineage, never fresh facts."""
    from .bill_query_context import identity_fields, immutable_identity_value as bill_identity_value
    supplied = entry.get('actual_entity') or entry.get('entity') or {}
    if identity_fields(supplied):
        # Unlike completed payment receipts, this is a fresh READ consumer.
        # Revalidate the complete original query source, not only its identity.
        from .dialogue_references import validate_result_reference_catalog
        from .conversation_models import ChatThread
        catalog = entry.get('catalog') or {}
        thread = session.get(ChatThread, catalog.get('conversation_id'))
        validate_result_reference_catalog(session, principal, thread, catalog)
        if supplied not in catalog.get('entities', []):
            _deny()
        return bill_identity_value(session, principal, entry, field, action_kind)
    entity, child = _original_entity(session, principal, entry)
    kind = entity["read_kind"]
    value = None
    if kind == 'TRANSFER_SCHEDULE_VIEW':
        from .banking_extension_models import ScopedScheduledTransfer
        row=session.get(ScopedScheduledTransfer,entity['value'])
        if action_kind!='TRANSFER_SCHEDULE' or field!='schedule_reference' or not row or (row.user_id,row.sandbox_id)!=(principal.user_id,principal.sandbox_id):_deny()
        # Only identity is inherited. Preparation binds the latest version;
        # new confirmation rechecks it under row locks before replacement.
        return row.id
    if kind == "AA_VIEW" and field == "aa_obligation_ref" and action_kind == "AA_PAY":
        from .aa_obligations import aa_obligation_identity_current
        value = aa_obligation_identity_current(session, principal, child.result_json, entity["value"], source_task_id=child.id,
            payment_parent_task_id=payment_parent_task_id, audit_only=audit_only)["aa_obligation_ref"]
    elif kind=='AA_VIEW' and action_kind=='AA_VIEW' and field==entity.get('field') and field in {'aa_obligation_ref','aa_collection_ref'}:
        # Only the historical record identity is retained. The new AA_VIEW
        # obtains current paid/pending totals from the owned ledger.
        if field=='aa_obligation_ref':
            from .aa_obligations import owned_obligation
            owned_obligation(session,principal,entity['value'])
        else:
            from .banking_extension_models import AACollectionRequest
            identifier=entity['value']
            row=session.get(AACollectionRequest,identifier[len('aa-collection:'):]) if identifier.startswith('aa-collection:') else None
            if not row or (row.user_id,row.sandbox_id)!=(principal.user_id,principal.sandbox_id):_deny()
        value=entity['value']
    elif kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
        from .credit_facts import credit_entity_identity_current
        value = credit_entity_identity_current(session, principal, action_kind, entity, field=field,
            payment_parent_task_id=payment_parent_task_id, audit_only=audit_only)
    elif kind in {"OCCASION_MEMBER_VIEW", "OCCASION_VIEW", "LIFE_PLAN_VIEW", "MERCHANT_CATALOG", "MERCHANT_ORDERS"}:
        from .life_plan_facts import life_entity_identity_current
        value = life_entity_identity_current(session, principal, action_kind, entity, field=field,
            payment_parent_task_id=payment_parent_task_id, audit_only=audit_only)
    elif kind == "CARD_STATUS":
        if field=="destination_card_reference" and action_kind!="OWN_ACCOUNT_TRANSFER":_deny()
        if action_kind in {"CREDIT_REPAY", "CREDIT_SETTLE_INSTALLMENT"} and field != "source_card":
            _deny()  # A debit card is the repayment source, not the credit target.
        from .models import Card
        from .card_identifiers import card_identifier_view
        from .planning import canonical_hash
        card = session.get(Card, entity.get("record_id"))
        read_selector = action_kind in {"BILL_ANALYSIS", "CARD_STATUS"} and field in {"card_number", "card_last4"}
        if card and (read_selector or card.status == "ACTIVE") and (card.user_id, card.sandbox_id) == (principal.user_id, principal.sandbox_id):
            old_scope = (entity.get("facts") or {}).get("balance_scope_refs", {}).get("CNY")
            current_scope = "CNY:" + canonical_hash({"sandbox_id": card.sandbox_id, "user_id": card.user_id, "account_id": card.account_id})
            if old_scope is None or old_scope == current_scope:
                value = (card_identifier_view(session, card) or {}).get("card_number")
    if not isinstance(value, str) or not value:
        _deny()
    if field == "card_last4":
        # Preserve the typed 4HEX slot. A tail is usable only if the union of
        # current owned debit/credit instruments resolves it uniquely back to
        # this exact full identity; never blindly truncate an ambiguous card.
        from .payment_instruments import resolve_source_instrument
        resolved = resolve_source_instrument(session, principal, value[-4:], "CNY", purpose="READ")
        if resolved.get("card_number") != value:
            _deny()
        return value[-4:]
    return value
