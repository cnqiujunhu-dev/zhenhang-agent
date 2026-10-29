"""Independent registered customer/card tiers, never derived from risk or money."""
from sqlalchemy import select

from .authentication import as_utc
from .models import Card
from .card_identifiers import card_identifier_view
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from .customer_profile_common import CARD_TIERS, CUSTOMER_TIERS, finish_read, profile_clock, require_profile_actor
from .customer_profile_models import CustomerCardTier, CustomerMembership


def card_tier_display(session, card):
    """Read-only augmentation of an already owner-checked CARD_STATUS row."""
    row = session.scalar(select(CustomerCardTier).where(CustomerCardTier.card_id == card.id,
        CustomerCardTier.sandbox_id == card.sandbox_id, CustomerCardTier.user_id == card.user_id))
    return {"card_tier_code": row.tier_code if row else None,
        "card_tier_name": CARD_TIERS.get(row.tier_code, "未登记卡等级") if row else "未登记卡等级",
        "card_tier_version": row.version if row else None}


def membership_view(session, principal):
    require_profile_actor(session, principal)
    clock = profile_clock(session, principal)
    row = session.scalar(select(CustomerMembership).where(
        CustomerMembership.sandbox_id == principal.sandbox_id, CustomerMembership.user_id == principal.user_id))
    status = row.status if row else "UNREGISTERED"
    if row and as_utc(row.effective_from) > clock:
        status = "NOT_STARTED"
    if row and row.expires_at and as_utc(row.expires_at) <= clock:
        status = "EXPIRED"
    membership = {
        "tier_code": row.tier_code if row else None,
        "tier_name": CUSTOMER_TIERS.get(row.tier_code, "未登记等级") if row else "未登记等级",
        "status": status, "version": row.version if row else None,
        "source": row.source if row else None,
    }
    assignments = {item.card_id: item for item in session.scalars(select(CustomerCardTier).where(
        CustomerCardTier.sandbox_id == principal.sandbox_id, CustomerCardTier.user_id == principal.user_id))}
    cards = []
    for card in session.scalars(select(Card).where(Card.sandbox_id == principal.sandbox_id,
            Card.user_id == principal.user_id, Card.status != ARCHIVED_RESOURCE_STATUS).order_by(Card.last4, Card.id)):
        tier = assignments.get(card.id)
        identifier = card_identifier_view(session, card) or {}
        cards.append({"card_id": card.id, "last4": identifier.get("display_last4", card.last4), "status": card.status,
            "card_tier_code": tier.tier_code if tier else None,
            "card_tier_name": CARD_TIERS.get(tier.tier_code, "未登记卡等级") if tier else "未登记卡等级",
            "tier_version": tier.version if tier else None, "card_version": card.version})
    from .wealth import risk_profile_view
    risk_state = risk_profile_view(session, principal.sandbox_id, principal.user_id)
    risk_profile = {key: risk_state[key] for key in ("risk_level", "valid", "expires_at")}
    versions = {"membership": {"id": row.id, "version": row.version} if row else None,
        "cards": [{"card_id": item["card_id"], "card_version": item["card_version"],
                   "tier_version": item["tier_version"]} for item in cards], "risk_profile": risk_profile}
    return finish_read(session, principal, "MEMBERSHIP_VIEW", {"membership": membership,
        "cards": cards, "risk_profile": risk_profile, "tier_axes_independent": True}, versions)
