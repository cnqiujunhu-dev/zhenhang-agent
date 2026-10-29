"""Owner-bound READ evidence and finite entities for birthday/merchant followups.

Financial fact publication rechecks original payment/confirmation sources. This
does not grant write permission, advance jobs, refresh a quote, or repair data.
"""
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .planning import canonical_hash
from .models import utcnow


READ_ACTIONS = frozenset({"OCCASION_MEMBER_VIEW", "OCCASION_VIEW", "LIFE_PLAN_VIEW", "MERCHANT_CATALOG", "MERCHANT_ORDERS"})
DOMAINS = {"OCCASION_VIEW": "OCCASIONS", "LIFE_PLAN_VIEW": "LIFE_PLANS",
    "OCCASION_MEMBER_VIEW": "OCCASIONS",
    "MERCHANT_CATALOG": "MERCHANTS", "MERCHANT_ORDERS": "MERCHANT_ORDERS"}
SCHEMAS = {"OCCASION_VIEW": "family-occasion-v1", "LIFE_PLAN_VIEW": "life-plans-v1",
    "OCCASION_MEMBER_VIEW": "family-profile-v1",
    "MERCHANT_CATALOG": "virtual-merchant-catalog-v1", "MERCHANT_ORDERS": "virtual-merchant-orders-v1"}
SELECTORS = {"OCCASION_VIEW": {"occasion_id"}, "LIFE_PLAN_VIEW": {"plan_id"},
    "OCCASION_MEMBER_VIEW": {"family_member_id"},
    "MERCHANT_CATALOG": set(), "MERCHANT_ORDERS": set()}
READ_VERSION = "life-read-proof-v1"


def _body(result):
    return {key: value for key, value in result.items() if key not in {"observed_at", "read_fingerprint"}}


def _owner(principal):
    return canonical_hash({"user_id": principal.user_id, "sandbox_id": principal.sandbox_id})


def _query(action, query):
    if action=='MERCHANT_ORDERS' and isinstance(query,dict) and 'order_scope' in query:
        if set(query)-{'order_scope','plan_id'} or query['order_scope'] not in {'ALL','LIFE_PLANS'} or ('plan_id' in query and (not isinstance(query['plan_id'],str) or not query['plan_id'] or len(query['plan_id'])>120)):
            raise DomainError('LIFE_READ_SELECTOR_INVALID','订单查询范围无效。',409)
        return deepcopy(query)
    if action=='MERCHANT_CATALOG':
        from .merchant_catalog_selection import catalog_query
        return catalog_query(query)
    if action == 'MERCHANT_ORDERS' and isinstance(query, dict) and set(query) == {'plan_id'}:
        if not isinstance(query['plan_id'], str) or not query['plan_id'] or len(query['plan_id']) > 120:
            raise DomainError('LIFE_READ_SELECTOR_INVALID', '订单关联事项无效。', 409)
        return deepcopy(query)
    if not isinstance(query, dict) or set(query) != SELECTORS[action]:
        raise DomainError("LIFE_READ_SELECTOR_INVALID", "查询范围没有完整记录。", 409)
    if any(value is not None and (not isinstance(value, str) or not value or len(value)>120) for value in query.values()):
        raise DomainError("LIFE_READ_SELECTOR_INVALID", "查询对象标识无效。", 409)
    return deepcopy(query)


def _verify_orders(session, principal, rows):
    from .merchant_models import MerchantOrder, MerchantRefund
    from .merchant_service import _verified_life_order, verified_order_payment
    from .merchant_runtime import verified_single_order_source
    from .life_plan_service import _verified_original_authorization, owned_plan
    from .life_plan_models import LifePlanMandate, LifePlanOccurrence
    from .models import Confirmation, Task
    from .credit_models import PaymentRefund
    for projection in rows:
        row = session.get(MerchantOrder, projection["order_id"])
        if row is None or (row.user_id, row.sandbox_id) != (principal.user_id, principal.sandbox_id):
            raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单不属于本人。", 409)
        payment = verified_order_payment(session, principal, row)
        if payment.status == "REFUNDED" and row.status != "REFUNDED":
            raise DomainError("MERCHANT_ORDER_RECONCILIATION_REQUIRED", "付款已退款但商户订单尚未核对，不宣称未退款或已履约。", 409)
        if row.occurrence_id:
            occurrence = session.get(LifePlanOccurrence, row.occurrence_id)
            plan = owned_plan(session, principal, occurrence.plan_id)
            mandate = session.get(LifePlanMandate, occurrence.mandate_id)
            confirmation = session.get(Confirmation, mandate.confirmation_id) if mandate else None
            if mandate is None or confirmation is None or canonical_hash(mandate.scope_json) != mandate.scope_hash:
                raise DomainError("LIFE_PLAN_ORIGINAL_AUTHORIZATION_INVALID", "原计划授权来源不完整。", 409)
            _verified_original_authorization(session, principal, plan, mandate, confirmation, session.get(Task, mandate.task_id))
            _verified_life_order(row, {"plan": plan, "occurrence": occurrence, "mandate": mandate})
        else:
            verified_single_order_source(session, principal, row)
        if row.status == "REFUNDED":
            refund = session.scalar(select(MerchantRefund).where(MerchantRefund.order_id == row.id, MerchantRefund.status == "POSTED"))
            posted = session.get(PaymentRefund, (refund.receipt_json or {}).get("refund_id")) if refund else None
            if (posted is None or posted.payment_id != row.payment_receipt_json["payment_id"]
                    or (posted.user_id, posted.sandbox_id) != (principal.user_id, principal.sandbox_id)
                    or posted.receipt_json.get("amount_minor") != row.amount_minor):
                raise DomainError("MERCHANT_REFUND_NOT_VERIFIED", "退款入账来源未通过核验。", 409)


def _verify_result(session, principal, action, result):
    if action == "MERCHANT_ORDERS":
        _verify_orders(session, principal, result.get("orders", []))
    elif action == "LIFE_PLAN_VIEW":
        from .credit_models import PaymentCashReservation
        from .life_plan_service import owned_plan
        for projected in result.get("plans", []):
            plan = owned_plan(session, principal, projected["plan_id"])
            if canonical_hash(plan.scope_json) != plan.scope_hash:
                raise DomainError("LIFE_PLAN_SCOPE_CHANGED", "计划范围未通过核验。", 409)
            order_rows = [order for occurrence in projected["occurrences"] for order in occurrence["orders"]]
            _verify_orders(session, principal, order_rows)
            if sum(order["amount_minor"] for order in order_rows) != plan.spent_minor:
                raise DomainError("LIFE_PLAN_PAYMENT_TOTAL_CHANGED", "已付合计与真实订单不一致。", 409)
            if plan.reservation_id:
                row = session.get(PaymentCashReservation, plan.reservation_id)
                expected_owners = {"life:" + occurrence["occurrence_id"] for occurrence in projected["occurrences"]}
                if (row is None or (row.user_id, row.sandbox_id, row.account_id) != (plan.user_id, plan.sandbox_id, plan.account_id)
                        or row.owner_ref not in expected_owners or row.remaining_minor != plan.reserved_minor):
                    raise DomainError("LIFE_PLAN_RESERVATION_CHANGED", "预留合计与中央预留来源不一致。", 409)
            elif plan.reserved_minor != 0:
                raise DomainError("LIFE_PLAN_RESERVATION_CHANGED", "缺少预留来源，不将未知金额当成有效预留。", 409)


def finish_life_read(session, principal, action, result, query):
    if action not in READ_ACTIONS or result.get("schema_version") != SCHEMAS[action] or result.get("bank_state_changed") is not False:
        raise DomainError("LIFE_READ_RESULT_INVALID", "不是已登记的只读结果。", 409)
    _verify_result(session, principal, action, result)
    observed=as_utc(utcnow()).isoformat()
    if action=='OCCASION_VIEW' and 'next_birthdays' in result:
        from .occasion_upcoming import upcoming_result_valid
        if not upcoming_result_valid(result):
            raise DomainError('OCCASION_READ_DATE_INVALID','生日查询的观察日期未通过核验。',409)
        observed=result['next_birthdays']['as_of']
    value = {**deepcopy(result), "action": action, "query": _query(action, query),
        "read_schema_version": READ_VERSION, "owner_scope_hash": _owner(principal), "observed_at": observed}
    if action == "MERCHANT_CATALOG":
        from .merchant_catalog_selection import category_scope_valid
        if not category_scope_valid(value):
            raise DomainError("MERCHANT_CATALOG_READ_SCOPE_INVALID", "商品目录结果与本次类别筛选不一致。", 409)
    value["read_fingerprint"] = canonical_hash(_body(value))
    return value


def life_read_result_current(session, principal, action, result):
    """Boolean fresh proof, analogous to profile_read_result_current."""
    try:
        if (action not in READ_ACTIONS or not isinstance(result, dict) or result.get("read_schema_version") != READ_VERSION
                or result.get("schema_version") != SCHEMAS[action] or result.get("action") != action
                or result.get("bank_state_changed") is not False or result.get("owner_scope_hash") != _owner(principal)
                or not datetime.fromisoformat(result["observed_at"]).tzinfo
                or result.get("read_fingerprint") != canonical_hash(_body(result))):
            return False
        query = _query(action, result.get("query"))
        if action == "OCCASION_MEMBER_VIEW":
            from .family_profile_service import family_member_profiles
            fresh = family_member_profiles(session, principal, query["family_member_id"])
        elif action == "OCCASION_VIEW":
            from .occasion_service import occasion_view
            upcoming=result.get('next_birthdays')
            retained_as_of=datetime.fromisoformat(upcoming['as_of']) if isinstance(upcoming,dict) else None
            fresh = occasion_view(session, principal, query["occasion_id"],as_of=retained_as_of)
        elif action == "LIFE_PLAN_VIEW":
            from .life_plan_service import life_plan_view
            fresh = life_plan_view(session, principal, query["plan_id"])
        elif action == "MERCHANT_CATALOG":
            from .merchant_service import merchant_catalog
            fresh = merchant_catalog(session, principal, **query)
        else:
            from .merchant_service import orders_view
            fresh = orders_view(session, principal, **query)
        expected = finish_life_read(session, principal, action, fresh, query)
        return expected["read_fingerprint"] == result["read_fingerprint"]
    except (DomainError, TypeError, ValueError, KeyError, AttributeError):
        return False


def life_result_fact_entities(action, result):
    """Only after authenticated fresh proof; identity selectors grant no write."""
    from .life_plan_presentation import life_read_recorded
    if not isinstance(action, str) or not life_read_recorded(action, result):
        return []
    entities = []
    def add(field, value, label, facts, version=None, ordinal=None):
        facts = {**deepcopy(facts), "facts_authority": "OWNED_REGISTERED_LIFE_READ_NOT_WRITE_AUTHORIZATION"}
        entities.append({"read_kind": action, "domain": DOMAINS[action], "field": field, "value": value,
            "label": label, "record_id": value if field.endswith("_id") else canonical_hash({"field": field, "query": result["query"]})[:36],
            "record_version": version, "ordinal": ordinal, "live_record_hash": canonical_hash(facts), "facts": facts})
    if action == "OCCASION_MEMBER_VIEW":
        for index, row in enumerate(result["members"], 1):
            add("family_member_id", row["family_member_id"], row["family_member_name"],
                {key: row.get(key) for key in ("family_member_name", "family_relationship", "status", "source", "account_access_granted")},
                row["version"], index)
    elif action == "OCCASION_VIEW":
        add("occasion_collection", "家庭生日", "本次查询的家庭生日", {"occasion_count": len(result["occasions"])})
        for index, row in enumerate(result["occasions"], 1):
            add("occasion_id", row["occasion_id"], row["family_member_name"] + "的生日",
                {key: row.get(key) for key in ("family_member_id", "family_member_name", "month", "day", "calendar", "timezone", "confirmed_solar_date", "source", "status")}, row["version"], index)
    elif action == "LIFE_PLAN_VIEW":
        add("life_plan_collection", "生活计划", "本次查询的生活计划", {"plan_count": len(result["plans"])})
        for index, row in enumerate(result["plans"], 1):
            add("life_plan_id", row["plan_id"], row["name"], {"name": row["name"], "status": row["status"],
                "budget_minor": row["budget_minor"], "reserved_minor": row["reserved_minor"], "posted_minor": row["spent_minor"],
                "refunded_minor": row["refunded_minor"], "net_cash_debit_minor": row["net_cash_debit_minor"], "currency": "CNY",
                "card_lock_changed": False, "terms": {"maximum_occurrences": 1, "recurring": False,
                    "expires_at": row["scope"]["expires_at"], "failure_policy": row["scope"]["failure_policy"],
                    "funding_instrument_type": "DEBIT", "cash_reservation_is_card_lock": False},
                "order_statuses": [{"status": order["status"], "paid_amount_minor": order["amount_minor"]}
                    for occurrence in row["occurrences"] for order in occurrence["orders"]]}, row["version"], index)
    elif action == "MERCHANT_CATALOG":
        category_set = result['query'].get('merchant_categories')
        add("merchant_collection", "商户目录", "当前登记商户目录", {"product_count": len(result["products"]),
            **({"merchant_categories": deepcopy(category_set)} if category_set is not None else {})})
        for index, row in enumerate(result["products"], 1):
            add("merchant_product_id", row["product_id"], row["merchant_name"] + " · " + row["product_name"],
                {"name": row["product_name"], "merchant_name": row["merchant_name"], "unit_price_minor": row["unit_price_minor"],
                    "currency": row["currency"], "stock": row["stock"], "status": row["status"],
                    **({"service_content": deepcopy(row["service_content"])} if isinstance(row.get("service_content"), dict) else {}),
                    "price_source": "REGISTERED_VIRTUAL_MERCHANT_CATALOG_NOT_EXTERNAL_QUOTE",
                    "terms": {"refund_policy": row["refund_policy"], "external_merchant_connected": False,
                        "single_purchase_payment_types": ["DEBIT", "CREDIT"]}}, row["product_version"], index)
    else:
        add("merchant_order_collection", "商户订单", "本人已登记商户订单", {"order_count": len(result["orders"])})
        for index, row in enumerate(result["orders"], 1):
            add("merchant_order_id", row["order_id"], str(row["merchant_name"]) + " · " + str(row["product_name"]),
                {"name": row["product_name"], "merchant_name": row["merchant_name"], "status": row["status"],
                    "paid_amount_minor": row["amount_minor"], "refunded_minor": row["amount_minor"] if row["status"] == "REFUNDED" else 0,
                    "currency": row["currency"], "fulfilment_status": "FULFILLED" if row["status"] == "FULFILLED" else "NOT_VERIFIED",
                    **{key: row["payment"][key] for key in ("instrument_type", "cash_debit_minor", "credit_debit_minor", "credit_balance_used_minor")
                       if key in row.get("payment", {})},
                    "status_source": row["status_source"], "external_fulfilment_verified": False}, row["version"], index)
    return entities


def life_entity_identity_current(session, principal, action_kind, entity, *, field,
        payment_parent_task_id=None, audit_only=False):
    """Only immutable owned selectors; never reuse price/budget/card/recipient.

    Full READ freshness remains strict in life_read_result_current. The caller
    checks the original source catalog and actor. An unchanged identity needs
    no write receipt; changed identities need this parent's completed write.
    """
    if field=='life_plan_name' and entity.get('read_kind')=='LIFE_PLAN_VIEW' and entity.get('field')=='life_plan_id':
        identifier=life_entity_identity_current(session,principal,action_kind,entity,field='life_plan_id',
            payment_parent_task_id=payment_parent_task_id,audit_only=audit_only)
        if identifier is None:return None
        from .life_plan_service import owned_plan
        current=owned_plan(session,principal,identifier)
        return current.name if (entity.get('facts')or{}).get('name')==current.name else None
    try:
        if not isinstance(entity, dict) or not isinstance(entity.get("facts"), dict):
            return None
        allowed = {
            "occasion_id": ("OCCASION_VIEW", "occasion_id", {"OCCASION_VIEW", "OCCASION_SET", "LIFE_PLAN_AUTHORIZE"}),
            "family_id": ("OCCASION_VIEW", "occasion_id", {"OCCASION_SET", "LIFE_PLAN_AUTHORIZE"}),
            "family_member_id": ("OCCASION_VIEW", "occasion_id", {"OCCASION_SET", "LIFE_PLAN_AUTHORIZE"}),
            "life_plan_id": ("LIFE_PLAN_VIEW", "life_plan_id", {"LIFE_PLAN_VIEW", "LIFE_PLAN_AUTHORIZE", "LIFE_PLAN_CANCEL", "MERCHANT_ORDERS"}),
            "merchant_product_id": ("MERCHANT_CATALOG", "merchant_product_id", {"MERCHANT_PURCHASE","MERCHANT_CATALOG"}),
        }
        if field == "family_member_id" and entity.get("read_kind") == "OCCASION_MEMBER_VIEW":
            allowed[field] = ("OCCASION_MEMBER_VIEW", "family_member_id", {"OCCASION_MEMBER_VIEW", "OCCASION_MEMBER_SET", "OCCASION_SET", "LIFE_PLAN_AUTHORIZE"})
        contract = allowed.get(field)
        if contract is None:
            return None
        read_kind, source_field, actions = contract
        identifier = entity.get("record_id")
        if (action_kind not in actions or entity.get("read_kind") != read_kind or entity.get("domain") != DOMAINS[read_kind]
                or entity.get("field") != source_field or entity.get("value") != identifier
                or not isinstance(identifier, str) or not identifier
                or entity.get("live_record_hash") != canonical_hash(entity["facts"])):
            return None
        if read_kind == "OCCASION_MEMBER_VIEW":
            from .family_profile_service import family_member_profiles
            raw, query = family_member_profiles(session, principal, identifier), {"family_member_id": identifier}
        elif read_kind == "OCCASION_VIEW":
            from .occasion_service import occasion_view, owned_occasion
            original_row = owned_occasion(session, principal, identifier)
            raw, query = occasion_view(session, principal, identifier), {"occasion_id": identifier}
        elif read_kind == "LIFE_PLAN_VIEW":
            from .life_plan_service import life_plan_view
            raw, query = life_plan_view(session, principal, identifier), {"plan_id": identifier}
        else:
            from .merchant_service import merchant_catalog
            raw, query = merchant_catalog(session, principal), {}
        live = finish_life_read(session, principal, read_kind, raw, query)
        current = next((row for row in life_result_fact_entities(read_kind, live)
            if row["field"] == source_field and row["record_id"] == identifier), None)
        if current is None:
            return None
        if action_kind=='MERCHANT_CATALOG' and read_kind=='MERCHANT_CATALOG':
            # The immutable original READ proves identity only. A fresh query
            # obtains current price/stock; old values never authorize a WRITE.
            return identifier
        equal = all(entity.get(key) == current.get(key) for key in ("record_version", "live_record_hash", "facts"))
        if read_kind == "OCCASION_MEMBER_VIEW":
            return identifier if equal else None
        if action_kind in {"OCCASION_VIEW", "LIFE_PLAN_VIEW"}:
            # A fresh READ can select the same owned current birthday. Its
            # audit is still a READ source proof, not a completed WRITE; do
            # not require a nonexistent write receipt or accept stale dates.
            return identifier if equal else None
        if audit_only:
            if not equal and not _self_identity_change(session, principal, payment_parent_task_id, entity, current):
                return None
        elif not equal:
            return None
        return original_row.family_member_id if field in {"family_id", "family_member_id"} else identifier
    except (DomainError, TypeError, ValueError, KeyError, AttributeError, IndexError):
        return None


def _self_identity_change(session, principal, parent_id, old, current):
    from .runtime_models import RuntimeOperation, RuntimeRecord
    from .models import Task
    from .life_write_receipts import completed_life_write_receipt_integrity
    parent = session.get(Task, parent_id) if isinstance(parent_id, str) else None
    record = session.get(RuntimeRecord, parent_id) if parent else None
    if (parent is None or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or record is None or record.kind != "PARENT" or canonical_hash(parent.plan_json) != parent.plan_hash):
        return False
    proven = []
    for operation in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id,
            RuntimeOperation.sandbox_id == principal.sandbox_id, RuntimeOperation.status == "SUCCEEDED").order_by(RuntimeOperation.ordinal)):
        from .intelligence import action_signature
        child = session.get(Task, operation.child_task_id) if operation.child_task_id else None
        child_record = session.get(RuntimeRecord, operation.child_task_id) if child else None
        if (child_record is None or child_record.kind != "CHILD" or child_record.root_task_id != parent.id
                or child_record.metadata_json.get("operation_id") != operation.id
                or operation.args_json.get("kind") != operation.action_kind
                or operation.fingerprint != canonical_hash(action_signature(operation.args_json))
                or not completed_life_write_receipt_integrity(session, principal, child, operation.action_kind)):
            continue
        proven.append((operation, child))
    if not proven:
        return False
    field, identifier = old["field"], old["record_id"]
    before, after = old["facts"], current["facts"]
    if field == "occasion_id":
        updates = [row for operation, child in proven if operation.action_kind == "OCCASION_SET"
            for row in child.result_json.get("occasions", []) if row.get("occasion_id") == identifier]
        if not updates or any(before.get(key) != after.get(key) for key in ("family_member_id", "family_member_name")):
            return False
        versions = sorted(row["version"] for row in updates)
        return versions == list(range(old["record_version"] + 1, current["record_version"] + 1))
    if field == "life_plan_id":
        receipts = [row for operation, child in proven if operation.action_kind in {"LIFE_PLAN_AUTHORIZE", "LIFE_PLAN_CANCEL"}
            for row in child.result_json.get("plans", []) if row.get("plan_id") == identifier]
        receipts += [child.result_json["replaced_plan"] for operation, child in proven
            if operation.action_kind == "LIFE_PLAN_AUTHORIZE" and isinstance(child.result_json.get("replaced_plan"), dict)
            and child.result_json["replaced_plan"].get("plan_id") == identifier]
        if not receipts or any(before.get(key) != after.get(key) for key in ("name", "budget_minor", "currency", "terms")):
            return False
        latest = receipts[-1]
        return (latest.get("status") == after.get("status") and latest.get("reserved_minor") == after.get("reserved_minor")
            and latest.get("spent_minor") == after.get("posted_minor"))
    if field == "merchant_product_id":
        from .merchant_models import MerchantOrder
        orders = [order for operation, child in proven if operation.action_kind == "MERCHANT_PURCHASE"
            for order in session.scalars(select(MerchantOrder).where(MerchantOrder.source_task_id == child.id))
            if order.product_id == identifier and order.status in {"PAID", "FULFILLED"}]
        if not orders:
            return False
        if any(before.get(key) != after.get(key) for key in before if key != "stock"):
            return False
        versions = sorted(order.product_snapshot_json["product_version"] for order in orders)
        return (versions == list(range(old["record_version"], current["record_version"]))
            and before.get("stock") - sum(order.quantity for order in orders) == after.get("stock"))
    return False
