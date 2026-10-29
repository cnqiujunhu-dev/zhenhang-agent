"""Read-time wording for sealed v3/v4 subscription details, not new authority."""
from decimal import Decimal

from .customer_read_question_facts import KEY, SUBSCRIPTION_VERSION, SUBSCRIPTION_STATUS_VERSION, read_question_response
from .customer_query_layout import customer_display_name
from .subscription_answer_facts import access_answer, renewal_answer


def subscription_question_display(trace, question_reply):
    """Keep specific fee/status answers, without reciting the whole card.

    The original snapshot, fact versions, debit authority, raw expiry and cache
    are untouched. A recorded renewal amount is not a public new-service price.
    """
    if not isinstance(question_reply, dict) or question_reply.get('reply_language') not in {'zh-CN', 'en'} \
            or read_question_response(trace, question_reply['reply_language']) != question_reply:
        return None
    source=trace[KEY];aspects=set(source['question_aspects'])
    if source['schema_version'] not in {SUBSCRIPTION_VERSION,SUBSCRIPTION_STATUS_VERSION} \
            or not aspects <= {'SUMMARY','STATUS','PRICE','CHARGE'} or aspects == {'SUMMARY'} \
            or any(node['action']!='SUBSCRIPTION_LIST' for node in trace['nodes']):
        return None
    facts=source['facts'];english=question_reply['reply_language']=='en';lines=[]
    allowed={'status','auto_renew','entitled','entitlement_end_at','next_due_at','amount_minor',
        'billing_months','currency','source_type','subscription_authorization_version'}
    for fact in facts:
        value=fact.get('value') or {};fields=value.get('fields');name=customer_display_name(value.get('label'))
        if value.get('domain')!='SUBSCRIPTIONS' or not isinstance(fields,dict) or not name \
                or set(fields)-allowed or value.get('authority')!='VERIFIED_READ_FACTS_NOT_WRITE_AUTHORIZATION':
            return None
        if source['schema_version']==SUBSCRIPTION_VERSION and (
                fields.get('subscription_authorization_version')!='owned-subscription-charge-v1'
                or value.get('aspect') not in {'SUMMARY','CHARGE','PRICE','SUBSCRIPTION_AUTHORIZATION'}):
            return None
        if source['schema_version']==SUBSCRIPTION_STATUS_VERSION and value.get('aspect')!='SUBSCRIPTION_AUTHORIZATION':
            return None
        row={**fields,'merchant':name};parts=[]
        if aspects & {'SUMMARY','CHARGE','PRICE'}:
            amount,months,currency=fields.get('amount_minor'),fields.get('billing_months'),fields.get('currency')
            if type(amount) is int and 0<=amount<=2**53-1 and currency in {'CNY','USD'}:
                period=(('month' if months==1 else f'{months} months') if english else ('月' if months==1 else f'{months}个月')) \
                    if type(months) is int and 1<=months<=12 else None
                fee=f'{Decimal(amount)/100:.2f} {currency}' if english else f'{Decimal(amount)/100:.2f}{"元" if currency=="CNY" else "美元"}'
                parts.append(('recorded charge ' if english else '登记费用')+fee+(f'/{period}' if period else ('; billing period unavailable' if english else '，费用周期未提供')))
            else:
                parts.append('recorded fee is not available' if english else '登记费用尚未提供')
        access=access_answer(row,question_reply['reply_language'])
        renewal=renewal_answer(row,question_reply['reply_language'])
        if not access or not renewal:
            return None
        opening=name+(': ' if english else '：')+('；'.join(parts)+'；' if parts else '')+access
        # Strip only this pure formatter's exact own label, never user text.
        setting=renewal[len(name):].lstrip(': ') if renewal.startswith(name) else renewal
        text=opening+' '+setting
        # A distinct recorded charge date is material; do not silently omit it.
        due=fields.get('next_due_at')
        if fields.get('auto_renew') is True and due is not None and due!=fields.get('entitlement_end_at'):
            from .customer_response import _subscription_datetime
            from datetime import timedelta,timezone
            at=_subscription_datetime(due)
            if at is None:return None
            stamp=at.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
            text+=f' The recorded next debit date is {stamp} (China time).' if english else f'记录的下次扣款时间为{stamp}。'
        lines.append(text)
    if not lines or len('\n'.join(lines))>2300:
        return None  # Preserve the original complete typed coverage boundary.
    return {**question_reply,'intro':('Here are your recorded subscription details (China time).' if english else
        '这是已有订阅的费用与权益情况，时间均为北京时间。'),'detail':'\n'.join(lines)}
