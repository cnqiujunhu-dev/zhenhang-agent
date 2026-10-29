"""Finite virtual merchant adapter. Every status is a local ledger observation.

No HTTP, external fulfilment, or model call occurs here. A paid order is not
delivered; a requested refund is not credited until the payment ledger says so.
"""
from copy import deepcopy
import unicodedata
import re

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .merchant_models import MerchantOrder, MerchantProduct, MerchantRefund, VirtualMerchant
from .models import utcnow
from .planning import canonical_hash


from .merchant_catalog_registry import DEFAULT_CATALOG, CATEGORY_ALIASES


def ensure_virtual_merchant_catalog(session, sandbox_id):
    """Explicit additive initializer only; never resets price, stock or orders."""
    for code, name, category, sku, product_name, amount in DEFAULT_CATALOG:
        merchant = session.scalar(select(VirtualMerchant).where(VirtualMerchant.sandbox_id == sandbox_id,
            VirtualMerchant.code == code))
        if merchant is None:
            merchant = VirtualMerchant(sandbox_id=sandbox_id, code=code, name=name, category=category)
            session.add(merchant); session.flush()
        if session.scalar(select(MerchantProduct.id).where(MerchantProduct.merchant_id == merchant.id,
                MerchantProduct.sku == sku)) is None:
            session.add(MerchantProduct(merchant_id=merchant.id, sku=sku, name=product_name,
                price_minor=amount, stock=20))
    session.flush()


CATALOG_READ_ALIASES = {category: frozenset(names) for category, names in CATEGORY_ALIASES.items()}


def _catalog_name(value):
    return unicodedata.normalize('NFKC',value).strip().casefold() if isinstance(value,str) else None


def merchant_catalog(session, principal, *, merchant_product_id=None, product_name=None, merchant_categories=None):
    require_profile_actor(session, principal)
    rows = session.execute(select(VirtualMerchant, MerchantProduct).join(MerchantProduct,
        MerchantProduct.merchant_id == VirtualMerchant.id).where(VirtualMerchant.sandbox_id == principal.sandbox_id)
        .order_by(VirtualMerchant.code, MerchantProduct.sku)).all()
    from .merchant_catalog_selection import catalog_query, category_scope
    selectors=catalog_query({key:value for key,value in {'merchant_product_id':merchant_product_id,'product_name':product_name,
        'merchant_categories':merchant_categories}.items() if value is not None})
    if selectors:
        named=[(merchant,product) for merchant,product in rows if product_name is None
            or _catalog_name(product_name)==_catalog_name(product.name)
            or _catalog_name(product_name) in CATALOG_READ_ALIASES.get(merchant.category,())]
        selected=[(merchant,product) for merchant,product in named if (merchant_product_id is None or product.id==merchant_product_id)
            and ('merchant_categories' not in selectors or merchant.category in selectors['merchant_categories'])]
        scalar_identity = merchant_product_id is not None or product_name is not None
        if not selected and scalar_identity:
            raise DomainError('MERCHANT_CATALOG_SELECTOR_NOT_FOUND','未找到同时符合所选条件的本账套登记商品。',422)
        if len(selected)!=1 and scalar_identity:
            raise DomainError('MERCHANT_CATALOG_SELECTOR_AMBIGUOUS','该名称对应多个登记商品，请明确选择，不能默认取第一项。',422)
        rows=selected
    result={"schema_version": "virtual-merchant-catalog-v1", "source": "VIRTUAL_MERCHANT_REGISTER",
        "external_merchant_connected": False, "bank_state_changed": False, "products": [{"merchant_id": m.id, "merchant_name": m.name,
            "category": m.category, "product_id": p.id, "sku": p.sku, "product_name": p.name,
            "unit_price_minor": p.price_minor, "currency": p.currency, "stock": p.stock,
            "merchant_version": m.version, "product_version": p.version,
            "status": p.status if m.status == "ACTIVE" else m.status,
            "refund_policy": p.refund_policy,
            "service_content": {"version": "merchant-service-content-v1", "status": "NOT_REGISTERED",
                "included_items": None, "guest_count": None, "delivery_included": None}} for m, p in rows]}
    if selectors:result['query_scope']=category_scope(selectors) if 'merchant_categories' in selectors else {'version':'merchant-catalog-selector-v1',**selectors}
    return result


def selected_plan_items(session, principal, text):
    """Parse only an explicit list field against the current owned catalog."""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
        raise DomainError("LIFE_PLAN_ITEMS_REQUIRED", "请先查看商品目录，再明确选择本次1至4项商品；可单选餐厅，不会默认选择鲜花或蛋糕。", 422)
    number = r"(?:\d{1,2}|[一二两三四五六七八九十]{1,2})"
    shared = re.fullmatch(rf"(.+?)各\s*({number})\s*(?:份|件|项)", text.strip())
    list_text = shared[1] if shared else text.strip()
    parts = re.split(r"\s*(?:、|，|,|；|;|和|与|\band\b)\s*", list_text, flags=re.I)
    if not 1 <= len(parts) <= 4 or any(not value for value in parts):
        raise DomainError("MERCHANT_ITEMS_INVALID", "请给出1至4项明确商品清单，每项使用登记商品名并可指定1至10份。", 422)
    catalog = merchant_catalog(session, principal)["products"]
    items = []
    for part in parts:
        quantity = 1
        number = r"(?:\d{1,2}|[一二两三四五六七八九十]{1,2})"
        match = re.fullmatch(rf"(.+?)(?:\s*[x×*]\s*({number})|\s+({number})\s*(?:份|件|项)?|\s*({number})\s*(?:份|件))", part)
        if match:
            part = match[1].strip()
            token = next(value for value in match.groups()[1:] if value is not None)
        else:
            match = re.fullmatch(rf"({number})\s*(?:份|件)\s*(.+)", part)
            token = match[1] if match else (shared[2] if shared else "1")
            if match: part = match[2].strip()
        from .intelligence import _chinese_integer
        quantity = int(token) if token.isascii() and token.isdigit() else _chinese_integer(token)
        if not 1 <= quantity <= 10:
            raise DomainError("MERCHANT_ITEMS_INVALID", "每项商品数量须明确为1至10份。", 422)
        # This typed list may retain the user's explicit merchant qualifier.
        # Match complete registered identities, never strip an arbitrary shop
        # prefix, combine filters from different items or select a first hit.
        reference = _catalog_name(part)
        matching = [row for row in catalog if reference == _catalog_name(row["product_name"])
            or reference in CATALOG_READ_ALIASES.get(row["category"], ())
            or reference in {_catalog_name(f'{row["merchant_name"]}{separator}{row["product_name"]}')
                for separator in ('的', '', ' ') }]
        if not matching:
            raise DomainError('MERCHANT_CATALOG_SELECTOR_NOT_FOUND', '未找到同时符合所选条件的本账套登记商品。', 422)
        if len(matching) != 1:
            raise DomainError('MERCHANT_CATALOG_SELECTOR_AMBIGUOUS', '该名称对应多个登记商品，请明确选择，不能默认取第一项。', 422)
        selected = matching[0]
        items.append({"merchant_id": selected["merchant_id"], "product_id": selected["product_id"], "quantity": quantity})
    quote_products(session, principal, items)
    return items


def quote_products(session, principal, items, *, lock=False):
    if not isinstance(items, list) or not 1 <= len(items) <= 4:
        raise DomainError("MERCHANT_ITEMS_INVALID", "请选择一至四项明确商品。", 422)
    quoted, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"merchant_id", "product_id", "quantity"}:
            raise DomainError("MERCHANT_ITEMS_INVALID", "商品选择字段不正确。", 422)
        quantity = item.get("quantity")
        if (type(quantity) is not int or not 1 <= quantity <= 10
                or any(not isinstance(item.get(key), str) or not 1 <= len(item[key]) <= 80 for key in ("product_id", "merchant_id"))
                or item.get("product_id") in seen):
            raise DomainError("MERCHANT_ITEMS_INVALID", "请核对商品数量，不能重复同一商品。", 422)
        seen.add(item.get("product_id"))
    locked = {}
    if lock:
        # Different payers can share a catalog. Lock the entire SKU set in a
        # stable order, not the customer's flower/cake presentation order.
        rows = session.execute(select(MerchantProduct, VirtualMerchant).join(VirtualMerchant,
            MerchantProduct.merchant_id == VirtualMerchant.id).where(MerchantProduct.id.in_(sorted(seen)),
            VirtualMerchant.sandbox_id == principal.sandbox_id).order_by(VirtualMerchant.id, MerchantProduct.id)
            .with_for_update().execution_options(populate_existing=True)).all()
        locked = {product.id: (product, merchant) for product, merchant in rows}
    for item in items:
        quantity = item["quantity"]
        query = select(MerchantProduct, VirtualMerchant).join(VirtualMerchant,
            MerchantProduct.merchant_id == VirtualMerchant.id).where(MerchantProduct.id == item.get("product_id"),
            VirtualMerchant.id == item.get("merchant_id"), VirtualMerchant.sandbox_id == principal.sandbox_id)
        found = locked.get(item["product_id"]) if lock else session.execute(query).first()
        if found is not None and found[1].id != item["merchant_id"]:
            found = None
        if found is None:
            raise DomainError("MERCHANT_PRODUCT_NOT_FOUND", "未找到本账套已登记的指定商户商品。", 404)
        product, merchant = found
        if merchant.status != "ACTIVE" or product.status != "AVAILABLE" or product.currency != "CNY":
            raise DomainError("MERCHANT_PRODUCT_UNAVAILABLE", "指定商品或商户目前不可用。", 409)
        if product.stock < quantity:
            raise DomainError("MERCHANT_STOCK_INSUFFICIENT", "指定商品库存不足，不会擅自替换。", 409)
        quoted.append({"merchant_id": merchant.id, "merchant_name": merchant.name, "merchant_category": merchant.category,
            "product_id": product.id, "product_name": product.name, "sku": product.sku, "quantity": quantity,
            "unit_price_cap_minor": product.price_minor, "amount_minor": product.price_minor * quantity,
            "currency": product.currency, "product_version": product.version, "merchant_version": merchant.version,
            "refund_policy": product.refund_policy, "source": "VIRTUAL_MERCHANT_REGISTER"})
    return quoted


def _order_request(context, index):
    plan, occurrence, mandate = context["plan"], context["occurrence"], context["mandate"]
    scope = mandate.scope_json
    if type(index) is not int or not 0 <= index < len(scope["items"]):
        raise DomainError("MERCHANT_ITEM_NOT_AUTHORIZED", "商品不在本次授权范围内。", 403)
    return {"plan_id": plan.id, "occurrence_id": occurrence.id, "mandate_id": mandate.id,
        "scope_hash": mandate.scope_hash, "item_index": index, "item": scope["items"][index],
        "recipient": scope["recipient"], "delivery_start": scope["delivery_start"], "delivery_end": scope["delivery_end"]}


def place_authorized_order(session, principal, occurrence_id, item_index, *, now=None):
    from .life_plan_service import execution_context
    from .payment_instruments import post_merchant_payment
    now = as_utc(now or utcnow())
    context = execution_context(session, principal, occurrence_id, now=now, for_order=True)
    plan, occurrence = context["plan"], context["occurrence"]
    request = _order_request(context, item_index)
    key, digest = f"life:{occurrence.id}:order:{item_index}", canonical_hash(request)
    previous = session.scalar(select(MerchantOrder).where(MerchantOrder.sandbox_id == principal.sandbox_id,
        MerchantOrder.operation_key == key))
    if previous:
        if previous.user_id != principal.user_id or previous.request_hash != digest:
            raise DomainError("MERCHANT_IDEMPOTENCY_CONFLICT", "订单请求与原幂等记录不一致。", 409)
        return order_view(previous)
    item = request["item"]
    quote = quote_products(session, principal, [{k: item[k] for k in ("merchant_id", "product_id", "quantity")}], lock=True)[0]
    if quote["unit_price_cap_minor"] > item["unit_price_cap_minor"] or quote["merchant_version"] != item["merchant_version"] \
            or quote["refund_policy"] != item["refund_policy"] or quote["sku"] != item["sku"] \
            or quote["product_name"] != item["product_name"] or quote["merchant_category"] != item["merchant_category"]:
        raise DomainError("LIFE_PLAN_PRICE_OR_TERMS_CHANGED", "商品超价或条款已变化，计划暂停等待本人重新确认。", 409)
    amount = quote["amount_minor"]
    if plan.spent_minor + amount > plan.budget_minor or plan.reserved_minor < amount or not plan.reservation_id:
        raise DomainError("LIFE_PLAN_BUDGET_EXCEEDED", "订单超出本次预留预算，不会改用其他资金。", 409)
    order = MerchantOrder(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        merchant_id=item["merchant_id"], product_id=item["product_id"], occurrence_id=occurrence.id,
        quantity=item["quantity"], amount_minor=amount, recipient_json=deepcopy(request["recipient"]),
        delivery_start=context["delivery_start"], delivery_end=context["delivery_end"],
        operation_key=key, request_hash=digest, payment_receipt_json={}, product_snapshot_json=deepcopy(quote))
    session.add(order); session.flush()
    receipt = post_merchant_payment(session, principal, instrument_snapshot=context["instrument"],
        amount_minor=amount, merchant_ref=item["merchant_id"], operation_key=key,
        reservation_id=plan.reservation_id, authorization_ref=context["mandate"].id, channel="LIFE_PLAN")
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != "payment-receipt-v1"
            or receipt.get("status") != "POSTED" or receipt.get("amount_minor") != amount
            or receipt.get("account_id") != plan.account_id or receipt.get("instrument_type") != "DEBIT"
            or receipt.get("instrument_id") != context["instrument"]["instrument_id"]
            or receipt.get("reservation_id") != plan.reservation_id or receipt.get("currency") != "CNY"
            or receipt.get("merchant_ref") != item["merchant_id"] or not receipt.get("payment_id")
            or receipt.get("authorization_ref") != context["mandate"].id
            or receipt.get("cash_debit_minor") != amount or receipt.get("credit_debit_minor") != 0
            or receipt.get("ledger_balanced") is not True):
        raise DomainError("MERCHANT_PAYMENT_NOT_VERIFIED", "付款未取得可核验成功回执，订单不宣称支付成功。", 409)
    product = session.get(MerchantProduct, item["product_id"])
    product.stock -= item["quantity"]; product.version += 1
    order.payment_receipt_json = deepcopy(receipt)
    verified_order_payment(session, principal, order)
    plan.reserved_minor -= amount; plan.spent_minor += amount
    session.flush()
    return order_view(order)


def order_view(row):
    return {"order_id": row.id, "occurrence_id": row.occurrence_id, "merchant_id": row.merchant_id,
        "merchant_name": row.product_snapshot_json.get("merchant_name"), "product_name": row.product_snapshot_json.get("product_name"),
        "product_id": row.product_id, "quantity": row.quantity, "amount_minor": row.amount_minor,
        "currency": row.currency, "status": row.status, "version": row.version,
        "delivery_start": as_utc(row.delivery_start).isoformat(), "delivery_end": as_utc(row.delivery_end).isoformat(),
        "payment": deepcopy(row.payment_receipt_json), "status_source": row.status_source,
        "external_fulfilment_verified": False, "simulated": True}


def verified_order_payment(session, principal, order):
    from .credit_models import PaymentJournal, PaymentLedgerEntry, PaymentRecord
    receipt = order.payment_receipt_json
    payment = session.get(PaymentRecord, receipt.get("payment_id")) if isinstance(receipt, dict) else None
    journal = session.get(PaymentJournal, payment.journal_id) if payment else None
    entries = session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == payment.journal_id)).all() if payment else []
    if (payment is None or journal is None or payment.status not in {"POSTED", "REFUNDED"}
            or (payment.user_id, payment.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or payment.receipt_json != receipt or payment.operation_key != order.operation_key
            or payment.amount_minor != order.amount_minor or payment.currency != order.currency
            or payment.merchant_ref != order.merchant_id or journal.request_hash != payment.request_hash
            or journal.operation_key != payment.operation_key or journal.result_json != receipt
            or len(entries) != 2 or sum(entry.delta_minor for entry in entries) != 0
            or any((entry.user_id, entry.sandbox_id, entry.currency) !=
                (principal.user_id, principal.sandbox_id, payment.currency) for entry in entries)
            or sum(entry.delta_minor for entry in entries if entry.account_ref == "MERCHANT:" + order.merchant_id) != order.amount_minor):
        raise DomainError("MERCHANT_PAYMENT_SOURCE_INVALID", "原付款与成对账务来源未通过核验。", 409)
    source_ref = payment.account_id if payment.instrument_type == "DEBIT" else "CREDIT:" + str(payment.credit_account_id)
    if sum(entry.delta_minor for entry in entries if entry.account_ref == source_ref) != -order.amount_minor:
        raise DomainError("MERCHANT_PAYMENT_SOURCE_INVALID", "原付款资金来源未通过核验。", 409)
    return payment


def _verified_life_order(row, context):
    scope, occurrence = context["mandate"].scope_json, context["occurrence"]
    matches = [index for index, item in enumerate(scope["items"]) if
        (item["merchant_id"], item["product_id"], item["quantity"]) == (row.merchant_id, row.product_id, row.quantity)]
    if len(matches) != 1:
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单不属于本次已授权商品。", 409)
    index = matches[0]
    expected = _order_request(context, index)
    if (row.request_hash != canonical_hash(expected) or row.operation_key != f"life:{occurrence.id}:order:{index}"
            or row.recipient_json != scope["recipient"] or row.amount_minor > scope["items"][index]["amount_minor"]
            or row.payment_receipt_json.get("authorization_ref") != context["mandate"].id
            or as_utc(row.delivery_start).isoformat() != scope["delivery_start"]
            or as_utc(row.delivery_end).isoformat() != scope["delivery_end"]):
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单与原始有界授权来源不一致。", 409)
    snapshot = row.product_snapshot_json
    if (snapshot.get("amount_minor") != row.amount_minor
            or any(snapshot.get(field) != scope["items"][index].get(field)
                for field in ("merchant_id", "product_id", "sku", "product_name", "quantity", "currency", "refund_policy"))):
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单商品快照与原授权不一致。", 409)


def orders_view(session, principal, *, occurrence_id=None, plan_id=None,order_scope=None):
    require_profile_actor(session, principal)
    from .customer_history_archive import visible_history
    query = select(MerchantOrder).where(MerchantOrder.user_id == principal.user_id,
        MerchantOrder.sandbox_id == principal.sandbox_id,
        visible_history(session, principal, MerchantOrder.created_at))
    if order_scope not in {None,'ALL','LIFE_PLANS'}:raise DomainError('MERCHANT_ORDER_SCOPE_INVALID','订单查询范围无效。',422)
    if order_scope=='LIFE_PLANS':query=query.where(MerchantOrder.occurrence_id.is_not(None))
    if occurrence_id:
        query = query.where(MerchantOrder.occurrence_id == occurrence_id)
    if plan_id is not None:
        from .life_plan_service import owned_plan
        from .life_plan_models import LifePlanOccurrence
        plan = owned_plan(session, principal, plan_id)
        query = query.join(LifePlanOccurrence, MerchantOrder.occurrence_id == LifePlanOccurrence.id).where(
            LifePlanOccurrence.plan_id == plan.id)
    return {"schema_version": "virtual-merchant-orders-v1", "orders": [order_view(row) for row in session.scalars(query)],
        "bank_state_changed": False, "source": "VIRTUAL_MERCHANT_ORDER_LEDGER"}


def fulfil_virtual_order(session, principal, order_id, *, now=None):
    from .life_plan_service import execution_context
    require_profile_actor(session, principal, lock=True)
    now = as_utc(now or utcnow())
    row = session.scalar(select(MerchantOrder).where(MerchantOrder.id == order_id,
        MerchantOrder.user_id == principal.user_id, MerchantOrder.sandbox_id == principal.sandbox_id))
    if row is None:
        raise DomainError("MERCHANT_ORDER_NOT_FOUND", "未找到本人的订单。", 404)
    if row.occurrence_id:
        context = execution_context(session, principal, row.occurrence_id, now=now, for_order=False, payment_required=False)
        _verified_life_order(row, context)
    else:
        from .merchant_runtime import verified_single_order_source
        verified_single_order_source(session, principal, row)
    payment = verified_order_payment(session, principal, row)
    if payment.status != "POSTED":
        raise DomainError("MERCHANT_PAYMENT_ALREADY_REFUNDED", "原付款已退款，不能继续自动履约。", 409)
    row = session.scalar(select(MerchantOrder).where(MerchantOrder.id == row.id).with_for_update().execution_options(populate_existing=True))
    if row.status == "FULFILLED":
        return order_view(row)
    if row.status != "PAID" or not as_utc(row.delivery_start) <= now <= as_utc(row.delivery_end):
        raise DomainError("MERCHANT_FULFILMENT_WINDOW_INVALID", "尚未到履约时间或订单当前不可履约。", 409)
    row.status, row.fulfilled_at, row.version = "FULFILLED", now, row.version + 1
    row.status_source = "VIRTUAL_MERCHANT_FULFILMENT_LEDGER"
    return order_view(row)


def request_order_refund(session, principal, order_id, *, now=None):
    """Only the original bounded cancellation policy may refund a paid order."""
    from .life_plan_service import refund_context
    require_profile_actor(session, principal, lock=True)
    now = as_utc(now or utcnow())
    row = session.scalar(select(MerchantOrder).where(MerchantOrder.id == order_id,
        MerchantOrder.user_id == principal.user_id, MerchantOrder.sandbox_id == principal.sandbox_id))
    if row is None:
        raise DomainError("MERCHANT_ORDER_NOT_FOUND", "未找到本人的订单。", 404)
    if row.occurrence_id:
        context = refund_context(session, principal, row.occurrence_id, now=now,
            audit_completed=row.status == "REFUNDED")
        _verified_life_order(row, context)
    else:
        from .merchant_runtime import verified_single_order_source
        verified_single_order_source(session, principal, row)
    verified_order_payment(session, principal, row)
    row = session.scalar(select(MerchantOrder).where(MerchantOrder.id == row.id).with_for_update().execution_options(populate_existing=True))
    previous = session.scalar(select(MerchantRefund).where(MerchantRefund.order_id == row.id))
    if previous:
        return previous
    if row.status != "PAID" or now >= as_utc(row.delivery_start) or row.product_snapshot_json.get("refund_policy") != "BEFORE_FULFILMENT_ONLY":
        raise DomainError("MERCHANT_REFUND_NOT_ALLOWED", "订单已履约或超过可退窗口，不能伪造退款成功。", 409)
    refund = MerchantRefund(sandbox_id=principal.sandbox_id, user_id=principal.user_id, order_id=row.id,
        amount_minor=row.amount_minor, operation_key=f"life-refund:{row.id}")
    session.add(refund); row.status = "REFUND_PENDING"; row.version += 1
    session.flush()
    return refund


def complete_order_refund(session, principal, refund_id, *, now=None):
    from .life_plan_service import refund_context
    from .payment_instruments import refund_merchant_payment
    require_profile_actor(session, principal, lock=True)
    now = as_utc(now or utcnow())
    refund = session.scalar(select(MerchantRefund).where(MerchantRefund.id == refund_id,
        MerchantRefund.user_id == principal.user_id, MerchantRefund.sandbox_id == principal.sandbox_id))
    if refund is None:
        raise DomainError("MERCHANT_REFUND_NOT_FOUND", "未找到本人的退款。", 404)
    row = session.get(MerchantOrder, refund.order_id)
    if row.occurrence_id:
        context = refund_context(session, principal, row.occurrence_id, now=now,
            audit_completed=refund.status == "POSTED")
        _verified_life_order(row, context)
        authorization_ref = context["mandate"].id
    else:
        from .merchant_runtime import verified_single_order_source
        verified_single_order_source(session, principal, row)
        authorization_ref = "confirmed-task:" + row.source_task_id
    verified_order_payment(session, principal, row)
    refund = session.scalar(select(MerchantRefund).where(MerchantRefund.id == refund.id).with_for_update().execution_options(populate_existing=True))
    row = session.scalar(select(MerchantOrder).where(MerchantOrder.id == row.id).with_for_update().execution_options(populate_existing=True))
    if refund.status == "POSTED":
        return deepcopy(refund.receipt_json)
    if refund.status != "REQUESTED" or row.status != "REFUND_PENDING" or row.amount_minor != refund.amount_minor:
        raise DomainError("MERCHANT_REFUND_STATE_CHANGED", "退款状态已变化，需查单核实。", 409)
    payment_id = row.payment_receipt_json.get("payment_id")
    if not payment_id:
        raise DomainError("MERCHANT_PAYMENT_NOT_VERIFIED", "原付款回执缺失，不能伪造退款。", 409)
    receipt = refund_merchant_payment(session, principal, payment_id=payment_id,
        operation_key=refund.operation_key, authorization_ref=authorization_ref)
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != "payment-refund-receipt-v1"
            or receipt.get("status") != "POSTED" or receipt.get("payment_id") != payment_id
            or receipt.get("amount_minor") != row.amount_minor or receipt.get("currency") != row.currency
            or receipt.get("account_id") != row.payment_receipt_json.get("account_id")
            or receipt.get("instrument_id") != row.payment_receipt_json.get("instrument_id")
            or receipt.get("ledger_balanced") is not True):
        raise DomainError("MERCHANT_REFUND_NOT_VERIFIED", "退款尚未取得入账回执。", 409)
    if row.payment_receipt_json.get("instrument_type") == "DEBIT":
        correct = receipt.get("cash_credit_minor") == row.amount_minor and receipt.get("credit_reduction_minor") == 0
    else:
        correct = (receipt.get("cash_credit_minor") == 0 and type(receipt.get("credit_reduction_minor")) is int
            and type(receipt.get("credit_balance_added_minor")) is int
            and receipt["credit_reduction_minor"] + receipt["credit_balance_added_minor"] == row.amount_minor)
    if not correct:
        raise DomainError("MERCHANT_REFUND_NOT_VERIFIED", "退款金额或原路入账口径不一致。", 409)
    refund.status, refund.completed_at, refund.receipt_json = "POSTED", now, deepcopy(receipt)
    row.status, row.version = "REFUNDED", row.version + 1
    product = session.scalar(select(MerchantProduct).where(MerchantProduct.id == row.product_id)
        .with_for_update().execution_options(populate_existing=True))
    if product is None:
        raise DomainError("MERCHANT_PRODUCT_SOURCE_CHANGED", "原商品库存记录缺失，退款与库存需一并核对。", 409)
    product.stock += row.quantity; product.version += 1
    return deepcopy(receipt)
