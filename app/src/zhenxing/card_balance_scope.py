"""Finite deposit-card balance READ collections, never a payment denomination."""
from copy import deepcopy
import re


CURRENCIES = ('CNY', 'USD')
VERSION = 'card-balance-scope-v1'


def canonical_currencies(value):
    if type(value) is not list or not 1 <= len(value) <= 2 or any(type(item) is not str or item not in CURRENCIES for item in value) \
            or len(set(value)) != len(value):
        raise ValueError('SEMANTIC_CARD_BALANCE_CURRENCIES_INVALID')
    return sorted(value)


def currency_literal(raw, selected):
    """A field grammar for named currency sets, not an intent/phrase classifier."""
    wanted = canonical_currencies(selected)
    token = r'(?:人民币|CNY|RMB|美元|美金|USD|US\$)'
    if not isinstance(raw, str) or re.fullmatch(token + r'(?:\s*(?:和|与|及|、|，|,|/|&|\band\b)\s*' + token + r')?', raw.strip(), re.I) is None:
        raise ValueError('SEMANTIC_CARD_BALANCE_CURRENCY_SOURCE_INVALID')
    found = ['CNY' if item.upper() in {'CNY', 'RMB'} or item == '人民币' else 'USD'
        for item in re.findall(token, raw, re.I)]
    if len(set(found)) != len(found) or sorted(found) != wanted:
        raise ValueError('SEMANTIC_CARD_BALANCE_CURRENCY_SOURCE_INVALID')
    return wanted


def check_scalar_currency(requested, scalar):
    if scalar is not None and requested != [scalar]:
        raise ValueError('SEMANTIC_CARD_BALANCE_CURRENCY_CONFLICT')


def project_balance_row(row, requested):
    """Keep real support metadata, expose amounts only for requested currencies."""
    requested = canonical_currencies(requested)
    value = deepcopy(row)
    value['requested_balance_currencies'] = requested
    value['unsupported_balance_currencies'] = [key for key in requested if key not in row.get('supported_currencies', [])]
    for key in ('balances_by_currency', 'available_by_currency', 'balance_scope_refs'):
        if key in value:
            value[key] = {currency: amount for currency, amount in value[key].items() if currency in requested}
    if 'CNY' not in requested:
        for key in ('reserved_cash_minor', 'limit_minor', 'limit_currency'):
            value.pop(key, None)
    return value


def balance_scope(rows, requested, reference):
    requested = canonical_currencies(requested)
    return {'schema_version': VERSION, 'card_reference': reference, 'requested_currencies': requested,
        'card_ids': sorted(row['card_id'] for row in rows), 'instrument_type': 'DEBIT',
        'actual_supported_currencies': {row['card_id']: list(row['supported_currencies']) for row in rows},
        'unsupported_currencies': {row['card_id']: [key for key in requested if key not in row['supported_currencies']] for row in rows},
        'currency_balances_are_not_summed': True}


def normalize_card_currency_read(value, prepared):
    """Only the observed equivalent pure-READ multi-option wire form converts."""
    from .intelligence import READ_KINDS
    kinds = value.get('action_kinds', [])
    offered = {row['kind']: row for row in prepared['capabilities']}
    if value.get('mode') != 'REQUEST' or not kinds or any(kind not in READ_KINDS or offered.get(kind, {}).get('read_only') is not True for kind in kinds):
        return value, []
    result = deepcopy(value); events = []
    for row in result.get('changes', []):
        options = row.get('selected_options', [])
        if row.get('field') != 'currency' or type(options) is not list or len(options) <= 1:
            continue
        match = re.fullmatch(r'new:([0-7])', row.get('action_ref', ''))
        if not match or int(match[1]) >= len(kinds) or kinds[int(match[1])] != 'CARD_STATUS':
            continue
        if row.get('operation', 'SET') != 'SET' or row.get('entity_ref') is not None:
            raise ValueError('SEMANTIC_CARD_BALANCE_CURRENCY_SOURCE_INVALID')
        from .semantic_query_scope import _source_text
        literal = _source_text(row.get('source_span'), prepared['message'])
        if row.get('source_text') not in (None, literal):
            raise ValueError('SEMANTIC_CARD_BALANCE_CURRENCY_SOURCE_INVALID')
        currency_literal(literal, options)
        row['field'] = 'card_balance_currencies'
        row['selected_options'] = canonical_currencies(options)
        events.append({'code': 'CARD_BALANCE_CURRENCY_COLLECTION', 'action_ref': row['action_ref'],
            'field': row['field'], 'currency_count': len(options), 'source_span_preserved': True, 'bank_write_authorized': False})
    return (result if events else value), events
