"""Owned live-source recheck and finite credit facts for dialogue consumers."""
from copy import deepcopy
from datetime import datetime

from .contracts import DomainError
from .credit_read_common import READ_ACTIONS, credit_read_fingerprint
from .planning import canonical_hash


def credit_read_result_current(session, principal, action, result):
    try:
        if (action not in READ_ACTIONS or not isinstance(result, dict) or result.get("action") != action
                or result.get("bank_state_changed") is not False or not datetime.fromisoformat(result["observed_at"]).tzinfo
                or result.get("read_fingerprint") != credit_read_fingerprint(result)):
            return False
        query = result.get("query")
        if not isinstance(query, dict): return False
        if action == "CREDIT_VIEW":
            from .credit_service import credit_view
            if set(query) != {"card_reference"}: return False
            fresh = credit_view(session, principal, query["card_reference"])
        else:
            from .credit_billing import statement_view
            from .credit_statement_identity import projection_version
            from .credit_consumption import projection_version as consumption_version
            if set(query) != {"card_reference", "currency"}: return False
            fresh = statement_view(session, principal, query["card_reference"], currency=query["currency"],
                identity_projection_version=projection_version(result),
                consumption_projection_version=consumption_version(result))
        return fresh["read_fingerprint"] == result["read_fingerprint"]
    except (DomainError, KeyError, ValueError, TypeError, AttributeError):
        return False


def credit_result_fact_entities(action, result):
    """Use only after authenticated credit_read_result_current and read proof.

    Values are economic facts, never authority to spend or targets for a write.
    Identical credit limits/balances do not prove shared facilities: only the
    exact server-owned facility ID establishes that relation.
    """
    if action not in READ_ACTIONS or not isinstance(result, dict) or result.get("action") != action \
            or result.get("read_fingerprint") != credit_read_fingerprint(result): return []
    if action == 'CREDIT_STATEMENT':
        from .credit_consumption import projection_version
        try: projection_version(result)
        except (ValueError,TypeError): return []
    entities = []
    def add(field, value, label, facts, version=None, ordinal=None):
        body = {**deepcopy(facts), "facts_authority": "OWNED_CREDIT_BOOK", "credit_is_cash": False}
        entities.append({"read_kind": action, "domain": "CREDIT", "field": field, "value": value, "label": label,
            "ordinal": ordinal, "record_id": canonical_hash({"field": field, "value": value})[:36],
            "record_version": version, "live_record_hash": canonical_hash(body), "facts": body})
    if action == "CREDIT_VIEW":
        add("credit_collection", "我的信用卡", "我的信用卡", {"cards": [{key: row[key] for key in
            ("card_id", "card_number", "status", "credit_account_id", "version")} for row in result.get("cards", [])]})
        seen_facilities = set()
        for index, card in enumerate(result.get("cards", []), 1):
            number, facility = card["card_number"], card["credit_account_id"]
            from .credit_pool_facts import valid_credit_pool_contract
            pool=card.get('credit_pool_contract')
            pool_facts={'credit_pool_contract':deepcopy(pool)} if valid_credit_pool_contract(pool,card) else {}
            add("credit_card_status", number, "信用卡尾号"+number[-4:], {key: card[key] for key in
                ("card_id", "card_number", "status", "version", "credit_account_id", "transaction_limit_minor", "supported_currencies")}, card["version"], index)
            if facility not in seen_facilities:
                seen_facilities.add(facility)
                add("credit_facility", facility, "信用卡共享授信", {**{key: card[key] for key in ("credit_account_id", "base_currency",
                    "supported_currencies","shared_credit_limit", "base_credit_limit_minor", "used_base_exposure_minor", "authorization_base_exposure_minor", "available_base_credit_minor")},**pool_facts}, card["facility_version"])
                for balance in card["balances"]:
                    add("credit_currency_debt", facility+":"+balance["currency"], balance["currency"]+"信用卡欠款",
                        {"credit_account_id": facility, **balance, **pool_facts}, card["facility_version"])
        for item in result.get("applications", []):
            add("credit_application", item["application_id"], "信用卡申请" if item["kind"] == "CARD" else "授信调额申请", item, item["version"])
    else:
        from .credit_statement_identity import identity_facts, FIELD, KIND
        try:
            identity = identity_facts(result)
        except (ValueError, TypeError, KeyError):
            return []
        if identity is not None:
            add(FIELD, identity['card_number'], '信用卡尾号' + identity['card_number'][-4:] + '（查询对象）',
                identity, ordinal=1)
            entities[-1]['kind'] = KIND
        from .credit_pool_facts import valid_credit_pool_contract
        pool = result.get("credit_pool_contract")
        pool_facts = {"credit_pool_contract": deepcopy(pool)} if valid_credit_pool_contract(pool, result) else {}
        for row in result.get("statements_by_currency", []):
            add("credit_statement", result["credit_account_id"]+":"+row["currency"], row["currency"]+"信用卡账单",
                {**{key: row[key] for key in ("currency", "outstanding_minor", "current_due_minor", "minimum_due_minor", "principal_minor",
                    "fees_minor", "interest_minor", "credit_balance_minor", "unbilled_principal_minor", "minimum_payment_explanation")}, **pool_facts}, result["facility_version"])
            for obligation in row.get("obligations", []):
                add("credit_payment", obligation["payment_id"], row["currency"]+"信用卡消费记录",
                    {"credit_account_id": result["credit_account_id"], "currency": row["currency"], **obligation}, result["facility_version"])
        for item in result.get("installments", []):
            add("credit_installment", item["installment_id"], item["currency"]+"信用卡分期",
                {key: value for key, value in item.items() if key != "policy_hash"}, result["facility_version"])
    return entities


def _own_completed_effect(session, principal, parent_id, action, field, identity):
    """Audit exception requires the actual confirmed current task's domain receipt."""
    from sqlalchemy import select
    from .credit_runtime import ACTION_TOOLS
    from .models import Confirmation, Task, ToolCall
    from .runtime_models import RuntimeOperation, RuntimeRecord
    parent = session.get(Task, parent_id) if parent_id else None
    record = session.get(RuntimeRecord, parent_id) if parent else None
    if not parent or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id) \
            or parent.state not in {"SUCCEEDED","EXECUTING","AWAITING_CONFIRMATION","PLANNING","PARTIAL","NEED_CLARIFICATION"} or not record or record.kind != "PARENT": return False
    operations = session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id,
        RuntimeOperation.active == 1, RuntimeOperation.plan_version == record.plan_version,
        RuntimeOperation.action_kind == action, RuntimeOperation.status == "SUCCEEDED"))
    for operation in operations:
        child = session.get(Task, operation.child_task_id)
        if not child or child.state != "SUCCEEDED" or (child.user_id, child.sandbox_id) != (principal.user_id, principal.sandbox_id) \
                or canonical_hash(child.plan_json) != child.plan_hash: continue
        nodes = child.plan_json.get("nodes") or []
        if len(nodes) != 1: continue
        node = nodes[0]; args = node.get("args") or {}; tool = ACTION_TOOLS.get(action)
        if action == 'MERCHANT_PURCHASE':
            from .life_write_receipts import TOOLS
            tool = TOOLS[action]
        if node.get("tool_name") != tool or node.get("action_kind") != action: continue
        call = session.scalar(select(ToolCall).where(ToolCall.task_id == child.id, ToolCall.sandbox_id == principal.sandbox_id,
            ToolCall.tool_name == tool, ToolCall.status == "SUCCESS", ToolCall.idempotency_key == operation.idempotency_key))
        if not call or call.result_json != child.result_json: continue
        confirmations = list(session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
            Confirmation.user_id == principal.user_id, Confirmation.sandbox_id == principal.sandbox_id,
            Confirmation.action_type == tool, Confirmation.status == "CONSUMED", Confirmation.consumed_at.is_not(None),
            Confirmation.plan_hash == child.plan_hash, Confirmation.node_key == node.get("node_id"))))
        if action == 'MERCHANT_PURCHASE':
            from .life_write_receipts import completed_life_write_receipt_integrity
            if not confirmations or not completed_life_write_receipt_integrity(session,principal,child,kind=action):continue
        else:
            from .credit_receipts import completed_credit_receipt_integrity
            if not any(completed_credit_receipt_integrity(session,parent,operation,child,call,item) for item in confirmations):continue
        expected = args.get(field)
        if field in {"card_reference", "card_number", "card_last4"}:
            if action == 'MERCHANT_PURCHASE':
                from .credit_models import CreditCard, PaymentRecord
                payment=session.get(PaymentRecord,(child.result_json.get('payment') or {}).get('payment_id'))
                instrument_id=payment.instrument_id if payment else None
                card=session.get(CreditCard,instrument_id.removeprefix('credit:')) \
                    if isinstance(instrument_id,str) and instrument_id.startswith('credit:') else None
                instrument=args.get('instrument') or {}
                if not payment or payment.instrument_type!='CREDIT' or not card \
                        or (payment.user_id,payment.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                        or (card.user_id,card.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                        or card.credit_account_id!=payment.credit_account_id \
                        or payment.instrument_id!=instrument.get('instrument_id') \
                        or instrument.get('instrument_type')!='CREDIT':continue
                expected=card.card_number
            else:
                expected = child.result_json.get("card_number")
        if expected == identity: return True
    return False


def credit_entity_identity_current(session, principal, action_kind, entity, *, field,
        payment_parent_task_id=None, audit_only=False):
    """Identity-only write selector; never returns amount, limit or old authority."""
    from sqlalchemy import select
    from .credit_models import CreditApplication, CreditCard, CreditInstallment, CreditObligation, PaymentRecord
    from .customer_profile_common import require_profile_actor
    try:
        require_profile_actor(session, principal)
        if not isinstance(entity, dict) or entity.get("domain") != "CREDIT": return None
        facts = entity.get("facts")
        if not isinstance(facts, dict) or facts.get("facts_authority") != "OWNED_CREDIT_BOOK" \
                or entity.get("live_record_hash") != canonical_hash(facts): return None
        owner = lambda row: row is not None and (row.sandbox_id, row.user_id) == (principal.sandbox_id, principal.user_id)
        audited = lambda identity: audit_only and _own_completed_effect(session, principal, payment_parent_task_id, action_kind, field, identity)
        from .credit_statement_identity import FIELD, CONSUMERS, checked_identity_facts
        if entity.get('field') == FIELD:
            # This branch never gains the completed-WRITE audit exception.
            if action_kind not in CONSUMERS or field != 'card_reference': return None
            identity = checked_identity_facts(entity)
            if identity is None or identity['owner_scope'] != canonical_hash({
                    'sandbox_id': principal.sandbox_id, 'user_id': principal.user_id}): return None
            card = session.get(CreditCard, identity['card_id'])
            if not owner(card) or card.card_number != identity['card_number'] \
                    or card.credit_account_id != identity['credit_account_id']: return None
            from .credit_service import _facility
            _facility(session, principal, card.credit_account_id, lock=False)
            return card.card_number  # Fresh READ obtains current status/debt.
        if field in {"card_reference", "card_number", "card_last4"}:
            if entity.get("field") != "credit_card_status": return None
            if action_kind=='CARD_STATUS' and field not in {'card_number','card_last4'}:return None
            if action_kind=='BILL_ANALYSIS' and field not in {'card_number','card_last4'}:return None
            card = session.get(CreditCard, facts.get("card_id"))
            if not owner(card) or facts.get("card_number") != card.card_number or facts.get("credit_account_id") != card.credit_account_id: return None
            states = {"CARD_LOCK": {"ACTIVE"}, "CARD_UNLOCK": {"LOCKED"}, "CARD_REPORT_LOSS": {"ACTIVE", "LOCKED", "ISSUED"},
                "CARD_REPLACEMENT": {"LOST"}, "CARD_SET_LIMIT": {"ACTIVE"}, "CREDIT_ACTIVATE": {"ISSUED"},
                'MERCHANT_PURCHASE': {'ACTIVE'}}
            if action_kind not in states and action_kind not in {"CARD_STATUS", "CREDIT_VIEW", "CREDIT_STATEMENT", 'BILL_ANALYSIS', "CREDIT_REPAY", "CREDIT_APPLY", "CREDIT_LIMIT_REQUEST",
                    'CREDIT_INSTALLMENT','CREDIT_SETTLE_INSTALLMENT'}: return None
            return card.card_number if action_kind not in states or card.status in states[action_kind] or audited(card.card_number) else None
        if field == "payment_id" and action_kind == "CREDIT_INSTALLMENT" and entity.get("field") == "credit_payment":
            payment = session.get(PaymentRecord, facts.get("payment_id"))
            if not owner(payment) or payment.instrument_type != "CREDIT" or payment.credit_account_id != facts.get("credit_account_id"): return None
            obligation = session.scalar(select(CreditObligation).where(CreditObligation.payment_id == payment.id))
            return payment.id if obligation and (payment.status == "POSTED" and obligation.remaining_minor > 0 and not obligation.installment_id or audited(payment.id)) else None
        if field == "installment_id" and action_kind == "CREDIT_SETTLE_INSTALLMENT" and entity.get("field") == "credit_installment":
            item = session.get(CreditInstallment, facts.get("installment_id"))
            if not item: return None
            from .credit_service import _facility
            _facility(session, principal, item.credit_account_id, lock=False)
            obligation = session.get(CreditObligation, item.obligation_id)
            return item.id if item.status == "ACTIVE" and obligation and obligation.remaining_minor > 0 or audited(item.id) else None
        if field == "application_id" and action_kind == "CREDIT_ACTIVATE" and entity.get("field") == "credit_application":
            application = session.get(CreditApplication, facts.get("application_id"))
            if not owner(application) or application.kind != "CARD" or application.status != "APPROVED": return None
            card = session.get(CreditCard, application.card_id)
            return application.id if owner(card) and (card.status == "ISSUED" or audited(application.id)) else None
    except (DomainError, AttributeError, KeyError, TypeError, ValueError):
        return None
    return None
