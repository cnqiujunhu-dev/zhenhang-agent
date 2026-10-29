"""Pure, finite credit projection. This does not establish bank authorization."""
from decimal import Decimal


def _number(value, minimum=0): return type(value) is int and value>=minimum
def _id(value): return isinstance(value,str) and bool(value.strip())
def _money(value,currency): return f"{Decimal(value)/100:.2f} {currency}"


def credit_status_summary(last4, status, *, source_current, english=False):
    """Text only; caller must possess the private current-read display proof."""
    states = {
        'LOST': ('已挂失，不能用于新的消费。', 'reported lost and cannot be used for new purchases.'),
        'LOCKED': ('已锁定，当前不能用于新的消费。', 'locked and cannot currently be used for new purchases.'),
        'ISSUED': ('尚未激活，当前不能用于新的消费。', 'awaiting activation and cannot currently be used for new purchases.'),
        'REPLACED': ('已更换，旧卡不能再用于新的消费。', 'replaced; the old card cannot be used for new purchases.'),
        'ACTIVE': ('状态正常。', 'active.'),
    }
    if source_current is not True or not isinstance(status, str) or status not in states:
        return (('The current status of this credit card is not verified.' if english else '这张信用卡的当前状态暂未核实。'),
            ('Query it again before relying on it for a purchase; no new banking action was taken.' if english else
             '可重新查询后再判断是否能消费；本次没有执行新的银行操作。'))
    intro = (f'This read shows credit card ending {last4} is {states[status][1]}' if english else
        f'本次查询显示尾号{last4}信用卡{states[status][0]}')
    detail = ('CNY and USD share one CNY-based credit pool; credit is not cash.' if english else
        '人民币和美元共用人民币计价的授信池；信用额度不是现金。')
    if status == 'ACTIVE':
        intro += (' A purchase still needs available credit, the transaction limit and current transaction checks; payment is not guaranteed.' if english else
            '消费仍需核对可用额度、单笔限额和交易条件，不保证一定可付。')
    return intro, detail


def credit_read_recorded(kind,result):
    if not isinstance(result,dict) or result.get("action")!=kind or result.get("bank_state_changed") is not False:return False
    if kind=="CREDIT_VIEW":
        cards=result.get("cards");apps=result.get("applications")
        return result.get("schema_version")=="credit-view-v1" and isinstance(cards,list) and isinstance(apps,list) \
            and all(isinstance(card,dict) and card.get("instrument_type")=="CREDIT" and card.get("credit_is_cash") is False
                and card.get("base_currency")=="CNY" and card.get("shared_credit_limit") is True
                and all(_number(card.get(key)) for key in ("base_credit_limit_minor","used_base_exposure_minor","authorization_base_exposure_minor","available_base_credit_minor")) for card in cards)
    if kind=="CREDIT_STATEMENT":
        rows=result.get("statements_by_currency")
        return result.get("schema_version")=="credit-statement-v1" and _id(result.get("card_id")) and isinstance(rows,list) \
            and all(isinstance(row,dict) and row.get("currency") in {"CNY","USD"} and all(_number(row.get(key)) for key in
                ("outstanding_minor","current_due_minor","minimum_due_minor","principal_minor","fees_minor","interest_minor")) for row in rows)
    return False


def credit_write_recorded(kind,result):
    if not isinstance(result,dict) or result.get("action")!=kind:return False
    schema=result.get("schema_version")
    if kind=="CREDIT_REPAY":return schema=="credit-repayment-v1" and result.get("status")=="POSTED" and _id(result.get("repayment_id")) and _number(result.get("amount_minor"),1) and result.get("currency") in {"CNY","USD"} and result.get("ledger_balanced") is True
    if kind=="CREDIT_SETTLE_INSTALLMENT":return schema=="credit-repayment-v1" and result.get("status")=="SETTLED" and _id(result.get("installment_id")) and _id(result.get("repayment_id")) and result.get("ledger_balanced") is True
    if kind=="CREDIT_INSTALLMENT":return schema=="credit-installment-v1" and result.get("status")=="ACTIVE" and _id(result.get("installment_id")) and _number(result.get("principal_minor"),1) and _number(result.get("fee_minor")) and result.get("new_consumption_minor")==0
    if kind in {"CREDIT_APPLY","CREDIT_LIMIT_REQUEST"}:return schema=="credit-application-v1" and result.get("status")=="PENDING_REVIEW" and _id(result.get("application_id")) and result.get("credit_limit_changed") is False
    if kind in {"CREDIT_ACTIVATE","CARD_LOCK","CARD_UNLOCK","CARD_REPORT_LOSS","CARD_REPLACEMENT","CARD_SET_LIMIT"}:
        return schema=="credit-card-control-v1" and result.get("instrument_type")=="CREDIT" and _id(result.get("card_id")) and _number(result.get("version"),1) and result.get("status")=={
            "CREDIT_ACTIVATE":"ACTIVE","CARD_LOCK":"LOCKED","CARD_UNLOCK":"ACTIVE","CARD_REPORT_LOSS":"LOST","CARD_REPLACEMENT":"REPLACED","CARD_SET_LIMIT":"ACTIVE"}[kind]
    return False



def credit_facility_display_groups(result):
    """Use owned facility identities, not equal limits or two zero debts.

    This is a display-only consumer after the normal authenticated read proof.
    A missing/conflicting facility never becomes a common pool by inference.
    Internal IDs and policy fingerprints do not enter the returned projection.
    """
    from .card_identifiers import CARD_NUMBER_PATTERN
    from .credit_pool_facts import REGISTERED_POLICY_VERSION
    import re
    if not isinstance(result, dict) or not isinstance(result.get('cards'), list):
        return None
    groups = {}; seen_cards = set(); seen_numbers = set(); seen_tails = set()
    maximum = 2**53 - 1
    def integer(value, minimum=0):
        return type(value) is int and minimum <= value <= maximum
    for card in result['cards']:
        if not isinstance(card, dict): return None
        identifier, facility, number = (card.get(field) for field in ('card_id', 'credit_account_id', 'card_number'))
        if not all(isinstance(value, str) and value.strip() for value in (identifier, facility, number)) \
                or not re.fullmatch(CARD_NUMBER_PATTERN, number) or identifier in seen_cards or number in seen_numbers \
                or number[-4:] in seen_tails:
            return None
        policy = card.get('policy')
        if card.get('instrument_type') != 'CREDIT' or card.get('credit_is_cash') is not False \
                or card.get('base_currency') != 'CNY' or card.get('supported_currencies') != ['CNY', 'USD'] \
                or card.get('shared_credit_limit') is not True or not integer(card.get('facility_version'), 1) \
                or not isinstance(policy, dict) or policy.get('policy_version') != REGISTERED_POLICY_VERSION \
                or policy.get('base_currency') != 'CNY' or policy.get('supported_currencies') != ['CNY', 'USD'] \
                or policy.get('shared_credit_limit', True) is not True:
            return None
        values = ('base_credit_limit_minor', 'used_base_exposure_minor', 'authorization_base_exposure_minor', 'available_base_credit_minor')
        if not all(integer(card.get(field)) for field in values) \
                or sum(card[field] for field in values[1:]) != card['base_credit_limit_minor']:
            return None
        balances = card.get('balances')
        if not isinstance(balances, list) or len(balances) != 2 or not all(isinstance(row, dict)
                and isinstance(row.get('currency'), str) and row['currency'] in {'CNY', 'USD'} for row in balances) \
                or {row.get('currency') for row in balances} != {'CNY', 'USD'}:
            return None
        balance_fields = ('principal_minor', 'fees_minor', 'interest_minor', 'credit_balance_minor', 'outstanding_minor')
        checked_balances = []
        for balance in sorted(balances, key=lambda row: row['currency']):
            if not all(integer(balance.get(field)) for field in balance_fields) \
                    or sum(balance[field] for field in balance_fields[:3]) != balance['outstanding_minor']:
                return None
            checked_balances.append({field: balance[field] for field in ('currency', *balance_fields)})
        proof = {field: card[field] for field in ('facility_version', 'base_currency', 'supported_currencies', 'shared_credit_limit', *values)}
        proof.update(policy=policy, balances=checked_balances)
        group = groups.get(facility)
        if group is None:
            group = groups[facility] = {'proof': proof, 'card_tails': [], 'limit_minor': card['base_credit_limit_minor']}
        elif proof != group['proof']:
            return None
        group['card_tails'].append(number[-4:])
        seen_cards.add(identifier); seen_numbers.add(number); seen_tails.add(number[-4:])
    return [{key: group[key] for key in ('card_tails', 'limit_minor')} for group in groups.values()]


def credit_facility_read_summary(result, english=False):
    groups = credit_facility_display_groups(result)
    if groups is None:
        return (('Your credit-card records were returned.' if english else '已查询你的信用卡。'),
            ('The facility grouping is not fully verified. Do not assume these cards share one limit or add their limits; credit is not cash.' if english else
             '授信关联信息还未完整核对，不能认定这些卡共用同一份额度，也不能把卡片额度相加；信用额度不是现金。'))
    if not groups:
        return (('No credit cards were returned in this read.' if english else '本次没有返回信用卡记录。'),
            ('There is no returned facility relationship to compare; credit is not cash.' if english else '本次没有可核对的授信关联；信用额度不是现金。'))
    intro = (f"This read found {sum(len(group['card_tails']) for group in groups)} credit cards across {len(groups)} facilities." if english else
        f"本次查到{sum(len(group['card_tails']) for group in groups)}张信用卡，分属{len(groups)}份授信。")
    pieces = []
    for index, group in enumerate(groups, 1):
        tails = ', '.join(group['card_tails']) if english else '、'.join(group['card_tails'])
        limit = _money(group['limit_minor'], 'CNY')
        pieces.append((f"Facility {index}, cards ending {tails}: CNY and USD share this facility's CNY-based limit of {limit}." if english else
            f"第{index}份授信（卡片尾号{tails}）：人民币和美元共用本份人民币计价的授信额度{limit}。"))
    pieces.append(('Separate facilities are not combined. Debts remain separate by currency; credit is not cash.' if english else
        '不同授信账户的额度不合并，人民币和美元欠款仍分开记账；信用额度不是现金。'))
    return intro, ' '.join(pieces)


def credit_summary(kind,result,english=False):
    if kind=="CREDIT_VIEW":
        return credit_facility_read_summary(result, english)
    if kind=="CREDIT_STATEMENT":
        pieces=[row["currency"]+": "+_money(row["outstanding_minor"],row["currency"])+(" outstanding" if english else "未还欠款") for row in result.get("statements_by_currency",[])]
        return ("Your statements are grouped by currency." if english else "已按币种列出信用卡账单。","; ".join(pieces) if english else "；".join(pieces)+"。最低应还不等于全额还清。")
    if kind=="CREDIT_REPAY":return ("Repayment posted." if english else "信用卡还款已入账。",_money(result["amount_minor"],result["currency"])+(" repaid; this is not new spending." if english else "已还入对应币种欠款，不计为新增消费。"))
    if kind=="CREDIT_INSTALLMENT":return ("Installment recorded." if english else "分期已办理。",("Principal " if english else "分期本金")+_money(result["principal_minor"],result["currency"])+("; total fee " if english else "，登记总费用")+_money(result["fee_minor"],result["currency"])+(". No duplicate principal consumption." if english else "；未重复计入消费本金。"))
    if kind=="CREDIT_SETTLE_INSTALLMENT":return ("Installment settled." if english else "该笔分期已提前结清。",("Remaining principal and registered fees were repaid; no additional early-settlement penalty." if english else "剩余本金与原登记费用已偿还，未新增提前结清罚费。"))
    if kind in {"CREDIT_APPLY","CREDIT_LIMIT_REQUEST"}:return ("Application submitted." if english else "申请已提交。",("Pending administrator review; no card activation or credit-limit change has occurred." if english else "当前待审核；不代表已发卡激活或已调整授信额度。"))
    if kind=="CREDIT_ACTIVATE":return ("Credit card activated." if english else "信用卡已激活。",("CNY and USD still share the same credit pool." if english else "人民币和美元仍共用同一授信池。"))
    if kind=="CARD_REPLACEMENT":return ("Replacement issued; activation required." if english else "已生成待激活补卡。",("The old lost card is not restored; activate the replacement separately." if english else "旧挂失卡未恢复，新卡需本人另行核对并激活。"))
    return None
