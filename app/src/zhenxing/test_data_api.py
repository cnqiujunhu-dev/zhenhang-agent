"""DEV_TEST's own synthetic fixtures; never a customer/admin bank write API."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, Query
from pydantic import Field
from sqlalchemy import func, select

from .access_models import ScenarioAdjustmentLedger, ScenarioEditEvent, ScenarioRowVersion
from .accounts import require_account_role
from .contracts import DomainError, StrictModel
from .extensions import reserved_goal_cash_minor
from .models import Account, Card, RiskProfile, Subscription, Task, Transaction, utcnow
from .runtime_models import RuntimeOperation
from .simulation import as_utc, business_now
from .resource_defaults import ARCHIVED_RESOURCE_STATUS


EDITABLE_FIELDS = {
    "account": ["balance_minor"],
    "history": ["amount_minor", "merchant", "category", "flag", "occurred_at"],
    "card": ["status", "limit_minor"],
    "subscription": ["merchant", "amount_minor", "status", "next_due_at"],
    "risk_profile": ["risk_level", "expires_at"],
}


def _row_version(session, principal, entity, row_id, create=False):
    row = session.scalar(select(ScenarioRowVersion).where(ScenarioRowVersion.sandbox_id == principal.sandbox_id,
        ScenarioRowVersion.user_id == principal.user_id, ScenarioRowVersion.entity == entity,
        ScenarioRowVersion.row_id == row_id).with_for_update())
    if row is None and create:
        row = ScenarioRowVersion(sandbox_id=principal.sandbox_id, user_id=principal.user_id, entity=entity, row_id=row_id)
        session.add(row)
        session.flush()
    return row


def _version(session, principal, entity, row):
    if hasattr(row, "version"):
        return row.version
    version = _row_version(session, principal, entity, row.id)
    return version.version if version else 1


def _time(value, label):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return as_utc(result)
    except (ValueError, TypeError, AttributeError):
        raise DomainError("INVALID_TEST_TIME", f"{label}必须为带时区ISO时间", 422) from None


def _integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DomainError("INVALID_TEST_INTEGER", f"{label}须为允许范围内整数", 422)
    return value


def _safe_text(value, maximum, label):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DomainError("INVALID_TEST_TEXT", f"{label}不能为空且长度不能超过{maximum}", 422)
    return value.strip()


def _assert_idle(session, principal):
    if session.scalar(select(Task.id).where(Task.sandbox_id == principal.sandbox_id, Task.user_id == principal.user_id,
                                           Task.state.in_(["EXECUTING", "AUTHORIZED", "UNKNOWN"])).limit(1)):
        raise DomainError("TEST_DATA_WORKFLOW_BUSY", "有执行中或结果未知的操作，请先确定结果再编辑测试数据", 409)
    if session.scalar(select(RuntimeOperation.id).join(Task, Task.id == RuntimeOperation.parent_task_id).where(
        RuntimeOperation.sandbox_id == principal.sandbox_id, Task.user_id == principal.user_id,
        RuntimeOperation.active == 1, RuntimeOperation.status.in_(["RUNNING", "AUTHORIZED", "UNKNOWN"])).limit(1)):
        raise DomainError("TEST_DATA_WORKFLOW_BUSY", "有执行中或结果未知的操作，请先确定结果再编辑测试数据", 409)


def get_test_data(session, principal, history_page=1, history_page_size=50):
    require_account_role(session, principal, {"DEV_TEST"})
    sid, uid = principal.sandbox_id, principal.user_id
    account = session.scalar(select(Account).where(Account.sandbox_id == sid, Account.user_id == uid))
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "测试账套资金账户不存在", 404)
    history_query = select(Transaction).where(Transaction.sandbox_id == sid, Transaction.account_id == account.id,
                                             Transaction.source_kind == "HISTORY", Transaction.operation_ref.is_(None))
    total = session.scalar(select(func.count()).select_from(history_query.subquery()))
    history = list(session.scalars(history_query.order_by(Transaction.occurred_at.desc(), Transaction.id)
        .offset((history_page-1)*history_page_size).limit(history_page_size)))
    profile = session.scalar(select(RiskProfile).where(RiskProfile.sandbox_id == sid, RiskProfile.user_id == uid))
    return {"scope": "OWN_SYNTHETIC_TEST_DATA", "role": "DEV_TEST", "editable_fields": EDITABLE_FIELDS,
            "account": {"row_id": account.id, "name": account.name, "balance_minor": account.balance_minor,
                        "currency": account.currency, "version": account.version},
            "history": [{"row_id": row.id, "amount_minor": row.amount_minor, "merchant": row.merchant,
                         "category": row.category, "flag": row.flag, "occurred_at": as_utc(row.occurred_at).isoformat(),
                         "source_kind": row.source_kind, "version": _version(session, principal, "history", row)} for row in history],
            "history_page": history_page, "history_page_size": history_page_size, "history_total": total,
            "cards": [{"row_id": row.id, "last4": row.last4, "status": row.status, "limit_minor": row.limit_minor,
                       "version": row.version} for row in session.scalars(select(Card).where(Card.sandbox_id == sid,
                           Card.user_id == uid, Card.status != ARCHIVED_RESOURCE_STATUS))],
            "subscriptions": [{"row_id": row.id, "merchant": row.merchant, "amount_minor": row.amount_minor,
                    "source_type": row.source_type, "status": row.status, "next_due_at": as_utc(row.next_due_at).isoformat(),
                    "version": row.version} for row in session.scalars(select(Subscription).where(Subscription.sandbox_id == sid,
                        Subscription.user_id == uid, Subscription.status != ARCHIVED_RESOURCE_STATUS))],
            "risk_profile": {"row_id": profile.id, "risk_level": profile.risk_level,
                    "expires_at": as_utc(profile.expires_at).isoformat(), "version": _version(session, principal, "risk_profile", profile)} if profile else None,
            "business_time": business_now(session, sid).isoformat(),
            "recent_edits": [{"edit_event_id": row.id, "entity": row.entity, "row_id": row.row_id,
                "previous_version": row.previous_version, "resulting_version": row.resulting_version,
                "fields": list(row.changes_json), "created_at": as_utc(row.created_at).isoformat()}
                for row in session.scalars(select(ScenarioEditEvent).where(ScenarioEditEvent.sandbox_id == sid,
                    ScenarioEditEvent.user_id == uid).order_by(ScenarioEditEvent.created_at.desc()).limit(20))]}


def edit_test_data(session, principal, entity, row_id, expected_version, changes, reason):
    actor = require_account_role(session, principal, {"DEV_TEST"}, lock=True)
    if entity not in EDITABLE_FIELDS or not isinstance(changes, dict) or not changes \
            or any(field not in EDITABLE_FIELDS[entity] for field in changes):
        raise DomainError("TEST_DATA_FIELD_NOT_ALLOWED", "仅允许已公布白名单字段；不能提交SQL、身份或回执字段", 422)
    reason = _safe_text(reason, 200, "编辑原因")
    sid, uid = principal.sandbox_id, principal.user_id
    # Serializes fixture mutation against all bank cash writes for this user.
    account = session.scalar(select(Account).where(Account.sandbox_id == sid, Account.user_id == uid).with_for_update())
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "测试资金账户不存在", 404)
    _assert_idle(session, principal)
    model = {"account": Account, "history": Transaction, "card": Card,
             "subscription": Subscription, "risk_profile": RiskProfile}[entity]
    query = select(model).where(model.id == row_id, model.sandbox_id == sid)
    query = query.where(model.account_id == account.id) if entity == "history" else query.where(model.user_id == uid)
    row = session.scalar(query.with_for_update())
    if row is None:
        raise DomainError("TEST_DATA_ROW_NOT_FOUND", "该记录不属于当前测试主体", 404)
    if entity in {"card", "subscription"} and row.status == ARCHIVED_RESOURCE_STATUS:
        raise DomainError("TEST_DATA_RESOURCE_ARCHIVED", "归档记录保留历史，不可通过测试编辑重新启用", 409)
    if entity == "history" and (row.source_kind != "HISTORY" or row.operation_ref is not None):
        raise DomainError("IMMUTABLE_BANK_RECEIPT", "实际业务资金流水与已完成回执不能作为测试历史修改", 403)
    version_state = _row_version(session, principal, entity, row.id, create=True) if entity in {"history", "risk_profile"} else None
    version = row.version if hasattr(row, "version") else version_state.version
    if expected_version != version:
        raise DomainError("TEST_DATA_VERSION_CHANGED", "数据版本已变化，请刷新后编辑", 409)
    parsed = {}
    for field, value in changes.items():
        if field == "balance_minor":
            parsed[field] = _integer(value, 0, 2_000_000_000, "余额")
        elif field == "amount_minor":
            parsed[field] = _integer(value, -50_000_000 if entity == "history" else 1, 50_000_000, "金额")
        elif field == "limit_minor":
            parsed[field] = _integer(value, 10000, 2000000, "卡片限额")
        elif field == "risk_level":
            parsed[field] = _integer(value, 1, 5, "风险等级")
        elif field in {"occurred_at", "expires_at", "next_due_at"}:
            parsed[field] = _time(value, field)
            if field == "occurred_at" and parsed[field] > business_now(session, sid):
                raise DomainError("FUTURE_HISTORY_NOT_POSTED", "未来历史记录不能先行改变现金余额", 422)
        elif field == "status":
            allowed = {"ACTIVE", "LOCKED", "LOST"} if entity == "card" else {"ACTIVE", "CANCELLED"}
            if value not in allowed:
                raise DomainError("INVALID_TEST_STATUS", "测试状态不在白名单", 422)
            parsed[field] = value
        elif field == "flag":
            if value not in {"NORMAL", "ANOMALY"}:
                raise DomainError("INVALID_TEST_FLAG", "测试标记不在白名单", 422)
            parsed[field] = value
        else:
            parsed[field] = _safe_text(value, 200 if field == "merchant" and entity == "history" else 80, field)
    old_values = {field: getattr(row, field) for field in parsed}
    delta = parsed.get("balance_minor", account.balance_minor) - account.balance_minor if entity == "account" else (
        parsed.get("amount_minor", row.amount_minor) - row.amount_minor if entity == "history" else 0)
    if account.balance_minor + delta < reserved_goal_cash_minor(session, sid, uid, account.id) or account.balance_minor + delta < 0:
        raise DomainError("TEST_DATA_RESERVATION_BREACH", "该变更会侵占已预留目标或使现金为负", 409)
    for field, value in parsed.items():
        setattr(row, field, value)
    if hasattr(row, "version"):
        row.version += 1
        result_version = row.version
    else:
        version_state.version += 1
        result_version = version_state.version
    if entity == "history" and delta:
        account.balance_minor += delta
        account.version += 1
    serial_changes = {field: {"before": as_utc(old_values[field]).isoformat() if isinstance(old_values[field], datetime) else old_values[field],
                             "after": value.isoformat() if isinstance(value, datetime) else value} for field, value in parsed.items()}
    edit = ScenarioEditEvent(sandbox_id=sid, user_id=uid, actor_account_id=actor.id, entity=entity, row_id=row.id,
                            previous_version=version, resulting_version=result_version, reason=reason, changes_json=serial_changes)
    session.add(edit)
    session.flush()
    if entity == "account" and delta:
        session.add_all([
            ScenarioAdjustmentLedger(edit_event_id=edit.id, sandbox_id=sid, user_id=uid, account_ref=account.id, delta_minor=delta),
            ScenarioAdjustmentLedger(edit_event_id=edit.id, sandbox_id=sid, user_id=uid,
                                     account_ref=f"DEV_TEST_CLEARING:{sid}", delta_minor=-delta),
        ])
        session.add(Transaction(sandbox_id=sid, account_id=account.id, card_id=None, occurred_at=business_now(session, sid),
            amount_minor=delta, merchant="测试场景现金调整", category="测试控制", flag="NORMAL",
            source_kind="DEV_TEST_ADJUSTMENT", operation_ref=f"test_adjust:{edit.id}"))
    return {"edited": True, "synthetic": True, "entity": entity, "row_id": row.id, "version": result_version,
            "edit_event_id": edit.id, "fields": list(parsed), "cash_delta_minor": delta,
            "account_balance_minor": account.balance_minor, "account_version": account.version,
            "journal_balanced": True, "bank_receipts_changed": False}


class TestDataPatch(StrictModel):
    expected_version: int = Field(ge=1)
    changes: dict
    reason: str = Field(min_length=1, max_length=200)


def install_test_data_routes(app, get_session, get_principal):
    @app.get("/api/v2/test-data")
    def data(history_page: int = Query(default=1, ge=1), history_page_size: int = Query(default=50, ge=1, le=200),
             session=Depends(get_session), principal=Depends(get_principal)):
        return get_test_data(session, principal, history_page, history_page_size)

    @app.patch("/api/v2/test-data/{entity}/{row_id}")
    def edit(entity: str, row_id: str, payload: TestDataPatch, session=Depends(get_session), principal=Depends(get_principal)):
        result = edit_test_data(session, principal, entity, row_id, payload.expected_version, payload.changes, payload.reason)
        session.commit()
        return result
