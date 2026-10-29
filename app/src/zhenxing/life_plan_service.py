"""One bounded birthday occurrence, confirmed once, never a universal mandate."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import os

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .credit_atomic import atomic_domain
from .customer_profile_common import require_profile_actor
from .life_plan_models import LifePlan, LifePlanJob, LifePlanMandate, LifePlanOccurrence
from .merchant_models import MerchantOrder
from .merchant_service import orders_view, quote_products
from .models import Account, Confirmation, Task, ToolCall, utcnow
from .occasion_service import occurrence_date, owned_occasion
from .planning import canonical_hash


AUTH_TOOL = "life.plan.authorize.v1"
SCOPE_VERSION = "bounded-birthday-plan-v2"
FINISHED = {"SUCCEEDED", "CANCELLED", "COMPENSATED"}
DEFAULT_BUDGET_MINOR = 100000
HARD_BUDGET_LIMIT_MINOR = 100000000


def configured_budget_limit():
    raw = os.getenv("ZX_LIFE_PLAN_MAX_BUDGET_MINOR", "1000000")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= HARD_BUDGET_LIMIT_MINOR:
        raise DomainError("LIFE_PLAN_BUDGET_CONFIG_INVALID", "生活计划预算上限配置暂不可用，请稍后再试。", 503)
    return int(raw)


def scope_budget_valid(scope):
    """An old scope keeps its old ceiling, even after configuration changes."""
    limit = scope.get("budget_limit_minor", DEFAULT_BUDGET_MINOR)
    return (type(limit) is int and 1 <= limit <= HARD_BUDGET_LIMIT_MINOR
        and type(scope.get("budget_minor")) is int and 0 < scope["budget_minor"] <= limit)


def schedule_options(birthday, zone, delivery_start, now):
    """Concrete proposals only; selecting and confirming one grants authority."""
    candidates = [(datetime.combine(birthday - timedelta(days=2), time(8), zone),
        datetime.combine(birthday - timedelta(days=2), time(9), zone))]
    candidates.append((now + timedelta(minutes=5), now + timedelta(minutes=10)))
    result = []
    for reserve, order in candidates:
        reserve, order = as_utc(reserve), as_utc(order)
        if now < reserve <= order < delivery_start:
            value = {"life_plan_reserve_at": reserve.astimezone(zone).isoformat(),
                "life_plan_order_at": order.astimezone(zone).isoformat(), "requires_new_confirmation": True}
            if value not in result: result.append(value)
    return result


def _schedule_error(code, message, birthday, zone, start, now):
    options = schedule_options(birthday, zone, start, now)
    if options:
        message += " 可选择：" + "；".join(f"{row['life_plan_reserve_at']}预留、{row['life_plan_order_at']}下单" for row in options)
        message += "。请明确采用的两个时点，再核对完整计划重新确认。"
    else:
        message += " 当前配送窗前已无可执行的准备时间，请明确更晚的本次配送时间并重新确认；生日资料不变。"
    error = DomainError(code, message, 422)
    error.schedule_options = options
    raise error


def _principal(plan):
    return SimpleNamespace(user_id=plan.user_id, sandbox_id=plan.sandbox_id, family_context=None)


def owned_plan(session, principal, plan_id, *, lock=False):
    # The same owner lock used by cash/credit must precede every plan lock.
    require_profile_actor(session, principal, lock=lock)
    query = select(LifePlan).where(LifePlan.id == plan_id, LifePlan.user_id == principal.user_id,
        LifePlan.sandbox_id == principal.sandbox_id)
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None:
        raise DomainError("LIFE_PLAN_NOT_FOUND", "未找到本人的生活计划。", 404)
    return row


def replacement_source(session, principal, reference, *, lock=False):
    """The owner lock serializes replacement with reserve/order work."""
    if not isinstance(reference, dict) or set(reference) != {"plan_id", "version", "scope_hash"}:
        raise DomainError("LIFE_PLAN_REPLACEMENT_INVALID", "原安排的替换范围不完整，请重新核对。", 409)
    old = owned_plan(session, principal, reference["plan_id"], lock=lock)
    if (type(reference["version"]) is not int or old.version != reference["version"]
            or old.scope_hash != reference["scope_hash"] or canonical_hash(old.scope_json) != old.scope_hash):
        raise DomainError("LIFE_PLAN_REPLACEMENT_CHANGED", "原安排已变化或已结束，请重新核对；本次未替换。", 409)
    if session.scalar(select(MerchantOrder.id).join(LifePlanOccurrence,
            MerchantOrder.occurrence_id == LifePlanOccurrence.id).where(LifePlanOccurrence.plan_id == old.id)):
        raise DomainError("LIFE_PLAN_PAID_ORDER_IMMUTABLE", "原安排已有订单，不能把改期或换商品变成再次付款；请先核对原订单的取消或退款，再明确建立新安排。", 422)
    if old.status not in {"DRAFT", "ACTIVE", "PAUSED_RECONFIRM"}:
        raise DomainError("LIFE_PLAN_REPLACEMENT_CHANGED", "原安排已变化或已结束，请重新核对；本次未替换。", 409)
    return old


def _recipient(value):
    if not isinstance(value, dict) or set(value) != {"name", "phone", "address"}:
        raise DomainError("LIFE_PLAN_RECIPIENT_REQUIRED", "请确认收件人、联系电话和地址。", 422)
    limits = {"name": 80, "phone": 40, "address": 300}
    if any(not isinstance(value[k], str) or not value[k].strip() or len(value[k]) > limits[k] for k in limits):
        raise DomainError("LIFE_PLAN_RECIPIENT_REQUIRED", "请核对必要收件信息。", 422)
    return {k: value[k].strip() for k in limits}


def _aware(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            raise ValueError()
        return as_utc(parsed)
    except (ValueError, TypeError):
        raise DomainError("LIFE_PLAN_TIME_INVALID", "时间必须包含明确时区。", 422) from None


def _make_scope(session, principal, *, occasion_id, year, account_id, card_reference, budget_minor,
        items, recipient, delivery_start, delivery_end, now, reserve_at=None, order_at=None, replaces=None):
    from .payment_instruments import resolve_source_instrument
    from .extensions_models import FamilyMember
    from .family_profile_service import member_display_name
    occasion = owned_occasion(session, principal, occasion_id, lock=True)
    birthday = occurrence_date(occasion, year)
    zone = ZoneInfo(occasion.timezone)
    start, end = _aware(delivery_start), _aware(delivery_end)
    if (start.astimezone(zone).date() != birthday or end <= start or end - start > timedelta(hours=24)
            or end.astimezone(zone).date() > birthday + timedelta(days=1) or start <= now):
        raise DomainError("LIFE_PLAN_DELIVERY_WINDOW_INVALID", "请确认本次生日的未来配送时间范围。", 422)
    from .life_schedule_fields import ON_CONFIRMATION
    on_confirmation=reserve_at==ON_CONFIRMATION
    explicit_reserve, explicit_order = reserve_at is not None and not on_confirmation, order_at is not None
    reserve_at = now if on_confirmation else _aware(reserve_at) if explicit_reserve else as_utc(datetime.combine(birthday.replace(day=1), time.min, zone))
    order_at = _aware(order_at) if explicit_order else as_utc(datetime.combine(birthday - timedelta(days=2), time(9), zone))
    order_window_end = start if explicit_order else as_utc(datetime.combine(birthday - timedelta(days=1), time.min, zone))
    if order_at < reserve_at:
        _schedule_error("LIFE_PLAN_TIME_CONFLICT", "下单早于预留时间，需要明确调整预留或下单安排。", birthday, zone, start, now)
    if now >= order_window_end or order_at >= start:
        _schedule_error("LIFE_PLAN_ORDER_WINDOW_PASSED", "原办理时间已过或不早于配送，请明确新的时间安排。", birthday, zone, start, now)
    if explicit_order and order_at <= now:
        _schedule_error("LIFE_PLAN_ORDER_WINDOW_PASSED", "明确指定的下单时点已经过去。", birthday, zone, start, now)
    if explicit_reserve and reserve_at <= now and replaces is None:
        _schedule_error("LIFE_PLAN_RESERVE_WINDOW_PASSED", "明确指定的预留时点已经过去。", birthday, zone, start, now)
    limit = configured_budget_limit()
    if type(budget_minor) is not int or not 0 < budget_minor <= limit:
        raise DomainError("LIFE_PLAN_BUDGET_INVALID", f"请明确不超过{limit / 100:g}元的本次现金预算；不会自动提高预算。", 422)
    instrument = resolve_source_instrument(session, principal, card_reference, "CNY", purpose="LIFE_PLAN")
    if instrument.get("instrument_type") != "DEBIT" or instrument.get("account_id") != account_id:
        raise DomainError("LIFE_PLAN_CASH_ACCOUNT_REQUIRED", "生日计划只使用本人明确选择的现金付款账户，不自动改用信用卡或其他账户。", 422)
    quoted = quote_products(session, principal, items)
    if sum(row["amount_minor"] for row in quoted) > budget_minor:
        raise DomainError("LIFE_PLAN_BUDGET_EXCEEDED", "指定商品总价超过本次预算，不会减少数量或擅自替换。", 422)
    previous = replacement_source(session, principal, replaces, lock=True) if replaces is not None else None
    scope = {"schema_version": SCOPE_VERSION, "occasion_id": occasion.id, "occasion_version": occasion.version,
        **({"replaces": deepcopy(replaces)} if replaces is not None else {}),
        "family_member_id": occasion.family_member_id, "birthday": birthday.isoformat(), "year": year,
        "family_member_name": member_display_name(session, session.get(FamilyMember, occasion.family_member_id)),
        "timezone": occasion.timezone, "account_id": account_id, "instrument": deepcopy(instrument),
        "budget_minor": budget_minor, "budget_limit_minor": limit, "currency": "CNY", "items": quoted, "recipient": _recipient(recipient),
        "reserve_at": reserve_at.isoformat(), "order_at": order_at.isoformat(),
        "order_window_end": order_window_end.isoformat(),
        "schedule_policy": "EXPLICIT" if on_confirmation or explicit_reserve or explicit_order else "MONTH_START_AND_T_MINUS_2",
        **({"reserve_policy":ON_CONFIRMATION} if on_confirmation else {}),
        "delivery_start": start.isoformat(), "delivery_end": end.isoformat(),
        "expires_at": end.isoformat(), "maximum_occurrences": 1, "recurring": False,
        "replacement_allowed": False, "price_increase_allowed": False,
        "cancellation_policy": "REFUND_UNFULFILLED_TO_ORIGINAL_SOURCE",
        "failure_policy": "REFUND_UNFULFILLED_AND_RELEASE_UNUSED", "unused_budget_policy": "RELEASE_AFTER_COMPLETION"}
    if reserve_at <= now and previous is not None:
        from .life_reservation_replacement import eligible_shape, prepare_marker, SCOPE_VERSION as RESERVED_SCOPE_VERSION
        if eligible_shape(previous, scope):
            scope["schema_version"] = RESERVED_SCOPE_VERSION
            scope["reservation_replacement"] = prepare_marker(session, principal, scope, now)
        elif explicit_reserve:
            _schedule_error("LIFE_PLAN_RESERVE_WINDOW_PASSED", "明确指定的预留时点已经过去。", birthday, zone, start, now)
    return scope


def create_life_plan_draft(session, principal, *, occasion_id, year, account_id, card_reference=None,
        budget_minor=DEFAULT_BUDGET_MINOR, items, recipient, delivery_start, delivery_end, request_key, now=None,
        reserve_at=None, order_at=None, replaces=None):
    require_profile_actor(session, principal, lock=True)
    now = as_utc(now or utcnow())
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 120:
        raise DomainError("LIFE_PLAN_REQUEST_KEY_REQUIRED", "计划请求缺少稳定标识。", 422)
    original = {"occasion_id": occasion_id, "year": year, "account_id": account_id, "card_reference": card_reference,
        "budget_minor": budget_minor, "items": items, "recipient": recipient,
        "delivery_start": _aware(delivery_start).isoformat(), "delivery_end": _aware(delivery_end).isoformat()}
    from .life_schedule_fields import ON_CONFIRMATION
    if reserve_at is not None: original["reserve_at"] = ON_CONFIRMATION if reserve_at==ON_CONFIRMATION else _aware(reserve_at).isoformat()
    if order_at is not None: original["order_at"] = _aware(order_at).isoformat()
    if replaces is not None: original["replaces"] = deepcopy(replaces)
    digest = canonical_hash(original)
    previous = session.scalar(select(LifePlan).where(LifePlan.sandbox_id == principal.sandbox_id,
        LifePlan.user_id == principal.user_id, LifePlan.request_key == request_key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("LIFE_PLAN_IDEMPOTENCY_CONFLICT", "同一计划请求标识不能改变内容。", 409)
        return life_plan_view(session, principal, previous.id)
    scope = _make_scope(session, principal, **original, now=now)
    plan = LifePlan(sandbox_id=principal.sandbox_id, user_id=principal.user_id, occasion_id=occasion_id,
        account_id=account_id, name=f"{scope['family_member_name']}{year}年生日计划", request_key=request_key, request_hash=digest,
        budget_minor=budget_minor, scope_json=scope, scope_hash=canonical_hash(scope))
    session.add(plan); session.flush()
    return life_plan_view(session, principal, plan.id)


def amend_life_plan_draft(session, principal, plan_id, *, expected_version, **changes):
    """Prepare a replacement draft; original scope and funds remain intact."""
    plan = owned_plan(session, principal, plan_id, lock=True)
    if type(expected_version) is not int or plan.version != expected_version or plan.status not in {"DRAFT", "PAUSED_RECONFIRM"}:
        raise DomainError("LIFE_PLAN_VERSION_CHANGED", "计划已变化或仍在执行，请先暂停并核对。", 409)
    if session.scalar(select(MerchantOrder.id).join(LifePlanOccurrence,
            MerchantOrder.occurrence_id == LifePlanOccurrence.id).where(LifePlanOccurrence.plan_id == plan.id)):
        raise DomainError("LIFE_PLAN_PAID_ORDER_IMMUTABLE", "已有订单不能改写；请先按原订单取消或退款，再建立新授权计划。", 409)
    allowed = {"occasion_id", "year", "account_id", "card_reference", "budget_minor", "items", "recipient", "delivery_start", "delivery_end", "reserve_at", "order_at", "now"}
    if set(changes) - allowed:
        raise DomainError("LIFE_PLAN_CHANGE_NOT_ALLOWED", "不能修改未登记的计划字段。", 422)
    old = plan.scope_json
    values = {k: old[k] for k in ("occasion_id", "year", "account_id", "budget_minor", "recipient", "delivery_start", "delivery_end")}
    values["items"] = [{k: row[k] for k in ("merchant_id", "product_id", "quantity")} for row in old["items"]]
    values["card_reference"] = old["instrument"]["instrument_id"]
    if old.get("schedule_policy") == "EXPLICIT":
        values.update(reserve_at=old["reserve_at"], order_at=old["order_at"])
        if old.get('reserve_policy')=='ON_CONFIRMATION':values['reserve_at']='ON_CONFIRMATION'
    if old.get("replaces"):
        values["replaces"] = deepcopy(old["replaces"])
    values.update(changes); values["now"] = as_utc(values.get("now") or utcnow())
    values["replaces"] = deepcopy(old.get("replaces") or {"plan_id": plan.id, "version": plan.version, "scope_hash": plan.scope_hash})
    scope = _make_scope(session, principal, **values)
    return create_life_plan_draft(session, principal, **values,
        request_key=f"revision:{plan.id}:v{plan.version}:{canonical_hash(scope)[:16]}")


def authorization_args(plan):
    return {"plan_id": plan.id, "expected_plan_version": plan.version, "scope_hash": plan.scope_hash}


def public_plan_scope(scope):
    """Targets and bounds, never old authorization-time balances as current facts."""
    value = deepcopy(scope)
    value["instrument"] = {key: scope["instrument"].get(key) for key in
        ("instrument_id", "instrument_type", "card_id", "card_number", "display_last4", "account_id", "currency")}
    if "reservation_replacement" in scope:
        from .life_reservation_replacement import public_adjustment
        value.pop("reservation_replacement")
        value["reservation_adjustment"] = public_adjustment(scope)
    return value


@atomic_domain
def authorize_life_plan(session, principal, plan_id, *, expected_version, scope_hash, confirmation_id, task_id,
        auth_challenge_id=None, now=None):
    # A failed replacement/reservation must leave the old mandate and money intact,
    # including callers that catch the error within a larger transaction.
    from .life_reservation_replacement import lock_actor_before_owner
    lock_actor_before_owner(session, principal, plan_id)
    return _authorize_life_plan(session, principal, plan_id, expected_version=expected_version,
        scope_hash=scope_hash, confirmation_id=confirmation_id, task_id=task_id,
        auth_challenge_id=auth_challenge_id, now=now)


def _authorize_life_plan(session, principal, plan_id, *, expected_version, scope_hash, confirmation_id, task_id,
        auth_challenge_id=None, now=None):
    """Called by the ordinary ToolGateway after step-up; rechecks actual DB grant.

    The view hash is canonical_hash({plan_hash, action:'LIFE_PLAN_AUTHORIZE',
    args:{plan_id,expected_plan_version,scope_hash}}). No caller-supplied boolean
    or old successful task can grant this long-lived mandate.
    """
    from .payment_instruments import validate_instrument_snapshot
    plan = owned_plan(session, principal, plan_id, lock=True)
    now = as_utc(now or utcnow())
    previous = session.scalar(select(LifePlanMandate).where(LifePlanMandate.confirmation_id == confirmation_id))
    if previous:
        if (previous.plan_id, previous.plan_version, previous.scope_hash, previous.task_id) != (plan.id, expected_version, scope_hash, task_id):
            raise DomainError("LIFE_PLAN_AUTHORIZATION_CONFLICT", "原确认不能用于另一份计划或版本。", 409)
        return life_plan_view(session, principal, plan.id)
    if type(expected_version) is not int or plan.status not in {"DRAFT", "PAUSED_RECONFIRM"} or plan.version != expected_version \
            or scope_hash != plan.scope_hash or canonical_hash(plan.scope_json) != scope_hash or not scope_budget_valid(plan.scope_json):
        raise DomainError("LIFE_PLAN_SCOPE_CHANGED", "计划范围或版本已变化，请重新核对授权。", 409)
    task = session.get(Task, task_id)
    confirmation = session.scalar(select(Confirmation).where(Confirmation.id == confirmation_id).with_for_update().execution_options(populate_existing=True))
    args = authorization_args(plan)
    if (task is None or confirmation is None or (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or confirmation.task_id != task.id or confirmation.user_id != principal.user_id
            or confirmation.sandbox_id != principal.sandbox_id or confirmation.status != "PENDING"
            or confirmation.action_type != AUTH_TOOL or confirmation.target_id != plan.id
            or as_utc(confirmation.expires_at) <= now or task.state not in {"AWAITING_CONFIRMATION", "PARTIAL"}
            or canonical_hash(task.plan_json) != task.plan_hash or confirmation.plan_hash != task.plan_hash
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": "LIFE_PLAN_AUTHORIZE", "args": args})):
        raise DomainError("LIFE_PLAN_CONFIRMATION_INVALID", "本次计划确认无效、已使用或过期。", 409)
    nodes = task.plan_json.get("nodes", [])
    if (len(nodes) != 1 or nodes[0].get("action_kind") != "LIFE_PLAN_AUTHORIZE"
            or nodes[0].get("args") != args or nodes[0].get("tool_name") != AUTH_TOOL
            or confirmation.node_key != nodes[0].get("node_id")):
        raise DomainError("LIFE_PLAN_CONFIRMATION_INVALID", "计划授权与已展示步骤不一致。", 409)
    from .runtime_models import AuthChallenge
    challenge = session.scalar(select(AuthChallenge).where(AuthChallenge.id == auth_challenge_id,
        AuthChallenge.task_id == task.id, AuthChallenge.confirmation_id == confirmation.id,
        AuthChallenge.user_id == principal.user_id, AuthChallenge.sandbox_id == principal.sandbox_id).with_for_update().execution_options(populate_existing=True))
    if (challenge is None or challenge.status not in {"VERIFIED", "CONSUMED"}
            or challenge.plan_hash != task.plan_hash or challenge.node_key != confirmation.node_key
            or as_utc(challenge.expires_at) <= now or challenge.verified_at is None
            or (challenge.status == "CONSUMED" and challenge.consumed_at is None)):
        raise DomainError("STEP_UP_REQUIRED", "请完成本次有界计划授权的二次核验。", 403)
    occasion = owned_occasion(session, principal, plan.occasion_id, lock=True)
    if occasion.version != plan.scope_json["occasion_version"] or now >= _aware(plan.scope_json["order_window_end"]):
        raise DomainError("LIFE_PLAN_SCOPE_CHANGED", "生日资料或办理时间已变化，请重新确认。", 409)
    fresh = validate_instrument_snapshot(session, principal, plan.scope_json["instrument"], lock=True,
        purpose="LIFE_PLAN", version_policy="IDENTITY_AND_CONTROLS")
    if fresh.get("instrument_type") != "DEBIT" or fresh.get("account_id") != plan.account_id:
        raise DomainError("LIFE_PLAN_CASH_ACCOUNT_REQUIRED", "本次授权付款账户已变化。", 409)
    from .life_reservation_replacement import validate_source, record_adjustment
    held_replacement = validate_source(session, principal, plan.scope_json, now, lock=True)
    replaced = None
    if plan.scope_json.get("replaces"):
        old = replacement_source(session, principal, plan.scope_json["replaces"], lock=True)
        if old.id == plan.id:
            raise DomainError("LIFE_PLAN_REPLACEMENT_INVALID", "不能用同一计划改写原授权。", 409)
        replaced = {"plan_id": old.id, "version": old.version, "scope_hash": old.scope_hash,
            "name": old.name, "released_reserved_minor": old.reserved_minor,
            "status": "CANCELLED", "reserved_minor": 0, "spent_minor": old.spent_minor}
        cancel_life_plan(session, principal, old.id, expected_version=old.version, now=now)
    # Reauthorization invalidates old queued work before installing a new scope.
    for old in session.scalars(select(LifePlanMandate).where(LifePlanMandate.plan_id == plan.id,
            LifePlanMandate.status == "ACTIVE").with_for_update()):
        old.status, old.revoked_at = "SUPERSEDED", now
    mandate = LifePlanMandate(plan_id=plan.id, plan_version=plan.version, scope_hash=scope_hash,
        scope_json=deepcopy(plan.scope_json), confirmation_id=confirmation.id, auth_challenge_id=challenge.id, task_id=task.id,
        authorized_at=now, expires_at=_aware(plan.scope_json["expires_at"]))
    session.add(mandate); session.flush()
    occurrence = session.scalar(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id == plan.id).with_for_update().execution_options(populate_existing=True))
    if occurrence is None:
        occurrence = LifePlanOccurrence(plan_id=plan.id, occurrence_key=plan.scope_json["birthday"],
            mandate_id=mandate.id, reserve_at=_aware(plan.scope_json["reserve_at"]), order_at=_aware(plan.scope_json["order_at"]))
        session.add(occurrence); session.flush()
    else:
        for job in session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id,
                LifePlanJob.status.in_(["QUEUED", "BLOCKED_RECONFIRM"]))):
            job.status = "CANCELLED"
        occurrence.mandate_id, occurrence.reserve_at, occurrence.order_at = mandate.id, _aware(plan.scope_json["reserve_at"]), _aware(plan.scope_json["order_at"])
        occurrence.occurrence_key = plan.scope_json["birthday"]
        occurrence.status = "WAITING_RESERVE"; occurrence.version += 1
    plan.status, plan.pause_reason = "ACTIVE", None
    challenge.status, challenge.consumed_at = "CONSUMED", now
    confirmation.status, confirmation.consumed_at = "CONSUMED", now
    if held_replacement is None:
        queue_job(session, occurrence, "RESERVE", max(now, as_utc(occurrence.reserve_at)), version=plan.version)
    queue_job(session, occurrence, "EXPIRE", _aware(mandate.scope_json["delivery_end"]) + timedelta(seconds=1), version=plan.version)
    session.flush()
    if now >= as_utc(occurrence.reserve_at):
        _reserve_context(session, principal, {"plan": plan, "occurrence": occurrence, "mandate": mandate,
            "instrument": fresh, "delivery_end": _aware(mandate.scope_json["delivery_end"])}, now)
    view = life_plan_view(session, principal, plan.id)
    view.update(life_plan_state_changed=True, bank_state_changed=bool(plan.reserved_minor), cash_debit_minor=0,
        authorization_receipt={"mandate_id": mandate.id, "confirmation_id": confirmation.id, "plan_id": plan.id,
            "plan_version": plan.version, "scope_hash": scope_hash, "maximum_occurrences": 1,
            "expires_at": as_utc(mandate.expires_at).isoformat()},
        message="本次有界授权已生效，将在约定范围和时间内自动办理；这不是订单已履约的回执。")
    if replaced is not None:
        view.update(replaced_plan=replaced, bank_state_changed=bool(plan.reserved_minor or replaced["released_reserved_minor"]))
    if held_replacement is not None:
        view["reservation_adjustment"] = record_adjustment(session, principal, plan, occurrence, mandate)
    return view


def queue_job(session, occurrence, kind, when, *, item_index=None, version=1, result=None):
    discriminator = (result or {}).get("order_id") if kind == "REFUND" else item_index
    key = f"life:{occurrence.id}:v{version}:{kind}:{discriminator if discriminator is not None else '-'}"
    previous = session.scalar(select(LifePlanJob).where(LifePlanJob.dedupe_key == key))
    if previous:
        if kind == "REFUND" and previous.status == "CANCELLED" and previous.result_json == (result or {}):
            previous.status, previous.next_run_at = "QUEUED", as_utc(when)
        return previous
    job = LifePlanJob(occurrence_id=occurrence.id, kind=kind, item_index=item_index, plan_version=version,
        dedupe_key=key, next_run_at=as_utc(when), result_json=result or {})
    session.add(job); session.flush()
    return job


def execution_context(session, principal, occurrence_id, *, now=None, for_order=False, payment_required=True):
    from .payment_instruments import validate_instrument_snapshot
    now = as_utc(now or utcnow())
    occurrence_hint = session.get(LifePlanOccurrence, occurrence_id)
    if occurrence_hint is None:
        raise DomainError("LIFE_PLAN_OCCURRENCE_NOT_FOUND", "未找到本次计划事项。", 404)
    plan = owned_plan(session, principal, occurrence_hint.plan_id, lock=True)
    occurrence = session.scalar(select(LifePlanOccurrence).where(LifePlanOccurrence.id == occurrence_id).with_for_update().execution_options(populate_existing=True))
    mandate = session.scalar(select(LifePlanMandate).where(LifePlanMandate.id == occurrence.mandate_id).with_for_update().execution_options(populate_existing=True))
    confirmation = session.get(Confirmation, mandate.confirmation_id) if mandate else None
    auth_task = session.get(Task, mandate.task_id) if mandate else None
    if (mandate is None or plan.status != "ACTIVE" or mandate.status != "ACTIVE" or mandate.plan_id != plan.id
            or mandate.plan_version != plan.version or mandate.scope_hash != plan.scope_hash
            or canonical_hash(plan.scope_json) != plan.scope_hash or canonical_hash(mandate.scope_json) != mandate.scope_hash
            or confirmation is None or confirmation.status != "CONSUMED" or confirmation.task_id != mandate.task_id
            or confirmation.user_id != principal.user_id or confirmation.sandbox_id != principal.sandbox_id
            or now > as_utc(mandate.expires_at)):
        raise DomainError("LIFE_PLAN_AUTHORIZATION_NOT_ACTIVE", "本次计划授权已失效或范围变化，不能继续自动办理。", 409)
    scope = mandate.scope_json
    if (plan.account_id != scope["account_id"] or plan.budget_minor != scope["budget_minor"]
            or not scope_budget_valid(scope)
            or plan.occasion_id != scope["occasion_id"] or occurrence.occurrence_key != scope["birthday"]
            or as_utc(occurrence.reserve_at).isoformat() != scope["reserve_at"]
            or as_utc(occurrence.order_at).isoformat() != scope["order_at"]
            or as_utc(mandate.expires_at).isoformat() != scope["expires_at"]
            or now < as_utc(mandate.authorized_at)):
        raise DomainError("LIFE_PLAN_SCOPE_CHANGED", "计划账户、预算、日期或执行时间不再符合原有界授权。", 409)
    _verified_original_authorization(session, principal, plan, mandate, confirmation, auth_task)
    occasion = owned_occasion(session, principal, plan.occasion_id, lock=True)
    if occasion.version != mandate.scope_json["occasion_version"] or occasion.family_member_id != mandate.scope_json["family_member_id"]:
        raise DomainError("LIFE_PLAN_OCCASION_CHANGED", "生日或家庭成员资料已变化，计划需要重新确认。", 409)
    if for_order and (now < as_utc(occurrence.order_at) or now >= _aware(mandate.scope_json["order_window_end"])):
        raise DomainError("LIFE_PLAN_ORDER_TIME_INVALID", "当前时间不在本次授权的下单窗口内。", 409)
    fresh = (validate_instrument_snapshot(session, principal, mandate.scope_json["instrument"], lock=True,
        purpose="LIFE_PLAN", version_policy="IDENTITY_AND_CONTROLS") if payment_required else deepcopy(mandate.scope_json["instrument"]))
    if fresh.get("account_id") != plan.account_id or fresh.get("instrument_type") != "DEBIT":
        raise DomainError("LIFE_PLAN_PAYMENT_SCOPE_CHANGED", "付款工具已变化，不会切换账户或信用卡。", 409)
    if plan.reservation_id:
        _validate_reservation(session, plan, occurrence, mandate, lock=payment_required)
    return {"plan": plan, "occurrence": occurrence, "mandate": mandate, "instrument": fresh,
        "delivery_start": _aware(mandate.scope_json["delivery_start"]), "delivery_end": _aware(mandate.scope_json["delivery_end"])}


def _reserve_context(session, principal, context, now):
    from .payment_instruments import reserve_cash
    plan, occurrence = context["plan"], context["occurrence"]
    if now < as_utc(occurrence.reserve_at):
        raise DomainError("LIFE_PLAN_RESERVE_NOT_DUE", "尚未到本次授权的生日月预留时间。", 409)
    if plan.reservation_id is None:
        reserved = reserve_cash(session, principal, instrument_snapshot=context["instrument"], amount_minor=plan.budget_minor,
            reservation_key=f"life:{occurrence.id}:v{plan.version}:reserve", owner_ref=f"life:{occurrence.id}", expires_at=context["delivery_end"])
        plan.reservation_id, plan.reserved_minor = reserved["reservation_id"], plan.budget_minor
    occurrence.status = "WAITING_ORDER"
    for index in range(len(context["mandate"].scope_json["items"])):
        queue_job(session, occurrence, "ORDER", occurrence.order_at, item_index=index, version=plan.version)
    return {"reservation_id": plan.reservation_id, "reserved_minor": plan.reserved_minor, "cash_debit_minor": 0,
        "card_state_changed": False, "status": occurrence.status}


def _validate_reservation(session, plan, occurrence, mandate, *, lock=True):
    """Read the central reservation only; never duplicate available-cash math."""
    from .credit_models import PaymentCashReservation
    query = select(PaymentCashReservation).where(PaymentCashReservation.id == plan.reservation_id)
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if (row is None or (row.user_id, row.sandbox_id, row.account_id) != (plan.user_id, plan.sandbox_id, plan.account_id)
            or row.owner_ref != f"life:{occurrence.id}" or row.reservation_key != f"life:{occurrence.id}:v{plan.version}:reserve"
            or row.instrument_id != mandate.scope_json["instrument"]["instrument_id"]
            or row.amount_minor != plan.budget_minor or row.remaining_minor != plan.reserved_minor
            or row.status not in {"ACTIVE", "CONSUMED"} or row.expires_at is None
            or as_utc(row.expires_at) != as_utc(mandate.expires_at)):
        raise DomainError("LIFE_PLAN_RESERVATION_CHANGED", "本次计划预留来源已变化，不会动用其他计划的资金。", 409)


def reserve_plan_cash(session, principal, occurrence_id, *, now=None):
    now = as_utc(now or utcnow())
    return _reserve_context(session, principal, execution_context(session, principal, occurrence_id, now=now), now)


def _verified_original_authorization(session, principal, plan, mandate, confirmation, task):
    from .runtime_models import AuthChallenge
    args = {"plan_id": plan.id, "expected_plan_version": mandate.plan_version, "scope_hash": mandate.scope_hash}
    nodes = (task.plan_json or {}).get("nodes", []) if task else []
    calls = session.scalars(select(ToolCall).where(ToolCall.task_id == mandate.task_id,
        ToolCall.sandbox_id == principal.sandbox_id, ToolCall.tool_name == AUTH_TOOL,
        ToolCall.status == "SUCCESS")).all()
    challenge = session.get(AuthChallenge, mandate.auth_challenge_id)
    if (challenge is None or challenge.status != "CONSUMED" or challenge.confirmation_id != confirmation.id
            or challenge.task_id != mandate.task_id or challenge.user_id != principal.user_id
            or challenge.sandbox_id != principal.sandbox_id or challenge.plan_hash != confirmation.plan_hash
            or challenge.node_key != confirmation.node_key or challenge.verified_at is None or challenge.consumed_at is None
            or as_utc(mandate.authorized_at) > as_utc(challenge.expires_at)):
        raise DomainError("LIFE_PLAN_ORIGINAL_AUTHORIZATION_INVALID", "原二次核验来源失效，不能继续自动办理。", 409)
    if (task is None or task.state != "SUCCEEDED" or (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id)
            or canonical_hash(task.plan_json) != task.plan_hash or confirmation.plan_hash != task.plan_hash
            or confirmation.target_id != plan.id or confirmation.action_type != AUTH_TOOL
            or len(nodes) != 1 or nodes[0].get("action_kind") != "LIFE_PLAN_AUTHORIZE" or nodes[0].get("args") != args
            or confirmation.node_key != nodes[0].get("node_id")
            or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": "LIFE_PLAN_AUTHORIZE", "args": args})
            or confirmation.consumed_at is None or as_utc(confirmation.consumed_at) != as_utc(mandate.authorized_at)
            or len(calls) != 1):
        raise DomainError("LIFE_PLAN_ORIGINAL_AUTHORIZATION_INVALID", "原计划确认或执行回执核验失败，不能继续自动办理。", 409)
    result = calls[0].result_json
    rows = result.get("plans", []) if isinstance(result, dict) else []
    receipt = result.get("authorization_receipt", {}) if isinstance(result, dict) else {}
    if (len(rows) != 1 or rows[0].get("plan_id") != plan.id or rows[0].get("scope_hash") != mandate.scope_hash
            or receipt.get("mandate_id") != mandate.id or receipt.get("confirmation_id") != confirmation.id
            or receipt.get("scope_hash") != mandate.scope_hash):
        raise DomainError("LIFE_PLAN_ORIGINAL_AUTHORIZATION_INVALID", "原授权回执不属于本次计划范围。", 409)


def pause_life_plan(session, principal, plan_id, *, expected_version, reason="USER_PAUSED"):
    plan = owned_plan(session, principal, plan_id, lock=True)
    if type(expected_version) is not int or plan.version != expected_version or plan.status in FINISHED:
        raise DomainError("LIFE_PLAN_VERSION_CHANGED", "计划已结束或版本变化。", 409)
    plan.status, plan.pause_reason = "PAUSED_RECONFIRM", reason
    for occurrence in session.scalars(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id == plan.id)):
        for job in session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id,
                LifePlanJob.status == "QUEUED", LifePlanJob.kind != "EXPIRE")):
            job.status = "BLOCKED_RECONFIRM"
    view = life_plan_view(session, principal, plan.id)
    view["life_plan_state_changed"] = True
    return view


def refund_context(session, principal, occurrence_id, *, now=None, audit_completed=False):
    occurrence = session.get(LifePlanOccurrence, occurrence_id)
    if occurrence is None:
        raise DomainError("LIFE_PLAN_OCCURRENCE_NOT_FOUND", "未找到本次计划事项。", 404)
    plan = owned_plan(session, principal, occurrence.plan_id, lock=True)
    mandate = session.get(LifePlanMandate, occurrence.mandate_id)
    confirmation = session.get(Confirmation, mandate.confirmation_id) if mandate else None
    if (mandate is None or canonical_hash(mandate.scope_json) != mandate.scope_hash
            or mandate.scope_json.get("cancellation_policy") != "REFUND_UNFULFILLED_TO_ORIGINAL_SOURCE"
            or plan.status not in ({"CANCELLING", "COMPENSATING", "PARTIAL", "COMPENSATED"} if audit_completed
                else {"CANCELLING", "COMPENSATING", "PARTIAL"})
            or confirmation is None or confirmation.status != "CONSUMED" or confirmation.user_id != principal.user_id
            or confirmation.sandbox_id != principal.sandbox_id):
        raise DomainError("LIFE_PLAN_REFUND_NOT_AUTHORIZED", "退款不在本次原始订单补偿范围内。", 403)
    _verified_original_authorization(session, principal, plan, mandate, confirmation, session.get(Task, mandate.task_id))
    return {"plan": plan, "occurrence": occurrence, "mandate": mandate}


@atomic_domain
def cancel_life_plan(session, principal, plan_id, *, expected_version, now=None):
    from .payment_instruments import release_cash_reservation
    now = as_utc(now or utcnow())
    plan = owned_plan(session, principal, plan_id, lock=True)
    if plan.status in {"CANCELLED", "COMPENSATED"}:
        view = life_plan_view(session, principal, plan.id)
        view.update(no_effect=True, life_plan_state_changed=False,
            message="这份计划已结束，本次未再次撤销、退款或改动现金。")
        return view
    if type(expected_version) is not int or plan.version != expected_version or plan.status == "SUCCEEDED":
        raise DomainError("LIFE_PLAN_VERSION_CHANGED", "计划已经完成或版本变化；已履约订单不能伪回滚。", 409)
    occurrence = session.scalar(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id == plan.id).with_for_update().execution_options(populate_existing=True))
    plan.status = "CANCELLING"
    for mandate in session.scalars(select(LifePlanMandate).where(LifePlanMandate.plan_id == plan.id,
            LifePlanMandate.status == "ACTIVE").with_for_update()):
        mandate.status, mandate.revoked_at = "REVOKED", now
    if occurrence:
        for job in session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id == occurrence.id,
                LifePlanJob.status.in_(["QUEUED", "BLOCKED_RECONFIRM"])).with_for_update()):
            job.status = "CANCELLED"
        orders = session.scalars(select(MerchantOrder).where(MerchantOrder.occurrence_id == occurrence.id)).all()
        for index, order in enumerate(orders):
            if order.status in {"PAID", "REFUND_PENDING"}:
                queue_job(session, occurrence, "REFUND", now, item_index=index, version=plan.version,
                    result={"order_id": order.id})
        if not orders:
            plan.status, occurrence.status = "CANCELLED", "CANCELLED"
        elif all(order.status == "REFUNDED" for order in orders):
            plan.status = "COMPENSATING"
        else:
            plan.status = "COMPENSATING" if all(order.status in {"PAID", "REFUND_PENDING", "REFUNDED"} for order in orders) else "PARTIAL"
    else:
        plan.status = "CANCELLED"
    released = plan.reserved_minor
    if plan.reservation_id and plan.reserved_minor:
        release_cash_reservation(session, principal, plan.reservation_id, operation_key=f"life:{plan.id}:cancel-release")
        plan.reserved_minor = 0
    if occurrence and plan.status == 'COMPENSATING':
        from .life_compensation import finish_compensation
        finish_compensation(session, principal, plan, occurrence, now=now)
    view = life_plan_view(session, principal, plan.id)
    view.update(life_plan_state_changed=True, bank_state_changed=bool(released), reserved_cash_released_minor=released,
        message="未执行授权已撤销；已有订单及退款分别保留真实状态，不代表已支付部分被回滚。")
    return view


def life_plan_view(session, principal, plan_id=None):
    from .merchant_models import MerchantRefund
    require_profile_actor(session, principal)
    from .customer_history_archive import visible_history, require_visible_history
    if plan_id:
        require_visible_history(session, principal, LifePlan, plan_id)
    plans = [owned_plan(session, principal, plan_id)] if plan_id else session.scalars(select(LifePlan).where(
        LifePlan.user_id == principal.user_id, LifePlan.sandbox_id == principal.sandbox_id,
        visible_history(session, principal, LifePlan.created_at))).all()
    values = []
    for plan in plans:
        occurrences = session.scalars(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id == plan.id)).all()
        refunded = sum(session.scalars(select(MerchantRefund.amount_minor).join(MerchantOrder,
            MerchantRefund.order_id == MerchantOrder.id).join(LifePlanOccurrence,
            MerchantOrder.occurrence_id == LifePlanOccurrence.id).where(LifePlanOccurrence.plan_id == plan.id,
            MerchantRefund.status == "POSTED")).all())
        from .life_execution_review import execution_review
        values.append({"plan_id": plan.id, "name": plan.name, "version": plan.version, "status": plan.status,
            "execution_review": execution_review(session, principal, plan),
            "pause_reason": plan.pause_reason, "scope_hash": plan.scope_hash, "scope": public_plan_scope(plan.scope_json),
            "budget_minor": plan.budget_minor, "reserved_minor": plan.reserved_minor, "spent_minor": plan.spent_minor,
            "refunded_minor": refunded, "net_cash_debit_minor": plan.spent_minor - refunded,
            "cash_reservation_is_card_lock": False, "authorization_is_bounded": True,
            "occurrences": [{"occurrence_id": row.id, "date": row.occurrence_key, "status": row.status,
                "reserve_at": as_utc(row.reserve_at).isoformat(), "order_at": as_utc(row.order_at).isoformat(),
                "nodes": [{"job_id": job.id, "kind": job.kind, "item_index": job.item_index, "status": job.status,
                    "plan_version": job.plan_version, "next_run_at": as_utc(job.next_run_at).isoformat(), "error": job.last_error} for job in session.scalars(
                        select(LifePlanJob).where(LifePlanJob.occurrence_id == row.id).order_by(LifePlanJob.next_run_at, LifePlanJob.id))],
                "orders": orders_view(session, principal, occurrence_id=row.id)["orders"]} for row in occurrences]})
    return {"schema_version": "life-plans-v1", "plans": values, "bank_state_changed": False,
        "merchant_source": "VIRTUAL_MERCHANT_REGISTER_NOT_EXTERNAL_DELIVERY"}
