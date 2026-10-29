"""Closed compatibility sources, not inferred subjects from retrieved records.

Only the legacy router calls the subject bridge. Its existing entry matcher
selects the question's scope before retrieval; current whole-record equality
is still required. This module grants no read or execution authority.
"""
import re


def credential_channel_source():
    # Keep the existing v1 record byte-for-byte compatible.
    return {"source_id": "kb:security.credentials-channel-v1", "title": "登录与业务验证的安全边界",
        "kind": "PRODUCT_KNOWLEDGE", "reference": "conversation-policy:credentials-channel-v1",
        "version": "credentials-channel-v1", "content":
        "密码用于专用登录控件，验证码用于对应的专用验证控件；聊天不会使用或代填密码、验证码、密钥或令牌。"
        "聊天中询问验证机制、忘记密码或收不到验证码仅作咨询，不创建授权或执行银行操作。"
        "需要二次验证的业务应在当前任务验证控件操作，并核对本人、当前版本、金额、币种与对象；验证不能替代最终确认。"
        "不要在聊天中发送凭据。平台未提供的密码重置或验证码重发功能不能凭解释假称已完成；可使用实际可用的登录/验证入口，无法操作时联系服务支持。"}


def verified_legacy_public_kwargs(message, records, *, credential_help=False):
    """Forward only an established question scope plus its exact current source.

    Never select a subject from source_ids. Reuse the legacy catalogue's
    existing bilingual matchers, requiring a single, supported match. Mixed
    questions and unknown subjects remain unavailable, not a document dump.
    """
    from .consultation_fallback import _policy
    from .product_knowledge import ENTRIES, ENGLISH_ENTRIES
    matches = [identifier for identifier, _title, pattern, _text in ENTRIES
        if pattern and (re.search(pattern, message, re.I)
            or re.search(ENGLISH_ENTRIES[identifier][1], message, re.I))]
    if credential_help is True:
        # Verification can be about the existing transfer-confirmation scope;
        # it cannot silently absorb a separate product/currency/goal question.
        if set(matches) - {'transfer.confirm-v1'}:
            return {}
        identifier, subject = 'security.credentials-channel-v1', 'policy:credentials-channel'
    else:
        scopes = {'fx.quote-v1': 'topic:FX', 'goals.available-v1': 'policy:goal-reservations'}
        if len(matches) != 1 or matches[0] not in scopes:
            return {}
        identifier, subject = matches[0], scopes[matches[0]]
    expected = _policy(identifier)
    matching = [row for row in records if row.get('source_id') == expected['source_id']]
    if matching != [expected]:
        return {}
    return {'public_subject_ref': subject, 'question_aspects': ['RULES'], 'rule_aspects': ['GENERAL']}


def registered_state_fallback(records, language):
    """Render only already-authorized legacy card/subscription/goal records.

    No KB/document/retrieval loop, no money inferred from an untyped field,
    and no full card identifiers. The caller retains live ownership/source
    revalidation; these established formatters only provide presentation.
    """
    from .customer_consultation import _bank_content, _format_bank, _format_bank_english
    allowed = ('facts:cards', 'facts:subscriptions', 'facts:goals')
    rendered = []
    for identifier in allowed:
        matching = [row for row in records if row.get('source_id') == identifier]
        if len(matching) != 1:
            continue
        record = matching[0]
        content = _bank_content(record)
        if not isinstance(content, list) or not all(isinstance(row, dict) for row in content):
            continue
        renderer = _format_bank_english if language == 'en' else _format_bank
        rendered.append(renderer(record, content, {}))
    return '\n\n'.join(rendered) if rendered else None


def registered_public_price_refs(records, facts):
    """A named public catalogue selection, never a private recurring charge.

    The caller must revalidate the current catalogue after inference. Require
    the complete registered terms and exact server selector here, then reuse
    the existing typed MINOR fact catalogue rather than parse model prose.
    An unselected/all-products catalogue is not a licence to dump its prices.
    """
    rows = [row for row in records if row.get('source_id') == 'facts:subscription-products']
    if len(rows) != 1:
        return []
    record = rows[0]
    if (record.get('kind') != 'SCOPED_BUSINESS_FACTS'
        or record.get('reference') != 'subscription-catalog:registered-products'
        or record.get('authority') != 'REGISTERED_PRODUCT_TERMS_NOT_PAYMENT_OR_RECURRING_AUTHORIZATION'
        or not isinstance(record.get('catalog_filter'), str) or not record['catalog_filter']):
        return []
    import json
    try:
        products = json.loads(record['content'])
    except (KeyError, ValueError, TypeError):
        return []
    fields = {'merchant', 'monthly_price_minor', 'currency', 'version', 'status',
        'can_start_here', 'supported_months', 'default_months', 'default_auto_renew'}
    if not isinstance(products, list) or len(products) != 1 or not isinstance(products[0], dict):
        return []
    product = products[0]
    if (set(product) != fields or product['merchant'] != record['catalog_filter']
        or product['currency'] != 'CNY' or product['status'] != 'AVAILABLE'
        or product['can_start_here'] is not True or type(product['version']) is not int or product['version'] <= 0
        or type(product['monthly_price_minor']) is not int or product['monthly_price_minor'] <= 0
        or type(product['default_auto_renew']) is not bool):
        return []
    months = product['supported_months']
    if (not isinstance(months, dict) or set(months) != {'min', 'max'}
        or any(type(months[key]) is not int for key in months)
        or not 0 < months['min'] <= months['max']
        or type(product['default_months']) is not int
        or not months['min'] <= product['default_months'] <= months['max']):
        return []
    refs = [key for key, fact in facts.items() if fact.get('source_id') == record['source_id']
        and fact.get('field') == 'monthly_price_minor' and fact.get('unit') == 'MINOR'
        and fact.get('currency') == product['currency'] and fact.get('value') == product['monthly_price_minor']]
    return refs if len(refs) == 1 else []
