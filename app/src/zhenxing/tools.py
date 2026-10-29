from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit
from .authentication import consume_auth
from .contracts import ConfirmTaskRequest, DomainError
from .models import (
    Account,
    Card,
    CardReplacementRequest,
    BudgetEnvelope,
    Confirmation,
    LedgerEntry,
    Holding,
    InvestmentLedgerEntry,
    InvestmentOrder,
    InvestmentProduct,
    OutboxMessage,
    Payee,
    PolicyDecisionRecord,
    RiskProfile,
    Subscription,
    Task,
    TaskNode,
    ToolCall,
    Transfer,
    utcnow,
)
from .planning import canonical_hash
from .posting import record_cash_transaction
from .platform import TOOL_REGISTRY
from .policy import check_lock_card_policy
from .workflow import finish_node


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ToolGateway:
    """Authorizes every write before invoking a domain handler."""

    LOCK_TOOL = "card.lock.v1"
    TRANSFER_TOOL = "transfer.commit.v1"
    CARD_MANAGE_TOOL = "card.manage.v1"
    SUBSCRIPTION_CANCEL_TOOL = "subscription.cancel.v1"
    SUBSCRIPTION_START_TOOL = "subscription.start.v1"
    SUBSCRIPTION_RENEW_TOOL = "subscription.renew.v1"
    WEALTH_ORDER_TOOL = "wealth.order.v1"

    def authorize_write(
        self,
        session: Session,
        *,
        task: Task,
        sandbox_id: str,
        user_id: str,
        request: ConfirmTaskRequest,
        idempotency_key: str,
        tool_name: str,
        node_key: str,
    ) -> tuple[str, TaskNode | None, Confirmation | None, bool]:
        if task.user_id != user_id or task.sandbox_id != sandbox_id:
            raise DomainError("TOOL_ACTOR_MISMATCH", "工具调用主体不属于当前任务", 403)
        from .permissions import require_banking_actor
        from .services import Principal
        require_banking_actor(session, Principal(user_id, sandbox_id))
        from .extensions import is_family_helper
        if is_family_helper(session, sandbox_id, user_id) and tool_name != "family.propose.v1":
            raise DomainError("FAMILY_HELPER_SCOPE_REQUIRED", "协助身份不能直接执行本人银行或权益写操作", 403)
        if not idempotency_key or len(idempotency_key) > 120:
            raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "需要有效的 Idempotency-Key", 400)
        request_hash = canonical_hash(
            {
                "task_id": task.id,
                "confirmation_id": request.confirmation_id,
                "plan_hash": request.plan_hash,
            }
        )
        existing = session.scalar(
            select(ToolCall).where(
                ToolCall.sandbox_id == sandbox_id,
                ToolCall.tool_name == tool_name,
                ToolCall.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.task_id != task.id or existing.request_hash != request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同请求", 409)
            return request_hash, None, None, True

        from .runtime_models import RuntimeRecord
        from .extensions import validate_family_origin
        managed = session.get(RuntimeRecord, task.id)
        if managed and managed.root_task_id:
            validate_family_origin(session, session.get(Task, managed.root_task_id))

        if request.expected_task_version != task.version:
            raise DomainError("TASK_VERSION_CHANGED", "任务版本已变化，请刷新后重试", 409)
        if task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}:
            raise DomainError("TASK_NOT_CONFIRMABLE", "任务当前不允许执行确认动作", 409)
        if task.plan_hash != request.plan_hash:
            raise DomainError("PLAN_CHANGED", "计划内容不一致，不能执行", 409)
        if canonical_hash(task.plan_json) != task.plan_hash:
            raise DomainError("PLAN_INTEGRITY_FAILED", "计划内容完整性校验失败", 409)
        tool_definition = TOOL_REGISTRY.get(tool_name)
        if tool_definition is None or tool_definition["read_only"]:
            raise DomainError("TOOL_NOT_ALLOWED", "工具未登记为可写能力", 403)
        node = session.scalar(
            select(TaskNode)
            .where(
                TaskNode.task_id == task.id,
                TaskNode.sandbox_id == sandbox_id,
                TaskNode.node_key == node_key,
                TaskNode.tool_name == tool_name,
            )
            .with_for_update()
        )
        decision = session.scalar(
            select(PolicyDecisionRecord).where(
                PolicyDecisionRecord.task_id == task.id,
                PolicyDecisionRecord.sandbox_id == sandbox_id,
                PolicyDecisionRecord.node_key == node_key,
            )
        )
        if node is None or node.status != "WAITING_CONFIRMATION":
            raise DomainError("NODE_NOT_READY", "工具节点不在待确认状态", 409)
        dependencies = list(session.scalars(select(TaskNode).where(
            TaskNode.task_id == task.id,
            TaskNode.sandbox_id == sandbox_id,
            TaskNode.node_key.in_(node.depends_on_json),
        ))) if node.depends_on_json else []
        if len(dependencies) != len(node.depends_on_json) or any(
            dependency.status != "SUCCEEDED" for dependency in dependencies
        ):
            raise DomainError("NODE_DEPENDENCY_NOT_READY", "前置节点尚未完成", 409)
        if decision is None or decision.decision != "REQUIRE_CONFIRMATION":
            raise DomainError("POLICY_NOT_APPROVED", "缺少有效的策略判定", 403)
        confirmation = session.scalar(
            select(Confirmation)
            .where(
                Confirmation.id == request.confirmation_id,
                Confirmation.task_id == task.id,
                Confirmation.sandbox_id == sandbox_id,
                Confirmation.user_id == user_id,
                Confirmation.node_key == node_key,
                Confirmation.action_type == tool_name,
            )
            .with_for_update()
        )
        if confirmation is None:
            raise DomainError("CONFIRMATION_NOT_FOUND", "确认记录不存在", 404)
        if confirmation.status != "PENDING":
            raise DomainError("CONFIRMATION_USED", "确认记录已经使用", 409)
        if confirmation.plan_hash != task.plan_hash:
            raise DomainError("PLAN_CHANGED", "计划已变化，请重新确认", 409)
        if as_utc(confirmation.expires_at) <= utcnow():
            raise DomainError("CONFIRMATION_EXPIRED", "确认已过期，请重新发起任务", 409)
        consume_auth(session, task=task, confirmation=confirmation,
                     challenge_id=request.auth_challenge_id, user_id=user_id)
        return request_hash, node, confirmation, False

    def dispatch_lock(
        self,
        session: Session,
        *,
        task: Task,
        user_id: str,
        sandbox_id: str,
        request: ConfirmTaskRequest,
        idempotency_key: str,
    ) -> Task:
        request_hash, node, confirmation, duplicate = self.authorize_write(
            session,
            task=task,
            sandbox_id=sandbox_id,
            user_id=user_id,
            request=request,
            idempotency_key=idempotency_key,
            tool_name=self.LOCK_TOOL,
            node_key="lock-card",
        )
        if duplicate:
            return task
        assert node is not None and confirmation is not None
        card = session.scalar(
            select(Card)
            .where(
                Card.id == confirmation.target_id,
                Card.sandbox_id == sandbox_id,
                Card.user_id == user_id,
            )
            .with_for_update()
        )
        if card is None:
            raise DomainError("CARD_NOT_FOUND", "卡片不存在", 404)
        check_lock_card_policy(task, card, confirmation)
        if confirmation.view_hash != canonical_hash({
            "plan_hash": task.plan_hash, "card_last4": card.last4,
            "action": "TEMPORARY_LOCK",
        }):
            raise DomainError("CONFIRMATION_VIEW_CHANGED", "确认信息已变化", 409)
        expected_card_version = task.plan_json["nodes"][1]["expected_card_version"]
        if card.version != expected_card_version:
            raise DomainError("CARD_VERSION_CHANGED", "卡片状态已变化，需重新评估", 409)

        card.status = "LOCKED"
        card.version += 1
        confirmation.status = "CONSUMED"
        confirmation.consumed_at = utcnow()
        task.result_json = {
            "message": f"尾号 {card.last4} 的卡片已临时锁定。",
            "card_id": card.id,
            "card_status": card.status,
            "card_version": card.version,
            "simulated": True,
        }
        finish_node(
            session,
            task,
            node,
            {"card_id": card.id, "status": card.status, "version": card.version},
        )
        session.add(
            ToolCall(
                sandbox_id=sandbox_id,
                task_id=task.id,
                tool_name=self.LOCK_TOOL,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="SUCCESS",
                result_json=task.result_json,
            )
        )
        append_audit(
            session,
            sandbox_id=sandbox_id,
            task_id=task.id,
            event_type="CARD_LOCKED",
            payload={"card_id": card.id, "last4": card.last4, "status": card.status, "version": card.version},
        )
        if task.state == "SUCCEEDED":
            append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                         event_type="TASK_SUCCEEDED",
                         payload={"plan_hash": task.plan_hash, "simulated": True})
            session.add(OutboxMessage(
                sandbox_id=sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
                payload_json={"task_id": task.id, "card_status": card.status},
            ))
        return task

    def dispatch_transfer(
        self,
        session: Session,
        *,
        task: Task,
        user_id: str,
        sandbox_id: str,
        request: ConfirmTaskRequest,
        idempotency_key: str,
    ) -> Task:
        request_hash, node, confirmation, duplicate = self.authorize_write(
            session,
            task=task,
            sandbox_id=sandbox_id,
            user_id=user_id,
            request=request,
            idempotency_key=idempotency_key,
            tool_name=self.TRANSFER_TOOL,
            node_key="commit-transfer",
        )
        if duplicate:
            return task
        assert node is not None and confirmation is not None
        plan_node = next(
            (item for item in task.plan_json["nodes"] if item["node_id"] == "commit-transfer"),
            None,
        )
        if plan_node is None:
            raise DomainError("PLAN_NODE_MISSING", "转账执行节点不存在", 409)
        if task.intent_type != "TRANSFER":
            raise DomainError("INTENT_NOT_ALLOWED", "当前任务没有转账权限", 403)
        payee = session.scalar(
            select(Payee).where(
                Payee.id == plan_node["payee_id"],
                Payee.sandbox_id == sandbox_id,
                Payee.user_id == user_id,
                Payee.status == "ACTIVE",
            )
        )
        if payee is None or confirmation.target_id != payee.id:
            raise DomainError("TRANSFER_TARGET_CHANGED", "付款账户或收款人已变化", 409)
        from .internal_banking import lock_transfer_accounts, commit_transfer_money, recipient_scope, account_reference
        from .contacts import validate_alias_proposal, validate_recipient_context, commit_alias_proposal
        # Personal role reads and identity writes serialize under the same
        # sorted registry lock before monetary accounts, including CREATE races.
        if plan_node.get("alias_binding"):
            validate_alias_proposal(session, task, plan_node["alias_binding"])
        if plan_node.get("recipient_context"):
            validate_recipient_context(session, sandbox_id, user_id, plan_node["recipient_context"])
        account = lock_transfer_accounts(session, sandbox_id, user_id, plan_node["source_account_id"], payee)
        destination = {key: plan_node.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")}
        if "transfer_channel" not in plan_node:
            destination = {"transfer_channel": "EXTERNAL_SIM", "recipient_username": None, "recipient_account_id": None}
        if recipient_scope(session, payee) != destination:
            raise DomainError("RECIPIENT_SCOPE_CHANGED", "收款渠道或已确认平台账号发生变化", 409)
        if account.version != plan_node["expected_account_version"]:
            raise DomainError("ACCOUNT_VERSION_CHANGED", "账户状态已变化，请重新评估", 409)
        amount_minor = plan_node["amount_minor"]
        payment_scope = plan_node.get("transfer_payment_scope")
        if payment_scope:
            from .transfer_payment_scope import validate_transfer_payment_scope
            from .services import Principal
            if (payment_scope.get("amount_minor"), payment_scope.get("currency")) != (amount_minor, "CNY"):
                raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED", "确认金额与收付款卡范围不一致，未执行。", 409)
            fresh_source = validate_transfer_payment_scope(session, Principal(user_id, sandbox_id), payment_scope)
            if fresh_source.get("account_id") != account.id or (payment_scope.get("recipient") or {}).get("account_id") != destination.get("recipient_account_id"):
                raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED", "收付款卡与确认账户不一致，未执行。", 409)
        source_card = plan_node.get("source_card_context")
        if source_card:
            selected = session.scalar(select(Card).where(Card.id == source_card["card_id"],
                Card.sandbox_id == sandbox_id, Card.user_id == user_id).with_for_update().execution_options(populate_existing=True))
            if selected is None or selected.account_id != account.id or source_card["account_id"] != account.id \
                    or selected.status != "ACTIVE" or selected.version != source_card["card_version"] \
                    or selected.limit_minor < amount_minor:
                raise DomainError("TRANSFER_SOURCE_CARD_CHANGED", "明确付款卡已变化或不可用，请重新确认，不会自动换卡", 409)
        if not isinstance(amount_minor, int) or amount_minor <= 0 or amount_minor > 5_000_000:
            raise DomainError("INVALID_AMOUNT", "转账金额不在允许范围", 409)
        if account.balance_minor < amount_minor:
            raise DomainError("INSUFFICIENT_FUNDS", "账户余额不足，转账未执行", 409)
        from .extensions import evaluate_transfer_risk, reserved_goal_cash_minor
        if account.balance_minor - reserved_goal_cash_minor(session, sandbox_id, user_id, account.id) < amount_minor:
            raise DomainError("GOAL_RESERVE_BREACH", "转账会占用已预留的目标资金，请先调整目标", 409)
        risk = evaluate_transfer_risk(session, task=task, context={"payee_id": payee.id, "for_commit": True})
        if risk["decision"] != "ALLOW":
            raise DomainError("FRAUD_TRANSFER_BLOCKED", "转账风险未通过核验，确认不能绕过风险限制", 403)
        confirmation_view = {"plan_hash": task.plan_hash, "payee_last4": payee.account_last4, "amount_minor": amount_minor}
        if "transfer_channel" in plan_node:
            confirmation_view["recipient_scope"] = destination
        if confirmation.view_hash != canonical_hash(confirmation_view):
            raise DomainError("CONFIRMATION_VIEW_CHANGED", "确认信息已变化", 409)

        transfer = commit_transfer_money(session, account, payee, amount_minor, plan_node["memo"], idempotency_key, destination,
            source_card_id=(payment_scope["source"].get("card_id") if payment_scope else None),
            recipient_card_id=((payment_scope.get("recipient") or {}).get("card_id") if payment_scope else None))
        saved_alias = commit_alias_proposal(session, task, plan_node.get("alias_binding"))
        confirmation.status = "CONSUMED"
        confirmation.consumed_at = utcnow()
        task.result_json = {
            "message": (f"已向平台账号 {payee.display_name}转账 {amount_minor/100:.2f} 元，双方账本与流水已同时入账。"
                        if destination["transfer_channel"] == "INTERNAL_BANK" else
                        f"已向 {payee.display_name}外部转出 {amount_minor/100:.2f} 元（外部清算已记账；外部到账未核验，无平台用户入账）。"),
            **destination,
            "recipient_reference": account_reference(destination["recipient_account_id"]) if destination["recipient_account_id"] else None,
            "transfer_id": transfer.id,
            "amount_minor": amount_minor,
            "source_balance_minor": account.balance_minor,
            "payee_last4": payee.account_last4,
            "simulated": True,
            **({"contact_alias": saved_alias} if saved_alias else {}),
            **({"recipient_context": plan_node["recipient_context"]} if plan_node.get("recipient_context") else {}),
            **({"source_card_context": source_card} if source_card else {}),
            **({"payment_confirmation": payment_scope["display"]} if payment_scope else {}),
        }
        finish_node(
            session,
            task,
            node,
            {"transfer_id": transfer.id, "status": "POSTED", "amount_minor": amount_minor},
        )
        session.add(
            ToolCall(
                sandbox_id=sandbox_id,
                task_id=task.id,
                tool_name=self.TRANSFER_TOOL,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="SUCCESS",
                result_json=task.result_json,
            )
        )
        append_audit(
            session,
            sandbox_id=sandbox_id,
            task_id=task.id,
            event_type="TRANSFER_POSTED",
            payload={"transfer_id": transfer.id, "amount_minor": amount_minor, "payee_last4": payee.account_last4},
        )
        append_audit(
            session,
            sandbox_id=sandbox_id,
            task_id=task.id,
            event_type="TASK_SUCCEEDED",
            payload={"plan_hash": task.plan_hash, "simulated": True},
        )
        session.add(
            OutboxMessage(
                sandbox_id=sandbox_id,
                task_id=task.id,
                event_type="TASK_COMPLETED",
                payload_json={"task_id": task.id, "transfer_id": transfer.id},
            )
        )
        return task

    def dispatch_card_manage(
        self,
        session: Session,
        *,
        task: Task,
        user_id: str,
        sandbox_id: str,
        request: ConfirmTaskRequest,
        idempotency_key: str,
    ) -> Task:
        request_hash, node, confirmation, duplicate = self.authorize_write(
            session, task=task, sandbox_id=sandbox_id, user_id=user_id,
            request=request, idempotency_key=idempotency_key,
            tool_name=self.CARD_MANAGE_TOOL, node_key="manage-card",
        )
        if duplicate:
            return task
        assert node is not None and confirmation is not None
        if task.intent_type != "CARD_MANAGEMENT":
            raise DomainError("INTENT_NOT_ALLOWED", "当前任务没有卡片管理权限", 403)
        plan_node = next((item for item in task.plan_json.get("nodes", [])
                          if item.get("node_id") == "manage-card"), None)
        if plan_node is None:
            raise DomainError("PLAN_NODE_MISSING", "卡片执行节点不存在", 409)
        card = session.scalar(select(Card).where(
            Card.id == plan_node["card_id"], Card.sandbox_id == sandbox_id,
            Card.user_id == user_id,
        ).with_for_update())
        if card is None or confirmation.target_id != card.id:
            raise DomainError("CARD_NOT_FOUND", "卡片不存在或确认目标已变化", 409)
        if card.version != plan_node["expected_card_version"]:
            raise DomainError("CARD_VERSION_CHANGED", "卡片状态已变化，请重新评估", 409)
        action = plan_node["action"]
        limit_minor = plan_node.get("limit_minor")
        if confirmation.view_hash != canonical_hash({
            "plan_hash": task.plan_hash, "card_last4": card.last4,
            "action": action, "limit_minor": limit_minor,
        }):
            raise DomainError("CONFIRMATION_VIEW_CHANGED", "确认信息已变化", 409)
        if action == "LOCK" and card.status == "ACTIVE":
            card.status = "LOCKED"
        elif action == "UNLOCK" and card.status == "LOCKED":
            card.status = "ACTIVE"
        elif action == "REPORT_LOSS" and card.status in {"ACTIVE", "LOCKED"}:
            card.status = "LOST"
        elif action == "REQUEST_REPLACEMENT" and card.status == "LOST":
            if session.scalar(select(CardReplacementRequest).where(
                CardReplacementRequest.sandbox_id == sandbox_id,
                CardReplacementRequest.user_id == user_id,
                CardReplacementRequest.card_id == card.id,
                CardReplacementRequest.status == "REQUESTED",
            )) is not None:
                raise DomainError("REPLACEMENT_ALREADY_REQUESTED", "该卡已有待处理的补卡申请", 409)
            session.add(CardReplacementRequest(
                sandbox_id=sandbox_id, user_id=user_id, card_id=card.id,
                idempotency_key=idempotency_key,
            ))
        elif action == "SET_LIMIT" and card.status == "ACTIVE" and isinstance(limit_minor, int) \
                and 10_000 <= limit_minor <= 2_000_000:
            card.limit_minor = limit_minor
        else:
            raise DomainError("CARD_ACTION_NOT_ALLOWED", "当前卡片状态不允许该操作", 409)
        card.version += 1
        confirmation.status = "CONSUMED"
        confirmation.consumed_at = utcnow()
        task.result_json = {
            "message": (
                f"卡片 {plan_node.get('card_number') or ('旧兼容尾号 '+card.last4)} 已提交补卡申请（未实际制卡）。"
                if action == "REQUEST_REPLACEMENT" else
                f"卡片 {plan_node.get('card_number') or ('旧兼容尾号 '+card.last4)} 已完成 {action} 操作。"
            ),
            "card_id": card.id,
            "card_status": card.status,
            "card_version": card.version,
            "limit_minor": card.limit_minor,
            "simulated": True,
            **{key: value for key, value in plan_node.items()
               if key in {"card_number", "display_last4", "legacy_last4", "issuer_prefix"}},
        }
        finish_node(session, task, node, {
            "card_id": card.id, "action": action, "status": card.status,
            "version": card.version, "limit_minor": card.limit_minor,
        })
        session.add(ToolCall(
            sandbox_id=sandbox_id, task_id=task.id, tool_name=self.CARD_MANAGE_TOOL,
            idempotency_key=idempotency_key, request_hash=request_hash,
            status="SUCCESS", result_json=task.result_json,
        ))
        append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                     event_type="CARD_MANAGEMENT_COMPLETED",
                     payload={"action": action, "card_last4": card.last4,
                              "status": card.status, "version": card.version})
        append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                     event_type="TASK_SUCCEEDED",
                     payload={"plan_hash": task.plan_hash, "simulated": True})
        session.add(OutboxMessage(
            sandbox_id=sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
            payload_json={"task_id": task.id, "card_status": card.status},
        ))
        return task

    def dispatch_subscription_cancel(
        self, session: Session, *, task: Task, user_id: str,
        sandbox_id: str, request: ConfirmTaskRequest, idempotency_key: str,
    ) -> Task:
        request_hash, node, confirmation, duplicate = self.authorize_write(
            session, task=task, sandbox_id=sandbox_id, user_id=user_id,
            request=request, idempotency_key=idempotency_key,
            tool_name=self.SUBSCRIPTION_CANCEL_TOOL, node_key="cancel-subscription",
        )
        if duplicate:
            return task
        assert node is not None and confirmation is not None
        if task.intent_type not in {"SUBSCRIPTION_MANAGEMENT", "CROSS_SCENARIO"}:
            raise DomainError("INTENT_NOT_ALLOWED", "当前任务没有订阅管理权限", 403)
        plan_node = next((item for item in task.plan_json.get("nodes", [])
                          if item.get("node_id") == "cancel-subscription"), None)
        if plan_node is None:
            raise DomainError("PLAN_NODE_MISSING", "订阅执行节点不存在", 409)
        subscription = session.scalar(select(Subscription).where(
            Subscription.id == plan_node["subscription_id"],
            Subscription.sandbox_id == sandbox_id,
            Subscription.user_id == user_id,
        ).with_for_update())
        if subscription is None or confirmation.target_id != subscription.id:
            raise DomainError("SUBSCRIPTION_NOT_FOUND", "订阅不存在或确认目标已变化", 409)
        if subscription.version != plan_node["expected_version"] or subscription.status != "ACTIVE":
            raise DomainError("SUBSCRIPTION_CHANGED", "订阅状态已变化，请重新评估", 409)
        if subscription.source_type not in {"BANK_MANDATE", "MERCHANT_SIM"}:
            raise DomainError("UNCONNECTED_SUBSCRIPTION", "该服务未接入取消能力", 403)
        expected_source = plan_node.get("source_type")
        if subscription.source_type != expected_source:
            raise DomainError("SUBSCRIPTION_SOURCE_CHANGED", "订阅来源已变化，请重新评估", 409)
        if subscription.merchant != plan_node["merchant"] or subscription.amount_minor != plan_node["amount_minor"]:
            raise DomainError("SUBSCRIPTION_CHANGED", "订阅名称或周期金额已变化，请重新评估", 409)
        view = {
            "plan_hash": task.plan_hash, "merchant": subscription.merchant,
            "amount_minor": subscription.amount_minor,
            "source_type": subscription.source_type,
        }
        if confirmation.view_hash != canonical_hash(view):
            raise DomainError("CONFIRMATION_VIEW_CHANGED", "确认信息已变化", 409)
        subscription.status = "CANCELLED"
        subscription.auto_renew = False
        subscription.version += 1
        confirmation.status = "CONSUMED"
        confirmation.consumed_at = utcnow()
        task.result_json = {
            "message": f"{subscription.merchant} 的{'银行代扣协议' if subscription.source_type == 'BANK_MANDATE' else '商户订阅'}已取消。",
            "subscription_id": subscription.id, "status": subscription.status,
            "source_type": subscription.source_type, "version": subscription.version,
            "auto_renew": False,
            "entitlement_end_at": as_utc(subscription.entitlement_end_at).isoformat() if subscription.entitlement_end_at else None,
            "entitlement_preserved": True, "refunded_minor": 0,
            "simulated": True,
        }
        finish_node(session, task, node, {
            "subscription_id": subscription.id, "status": subscription.status,
            "version": subscription.version, "source_type": subscription.source_type,
        })
        session.add(ToolCall(
            sandbox_id=sandbox_id, task_id=task.id,
            tool_name=self.SUBSCRIPTION_CANCEL_TOOL, idempotency_key=idempotency_key,
            request_hash=request_hash, status="SUCCESS", result_json=task.result_json,
        ))
        append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                     event_type="SUBSCRIPTION_CANCELLED",
                     payload={"subscription_id": subscription.id, "merchant": subscription.merchant,
                              "source_type": subscription.source_type, "version": subscription.version})
        if task.state == "SUCCEEDED":
            append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                         event_type="TASK_SUCCEEDED",
                         payload={"plan_hash": task.plan_hash, "simulated": True})
            session.add(OutboxMessage(
                sandbox_id=sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
                payload_json={"task_id": task.id, "subscription_id": subscription.id},
            ))
        return task

    def dispatch_subscription_start(self, session: Session, *, task: Task, user_id: str,
                                    sandbox_id: str, request: ConfirmTaskRequest, idempotency_key: str) -> Task:
        from .subscription_commerce import dispatch_subscription_payment
        return dispatch_subscription_payment(self, session, task=task, user_id=user_id,
            sandbox_id=sandbox_id, request=request, idempotency_key=idempotency_key, action="START")

    def dispatch_subscription_renew(self, session: Session, *, task: Task, user_id: str,
                                    sandbox_id: str, request: ConfirmTaskRequest, idempotency_key: str) -> Task:
        from .subscription_commerce import dispatch_subscription_payment
        return dispatch_subscription_payment(self, session, task=task, user_id=user_id,
            sandbox_id=sandbox_id, request=request, idempotency_key=idempotency_key, action="RENEW")

    def dispatch_wealth_order(
        self, session: Session, *, task: Task, user_id: str,
        sandbox_id: str, request: ConfirmTaskRequest, idempotency_key: str,
    ) -> Task:
        from .customer_profile_common import require_profile_actor
        from .services import Principal
        require_profile_actor(session, Principal(user_id, sandbox_id), lock=True)
        request_hash, node, confirmation, duplicate = self.authorize_write(
            session, task=task, sandbox_id=sandbox_id, user_id=user_id,
            request=request, idempotency_key=idempotency_key,
            tool_name=self.WEALTH_ORDER_TOOL, node_key="post-investment-order",
        )
        if duplicate:
            return task
        assert node is not None and confirmation is not None
        if task.intent_type != "WEALTH":
            raise DomainError("INTENT_NOT_ALLOWED", "当前任务没有理财交易权限", 403)
        plan_node = next((item for item in task.plan_json.get("nodes", [])
                          if item.get("node_id") == "post-investment-order"), None)
        if plan_node is None:
            raise DomainError("PLAN_NODE_MISSING", "理财交易节点不存在", 409)
        product = session.scalar(select(InvestmentProduct).where(
            InvestmentProduct.id == plan_node["product_id"],
            InvestmentProduct.sandbox_id == sandbox_id,
        ).with_for_update())
        from .services import Principal
        from .payment_instruments import resolve_source_instrument, validate_instrument_snapshot, cash_reserved_minor
        wealth_principal = Principal(user_id, sandbox_id)
        source = plan_node.get('payment_instrument') or resolve_source_instrument(session, wealth_principal, None, 'CNY', purpose='WEALTH' if plan_node.get('order_type') == 'BUY' else 'READ')
        if source['instrument_type'] != 'DEBIT':
            raise DomainError('WEALTH_CASH_ACCOUNT_REQUIRED', '理财申赎须使用本人借记资金账户。', 403)
        if plan_node.get('order_type') == 'REDEEM':
            validate_instrument_snapshot(session, wealth_principal, source, purpose='READ')
        account = session.scalar(select(Account).where(
            Account.id == source['account_id'], Account.sandbox_id == sandbox_id, Account.user_id == user_id,
        ).with_for_update())
        profile = session.scalar(select(RiskProfile).where(
            RiskProfile.id == plan_node["expected_profile_id"],
            RiskProfile.sandbox_id == sandbox_id, RiskProfile.user_id == user_id,
        ).with_for_update())
        holding = session.scalar(select(Holding).where(
            Holding.sandbox_id == sandbox_id, Holding.user_id == user_id,
            Holding.product_id == plan_node["product_id"],
        ).with_for_update())
        if product is None or account is None or (plan_node.get("order_type") == "BUY" and profile is None) or confirmation.target_id != product.id:
            raise DomainError("WEALTH_TARGET_CHANGED", "理财交易目标已变化", 409)
        if product.status != "AVAILABLE" or product.version != plan_node["expected_product_version"] \
                or account.version != plan_node["expected_account_version"]:
            raise DomainError("WEALTH_FACTS_CHANGED", "产品或账户状态已变化，请重新评估", 409)
        from .simulation import as_utc as bank_as_utc
        from .operational_clock import posting_now
        if plan_node.get("order_type") == "BUY" and bank_as_utc(profile.expires_at) <= posting_now(session, sandbox_id, user_id):
            raise DomainError("RISK_PROFILE_EXPIRED", "风险测评已过期", 409)
        if (holding.version if holding else None) != plan_node["expected_holding_version"]:
            raise DomainError("HOLDING_CHANGED", "持仓已变化，请重新评估", 409)
        order_type = plan_node["order_type"]
        amount_minor = plan_node["amount_minor"]
        units = plan_node["units"]
        if order_type not in {"BUY", "REDEEM"} or not isinstance(units, int) or units <= 0 \
                or amount_minor != units * product.price_minor \
                or product.price_minor != plan_node["unit_price_minor"]:
            raise DomainError("INVALID_INVESTMENT_ORDER", "理财交易参数无效", 409)
        if confirmation.view_hash != canonical_hash({
            "plan_hash": task.plan_hash, "product_name": product.name,
            "order_type": order_type, "amount_minor": amount_minor, "units": units,
        }):
            raise DomainError("CONFIRMATION_VIEW_CHANGED", "确认信息已变化", 409)
        if order_type == "BUY":
            validate_instrument_snapshot(session, wealth_principal, source, purpose='WEALTH', amount_minor=amount_minor)
            from .wealth import risk_profile_view
            if not risk_profile_view(session, sandbox_id, user_id)['valid']:
                raise DomainError('RISK_PROFILE_REASSESSMENT_REQUIRED', '风险测评已失效或需要更新，未执行申购。', 409)
            if product.risk_level > profile.risk_level:
                raise DomainError("RISK_MISMATCH", "产品风险等级超出用户测评等级", 403)
            budget = session.scalar(select(BudgetEnvelope).where(
                BudgetEnvelope.sandbox_id == sandbox_id,
                BudgetEnvelope.user_id == user_id,
                BudgetEnvelope.label == "应急金",
            ))
            reserved = cash_reserved_minor(session, wealth_principal, account.id)
            if account.balance_minor - reserved - amount_minor < (budget.reserved_minor if budget else 0):
                raise DomainError("EMERGENCY_RESERVE_BREACH", "交易会侵占应急金", 409)
            if holding is None:
                holding = Holding(sandbox_id=sandbox_id, user_id=user_id,
                                  product_id=product.id, units=0)
                session.add(holding)
            account_delta = -amount_minor
            holding.units += units
        else:
            if holding is None or holding.units < units:
                raise DomainError("INSUFFICIENT_HOLDING", "持仓份额不足", 409)
            account_delta = amount_minor
            holding.units -= units
        account.balance_minor += account_delta
        account.version += 1
        holding.version += 1
        order = InvestmentOrder(
            sandbox_id=sandbox_id, user_id=user_id,
            product_id=product.id, account_id=account.id,
            order_type=order_type, units=units, amount_minor=amount_minor,
            idempotency_key=idempotency_key,
        )
        session.add(order)
        session.flush()
        entries = [
            InvestmentLedgerEntry(sandbox_id=sandbox_id, order_id=order.id,
                                  account_ref=account.id, delta_minor=account_delta),
            InvestmentLedgerEntry(sandbox_id=sandbox_id, order_id=order.id,
                                  account_ref=f"INVESTMENT:{product.id}", delta_minor=-account_delta),
        ]
        if sum(item.delta_minor for item in entries) != 0:
            raise DomainError("LEDGER_NOT_BALANCED", "理财账本分录不平衡", 500)
        session.add_all(entries)
        record_cash_transaction(session, sandbox_id=sandbox_id, account_id=account.id,
                                delta_minor=account_delta, merchant=product.name, category="投资交易",
                                source_kind="INVESTMENT", operation_ref=f"investment:{order.id}")
        confirmation.status = "CONSUMED"
        confirmation.consumed_at = utcnow()
        task.result_json = {
            "message": f"{product.name} 已完成{('申购' if order_type == 'BUY' else '赎回')}。",
            "order_id": order.id, "order_type": order_type,
            "amount_minor": amount_minor, "holding_units": holding.units,
            "source_balance_minor": account.balance_minor, "simulated": True,
        }
        finish_node(session, task, node, {"order_id": order.id, "status": "POSTED"})
        session.add(ToolCall(
            sandbox_id=sandbox_id, task_id=task.id, tool_name=self.WEALTH_ORDER_TOOL,
            idempotency_key=idempotency_key, request_hash=request_hash,
            status="SUCCESS", result_json=task.result_json,
        ))
        append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                     event_type="WEALTH_ORDER_POSTED",
                     payload={"order_id": order.id, "order_type": order_type,
                              "amount_minor": amount_minor, "product_id": product.id})
        append_audit(session, sandbox_id=sandbox_id, task_id=task.id,
                     event_type="TASK_SUCCEEDED",
                     payload={"plan_hash": task.plan_hash, "simulated": True})
        session.add(OutboxMessage(
            sandbox_id=sandbox_id, task_id=task.id, event_type="TASK_COMPLETED",
            payload_json={"task_id": task.id, "order_id": order.id},
        ))
        return task
