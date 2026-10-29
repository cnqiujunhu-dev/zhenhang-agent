"""Finite contents projection from a merchant register, never guessed details.

Current products have no included-service field. Say that explicitly instead
of using their price or name to imply dishes, guests, delivery or extra items.
This does not upgrade the product model or write any catalogue record.
"""


def content_notice(row, language='zh-CN'):
    if not isinstance(row, dict):
        return None
    if row.get('service_content') != {'version': 'merchant-service-content-v1', 'status': 'NOT_REGISTERED',
            'included_items': None, 'guest_count': None, 'delivery_included': None}:
        return None
    if language == 'en':
        return ('The registered product does not specify its included items, portions, guest count or delivery services. '
            'Its name, price and stock do not establish those details; confirm them before ordering.')
    return '商品登记中未列明具体内容、份量、适用人数或配送服务；不能仅凭名称、价格或库存推断，购买前需要确认。'
