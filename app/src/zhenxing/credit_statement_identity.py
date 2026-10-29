"""Versioned statement card identity, without historic values or write scope."""
import re

from .planning import canonical_hash

VERSION = 'credit-statement-card-identity-v1'
MARKER = 'card_identity_projection_version'
FIELD = 'credit_card_read_identity'
KIND = 'CREDIT_STATEMENT_CARD_READ_IDENTITY'
CONSUMER_VERSION = 'offered-statement-card-read-identity-v1'
CONSUMERS = ('CREDIT_STATEMENT', 'CREDIT_VIEW')
_KEYS = frozenset({'card_id', 'card_number', 'credit_account_id', 'owner_scope', MARKER,
    'facts_authority', 'credit_is_cash', 'identity_only'})


def projection_version(result):
    if MARKER not in result:
        return None
    if type(result[MARKER]) is not str or result[MARKER] != VERSION:
        raise ValueError('CREDIT_STATEMENT_IDENTITY_VERSION_INVALID')
    return VERSION


def identity_facts(result):
    """The read fingerprint binds these existing result fields plus opt-in."""
    from .card_identifiers import CARD_NUMBER_PATTERN
    if projection_version(result) is None:
        return None
    values = {key: result.get(key) for key in ('card_id', 'card_number', 'credit_account_id', 'owner_scope')}
    if any(not isinstance(value, str) or not value for value in values.values()) \
            or re.fullmatch(CARD_NUMBER_PATTERN, values['card_number']) is None \
            or re.fullmatch(r'[a-f0-9]{64}', values['owner_scope']) is None:
        raise ValueError('CREDIT_STATEMENT_IDENTITY_INVALID')
    return {**values, MARKER: VERSION, 'facts_authority': 'OWNED_CREDIT_BOOK',
        'credit_is_cash': False, 'identity_only': True}


def checked_identity_facts(entity):
    if (not isinstance(entity, dict) or entity.get('kind') != KIND
            or entity.get('domain') != 'CREDIT' or entity.get('read_kind') != 'CREDIT_STATEMENT'
            or entity.get('field') != FIELD):
        return None
    facts = entity.get('facts')
    if not isinstance(facts, dict) or set(facts) != _KEYS or facts.get('identity_only') is not True \
            or facts.get('credit_is_cash') is not False or facts.get('facts_authority') != 'OWNED_CREDIT_BOOK' \
            or entity.get('live_record_hash') != canonical_hash(facts):
        return None
    try:
        if identity_facts(facts) != facts or entity.get('value') != facts['card_number']:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return facts


def consumer_contract():
    return {'consumer_contract_version': CONSUMER_VERSION, 'consumer_kinds': list(CONSUMERS)}


def semantic_identity_metadata(entity):
    return {'kind': KIND, **consumer_contract()} if checked_identity_facts(entity) is not None else {}


def visible_catalog_entities(entities):
    """One current card query need not offer a duplicate, read-only alias.

    Only collapse exact immutable card/facility IDs within the same verified
    catalog. Do not replace an older catalog or broaden its consumer contract.
    """
    keys = ('card_id', 'card_number', 'credit_account_id')
    current = {tuple(row.get('facts', {}).get(key) for key in keys) for row in entities
        if row.get('field') == 'credit_card_status' and row.get('read_kind') == 'CREDIT_VIEW'
        and row.get('domain') == 'CREDIT' and row.get('facts', {}).get('facts_authority') == 'OWNED_CREDIT_BOOK'
        and row.get('live_record_hash') == canonical_hash(row.get('facts', {}))}
    return [row for row in entities if not ((facts := checked_identity_facts(row)) is not None
        and tuple(facts[key] for key in keys) in current)]
