"""Semantic rendering ownership for verified queries, never banking authority.

Cards own their primary facts. Natural narration owns only the short bridge and
necessary notices. A semantic field key includes operation/record, period and
currency; equal numeric values are not evidence of the same financial fact.
"""
import json
import re
from .redaction import redact_credentials

LAYOUT_VERSION='customer-query-layout-v1'


def customer_display_name(value):
    if not isinstance(value,str): return ''
    value=redact_credentials(value)
    value=re.sub(r'[（(](?:仿真|模拟|演示)[）)]$','',value)
    changes={'模拟外部转出':'外部清算转出','外部模拟转账':'外部清算转账','受控外汇仿真付款/换出':'外汇付款/换出',
        '受控外汇仿真收款/换入':'外汇收款/换入','仿真外部清算账户':'外部清算账户','模拟银行代扣':'银行代扣',
        '仿真银行代扣':'银行代扣','仿真商户订阅':'商户订阅','模拟卡片':'卡片','演示理财产品':'理财产品',
        '演示积分':'积分','演示权益':'权益','模拟权益':'权益','当前合成账套':'当前账户','双方合成账务':'双方账户账务'}
    for before,after in changes.items():
        if value==before: value=after
    catalogue={'演示步行5公里':'步行5公里','演示骑行8公里':'骑行8公里','演示地铁20公里':'地铁20公里',
        '演示节能主题权益':'节能主题权益','演示绿色出行权益券':'绿色出行权益券','演示出行权益':'出行权益','演示出行券':'出行券','演示旅行基金':'旅行基金'}
    return catalogue.get(value,value)[:120]


def semantic_field(node,field,*,record=None,period=None,currency=None):
    identity=node.get('operation_id') or node.get('node_id')
    if not isinstance(identity,str) or not identity: return None
    return json.dumps([identity,record,period,currency,field],ensure_ascii=False,separators=(',',':'))


def single_card_currency_bridge(cards,language):
    """Describe one verified card's registered currencies, not payment ability.

    The caller has already checked the owned successful READ and balances.
    Unknown/malformed metadata keeps the existing generic presentation.
    """
    if not isinstance(cards,list) or len(cards)!=1 or not isinstance(cards[0],dict): return None
    row=cards[0];currencies=row.get('supported_currencies')
    tail=row.get('display_last4',row.get('last4'))
    legacy=row.get('last4')
    if not isinstance(tail,str) or not re.fullmatch('[0-9a-fA-F]{4}',tail) \
            or legacy is not None and legacy!=tail and (row.get('legacy_last4')!=legacy
                or not isinstance(legacy,str) or not re.fullmatch('[0-9a-fA-F]{4}',legacy)) \
            or not isinstance(currencies,list) or not 1<=len(currencies)<=2 \
            or any(not isinstance(value,str) or value not in {'CNY','USD'} for value in currencies) \
            or len(set(currencies))!=len(currencies): return None
    number=row.get('card_number')
    if number is not None and (not isinstance(number,str)
            or not re.fullmatch(r'(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[0-9]{12,19}',number)
            or not number.casefold().endswith(tail.casefold())): return None
    selected=[value for value in ('CNY','USD') if value in currencies]
    if language=='en':
        return f"Card ending {tail} has registered currencies {' and '.join(selected)}. Its balance and current status are shown below."
    labels={'CNY':'人民币','USD':'美元'}
    return f"尾号{tail}这张卡的币种为{'、'.join(labels[value] for value in selected)}，余额与当前状态列在下面。"


def query_layout(trace,language):
    from .customer_response import _recorded
    from .intelligence import READ_KINDS
    nodes=trace.get('nodes')
    if trace.get('state')!='SUCCEEDED' or (trace.get('outcome') or {}).get('verified') is not True or trace.get('pending_confirmation') \
        or not isinstance(nodes,list) or not nodes or any(not isinstance(node,dict) or node.get('action') not in READ_KINDS or not _recorded(node,trace) for node in nodes): return None
    if sum(node['action']=='BILL_ANALYSIS' for node in nodes)>1: return None  # a shared trace.analysis cannot prove separate periods
    english=language=='en'; owned=[]; bridges=[]; notices=[]; kinds=[]; focus=None; node_focus={}
    for node in nodes:
        kind=node['action'];result=node.get('result') or {}; kinds.append(kind)
        if kind=='BILL_ANALYSIS':
            analysis=trace.get('supplementary_bill') or trace.get('analysis') or result.get('analysis')
            if not isinstance(analysis,dict): return None
            facts=analysis.get('financial_insights');coverage=analysis.get('coverage')
            if not isinstance(facts,dict) or facts.get('version')!='financial-diagnostics-v1' or facts.get('currency')!='CNY' or facts.get('unit')!='MINOR' \
                or facts.get('read_only') is not True or facts.get('bank_state_changed') is not False or facts.get('model_calculated_money') is not False \
                or not isinstance(coverage,dict) or coverage.get('status') not in ('COMPLETE','PARTIAL','OBSERVED_HISTORY') or coverage.get('basis')=='LIVE_ACTIVITY_ONLY': return None
            selected=analysis.get('category_filter')
            if selected is not None and (not isinstance(selected,dict) or selected.get('version')!='bill-category-filter-v1' \
                or selected.get('basis')!='CATEGORY_EXACT_MATCH' or selected.get('currency')!='CNY' or selected.get('read_only') is not True \
                or selected.get('bank_state_changed') is not False or facts.get('category_filter')!=selected): return None
            from .customer_bill_fallback import checked_bill_fallback,fallback_month_labels
            if analysis.get('period_fallback') and not checked_bill_fallback(analysis): return None
            period=[analysis.get('period_start'),analysis.get('period_end')]
            for field in ('scope','spending_minor','transaction_count'):
                owned.append(semantic_field(node,field,period=period,currency='CNY'))
            categories=analysis.get('category_spending_minor')
            if not isinstance(categories,dict) or any(type(value) is not int or not 0<=value<=2**53-1 for value in categories.values()) \
                or type(analysis.get('transaction_count')) is not int or not 0<=analysis['transaction_count']<=2**53-1 \
                or type(analysis.get('spending_minor')) is not int or not 0<=analysis['spending_minor']<=2**53-1 or sum(categories.values())!=analysis['spending_minor']: return None
            for category,_ in sorted(categories.items(),key=lambda pair:pair[1],reverse=True)[:3]:
                owned.append(semantic_field(node,'category_spending_minor',record=category,period=period,currency='CNY'))
            question=facts.get('question')
            if question in {'SPENDING_CHANGE','SAVING_OPTIONS','CASH_FLOW'}: return None  # conclusions are not a plain overview
            if selected:
                bridges.append('I have filtered the recorded transactions for you.' if english else '已按你的要求筛好了，可以查看对应的消费记录。')
            else:
                bridges.append('The spending overview includes your recent transactions.' if english else '可以先看支出概览，也可以展开最近的消费记录。')
            fallback=fallback_month_labels(analysis,english=english)
            if fallback:
                requested,displayed=fallback
                movement_only=analysis['period_fallback']['reason']=='REQUESTED_PERIOD_ONLY_MOVEMENTS'
                notices.append((f'{requested} currently contains funds movements, not complete consumption records; the spending shown is for {displayed}.' if english
                    else f'{requested}目前有资金流水，消费记录尚未完整更新；这里显示{displayed}已有消费账单。') if movement_only
                    else (f'{requested} is not available yet; the records shown are for {displayed}.' if english else f'{requested}账单还未更新，这里显示{displayed}已有记录。'))
        elif kind=='CARBON_VIEW':
            from .customer_response import read_query_coverage
            coverage=read_query_coverage(kind,result)
            if coverage['status']=='UNAVAILABLE_SCOPE': return None
            selected=set(coverage.get('selected_dimensions',()))
            activities_queried=coverage['status']=='UNFILTERED' or 'ACTIVITIES' in selected
            rewards_queried=coverage['status']=='UNFILTERED' or 'REWARDS' in selected
            wallet=result.get('wallet');rewards=result.get('rewards')
            if not isinstance(wallet,dict) or type(wallet.get('points')) is not int or not 0<=wallet['points']<=2**53-1 or not isinstance(rewards,list): return None
            eligible=[]
            owned.append(semantic_field(node,'wallet_points',currency='POINTS'))
            for index,row in enumerate(rewards):
                if not isinstance(row,dict) or type(row.get('cost_points')) is not int or not 0<row['cost_points']<=2**53-1 \
                    or type(row.get('stock')) is not int or not 0<=row['stock']<=2**53-1 or type(row.get('can_redeem')) is not bool: return None
                record=row.get('reward_id') or row.get('reward_name') or index
                owned.extend(semantic_field(node,field,record=record,currency='POINTS') for field in ('reward_name','cost_points','eligibility','gap'))
                if row['can_redeem'] and row['stock']>0 and wallet['points']>=row['cost_points']: eligible.append(row)
            message=trace.get('customer_user_message',trace.get('message',''))
            wants_records=activities_queried and (not rewards_queried or bool(re.search(r'(?:(?:可|能|可以).{0,5}(?:领|申领|领取).{0,12}(?:记录|行为|活动)|(?:记录|行为|活动).{0,12}(?:领|积分)|(?:领|申领|领取|赚取|获得).{0,10}(?:积分|记录|行为)|\b(?:claimable|claim|earn)\b.{0,40}\b(?:records?|activities|activity|points?)\b)',message,re.I)))
            wants_rewards=rewards_queried and (not activities_queried or bool(re.search(r'兑|权益|\b(?:redeem|rewards?|benefits?)\b',message,re.I)))
            if wants_records:
                events=result.get('events')
                if not isinstance(events,list) or any(not isinstance(row,dict) for row in events): return None
                focus='CARBON_CLAIMABLE_RECORDS'
                known=all(type(row.get('claimable')) is bool and type(row.get('claimed')) is bool and type(row.get('method_supported')) is bool \
                    and type(row.get('estimated_points')) is int and 0<=row['estimated_points']<=2**53-1 for row in events)
                has_claimable=any(row.get('claimable') is True and row.get('claimed') is False and row.get('method_supported') is True \
                    and type(row.get('estimated_points')) is int and 0<row['estimated_points']<=2**53-1 for row in events)
                bridges.append(('The claimable activities are in the result cards; rewards and other records are in the details.' if has_claimable else 'No recorded activity currently meets the claim conditions.' if known else 'Some activity conditions still need verification; no missing conditions are assumed.') if english else
                    ('先看结果卡中能领取积分的行为记录，权益和其他记录可以展开查看。' if has_claimable else '当前没有满足领取条件的行为记录。' if known else '部分行为领取条件还需要核对，暂不认定可领取。'))
                if has_claimable and not rewards_queried:
                    bridges[-1]='The selected activity and its recorded claim conditions are in the result cards.' if english else '这项行为记录和领取条件列在结果卡中。'
                for index,row in enumerate(events):
                    record=row.get('behavior_id') or row.get('behavior_name') or index
                    owned.extend(semantic_field(node,field,record=record,currency='POINTS') for field in ('behavior_name','occurred_at','estimated_points','claimability'))
            elif wants_rewards:
                focus='CARBON_ELIGIBLE_REWARDS'
                nearest=sorted([row for row in rewards if row['stock']>0 and row['cost_points']>wallet['points'] and isinstance(row.get('reward_name'),str)],key=lambda row:row['cost_points']-wallet['points'])
                nearest_name=customer_display_name(nearest[0]['reward_name']) if nearest else ''
                all_eligible=bool(rewards) and len(eligible)==len(rewards)
                bridges.append(('All listed rewards currently meet the recorded redemption conditions.' if all_eligible else 'Some rewards meet the current conditions; others do not. Each item is labelled in the result cards.' if eligible else f'No listed reward can be redeemed yet. {nearest_name} has the nearest points threshold; the gap is shown in the result cards.' if nearest_name else 'No listed reward can be redeemed yet; the conditions are shown in the result cards.') if english else
                    ('这些权益目前都满足兑换条件，具体门槛也列好了。' if all_eligible else '有些权益已经满足条件，还有些尚未满足，结果卡中逐项标明。' if eligible else f'目前还不能兑换，{nearest_name}离积分门槛最近，差额列在结果卡中。' if nearest_name else '目前还不能兑换这些权益，具体条件列在结果卡中。'))
            else: bridges.append('Your points and reward conditions are in the result cards.' if english else '你的积分和权益条件在结果卡中。')
            if focus in {'CARBON_CLAIMABLE_RECORDS','CARBON_ELIGIBLE_REWARDS'} and isinstance(node.get('node_id'),str): node_focus[node['node_id']]=focus
        elif kind=='CARD_STATUS':
            message=trace.get('customer_user_message',trace.get('message',''))
            cards=result.get('cards')
            if not isinstance(cards,list) or any(not isinstance(row,dict) for row in cards): return None
            asks_limit=bool(re.search(r'限额|额度|\blimits?\b',message,re.I) and not re.search(r'不(?:查|看|问).{0,4}(?:限额|额度)|do\s+not\s+(?:show|check).*limit',message,re.I))
            if not asks_limit:
                from .customer_response import _card_balances_complete
                if cards and (not _card_balances_complete(cards) or any(row.get('status') not in ('ACTIVE','LOCKED','LOST','CLOSED') for row in cards)): return None
                bridges.append('You can open a card to check its balance and status.' if cards and english else '需要了解哪张卡的余额和状态，可以展开查看。' if cards else 'No card record was returned.' if english else '这次没有查到银行卡记录。')
                currencies_bridge=single_card_currency_bridge(cards,language)
                if currencies_bridge: bridges[-1]=currencies_bridge
                continue
            focus='CARD_BALANCE_LIMIT' if re.search(r'余额|\bbalances?\b',message,re.I) and not re.search(r'不(?:查|看|问).{0,4}余额|do\s+not\s+(?:show|check).*balance',message,re.I) else 'CARD_LIMIT'
            complete=all(type(row.get('limit_minor')) is int and 0<=row['limit_minor']<=2**53-1 and row.get('limit_currency','CNY') in ('CNY','USD') for row in cards)
            bridges.append(('Here are the recorded per-payment limits. Other card details are available in the result cards.' if complete else 'Some limit details need verification; they are shown explicitly in the result cards.') if english else
                ('这是查到的单笔限额，其他卡片信息可以展开查看。' if complete else '部分限额资料还需要核对，不能据此认定可支付额度。'))
            if focus=='CARD_BALANCE_LIMIT':
                bridges[-1]='Your balance and per-payment limit are both shown in the result cards; missing details are marked.' if english else '余额和单笔限额都列在结果卡中，未返回的资料会单独标明。'
            if re.search(r'每天|每日|日限额|daily|per.day',message,re.I): notices.append('A daily limit was not returned; these are per-payment limits.' if english else '日限额尚未返回，查到的是单笔限额，不能当作每天的额度。')
            if re.search(r'美元|USD|dollar',message,re.I) and any(row.get('limit_currency','CNY')!='USD' for row in cards):
                notices.append('A USD limit was not returned; the currency of each recorded limit is shown explicitly.' if english else '美元限额尚未返回，结果卡中按实际返回的币种展示，不能当作美元额度。')
            if isinstance(node.get('node_id'),str): node_focus[node['node_id']]=focus
        elif kind=='SUBSCRIPTION_LIST':
            subscriptions=result.get('subscriptions')
            if not isinstance(subscriptions,list): return None
            for index,row in enumerate(subscriptions):
                if not isinstance(row,dict) or row.get('status') not in ('ACTIVE','CANCELLED') or type(row.get('amount_minor')) is not int \
                    or not 0<=row['amount_minor']<=2**53-1 or row.get('currency','CNY') not in ('CNY','USD'): return None
                record=row.get('subscription_id') or row.get('merchant') or index
                owned.extend(semantic_field(node,field,record=record,currency=row.get('currency','CNY')) for field in ('merchant','status','amount_minor','auto_renew','entitled'))
                # Each compact subscription card owns its explicit access
                # expiry (including absence); don't duplicate every date as a
                # global paragraph, or infer access from a scheduled charge.
                owned.append(semantic_field(node,'entitlement_end_at',record=record,currency=row.get('currency','CNY')))
                if row['status']=='ACTIVE' and row.get('source_type')=='EXTERNAL_UNCONNECTED' and row.get('can_cancel_here') is False:
                    label=customer_display_name(row.get('merchant'))
                    notices.append(f'{label} must be managed with the provider; cancellation is not connected here.' if english else f'{label}需要到服务商关闭，这里未接入取消办理。')
                elif row['status']=='ACTIVE' and not (row.get('source_type') in ('BANK_MANDATE','MERCHANT_SIM') and row.get('can_cancel_here') is True): return None
            review=result.get('renewal_review')
            if review is not None:
                if not isinstance(review,dict) or review.get('version')!='visitor-renewal-review-v1' or review.get('read_only') is not True or review.get('bank_state_changed') is not False \
                    or not isinstance(review.get('departure_date'),str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',review['departure_date']) \
                    or not isinstance(review.get('recorded_after_departure'),list) or not isinstance(review.get('active_dates_need_verification'),list) \
                    or not isinstance(review.get('notice_en' if english else 'notice'),str): return None
                from datetime import date
                try: date.fromisoformat(review['departure_date'])
                except ValueError: return None
                after_count=len(review['recorded_after_departure']); unknown_count=len(review['active_dates_need_verification'])
                notices.append((f"For departure on {review['departure_date']}, {after_count} recorded dates fall later and {unknown_count} active dates need verification. " if english else
                    f"针对{review['departure_date']}离境，有{after_count}项记录日期在其后，{unknown_count}项活跃服务日期需核实。")+redact_credentials(review['notice_en' if english else 'notice']))
            bridges.append('You can open a service to check its renewal details.' if english else '想了解哪项服务的续费情况，可以展开查看。')
            # A focused service answer must state its important renewal fact,
            # not hide it behind a generic navigation sentence. This depends
            # only on the verified selected record, not phrase matching.
            if len(subscriptions)==1:
                row=subscriptions[0]
                from .subscription_answer_facts import renewal_answer, access_answer
                bridges[-1]=renewal_answer(row,language)
                if row.get('entitled') is True:
                    bridges[-1]+=' '+access_answer(row,language)
        elif kind=='ASSET_POSITIONS_READ':
            positions=result.get('positions')
            if not isinstance(positions,list) or any(not isinstance(row,dict) or not isinstance(row.get('total_quantity'),str) for row in positions): return None
            for row in positions:
                owned.extend(semantic_field(node,field,record=row.get('position_id'),currency=row.get('currency'))
                    for field in ('product_name','total_quantity','quantity_unit','market_value_minor'))
            bridges.append(('Here are your current holdings. Open a position for price and cost details.' if positions else 'No matching holdings were found.') if english else
                ('已查到你的当前持仓，展开后可以查看价格和成本明细。' if positions else '目前没有符合条件的持仓。'))
        elif kind in {'MEMBERSHIP_VIEW','BENEFITS_VIEW','PREFERENCE_VIEW'}:
            bridges.append({'MEMBERSHIP_VIEW':('Your customer and card tiers are shown separately.','客户等级和每张卡的等级分别列在下面。'),
                'BENEFITS_VIEW':('I have checked the offer conditions against your current profile.','已根据当前客户资料核对活动条件。'),
                'PREFERENCE_VIEW':('Here are the preferences you have saved.','这是目前保存的个人偏好，你可以随时修改或撤回。')}[kind][0 if english else 1])
        elif kind=='WEALTH_COMPARE':
            from .customer_response import read_query_coverage
            coverage=read_query_coverage(kind,result)
            if coverage['status']=='UNAVAILABLE_SCOPE': return None
            products=result.get('products')
            if not isinstance(products,list): return None
            for index,row in enumerate(products):
                if not isinstance(row,dict) or row.get('status') not in {'AVAILABLE','UNAVAILABLE'} or type(row.get('suitable')) is not bool \
                    or type(row.get('risk_level')) is not int or not 1<=row['risk_level']<=5: return None
                terms=row.get('terms')
                if not isinstance(terms,dict) or any(not isinstance(terms.get(key),str) or not terms[key].strip() for key in ('term','redemption_timing')): return None
                record=row.get('product_id') or row.get('name') or index
                owned.extend(semantic_field(node,field,record=record,currency=row.get('currency')) for field in ('name','risk_level','term','redemption_timing','suitability'))
            bridges.append('Compare the recorded access, term and risk in the result cards; suitable available records appear first.' if english else '先比较取用时间、期限和风险，当前适配且可用的产品排在前面。')
            if coverage['status']=='FILTERED':
                bridges[-1]='The selected product\'s recorded access, term, risk and suitability are in the result cards.' if english else '这款产品的取用时间、期限、风险和适配情况列在结果卡中。'
            from .wealth_risk_state import risk_status_summary
            risk_status = risk_status_summary(result.get('risk_profile'),language)
            if risk_status:
                # Mandatory narration, not an optional product fact or a
                # public-policy guess. Product cards keep their own contents.
                notices.append(risk_status)
        elif kind=='FAMILY_VIEW':
            from .customer_response import read_query_coverage
            coverage=read_query_coverage(kind,result)
            if coverage['status']=='UNAVAILABLE_SCOPE': return None
            from .customer_query_focus import FAMILY_FOCUS
            selected_focus=trace.get('customer_query_focus')
            if selected_focus not in FAMILY_FOCUS:
                selected_focus='FAMILY_AUTHORIZED_BALANCES' if (result.get('current_actor') or {}).get('role')=='FAMILY_HELPER' else 'FAMILY_DELEGATIONS'
            focus=selected_focus
            if isinstance(node.get('node_id'),str): node_focus[node['node_id']]=selected_focus
            from .family_balance_access_display import valid_negative_balance_authorization
            if valid_negative_balance_authorization(result,coverage):
                bridges.append('There is no current balance-viewing authorization. The account owner needs to authorize access again.'
                    if english else '当前还不能查看余额，需要账户本人重新授权。')
            else:
                bridges.append(('The authorized balances are shown below.' if english else '已为你查好当前获授权查看的余额。')
                    if selected_focus=='FAMILY_AUTHORIZED_BALANCES' else ('Your family permissions and their expiry dates are shown below.' if english else '家人之间的授权范围和有效期列在下面。'))
        elif kind=='FRAUD_ANALYZE' and len(nodes)==1:
            risk=result.get('risk')
            if not isinstance(risk,dict) or (risk.get('risk_level'),risk.get('decision')) not in {('HIGH','BLOCK'),('LOW','ALLOW'),('UNKNOWN','HOLD'),('UNKNOWN','HOLD_FOR_REVIEW')}: return None
            bridges.append(('High-risk signs were detected. Follow the safety guidance in the result cards.' if risk['decision']=='BLOCK' else 'No high-risk rule was triggered; this is not a safety guarantee.' if risk['decision']=='ALLOW' else 'The risk is still unknown; check the request before proceeding.') if english else
                ('发现高风险，请先按结果卡中的安全建议处理。' if risk['decision']=='BLOCK' else '当前没有命中高风险规则，但这不代表安全保证。' if risk['decision']=='ALLOW' else '风险还没有核清，请先核实对方的要求。'))
            from .customer_response import deferred_security_write_notice
            later=deferred_security_write_notice(trace,english=english)
            if later: notices.append(later)
        else:
            # Do not erase other domains' suitability, expiry or anti-fraud
            # warnings to compact a mixed query. They keep their existing path.
            return None
    if set(kinds)=={'CARD_STATUS','SUBSCRIPTION_LIST'} and focus is None:
        bridges=['You can check your cards and renewal records. Tell me which item you want to focus on.' if english else '银行卡和续费记录都已查到，想重点看哪一项可以继续告诉我。']
    elif len(nodes)>1:
        bridges=['The results are grouped in the result cards in the order of your request.' if english else '按你的需求顺序，结果分别放在结果卡中。']
    # Multiple product READs can carry the same mandatory assessment notice.
    # Preserve each distinct verified notice once, never repeat it per card.
    notices=list(dict.fromkeys(notices))
    return {'version':LAYOUT_VERSION,'intro':' '.join(bridges) if english else ''.join(bridges),
        'detail':' '.join(notices) if english else ''.join(notices),'card_fields':[field for field in owned if field],'actions':kinds,'focus':focus,'focus_by_node':node_focus}


def apply_query_layout(response,trace):
    if response.get('state_kind')!='QUERY_RESULT' or response.get('read_only') is not True: return response
    layout=query_layout(trace,response.get('reply_language','zh-CN'))
    from .reviewed_bill_reply import comparison_reply
    material=comparison_reply(trace,response.get('reply_language','zh-CN'))
    if material:
        opening,*remaining=material['text'].split('\n')
        detail='\n'.join(remaining) or None
        return {**response,'intro':opening,'detail':detail,'card_intro':opening,'card_detail':detail,
            'query_layout_version':LAYOUT_VERSION,'query_focus':None,'query_focus_by_node':{}}
    if not layout: return response
    return {**response,'card_intro':layout['intro'],'card_detail':layout['detail'] or None,'query_layout_version':LAYOUT_VERSION,'query_focus':layout['focus'],'query_focus_by_node':layout['focus_by_node']}
