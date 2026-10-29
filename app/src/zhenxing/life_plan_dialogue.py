"""Finite owned-entity resolution after semantic action selection, not routing."""
from datetime import datetime, time
import unicodedata
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .life_plan_models import LifePlan
from .life_plan_service import create_life_plan_draft, owned_plan
from .merchant_service import selected_plan_items
from .models import utcnow
from .occasion_service import occurrence_date, resolve_family_occasion


def _name(value):
    return unicodedata.normalize("NFKC", value).strip().casefold() if isinstance(value, str) else None


def resolve_life_plan(session, principal, *, life_plan_id=None, life_plan_name=None, allow_missing=False,
        include_terminal=False):
    require_profile_actor(session, principal)
    if life_plan_id:
        row = owned_plan(session, principal, life_plan_id)
        if life_plan_name is not None and _name(row.name) != _name(life_plan_name):
            raise DomainError("LIFE_PLAN_SELECTION_CHANGED", "计划名称与选中的事项不一致。", 409)
        return row
    query = select(LifePlan).where(LifePlan.user_id == principal.user_id, LifePlan.sandbox_id == principal.sandbox_id)
    if not include_terminal:
        query = query.where(LifePlan.status.notin_(["CANCELLED", "COMPENSATED", "SUCCEEDED"]))
    rows = session.scalars(query).all()
    if life_plan_name is not None:
        rows = [row for row in rows if _name(row.name) == _name(life_plan_name)]
    if not rows and allow_missing:
        return None
    if len(rows) != 1:
        raise DomainError("LIFE_PLAN_SELECTION_REQUIRED", "请明确选择本人哪一份生活计划。", 422)
    return rows[0]


def prepare_dialogue_life_plan_draft(session, principal, slots, *, request_key, now=None, task=None, context=None,
        prior_plan=None):
    """Create metadata only; actual cash reservation waits for confirmed mandate."""
    from .payment_instruments import resolve_source_instrument
    from .delivery_window_fields import parse_delivery_window
    now = as_utc(now or utcnow())
    slots = {key: value for key, value in slots.items() if value is not None}
    old = prior_plan.scope_json if prior_plan is not None else {}
    replaces = None
    if prior_plan is not None:
        from .life_plan_service import replacement_source
        from .planning import canonical_hash
        if canonical_hash(old) != prior_plan.scope_hash:
            raise DomainError("LIFE_PLAN_SCOPE_CHANGED", "原计划范围未通过核验，请重新查询。", 409)
        current_reference = {"plan_id": prior_plan.id, "version": prior_plan.version, "scope_hash": prior_plan.scope_hash}
        replaces = current_reference if prior_plan.status == "ACTIVE" else old.get("replaces") or current_reference
        replacement_source(session, principal, replaces, lock=True)
    if slots.get("life_plan_name") is not None and (not isinstance(slots["life_plan_name"], str) or not 1 <= len(slots["life_plan_name"].strip()) <= 100):
        raise DomainError("LIFE_PLAN_NAME_INVALID", "计划名称长度不正确。", 422)
    inherited_occasion = old.get("occasion_id") if not any(slots.get(key) for key in ("family_member_name", "family_id", "family_member_id")) else None
    occasion = resolve_family_occasion(session, principal, occasion_id=slots.get("occasion_id") or inherited_occasion,
        family_member_name=slots.get("family_member_name"), family_member_id=slots.get("family_id") or slots.get("family_member_id"))
    year = slots.get("plan_year", old.get("year", now.astimezone(ZoneInfo(occasion.timezone)).year))
    birthday = occurrence_date(occasion, year)
    items = (selected_plan_items(session, principal, slots["life_plan_items"]) if "life_plan_items" in slots else
        [{key: value[key] for key in ("merchant_id", "product_id", "quantity")} for value in old["items"]] if old else
        selected_plan_items(session, principal, None))
    card_ref = slots.get("card_reference") or slots.get("card_number") or slots.get("card_last4") or old.get("instrument", {}).get("instrument_id")
    if slots.get("currency", "CNY") != "CNY":
        raise DomainError("LIFE_PLAN_CNY_ONLY", "本轮生日计划只预留人民币现金，不会自动改币种或改用信用卡。", 422)
    if not card_ref:
        raise DomainError("LIFE_PLAN_PAYMENT_CARD_REQUIRED", "请明确本次现金预留和付款使用哪张本人借记卡。", 422)
    instrument = resolve_source_instrument(session, principal, card_ref, "CNY", purpose="LIFE_PLAN")
    recipient = {key: slots.get("recipient_" + key, old.get("recipient", {}).get(key)) for key in ("name", "phone", "address")}
    missing = [name for name, value in recipient.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        labels = {"name": "收件人", "phone": "联系电话", "address": "收件地址"}
        raise DomainError("LIFE_PLAN_RECIPIENT_REQUIRED", "还需本人确认：" + "、".join(labels[key] for key in missing) + "。", 422)
    zone = ZoneInfo(occasion.timezone)
    from .temporal_fields import temporal_field_clock
    clocks={}
    for field in ("delivery_start","delivery_end","life_plan_reserve_at","life_plan_order_at"):
        if field not in slots: continue
        if task is None and context:
            raise DomainError("TEMPORAL_SOURCE_REQUIRED", "请重新明确配送日期和时间；原日期来源暂时无法核验，不会改按今天重新推算。", 422)
        clocks[field]=(temporal_field_clock(session,task,context,field,slots[field]) if task is not None else None) or now
    from .life_schedule_fields import split_delivery_range,parse_life_schedule_field,ON_CONFIRMATION
    delivery_start,delivery_end=split_delivery_range(slots.get("delivery_start"),slots.get("delivery_end"))
    start, end = parse_delivery_window(
        delivery_start or old.get("delivery_start") or datetime.combine(birthday, time(10), zone),
        delivery_end or old.get("delivery_end") or datetime.combine(birthday, time(12), zone),
        now, timezone=occasion.timezone, anchor_date=birthday,
        start_clock=clocks.get("delivery_start"),end_clock=clocks.get("delivery_end") or clocks.get("delivery_start"))
    schedule = {}
    for source, target in (("life_plan_reserve_at", "reserve_at"), ("life_plan_order_at", "order_at")):
        if source in slots:
            schedule[target] = parse_life_schedule_field(slots[source],kind='reserve' if target=='reserve_at' else 'order',
                clock=clocks.get(source) or now,zone=zone,birthday=birthday)
        elif old.get("schedule_policy") == "EXPLICIT":
            schedule[target] = ON_CONFIRMATION if target=='reserve_at' and old.get('reserve_policy')==ON_CONFIRMATION else old[target]
    value = create_life_plan_draft(session, principal, occasion_id=occasion.id, year=year,
        account_id=instrument["account_id"], card_reference=card_ref, budget_minor=slots.get("budget_minor", old.get("budget_minor", 100000)),
        items=items, recipient=recipient, delivery_start=start.isoformat(),
        delivery_end=end.isoformat(), request_key=request_key, now=now, replaces=replaces, **schedule)
    plan = owned_plan(session, principal, value["plans"][0]["plan_id"], lock=True)
    if slots.get("life_plan_name"):
        name = slots["life_plan_name"]
        plan.name = name.strip()
    elif prior_plan is not None:
        plan.name = prior_plan.name
    return plan
