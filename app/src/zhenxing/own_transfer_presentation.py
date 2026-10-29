"""Finite public own-account receipt display; never posting authority."""
from decimal import Decimal
import re


def own_transfer_write_recorded(action,result):
    return (action=='OWN_ACCOUNT_TRANSFER' and isinstance(result,dict)
        and result.get('schema_version')=='own-account-transfer-receipt-v1'
        and result.get('action')==action and result.get('status')=='POSTED'
        and type(result.get('amount_minor')) is int and 0<result['amount_minor']<=2**53-1
        and result.get('currency')=='CNY'
        and all(isinstance(result.get(key),str) and re.fullmatch(r'[a-fA-F0-9]{4}',result[key])
            for key in ('source_card_last4','destination_card_last4'))
        and result.get('same_owner_internal_movement') is True
        and result.get('outward_transfer_daily_quota_consumed') is False
        and result.get('bank_state_changed') is True and result.get('ledger_balanced') is True
        and result.get('authority')=='VERIFIED_SAME_OWNER_CASH_MOVEMENT')


def own_transfer_summary(action,result,*,english=False):
    if not own_transfer_write_recorded(action,result):
        return ('The account transfer needs verification.','No new receipt is confirmed.') if english else ('本人账户转存还需要核对。','尚无可确认的划转回执。')
    amount=f"{Decimal(result['amount_minor'])/100:.2f}"
    source,destination=result['source_card_last4'],result['destination_card_last4']
    return (('The transfer between your accounts was posted.',
        f'CNY {amount} moved from your debit card ending {source} to your debit card ending {destination}. This is not spending or new income; total owned cash is unchanged.')
        if english else ('本人账户转存已完成。',f'{amount}元已从本人尾号{source}借记卡转存至尾号{destination}借记卡，不计消费、不新增收入，本人现金总额不变。'))
