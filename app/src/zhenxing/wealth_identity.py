"""Exact registered product identities shared by READ selection and proof.

Public catalogue keys and reviewed display names resolve only among the
caller's current bank rows. They never supply a price, holding or permission.
"""
from .customer_query_layout import customer_display_name
from .financial_product_catalog import product_catalog


def product_reference_matches(row, reference):
    if not isinstance(row,dict) or not isinstance(reference,str) or not reference \
            or reference!=reference.strip() or not isinstance(row.get('name'),str):
        return False
    name=row['name'];display=customer_display_name(name)
    aliases={name,display}
    identifier=row.get('product_id')
    if isinstance(identifier,str) and identifier:aliases.add(identifier)
    for public in product_catalog():
        if display==public['name_zh']:
            aliases.update((public['product_key'],public['name_zh'],public['name_en']))
    return reference.casefold() in {value.casefold() for value in aliases if value}


def matching_owned_products(rows,reference):
    """Return every exact candidate; the domain requires exactly one."""
    return [row for row in rows if product_reference_matches(row,reference)]
