"""Model-selected registered product -> current owned catalogue READ, no guessed quote."""
from .planning import canonical_hash
import json

def selected_merchant_consultation(session,principal,decision,offered):
    selected=decision.retrieval_query
    if decision.mode!='CONSULT' or selected is None or not selected.subject_ref.startswith('merchant:'):return None
    from .semantic_router import prepare_semantic_input
    from .semantic_retrieval_query import compile_retrieval_query
    from .merchant_catalog_registry import DEFAULT_CATALOG
    from .merchant_service import merchant_catalog
    # Validate the exact model-selected current/history/public provenance;
    # no user-word routing or entity added by an independent phrase matcher.
    proof=compile_retrieval_query(selected,prepare_semantic_input(**offered['model']),mode='CONSULT',knowledge_topics=decision.knowledge_topics)
    matches=[row for row in DEFAULT_CATALOG if 'merchant:'+row[0].lower()==selected.subject_ref]
    if len(matches)!=1:return None
    result=merchant_catalog(session,principal,product_name=matches[0][4]);products=result['products']
    if len(products)!=1:return None
    product=products[0];en=selected.language=='en';price=product['unit_price_minor']/100
    name=product['product_name'];merchant=product['merchant_name'];content=product.get('service_content')or{}
    text=(f"{merchant} · {name}: CNY {price:.2f}. Current stock: {product['stock']}." if en else
        f"{merchant}的{name}，当前价格{price:.2f}元，库存{product['stock']}份。")
    if content.get('status')=='NOT_REGISTERED':
        text+=(' Detailed included items are not registered, so I cannot invent the contents.' if en else '具体包含的内容尚未登记，我不能替商户编造套餐内容。')
    text+=(' This is a catalogue lookup; no order or payment has been made.' if en else '目前只是查询，还没有下单或付款。')
    return {'answer':text,'source':{'source_id':'merchant-catalog:'+product['product_id'],'kind':'SCOPED_BUSINESS_FACTS',
        'title':merchant+' · '+name,'reference':'registered-merchant-catalog','content':json.dumps(result,ensure_ascii=False,sort_keys=True),
        'snapshot_kind':'owned-merchant-catalog-read-v1',
        'source_hash':canonical_hash(result),'owner_scope_hash':canonical_hash({'user_id':principal.user_id,'sandbox_id':principal.sandbox_id}),
        'bank_actions_authorized':False},'diagnostics':{'version':'selected-current-merchant-consult-v1','read_kind':'MERCHANT_CATALOG',
            'source_fingerprint':canonical_hash(result),'selected_subject':selected.subject_ref,'retrieval_proof':proof['diagnostics'],'bank_state_changed':False}}

def catalog_source_authorized(session,principal,row):
    from .merchant_service import merchant_catalog
    from .contracts import DomainError
    try:
        if row.get('snapshot_kind')!='owned-merchant-catalog-read-v1' or row.get('reference')!='registered-merchant-catalog' or row.get('kind')!='SCOPED_BUSINESS_FACTS':return False
        recorded=json.loads(row['content'])
        if canonical_hash(recorded)!=row.get('source_hash') or row.get('owner_scope_hash')!=canonical_hash({'user_id':principal.user_id,'sandbox_id':principal.sandbox_id}):return False
        products=recorded['products']
        if len(products)!=1:return False
        product=products[0]
        if row['source_id']!='merchant-catalog:'+product['product_id']:return False
        current=merchant_catalog(session,principal,merchant_product_id=product['product_id'])['products']
        return len(current)==1 and all(current[0][key]==product[key] for key in ('product_id','merchant_id','product_name','merchant_name','sku'))
    except (DomainError,ValueError,TypeError,KeyError):return False
