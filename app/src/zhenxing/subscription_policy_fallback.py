"""A current registered cancellation boundary, not an inferred refund receipt.

Only an already selected, owned, connected subscription can anchor this
fallback. It changes no guard and selects no subject from source IDs alone.
"""
import re


def owned_cancellation_boundary(records, structured, language, *, public_subject_ref=None, rule_aspects=()):
    if public_subject_ref not in {None, 'topic:SUBSCRIPTIONS'}:
        return None
    rules = set(rule_aspects or ())
    if not rules or 'CANCELLATION' not in rules or not rules <= {'CANCELLATION', 'SETTLEMENT'}:
        return None
    if not isinstance(structured, dict) or not structured:
        return None
    for fact in structured.values():
        value = fact.get('value') if isinstance(fact, dict) else None
        if (not isinstance(value, dict) or value.get('domain') != 'SUBSCRIPTIONS'
                or value.get('aspect') != 'SUBSCRIPTION_AUTHORIZATION'
                or value.get('bank_state_changed') is not False
                or fact.get('required') is not True
                or value.get('fields', {}).get('source_type') not in {'BANK_MANDATE', 'MERCHANT_SIM'}):
            return None
    from .consultation_fallback import _policy
    expected = _policy('subscription.scope-v1')
    matches = [row for row in records if row.get('source_id') == expected['source_id']]
    if matches != [expected] or expected.get('version') != 'zhenxing-product-knowledge-20261013-subscription-rights-v8':
        return None
    text = expected['content_en'] if language == 'en' else expected['content']
    sentences = [part.strip() for part in re.split(r'(?<=[。！？])|(?<=[.!?])\s+', text) if part.strip()]
    index = 7 if language == 'en' else 6
    if index >= len(sentences):
        return None
    # This recipe belongs to the exact stock v8 record checked above. It is
    # not the opening of a generic source dump or an external refund promise.
    return sentences[index], {'fallback_scope': 'REGISTERED_SUBSCRIPTION_CANCELLATION_BOUNDARY',
        'fallback_policy_source_ids': [expected['source_id']],
        'question_coverage': {'complete': 'SETTLEMENT' not in rules,
            'covered_rule_aspects': ['CANCELLATION'],
            'missing_rule_aspects': ['SETTLEMENT'] if 'SETTLEMENT' in rules else [],
            'coverage_boundary': 'CANCELLATION_VS_RETAINED_PAID_ACCESS_NOT_ACTUAL_REFUND_RECEIPT'},
        'refund_receipt_claimed': False}
