from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import DomainError
from .models import Task, TaskNode


ALLOWED_TRANSITIONS = {
    "NEED_CLARIFICATION": {"PLANNED", "AWAITING_CONFIRMATION", "SUCCEEDED", "FAILED", "CLOSED"},
    "PLANNED": {"POLICY_CHECKED", "AWAITING_CONFIRMATION", "FAILED"},
    "POLICY_CHECKED": {"AWAITING_CONFIRMATION", "AUTHORIZED", "FAILED"},
    "AWAITING_CONFIRMATION": {"AUTHORIZED", "EXECUTING", "WAITING", "PARTIAL", "SUCCEEDED", "FAILED", "CLOSED"},
    "WAITING": {"EXECUTING", "AWAITING_CONFIRMATION", "SUCCEEDED", "FAILED", "CLOSED"},
    "AUTHORIZED": {"EXECUTING", "FAILED"},
    "EXECUTING": {"AWAITING_CONFIRMATION", "PARTIAL", "SUCCEEDED", "FAILED", "UNKNOWN"},
    "PARTIAL": {"AWAITING_CONFIRMATION", "COMPENSATING", "SUCCEEDED", "CLOSED"},
    "UNKNOWN": {"EXECUTING", "PARTIAL", "SUCCEEDED", "FAILED"},
    "COMPENSATING": {"PARTIAL", "FAILED", "CLOSED"},
    "SUCCEEDED": {"CLOSED"},
    "FAILED": {"CLOSED"},
    "CLOSED": set(),
}


def transition_task(task: Task, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(task.state, set()):
        raise DomainError("INVALID_TASK_TRANSITION", "不允许的任务状态变化", 409)
    task.state = target
    task.version += 1


def ready_node_keys(nodes: list[TaskNode]) -> list[str]:
    """Return nodes whose recorded dependencies all succeeded."""
    by_key = {node.node_key: node for node in nodes}
    if len(by_key) != len(nodes):
        raise DomainError("PLAN_DUPLICATE_NODE", "任务计划包含重复节点", 409)
    ready = []
    for node in nodes:
        if node.status not in {"READY", "BLOCKED"}:
            continue
        if any(dependency not in by_key for dependency in node.depends_on_json):
            raise DomainError("PLAN_MISSING_DEPENDENCY", "任务计划引用不存在的节点", 409)
        if all(by_key[dependency].status == "SUCCEEDED" for dependency in node.depends_on_json):
            ready.append(node.node_key)
    return ready


def finish_node(session: Session, task: Task, node: TaskNode, result: dict) -> None:
    if node.status not in {"RUNNING", "WAITING_CONFIRMATION", "WAITING_TIME", "READY"}:
        raise DomainError("NODE_NOT_FINISHABLE", "节点当前不能完成", 409)
    node.status = "SUCCEEDED"
    node.version += 1
    node.result_json = result
    nodes = list(
        session.scalars(
            select(TaskNode).where(
                TaskNode.task_id == task.id,
                TaskNode.sandbox_id == task.sandbox_id,
            )
        )
    )
    if all(item.status == "SUCCEEDED" for item in nodes):
        transition_task(task, "SUCCEEDED")
    else:
        transition_task(task, "PARTIAL")


def mark_node_unknown(task: Task, node: TaskNode, reason: str) -> None:
    if node.status != "RUNNING":
        raise DomainError("NODE_NOT_RUNNING", "只有执行中的节点可标记结果未知", 409)
    node.status = "UNKNOWN"
    node.version += 1
    node.result_json = {"reason": reason, "requires_query": True}
    transition_task(task, "UNKNOWN")
