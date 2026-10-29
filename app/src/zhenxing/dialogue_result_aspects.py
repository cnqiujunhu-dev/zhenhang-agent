"""Question-shaped, source-bound facts for one or more queried objects.

Only the registered catalog's exact facts enter this projection. This module
does not read accounts, resolve identities, infer shared funds from balances,
or accept model-authored financial quantities.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from .planning import canonical_hash

UNIT = 'QUERIED_OBJECT_ASPECT'
AUTHORITY = 'VERIFIED_READ_FACTS_NOT_WRITE_AUTHORIZATION'
ASPECT_FIELDS = {
    'STATUS': ('status','auto_renew','entitled','entitlement_end_at','next_due_at','tier_name','card_tier_name','risk_level','valid','expires_at',
        'effective_request_status','paymentable','payment_task_state','receipt_verified','fulfilment_status','external_fulfilment_verified','adult_eligibility_verified','cards'),
    'PRICE': ('unit_price_minor','reference_price','price_observed_at','price_effective_at','price_source','currency'),
    'HOLDINGS': ('holding_units','total_quantity','available_quantity','frozen_quantity','quantity_unit','availability_status'),
    'QUANTITY': ('holding_units','total_quantity','available_quantity','frozen_quantity','quantity_unit','availability_status'),
    'VALUATION': ('market_value_minor','reference_price','price_observed_at','price_source','currency'),
    'PROFIT_LOSS': ('remaining_cost_minor','average_cost','unrealized_pnl_minor','return_pct','currency'),
    'COST_BASIS': ('remaining_cost_minor',),
    'TERMS': ('terms','risk_level','suitable','suitability_reason_codes','periods','schedule','minimum_payment_explanation','service_content'),
    'POINTS': ('points','cost_points','estimated_points','settlement'),
    'SETTLEMENT': ('settlement',),
    'CHARGE': ('amount_minor','billing_months','charge_basis','currency','principal_minor','remaining_principal_minor','fee_minor','remaining_fee_minor','spending_minor',
        'total_minor','received_minor','budget_minor','reserved_minor','posted_minor','refunded_minor','net_cash_debit_minor','paid_amount_minor',
        'refund_accounting','totals_by_currency','recorded_totals_known','empty_record_set','empty_does_not_prove_zero'),
    'ELIGIBILITY': ('can_redeem','stock','eligible','eligibility','status','conditions','starts_at','ends_at','valid_from','valid_until'),
    'BALANCE': ('balances_by_currency','available_by_currency','reserved_cash_by_currency','supported_currencies','base_currency','shared_credit_limit','credit_is_cash','base_credit_limit_minor',
        'used_base_exposure_minor','authorization_base_exposure_minor','available_base_credit_minor','outstanding_minor','current_due_minor',
        'minimum_due_minor','principal_minor','fees_minor','interest_minor','credit_balance_minor','unbilled_principal_minor'),
    'TIME_SCOPE': ('period_start','period_end','coverage','month','day','calendar','timezone','confirmed_solar_date',
        'billing_scope','record_count','recorded_totals_known','empty_record_set','empty_does_not_prove_zero'),
    'BENEFITS': ('activity_name','name','eligible','eligibility','status','conditions','starts_at','ends_at','valid_from','valid_until','service_content'),
    'PREFERENCES': ('preference_kind','preference_value','kind','value','explicit','suggested','confirmed','active_preferences','preferences','retracted_kinds'),
}
LABELS = {
    'status':'状态','auto_renew':'自动续费','entitled':'付费权益有效','entitlement_end_at':'权益到期时间',
    'next_due_at':'记录下次扣款日','unit_price_minor':'登记每份价格','reference_price':'参考单价',
    'amount_minor':'金额','billing_months':'每期月数','spending_minor':'支出',
    'price_observed_at':'价格记录时间','price_effective_at':'价格生效时间','price_source':'价格来源',
    'holding_units':'持有份额','total_quantity':'总数量','available_quantity':'可用数量','frozen_quantity':'冻结数量',
    'availability_status':'数量可用性口径','market_value_minor':'参考市值','remaining_cost_minor':'剩余成本',
    'average_cost':'平均成本','unrealized_pnl_minor':'浮动盈亏','return_pct':'收益率',
    'risk_level':'风险等级','suitable':'适合当前风险档案','points':'可用积分','cost_points':'兑换所需积分',
    'estimated_points':'估算积分','can_redeem':'当前可以兑换','stock':'库存','eligible':'符合资格',
    'activity_name':'活动','conditions':'资格条件','valid_from':'开始时间','valid_until':'结束时间',
    'period_start':'统计起点','period_end':'统计终点','preference_kind':'偏好类型','preference_value':'本人偏好',
    'kind':'偏好类型','value':'本人偏好','explicit':'本人明确提供','suggested':'未确认建议','confirmed':'本人已确认',
    'customer_tier':'客户等级','card_tier':'卡等级','risk_profile':'风险档案',
    'minimum_purchase_minor':'按登记条款计算的起始投入',
    'tier_name':'客户等级','card_tier_name':'卡等级','valid':'风险测评有效','expires_at':'风险测评到期时间',
    'starts_at':'活动开始时间','ends_at':'活动结束时间','eligibility':'活动资格','name':'名称',
    'base_credit_limit_minor':'共享授信额度','used_base_exposure_minor':'已用授信（折合人民币）',
    'authorization_base_exposure_minor':'预授权占用（折合人民币）','available_base_credit_minor':'可用授信（折合人民币）',
    'outstanding_minor':'全部未还欠款','current_due_minor':'本期应还','minimum_due_minor':'最低应还','principal_minor':'本金',
    'fees_minor':'未还费用','interest_minor':'未还利息','credit_balance_minor':'溢缴款','unbilled_principal_minor':'未出账本金',
    'remaining_principal_minor':'剩余本金','fee_minor':'登记总费用','remaining_fee_minor':'剩余费用','periods':'期数',
    'shared_credit_limit':'两币种共用授信池','credit_is_cash':'信用额度属于现金','minimum_payment_explanation':'最低还款口径',
    'total_minor':'本次收款总额','received_minor':'已核验实收金额','budget_minor':'本次预算上限','reserved_minor':'中央现金预留',
    'posted_minor':'实际支付毛额','refunded_minor':'已核验原路退款','net_cash_debit_minor':'净现金支出','paid_amount_minor':'订单实际支付金额',
    'effective_request_status':'请求有效状态','paymentable':'当前可准备付款','payment_task_state':'付款任务状态','receipt_verified':'实际付款回执已核验',
    'fulfilment_status':'履约状态','external_fulfilment_verified':'外部履约已核验','adult_eligibility_verified':'成年申请条件已核验',
    'month':'月','day':'日','calendar':'历法','timezone':'时区','confirmed_solar_date':'本人确认的公历日期',
}
STATES = {'ACTIVE':'启用','CANCELLED':'已取消续费','AVAILABLE':'可用','LOCKED':'已临时锁定',
    'LOST':'已挂失','FROZEN':'已冻结','EXPIRED':'已过期','ELIGIBLE':'符合资格','INELIGIBLE':'不符合资格',
    'UNREGISTERED':'未登记','NOT_STARTED':'尚未开始','NOT_TRACKED':'未登记可用及冻结数量','RECORDED':'已登记',
    'CONSUMPTION':'消费','TRAVEL':'出行','EMPTY':'本次范围没有持仓记录',
    'SIMULATION_PRODUCT_RECORD_NO_MARKET_TIMESTAMP':'登记参考价格（未提供行情时间）',
    'PENDING_REVIEW':'待审核','ISSUED':'已发卡待激活','REPLACED':'旧卡已换卡','SETTLED':'已结清','REFUNDED':'已退款',
    'PAYMENT_PENDING':'等待付款确认','PAID':'已付款','COMPENSATING':'退款处理中','REFUND_PENDING':'退款处理中',
    'COMPENSATED':'已完成补偿退款','WAITING_TRIGGER':'等待触发','AUTHORIZED':'已授权','CARD':'信用卡申请','LIMIT':'授信调额申请',
    'SYNTHETIC_SNAPSHOT_NOT_MARKET_DATA':'登记价格快照（非实时行情）','SNAPSHOT_UNAVAILABLE':'未提供价格快照'}
MONEY_FIELDS = {'unit_price_minor','market_value_minor','remaining_cost_minor','unrealized_pnl_minor','fee_minor','amount_minor','minimum_purchase_minor','spending_minor',
    'base_credit_limit_minor','used_base_exposure_minor','authorization_base_exposure_minor','available_base_credit_minor',
    'outstanding_minor','current_due_minor','minimum_due_minor','principal_minor','fees_minor','interest_minor','credit_balance_minor','unbilled_principal_minor',
    'remaining_principal_minor','remaining_fee_minor','total_minor','received_minor','budget_minor','reserved_minor','posted_minor','refunded_minor','net_cash_debit_minor','paid_amount_minor'}
BASE_CNY_FIELDS = {'base_credit_limit_minor','used_base_exposure_minor','authorization_base_exposure_minor','available_base_credit_minor'}
DOMAIN_LABELS = {'SUBSCRIPTIONS':{'amount_minor':'每期费用'},'AA':{'amount_minor':'本人本次应付AA份额'},
    'CREDIT':{'amount_minor':'本次信用业务金额','kind':'信用申请类型'},'LIFE_PLANS':{'amount_minor':'本次计划金额'},
    'MERCHANTS':{'amount_minor':'商品金额'},'MERCHANT_ORDERS':{'amount_minor':'本次订单金额'}}


def available_aspects(fields, domain=None):
    keys = set(fields)
    result = ['SUMMARY']
    result += [aspect for aspect, names in ASPECT_FIELDS.items() if aspect != 'COST_BASIS' and keys.intersection(names)]
    if domain == 'ASSETS' and 'remaining_cost_minor' in keys:result.append('COST_BASIS')
    if domain == 'ASSETS' and keys.intersection({'price_observed_at','price_effective_at'}):result.append('TIME_SCOPE')
    if domain == 'CARDS' and 'balance_scope_refs' in keys:result.append('ACCOUNT_RELATIONSHIP')
    if domain == 'CREDIT' and 'credit_pool_contract' in keys:result.append('ACCOUNT_RELATIONSHIP')
    return list(dict.fromkeys(result))


def selected_query_fact_catalog(records):
    from .dialogue_reference_facts import _registered_content, is_selected_source_id, SOURCE_ID
    selected = [(row, _registered_content(row)) for row in records if is_selected_source_id(row.get('source_id'))]
    selected = [(row,content) for row,content in selected if content is not None]
    from .wealth_suitability_facts import ASPECT as WEALTH_ASPECT, checked_product_fact
    # A relation is per object, never inferred from another object's profile.
    # Mixed profile/owner contexts cannot be collapsed into a common current
    # risk assertion, even before the caller's independent live source recheck.
    wealth_contexts = set()
    for _, content in selected:
        if content['domain'] != 'WEALTH':continue
        for item in content['facts']:
            if item['field'] != 'wealth_suitability':continue
            try:
                snapshot=checked_product_fact(item['value'])['snapshot']
                wealth_contexts.add((snapshot['owner_hash'],snapshot['profile_source_hash']))
            except (ValueError,TypeError,KeyError):
                wealth_contexts.add(('INVALID','INVALID'))
    from .bill_category_consultation import selected_category_facts
    facts, category_sources = selected_category_facts(selected)
    for row, content in selected:
        if row['source_id'] in category_sources:
            continue
        # The immutable receipt is bound by semantic_selected_object_sources_current.
        receipt = (row.get('source_binding') or {}).get('question_aspects', [])
        aspects = receipt or (['SUMMARY'] if len(selected)>1 or content['domain'] in {'ASSETS','MEMBERSHIP','BENEFITS','PREFERENCES','CREDIT','AA','OCCASIONS','LIFE_PLANS','MERCHANTS','MERCHANT_ORDERS'} else [])
        if 'SETTLEMENT' in (row.get('source_binding') or {}).get('rule_aspects',[]) and 'SETTLEMENT' not in aspects:
            aspects=[*aspects,'SETTLEMENT']
        fields = {fact['field']: deepcopy(fact['value']) for fact in content['facts']}
        from .bill_query_context import VERSION as BILL_QUERY_VERSION
        if content['domain'] == 'BILLS' and fields.get('query_context_version') == BILL_QUERY_VERSION \
                and fields.get('refund_accounting') == 'REFUNDS_REPORTED_SEPARATELY_NOT_SUBTRACTED_FROM_CONSUMPTION' \
                and any(aspect in {'SUMMARY', 'CHARGE', 'TIME_SCOPE', 'SETTLEMENT'} for aspect in aspects):
            # One complete, typed query lens answers an empty-result follow-up.
            # Publishing raw boolean flags or ISO boundaries is not a customer
            # explanation of whether refunds were included.
            identifier = row['source_id'] + '.bill_query_scope'
            facts[identifier] = {'fact_id':identifier, 'source_id':row['source_id'], 'field':'BILL_QUERY_SCOPE',
                'unit':UNIT, 'currency':'NONE', 'label':'刚才账单的退款与统计口径', 'label_en':'Bill refund accounting',
                'required':True, 'value':{'authority':AUTHORITY, 'domain':'BILLS', 'aspect':'BILL_QUERY_SCOPE',
                    'fields':{**deepcopy(fields), 'requested_aspects':list(aspects)}, 'result_ref':content['result_ref'],
                    'record_version':content.get('record_version'), 'bank_state_changed':False}}
            continue
        lens = ((row.get('source_binding') or {}).get('query_reference') or {}).get('data_kind')
        if content['domain'] == 'SUBSCRIPTIONS' and lens != 'PUBLIC_TERMS' \
                and set(aspects) & {'SUMMARY', 'STATUS', 'PRICE', 'CHARGE', 'TERMS', 'TIME_SCOPE'}:
            # Public rules stay separately cited; do not turn empty bank
            # TERMS/SETTLEMENT facets into repeated "not provided" paragraphs.
            names = ('amount_minor', 'billing_months', 'currency', 'source_type', 'status',
                'auto_renew', 'entitled', 'entitlement_end_at')
            values = {name: deepcopy(fields[name]) for name in names if name in fields}
            identifier = row['source_id'] + '.subscription_authorization'
            facts[identifier] = {'fact_id': identifier, 'source_id': row['source_id'],
                'field': 'SUBSCRIPTION_AUTHORIZATION', 'unit': UNIT, 'currency': 'NONE',
                'label': content['label'] + ' · subscription', 'required': True,
                'value': {'authority': AUTHORITY, 'domain': 'SUBSCRIPTIONS',
                    'aspect': 'SUBSCRIPTION_AUTHORIZATION', 'label': content['label'], 'fields': values,
                    'requested_aspects': list(aspects), 'result_ref': content['result_ref'],
                    'record_version': content.get('record_version'), 'bank_state_changed': False}}
            continue
        suitability=None
        if content['domain']=='WEALTH' and 'wealth_suitability' in fields and len(wealth_contexts)==1 \
                and ('INVALID','INVALID') not in wealth_contexts:
            try:
                candidate=checked_product_fact(fields['wealth_suitability'])
                entity=next(item for item in row['source_binding']['catalog']['entities']
                    if item['entity_ref']==row['source_binding']['resolution']['entity_ref'])
                product=candidate['product']
                if product['product_id']!=entity['record_id'] or product['version']!=content['record_version'] \
                        or any(product[key]!=fields.get(key) for key in ('risk_level','unit_price_minor','status','suitable','suitability_reason_codes')):
                    raise ValueError('INVALID_WEALTH_SUITABILITY_FACT')
                suitability=candidate
            except (ValueError,TypeError,KeyError,StopIteration):pass
        if suitability is not None and not aspects:aspects=['SUMMARY']
        risk_requested=bool(set(aspects)&{'SUMMARY','STATUS','ELIGIBILITY','TERMS','RULES'} or
            set((row.get('source_binding') or {}).get('rule_aspects',[]))&{'RISK','ELIGIBILITY'})
        if suitability is not None and risk_requested:
            identifier=row['source_id']+'.wealth_suitability'
            facts[identifier]={'fact_id':identifier,'source_id':row['source_id'],'field':WEALTH_ASPECT,
                'unit':UNIT,'currency':'NONE','label':content['label']+' · '+WEALTH_ASPECT,
                'label_en':content['label']+' · '+WEALTH_ASPECT,'required':True,
                'value':{'authority':AUTHORITY,'label':content['label'],'domain':'WEALTH','aspect':WEALTH_ASPECT,
                    'fields':{'suitability':suitability},'result_ref':content['result_ref'],
                    'record_version':content.get('record_version'),'bank_state_changed':False}}
        for aspect in aspects:
            if aspect in {'OPERATION_SCOPE','ACCOUNT_RELATIONSHIP','RULES'}:continue
            if aspect == 'SUMMARY':
                names = tuple(dict.fromkeys(name for group in ASPECT_FIELDS.values() for name in group))
                names += ('amount_minor','customer_tier','card_tier','risk_profile','tier_name','card_tier_name','valid','expires_at')
                names += ('position_count','preferences','active_preferences','retracted_kinds','inferred_suggestions_are_active_preferences')
            else:names = ASPECT_FIELDS.get(aspect, ())
            if content['domain'] == 'ASSETS' and aspect == 'TIME_SCOPE':
                names = ('price_observed_at','price_effective_at','price_source')
            if aspect=='POINTS' and 'SETTLEMENT' in aspects:names=tuple(name for name in names if name!='settlement')
            lens=((row.get('source_binding') or {}).get('query_reference') or {}).get('data_kind')
            if aspect=='PRICE' and lens=='PUBLIC_TERMS' and content['domain']=='SUBSCRIPTIONS':continue
            values = {name: deepcopy(fields[name]) for name in names if name in fields}
            if suitability is not None and risk_requested:
                for name in ('risk_level','suitable','suitability_reason_codes'):values.pop(name,None)
                if aspect in {'STATUS','ELIGIBILITY'} and set(values)<={'status'}:continue
            terms=fields.get('terms')
            if content['domain']=='WEALTH' and aspect in {'PRICE','TERMS'} and isinstance(terms,dict) \
                    and type(terms.get('minimum_units')) is int and terms['minimum_units']>0 \
                    and type(fields.get('unit_price_minor')) is int and 0<fields['unit_price_minor']*terms['minimum_units']<=2**53-1:
                values['minimum_purchase_minor']=fields['unit_price_minor']*terms['minimum_units']
            # Currency and quantity unit interpret values but are not independent answers.
            for name in ('currency','base_currency','quantity_unit'):
                if name in fields:values[name]=deepcopy(fields[name])
            if content['domain']=='ASSETS':
                for name in ('synthetic','price_is_live'):
                    if name in fields:values[name]=fields[name]
            if fields.get('facts_authority')=='INFERRED_SUGGESTION_NOT_USER_PREFERENCE':
                values['unconfirmed_suggestion']=True
            identifier = row['source_id'] + '.aspect_' + aspect.lower()
            facts[identifier] = {'fact_id':identifier,'source_id':row['source_id'],'field':aspect,
                'unit':UNIT,'currency':'NONE','label':content.get('label','查询对象')+' · '+aspect,
                'label_en':content.get('label','Queried object')+' · '+aspect,'required':True,
                'value':{'authority':AUTHORITY,'label':content.get('label','查询对象'),'domain':content['domain'],
                    'aspect':aspect,'fields':values,'result_ref':content['result_ref'],
                    'record_version':content.get('record_version'),'bank_state_changed':False}}
    relation_rows = [(row,content) for row,content in selected if content['domain']=='CARDS'
        and 'ACCOUNT_RELATIONSHIP' in (row.get('source_binding') or {}).get('question_aspects', [])]
    if relation_rows:
        sources=[row['source_id'] for row,_ in relation_rows]
        identifier='facts:card-account-relation.'+canonical_hash(sources)[:24]
        cards=[]
        for row,content in relation_rows:
            fields={fact['field']:fact['value'] for fact in content['facts']}
            cards.append({'label':content['label'],'scopes':deepcopy(fields.get('balance_scope_refs',{})),
                'result_ref':content['result_ref']})
        facts[identifier]={'fact_id':identifier,'source_id':sources[0],'source_ids':sources,'field':'ACCOUNT_RELATIONSHIP',
            'unit':UNIT,'currency':'NONE','label':'银行卡账户关系','label_en':'Card account relationship','required':True,
            'value':{'authority':AUTHORITY,'aspect':'ACCOUNT_RELATIONSHIP','cards':cards,'bank_state_changed':False}}
    from .credit_pool_facts import selected_credit_pool_relation
    relation = selected_credit_pool_relation(selected)
    if relation is not None:
        sources = [row['source_id'] for row, _ in selected]
        identifier = 'facts:credit-pool-relation.' + canonical_hash(sources)[:24]
        facts[identifier] = {'fact_id':identifier, 'source_id':sources[0], 'source_ids':sources,
            'field':'ACCOUNT_RELATIONSHIP', 'unit':UNIT, 'currency':'NONE', 'required':True,
            'label':'信用卡共享授信关系', 'label_en':'Credit pool relationship',
            'value':{'authority':AUTHORITY, 'aspect':'CREDIT_POOL_RELATIONSHIP', 'domain':'CREDIT',
                'fields':relation, 'bank_state_changed':False}}
    return facts


def selected_public_price_facts(records):
    """The set identifies objects; the current public registry supplies prices."""
    from .dialogue_reference_facts import _registered_content,is_selected_source_id
    from .product_knowledge import monetary_fact_catalog
    public=monetary_fact_catalog([row for row in records if not is_selected_source_id(row.get('source_id'))])
    facts={}
    for row in records:
        content=_registered_content(row)
        if content is None or content['domain']!='SUBSCRIPTIONS':continue
        binding=row.get('source_binding') or {}
        if (binding.get('query_reference') or {}).get('data_kind')!='PUBLIC_TERMS' or 'PRICE' not in binding.get('question_aspects',[]):continue
        matches=[(key,fact) for key,fact in public.items() if fact.get('source_id')=='facts:subscription-products'
            and fact.get('label')==content['label']+'月价']
        if len(matches)==1:
            key,fact=matches[0]
            facts[key]={**fact,'required':True,'source_ids':[fact['source_id'],row['source_id']]}
        else:
            key=row['source_id']+'.public_price_unavailable'
            facts[key]={'fact_id':key,'source_id':row['source_id'],'field':'PRICE','unit':UNIT,'currency':'NONE','required':True,
                'label':content['label']+' · PRICE','value':{'authority':AUTHORITY,'aspect':'PRICE','domain':'SUBSCRIPTIONS',
                    'label':content['label'],'fields':{},'bank_state_changed':False}}
    return facts


def concise_rule_fallback(records,language=None):
    """Registered whole policy paragraphs only; never concatenated RAG chunks."""
    selected=[];seen=set()
    for row in records:
        if row.get('kind')!='PRODUCT_KNOWLEDGE' or not str(row.get('source_id','')).startswith('kb:') \
                or row.get('source_id')=='kb:scope.boundary-v1':continue
        text=row.get('content_en') if language=='en' else row.get('content')
        if not isinstance(text,str) or not text.strip() or len(text)>1800 or text in seen:continue
        seen.add(text);selected.append(text.strip())
    if selected:return '\n\n'.join(selected[:2])
    return 'The remaining rules could not be verified in this reply.' if language=='en' else '其余规则这轮还未可靠核实，不能补成确定结论。'


def _scalar(name, value, fields, english=False, domain=None):
    label = name.replace('_',' ') if english else DOMAIN_LABELS.get(domain,{}).get(name,LABELS.get(name))
    if label is None:return None
    if value is None:return f'{label}: not provided' if english else f'{label}未提供'
    if type(value) is bool:text=('yes' if value else 'no') if english else ('是' if value else '否')
    elif name in MONEY_FIELDS and type(value) is int:
        currency=fields.get('base_currency') if name in BASE_CNY_FIELDS else fields.get('currency')
        if currency not in {'CNY','USD'}:return f'{label}: currency not provided' if english else f'{label}的币种未提供，不能补猜'
        text=f'{Decimal(value)/100:.2f} {currency}'
    elif domain == 'ASSETS' and name in {'price_observed_at','price_effective_at'} \
            or domain == 'BILLS' and name in {'period_start','period_end'}:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            moment=datetime.fromisoformat(value) if isinstance(value,str) else None
            if moment is None or moment.tzinfo is None:raise ValueError()
            text=moment.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')+' (Asia/Shanghai)'
        except (TypeError,ValueError):
            text='not provided' if english else '未提供可核实的时间'
    elif domain == 'ASSETS' and name == 'price_source':
        # A backend price-source identifier is provenance, not customer prose.
        return None
    elif isinstance(value,(str,int,Decimal)):
        text=str(value) if english else STATES.get(str(value),str(value))
        if name in {'points','cost_points','estimated_points'}:text += ' points' if english else '积分'
        elif name=='holding_units':text += ' units' if english else '份'
        elif name in {'total_quantity','available_quantity','frozen_quantity'}:
            text += ' '+(({'UNIT':'units','SHARE':'shares','GRAM':'grams'} if english else {'UNIT':'份','SHARE':'股','GRAM':'克'}).get(fields.get('quantity_unit'),str(fields.get('quantity_unit') or '')))
        elif name=='reference_price':
            unit=({'UNIT':'unit','SHARE':'share','GRAM':'gram'} if english else {'UNIT':'份','SHARE':'股','GRAM':'克'}).get(fields.get('quantity_unit'))
            text += ' '+str(fields.get('currency') or '')+('/'+unit if unit else '')
    elif isinstance(value,list):text='、'.join(str(item) for item in value if isinstance(item,(str,int)))
    elif isinstance(value,dict):
        if name=='eligibility':
            eligible=value.get('eligible')
            text=('eligible' if eligible is True else 'ineligible' if eligible is False else 'not established') if english else ('符合' if eligible is True else '不符合' if eligible is False else '资料不足，尚不能确定')
            reasons=value.get('reasons',[])
            details=[]
            for reason in reasons:
                if not isinstance(reason,dict) or not isinstance(reason.get('message'),str):continue
                met=reason.get('met')
                state=('met' if met is True else 'not met' if met is False else 'not established') if english else ('满足' if met is True else '不满足' if met is False else '未能核实')
                labels={'ACTIVITY_ACTIVE':'活动启用状态','ACTIVITY_STARTED':'活动开始时间要求',
                    'ACTIVITY_UNEXPIRED':'活动截止时间要求','CUSTOMER_TIER':'客户等级要求','CARD_TIER_AND_STATE':'卡等级与卡片状态要求'}
                details.append(labels.get(reason.get('code'),reason['message'])+'：'+state)
            prefix=f'{label}: {text}' if english else f'{label}：{text}'
            return prefix+('；资格原因：'+'；'.join(details) if details else '')
        if name=='conditions':
            from .customer_profile_common import CARD_TIERS,CUSTOMER_TIERS
            pieces=[]
            if value.get('customer_tiers'):pieces.append('客户等级需为'+ '、'.join(CUSTOMER_TIERS.get(key,key) for key in value['customer_tiers']))
            if value.get('card_tiers'):pieces.append('卡等级需为'+ '、'.join(CARD_TIERS.get(key,key) for key in value['card_tiers']))
            if value.get('active_card_required') is True:pieces.append('卡片须正常使用')
            return label+'：'+('；'.join(pieces) if pieces else '无额外登记限制')
        # Tier/profile records expose customer-facing names, never internal versions.
        text=str(value.get('label') or value.get('name') or value.get('level') or value.get('status') or '')
        if not text:return None
    else:return None
    return f'{label}: {text}' if english else f'{label}：{text}'


def render_query_fact(fact, language=None):
    if fact.get('unit')!=UNIT:return None
    value=fact.get('value')
    if not isinstance(value,dict) or value.get('authority')!=AUTHORITY or value.get('bank_state_changed') is not False:return None
    english=language=='en'
    if value.get('aspect') in {'PROFILE_TIER_AXES', 'ASSET_VALUATION_SCOPE'}:
        from .profile_asset_consultation import render_relation
        return render_relation(fact, language)
    from .bill_category_consultation import is_category_fact, render_category_fact
    if is_category_fact(fact):
        return render_category_fact(fact, language)
    fields = value.get('fields')
    if value.get('domain') == 'BILLS' and value.get('aspect') == 'BILL_QUERY_SCOPE':
        from .bill_query_context import render_query_summary
        return render_query_summary(fields, language)
    if value.get('domain') == 'SUBSCRIPTIONS' and value.get('aspect') == 'SUBSCRIPTION_AUTHORIZATION' \
            and isinstance(fields, dict):
        from .subscription_answer_facts import authorization_answer
        from .customer_query_layout import customer_display_name
        return authorization_answer({**fields, 'merchant': customer_display_name(value.get('label'))}, language,
            include_charge=bool(set(value.get('requested_aspects', [])) & {'SUMMARY', 'CHARGE', 'PRICE'}))
    if value.get('domain') == 'SUBSCRIPTIONS' and isinstance(fields, dict) \
            and fields.get('subscription_authorization_version') == 'owned-subscription-charge-v1' \
            and fields.get('auto_renew') is False and value.get('aspect') in {'CHARGE', 'PRICE', 'SUMMARY'}:
        from .subscription_answer_facts import authorization_answer
        from .customer_query_layout import customer_display_name
        row = {**fields, 'merchant': customer_display_name(value.get('label'))}
        if not row['merchant']:
            return None
        return authorization_answer(row, language, include_charge=True)
    if value.get('aspect')=='COST_BASIS':
        if value.get('domain')!='ASSETS' or not isinstance(value.get('fields'),dict):return None
        from .customer_query_layout import customer_display_name
        label=customer_display_name(str(value.get('label') or ('This holding' if english else '这份持仓')))
        fields=value['fields'];cost=fields.get('remaining_cost_minor');currency=fields.get('currency')
        if 'remaining_cost_minor' in fields and cost is None:
            return (f'{label}: the remaining cost basis is not recorded. Reference market value cannot replace its cost basis.' if english else
                f'{label}：尚未记录当前持仓的本金成本，不能用参考市值代替本金成本。')
        if type(cost) is not int or not 0<=cost<=2**53-1 or not isinstance(currency,str) or currency not in {'CNY','USD'}:
            return (f'{label}: the cost basis could not be verified; it is not zero and reference market value is not a substitute.' if english else
                f'{label}：本次未能核实本金成本；不能当作零成本，也不能用参考市值代替。')
        return (f'{label}: the remaining recorded cost of the current holding is {Decimal(cost)/100:.2f} {currency}.' if english else
            f'{label}：当前持仓记录的剩余成本为{Decimal(cost)/100:.2f} {currency}。')
    if value.get('aspect')=='WEALTH_SUITABILITY':
        from .wealth_suitability_facts import render_product_fact
        return render_product_fact((value.get('fields') or {}).get('suitability'),
            value.get('label') or ('Queried product' if english else '查询产品'),language)
    if value.get('aspect')=='CREDIT_POOL_RELATIONSHIP':
        fields=value.get('fields')
        expected={'base_currency':'CNY','supported_currencies':['CNY','USD'],'shared_credit_limit':True,
            'debts_separate_by_currency':True,'credit_is_cash':False}
        if fields!=expected:return None
        return ('CNY and USD share the same credit pool, measured in base CNY; they are not separate credit limits. '
            'Debts and amounts due remain separate by currency; credit is not deposit cash.' if english else
            '人民币和美元共用同一个按人民币计价的授信池，不是两份独立额度。欠款和应还金额仍分币种列示；信用额度不是存款现金。')
    if value.get('aspect')=='ACCOUNT_RELATIONSHIP':
        cards=value.get('cards',[])
        if len(cards)<2:return 'Choose at least two cards to compare their accounts.' if english else '需要两张已查到的卡才能核对账户关系。'
        currencies=set.intersection(*(set(card.get('scopes',{})) for card in cards))
        relations=[]
        for currency in sorted(currencies):
            refs=[card['scopes'][currency] for card in cards]
            if not all(isinstance(ref,str) and ref.startswith(currency+':') for ref in refs):continue
            same=len(set(refs))==1
            relations.append((f'These cards share the {currency} balance account; it is not separate money for each card.' if same else f'These cards use different {currency} balance accounts.') if english else
                (f'这些卡共用{currency}账户余额，不能把每张卡显示的余额重复相加。' if same else f'这些卡对应不同的{currency}余额账户。'))
        return '\n'.join(relations) if relations else ('The queried records do not establish a shared account. Equal balances alone are not proof.' if english else '已查记录未提供足够的同账户关系依据；余额相等本身不能证明共用账户。')
    fields=value.get('fields',{})
    lines=[]
    for name,item in fields.items():
        if value.get('domain') == 'ASSETS' and name == 'price_effective_at' and item == fields.get('price_observed_at'):
            continue  # Same authenticated timestamp, one customer-facing line.
        if name == 'service_content' and value.get('domain') == 'MERCHANTS':
            from .merchant_service_content import content_notice
            notice = content_notice({'service_content': item}, language)
            if notice:
                lines.append(notice.rstrip('.。'))
            continue
        if value.get('domain') == 'SUBSCRIPTIONS' and name in {'subscription_authorization_version', 'source_type'}:
            continue
        if name in {'preferences','active_preferences'} and isinstance(item,list):
            labels={'CONSUMPTION':'spending','TRAVEL':'travel'} if english else {'CONSUMPTION':'消费','TRAVEL':'出行'}
            entries=[(labels.get(pref.get('kind'),'personal')+' preference: ' if english else labels.get(pref.get('kind'),'本人')+'偏好：')+str(pref.get('value')) for pref in item
                if isinstance(pref,dict) and isinstance(pref.get('value'),str)]
            lines.append(('Currently saved preferences: '+('; '.join(entries) if entries else 'none')) if english else '当前已保存偏好：'+('；'.join(entries) if entries else '无'))
        elif name=='retracted_kinds' and isinstance(item,list) and item:
            labels={'CONSUMPTION':'spending','TRAVEL':'travel'} if english else {'CONSUMPTION':'消费','TRAVEL':'出行'}
            known=[labels[key] for key in item if key in labels]
            if known:lines.append(('Withdrawn preferences: '+', '.join(known)+'; old values are not used as current preferences.') if english else '已撤回'+ '、'.join(label+'偏好' for label in known)+'，旧值不作为当前偏好使用')
        elif name=='position_count' and type(item) is int:
            lines.append(f'本次查询范围内持仓记录：{item}项')
        elif name=='cards' and value.get('domain')=='CREDIT' and isinstance(item,list):
            known=[row for row in item if isinstance(row,dict) and isinstance(row.get('card_number'),str) and isinstance(row.get('status'),str)]
            lines.append((f'{len(known)} credit cards recorded' if english else f'当前记录信用卡{len(known)}张')+
                (('；'+'；'.join(row['card_number']+' '+(row['status'] if english else STATES.get(row['status'],row['status'])) for row in known)) if known else ''))
        elif name=='credit_is_cash' and item is False:
            lines.append('Credit limits are not deposit cash' if english else '信用额度不是存款现金')
        elif name=='schedule' and value.get('domain')=='CREDIT' and isinstance(item,list):
            currency=fields.get('currency')
            if currency in {'CNY','USD'}:
                for part in item:
                    if not isinstance(part,dict) or not all(type(part.get(key)) is int for key in ('period','principal_minor','fee_minor')):continue
                    lines.append((f"Period {part['period']}: principal {Decimal(part['principal_minor'])/100:.2f} {currency}, fee {Decimal(part['fee_minor'])/100:.2f} {currency}" if english else
                        f"第{part['period']}期本金{Decimal(part['principal_minor'])/100:.2f} {currency}，费用{Decimal(part['fee_minor'])/100:.2f} {currency}")+('，'+str(part['due_at'])[:10] if isinstance(part.get('due_at'),str) else ''))
        elif name=='settlement' and isinstance(item,dict):
            from .carbon_settlement import valid_reward_settlement
            if valid_reward_settlement(item):
                lines.append('登记兑换规则：只扣本活动积分，不扣银行卡资金；实际兑换仍需本人确认' if not english else
                    'Registered redemption uses activity points only, with no bank-card cash debit; actual redemption still requires your confirmation')
        elif name=='terms' and isinstance(item,dict):
            terms=[]
            if value.get('domain') in {'LIFE_PLANS','MERCHANTS','MERCHANT_ORDERS'}:
                labels={'maximum_occurrences':'最多执行次数','recurring':'循环执行','expires_at':'授权截止','failure_policy':'失败处理政策','refund_policy':'退款政策'}
                for key,label in labels.items():
                    if key in item and isinstance(item[key],(str,int,bool)):
                        text = item[key]
                        if key == 'refund_policy' and text == 'BEFORE_FULFILMENT_ONLY':
                            text = 'Refunds may be requested before fulfilment; a request is not a credited refund.' if english else '履约前可以申请退款，申请不等于已经退款到账'
                        terms.append((key if english else label)+'：'+str(text))
                lines.extend(terms);continue
            for key,label in (('minimum_units','起始份额'),('term','期限'),('redemption_timing','赎回到账口径')):
                if key in item:terms.append(label+'：'+str(item[key])+('份' if key=='minimum_units' else ''))
            if type(item.get('fee_minor')) is int:terms.append(f"登记手续费：{Decimal(item['fee_minor'])/100:.2f} CNY")
            if item.get('capital_guaranteed') is False:terms.append('不保本，不保证收益')
            lines.extend(terms)
        elif name in {'balances_by_currency','available_by_currency','reserved_cash_by_currency'} and isinstance(item,dict):
            labels={'balances_by_currency':('book balance','账面余额'),'available_by_currency':('available balance','可用余额'),
                'reserved_cash_by_currency':('reserved cash','预留金额')}
            label=labels[name][0 if english else 1]
            lines.extend(f'{currency} {label}：{Decimal(amount)/100:.2f}'
                for currency,amount in item.items() if currency in {'CNY','USD'} and type(amount) is int)
        else:
            text=_scalar(name,item,fields,english,value.get('domain'))
            if text:lines.append(text)
    label=str(value.get('label') or ('Queried object' if english else '查询对象'))
    if value.get('domain')=='ASSETS':
        from .customer_query_layout import customer_display_name
        label=customer_display_name(label)
    if fields.get('unconfirmed_suggestion') is True:
        label+=(' (unconfirmed suggestion, not a saved preference)' if english else '（未确认建议，尚未保存为本人偏好）')
    if not lines:return f'{label}: this aspect is not provided by the queried records.' if english else f'{label}：已查资料没有提供这方面信息，不能据此推断。'
    text=label+'：'+'；'.join(lines)+'。'
    if value.get('domain')=='ASSETS' and (fields.get('synthetic') is True or fields.get('price_is_live') is False):
        text+=(' These are registered holding snapshots; reference prices are not live quotes or executable prices.' if english else '这是登记持仓快照；参考价不是实时行情或可执行报价。')
    return text


def render_query_facts(facts, identifiers, language=None):
    from .product_knowledge import render_monetary_facts
    from .wealth_suitability_facts import ASPECT as WEALTH_ASPECT, render_product_group
    selected=list(dict.fromkeys(identifiers))
    candidate_keys=[key for key in selected if facts[key].get('field')==WEALTH_ASPECT
        or isinstance(facts[key].get('value'),dict) and facts[key]['value'].get('aspect')==WEALTH_ASPECT]
    candidates=[]
    for key in candidate_keys:
        fact=facts[key];value=fact.get('value')
        if fact.get('unit')!=UNIT or fact.get('field')!=WEALTH_ASPECT or fact.get('required') is not True \
                or not isinstance(fact.get('source_id'),str) or not fact['source_id'] \
                or not isinstance(value,dict) or value.get('authority')!=AUTHORITY or value.get('bank_state_changed') is not False \
                or value.get('domain')!='WEALTH' or value.get('aspect')!=WEALTH_ASPECT \
                or not isinstance(value.get('fields'),dict) or set(value['fields'])!={'suitability'}:
            candidates.append(None)
        else:
            candidates.append({'label':value.get('label'),'result_ref':value.get('result_ref'),
                'record_version':value.get('record_version'),'suitability':value['fields']['suitability']})
    grouped=render_product_group(candidates,language)
    # Several requested aspects can contain the same service-content paragraph
    # or holding price/time. Merge presentation only for the exact same
    # already-selected object; keep cost-basis's dedicated unknown-value rule.
    # All original required fact refs, source checks and values are retained.
    merchant_groups = {}
    for key in selected:
        fact = facts[key]
        value = fact.get('value')
        if fact.get('unit') != UNIT or not isinstance(value, dict) \
                or value.get('domain') not in {'MERCHANTS','ASSETS'} or value.get('aspect') == 'COST_BASIS' \
                or value.get('authority') != AUTHORITY \
                or value.get('bank_state_changed') is not False or not isinstance(value.get('fields'), dict) \
                or not fact.get('source_id') or not value.get('result_ref'):
            continue
        identity = canonical_hash({name: value.get(name) for name in ('result_ref', 'record_version', 'label')}
                                  | {'source_id': fact['source_id'],'domain':value['domain']})
        merchant_groups.setdefault(identity, []).append(key)
    merchant_merged = {}
    for keys in merchant_groups.values():
        if len(keys) < 2:
            continue
        combined = {}
        conflict = False
        for key in keys:
            for name, item in facts[key]['value']['fields'].items():
                if name in combined and canonical_hash(combined[name]) != canonical_hash(item):
                    conflict = True
                combined[name] = deepcopy(item)
        if conflict:
            continue
        merged = deepcopy(facts[keys[0]])
        merged['value']['fields'] = combined
        merchant_merged[keys[0]] = merged
        merchant_merged.update({key: None for key in keys[1:]})
    rendered=[]
    for key in selected:
        if grouped is not None and key in candidate_keys:
            if key==candidate_keys[0]:rendered.append(grouped)
            continue
        if key in merchant_merged:
            if merchant_merged[key] is not None:
                text = render_query_fact(merchant_merged[key], language)
                if text:
                    rendered.append(text)
            continue
        fact=facts[key]
        text=render_query_fact(fact,language) if fact.get('unit')==UNIT else render_monetary_facts(facts,[key],reply_language=language)
        if text:rendered.append(text)
    return '\n'.join(rendered)
