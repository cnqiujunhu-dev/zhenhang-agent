from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contracts import DomainError
from .models import Card, Confirmation, Task, utcnow
from .runtime_models import AuthChallenge, RuntimeRecord


STEP_UP_TOOLS = frozenset({
    "subscription.start.v1", "subscription.renew.v1",
    "credit.repay.v1", "credit.installment.v1", "credit.settle_installment.v1",
    "credit.apply.v1", "credit.limit_request.v1", "credit.activate.v1", "credit.card.control.v1",
    "life.plan.authorize.v1", "merchant.purchase.v1",
    "own.account.transfer.v1",
})


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def requires_step_up(session: Session, task: Task, confirmation: Confirmation) -> bool:
    if confirmation.action_type in STEP_UP_TOOLS:
        return True  # New paid capabilities never inherit the old v1 auth exemption.
    managed = session.get(RuntimeRecord, task.id)
    if managed is None or managed.kind != "CHILD":
        return False  # v1 compatibility fixture; v2 always uses managed children.
    if task.intent_type in {"TRANSFER", "TRANSFER_SCHEDULE", "WEALTH", "FOREX_TRANSFER", "FOREIGN_EXCHANGE", "CONTACT_MANAGEMENT"}:
        return True
    if task.intent_type in {"FAMILY_ASSISTANCE", "GOAL_CASHFLOW"}:
        return confirmation.node_key in {"family_grant", "goal_reserve", "goal_release"}
    if task.intent_type == "CARD_MANAGEMENT":
        action = next((item for item in task.plan_json.get("nodes", [])
                       if item["node_id"] == confirmation.node_key), {})
        if action.get("action") in {"UNLOCK", "REPORT_LOSS", "REQUEST_REPLACEMENT"}:
            return True
        if action.get("action") == "SET_LIMIT":
            card = session.get(Card, confirmation.target_id)
            return card is not None and action.get("limit_minor", 0) > card.limit_minor
    return False


def _digest(code: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), 120_000).hex()


def create_challenge(session: Session, *, task: Task, confirmation: Confirmation, user_id: str) -> dict:
    if task.user_id != user_id or confirmation.task_id != task.id or confirmation.status != "PENDING" \
            or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}:
        raise DomainError("AUTH_TARGET_INVALID", "认证目标不是当前待确认动作", 409)
    if confirmation.plan_hash != task.plan_hash or as_utc(confirmation.expires_at) <= utcnow():
        raise DomainError("CONFIRMATION_EXPIRED", "当前确认已经失效", 409)
    recent = session.scalar(select(func.count()).select_from(AuthChallenge).where(
        AuthChallenge.confirmation_id == confirmation.id,
        AuthChallenge.created_at >= utcnow() - timedelta(minutes=3)))
    if recent >= 3:
        raise DomainError("AUTH_CHALLENGE_RATE_LIMIT", "认证请求过于频繁，请稍后再试", 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    challenge = AuthChallenge(
        sandbox_id=task.sandbox_id, user_id=user_id, task_id=task.id,
        confirmation_id=confirmation.id, node_key=confirmation.node_key,
        plan_hash=task.plan_hash, code_hash=_digest(code, salt), salt=salt,
        expires_at=utcnow() + timedelta(minutes=3),
    )
    session.add(challenge)
    session.flush()
    return {"challenge_id": challenge.id, "provider": "SIMULATION", "demo_code": code,
            "expires_at": challenge.expires_at.isoformat(),
            "message": "当前代码用于本次确认流程，不代表独立身份认证。"}


def verify_challenge(session: Session, *, challenge_id: str, user_id: str, sandbox_id: str, code: str) -> dict:
    challenge = session.scalar(select(AuthChallenge).where(
        AuthChallenge.id == challenge_id, AuthChallenge.user_id == user_id,
        AuthChallenge.sandbox_id == sandbox_id,
    ).with_for_update())
    if challenge is None:
        raise DomainError("AUTH_CHALLENGE_NOT_FOUND", "认证挑战不存在", 404)
    if challenge.status != "PENDING" or challenge.attempts >= 5 or as_utc(challenge.expires_at) <= utcnow():
        return {"verified": False, "error": "AUTH_CHALLENGE_INVALID"}
    task = session.get(Task, challenge.task_id)
    confirmation = session.get(Confirmation, challenge.confirmation_id)
    if task is None or confirmation is None or task.plan_hash != challenge.plan_hash \
            or confirmation.status != "PENDING" or as_utc(confirmation.expires_at) <= utcnow():
        challenge.status = "REVOKED"
        return {"verified": False, "error": "AUTH_PLAN_CHANGED"}
    challenge.attempts += 1
    if not secrets.compare_digest(challenge.code_hash, _digest(code, challenge.salt)):
        if challenge.attempts >= 5:
            challenge.status = "LOCKED"
        return {"verified": False, "error": "AUTH_CODE_INVALID", "attempts_remaining": 5 - challenge.attempts}
    challenge.status = "VERIFIED"
    challenge.verified_at = utcnow()
    return {"verified": True, "challenge_id": challenge.id, "provider": "SIMULATION"}


def consume_auth(session: Session, *, task: Task, confirmation: Confirmation,
                 challenge_id: str | None, user_id: str, consume: bool = True) -> None:
    if not requires_step_up(session, task, confirmation):
        return
    challenge = session.scalar(select(AuthChallenge).where(
        AuthChallenge.id == challenge_id, AuthChallenge.task_id == task.id,
        AuthChallenge.sandbox_id == task.sandbox_id, AuthChallenge.user_id == user_id,
        AuthChallenge.confirmation_id == confirmation.id,
    ).with_for_update()) if challenge_id else None
    if challenge is None:
        raise DomainError("STEP_UP_REQUIRED", "请先完成二次验证", 403)
    if challenge.status != "VERIFIED" or challenge.plan_hash != task.plan_hash \
            or challenge.node_key != confirmation.node_key or as_utc(challenge.expires_at) <= utcnow():
        raise DomainError("AUTH_GRANT_INVALID", "认证已失效、已消费或与当前计划不符", 403)
    if consume:
        challenge.status = "CONSUMED"
        challenge.consumed_at = utcnow()
