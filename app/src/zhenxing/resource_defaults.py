"""Compact customer defaults and explicit, reversible local resource retirement.

Not a banking tool or startup sweep: archival is invoked only by a maintenance
command after human authorization and a quiescent-service check. Historical FK
rows, money, account/card identifiers and committed receipts remain intact.
"""
from sqlalchemy import select

from .access_models import AccessAudit
from .contracts import DomainError
from .fx_models import DualCurrencyProfile
from .models import Card, Subscription, Task

ARCHIVED_RESOURCE_STATUS = "ARCHIVED"
RESOURCE_DEFAULTS_REVISION = "two-cards-two-subscriptions-v1"
DEFAULT_CARD_COUNT = 2
DEFAULT_SUBSCRIPTION_MERCHANTS = ("视频会员", "云存储")
TERMINAL_TASK_STATES = frozenset({"SUCCEEDED", "FAILED", "CLOSED", "CANCELLED", "CANCELED", "SUPERSEDED"})


def archive_surplus_customer_resources(session, login):
    """Caller owns transaction; never commit partial account or financial changes."""
    if login.role not in {"CUSTOMER", "DEV_TEST"}:
        raise DomainError("CUSTOMER_RESOURCE_SCOPE_REQUIRED", "仅业务账户有卡片和订阅默认资源", 403)
    if session.scalar(select(Task.id).where(Task.sandbox_id == login.sandbox_id,
            Task.user_id == login.user_id, Task.state.not_in(TERMINAL_TASK_STATES)).limit(1)):
        raise DomainError("RESOURCE_MAINTENANCE_BUSY", "有未结束办理，不能调整默认资源", 409)
    cards = list(session.scalars(select(Card).where(Card.sandbox_id == login.sandbox_id,
        Card.user_id == login.user_id, Card.status != ARCHIVED_RESOURCE_STATUS).order_by(Card.id)
        .with_for_update().execution_options(populate_existing=True)))
    subscriptions = list(session.scalars(select(Subscription).where(Subscription.sandbox_id == login.sandbox_id,
        Subscription.user_id == login.user_id, Subscription.status != ARCHIVED_RESOURCE_STATUS).order_by(Subscription.id)
        .with_for_update().execution_options(populate_existing=True)))
    if len(cards) < DEFAULT_CARD_COUNT:
        raise DomainError("RESOURCE_DEFAULTS_INCOMPLETE", "现有卡片不足两张，未自动补造或重置资源", 409)
    dual = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.sandbox_id == login.sandbox_id,
        DualCurrencyProfile.user_id == login.user_id))
    keep = []
    if dual:
        bound = next((card for card in cards if card.id == dual.card_id), None)
        if bound is None:
            raise DomainError("RESOURCE_DUAL_BINDING_UNAVAILABLE", "双币卡绑定异常，未归档或重绑美元钱包", 409)
        keep.append(bound)
    # Retain the existing primary CNY card even when locked/lost: do not turn
    # retirement into a reset of a customer's security choice.
    for tail in ("4321", "6672"):
        for card in cards:
            if card.last4 == tail and card not in keep and len(keep) < DEFAULT_CARD_COUNT:
                keep.append(card)
    for card in cards:
        if len(keep) < DEFAULT_CARD_COUNT and card not in keep:
            keep.append(card)
    retained_subscriptions = []
    for merchant in DEFAULT_SUBSCRIPTION_MERCHANTS:
        matches = [row for row in subscriptions if row.merchant == merchant]
        if len(matches) != 1:
            raise DomainError("RESOURCE_DEFAULT_SUBSCRIPTION_UNRESOLVED", "默认订阅不唯一或缺失，未创建或恢复扣费协议", 409)
        retained_subscriptions.append(matches[0])
    changes = []
    for kind, rows, retained in (("CARD", cards, keep), ("SUBSCRIPTION", subscriptions, retained_subscriptions)):
        for row in rows:
            if row in retained:
                continue
            changes.append({"kind": kind, "resource_id": row.id, "previous_status": row.status,
                            "previous_version": row.version, "archived_version": row.version + 1})
            row.status = ARCHIVED_RESOURCE_STATUS
            row.version += 1
    if changes:
        session.add(AccessAudit(target_account_id=login.id, action="CUSTOMER_RESOURCES_ARCHIVED",
            payload_json={"revision": RESOURCE_DEFAULTS_REVISION, "authority": "EXPLICIT_LOCAL_USER_MAINTENANCE",
                "reason": "USER_REQUESTED_TWO_CARDS_TWO_DEFAULT_SUBSCRIPTIONS", "changes": changes,
                "retained_card_ids": [row.id for row in keep], "retained_subscription_ids": [row.id for row in retained_subscriptions],
                "historical_rows_deleted": False, "balances_changed": False}))
    session.flush()
    return {"username": login.username, "retained_cards": len(keep), "retained_subscriptions": len(retained_subscriptions),
            "archived_cards": sum(row["kind"] == "CARD" for row in changes),
            "archived_subscriptions": sum(row["kind"] == "SUBSCRIPTION" for row in changes), "changes": changes,
            "retained_card_ids": [row.id for row in keep], "retained_subscription_ids": [row.id for row in retained_subscriptions]}
