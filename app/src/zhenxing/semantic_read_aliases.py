"""Registered product/category navigation names, not request phrase rules."""
from .merchant_models import MerchantProduct, VirtualMerchant

from .merchant_catalog_registry import CATEGORY_ALIASES


def merchant_read_aliases(session, principal, entity):
    if entity.get("read_kind") != "MERCHANT_CATALOG" or entity.get("field") != "merchant_product_id": return []
    product = session.get(MerchantProduct, entity.get("record_id"))
    merchant = session.get(VirtualMerchant, product.merchant_id) if product else None
    facts = entity.get("facts") or {}
    if not merchant or merchant.sandbox_id != principal.sandbox_id or product.name != facts.get("name") \
            or merchant.name != facts.get("merchant_name"): return []
    return list(dict.fromkeys([product.name, *CATEGORY_ALIASES.get(merchant.category, ())]))


def valid_public_read_aliases(entity, aliases):
    """Offer-only syntax. Fresh identity/ownership still uses original proofs."""
    if entity.get("read_kind") != "MERCHANT_CATALOG" or entity.get("field") != "merchant_product_id" \
            or not isinstance(aliases, list) or not 1 <= len(aliases) <= 8 \
            or any(not isinstance(alias, str) or not 1 <= len(alias) <= 100 for alias in aliases) \
            or len(set(aliases)) != len(aliases):
        return False
    # A full registered product name is already part of its public label. Other
    # names must be from the closed category vocabulary, never model additions.
    names = set(alias for values in CATEGORY_ALIASES.values() for alias in values)
    label = entity.get("label", "")
    return all(alias in names or label.endswith(" · "+alias) for alias in aliases)
