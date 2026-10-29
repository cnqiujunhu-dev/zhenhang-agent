from __future__ import annotations

from .contracts import DomainError
from .models import Card, Confirmation, Task


def check_lock_card_policy(task: Task, card: Card, confirmation: Confirmation) -> None:
    if task.intent_type not in {"BILL_ANOMALY_LOCK_CARD", "CROSS_SCENARIO"}:
        raise DomainError("INTENT_NOT_ALLOWED", "当前任务没有锁卡权限", 403)
    if task.state != "AWAITING_CONFIRMATION":
        raise DomainError("TASK_NOT_CONFIRMABLE", "任务当前不在待确认状态", 409)
    if card.sandbox_id != task.sandbox_id or card.user_id != task.user_id:
        raise DomainError("CARD_NOT_OWNED", "无法操作该卡片", 403)
    if card.status != "ACTIVE":
        raise DomainError("CARD_NOT_ACTIVE", "卡片已不在可锁定状态", 409)
    if (
        confirmation.task_id != task.id
        or confirmation.target_id != card.id
        or confirmation.user_id != task.user_id
        or confirmation.action_type != "card.lock.v1"
    ):
        raise DomainError("CONFIRMATION_MISMATCH", "确认记录与任务不匹配", 409)
    if confirmation.status != "PENDING":
        raise DomainError("CONFIRMATION_USED", "确认记录已经使用", 409)
    if confirmation.plan_hash != task.plan_hash:
        raise DomainError("PLAN_CHANGED", "计划已变化，请重新确认", 409)
