"""Small optional narration atoms from verified, owned, read-only tool facts.

These are display selections, never execution parameters or model calculations.
Unknown types stay unknown; a model can arrange references but cannot edit them.
"""
from decimal import Decimal

from .redaction import redact_credentials

# Server-owned exact translations of the current product record contract.
# Unknown Chinese terms are not guessed/translated by the narration model.
_PRODUCT_TERM_ENGLISH={
    '开放式，无锁定期':'Open-ended, no lock-in period',
    '记账成功即到账':'Available after successful posting',
}


def english_product_term(value):
    if not isinstance(value,str) or not value.strip(): return None
    if value in _PRODUCT_TERM_ENGLISH: return _PRODUCT_TERM_ENGLISH[value]
    import re
    return value if not re.search(r'[\u3400-\u9fff]',value) else None


def optional_reply_facts(trace,language):
    if trace.get('state')!='SUCCEEDED' or (trace.get('outcome') or {}).get('verified') is not True or trace.get('pending_confirmation'):
        return []
    english=language=='en'; atoms=[]
    from .customer_read_question_facts import (KEY as QUESTION_FACTS_KEY, SUBSCRIPTION_VERSION,
        SUBSCRIPTION_STATUS_VERSION, read_question_response)
    question_facts = trace.get(QUESTION_FACTS_KEY)
    subscription_question_owned = (isinstance(question_facts, dict) and question_facts.get('schema_version') in {SUBSCRIPTION_VERSION,SUBSCRIPTION_STATUS_VERSION}
        and read_question_response(trace, language) is not None)
    from .customer_query_layout import customer_display_name,semantic_field
    def text(value): return customer_display_name(value) if isinstance(value,str) and value.strip() else None
    def integer(value,minimum=0): return type(value) is int and minimum <= value <= 2**53-1
    def money(value,currency): return (f'{currency} {Decimal(value)/100:.2f}' if english else f'{Decimal(value)/100:.2f}'+('人民币元' if currency=='CNY' else '美元')) if integer(value) and currency in {'CNY','USD'} else None
    def add(label,value,fields=()):
        if value and len(atoms)<4:
            atoms.append({'fact_id':f'fact_{len(atoms)}','label':label,'text':value,'required':False,'semantic_fields':[field for field in fields if field]})
    for node in trace.get('nodes',[]):
        if not isinstance(node,dict) or node.get('status')!='SUCCEEDED' or not isinstance(node.get('result'),dict): continue
        result=node['result']; kind=node.get('action')
        if kind=='SUBSCRIPTION_LIST':
            if subscription_question_owned:
                continue  # The required v3 question answer already owns these exact facts.
            subscriptions=result.get('subscriptions')
            if not isinstance(subscriptions,list): continue
            for row in subscriptions:
                if not isinstance(row,dict): continue
                name=text(row.get('merchant')); status=row.get('status'); fee=money(row.get('amount_minor'),row.get('currency','CNY'))
                if name and status in {'ACTIVE','CANCELLED'} and fee:
                    record=row.get('subscription_id') or row.get('merchant') or subscriptions.index(row)
                    from .subscription_answer_facts import renewal_answer
                    state = renewal_answer(row,language)
                    add('Recorded subscription fee and status',
                        f'{state} The recorded price is {fee} per billing cycle; it is not a next-debit promise.' if english
                        else f'{state} 原登记每期价格为{fee}，不是下一次应扣款。',
                        [semantic_field(node,field,record=record,currency=row.get('currency','CNY'))
                            for field in ('merchant','status','amount_minor','auto_renew','entitled')])
                    from .customer_response import _subscription_datetime
                    from datetime import timezone,timedelta
                    end=_subscription_datetime(row.get('entitlement_end_at'))
                    if end:
                        date=end.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
                        expiry=f'{name}: the recorded access expires at {date}.' if english else f'{name}的权益到期时间是{date}。'
                    else:
                        expiry=(f'{name}: the current record does not provide an access expiry date. The billing date is not an expiry date.' if english else
                            f'{name}的现有记录未提供权益到期日；扣款日期不是会员到期时间。')
                    add('Recorded service access expiry or explicit missing date',expiry,
                        [semantic_field(node,'entitlement_end_at',record=record,currency=row.get('currency','CNY'))])
        elif kind=='WEALTH_COMPARE':
            products=result.get('products')
            if not isinstance(products,list): continue
            products=sorted([row for row in products if isinstance(row,dict)],key=lambda row:row.get('suitable') is not True)
            for row in products:
                terms=row.get('terms'); name=text(row.get('name'))
                if not isinstance(terms,dict) or not name: continue
                term=text(terms.get('term')); timing=text(terms.get('redemption_timing')); risk=row.get('risk_level')
                if english:
                    term,timing=english_product_term(term),english_product_term(timing)
                if term and timing and integer(risk,1) and risk<=5 and type(row.get('suitable')) is bool:
                    suitability='suitability passed' if row['suitable'] else 'not currently suitable'
                    record=row.get('product_id') or row.get('name') or products.index(row)
                    add('Actual product liquidity and risk conditions',
                        f'{name}: R{risk}; term: {term}; redemption: {timing}; {suitability}. Suitability does not guarantee principal or returns.' if english
                        else f'{name}：R{risk}，期限{term}，赎回{timing}；{"当前风险适配通过" if row["suitable"] else "当前不适配"}。适配不代表保本或收益保证。',
                        [semantic_field(node,field,record=record,currency=row.get('currency')) for field in ('name','risk_level','term','redemption_timing','suitability')])
        elif kind=='CARBON_VIEW':
            from .customer_response import read_query_coverage
            coverage=read_query_coverage(kind,result)
            if coverage['status']=='UNAVAILABLE_SCOPE' or coverage['status']=='FILTERED' and 'REWARDS' not in coverage['selected_dimensions']:
                continue
            wallet=result.get('wallet'); rewards=result.get('rewards')
            if not isinstance(wallet,dict) or not integer(wallet.get('points')) or not isinstance(rewards,list): continue
            for row in rewards:
                if not isinstance(row,dict): continue
                name=text(row.get('reward_name')); cost=row.get('cost_points'); stock=row.get('stock')
                if not name or not integer(cost,1) or not integer(stock) or type(row.get('can_redeem')) is not bool: continue
                eligible=row['can_redeem'] and stock>0 and wallet['points']>=cost
                gap=max(0,cost-wallet['points'])
                state=('out of stock' if stock==0 else 'eligible to prepare redemption' if eligible else f'{gap} more points needed' if gap else 'not currently redeemable') if english else \
                    ('暂无库存' if stock==0 else '可准备兑换' if eligible else f'还差{gap}积分' if gap else '暂不可兑换')
                record=row.get('reward_id') or row.get('reward_name') or rewards.index(row)
                add('Reward eligibility and point gap',f'{name}: {cost} points required; {state}.' if english else f'{name}：需{cost}积分，{state}。',
                    [semantic_field(node,field,record=record,currency='POINTS') for field in ('reward_name','cost_points','eligibility','gap')])
        elif kind=='BILL_ANALYSIS':
            from .customer_bill_read_projection import bill_node_trace
            scoped = bill_node_trace(trace, node)
            if scoped is None: continue
            analysis=scoped.get('analysis') or result.get('analysis')
            if not isinstance(analysis,dict): continue
            from .billing_presentation import owned_bill_display_recorded
            breakdown = (analysis.get('financial_insights') or {}).get('instrument_breakdown')
            if owned_bill_display_recorded(analysis) and isinstance(breakdown,dict) and breakdown.get('status')=='RECORDED':
                names={'DEBIT':('借记卡','Debit card'),'CREDIT':('信用卡','Credit card'),'UNKNOWN':('未标明卡种','Card type unspecified')}
                lines=[]
                for group in breakdown['groups']:
                    label=names[group['instrument_type']][1 if english else 0]; curr=group['currency']
                    lines.append(f"{label} ({curr}): consumption {money(group['consumption_minor'],curr)}, recorded fees {money(group['fees_minor'],curr)}, refunds {money(group['refund_minor'],curr)}." if english
                        else f"{label}（{curr}）：消费{money(group['consumption_minor'],curr)}，已登记费用{money(group['fees_minor'],curr)}，退款{money(group['refund_minor'],curr)}。")
                if lines:
                    if any(group['instrument_type']=='UNKNOWN' for group in breakdown['groups']):
                        lines.append('Some records are not linked to a specific card.' if english else '部分记录未关联到具体卡片。')
                    lines.append('Funds movements, including repayments/transfers, are not consumption.' if english else '资金搬移（含还款/划转）不计消费。')
                    add('Recorded card-type breakdown',' '.join(lines),[semantic_field(node,'instrument_breakdown',period=[analysis.get('period_start'),analysis.get('period_end')])])
            scope=analysis.get('category_filter')
            if isinstance(scope,dict) and scope.get('version')=='bill-category-filter-v1' and scope.get('basis')=='CATEGORY_EXACT_MATCH' \
                and scope.get('currency')=='CNY' and scope.get('read_only') is True and scope.get('bank_state_changed') is False:
                name=text(scope.get('category'))
                if name: add('Explicit statement category scope',f'Only {name} transactions are included, not the whole statement.' if english else f'本次仅统计{name}分类，不是整期账单总额。',
                    [semantic_field(node,'scope',period=[analysis.get('period_start'),analysis.get('period_end')],currency='CNY')])
            categories=analysis.get('category_spending_minor'); total=analysis.get('spending_minor')
            if not isinstance(categories,dict) or not integer(total) or not all(integer(value) for value in categories.values()) or sum(categories.values())!=total: continue
            for name,value in sorted(categories.items(),key=lambda item:item[1],reverse=True)[:2]:
                if text(name): add('Recorded spending category',f'{text(name)}: recorded spending {money(value,"CNY")}.' if english else f'{text(name)}：已记录消费{money(value,"CNY")}。',
                    [semantic_field(node,'category_spending_minor',record=name,period=[analysis.get('period_start'),analysis.get('period_end')],currency='CNY')])
    from .bill_light_commentary import bill_light_facts
    # Preserve old atoms/ref identities; v2 may offer at most two approved
    # alternatives. The reply validator permits selecting only one of them.
    atoms.extend(bill_light_facts(trace,language)[:max(0,4-len(atoms))])
    return atoms
