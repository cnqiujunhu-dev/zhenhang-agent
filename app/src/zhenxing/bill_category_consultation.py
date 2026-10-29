"""Arithmetic over a selected, already rechecked bill-result catalogue.

The catalogue's SAME-result summary supplies the denominator. A selection of
category rows never becomes a guessed full-period total. No database, tool,
intent phrase matching, account selection or execution authority lives here.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import os
import re
from zoneinfo import ZoneInfo

from .planning import canonical_hash

ASPECT = 'BILL_CATEGORY_FACT'
VERSION = 'selected-bill-category-arithmetic-v1'
AUTHORITY = 'VERIFIED_READ_FACTS_NOT_WRITE_AUTHORIZATION'
UNIT = 'QUERIED_OBJECT_ASPECT'
CONTEXT_MARKER = 'selected_bill_category_context_version'
CONTEXT_VERSION = 'same-result-bill-category-context-v1'


def source_context(catalog, entity):
    """Narrow same-result summary from a caller-validated FULL catalogue.

    Stored v2 sources deliberately use compact_catalog, which contains only
    one selected entity. Attach this opt-in, replay-checked display context
    before compaction; do not guess a denominator from compact category rows.
    """
    if not isinstance(catalog,dict) or not isinstance(entity,dict) \
            or entity.get('domain') != 'BILLS' or entity.get('field') != 'category_filter' \
            or entity.get('read_kind') != 'BILL_ANALYSIS' \
            or catalog.get('catalog_hash') != canonical_hash({key:value for key,value in catalog.items() if key!='catalog_hash'}):
        return None
    summaries=[row for row in catalog.get('entities',[]) if row.get('domain')=='BILLS'
        and row.get('read_kind')=='BILL_ANALYSIS' and row.get('field')=='bill_query_summary'
        and row.get('result_ref')==entity.get('result_ref')]
    if len(summaries)!=1:
        return None
    summary=summaries[0]
    context={'version':CONTEXT_VERSION,'catalog_hash':catalog['catalog_hash'],'result_ref':entity['result_ref'],
        'summary_entity':deepcopy(summary),'summary_fact_refs':[deepcopy(row) for row in catalog['fact_refs']
            if row.get('entity_ref')==summary['entity_ref']]}
    context['context_hash']=canonical_hash(context)
    return context


def _entity_fields(catalog, entity):
    rows = [row for row in catalog.get('fact_refs', []) if row.get('entity_ref') == entity.get('entity_ref')]
    if len({row['field'] for row in rows}) != len(rows) or {row['fact_ref'] for row in rows} != set(entity.get('fact_refs', [])):
        raise ValueError('BILL_CATEGORY_FACT_SOURCE_INVALID')
    fields = {row['field']: row['value'] for row in rows}
    if entity.get('facts') != fields or entity.get('live_record_hash') != canonical_hash(fields):
        raise ValueError('BILL_CATEGORY_FACT_SOURCE_INVALID')
    return fields


def _amount(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def selected_category_facts(selected):
    """Return new display facts plus exactly the source IDs they replace.

    ``selected`` contains only _registered_content-checked records. The caller
    still performs its original current owner/catalog/source publication gate.
    Missing/ambiguous/inconsistent summaries do not acquire arithmetic facts.
    """
    groups = {}
    for row, content in selected:
        if content.get('domain') != 'BILLS':
            continue
        fields = {fact['field']: fact['value'] for fact in content['facts']}
        binding = row.get('source_binding') or {}
        aspects = binding.get('question_aspects') or []
        if not set(aspects) & {'SUMMARY', 'CHARGE'} or not {'category', 'spending_minor'} <= fields.keys():
            continue
        catalog = binding.get('catalog') or {}
        key = (catalog.get('catalog_hash'), content.get('result_ref'), fields.get('currency'))
        groups.setdefault(key, []).append((row, content, fields))
    facts, handled = {}, set()
    for (catalog_hash, result_ref, currency), entries in groups.items():
        try:
            catalog = entries[0][0]['source_binding']['catalog']
            if currency not in {'CNY', 'USD'} or not isinstance(result_ref, str) or not result_ref:
                continue
            context=entries[0][0]['source_binding'].get('bill_category_source_context')
            full_catalog=catalog
            if context is not None:
                if not isinstance(context,dict) or context.get('version')!=CONTEXT_VERSION \
                        or context.get('catalog_hash')!=catalog_hash or context.get('result_ref')!=result_ref \
                        or context.get('context_hash')!=canonical_hash({key:value for key,value in context.items() if key!='context_hash'}):
                    continue
                full_catalog={'entities':[context['summary_entity']],'fact_refs':context['summary_fact_refs']}
            elif catalog_hash != canonical_hash({key:value for key,value in catalog.items() if key!='catalog_hash'}):
                continue
            summaries = [entity for entity in full_catalog['entities'] if entity.get('domain') == 'BILLS'
                and entity.get('read_kind') == 'BILL_ANALYSIS' and entity.get('field') == 'bill_query_summary'
                and entity.get('result_ref') == result_ref]
            if len(summaries) != 1:
                continue
            summary = _entity_fields(full_catalog, summaries[0])
            from .bill_query_context import VERSION as QUERY_VERSION
            if summary.get('query_context_version') != QUERY_VERSION or summary.get('recorded_totals_known') is not True \
                    or summary.get('refund_accounting') != 'REFUNDS_REPORTED_SEPARATELY_NOT_SUBTRACTED_FROM_CONSUMPTION':
                continue
            left, right = (datetime.fromisoformat(summary[key]) for key in ('period_start', 'period_end'))
            if left.tzinfo is None or right.tzinfo is None or left >= right:
                continue
            totals = [row for row in summary['totals_by_currency'] if row.get('currency') == currency]
            if len(totals) != 1:
                continue
            total = totals[0].get('economic_spending_minor')
            categories = totals[0].get('category_spending_minor')
            if not _amount(total) or total <= 0 or not isinstance(categories, dict) or not categories \
                    or any(not isinstance(name, str) or not name or len(name) > 100 or not _amount(value)
                        for name, value in categories.items()) or sum(categories.values()) != total:
                continue
            chosen = {}
            for row, content, fields in entries:
                binding = row['source_binding']
                scope=summary.get('billing_scope')
                unfiltered_owned_scope='billing_scope' not in fields and isinstance(scope,dict) \
                    and scope.get('scope')=='ALL_OWNED_CASH_AND_CREDIT' and not {'card_filter','currency_filter'} & scope.keys()
                if binding['catalog'].get('catalog_hash') != catalog_hash \
                        or binding.get('bill_category_source_context') != context or any(fields.get(key) != summary.get(key)
                        for key in ('period_start', 'period_end', 'coverage')) \
                        or not unfiltered_owned_scope and fields.get('billing_scope') != scope \
                        or fields.get('currency') != currency or not _amount(fields.get('spending_minor')) \
                        or categories.get(fields.get('category')) != fields['spending_minor']:
                    raise ValueError('BILL_CATEGORY_FACT_SOURCE_INVALID')
                entity = next(entity for entity in binding['catalog']['entities'] if entity['entity_ref'] == binding['resolution']['entity_ref'])
                if entity.get('read_kind') != 'BILL_ANALYSIS' or entity.get('field') != 'category_filter' \
                        or entity.get('result_ref') != result_ref or _entity_fields(binding['catalog'], entity) != fields:
                    raise ValueError('BILL_CATEGORY_FACT_SOURCE_INVALID')
                if fields['category'] in chosen and chosen[fields['category']]['fields'] != fields:
                    raise ValueError('BILL_CATEGORY_FACT_SOURCE_INVALID')
                chosen[fields['category']] = {'source_id': row['source_id'], 'fields': fields}
            sources = list(dict.fromkeys(row['source_id'] for row, _, _ in entries))
            source_basis = {'version': VERSION, 'catalog_hash': catalog_hash, 'result_ref': result_ref,
                'summary_hash': canonical_hash(summary), 'denominator_minor': total,
                'currency': currency, 'period_start': summary['period_start'], 'period_end': summary['period_end'],
                'billing_scope': deepcopy(summary.get('billing_scope') or {}), 'coverage': deepcopy(summary.get('coverage') or {}),
                'full_category_count': len(categories), 'selected_category_count': len(chosen),
                'ranking_scope': 'FULL_RECORDED_SCOPE' if set(chosen) == set(categories) else 'SELECTED_CATEGORIES_ONLY',
                'rounding': 'ONE_DECIMAL_HALF_UP_WITH_EXPLICIT_TINY_TAIL_BOUND'}
            digest = canonical_hash(source_basis)[:20]
            scope_id = 'bill_category_scope_' + digest
            def fact(identifier, label, values, required=False):
                return {'fact_id': identifier, 'source_id': sources[0], 'source_ids': sources, 'field': ASPECT,
                    'unit': UNIT, 'currency': 'NONE', 'required': required, 'label': label,
                    'value': {'authority': AUTHORITY, 'domain': 'BILLS', 'aspect': ASPECT,
                        'fields': values, 'bank_state_changed': False}}
            facts[scope_id] = fact(scope_id, 'Selected bill card, recorded period, currency and category-share denominator',
                {'kind': 'SCOPE', **source_basis}, True)
            highest, lowest = max(row['fields']['spending_minor'] for row in chosen.values()), min(row['fields']['spending_minor'] for row in chosen.values())
            for name, chosen_row in chosen.items():
                amount = chosen_row['fields']['spending_minor']
                rank = ('highest' if amount == highest else 'lowest' if amount == lowest else 'other')
                ties = sum(row['fields']['spending_minor'] == amount for row in chosen.values())
                identifier = 'bill_category_value_' + canonical_hash([digest, name])[:20]
                facts[identifier] = fact(identifier, name + ' amount and share; ' + rank + (' tied' if ties > 1 else ''),
                    {'kind': 'CATEGORY', **source_basis, 'category': name, 'category_minor': amount,
                        'rank': rank, 'tie_count': ties})
            handled.update(sources)
        except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
            continue
    return facts, handled


def is_category_fact(fact):
    return isinstance(fact, dict) and fact.get('field') == ASPECT and fact.get('unit') == UNIT \
        and isinstance(fact.get('value'), dict) and fact['value'].get('aspect') == ASPECT


def _period(fields, english):
    left, right = (datetime.fromisoformat(fields[key]).astimezone(ZoneInfo('Asia/Shanghai')) for key in ('period_start', 'period_end'))
    if left.day == right.day == 1 and all(getattr(moment, key) == 0 for moment in (left, right) for key in ('hour', 'minute', 'second', 'microsecond')):
        months = (right.year * 12 + right.month) - (left.year * 12 + left.month)
        if months == 1:
            return left.strftime('%Y-%m') if english else f'{left.year}年{left.month}月'
        if months == 12 and left.month == right.month == 1:
            return str(left.year) if english else f'{left.year}年全年'
    return left.strftime('%Y-%m-%d %H:%M') + (' to ' if english else '至') + right.strftime('%Y-%m-%d %H:%M') + (' (end exclusive)' if english else '（结束时点不含）')


def render_category_fact(fact, language=None):
    if not is_category_fact(fact):
        return None
    value = fact['value']; fields = value.get('fields') or {}
    if value.get('authority') != AUTHORITY or value.get('bank_state_changed') is not False \
            or fields.get('version') != VERSION or fields.get('currency') not in {'CNY', 'USD'} \
            or not _amount(fields.get('denominator_minor')) or fields['denominator_minor'] <= 0:
        return None
    english = language == 'en'
    if fields.get('kind') == 'SCOPE':
        try:
            period = _period(fields, english)
        except (KeyError, ValueError, TypeError):
            return None
        scope = fields.get('billing_scope') or {}
        tail = (scope.get('card_filter') or {}).get('display_last4')
        card = (f'card ending {tail}, ' if english else f'尾号{tail}的卡，') if isinstance(tail, str) and re.fullmatch(r'[A-Za-z0-9]{4}', tail) else ''
        amount = f"{Decimal(fields['denominator_minor']) / 100:.2f} {fields['currency']}"
        text = (f'For {card}{period}, the recorded category-spending total is {amount}.' if english else
            f"{card}{period}的已记录分类支出合计{amount}。")
        if (fields.get('coverage') or {}).get('status') != 'COMPLETE':
            text += (' This covers available records, not a proven complete-period total.' if english else '这里只计算已覆盖记录，不当作完整期间总额。')
        return text
    amount = fields.get('category_minor')
    if fields.get('kind') != 'CATEGORY' or not _amount(amount) or amount > fields['denominator_minor'] \
            or not isinstance(fields.get('category'), str) or fields.get('rank') not in {'highest', 'lowest', 'other'} \
            or type(fields.get('tie_count')) is not int or fields['tie_count'] < 1:
        return None
    from .bill_light_commentary import _share, SAFE_CATEGORIES
    name = SAFE_CATEGORIES.get(fields['category'], fields['category']) if english else fields['category']
    amount_text = f"{Decimal(amount) / 100:.2f} {fields['currency']}"
    share = _share(amount, fields['denominator_minor'], english=english)
    rank = ''
    if fields['rank'] != 'other':
        selected = fields.get('ranking_scope') == 'SELECTED_CATEGORIES_ONLY'
        tied = fields['tie_count'] > 1
        if english:
            rank = ', ' + ('tied for ' if tied else '') + ('highest' if fields['rank'] == 'highest' else 'lowest') + (' among the selected categories' if selected else ' in the recorded categories')
        else:
            rank = '，' + ('在所选类别中' if selected else '在已记录类别中') + ('并列' if tied else '') + ('最多' if fields['rank'] == 'highest' else '最少')
    return f'{name}: {amount_text}, {share} of the same-currency recorded spending{rank}.' if english else f'{name}支出{amount_text}，占同币种已记录支出{share}{rank}。'


def light_remark_input(facts, language=None):
    style = os.getenv('ZX_BILL_COMMENT_STYLE', 'LIGHT').strip().upper()
    supplied = {'bill_comment_policy': {'style': style}, 'needed_questions': [], 'facts': [], 'reply_language': language}
    if style != 'LIGHT':
        return supplied
    from .bill_light_commentary import SAFE_CATEGORIES
    for fact in facts.values():
        if not is_category_fact(fact):
            continue
        fields = fact['value']['fields']
        if fields.get('kind') != 'CATEGORY' or fields.get('rank') not in {'highest', 'lowest'} \
                or fields.get('tie_count') != 1 or fields.get('category') not in SAFE_CATEGORIES:
            continue
        text = render_category_fact(fact, language)
        if text:
            supplied['facts'].append({'fact_id': fact['fact_id'], 'text': text.rstrip('.。'),
                'selection_group': 'BILL_LIGHT', 'source_basis': deepcopy(fields)})
    return supplied
