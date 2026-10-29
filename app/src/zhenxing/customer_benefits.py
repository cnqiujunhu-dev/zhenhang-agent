"""Read-only registered activity rules and deterministic personal eligibility."""
from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .customer_membership import membership_view
from .customer_profile_common import (CARD_TIERS, CUSTOMER_TIERS, finish_read, profile_clock,
    profile_time_basis, require_profile_actor)
from .customer_profile_models import CustomerBenefitActivity


def benefits_view(session, principal, *, benefit_name=None):
    require_profile_actor(session, principal)
    profile = membership_view(session, principal)
    clock = profile_clock(session, principal)
    rows = list(session.scalars(select(CustomerBenefitActivity).where(
        CustomerBenefitActivity.sandbox_id == principal.sandbox_id).order_by(CustomerBenefitActivity.activity_key)))
    if benefit_name is not None:
        if not isinstance(benefit_name, str) or not benefit_name.strip() or len(benefit_name) > 100:
            raise DomainError("BENEFIT_NAME_REQUIRED", "请明确要查看的已登记活动名称。", 422)
        rows = [row for row in rows if benefit_name in {row.name, row.activity_key}]
        if len(rows) != 1:
            raise DomainError("BENEFIT_NOT_FOUND", "没有找到唯一的已登记活动，请重新核对名称。", 422)
    activities = []
    for row in rows:
        customer_tiers, card_tiers = row.customer_tiers_json, row.card_tiers_json
        if (not isinstance(customer_tiers, list) or not isinstance(card_tiers, list)
                or any(code not in CUSTOMER_TIERS for code in customer_tiers)
                or any(code not in CARD_TIERS for code in card_tiers)
                or as_utc(row.ends_at) <= as_utc(row.starts_at)):
            raise DomainError("BENEFIT_RULE_UNAVAILABLE", "活动规则暂时无法核对，请稍后重试。", 409)
        member = profile["membership"]
        registered_member = member["status"] == "ACTIVE" and member["tier_code"] in CUSTOMER_TIERS
        member_ok = (member["tier_code"] in customer_tiers if registered_member else None) if customer_tiers else True
        matching = [card for card in profile["cards"] if (not card_tiers or card["card_tier_code"] in card_tiers)
                    and (not row.active_card_required or card["status"] == "ACTIVE")]
        if card_tiers or row.active_card_required:
            card_ok = bool(matching)
            if not matching and any(card["card_tier_code"] is None for card in profile["cards"]):
                card_ok = None
        else:
            card_ok = True
        started, unexpired = clock >= as_utc(row.starts_at), clock < as_utc(row.ends_at)
        reasons = [
            {"code": "ACTIVITY_ACTIVE", "message": "活动已启用", "met": row.status == "ACTIVE"},
            {"code": "ACTIVITY_STARTED", "message": "在活动开始时间之后", "met": started},
            {"code": "ACTIVITY_UNEXPIRED", "message": "在活动截止时间之前", "met": unexpired},
            {"code": "CUSTOMER_TIER", "message": "客户等级符合活动要求", "met": member_ok},
            {"code": "CARD_TIER_AND_STATE", "message": "持卡等级与卡片状态符合活动要求", "met": card_ok},
        ]
        values = [reason["met"] for reason in reasons]
        eligible = False if False in values else None if None in values else True
        status = ("DISABLED" if row.status != "ACTIVE" else "NOT_STARTED" if not started else "EXPIRED" if not unexpired
                  else "ELIGIBLE" if eligible is True else "UNDETERMINED" if eligible is None else "INELIGIBLE")
        activities.append({"activity_key": row.activity_key, "name": row.name, "description": row.description,
            "starts_at": as_utc(row.starts_at).isoformat(), "ends_at": as_utc(row.ends_at).isoformat(),
            "status": row.status, "rule_version": row.rule_version,
            "eligibility": {"status": status, "eligible": eligible, "reasons": reasons,
                "matching_cards": [{"last4": card["last4"], "card_tier_code": card["card_tier_code"]} for card in matching]},
            "conditions": {"customer_tiers": customer_tiers, "card_tiers": card_tiers,
                "active_card_required": row.active_card_required},
            "read_only": True, "can_enroll": False, "can_claim": False,
            "programme_scope": "PROJECT_REGISTERED_ACTIVITIES"})
    return finish_read(session, principal, "BENEFITS_VIEW", {"activities": activities,
        "eligibility_as_of": clock.isoformat(), "eligibility_time_basis": profile_time_basis(session, principal),
        "query": {"benefit_name": benefit_name}}, {"membership_read": profile["read_fingerprint"],
        "activities": [{"activity_key": item["activity_key"], "rule_version": item["rule_version"]} for item in activities]})
