"""Customer wording from explicit owned subscription authorization fields.

Registered price, future auto-debit and paid access are different facts.
Unknown flags never become false and an expiry is not a scheduled payment.
"""
from datetime import timedelta, timezone
from decimal import Decimal

from .redaction import redact_credentials


def renewal_answer(row, language='zh-CN'):
    if not isinstance(row, dict):
        return None
    merchant = row.get('merchant')
    if not isinstance(merchant, str) or not merchant.strip():
        return None
    name = redact_credentials(merchant.strip())[:120]
    english = language == 'en'
    if row.get('auto_renew') is False:
        if row.get('source_type') not in {'BANK_MANDATE', 'MERCHANT_SIM'}:
            return (f'{name}: the record shows automatic renewal is off, but the external debit channel is not verified here. Check future charges with the provider.' if english else
                f'{name}的记录显示已关闭自动续费；这里未核验外部扣款渠道，实际后续扣费需向服务商确认。')
        return (f'{name}: automatic renewal is currently off. Under the current setting, there is no next automatic-renewal debit.' if english else
            f'{name}已关闭自动续费；按当前设置，不会再自动续费扣款。')
    if row.get('auto_renew') is True and row.get('status') == 'ACTIVE':
        return (f'{name}: automatic renewal is currently on; future debits follow the currently authorized renewal plan.' if english else
            f'{name}已开启自动续费，后续扣款按当前授权的续费计划处理。')
    return (f'{name}: the next automatic-debit setting needs verification.' if english else
        f'{name}的后续自动扣款安排还需要核对。')


def access_answer(row, language='zh-CN'):
    if not isinstance(row, dict):
        return None
    from .customer_response import _subscription_datetime
    end = _subscription_datetime(row.get('entitlement_end_at'))
    english = language == 'en'
    if end:
        date = end.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        if row.get('entitled') is True:
            return (f'Paid access remains usable until {date} (China time).' if english else
                f'已付权益仍可使用，到期时间为{date}。')
        return (f'The recorded access expiry is {date} (China time); this is not a next-debit date.' if english else
            f'记录的权益到期时间为{date}，这不是下次扣款时间。')
    if row.get('entitled') is True:
        return ('Existing paid access is usable; its expiry was not returned.' if english else
            '现有付费权益仍可使用，但记录未提供到期时间。')
    return ('The access expiry was not returned; a billing date is not an access expiry.' if english else
        '记录未提供权益到期时间，不能把扣款日当作权益到期日。')


def authorization_answer(row, language='zh-CN', *, include_charge=False):
    """One service answer; an inactive billing date is not a future debit."""
    renewal = renewal_answer(row, language)
    if renewal is None:
        return None
    lines = [renewal, access_answer(row, language)]
    amount, months, currency = row.get('amount_minor'), row.get('billing_months'), row.get('currency')
    if include_charge and type(amount) is int and 0 <= amount <= 2**53-1 \
            and type(months) is int and 1 <= months <= 12 and currency in {'CNY', 'USD'}:
        lines.append((f'The previous registered amount was {Decimal(amount)/100:.2f} {currency} per {months} month(s); it is not a next-debit amount.' if language == 'en' else
            f'原登记每{months}个月费用为{Decimal(amount)/100:.2f} {currency}，不是下一次应扣款。'))
    return ' '.join(lines)
