from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import DomainError
from .models import (
    Conversation,
    ConversationMessage,
    EvidenceSnapshot,
    ModelInvocation,
    PolicyDecisionRecord,
    TaskNode,
    UserPreference,
)


TOOL_REGISTRY = {
    "bill.analyze.v1": {
        "read_only": True,
        "risk_level": "L1",
        "owner": "banking.bills",
    },
    "card.lock.v1": {
        "read_only": False,
        "risk_level": "L2",
        "owner": "banking.cards",
    },
    "card.status.v1": {
        "read_only": True,
        "risk_level": "L1",
        "owner": "banking.cards",
    },
    "card.manage.v1": {
        "read_only": False,
        "risk_level": "L2_TO_L4",
        "owner": "banking.cards",
    },
    "transfer.validate.v1": {
        "read_only": True,
        "risk_level": "L1",
        "owner": "banking.transfers",
    },
    "transfer.commit.v1": {
        "read_only": False,
        "risk_level": "L3",
        "owner": "banking.transfers",
    },
    "transfer.schedule.v1": {"read_only": False, "risk_level": "L3", "owner": "banking.transfers"},
    "transfer.aa.v1": {"read_only": False, "risk_level": "L2", "owner": "banking.transfers"},
    "subscription.list.v1": {
        "read_only": True,
        "risk_level": "L1",
        "owner": "banking.subscriptions",
    },
    "subscription.cancel.v1": {
        "read_only": False,
        "risk_level": "L2",
        "owner": "banking.subscriptions",
    },
    "subscription.start.v1": {"read_only": False, "risk_level": "L3", "owner": "banking.subscriptions"},
    "subscription.renew.v1": {"read_only": False, "risk_level": "L3", "owner": "banking.subscriptions"},
    "wealth.compare.v1": {
        "read_only": True,
        "risk_level": "L1",
        "owner": "banking.wealth",
    },
    "wealth.order.v1": {
        "read_only": False,
        "risk_level": "L3",
        "owner": "banking.wealth",
    },
}

TOOL_REGISTRY.update({
    "asset.positions.v1": {"read_only": True, "risk_level": "L1", "owner": "banking.assets"},
    "contact.bind.v1": {"read_only": False, "risk_level": "L3", "owner": "banking.contacts"},
    "fx.quote.v1": {"read_only": True, "risk_level": "L1", "owner": "banking.fx"},
    "fx.transfer.v1": {"read_only": False, "risk_level": "L3", "owner": "banking.fx"},
    "fx.exchange.v1": {"read_only": False, "risk_level": "L3", "owner": "banking.fx"},
    "fraud.analyze.v1": {"read_only": True, "risk_level": "L1", "owner": "extensions.fraud"},
    **{f"family.{verb}.v1": {"read_only": verb == "view", "risk_level": "L1" if verb == "view" else "L3",
                           "owner": "extensions.family"} for verb in ("view", "grant", "revoke", "propose")},
    **{f"goal.{verb}.v1": {"read_only": verb == "view", "risk_level": "L3" if verb in {"reserve", "release"} else "L2",
                         "owner": "extensions.goals"} for verb in ("view", "create", "reserve", "release")},
    **{f"carbon.{verb}.v1": {"read_only": verb == "view", "risk_level": "L1" if verb == "view" else "L2",
                           "owner": "extensions.carbon"} for verb in ("view", "claim", "redeem")},
})

from .customer_profile_common import TOOL_DEFINITIONS as CUSTOMER_PROFILE_TOOLS
TOOL_REGISTRY.update(CUSTOMER_PROFILE_TOOLS)
from .upgraded_capabilities import TOOL_DEFINITIONS as UPGRADED_TOOLS
for _tool, _definition in UPGRADED_TOOLS.items():
    TOOL_REGISTRY.setdefault(_tool, _definition)


def get_or_create_conversation(session: Session, sandbox_id: str, user_id: str) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.sandbox_id == sandbox_id,
            Conversation.user_id == user_id,
            Conversation.channel == "WEB",
        )
    )
    if conversation is None:
        conversation = Conversation(sandbox_id=sandbox_id, user_id=user_id, channel="WEB")
        session.add(conversation)
        session.flush()
    return conversation


def append_message(
    session: Session,
    *,
    conversation_id: str,
    sandbox_id: str,
    task_id: str | None,
    role: str,
    content: str,
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=conversation_id,
        sandbox_id=sandbox_id,
        task_id=task_id,
        role=role,
        content=content,
    )
    session.add(message)
    session.flush()
    return message


def record_model_invocation(
    session: Session,
    *,
    task_id: str,
    sandbox_id: str,
    provider_label: str,
) -> None:
    session.add(
        ModelInvocation(
            task_id=task_id,
            sandbox_id=sandbox_id,
            provider_label=provider_label,
            purpose="INTENT_CLASSIFICATION",
            outcome="FALLBACK" if "fallback" in provider_label else "SUCCESS",
        )
    )


def record_bill_evidence(session: Session, *, task_id: str, sandbox_id: str, payload: dict) -> str:
    from .simulation_models import SimulationState
    simulation = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == sandbox_id))
    snapshot = EvidenceSnapshot(
        task_id=task_id,
        sandbox_id=sandbox_id,
        source_kind="SYNTHETIC_TRANSACTION_AGGREGATE",
        source_version=simulation.seed_version if simulation else "seed-v1",
        payload_json=payload,
    )
    session.add(snapshot)
    session.flush()
    return snapshot.id


def add_task_nodes(session: Session, *, task_id: str, sandbox_id: str, plan: dict) -> None:
    validate_plan(plan, task_id=task_id, sandbox_id=sandbox_id)
    for node in plan.get("nodes", []):
        session.add(
            TaskNode(
                task_id=task_id,
                sandbox_id=sandbox_id,
                node_key=node["node_id"],
                tool_name=node["tool_name"],
                status=node.get("status", "WAITING_CONFIRMATION"),
                depends_on_json=node.get("depends_on", []),
                result_json={},
            )
        )


def validate_plan(plan: dict, *, task_id: str, sandbox_id: str) -> None:
    if not isinstance(plan, dict) or not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
        raise DomainError("PLAN_EMPTY", "任务计划必须包含节点", 409)
    if len(plan["nodes"]) > 32:
        raise DomainError("PLAN_TOO_LARGE", "任务计划节点数量超过上限", 409)
    if plan.get("task_id", task_id) != task_id or plan.get("sandbox_id", sandbox_id) != sandbox_id:
        raise DomainError("PLAN_SCOPE_MISMATCH", "计划所属任务或沙箱不一致", 409)
    by_key = {}
    for node in plan["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("node_id"), str) \
                or not node["node_id"] or len(node["node_id"]) > 80 or node["node_id"] in by_key:
            raise DomainError("PLAN_DUPLICATE_NODE", "任务计划节点无效或重复", 409)
        tool = TOOL_REGISTRY.get(node.get("tool_name"))
        if tool is None:
            raise DomainError("PLAN_UNKNOWN_TOOL", "任务计划引用未登记工具", 409)
        if node.get("read_only", tool["read_only"]) != tool["read_only"]:
            raise DomainError("PLAN_TOOL_MODE_MISMATCH", "工具读写属性与登记不一致", 409)
        if tool["read_only"] and node.get("status") != "SUCCEEDED":
            raise DomainError("PLAN_READ_NOT_EXECUTED", "只读节点必须先执行并提供结果", 409)
        if not tool["read_only"]:
            if node.get("status", "WAITING_CONFIRMATION") != "WAITING_CONFIRMATION" \
                    or node.get("requires_confirmation") is not True:
                raise DomainError("PLAN_UNSAFE_WRITE", "可写节点必须等待逐次确认", 409)
        dependencies = node.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise DomainError("PLAN_INVALID_DEPENDENCY", "节点依赖格式无效", 409)
        by_key[node["node_id"]] = dependencies
    visiting = set()
    visited = set()

    def visit(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            raise DomainError("PLAN_CYCLE", "任务计划存在循环依赖", 409)
        visiting.add(key)
        for dependency in by_key[key]:
            if dependency not in by_key:
                raise DomainError("PLAN_MISSING_DEPENDENCY", "任务计划引用不存在的节点", 409)
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for node_key in by_key:
        visit(node_key)


def record_policy(
    session: Session,
    *,
    task_id: str,
    sandbox_id: str,
    node_key: str,
    decision: str,
    reason_codes: list[str],
    policy_version: str = "card-lock-v1",
) -> None:
    session.add(
        PolicyDecisionRecord(
            task_id=task_id,
            sandbox_id=sandbox_id,
            node_key=node_key,
            decision=decision,
            reason_codes_json=reason_codes,
            policy_version=policy_version,
        )
    )


def task_nodes(session: Session, *, task_id: str, sandbox_id: str) -> list[TaskNode]:
    return list(
        session.scalars(
            select(TaskNode)
            .where(TaskNode.task_id == task_id, TaskNode.sandbox_id == sandbox_id)
            .order_by(TaskNode.node_key)
        )
    )


def conversation_messages(session: Session, *, sandbox_id: str, user_id: str) -> list[dict]:
    conversation = get_or_create_conversation(session, sandbox_id, user_id)
    rows = session.scalars(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.sandbox_id == sandbox_id,
        )
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
    )
    return [
        {
            "message_id": item.id,
            "task_id": item.task_id,
            "role": item.role,
            "content": item.content,
            "created_at": item.created_at.isoformat(),
        }
        for item in rows
    ]


def save_preference(
    session: Session,
    *,
    sandbox_id: str,
    user_id: str,
    key: str,
    value: str,
) -> dict:
    allowed_values = {
        "display_language": {"zh-CN", "en"},
        "notification_level": {"important", "all", "off"},
    }
    if key not in allowed_values or value not in allowed_values[key]:
        raise ValueError("Unsupported preference value")
    preference = session.scalar(
        select(UserPreference).where(
            UserPreference.sandbox_id == sandbox_id,
            UserPreference.user_id == user_id,
            UserPreference.key == key,
        )
    )
    if preference is None:
        preference = UserPreference(
            sandbox_id=sandbox_id,
            user_id=user_id,
            key=key,
            value_json={"value": value},
        )
        session.add(preference)
    else:
        preference.value_json = {"value": value}
    return {"key": key, "value": value}


def list_preferences(session: Session, *, sandbox_id: str, user_id: str) -> list[dict]:
    rows = session.scalars(
        select(UserPreference).where(
            UserPreference.sandbox_id == sandbox_id,
            UserPreference.user_id == user_id,
        )
    )
    return [{"key": item.key, "value": item.value_json["value"]} for item in rows]
