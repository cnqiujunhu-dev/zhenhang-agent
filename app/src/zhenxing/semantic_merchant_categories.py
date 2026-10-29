"""Registered category-set field grammar; never splits a product identity."""
import re
import unicodedata
from .semantic_read_aliases import CATEGORY_ALIASES


CATEGORIES = ('CAKE', 'FLOWER', 'RESTAURANT')


def canonical_categories(value):
    if type(value) is not list or not 1 <= len(value) <= 3 or any(type(item) is not str or item not in CATEGORIES for item in value) \
            or len(set(value)) != len(value):
        raise ValueError('SEMANTIC_MERCHANT_CATEGORIES_INVALID')
    return sorted(value)


def category_literal(raw, selected):
    wanted = canonical_categories(selected)
    if not isinstance(raw, str) or not 1 <= len(raw) <= 200:
        raise ValueError('SEMANTIC_MERCHANT_CATEGORIES_SOURCE_INVALID')
    text = unicodedata.normalize('NFKC', raw).strip().casefold()
    text = re.sub(r'^(?:已登记的|登记的|registered\s+)', '', text)
    parts = re.split(r'\s*(?:、|，|,|和|与|及|&|\band\b)\s*', text)
    aliases = {alias.casefold(): category for category, values in CATEGORY_ALIASES.items() for alias in values}
    aliases.update({alias + 's': category for alias, category in list(aliases.items()) if alias.isascii() and not alias.endswith('s')})
    found = []
    for part in parts:
        if part not in aliases:
            part = re.sub(r'(?:商品|产品|\s+(?:products?|items?))$', '', part).strip()
        category = aliases.get(part)
        if category is None: raise ValueError('SEMANTIC_MERCHANT_CATEGORIES_SOURCE_INVALID')
        found.append(category)
    if len(set(found)) != len(found) or sorted(found) != wanted:
        raise ValueError('SEMANTIC_MERCHANT_CATEGORIES_SOURCE_INVALID')
    return wanted
