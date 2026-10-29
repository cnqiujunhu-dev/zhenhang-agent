"""Source-bound partial product terms after rejected free-text narration.

Public plan rules cannot establish a product's contents or its refund policy.
Selected product facts are already model-selected and independently checked by
the publishing caller; this renderer preserves their per-product association.
"""
from .dialogue_reference_facts import _registered_content
from .merchant_service_content import content_notice

VERSION = 'merchant-terms-partial-fallback-v1'


def partial_terms_fallback(records, language, *, subject_ref, question_aspects,
        rule_aspects, public_rule_renderer):
    """Combine checked RULES with explicit missing TERMS, never all-green.

No word matching, source selection, product lookup or bank operation occurs.
Only the exact current NOT_REGISTERED content schema is supported here.
"""
    aspects = set(question_aspects or ())
    if not {'TERMS', 'RULES'} <= aspects or not aspects <= {'TERMS', 'RULES', 'SUMMARY'}:
        return None
    selected = []
    for record in records:
        content = _registered_content(record)
        if not content or content.get('domain') != 'MERCHANTS':
            continue
        proof = record['source_binding']
        entities = [row for row in proof['catalog']['entities']
            if row['entity_ref'] == proof['resolution']['entity_ref']]
        if len(entities) != 1 or (entities[0].get('read_kind'), entities[0].get('field')) != ('MERCHANT_CATALOG', 'merchant_product_id'):
            continue
        selected.append((record, content))
    if subject_ref not in {'policy:birthday-plan', 'policy:merchant'} and not selected:
        return None
    rules = list(dict.fromkeys(rule_aspects or ['GENERAL']))
    english = language == 'en'
    lines, product_sources, known_refund_sources, product_fact_refs = [], [], [], []
    all_product_refunds_known = bool(selected)
    for record, content in selected:
        fields = {fact['field']:fact['value'] for fact in content['facts']}
        label = content.get('label')
        if not isinstance(label, str) or not label.strip() or len(label) > 240:
            continue
        notice = content_notice({'service_content': fields.get('service_content')}, language)
        # Missing old fields are not backfilled as NOT_REGISTERED. Both paths
        # explicitly retain the absence of specific contents, with provenance.
        if notice is None:
            notice = ('Its specific included items, size, portions and delivery details have not been verified.' if english
                else '尚无可核对的具体内容、规格、份量或配送明细，暂不能确认。')
        lines.append(label + (': ' if english else '：') + notice)
        product_sources.append(record['source_id'])
        product_fact_refs.extend(fact['fact_ref'] for fact in content['facts'] if fact['field'] in {'service_content', 'terms'})
        terms = fields.get('terms')
        refund = terms.get('refund_policy') if isinstance(terms, dict) else None
        if refund == 'BEFORE_FULFILMENT_ONLY':
            known_refund_sources.append(record['source_id'])
            if 'REFUND' in rules:
                lines.append(label + (': The registered policy permits a refund request before fulfilment; '
                    'a request is not a credited refund. Any refund must follow its original payment instrument and receipt.'
                    if english else '：登记退款条件为履约前可以申请退款；提出申请不代表已经退款到账，须按原付款工具和退款回执核对。'))
        else:
            all_product_refunds_known = False
            if 'REFUND' in rules:
                lines.append(label + (': Its specific refund conditions have not been verified.' if english
                    else '：这项商品的具体退款条件尚未核对，不能套用其他商品的规则。'))
    if not product_sources:
        lines.append('No verifiable details for the specific product were obtained; its included items, size, portions or delivery cannot be confirmed.'
            if english else '本次尚未取得可核对的具体商品明细，不能确认该商品包含的内容、规格、份量或配送服务。')
    supported = {'policy:birthday-plan':{'GENERAL','CANCELLATION','REFUND'},
        'policy:merchant':{'GENERAL','REFUND','SETTLEMENT','PROCESS'}}.get(subject_ref, set())
    supported_rules = [rule for rule in rules if rule in supported]
    # Unsupported aspects remain in the original requested list and missing
    # coverage below. They must not suppress independent checked rule facts,
    # nor be silently reclassified as GENERAL to make an all-green answer.
    policy_text, policy_scope = public_rule_renderer(supported_rules) if supported_rules else ('', {})
    policy_known = policy_scope.get('fallback_scope') == 'REGISTERED_SUBJECT_POLICY' \
        and policy_scope.get('question_coverage', {}).get('complete') is True
    covered = set(policy_scope.get('question_coverage', {}).get('covered_rule_aspects', ())) if policy_known else set()
    if policy_known:
        lines.append(('The following are general plan/order rules, not proof of this product\'s contents, specific refund eligibility or a completed cancellation or refund.\n'
            if english else '以下仅说明计划或订单的一般规则，不代替该商品明细或具体退款条件，也不表示已经取消或退款。\n') + policy_text)
    elif not ('REFUND' in rules and known_refund_sources):
        lines.append('The cancellation or refund rules also need a verifiable policy before they can be confirmed.' if english
            else '取消或退款的一般规则还缺少可核对的依据，暂不能确认。')
    if selected and 'REFUND' in rules:
        if all_product_refunds_known and len(known_refund_sources) == len(selected):
            covered.add('REFUND')
        else:
            # A generic plan policy must not cover an unknown selected SKU's
            # refund terms, or borrow a second product's refund availability.
            covered.discard('REFUND')
    return '\n\n'.join(lines), {'fallback_scope':'REGISTERED_POLICY_PRODUCT_DETAILS_PARTIAL',
        'merchant_terms_fallback_version':VERSION,
        'fallback_policy_source_ids':list(policy_scope.get('fallback_policy_source_ids', ())) if policy_known else [],
        'selected_product_source_ids':list(dict.fromkeys(product_sources)),
        'selected_product_fact_refs':list(dict.fromkeys(product_fact_refs)),
        'product_contents_registered':False, 'bank_state_changed':False,
        'question_coverage':{'complete':False, 'covered_rule_aspects':[rule for rule in rules if rule in covered],
            'missing_rule_aspects':[rule for rule in rules if rule not in covered],
            'missing_question_aspects':['TERMS'],
            'coverage_boundary':'SPECIFIC_PRODUCT_DETAILS_UNAVAILABLE_GENERAL_POLICY_IS_NOT_PRODUCT_CONTENT_OR_REFUND_RECEIPT'}}
