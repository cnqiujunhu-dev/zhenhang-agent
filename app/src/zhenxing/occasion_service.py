"""Explicit family occasion data. Trusted callers own the confirmation UI."""
from calendar import isleap
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import unicodedata

from sqlalchemy import select

from .contracts import DomainError
from .customer_profile_common import require_profile_actor
from .extensions_models import FamilyMember
from .models import Confirmation, Task, ToolCall, utcnow
from .planning import canonical_hash
from .occasion_models import FamilyOccasion
from .family_profile_service import member_display_name, member_label


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def normalize_occasion_values(*, month, day, calendar="GREGORIAN",
        timezone="Asia/Shanghai", leap_day_policy=None, lunar_leap_month=False,
        confirmed_solar_date=None, adult_subject_confirmed=None, source_reference):
    """Pure field validation shared by preview and confirmed persistence."""
    if adult_subject_confirmed is not None and type(adult_subject_confirmed) is not bool:
        raise DomainError("OCCASION_AGE_FACT_INVALID", "年龄是否成年只能是明确事实或未知，不从生日推断。", 422)
    if not isinstance(source_reference, str) or not 1 <= len(source_reference.strip()) <= 120:
        raise DomainError("OCCASION_SOURCE_REQUIRED", "生日资料需要本人明确确认的来源。", 422)
    if calendar not in {"GREGORIAN", "LUNAR"} or not _integer(month, 1, 12) or not _integer(day, 1, 31):
        raise DomainError("OCCASION_DATE_INVALID", "请核对生日历法和月日。", 422)
    try:
        ZoneInfo(timezone)
        if calendar == "GREGORIAN":
            date(2000, month, day)
        elif day > 30:
            raise ValueError()
        if confirmed_solar_date is not None:
            if not isinstance(confirmed_solar_date, str) or date.fromisoformat(confirmed_solar_date).isoformat() != confirmed_solar_date:
                raise ValueError()
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise DomainError("OCCASION_DATE_INVALID", "请核对日期、时区或已确认的阳历日期。", 422) from None
    if month == 2 and day == 29 and calendar == "GREGORIAN" and leap_day_policy not in {"FEB28", "MAR1", "LEAP_YEARS_ONLY"}:
        raise DomainError("OCCASION_LEAP_POLICY_REQUIRED", "请确认非闰年的生日安排。", 422)
    if type(lunar_leap_month) is not bool or (calendar == "GREGORIAN" and lunar_leap_month):
        raise DomainError("OCCASION_DATE_INVALID", "闰月只用于农历生日。", 422)
    return dict(month=month, day=day, calendar=calendar, timezone=timezone, leap_day_policy=leap_day_policy,
        lunar_leap_month=lunar_leap_month, confirmed_solar_date=confirmed_solar_date,
        adult_subject_confirmed=adult_subject_confirmed, source_reference=source_reference.strip())


def put_family_occasion(session, principal, *, family_member_id, month, day, calendar="GREGORIAN",
        timezone="Asia/Shanghai", leap_day_policy=None, lunar_leap_month=False,
        confirmed_solar_date=None, adult_subject_confirmed=None, source_reference,
        expected_version=0):
    """Persist minimal confirmed family data; a child is a valid recipient.

    This never grants the recipient a bank account, credit, or payment rights.
    """
    require_profile_actor(session, principal, lock=True)
    member = resolve_family_member(session, principal, family_member_id=family_member_id)
    values = normalize_occasion_values(month=month, day=day, calendar=calendar, timezone=timezone,
        leap_day_policy=leap_day_policy, lunar_leap_month=lunar_leap_month, confirmed_solar_date=confirmed_solar_date,
        adult_subject_confirmed=adult_subject_confirmed, source_reference=source_reference)
    row = session.scalar(select(FamilyOccasion).where(FamilyOccasion.family_member_id == member.id,
        FamilyOccasion.owner_user_id == principal.user_id, FamilyOccasion.sandbox_id == principal.sandbox_id,
        FamilyOccasion.kind == "BIRTHDAY").with_for_update().execution_options(populate_existing=True))
    if type(expected_version) is not int or expected_version != (row.version if row else 0):
        raise DomainError("OCCASION_VERSION_CHANGED", "生日资料已变化，请重新核对。", 409)
    if row is None:
        row = FamilyOccasion(sandbox_id=principal.sandbox_id, owner_user_id=principal.user_id,
            family_member_id=member.id, adult_subject_confirmed=adult_subject_confirmed, month=month, day=day,
            calendar=calendar, source_reference=source_reference)
        session.add(row)
    else:
        row.version += 1
    for field, value in dict(**values, source="USER_CONFIRMED", status="ACTIVE", confirmed_at=utcnow()).items():
        setattr(row, field, value)
    session.flush()
    result = _projection(session, [row])
    result["occasion_state_changed"] = True
    return result


def _label(value):
    return unicodedata.normalize("NFKC", value).strip().casefold() if isinstance(value, str) else None


def resolve_family_member(session, principal, *, family_member_id=None, family_member_name=None):
    """Owned directory identity with finite first-person nominal forms only.

    This does not interpret a request, invent a relationship, create a member,
    or grant bank access. Ambiguous labels remain ambiguous rather than taking
    the first row. Source spans and original field values stay unchanged.
    """
    require_profile_actor(session, principal)
    rows = session.scalars(select(FamilyMember).where(FamilyMember.sandbox_id == principal.sandbox_id,
        FamilyMember.owner_user_id == principal.user_id)).all()
    if family_member_id:
        rows = [row for row in rows if row.id == family_member_id]
    if family_member_name is not None:
        literal = _label(family_member_name)
        def labels(row):
            profile = member_label(session, row)
            names = {row.display_name, member_display_name(session, row)}
            if profile: names.add(profile.relationship_label)
            return {_label(prefix + name) for name in names for prefix in ("", "我", "我的", "my ")}
        rows = [row for row in rows if literal in labels(row)]
    if len(rows) != 1:
        raise DomainError("OCCASION_MEMBER_SELECTION_REQUIRED", "请从本人已登记的家庭成员中明确选择一位。", 422)
    return rows[0]


def resolve_family_occasion(session, principal, *, occasion_id=None, family_member_name=None, family_member_id=None):
    if occasion_id:
        row = owned_occasion(session, principal, occasion_id)
        if family_member_name is not None or family_member_id is not None:
            member = resolve_family_member(session, principal, family_member_name=family_member_name, family_member_id=family_member_id)
            if row.family_member_id != member.id:
                raise DomainError("OCCASION_SELECTION_CHANGED", "生日对象与本次指定家庭成员不一致。", 409)
        return row
    member = resolve_family_member(session, principal, family_member_name=family_member_name, family_member_id=family_member_id)
    rows = session.scalars(select(FamilyOccasion).where(FamilyOccasion.sandbox_id == principal.sandbox_id,
        FamilyOccasion.owner_user_id == principal.user_id, FamilyOccasion.family_member_id == member.id,
        FamilyOccasion.status == "ACTIVE")).all()
    if len(rows) != 1:
        raise DomainError("OCCASION_BIRTHDAY_REQUIRED", "这位家庭成员尚无唯一已确认生日，请先核对并保存生日。", 422)
    return owned_occasion(session, principal, rows[0].id)


def owned_occasion(session, principal, occasion_id, *, lock=False):
    query = select(FamilyOccasion).where(FamilyOccasion.id == occasion_id,
        FamilyOccasion.owner_user_id == principal.user_id, FamilyOccasion.sandbox_id == principal.sandbox_id)
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    member = session.get(FamilyMember, row.family_member_id) if row else None
    if row is None or member is None or (member.owner_user_id, member.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError("OCCASION_NOT_FOUND", "未找到属于本人的生日资料。", 404)
    if row.status != "ACTIVE" or row.source != "USER_CONFIRMED" or not row.source_reference:
        raise DomainError("OCCASION_NOT_CONFIRMED", "生日资料尚未有效确认。", 409)
    if row.source_reference.startswith("confirmed-task:"):
        _confirmed_source(session, row)
    return row


def _confirmed_source(session, row):
    """Runtime-origin data retains its actual immutable confirmation chain."""
    task_id = row.source_reference.removeprefix("confirmed-task:")
    task = session.get(Task, task_id)
    confirmations = session.scalars(select(Confirmation).where(Confirmation.task_id == task_id,
        Confirmation.action_type == "occasion.set.v1")).all()
    calls = session.scalars(select(ToolCall).where(ToolCall.task_id == task_id,
        ToolCall.tool_name == "occasion.set.v1", ToolCall.status == "SUCCESS")).all()
    if (task is None or task.state != "SUCCEEDED" or (task.user_id, task.sandbox_id) != (row.owner_user_id, row.sandbox_id)
            or canonical_hash(task.plan_json) != task.plan_hash or len(confirmations) != 1
            or confirmations[0].status != "CONSUMED" or confirmations[0].plan_hash != task.plan_hash
            or confirmations[0].target_id != row.family_member_id or len(calls) != 1):
        raise DomainError("OCCASION_SOURCE_CHANGED", "生日资料的原始确认来源未通过核验，请重新核对。", 409)
    nodes = task.plan_json.get("nodes", [])
    node = nodes[0] if len(nodes) == 1 else {}
    args = node.get("args", {})
    receipt_rows = calls[0].result_json.get("occasions", [])
    receipt = next((value for value in receipt_rows if value.get("occasion_id") == row.id), {})
    fields = ("family_member_id", "month", "day", "calendar", "timezone", "leap_day_policy",
        "lunar_leap_month", "confirmed_solar_date", "adult_subject_confirmed")
    if (node.get("action_kind") != "OCCASION_SET" or confirmations[0].node_key != node.get("node_id")
            or confirmations[0].view_hash != canonical_hash({"plan_hash": task.plan_hash, "action": "OCCASION_SET", "args": args})
            or receipt.get("version") != row.version
            or any(args.get(field) != getattr(row, field) or receipt.get(field) != getattr(row, field) for field in fields)):
        raise DomainError("OCCASION_SOURCE_CHANGED", "生日资料与当时已确认的内容不一致。", 409)


def occurrence_date(occasion, year):
    if not _integer(year, 2000, 2200):
        raise DomainError("OCCASION_YEAR_INVALID", "请核对本次生日年份。", 422)
    if occasion.calendar == "LUNAR":
        resolved = date.fromisoformat(occasion.confirmed_solar_date) if occasion.confirmed_solar_date else None
        if resolved is None or resolved.year != year:
            raise DomainError("OCCASION_SOLAR_DATE_REQUIRED", "该农历生日需要本人确认本年的阳历日期，不能自动猜测换算。", 422)
        return resolved
    if occasion.month == 2 and occasion.day == 29 and not isleap(year):
        if occasion.leap_day_policy == "LEAP_YEARS_ONLY":
            raise DomainError("OCCASION_NO_DATE_THIS_YEAR", "已选择只在闰年安排，本年不自动执行。", 422)
        return date(year, 2, 28) if occasion.leap_day_policy == "FEB28" else date(year, 3, 1)
    return date(year, occasion.month, occasion.day)


def occasion_view(session, principal, occasion_id=None, *, as_of=None):
    require_profile_actor(session, principal)
    rows = [owned_occasion(session, principal, occasion_id)] if occasion_id else session.scalars(select(FamilyOccasion).where(
        FamilyOccasion.owner_user_id == principal.user_id, FamilyOccasion.sandbox_id == principal.sandbox_id)).all()
    for row in rows:
        owned_occasion(session, principal, row.id)
    result=_projection(session, rows)
    if as_of is not None:
        from .occasion_upcoming import upcoming_birthdays
        result['next_birthdays']=upcoming_birthdays(result['occasions'],as_of)
    return result


def _projection(session, rows):
    return {"schema_version": "family-occasion-v1", "occasions": [{"occasion_id": row.id,
        "family_member_name": member_display_name(session, session.get(FamilyMember, row.family_member_id)),
        "family_member_id": row.family_member_id, "kind": row.kind, "month": row.month, "day": row.day,
        "calendar": row.calendar, "timezone": row.timezone, "leap_day_policy": row.leap_day_policy,
        "lunar_leap_month": row.lunar_leap_month, "confirmed_solar_date": row.confirmed_solar_date,
        "adult_subject_confirmed": row.adult_subject_confirmed, "source": row.source,
        "version": row.version, "status": row.status} for row in rows], "bank_state_changed": False}
