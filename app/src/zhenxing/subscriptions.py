from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit
from .contracts import FinancialIntentContract
from .models import Confirmation, EvidenceSnapshot, Subscription, SubscriptionProduct, Task, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, append_message, record_policy
from .workflow import transition_task
from .resource_defaults import ARCHIVED_RESOURCE_STATUS


CONNECTED_SOURCES = frozenset({"BANK_MANDATE", "MERCHANT_SIM"})
SOURCE_LABELS = {"BANK_MANDATE": "银行代扣协议", "MERCHANT_SIM": "商户订阅", "EXTERNAL_UNCONNECTED": "未接入外部服务"}


def prepare_subscription_task(session: Session, *, task: Task, conversation_id: str, requested_merchant=None) -> Task:
    subscriptions = list(session.scalars(select(Subscription).where(
        Subscription.sandbox_id == task.sandbox_id,
        Subscription.user_id == task.user_id,
        Subscription.status != ARCHIVED_RESOURCE_STATUS,
    )))
    message = task.message
    wants_cancel = any(term in message for term in ("取消", "关闭", "停用", "停止", "退订", "关掉"))
    if re.search(r"(?:不要|别|先别|不想|不需要|无需|暂不|不).{0,5}(?:取消|关闭|停用|停止|退订|关掉)|如果|假如|只有|明天|后天|如何|怎么|能否|可以吗", message):
        wants_cancel = False
    fic = FinancialIntentContract.model_validate(task.fic_json)
    if not wants_cancel:
        if re.search(r"开通|恢复|重新(?:开启|订阅)|续订|续期|续费(?:一个|一|两|三|[1-9])|\b(?:subscribe|renew|activate|resume)\b", message, re.I):
            fic.status="READY";fic.requested_capabilities=["subscription.list.v1"];task.fic_json=fic.model_dump()
            transition_task(task,"SUCCEEDED")
            task.result_json={"message":"开通和付费续订需要先核对服务方案，请在对话入口提出需求后确认。",
                "subscriptions":[],"charged_minor":0,"requires_controlled_entry":True}
            return task
        # A compiler-specified merchant is an exact scoped filter. Unknown
        # names never silently turn into an unfiltered account inventory.
        selected=subscriptions
        if requested_merchant is None:
            from .subscription_intent import subscription_request
            scoped=subscription_request(message)
            if scoped and scoped.get("kind")=="SUBSCRIPTION_LIST":
                requested_merchant=(scoped.get("slots") or {}).get("merchant")
        if requested_merchant is None:
            literal_names={item.merchant for item in subscriptions if item.merchant in message}
            if len(literal_names)==1:
                requested_merchant=next(iter(literal_names))
        filter_applied=requested_merchant is not None
        if filter_applied:
            from .subscription_commerce import resolve_subscription_merchant
            selected=[item for item in subscriptions if item.merchant==resolve_subscription_merchant(requested_merchant)]
        from .subscription_timing import subscription_calendar_context, subscription_timing
        from .subscription_commerce import subscription_entitlement
        now, through = subscription_calendar_context(session, task.sandbox_id)
        products={row.merchant:row for row in session.scalars(select(SubscriptionProduct))}
        def registered(item):
            product=products.get(item.merchant)
            return bool(product and product.status=="AVAILABLE" and product.currency=="CNY"
                and product.source_type in CONNECTED_SOURCES and item.source_type in CONNECTED_SOURCES)
        rows = [{
            "merchant": item.merchant,
            "amount_minor": item.amount_minor,
            "source_type": item.source_type,
            "source_label": SOURCE_LABELS.get(item.source_type, "未登记服务"),
            "status": item.status,
            "next_due_at": item.next_due_at.isoformat(),
            "can_cancel_here": item.source_type in CONNECTED_SOURCES and item.status == "ACTIVE" and bool(item.auto_renew),
            "can_start_here": registered(item) and (item.status=="CANCELLED" or item.status=="ACTIVE"
                and item.entitlement_end_at is not None and not subscription_entitlement(item, now=now)["entitled"]),
            "can_renew_here": registered(item) and item.status in {"ACTIVE","CANCELLED"},
            "catalog_monthly_price_minor": products[item.merchant].monthly_price_minor if registered(item) else None,
            **subscription_entitlement(item, now=now),
            **subscription_timing(item, calendar_time=now, data_through=through),
        } for item in selected]
        snapshot = EvidenceSnapshot(
            task_id=task.id, sandbox_id=task.sandbox_id,
            source_kind="SYNTHETIC_SUBSCRIPTIONS", source_version="seed-v1",
            payload_json={"subscriptions": rows,"requested_merchant":requested_merchant,
                "matched_merchant":rows[0]["merchant"] if filter_applied and rows else None,"filter_applied":filter_applied},
        )
        session.add(snapshot)
        session.flush()
        task.plan_json = {"nodes": [{
            "node_id": "list-subscriptions", "tool_name": "subscription.list.v1",
            "status": "SUCCEEDED", "evidence_id": snapshot.id,
        }]}
        add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
        fic.requested_capabilities = ["subscription.list.v1"]
        fic.status = "READY"
        task.fic_json = fic.model_dump()
        transition_task(task, "SUCCEEDED")
        task.result_json = {"message": (f"没有找到{requested_merchant}的订阅记录。" if filter_applied and not rows else
            "已找到这项服务的订阅记录。" if filter_applied else "已列出订阅服务。"), "subscriptions": rows,
            "requested_merchant":requested_merchant,"matched_merchant":rows[0]["merchant"] if filter_applied and rows else None,
            "filter_applied":filter_applied}
        append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                     event_type="SUBSCRIPTIONS_LISTED", payload={"count": len(rows)})
        append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                       task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
        return task

    from .subscription_commerce import resolve_subscription_merchant
    matches = [item for item in subscriptions if item.merchant==resolve_subscription_merchant(requested_merchant)] if requested_merchant is not None \
        else [item for item in subscriptions if item.merchant in message]
    if len(matches) != 1:
        task.result_json = {
            "message": "请明确要取消的订阅名称。",
            "active_subscriptions": [item.merchant for item in subscriptions if item.status == "ACTIVE"],
        }
        return task
    subscription = matches[0]
    if subscription.status == "CANCELLED" or subscription.status=="ACTIVE" and not subscription.auto_renew:
        snapshot = EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id,
            source_kind="SYNTHETIC_SUBSCRIPTION_STATUS", source_version=f"subscription:{subscription.version}",
            payload_json={"subscription_id": subscription.id, "merchant": subscription.merchant,
                          "status": subscription.status, "version": subscription.version,
                          "source_type": subscription.source_type})
        session.add(snapshot)
        session.flush()
        fic.requested_capabilities = ["subscription.list.v1"]
        fic.status = "READY"
        task.fic_json = fic.model_dump()
        transition_task(task, "SUCCEEDED")
        from .subscription_commerce import subscription_entitlement
        from .subscription_commerce import record_subscription_no_effect
        no_effect=record_subscription_no_effect(session,task,subscription,
            "ALREADY_CANCELLED" if subscription.status=="CANCELLED" else "NO_AUTO_RENEW")
        task.result_json = {"message": f"{subscription.merchant}自动续费本来就未开启，这次不需要重复取消。",
                            "subscription_id": subscription.id, "merchant":subscription.merchant,
                            "status": subscription.status, "already_cancelled": True,
                            "entitlement_preserved":True,"refunded_minor":0,
                            **subscription_entitlement(subscription),
                            **{key:value for key,value in no_effect.items() if key!="subscription_id"},
                            "source_type": subscription.source_type, "simulated": True}
        append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                     event_type="SUBSCRIPTION_STATUS_READ", payload=snapshot.payload_json)
        return task
    if subscription.status != "ACTIVE":
        task.result_json = {"message": "订阅当前状态不支持取消，请先核实状态。", "status": subscription.status}
        return task
    if subscription.source_type not in CONNECTED_SOURCES:
        fic.slots = {"merchant": {"value": subscription.merchant, "source": "AUTHORITATIVE"}}
        fic.status = "READY"
        task.fic_json = fic.model_dump()
        transition_task(task, "SUCCEEDED")
        task.result_json = {
            "message": f"{subscription.merchant} 未接入本平台的取消能力。请到服务商的订阅设置核实并关闭，当前没有执行取消。",
            "can_cancel_here": False,
            "source_type": subscription.source_type,
        }
        append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                     event_type="SUBSCRIPTION_EXTERNAL_GUIDANCE",
                     payload={"merchant": subscription.merchant, "source_type": subscription.source_type})
        append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                       task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
        return task

    fic.slots = {
        "subscription_id": {"value": subscription.id, "source": "AUTHORITATIVE"},
        "merchant": {"value": subscription.merchant, "source": "AUTHORITATIVE"},
        "source_type": {"value": subscription.source_type, "source": "AUTHORITATIVE"},
        "action": {"value": "CANCEL_MANDATE" if subscription.source_type == "BANK_MANDATE" else "CANCEL_MERCHANT_SUBSCRIPTION", "source": "USER_TEXT"},
    }
    fic.requested_capabilities = ["subscription.cancel.v1"]
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    plan = {
        "plan_version": 1, "policy_version": "subscription-v2",
        "task_id": task.id, "sandbox_id": task.sandbox_id,
        "nodes": [{
            "node_id": "cancel-subscription", "tool_name": "subscription.cancel.v1",
            "read_only": False, "subscription_id": subscription.id,
            "merchant": subscription.merchant, "amount_minor": subscription.amount_minor,
            "source_type": subscription.source_type,
            "expected_version": subscription.version, "requires_confirmation": True,
        }],
    }
    task.plan_json = plan
    task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id,
                  node_key="cancel-subscription", decision="REQUIRE_CONFIRMATION",
                  reason_codes=["MANDATE_CHANGE" if subscription.source_type == "BANK_MANDATE" else "MERCHANT_SUBSCRIPTION_CHANGE", "USER_REQUESTED_CANCELLATION"],
                  policy_version="subscription-v2")
    transition_task(task, "AWAITING_CONFIRMATION")
    task.result_json = {
        "message": f"准备取消 {subscription.merchant} 的{SOURCE_LABELS[subscription.source_type]}（{subscription.amount_minor / 100:.2f} 元/期），确认后执行。",
        "source_type": subscription.source_type,
        "simulated": True,
    }
    session.add(Confirmation(
        sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
        node_key="cancel-subscription", action_type="subscription.cancel.v1",
        target_id=subscription.id, plan_hash=task.plan_hash,
        view_hash=canonical_hash({"plan_hash": task.plan_hash, "merchant": subscription.merchant,
                                  "amount_minor": subscription.amount_minor, "source_type": subscription.source_type}),
        expires_at=utcnow() + timedelta(minutes=5),
    ))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                 event_type="SUBSCRIPTION_PLAN_PROPOSED",
                 payload={"merchant": subscription.merchant, "plan_hash": task.plan_hash})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                   task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
    return task
