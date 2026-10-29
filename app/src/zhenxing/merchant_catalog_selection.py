"""Finite READ category sets; never split a product name or grant a purchase.

Categories are unioned within this field. A concrete product name/id remains
an additional AND restriction. Existing scalar/no-filter queries keep their
original dictionaries and v1 result scope, including historical fingerprints.
"""
from copy import deepcopy
import unicodedata

from .contracts import DomainError


CATEGORIES = frozenset({"CAKE", "FLOWER", "RESTAURANT"})
CATEGORY_SCOPE_VERSION = "merchant-catalog-category-set-v1"
SELECTORS = frozenset({"merchant_product_id", "product_name", "merchant_categories"})
CATEGORY_LABELS = {"CAKE": "蛋糕", "FLOWER": "鲜花", "RESTAURANT": "餐厅"}


def catalog_query(values):
    if not isinstance(values, dict) or not set(values) <= SELECTORS:
        raise DomainError("MERCHANT_CATALOG_SELECTOR_INVALID", "商品目录筛选字段无效。", 422)
    result = deepcopy(values)
    for key, value in result.items():
        if key == "merchant_categories":
            if (not isinstance(value, list) or not 1 <= len(value) <= 3
                    or any(not isinstance(item, str) or item not in CATEGORIES for item in value)
                    or len(set(value)) != len(value)):
                raise DomainError("MERCHANT_CATALOG_CATEGORY_SET_INVALID", "请选择不重复的登记商品类别。", 422)
            result[key] = sorted(value)
        elif not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise DomainError("MERCHANT_CATALOG_SELECTOR_INVALID", "请明确要查询的登记商品。", 422)
    return result


def category_scope(query):
    return {"version": CATEGORY_SCOPE_VERSION, **catalog_query(query)}


def category_scope_valid(result):
    """Display shape/source consistency only; full freshness still rereads DB."""
    if not isinstance(result, dict):
        return False
    query = result.get("query")
    scope = result.get("query_scope")
    has_set = isinstance(query, dict) and "merchant_categories" in query or isinstance(scope, dict) and "merchant_categories" in scope
    if not has_set:
        return not isinstance(scope, dict) or scope.get("version") != CATEGORY_SCOPE_VERSION
    try:
        if not isinstance(scope, dict) or scope.get("version") != CATEGORY_SCOPE_VERSION:
            return False
        normalized = catalog_query({key: value for key, value in scope.items() if key != "version"})
        if normalized.get("merchant_categories") != scope.get("merchant_categories") or scope != category_scope(normalized):
            return False
        if query is not None and (catalog_query(query) != query or query != normalized):
            return False
        products = result.get("products")
        from .semantic_read_aliases import CATEGORY_ALIASES
        def name(value): return unicodedata.normalize("NFKC", value).strip().casefold() if isinstance(value, str) else None
        return isinstance(products, list) and all(isinstance(row, dict) and row.get("category") in normalized["merchant_categories"]
            and ("merchant_product_id" not in normalized or row.get("product_id") == normalized["merchant_product_id"])
            and ("product_name" not in normalized or name(row.get("product_name")) == name(normalized["product_name"])
                or name(normalized["product_name"]) in CATEGORY_ALIASES.get(row.get("category"), ())) for row in products)
    except (DomainError, KeyError, TypeError, ValueError):
        return False
