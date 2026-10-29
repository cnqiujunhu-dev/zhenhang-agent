from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import analyze_bills
from .audit import append_audit
from .contracts import FinancialIntentContract
from .models import Account, Card, Confirmation, EvidenceSnapshot, Subscription, Task, utcnow
from .model_gateway import RuleIntentModel
from .planning import build_lock_plan, canonical_hash
from .platform import add_task_nodes, append_message, record_bill_evidence, record_policy
from .workflow import transition_task
from .resource_defaults import ARCHIVED_RESOURCE_STATUS


def prepare_cross_task(session: Session, *, task: Task, conversation_id: str) -> Task:
    if RuleIntentModel().classify(task.message).intent_type != "CROSS_SCENARIO":
        task.result_json = {"message": "跨场景操作需要明确提出账单核验、卡片保护和订阅处理。"}
        return task
    account = session.scalar(select(Account).where(
        Account.sandbox_id == task.sandbox_id, Account.user_id == task.user_id,
    ))
    if account is None:
        task.result_json = {"message": "未找到当前账户。"}
        transition_task(task, "FAILED")
        return task
    analysis = analyze_bills(session, task.sandbox_id, account.id)
    task.analysis_json = analysis.model_dump()
    card_ids = {item["card_id"] for item in analysis.anomalies if item["card_id"]}
    if len(card_ids) != 1:
        task.result_json = {"message": "异常交易未能唯一关联卡片，请先确认卡片。"}
        return task
    card = session.scalar(select(Card).where(
        Card.id == next(iter(card_ids)), Card.sandbox_id == task.sandbox_id,
        Card.user_id == task.user_id,
        Card.status != ARCHIVED_RESOURCE_STATUS,
    ))
    if card is None or card.status != "ACTIVE":
        task.result_json = {"message": "关联卡片无法临时锁定，请先核实卡片状态。"}
        return task
    subscriptions = list(session.scalars(select(Subscription).where(
        Subscription.sandbox_id == task.sandbox_id,
        Subscription.user_id == task.user_id,
        Subscription.status != ARCHIVED_RESOURCE_STATUS,
    )))
    wants_cancel = any(term in task.message for term in ("取消", "关闭", "停止", "退订"))
    if any(term in task.message for term in ("不要取消", "别取消", "先别取消", "不取消")):
        wants_cancel = False
    match = None
    if wants_cancel:
        matches = [item for item in subscriptions if item.status == "ACTIVE" and item.merchant in task.message]
        if len(matches) != 1:
            task.result_json = {
                "message": "请先明确要取消的订阅名称；卡片尚未操作。",
                "active_subscriptions": [item.merchant for item in subscriptions if item.status == "ACTIVE"],
            }
            return task
        match = matches[0]
        if match.source_type not in {"BANK_MANDATE", "MERCHANT_SIM"}:
            task.result_json = {
                "message": f"{match.merchant} 未接入银行代扣取消工具。请分开进行临时锁卡，并到服务商核实退订。",
                "card_unchanged": True,
            }
            return task

    evidence_id = record_bill_evidence(session, task_id=task.id, sandbox_id=task.sandbox_id,
                                       payload={
                                           "transaction_count": analysis.transaction_count,
                                           "anomaly_ids": [item["transaction_id"] for item in analysis.anomalies],
                                       })
    subscription_snapshot = EvidenceSnapshot(
        task_id=task.id, sandbox_id=task.sandbox_id,
        source_kind="SYNTHETIC_SUBSCRIPTIONS", source_version="seed-v1",
        payload_json={"subscriptions": [{
            "merchant": item.merchant, "source_type": item.source_type,
            "status": item.status, "amount_minor": item.amount_minor,
        } for item in subscriptions]},
    )
    session.add(subscription_snapshot)
    session.flush()
    plan, _ = build_lock_plan(
        task_id=task.id, sandbox_id=task.sandbox_id, card_id=card.id,
        card_last4=card.last4, card_version=card.version, analysis=analysis,
    )
    plan["policy_version"] = "cross-bill-card-subscription-v1"
    plan["nodes"][0]["evidence_id"] = evidence_id
    plan["nodes"].append({
        "node_id": "list-subscriptions", "tool_name": "subscription.list.v1",
        "read_only": True, "status": "SUCCEEDED",
        "evidence_id": subscription_snapshot.id,
    })
    if match is not None:
        plan["nodes"].append({
            "node_id": "cancel-subscription", "tool_name": "subscription.cancel.v1",
            "read_only": False, "depends_on": ["lock-card", "list-subscriptions"],
            "subscription_id": match.id, "merchant": match.merchant,
            "source_type": match.source_type,
            "amount_minor": match.amount_minor, "expected_version": match.version,
            "requires_confirmation": True,
        })
    task.plan_json = plan
    task.plan_hash = canonical_hash(plan)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id,
                  node_key="lock-card", decision="REQUIRE_CONFIRMATION",
                  reason_codes=["CARD_STATE_CHANGE", "USER_REQUESTED_PROTECTION"],
                  policy_version="cross-bill-card-subscription-v1")
    if match is not None:
        record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id,
                      node_key="cancel-subscription", decision="REQUIRE_CONFIRMATION",
                      reason_codes=["MANDATE_CHANGE", "USER_REQUESTED_CANCELLATION"],
                      policy_version="cross-bill-card-subscription-v1")
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.slots = {
        "card_id": {"value": card.id, "source": "AUTHORITATIVE_TRANSACTION_LINK"},
    }
    if match is not None:
        fic.slots["subscription_id"] = {"value": match.id, "source": "AUTHORITATIVE"}
    fic.requested_capabilities = ["bill.analyze.v1", "subscription.list.v1", "card.lock.v1"]
    if match is not None:
        fic.requested_capabilities.append("subscription.cancel.v1")
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    transition_task(task, "AWAITING_CONFIRMATION")
    task.result_json = {
        "message": (f"已核验账单与订阅。先确认临时锁定尾号 {card.last4} 的卡片；"
                    + (f"随后单独确认取消 {match.merchant} 的银行代扣。" if match else
                       "订阅只做只读盘点。")),
        "subscription_count": len(subscriptions), "simulated": True,
    }
    session.add(Confirmation(
        sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
        node_key="lock-card", action_type="card.lock.v1", target_id=card.id,
        plan_hash=task.plan_hash,
        view_hash=canonical_hash({"plan_hash": task.plan_hash, "card_last4": card.last4,
                                  "action": "TEMPORARY_LOCK"}),
        expires_at=utcnow() + timedelta(minutes=15),
    ))
    if match is not None:
        session.add(Confirmation(
            sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id,
            node_key="cancel-subscription", action_type="subscription.cancel.v1",
            target_id=match.id, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "merchant": match.merchant, "source_type": match.source_type,
                                      "amount_minor": match.amount_minor}),
            expires_at=utcnow() + timedelta(minutes=15),
        ))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                 event_type="CROSS_PLAN_PROPOSED",
                 payload={"plan_hash": task.plan_hash, "node_count": len(plan["nodes"]),
                          "anomaly_count": len(analysis.anomalies)})
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
                   task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
    return task
