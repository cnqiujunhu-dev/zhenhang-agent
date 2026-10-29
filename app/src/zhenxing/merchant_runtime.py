"""Ordinary single merchant purchase, distinct from a birthday cash mandate."""
from copy import deepcopy
from datetime import datetime, timedelta
import unicodedata

from sqlalchemy import select

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .merchant_models import MerchantOrder, MerchantProduct, VirtualMerchant
from .merchant_service import merchant_catalog, order_view, orders_view, quote_products, verified_order_payment
from .models import Confirmation, OutboxMessage, Task, TaskNode, ToolCall, User, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .runtime_models import AuthChallenge
from .workflow import finish_node, transition_task


ACTION_TOOLS = {"MERCHANT_CATALOG": "merchant.catalog.v1", "MERCHANT_ORDERS": "merchant.orders.v1",
    "MERCHANT_PURCHASE": "merchant.purchase.v1"}
TOOL_DEFINITIONS = {tool: {"read_only": kind != "MERCHANT_PURCHASE",
    "risk_level": "L3" if kind == "MERCHANT_PURCHASE" else "L1", "owner": "virtual_merchants"}
    for kind, tool in ACTION_TOOLS.items()}


def _name(value):
    return unicodedata.normalize("NFKC", value).strip().casefold() if isinstance(value, str) else None


def resolve_merchant_product(session, principal, *, product_name=None, merchant_name=None, product_id=None):
    require_profile_actor(session, principal)
    products = merchant_catalog(session, principal)["products"]
    if product_id:
        products = [row for row in products if row["product_id"] == product_id]
    if product_name is not None:
        products = [row for row in products if _name(row["product_name"]) == _name(product_name)]
    if merchant_name is not None:
        products = [row for row in products if _name(row["merchant_name"]) == _name(merchant_name)]
    if len(products) != 1:
        raise DomainError("MERCHANT_PRODUCT_SELECTION_REQUIRED", "请从已登记目录中明确选择一个商户商品。", 422)
    return products[0]


def _time(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None: raise ValueError()
        return as_utc(parsed)
    except (ValueError, TypeError):
        raise DomainError("MERCHANT_DELIVERY_TIME_INVALID", "配送时间需要明确时区。", 422) from None


def inherited_order_collection(session, principal, root_id):
    """Carry the sibling's typed ALL-life-plans scope into its order read.

    This does not turn a missing specific plan into all customer orders. Only
    one active unfiltered LIFE_PLAN_VIEW in the same owned plan version qualifies.
    """
    from .runtime_models import RuntimeOperation, RuntimeRecord
    root=session.get(Task,root_id);record=session.get(RuntimeRecord,root_id)
    if root is None or (root.user_id,root.sandbox_id)!=(principal.user_id,principal.sandbox_id) or record is None:
        raise DomainError('MERCHANT_ORDER_PLAN_SCOPE_REQUIRED','请先明确是哪份安排，再查看它关联的订单。',422)
    related=session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==root_id,
        RuntimeOperation.sandbox_id==principal.sandbox_id,RuntimeOperation.active==1,
        RuntimeOperation.plan_version==record.plan_version,RuntimeOperation.action_kind=='LIFE_PLAN_VIEW')).all()
    if not related:return None
    slots=(related[0].args_json or {}).get('slots',{})
    if len(related)==1 and not slots.get('life_plan_id') and not slots.get('life_plan_name'):
        return 'LIFE_PLANS'
    raise DomainError('MERCHANT_ORDER_PLAN_SCOPE_REQUIRED','请先明确是哪份安排，再查看它关联的订单。',422)


def _prepare_merchant_task(session, task, action, context=None):
    from .payment_instruments import resolve_source_instrument
    from .services import Principal
    principal = Principal(task.user_id, task.sandbox_id)
    require_profile_actor(session, principal)
    action = action.model_dump() if hasattr(action, "model_dump") else action
    kind, slots = action.get("kind"), action.get("slots") or {}
    slots = slots.model_dump(exclude_none=True) if hasattr(slots, "model_dump") else slots
    slots = {key: value for key, value in slots.items() if value is not None}
    if kind not in ACTION_TOOLS:
        raise DomainError("MERCHANT_ACTION_NOT_ALLOWED", "未登记的商户操作。", 422)
    if 'merchant_categories' in slots and kind != 'MERCHANT_CATALOG':
        raise DomainError("MERCHANT_CATEGORY_READ_ONLY", "商品类别集合只用于目录查询，不能作为购买商品。", 422)
    read, args, target = kind != "MERCHANT_PURCHASE", {}, None
    if kind == "MERCHANT_CATALOG":
        from .merchant_catalog_selection import catalog_query
        query=catalog_query({key:slots[key] for key in ('merchant_product_id','product_name','merchant_categories') if key in slots})
        result = merchant_catalog(session, principal, **query)
    elif kind == "MERCHANT_ORDERS":
        from .life_plan_dialogue import resolve_life_plan
        plan_id = None
        if slots.get('life_plan_id') is not None or slots.get('life_plan_name') is not None:
            plan_id = resolve_life_plan(session, principal, life_plan_id=slots.get('life_plan_id'),
                life_plan_name=slots.get('life_plan_name'), include_terminal=True).id
        elif slots.get('merchant_order_scope') is None and (context or {}).get('source_root_task_id'):
            inherited=inherited_order_collection(session,principal,context['source_root_task_id'])
            if inherited:slots={**slots,'merchant_order_scope':inherited}
        query = {'plan_id': plan_id} if plan_id is not None else {}
        if slots.get('merchant_order_scope') is not None:query['order_scope']=slots['merchant_order_scope']
        result = orders_view(session, principal, **query)
    else:
        product = resolve_merchant_product(session, principal, product_name=slots.get("product_name"),
            merchant_name=slots.get("merchant"), product_id=slots.get("merchant_product_id"))
        quantity = slots.get("quantity")
        if quantity is None:
            quantity = 1
        quote = quote_products(session, principal, [{"merchant_id": product["merchant_id"],
            "product_id": product["product_id"], "quantity": quantity}])[0]
        if slots.get("currency") is not None and slots["currency"] != quote["currency"]:
            raise DomainError("MERCHANT_CURRENCY_MISMATCH", "商品以登记币种计价，不能静默改成其他币种付款。", 422)
        instrument = resolve_source_instrument(session, principal,
            slots.get("card_reference") or slots.get("card_number") or slots.get("card_last4"), quote["currency"], purpose="MERCHANT")
        now = as_utc(utcnow())
        from .delivery_window_fields import parse_delivery_window
        if "delivery_start" not in slots and "delivery_end" not in slots:
            start, end = now + timedelta(minutes=10), now + timedelta(hours=2)
        else:
            from .temporal_fields import temporal_field_clock
            clocks={field:temporal_field_clock(session,task,context,field,slots[field]) or now
                for field in ("delivery_start","delivery_end") if field in slots}
            start, end = parse_delivery_window(slots.get("delivery_start"), slots.get("delivery_end"), now,
                start_clock=clocks.get("delivery_start"),end_clock=clocks.get("delivery_end"))
        if end <= start or start <= now or end - start > timedelta(hours=24):
            raise DomainError("MERCHANT_DELIVERY_TIME_INVALID", "请核对未来履约时间范围。", 422)
        if quote["merchant_category"] == "RESTAURANT":
            user = session.get(User, principal.user_id)
            recipient = {"name": slots.get("recipient_name") or user.display_name,
                "phone": slots.get("recipient_phone"), "address": quote["merchant_name"] + "店内服务"}
            mode = "ON_SITE"
        else:
            from .life_plan_service import _recipient
            recipient = _recipient({key: slots.get("recipient_" + key) for key in ("name", "phone", "address")})
            mode = "DELIVERY"
        args = {"quote": quote, "instrument": instrument, "recipient": recipient, "fulfilment_mode": mode,
            "delivery_start": start.isoformat(), "delivery_end": end.isoformat(),
            "external_merchant_connected": False}
        target = product["product_id"]
        result = {"preview": deepcopy(args), "message": "确认后支付所列商品总价。付款后等待商户履约；取消与退款按订单条款处理。",
            "bank_state_changed": False}
    if read:
        from .life_plan_facts import finish_life_read
        result = finish_life_read(session, principal, kind, result, query)
    tool, key = ACTION_TOOLS[kind], kind.lower()
    plan_node = {"node_id": key, "tool_name": tool, "action_kind": kind, "args": args,
        "read_only": read, "requires_confirmation": not read, "status": "SUCCEEDED" if read else "WAITING_CONFIRMATION"}
    task.plan_json = {"task_id": task.id, "sandbox_id": task.sandbox_id, "policy_version": "virtual-merchant-v1", "nodes": [plan_node]}
    task.plan_hash, task.result_json = canonical_hash(task.plan_json), result
    task.fic_json = {**task.fic_json, "requested_capabilities": [tool], "status": "READY"}
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=key, decision="ALLOW" if read else "REQUIRE_CONFIRMATION",
        reason_codes=["EXACT_OWNED_INSTRUMENT", "REGISTERED_VIRTUAL_MERCHANT"], policy_version="virtual-merchant-v1")
    if not read:
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id, node_key=key,
            action_type=tool, target_id=target, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "action": kind, "args": args}),
            expires_at=utcnow() + timedelta(minutes=5)))
    transition_task(task, "SUCCEEDED" if read else "AWAITING_CONFIRMATION")
    return task


def prepare_merchant_task(session, task, action, context=None):
    try:
        return _prepare_merchant_task(session, task, action, context)
    except DomainError as error:
        if error.status_code != 422:
            raise
        task.result_json = {"error": error.code, "message": error.message, "bank_state_changed": False}
        task.fic_json = {**task.fic_json, "status": "NEED_CLARIFICATION"}
        return task


def _confirmation(session, task, principal, confirmation, args, auth_challenge_id):
    if ((task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or confirmation is None or confirmation.status != "PENDING" or confirmation.task_id != task.id
            or confirmation.user_id != principal.user_id or confirmation.sandbox_id != principal.sandbox_id
            or confirmation.action_type != ACTION_TOOLS["MERCHANT_PURCHASE"]
            or confirmation.target_id != args["quote"]["product_id"] or canonical_hash(task.plan_json) != task.plan_hash
            or confirmation.plan_hash != task.plan_hash or as_utc(confirmation.expires_at) <= utcnow()
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": "MERCHANT_PURCHASE", "args": args})):
        raise DomainError("MERCHANT_CONFIRMATION_INVALID", "商户订单确认已变化或过期。", 409)
    challenge = session.get(AuthChallenge, auth_challenge_id) if auth_challenge_id else None
    if (challenge is None or challenge.status not in {"VERIFIED", "CONSUMED"} or challenge.confirmation_id != confirmation.id
            or challenge.task_id != task.id or challenge.user_id != principal.user_id or challenge.sandbox_id != principal.sandbox_id
            or challenge.plan_hash != task.plan_hash or challenge.node_key != confirmation.node_key or challenge.verified_at is None
            or as_utc(challenge.expires_at) <= utcnow() or (challenge.status == "CONSUMED" and challenge.consumed_at is None)):
        raise DomainError("STEP_UP_REQUIRED", "请完成本次商户付款的二次核验。", 403)
    return challenge


def commit_merchant_task(session, task, principal, confirmation, operation_key, *, request_hash=None,
        node=None, auth_challenge_id=None):
    from .payment_instruments import post_merchant_payment, validate_instrument_snapshot
    require_profile_actor(session, principal)
    rows = task.plan_json.get("nodes", [])
    row = rows[0] if len(rows) == 1 else {}
    if row.get("action_kind") != "MERCHANT_PURCHASE":
        raise DomainError("MERCHANT_ACTION_NOT_ALLOWED", "当前任务不是已确认的单笔商户订单。", 409)
    if confirmation is None or confirmation.node_key != row.get("node_id") or row.get("tool_name") != ACTION_TOOLS["MERCHANT_PURCHASE"]:
        raise DomainError("MERCHANT_CONFIRMATION_INVALID", "本次确认不属于当前订单节点。", 409)
    args = row["args"]
    challenge = _confirmation(session, task, principal, confirmation, args, auth_challenge_id)
    node = node or session.scalar(select(TaskNode).where(TaskNode.task_id == task.id, TaskNode.node_key == row["node_id"]))
    if node is None or node.status != "WAITING_CONFIRMATION" or node.tool_name != ACTION_TOOLS["MERCHANT_PURCHASE"]:
        raise DomainError("MERCHANT_CONFIRMATION_INVALID", "商户付款节点已变化。", 409)
    # Instrument facade owns account/facility -> card/binding lock order.
    instrument = validate_instrument_snapshot(session, principal, args["instrument"], lock=True, purpose="MERCHANT")
    quoted = args["quote"]
    fresh = quote_products(session, principal, [{key: quoted[key] for key in ("merchant_id", "product_id", "quantity")}], lock=True)[0]
    if fresh != quoted:
        raise DomainError("MERCHANT_QUOTE_CHANGED", "商品报价、库存依据或条款变化，请重新核对订单。", 409)
    if as_utc(utcnow()) >= _time(args["delivery_start"]):
        raise DomainError("MERCHANT_DELIVERY_TIME_INVALID", "确认已超过所选履约时间，请重新核对。", 409)
    order = MerchantOrder(sandbox_id=principal.sandbox_id, user_id=principal.user_id, source_task_id=task.id,
        merchant_id=quoted["merchant_id"], product_id=quoted["product_id"], occurrence_id=None,
        quantity=quoted["quantity"], amount_minor=quoted["amount_minor"], currency=quoted["currency"],
        recipient_json=deepcopy(args["recipient"]), delivery_start=_time(args["delivery_start"]),
        delivery_end=_time(args["delivery_end"]), payment_receipt_json={}, operation_key=operation_key,
        request_hash=canonical_hash(args), product_snapshot_json=deepcopy(quoted))
    session.add(order); session.flush()
    receipt = post_merchant_payment(session, principal, instrument_snapshot=instrument,
        amount_minor=order.amount_minor, merchant_ref=order.merchant_id, operation_key=operation_key,
        authorization_ref="confirmed-task:" + task.id, channel="MERCHANT")
    if (receipt.get("schema_version") != "payment-receipt-v1" or receipt.get("status") != "POSTED"
            or receipt.get("instrument_id") != instrument["instrument_id"] or receipt.get("amount_minor") != order.amount_minor
            or receipt.get("currency") != order.currency or receipt.get("ledger_balanced") is not True):
        raise DomainError("MERCHANT_PAYMENT_NOT_VERIFIED", "未取得匹配的支付入账回执。", 409)
    product = session.get(MerchantProduct, order.product_id)
    product.stock -= order.quantity; product.version += 1
    order.payment_receipt_json = deepcopy(receipt)
    verified_order_payment(session, principal, order)
    challenge.status, challenge.consumed_at = "CONSUMED", utcnow()
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    result = {"schema_version": "virtual-merchant-purchase-v1", "orders": [order_view(order)],
        "payment": receipt, "bank_state_changed": True, "message": "这笔订单已付款，尚待商户履约。"}
    task.result_json = result; finish_node(session, task, node, result)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=ACTION_TOOLS["MERCHANT_PURCHASE"],
        idempotency_key=operation_key, request_hash=request_hash or canonical_hash({"plan_hash": task.plan_hash, "operation_key": operation_key}),
        status="SUCCESS", result_json=result))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="VIRTUAL_MERCHANT_PAYMENT_POSTED",
        payload={"order_id": order.id, "payment_id": receipt["payment_id"], "operation_key": operation_key})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED", payload_json={"task_id": task.id}))
    return task


def verified_single_order_source(session, principal, order):
    """Original real task/confirmation/payment chain, not arbitrary order JSON."""
    task = session.get(Task, order.source_task_id) if order.source_task_id else None
    confirmations = session.scalars(select(Confirmation).where(Confirmation.task_id == order.source_task_id,
        Confirmation.action_type == ACTION_TOOLS["MERCHANT_PURCHASE"])).all() if task else []
    calls = session.scalars(select(ToolCall).where(ToolCall.task_id == order.source_task_id,
        ToolCall.tool_name == ACTION_TOOLS["MERCHANT_PURCHASE"], ToolCall.status == "SUCCESS")).all() if task else []
    if (task is None or (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or task.state != "SUCCEEDED" or canonical_hash(task.plan_json) != task.plan_hash
            or len(confirmations) != 1 or confirmations[0].status != "CONSUMED"
            or confirmations[0].plan_hash != task.plan_hash or len(calls) != 1
            or calls[0].result_json.get("payment") != order.payment_receipt_json):
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单原始确认与支付来源未通过核验。", 409)
    nodes = task.plan_json.get("nodes", [])
    row = nodes[0] if len(nodes) == 1 else {}
    confirmation = confirmations[0]
    if (row.get("action_kind") != "MERCHANT_PURCHASE" or row.get("tool_name") != ACTION_TOOLS["MERCHANT_PURCHASE"]
            or confirmation.target_id != order.product_id or confirmation.node_key != row.get("node_id")
            or confirmation.user_id != principal.user_id or confirmation.sandbox_id != principal.sandbox_id
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": "MERCHANT_PURCHASE", "args": row.get("args")})
            or canonical_hash(row.get("args")) != order.request_hash):
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单原始范围与确认不一致。", 409)
    if row["args"].get("quote") != order.product_snapshot_json:
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单商品快照与原确认不一致。", 409)
    challenges = session.scalars(select(AuthChallenge).where(AuthChallenge.confirmation_id == confirmation.id,
        AuthChallenge.task_id == task.id, AuthChallenge.user_id == principal.user_id,
        AuthChallenge.sandbox_id == principal.sandbox_id, AuthChallenge.status == "CONSUMED")).all()
    if (len(challenges) != 1 or challenges[0].plan_hash != task.plan_hash
            or challenges[0].node_key != confirmation.node_key or challenges[0].verified_at is None
            or challenges[0].consumed_at is None):
        raise DomainError("MERCHANT_ORDER_SOURCE_INVALID", "订单原始二次核验链不完整。", 409)
    verified_order_payment(session, principal, order)
    return task


def run_due_merchant_orders(session, *, now=None):
    """One normal virtual order per transaction; birthday orders use life jobs."""
    now = as_utc(now or utcnow())
    hints = session.scalars(select(MerchantOrder).where(MerchantOrder.occurrence_id.is_(None),
        MerchantOrder.source_task_id.is_not(None), MerchantOrder.status == "PAID",
        MerchantOrder.delivery_start <= now).order_by(MerchantOrder.delivery_start, MerchantOrder.id)
        .limit(50)).all()
    for hint in hints:
        from .services import Principal
        from .banking_locks import lock_financial_users
        principal = Principal(hint.user_id, hint.sandbox_id)
        lock_financial_users(session, {(hint.sandbox_id, hint.user_id)})
        order = session.scalar(select(MerchantOrder).where(MerchantOrder.id == hint.id, MerchantOrder.status == "PAID")
            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if order is None:
            continue
        require_profile_actor(session, principal)
        try:
            verified_single_order_source(session, principal, order)
            if verified_order_payment(session, principal, order).status != "POSTED":
                raise DomainError("MERCHANT_PAYMENT_ALREADY_REFUNDED", "原付款已退款，不能自动履约。", 409)
        except DomainError:
            order.status, order.status_source = "SOURCE_UNVERIFIED", "VIRTUAL_MERCHANT_SOURCE_UNAVAILABLE"
            order.version += 1
            return 1
        if now > as_utc(order.delivery_end):
            order.status, order.status_source = "FULFILMENT_UNVERIFIED", "VIRTUAL_MERCHANT_WINDOW_EXPIRED"
        else:
            order.status, order.fulfilled_at = "FULFILLED", now
            order.status_source = "VIRTUAL_MERCHANT_FULFILMENT_LEDGER"
        order.version += 1
        return 1
    return 0
