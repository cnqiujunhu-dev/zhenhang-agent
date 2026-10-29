"""Opt-in empty-capable bill query facts and identity-only READ selectors.

The caller must first rebuild the exact owned analysis. A query summary is
not a transaction set; a card identity is not a balance or write permission.
"""
from copy import deepcopy

from .contracts import DomainError
from .planning import canonical_hash

VERSION = 'owned-bill-query-context-v1'
MARKER = 'bill_query_context_version'
IDENTITY_KIND = 'BILL_QUERY_CARD_READ_IDENTITY'
CONSUMER_VERSION = 'offered-bill-query-card-read-v1'
TAIL_CONSUMER_VERSION = 'offered-bill-query-card-read-v2'


def context_version(analysis):
    query = (analysis.get('financial_insights') or {}).get('source_query') or {}
    if MARKER not in query:
        return None
    if query[MARKER] != VERSION or type(query[MARKER]) is not str:
        raise ValueError('BILL_QUERY_CONTEXT_VERSION_INVALID')
    return VERSION


def summary_entity(analysis):
    if context_version(analysis) is None:
        return None
    coverage = deepcopy(analysis.get('coverage'))
    known = isinstance(coverage, dict) and coverage.get('status') not in {'UNKNOWN', 'INSUFFICIENT'}
    facts = {key: deepcopy(analysis.get(key)) for key in ('period_start', 'period_end', 'coverage', 'billing_scope')}
    facts.update(record_count=analysis['transaction_count'],
        totals_by_currency=deepcopy(analysis.get('totals_by_currency', [])) if known else [],
        recorded_totals_known=known, empty_record_set=analysis['transaction_count'] == 0,
        empty_does_not_prove_zero=not known, refund_accounting='REFUNDS_REPORTED_SEPARATELY_NOT_SUBTRACTED_FROM_CONSUMPTION',
        query_context_version=VERSION, bank_state_changed=False)
    digest = canonical_hash(facts)
    return {'read_kind': 'BILL_ANALYSIS', 'domain': 'BILLS', 'field': 'bill_query_summary',
        'value': '本人账单查询摘要', 'label': '刚才账单的查询范围与退款口径', 'ordinal': None,
        'record_id': 'bill-query:' + digest, 'record_version': None,
        'live_record_hash': digest, 'facts': facts}


def card_entities(session, principal, analysis):
    """Selected instrument only; never infer a card from an empty row list."""
    if context_version(analysis) is None:
        return []
    query = analysis['financial_insights']['source_query']
    reference = query.get('card_reference')
    if reference is None:
        return []
    from .payment_instruments import resolve_source_instrument
    selected = resolve_source_instrument(session, principal, reference, 'CNY', purpose='READ')
    if selected['instrument_id'] != reference:
        raise DomainError('BILL_QUERY_IDENTITY_SOURCE_CHANGED', '之前查询的卡片身份需要重新核对。', 409)
    facts = {key: selected[key] for key in ('instrument_id', 'instrument_type', 'card_id', 'card_number', 'display_last4')}
    facts.update(owner_scope=canonical_hash({'sandbox_id': principal.sandbox_id, 'user_id': principal.user_id}),
        identity_only=True, query_context_version=VERSION, bank_write_authorized=False)
    # Separate selector types are explicit. Neither the model nor this adapter
    # converts a credit reference into a debit/repayment source.
    fields = ['card_number'] + (['card_reference'] if selected['instrument_type'] == 'CREDIT' else [])
    return [{'read_kind': 'BILL_ANALYSIS', 'domain': 'CREDIT' if field == 'card_reference' else 'CARDS',
        'field': field, 'kind': IDENTITY_KIND, 'value': selected['card_number'],
        'label': ('信用卡' if selected['instrument_type'] == 'CREDIT' else '借记卡') + '尾号' + selected['display_last4'] + ' · 查询中的卡片身份',
        'ordinal': None, 'record_id': selected['card_id'], 'record_version': None,
        'live_record_hash': canonical_hash(facts), 'facts': deepcopy(facts)} for field in fields]


def identity_fields(entity):
    if entity.get('kind') != IDENTITY_KIND or entity.get('read_kind') != 'BILL_ANALYSIS':
        return []
    if (entity.get('domain'), entity.get('field')) == ('CARDS', 'card_number'):
        return ['card_number', 'card_last4']
    if (entity.get('domain'), entity.get('field')) == ('CREDIT', 'card_reference'):
        return ['card_reference']
    return []


def consumer_contract(entity):
    fields = identity_fields(entity)
    if not fields:
        return {}
    number = entity.get('field') == 'card_number'
    consumers = ['BILL_ANALYSIS', 'CARD_STATUS'] if number else ['CREDIT_VIEW', 'CREDIT_STATEMENT']
    return {'consumer_contract_version': TAIL_CONSUMER_VERSION if number else CONSUMER_VERSION,
        'consumer_kinds': consumers}


def semantic_identity_metadata(entity):
    return {'kind': IDENTITY_KIND, **consumer_contract(entity)} if identity_fields(entity) else {}


def checked_consumer_contract(entity):
    contract = consumer_contract(entity)
    fields = identity_fields(entity)
    if entity.get('field') == 'card_number' and entity.get('consumer_contract_version') == CONSUMER_VERSION:
        # Persisted v1 public offers keep their exact one-field declaration and
        # input digest. New aliases are never backfilled into an old receipt.
        fields = ['card_number']
        contract = {**contract, 'consumer_contract_version': CONSUMER_VERSION}
    if not contract or entity.get('allowed_fields') != fields \
            or any(entity.get(key) != value for key, value in contract.items()):
        raise ValueError('SEMANTIC_INPUT_ENTITY_CONSUMER_CONTRACT')
    return contract


def has_identity_route(entity, field, action_kind):
    return field in identity_fields(entity) and action_kind in consumer_contract(entity).get('consumer_kinds', [])


def immutable_identity_value(session, principal, entry, field, action_kind):
    """Original catalog is independently revalidated by the compiler first."""
    entity = entry.get('actual_entity') or entry.get('entity') or {}
    if not has_identity_route(entity, field, action_kind):
        raise DomainError('BILL_QUERY_IDENTITY_READ_ONLY', '之前账单中的卡片身份仅供重新查询。', 409)
    facts = entity.get('facts') or {}
    owner = canonical_hash({'sandbox_id': principal.sandbox_id, 'user_id': principal.user_id})
    if facts.get('owner_scope') != owner or facts.get('identity_only') is not True \
            or facts.get('query_context_version') != VERSION or facts.get('bank_write_authorized') is not False \
            or entity.get('live_record_hash') != canonical_hash(facts):
        raise DomainError('BILL_QUERY_IDENTITY_SOURCE_CHANGED', '之前查询的卡片身份需要重新核对。', 409)
    from .payment_instruments import resolve_source_instrument
    current = resolve_source_instrument(session, principal, facts.get('instrument_id'), 'CNY', purpose='READ')
    if any(current.get(key) != facts.get(key) for key in ('instrument_id', 'instrument_type', 'card_id', 'card_number', 'display_last4')) \
            or entity.get('value') != current['card_number'] or entity.get('record_id') != current['card_id']:
        raise DomainError('BILL_QUERY_IDENTITY_SOURCE_CHANGED', '之前查询的卡片身份需要重新核对。', 409)
    if field == 'card_last4':
        tail = current['card_number'][-4:]
        unique = resolve_source_instrument(session, principal, tail, 'CNY', purpose='READ')
        if unique.get('instrument_id') != current['instrument_id']:
            raise DomainError('BILL_QUERY_IDENTITY_SOURCE_CHANGED', '卡片尾号不能唯一定位原来的卡片，请使用完整卡号。', 409)
        return tail
    return current['card_number']


def completed_multi_bill_scope(session, principal, parent):
    """A shared period exists only when every current BILL child agrees.

    All source/catalog checks still run per operation. This returns navigation
    for a set of independent reads, not one combined financial analysis.
    """
    if parent is None or parent.state != 'SUCCEEDED' \
            or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id):
        return None
    from sqlalchemy import select
    from .models import Task
    from .runtime_models import RuntimeOperation, RuntimeRecord
    from .conversation_models import ChatThread
    record = session.get(RuntimeRecord, parent.id)
    context = (record.metadata_json or {}).get('conversation_context') if record else None
    thread = session.get(ChatThread, context.get('conversation_id')) if isinstance(context, dict) else None
    if not record or not thread:
        return None
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == parent.id,
        RuntimeOperation.active == 1, RuntimeOperation.plan_version == record.plan_version).order_by(RuntimeOperation.ordinal)))
    if len(operations) < 2 or any(op.action_kind != 'BILL_ANALYSIS' or op.status != 'SUCCEEDED' for op in operations):
        return None
    children = [session.get(Task, op.child_task_id) for op in operations]
    try:
        if any(not child or child.state != 'SUCCEEDED' or context_version(child.analysis_json or {}) is None for child in children):
            return None
    except (ValueError, TypeError, AttributeError):
        raise DomainError('BILL_QUERY_IDENTITY_SOURCE_CHANGED', '之前查询的来源版本需要重新核对。', 409)
    from .dialogue_references import build_result_reference_catalog
    catalog = build_result_reference_catalog(session, principal, thread, task_id=parent.id)
    summaries = [entity for entity in catalog['entities'] if entity['field'] == 'bill_query_summary']
    if len(summaries) != len(operations):
        return None
    periods = {(row['facts']['period_start'], row['facts']['period_end']) for row in summaries}
    if len(periods) != 1:
        return None
    start, end = next(iter(periods))
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    from datetime import datetime
    try:
        left, right = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if left.tzinfo is None or right.tzinfo is None or left >= right:
            return None
    except ValueError:
        return None
    return {'version': 'multi-bill-shared-period-scope-v1', 'action': 'BILL_ANALYSIS',
        'origin_task_id': parent.id, 'period_start': start, 'period_end': end,
        'origin_analysis_hash': canonical_hash([child.analysis_json for child in children]),
        'source_kind': 'ORIGINAL_QUERY_RESULT', 'source_catalog_hash': catalog['catalog_hash']}


def render_query_summary(fields, language=None):
    """Natural wording from the original checked summary, never arithmetic."""
    if not isinstance(fields, dict) or fields.get('query_context_version') != VERSION \
            or fields.get('refund_accounting') != 'REFUNDS_REPORTED_SEPARATELY_NOT_SUBTRACTED_FROM_CONSUMPTION' \
            or type(fields.get('recorded_totals_known')) is not bool:
        return None
    english = language == 'en'
    scope = fields.get('billing_scope') or {}
    coverage = fields.get('coverage') or {}
    if not isinstance(scope, dict) or not isinstance(coverage, dict):
        return None
    card = scope.get('card_filter') or {}
    tail = card.get('display_last4') if isinstance(card, dict) else None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    label = 'the recorded history' if english else '已有记录'
    try:
        left, right = datetime.fromisoformat(fields['period_start']), datetime.fromisoformat(fields['period_end'])
        if left.tzinfo is not None and right.tzinfo is not None and left < right:
            left, right = left.astimezone(ZoneInfo('Asia/Shanghai')), right.astimezone(ZoneInfo('Asia/Shanghai'))
            if left.day == right.day == 1 and all(getattr(moment, part) == 0 for moment in (left,right)
                    for part in ('hour','minute','second','microsecond')):
                months = (right.year*12+right.month) - (left.year*12+left.month)
                if months == 1: label = left.strftime('%Y-%m') if english else f'{left.year}年{left.month}月'
                elif months == 12 and left.month == right.month == 1: label = str(left.year) if english else f'{left.year}年全年'
                else: label = left.strftime('%Y-%m-%d') + (' to ' if english else '至') + right.strftime('%Y-%m-%d')
            else: label = left.strftime('%Y-%m-%d') + (' to ' if english else '至') + right.strftime('%Y-%m-%d')
    except (ValueError, TypeError, KeyError):
        pass
    currency = scope.get('currency_filter')
    unit = {'CNY':'CNY', 'USD':'USD'}.get(currency, 'separate-currency') if english else {'CNY':'人民币', 'USD':'美元'}.get(currency, '分币种')
    prefix = (f'This query covers {label}, {unit}' + (f', card ending {tail}' if isinstance(tail,str) else '') + '. ') if english else (
        '这次查询的是' + ('尾号' + tail + '的卡片在' if isinstance(tail,str) else '') + label + '的' + unit + '账单。')
    requested = fields.get('requested_aspects', [])
    if requested and set(requested) <= {'TIME_SCOPE'}:
        return prefix + (('The queried period is not covered by the records.' if english else '这段期间的记录尚未覆盖。')
            if fields['recorded_totals_known'] is False else '')
    basis = ('Refunds are shown separately; the consumption amount does not have refunds subtracted from it.' if english else
        '退款是单独列出的，消费金额没有直接减去退款。')
    if fields['recorded_totals_known'] is False:
        return prefix + (('The records do not cover the queried period, so I cannot confirm whether refunds occurred. '
            'An empty list does not establish zero spending or zero refunds. ') if english else
            '刚才这段期间的账单记录尚未覆盖，暂时不能确认有没有退款；空清单不代表零消费或零退款。') + basis
    totals = fields.get('totals_by_currency')
    if not isinstance(totals, list):
        return None
    from decimal import Decimal
    refunds = []
    seen = set()
    for row in totals:
        if not isinstance(row, dict) or row.get('currency') not in {'CNY', 'USD'} \
                or row['currency'] in seen or type(row.get('refund_minor')) is not int or not 0 <= row['refund_minor'] <= 2**53-1:
            return None
        seen.add(row['currency'])
        amounts = []
        for key, zh, en in (('consumption_minor','消费','consumption'), ('fees_minor','手续费','fees'), ('refund_minor','退款','refunds')):
            if key not in row:
                continue
            if type(row[key]) is not int or not 0 <= row[key] <= 2**53-1:
                return None
            amounts.append((en + ' ' if english else zh) + f"{Decimal(row[key])/100:.2f} {row['currency']}")
        refunds.append((', ' if english else '、').join(amounts))
    detail = (('The recorded amounts in this query are ' + '; '.join(refunds) + '. ') if english else
        '这次查询已登记的金额为' + '；'.join(refunds) + '。') if refunds else (
            'No refund amount was provided for this query. ' if english else '这次查询未提供可核对的退款金额。')
    boundary = ('' if coverage.get('status') == 'COMPLETE' else
        ' Only the covered records are described; this is not a complete-period total.' if english else
        '这里只说明已覆盖记录，不当作完整期间总额。')
    return prefix + detail + basis + boundary
