"""Confirmed, transactional subscription commerce backed by versioned terms.

Catalog terms and paid rights are bank facts. Chat is not a payment grant.
No scheduler, external merchant call, or hidden model inference lives here.
"""
from calendar import monthrange
from datetime import timedelta

from sqlalchemy import select, update

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError, FinancialIntentContract
from .models import (Account, Confirmation, EvidenceSnapshot, OutboxMessage, Subscription, SubscriptionLedgerEntry,
    SubscriptionPayment, SubscriptionProduct, Task, ToolCall, Transaction, utcnow)
from .planning import canonical_hash
from .platform import add_task_nodes, append_message, record_policy
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from .workflow import finish_node, transition_task
from .subscription_catalog_data import CATALOG_DEFAULTS, TERMS_VERSION, MERCHANT_ALIASES


CONNECTED_SOURCES = {"BANK_MANDATE", "MERCHANT_SIM"}


def resolve_subscription_merchant(merchant):
    if not isinstance(merchant, str):
        return merchant
    stripped = merchant.strip()
    return MERCHANT_ALIASES.get(stripped.casefold(), stripped)


def ensure_subscription_catalog(session):
    """Seed missing registered products only; never reset existing terms."""
    for merchant, price, source in CATALOG_DEFAULTS:
        if session.scalar(select(SubscriptionProduct.id).where(SubscriptionProduct.merchant == merchant)) is None:
            session.add(SubscriptionProduct(merchant=merchant, monthly_price_minor=price,
                source_type=source, currency="CNY", status="AVAILABLE", version=1))
    session.flush()


def add_calendar_months(value, months):
    value = as_utc(value)
    serial = value.year * 12 + value.month - 1 + months
    year, month = divmod(serial, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def subscription_entitlement(item, *, now=None):
    now = as_utc(now or utcnow())
    end = item.entitlement_end_at
    return {"auto_renew": bool(item.auto_renew), "billing_months": item.billing_months,
        "entitlement_end_at": as_utc(end).isoformat() if end else None,
        "entitled": bool(end and as_utc(end) > now),
        "last_paid_at": as_utc(item.last_paid_at).isoformat() if item.last_paid_at else None,
        "last_payment_id": item.last_payment_id}


def subscription_catalog_rows(session):
    """Read-only public registered terms; not a payment or activation proof."""
    return [{"merchant": row.merchant, "monthly_price_minor": row.monthly_price_minor,
        "currency": row.currency, "version": row.version, "status": row.status,
        "can_start_here": row.status == "AVAILABLE" and row.source_type in CONNECTED_SOURCES,
        "supported_months": {"min": 1, "max": 12}, "default_months": 1, "default_auto_renew": False}
        for row in session.scalars(select(SubscriptionProduct).order_by(SubscriptionProduct.merchant))]


def _subscriptions(session, task, merchant):
    return list(session.scalars(select(Subscription).where(Subscription.sandbox_id == task.sandbox_id,
        Subscription.user_id == task.user_id, Subscription.status != ARCHIVED_RESOURCE_STATUS,
        Subscription.merchant == merchant)))


def _readonly(task, *, message, **result):
    task.result_json = {"message": message, **result}
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.status = "READY"; fic.requested_capabilities = ["subscription.list.v1"]
    task.fic_json = fic.model_dump()
    transition_task(task, "SUCCEEDED")
    return task


def _subscription_status_facts(subscription):
    return {"subscription_id": subscription.id, "merchant": subscription.merchant,
        "version": subscription.version, "status": subscription.status, "source_type": subscription.source_type,
        "amount_minor": subscription.amount_minor, **subscription_entitlement(subscription)}


def record_subscription_no_effect(session, task, subscription, kind):
    """Read status evidence, never a synthetic successful banking tool call."""
    facts = _subscription_status_facts(subscription)
    snapshot = EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id,
        source_kind="SUBSCRIPTION_NO_EFFECT_STATUS", source_version=f"subscription:{subscription.version}",
        payload_json={"no_effect_kind": kind, "task_message_hash": canonical_hash({"message":task.message}), **facts})
    session.add(snapshot); session.flush()
    task.plan_json = {"nodes": [{"node_id": "read-subscription-status", "tool_name": "subscription.list.v1",
        "read_only": True, "status": "SUCCEEDED", "evidence_id": snapshot.id}]}
    task.plan_hash = canonical_hash(task.plan_json)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    return {"no_effect_kind": kind, "no_effect_evidence_id": snapshot.id, "charged_minor": 0,
        "subscription_id": subscription.id, "subscription_version": subscription.version}


def _guidance_facts(session, task, merchant):
    product=session.scalar(select(SubscriptionProduct).where(SubscriptionProduct.merchant==merchant))
    rows=_subscriptions(session,task,merchant)
    return {"merchant":merchant,"product":{key:getattr(product,key) for key in (
        "id","merchant","monthly_price_minor","currency","source_type","status","version")} if product else None,
        "subscriptions":[_subscription_status_facts(row) for row in rows],
        "task_message_hash":canonical_hash({"message":task.message})}


def _guidance_only(facts):
    product=facts.get("product")
    return product is None or product["status"]!="AVAILABLE" or product["source_type"] not in CONNECTED_SOURCES \
        or any(row["source_type"] not in CONNECTED_SOURCES for row in facts["subscriptions"])


def record_unavailable_subscription_guidance(session,task,merchant):
    facts=_guidance_facts(session,task,merchant)
    if not _guidance_only(facts):
        raise DomainError("SUBSCRIPTION_FACTS_CHANGED","该服务的可用方案已变化，请重新核对。",409)
    evidence=EvidenceSnapshot(task_id=task.id,sandbox_id=task.sandbox_id,
        source_kind="SUBSCRIPTION_CATALOG_GUIDANCE",source_version="registered-catalog-v1",payload_json=facts)
    session.add(evidence);session.flush()
    task.plan_json={"nodes":[{"node_id":"read-subscription-catalog","tool_name":"subscription.list.v1",
        "read_only":True,"status":"SUCCEEDED","evidence_id":evidence.id}]}
    task.plan_hash=canonical_hash(task.plan_json)
    add_task_nodes(session,task_id=task.id,sandbox_id=task.sandbox_id,plan=task.plan_json)
    return {"no_effect_kind":"REGISTERED_UNAVAILABLE_GUIDANCE","no_effect_evidence_id":evidence.id}


def verified_subscription_no_effect(session, task):
    """Fresh statement that no write happened; not a paid service receipt."""
    try:
        from .permissions import require_banking_actor
        from .services import Principal
        require_banking_actor(session, Principal(task.user_id, task.sandbox_id))
        result = task.result_json or {}
        kind = result.get("no_effect_kind")
        if task.state != "SUCCEEDED" or kind not in {"START_ALREADY_ACTIVE", "START_PAID_RIGHTS_ACTIVE", "NO_AUTO_RENEW", "ALREADY_CANCELLED","REGISTERED_UNAVAILABLE_GUIDANCE"} \
                or result.get("charged_minor") != 0 or result.get("payment_id") \
                or canonical_hash(task.plan_json) != task.plan_hash:
            return False
        evidence = session.get(EvidenceSnapshot, result.get("no_effect_evidence_id"))
        if kind=="REGISTERED_UNAVAILABLE_GUIDANCE":
            merchant=result.get("merchant")
            node=next((row for row in task.plan_json.get("nodes",[]) if row.get("evidence_id")==result.get("no_effect_evidence_id")),None)
            if not isinstance(merchant,str) or not evidence or (evidence.task_id,evidence.sandbox_id)!=(task.id,task.sandbox_id) \
                    or evidence.source_kind!="SUBSCRIPTION_CATALOG_GUIDANCE" or evidence.source_version!="registered-catalog-v1" \
                    or evidence.payload_json!=_guidance_facts(session,task,merchant) or not _guidance_only(evidence.payload_json) \
                    or not node or node.get("tool_name")!="subscription.list.v1" or node.get("read_only") is not True \
                    or node.get("status")!="SUCCEEDED" or result.get("can_start_here") is not False or result.get("can_renew_here") is not False:
                return False
            if session.scalar(select(SubscriptionPayment.id).where(SubscriptionPayment.task_id==task.id)) \
                    or session.scalar(select(Confirmation.id).where(Confirmation.task_id==task.id,
                        (Confirmation.consumed_at.is_not(None)) | (Confirmation.status.in_(["PENDING","CONSUMED"])))):
                return False
            return True
        subscription = session.scalar(select(Subscription).where(Subscription.id == result.get("subscription_id"),
            Subscription.sandbox_id == task.sandbox_id, Subscription.user_id == task.user_id))
        node = next((row for row in task.plan_json.get("nodes", []) if row.get("evidence_id") == result.get("no_effect_evidence_id")), None)
        if not subscription or not evidence or (evidence.task_id, evidence.sandbox_id) != (task.id, task.sandbox_id) \
                or evidence.source_kind != "SUBSCRIPTION_NO_EFFECT_STATUS" \
                or evidence.source_version != f"subscription:{subscription.version}" \
                or evidence.payload_json != {"no_effect_kind": kind,
                    "task_message_hash":canonical_hash({"message":task.message}), **_subscription_status_facts(subscription)} \
                or result.get("subscription_version") != subscription.version \
                or not node or node.get("tool_name") != "subscription.list.v1" or node.get("read_only") is not True \
                or node.get("status") != "SUCCEEDED":
            return False
        if kind == "START_ALREADY_ACTIVE" and (subscription.status != "ACTIVE" or result.get("already_active") is not True \
                or subscription.entitlement_end_at and as_utc(subscription.entitlement_end_at)<=utcnow()):
            return False
        if kind == "START_PAID_RIGHTS_ACTIVE" and (subscription.status not in {"ACTIVE", "CANCELLED"}
                or result.get("already_active") is not True or subscription.entitlement_end_at is None
                or as_utc(subscription.entitlement_end_at) <= utcnow()):
            return False
        if kind == "NO_AUTO_RENEW" and (subscription.status != "ACTIVE" or subscription.auto_renew):
            return False
        if kind == "ALREADY_CANCELLED" and subscription.status != "CANCELLED":
            return False
        if session.scalar(select(SubscriptionPayment.id).where(SubscriptionPayment.task_id == task.id)) \
                or session.scalar(select(Confirmation.id).where(Confirmation.task_id == task.id,
                    (Confirmation.consumed_at.is_not(None)) | (Confirmation.status.in_(["PENDING", "CONSUMED"])))):
            return False
        return True
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return False


def commerce_view(plan_hash, node):
    result = {"plan_hash": plan_hash, **{key: node.get(key) for key in (
        "action", "merchant", "product_id", "expected_product_version", "subscription_id",
        "expected_subscription_version", "expected_subscription_source_type", "account_id", "expected_account_version",
        "monthly_price_minor", "total_amount_minor", "currency", "months", "auto_renew",
        "effective_from", "entitlement_end_at", "previous_entitlement_end_at", "effective_policy",
        "auto_renew_setting_source", "mandate_amount_minor", "mandate_billing_months")}}
    if "effect_kind" in node:
        result["effect_kind"] = node["effect_kind"]
    if node.get("effect_kind") == "RESTORE_AUTORENEW" and "autorenew_source" in node:
        result["source_card_last4"] = node.get("source_card_last4")
    return result


def prepare_subscription_commerce_task(session, *, task, conversation_id, action, merchant,
                                      months=1, auto_renew=None, source_card_reference=None, source_message=None):
    """Build a fixed-price single-period proposal, never charge during prepare."""
    if action not in {"START", "RENEW"} or type(months) is not int or not 1 <= months <= 12 \
            or auto_renew is not None and type(auto_renew) is not bool:
        raise DomainError("SUBSCRIPTION_TERMS_INVALID", "请明确一个服务、1至12个月的周期和续费方式。", 422)
    if not isinstance(merchant, str) or not merchant.strip():
        task.result_json = {"message": "你想开通或续订哪项服务？", "needed_fields": ["merchant"]}
        return task
    merchant = resolve_subscription_merchant(merchant)
    ensure_subscription_catalog(session)
    product = session.scalar(select(SubscriptionProduct).where(SubscriptionProduct.merchant == merchant))
    rows = _subscriptions(session, task, merchant)
    if len(rows) > 1:
        task.result_json = {"message": "这项服务有多条记录，需要先核对具体记录。", "merchant": merchant}
        return task
    existing = rows[0] if rows else None
    if product is None or product.status != "AVAILABLE" or product.source_type not in CONNECTED_SOURCES \
            or existing and existing.source_type not in CONNECTED_SOURCES:
        evidence=record_unavailable_subscription_guidance(session,task,merchant)
        return _readonly(task, message=f"{merchant}暂未接入开通和续订，请到服务商核对订阅设置。",
            merchant=merchant, can_start_here=False, can_renew_here=False, charged_minor=0,**evidence)
    if product.currency != "CNY" or type(product.monthly_price_minor) is not int or product.monthly_price_minor <= 0:
        raise DomainError("SUBSCRIPTION_PRODUCT_UNAVAILABLE", "该服务当前没有可确认的人民币方案。", 409)
    if existing and action == "START" and existing.status in {"ACTIVE", "CANCELLED"} \
            and existing.entitlement_end_at is not None and as_utc(existing.entitlement_end_at) > utcnow():
        if auto_renew is True and (existing.status == "CANCELLED" or not existing.auto_renew):
            from .subscription_autorenew import prepare_autorenew_restore
            return prepare_autorenew_restore(session, task=task, conversation_id=conversation_id,
                subscription=existing, product=product, months=months, source_card_reference=source_card_reference)
        evidence = record_subscription_no_effect(session, task, existing,
            "START_ALREADY_ACTIVE" if existing.status == "ACTIVE" else "START_PAID_RIGHTS_ACTIVE")
        return _readonly(task, message=f"{merchant}的已付权益仍可使用，本次不重复收费或延长权益；自动续费设置保持不变。",
            merchant=merchant, subscription_id=existing.id, status=existing.status, already_active=True, charged_minor=0,
            **subscription_entitlement(existing), **{key:value for key,value in evidence.items() if key not in {"charged_minor","subscription_id"}})
    if existing and action == "START" and existing.status == "ACTIVE" \
            and (existing.entitlement_end_at is None or as_utc(existing.entitlement_end_at)>utcnow()):
        evidence = record_subscription_no_effect(session, task, existing, "START_ALREADY_ACTIVE")
        return _readonly(task, message=f"{merchant}已开通，这次不会重复开通或扣款。", merchant=merchant,
            subscription_id=existing.id, status=existing.status, already_active=True, charged_minor=0,
            **subscription_entitlement(existing), **{key:value for key,value in evidence.items() if key not in {"charged_minor","subscription_id"}})
    if existing and existing.status not in {"ACTIVE", "CANCELLED"}:
        raise DomainError("SUBSCRIPTION_CHANGED", "该服务当前状态需要先核对，暂不扣款。", 409)
    from .subscription_spend_constraint import forbids_current_subscription_charge
    if forbids_current_subscription_charge(source_message if source_message is not None else task.message):
        task.result_json={'error':'SUBSCRIPTION_CURRENT_CHARGE_FORBIDDEN','message':'你要求本次不扣款，当前付费方案已被拦截。请重新核对自动续费设置；没有购买新周期，也没有扣费。','charged_minor':0}
        return task
    from .services import Principal
    from .payment_instruments import resolve_source_instrument
    principal = Principal(task.user_id, task.sandbox_id)
    # This existing subscription ledger settles cash only. A credit card must
    # not be smuggled into an Account or have its limit treated as a balance.
    source = resolve_source_instrument(session, principal, source_card_reference, 'CNY', purpose='WEALTH')
    account = session.get(Account, source['account_id'])
    now = utcnow()
    old_end = as_utc(existing.entitlement_end_at) if existing and existing.entitlement_end_at else None
    effective = max(now, old_end) if old_end else now
    end = add_calendar_months(effective, months)
    total = product.monthly_price_minor * months
    setting_source = "CURRENT_USER" if auto_renew is not None else (
        "EXISTING_AUTHORIZED_SETTING" if existing and existing.status == "ACTIVE" else "ONE_TIME_DEFAULT")
    selected_auto = auto_renew if auto_renew is not None else bool(
        existing and existing.status == "ACTIVE" and existing.auto_renew)
    preserve_mandate = setting_source == "EXISTING_AUTHORIZED_SETTING" and selected_auto
    mandate_amount = existing.amount_minor if preserve_mandate else total
    mandate_months = existing.billing_months if preserve_mandate else months
    node_key = "start-subscription" if action == "START" else "renew-subscription"
    tool = "subscription.start.v1" if action == "START" else "subscription.renew.v1"
    node = {"node_id": node_key, "tool_name": tool, "read_only": False, "requires_confirmation": True,
        "action": action, "merchant": merchant, "product_id": product.id,
        "expected_product_version": product.version, "source_type": product.source_type,
        "subscription_id": existing.id if existing else None,
        "expected_subscription_version": existing.version if existing else None,
        "expected_subscription_source_type": existing.source_type if existing else None,
        "previous_status": existing.status if existing else None,
        "previous_entitlement_end_at": old_end.isoformat() if old_end else None,
        "account_id": account.id, "expected_account_version": account.version,
        "payment_instrument": source, "source_card_last4": source['display_last4'],
        "monthly_price_minor": product.monthly_price_minor, "total_amount_minor": total,
        "currency": "CNY", "months": months, "auto_renew": selected_auto,
        "auto_renew_setting_source": setting_source, "mandate_amount_minor": mandate_amount,
        "mandate_billing_months": mandate_months,
        "effective_from": effective.isoformat(), "entitlement_end_at": end.isoformat(),
        "expires_at": end.isoformat(),
        "effective_policy": "EXTEND_FROM_ENTITLEMENT_END" if old_end and old_end > now else "START_NOW"}
    plan = {"plan_version": 1, "policy_version": TERMS_VERSION, "task_id": task.id,
        "sandbox_id": task.sandbox_id, "nodes": [node]}
    task.plan_json = plan; task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=node_key,
        decision="REQUIRE_CONFIRMATION", reason_codes=["SUBSCRIPTION_PAYMENT", "FIXED_CATALOG_TERMS",
            "EXPLICIT_RECURRING_AUTHORIZATION" if selected_auto else "SINGLE_PERIOD_ONLY"], policy_version=TERMS_VERSION)
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.requested_capabilities = [tool]; fic.status = "READY"
    fic.slots = {key: {"value": node[key], "source": "AUTHORITATIVE"} for key in (
        "merchant", "total_amount_minor", "currency", "months", "auto_renew")}
    task.fic_json = fic.model_dump()
    transition_task(task, "AWAITING_CONFIRMATION")
    task.result_json = {"message": f"{merchant}本次{months}个月，共{total / 100:.2f}元。"
        + (f"沿用原自动续费设置，每{mandate_months}个月{mandate_amount / 100:.2f}元，请核对后确认。" if preserve_mandate
            else "你选择了到期自动续费，请核对后确认。" if selected_auto else "仅支付这次周期，不启用自动续费，请核对后确认。"),
        **{key: node[key] for key in ("action", "merchant", "monthly_price_minor", "total_amount_minor",
            "currency", "months", "auto_renew", "effective_from", "entitlement_end_at", "expires_at", "effective_policy",
            "auto_renew_setting_source", "mandate_amount_minor", "mandate_billing_months")},
        "charged_minor": 0}
    session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
        node_key=node_key, action_type=tool, target_id=existing.id if existing else product.id,
        plan_hash=task.plan_hash, view_hash=canonical_hash(commerce_view(task.plan_hash, node)),
        expires_at=now + timedelta(minutes=5)))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
        event_type="SUBSCRIPTION_PAYMENT_PROPOSED", payload=commerce_view(task.plan_hash, node))
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
        task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
    return task


def dispatch_subscription_payment(gateway, session, *, task, user_id, sandbox_id, request,
                                  idempotency_key, action):
    """Authorize and post cash, rights, receipt and outbox in caller transaction."""
    from datetime import datetime
    from .customer_profile_common import require_profile_actor
    from .services import Principal
    require_profile_actor(session, Principal(user_id, sandbox_id), lock=True)
    if action not in {"START", "RENEW"}:
        raise DomainError("SUBSCRIPTION_ACTION_INVALID", "订阅动作无效。", 422)
    tool = gateway.SUBSCRIPTION_START_TOOL if action == "START" else gateway.SUBSCRIPTION_RENEW_TOOL
    node_key = "start-subscription" if action == "START" else "renew-subscription"
    request_hash, task_node, confirmation, duplicate = gateway.authorize_write(session, task=task,
        sandbox_id=sandbox_id, user_id=user_id, request=request, idempotency_key=idempotency_key,
        tool_name=tool, node_key=node_key)
    if duplicate:
        return task
    if task.intent_type not in {"SUBSCRIPTION_MANAGEMENT", "CROSS_SCENARIO"}:
        raise DomainError("INTENT_NOT_ALLOWED", "当前任务没有订阅付款权限。", 403)
    node = next((row for row in task.plan_json.get("nodes", []) if row.get("node_id") == node_key), None)
    if not node or node.get("action") != action:
        raise DomainError("PLAN_NODE_MISSING", "订阅付款节点不存在。", 409)
    if node.get("effect_kind") == "RESTORE_AUTORENEW":
        from .subscription_autorenew import dispatch_autorenew_restore
        return dispatch_autorenew_restore(session, task=task, node=node, task_node=task_node, confirmation=confirmation,
            request_hash=request_hash, idempotency_key=idempotency_key, user_id=user_id, sandbox_id=sandbox_id)
    if node.get("effect_kind") is not None:
        raise DomainError("SUBSCRIPTION_TERMS_INVALID", "订阅动作类型发生变化，请重新核对。", 409)
    from .subscription_spend_constraint import forbids_current_subscription_charge
    from .runtime_models import RuntimeRecord
    link=session.get(RuntimeRecord,task.id)
    source=session.get(Task,link.root_task_id) if link and link.root_task_id else task
    if source and (source.user_id,source.sandbox_id)==(user_id,sandbox_id) and forbids_current_subscription_charge(source.message):
        raise DomainError('SUBSCRIPTION_CURRENT_CHARGE_FORBIDDEN','本次不扣款的要求仍有效，不能执行付费续订。',409)
    account = session.scalar(select(Account).where(Account.id == node["account_id"],
        Account.user_id == user_id, Account.sandbox_id == sandbox_id).with_for_update())
    product = session.scalar(select(SubscriptionProduct).where(SubscriptionProduct.id == node["product_id"]).with_for_update())
    if not account or not product or account.currency != "CNY" or product.currency != "CNY" \
            or account.version != node["expected_account_version"] or product.version != node["expected_product_version"] \
            or product.status != "AVAILABLE" or product.source_type not in CONNECTED_SOURCES \
            or product.source_type != node["source_type"] or product.merchant != node["merchant"] \
            or product.monthly_price_minor != node["monthly_price_minor"]:
        raise DomainError("SUBSCRIPTION_FACTS_CHANGED", "账户或服务方案已变化，请重新核对。", 409)
    months = node["months"]; auto_renew = node["auto_renew"]; total = node["total_amount_minor"]
    if type(months) is not int or not 1 <= months <= 12 or type(auto_renew) is not bool \
            or type(total) is not int or total <= 0 or total != months * product.monthly_price_minor \
            or node["currency"] != "CNY":
        raise DomainError("SUBSCRIPTION_TERMS_INVALID", "确认的订阅周期或金额无效。", 409)
    rows = list(session.scalars(select(Subscription).where(Subscription.sandbox_id == sandbox_id,
        Subscription.user_id == user_id, Subscription.merchant == product.merchant,
        Subscription.status != ARCHIVED_RESOURCE_STATUS).with_for_update()))
    subscription = rows[0] if len(rows) == 1 else None
    if len(rows) > 1 or (subscription.id if subscription else None) != node["subscription_id"] \
            or (subscription.version if subscription else None) != node["expected_subscription_version"] \
            or (subscription.status if subscription else None) != node["previous_status"] \
            or (subscription.source_type if subscription else None) != node["expected_subscription_source_type"] \
            or subscription and (subscription.source_type not in CONNECTED_SOURCES or subscription.status not in {"ACTIVE", "CANCELLED"}) \
            or action == "START" and subscription and (
                subscription.status == "ACTIVE" and subscription.entitlement_end_at is None
                or subscription.entitlement_end_at is not None and as_utc(subscription.entitlement_end_at)>utcnow()):
        raise DomainError("SUBSCRIPTION_CHANGED", "该服务已变化，请重新核对，避免重复开通或扣款。", 409)
    old_end = as_utc(subscription.entitlement_end_at).isoformat() if subscription and subscription.entitlement_end_at else None
    if old_end != node["previous_entitlement_end_at"]:
        raise DomainError("SUBSCRIPTION_CHANGED", "已有权益期限已变化，请重新核对。", 409)
    setting_source = node.get("auto_renew_setting_source")
    if setting_source not in {"CURRENT_USER", "EXISTING_AUTHORIZED_SETTING", "ONE_TIME_DEFAULT"} \
            or type(node.get("mandate_amount_minor")) is not int or node["mandate_amount_minor"] <= 0 \
            or type(node.get("mandate_billing_months")) is not int or not 1 <= node["mandate_billing_months"] <= 12:
        raise DomainError("SUBSCRIPTION_TERMS_INVALID", "自动续费方案无效。", 409)
    if setting_source == "EXISTING_AUTHORIZED_SETTING":
        if not subscription or subscription.status != "ACTIVE" or auto_renew != bool(subscription.auto_renew) \
                or auto_renew and (node["mandate_amount_minor"] != subscription.amount_minor \
                    or node["mandate_billing_months"] != subscription.billing_months):
            raise DomainError("SUBSCRIPTION_CHANGED", "原自动续费授权已变化，请重新核对。", 409)
    elif node["mandate_amount_minor"] != total or node["mandate_billing_months"] != months \
            or setting_source == "ONE_TIME_DEFAULT" and auto_renew:
        raise DomainError("SUBSCRIPTION_TERMS_INVALID", "本次周期不能隐式扩大自动续费授权。", 409)
    if confirmation.target_id != (subscription.id if subscription else product.id) \
            or confirmation.view_hash != canonical_hash(commerce_view(task.plan_hash, node)):
        raise DomainError("CONFIRMATION_VIEW_CHANGED", "本次付款的确认信息已变化。", 409)
    effective = as_utc(datetime.fromisoformat(node["effective_from"]))
    end = as_utc(datetime.fromisoformat(node["entitlement_end_at"]))
    if end != add_calendar_months(effective, months) or end <= utcnow() \
            or subscription and subscription.entitlement_end_at and effective < as_utc(subscription.entitlement_end_at):
        raise DomainError("SUBSCRIPTION_TERMS_INVALID", "本次权益期限无效。", 409)
    from .services import Principal
    from .payment_instruments import cash_reserved_minor, validate_instrument_snapshot, resolve_source_instrument
    principal = Principal(user_id, sandbox_id)
    source = node.get('payment_instrument')
    if source is None:
        source = resolve_source_instrument(session, principal, None, 'CNY', purpose='WEALTH')
        if source['account_id'] != account.id:
            raise DomainError('SUBSCRIPTION_SOURCE_CHANGED', '原扣款账户已变化，请重新核对。', 409)
    validate_instrument_snapshot(session, principal, source, purpose='WEALTH', amount_minor=total)
    reserved = cash_reserved_minor(session, principal, account.id)
    if account.balance_minor - reserved < total:
        raise DomainError("INSUFFICIENT_BALANCE", "人民币可用余额不足，没有扣款。", 409)
    # The CAS also serializes two independently confirmed prepares of this account.
    affected = session.execute(update(Account).where(Account.id == account.id, Account.version == account.version,
        Account.balance_minor >= total + reserved).values(balance_minor=Account.balance_minor - total,
        version=Account.version + 1).execution_options(synchronize_session=False)).rowcount
    if affected != 1:
        raise DomainError("INSUFFICIENT_BALANCE", "人民币可用余额不足或账户已变化，没有扣款。", 409)
    session.refresh(account)
    if subscription is None:
        subscription = Subscription(sandbox_id=sandbox_id, user_id=user_id, merchant=product.merchant,
            amount_minor=total, source_type=product.source_type, status="ACTIVE",
            next_due_at=end, auto_renew=auto_renew, billing_months=months)
        session.add(subscription); session.flush()
    subscription.status = "ACTIVE"; subscription.auto_renew = auto_renew
    subscription.amount_minor = node["mandate_amount_minor"]
    subscription.billing_months = node["mandate_billing_months"]; subscription.next_due_at = end
    subscription.entitlement_end_at = end; subscription.last_paid_at = utcnow(); subscription.version += 1
    payment = SubscriptionPayment(sandbox_id=sandbox_id, user_id=user_id, task_id=task.id,
        subscription_id=subscription.id, product_id=product.id, account_id=account.id, idempotency_key=idempotency_key,
        action=action, amount_minor=total, months=months, auto_renew=auto_renew, product_version=product.version,
        effective_from=effective, entitlement_end_at=end)
    session.add(payment); session.flush()
    subscription.last_payment_id = payment.id
    session.add_all([SubscriptionLedgerEntry(sandbox_id=sandbox_id, payment_id=payment.id,
        account_ref=account.id, delta_minor=-total), SubscriptionLedgerEntry(sandbox_id=sandbox_id,
        payment_id=payment.id, account_ref="subscription-merchant:" + product.id, delta_minor=total)])
    from .operational_clock import posting_now
    transaction = Transaction(sandbox_id=sandbox_id, account_id=account.id, card_id=None,
        occurred_at=posting_now(session, sandbox_id, user_id), amount_minor=-total, merchant=product.merchant,
        category="订阅服务", flag="NORMAL", source_kind="SUBSCRIPTION", operation_ref="subscription:" + payment.id)
    session.add(transaction); session.flush()
    confirmation.status = "CONSUMED"; confirmation.consumed_at = utcnow()
    task.result_json = {"message": f"{product.merchant}已{'续订' if action == 'RENEW' else '开通'}{months}个月，"
        f"本次支付{total / 100:.2f}元。" + ("沿用原自动续费设置。" if setting_source=="EXISTING_AUTHORIZED_SETTING" and auto_renew
            else "已开启自动续费。" if auto_renew else "没有开启自动续费。"),
        "action": action, "merchant": product.merchant, "subscription_id": subscription.id,
        "status": subscription.status, "version": subscription.version, "currency": "CNY",
        "monthly_price_minor": product.monthly_price_minor, "total_amount_minor": total,
        "charged_minor": total, "months": months, "auto_renew": auto_renew, "effective_from": effective.isoformat(),
        "entitlement_end_at": end.isoformat(), "expires_at": end.isoformat(), "effective_policy": node["effective_policy"],
        "payment_id": payment.id, "transaction_id": transaction.id, "entitled": True}
    task.result_json.update({key:node[key] for key in ("auto_renew_setting_source","mandate_amount_minor","mandate_billing_months")})
    finish_node(session, task, task_node, task.result_json)
    session.add(ToolCall(sandbox_id=sandbox_id, task_id=task.id, tool_name=tool,
        idempotency_key=idempotency_key, request_hash=request_hash, status="SUCCESS", result_json=task.result_json))
    append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
        event_type="SUBSCRIPTION_PAYMENT_POSTED", payload=task.result_json)
    session.add(OutboxMessage(sandbox_id=sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
        payload_json={"task_id": task.id, "payment_id": payment.id, "subscription_id": subscription.id}))
    return task
