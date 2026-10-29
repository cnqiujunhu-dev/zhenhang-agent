"""Production-registry ASGI/chat job semantic entry; no live API or payments.

Do not count old registry-less RuleIntent fixtures as this gate. All semantic
and explanation HTTP use the real adapter and a shared MockTransport; the
temporary SQLite fixture is independent of every running project service.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func,select

from test_access_runtime import named_runtime,login
from zhenxing.conversation_models import ChatTurn
from zhenxing.conversation_service import process_chat_job
from zhenxing.accounts import create_customer_account
from zhenxing.model_gateway import DeepSeekIntentModel,ModelGateway
from zhenxing.model_routing import ProviderRegistry
from zhenxing.models import Account,Card,Confirmation,Subscription,Task,Transfer,Transaction,SubscriptionPayment,ToolCall,Holding,InvestmentOrder
from zhenxing.extensions_models import CarbonWallet
from zhenxing.access_models import LoginAccount
from zhenxing.routing_models import ModelRoutingPolicy,RoutingPolicy
from zhenxing.runtime_models import RuntimeOperation,WorkflowJob,RuntimeRecord
from zhenxing.runtime import run_one_job
from zhenxing.runtime import task_trace
from zhenxing.services import Principal

class IsolatedSemanticFixture(SimpleNamespace):
    def __repr__(self):return '<isolated SQLite/cloud MockTransport fixture; credentials omitted>'


def choice(mode='CONSULT',**changes):
    result={'mode':mode,'action_kinds':[],'dependencies':[],'focus_ref':None,'entity_refs':[],
        'changes':[],'knowledge_topics':[],'clarification':'请问你指的是哪一项？' if mode=='CLARIFY' else '', 'confidence':0.9}
    result.update(changes);return result


@pytest.fixture
def cloud_asgi(named_runtime,monkeypatch):
    from zhenxing import bounded_http
    http,factory,_old_gateway,ids=named_runtime
    original_boundary=bounded_http.bounded_post_json;original_send=httpx.Client.send
    state={'semantic':choice(),'status':200,'raw':None,'error':None,'events':[],'decision_factory':None}
    def transport_handler(request):
        assert request.url.host=='semantic-asgi.invalid' and request.method=='POST'
        payload=json.loads(request.content)
        from semantic_wire_fixture import supplied_input
        try:user=supplied_input(payload)
        except (json.JSONDecodeError,TypeError,KeyError):user=None
        semantic=isinstance(user,dict) and user.get('schema_version')=='unified-cloud-semantics-v1'
        narration=isinstance(user,dict) and user.get('schema_version')=='customer-reply-v1'
        explanation=isinstance(user,dict) and 'sources' in user
        purpose='SEMANTIC' if semantic else 'NARRATION' if narration else 'EXPLANATION' if explanation else 'UNEXPECTED_PLANNER'
        state['events'].append({'purpose':purpose,'payload':deepcopy(payload),'offered':deepcopy(user)})
        assert purpose!='UNEXPECTED_PLANNER','Record every actual HTTP attempt, including a forbidden second legacy planner'
        if semantic:
            if state.get('semantic_side_effect'):state['semantic_side_effect']()
            if state['error']:raise state['error']('fixture-only transport fault')
            reply=state['decision_factory'](user) if state['decision_factory'] else state['semantic']
            if reply.get('mode')=='CLARIFY' and 'clarification_evidence' not in reply:
                reply={**reply,'clarification_evidence':{'reason':'UNRESOLVED_PURPOSE',
                    'source_span':{'start':0,'end':len(user['message'])}}}
            raw=state['raw'] if state['raw'] is not None else json.dumps(reply,ensure_ascii=False)
            status=state['status']
        elif narration:
            assert any(event['purpose']=='SEMANTIC' for event in state['events'])
            facts=[fact['fact_id'] for fact in user['facts'] if fact.get('required') is True]
            questions=[item['question_id'] for item in user['needed_questions']]
            raw=json.dumps({'intro':'{{'+questions[0]+'}}' if questions else '好的，{{business_intro}}',
                'detail':'\n'.join('{{'+ref+'}}' for ref in questions[1:]) if questions else '{{business_detail}}' if 'business_detail' in facts else '',
                'fact_refs':[] if questions else facts,'question_refs':questions},ensure_ascii=False);status=200
        else:
            assert state['events'][0]['purpose']=='SEMANTIC','Explanation must not precede cloud semantics'
            assert 'sources' in user,'No second IntentProposal planner call in this social/consultation gate'
            sources=user['sources'];assert sources
            facts=user.get('available_monetary_facts',[])
            selected=[fact for fact in facts if state.get('fact_label') and state['fact_label'] in fact.get('label','')]
            refs=[fact['fact_id'] for fact in selected]
            citations=list(dict.fromkeys([sources[0]['source_id'],*[fact['source_id'] for fact in selected]]))
            raw=json.dumps({'answer':'我可以根据已登记的服务说明帮你解释；涉及办理仍需单独核对。',
                'sources':citations,'next_steps':[],'fact_refs':refs},ensure_ascii=False);status=200
            if state.get('explanation_factory'):
                raw=json.dumps(state['explanation_factory'](user),ensure_ascii=False)
        return httpx.Response(status,json={'model':'deepseek-v4-pro','choices':[{'finish_reason':'stop','message':{'content':raw}}]})
    transport=httpx.MockTransport(transport_handler)
    def bounded(url,payload,headers=None,**kwargs):
        assert str(url).startswith('https://semantic-asgi.invalid/')
        kwargs.pop('legacy_post',None)
        return original_boundary(url,payload,headers,transport=transport,legacy_post=None,**kwargs)
    def only_fixture(self,request,*args,**kwargs):
        assert request.url.host in {'semantic-asgi.invalid','testserver'},'No actual network or production data'
        assert state.get('allow_isolated_confirmations') or not any(part in request.url.path for part in ('/confirm','/execute','/auth/challenges')),'Never confirm outside an explicitly opted-in disposable fixture'
        return original_send(self,request,*args,**kwargs)
    def forbidden(*args,**kwargs):raise AssertionError('New registry entry cannot classify by keyword/rule')
    monkeypatch.setattr(bounded_http,'bounded_post_json',bounded);monkeypatch.setattr(httpx.Client,'send',only_fixture)
    # Import the legacy projection alias before patching its source module.
    # Otherwise a late import caches `forbidden` after this fixture's teardown
    # and contaminates later independent legacy-read fixtures in the same run.
    # Every alias remains forbidden during this unified semantic fixture.
    from semantic_fixture_guards import forbid_intent_rules
    forbid_intent_rules(monkeypatch, forbidden)
    # Fixture-only environment isolation, restored automatically by pytest;
    # never reads/writes .env, container state, actual policy or bank accounts.
    monkeypatch.delenv('ZX_KNOWLEDGE_DATABASE_URL',raising=False)
    monkeypatch.setenv('ZX_CUSTOMER_REPLY_ENABLED','1')
    adapter=DeepSeekIntentModel('https://semantic-asgi.invalid/v1','mock-not-live-key','deepseek-v4-pro')
    registry=ProviderRegistry();registry.register('deepseek','deepseek-v4-pro',adapter,is_cloud=True,credential_configured=True)
    gateway=ModelGateway(primary=adapter,registry=registry);http.app.state.model_gateway=gateway
    with factory.begin() as db:
        policy=db.get(ModelRoutingPolicy,'global');assert policy
        policy.policy_json=RoutingPolicy(mode='CLOUD_ONLY',cloud_model='deepseek-v4-pro',cloud_enabled=True,
            cloud_secondary_model=None,standard_read_fastpath=False,max_http_attempts=1,max_elapsed_seconds=45,version=10).model_dump();policy.version=10
        create_customer_account(db,'quyuwei','quyuwei88',seed=33)
    headers=login(http,'customerone','customer88')
    response=http.post('/api/v3/customer/conversations',headers=headers,json={'title':'Isolated cloud semantic test'})
    assert response.status_code==201;thread_id=response.json()['conversation_id']
    return IsolatedSemanticFixture(http=http,factory=factory,gateway=gateway,state=state,headers=headers,thread_id=thread_id)


def finance_snapshot(factory):
    with factory() as db:
        return {'accounts':[(row.id,row.balance_minor,row.version) for row in db.scalars(select(Account).order_by(Account.id))],
            'cards':[(row.id,row.status,row.version,row.limit_minor) for row in db.scalars(select(Card).order_by(Card.id))],
            'subscriptions':[(row.id,row.status,row.amount_minor,row.auto_renew,row.billing_months,row.version) for row in db.scalars(select(Subscription).order_by(Subscription.id))],
            'payments':db.scalar(select(func.count()).select_from(SubscriptionPayment)),
            'transfers':db.scalar(select(func.count()).select_from(Transfer)),
            'transactions':db.scalar(select(func.count()).select_from(Transaction)),
            'holdings':[(row.id,row.units,row.version) for row in db.scalars(select(Holding).order_by(Holding.id))],
            'orders':db.scalar(select(func.count()).select_from(InvestmentOrder)),
            'points':[(row.id,row.points) for row in db.scalars(select(CarbonWallet).order_by(CarbonWallet.id))],
            'consumed':db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.consumed_at.is_not(None)))}


def send(h,message,identifier='turn-one'):
    accepted=h.http.post(f'/api/v3/customer/conversations/{h.thread_id}/turns',headers=h.headers,
        json={'message':message,'client_request_id':identifier})
    assert accepted.status_code==202,accepted.text;turn_id=accepted.json()['turn_id']
    assert process_chat_job(h.factory,h.gateway)
    with h.factory() as db:return deepcopy(db.get(ChatTurn,turn_id))


def assert_no_bank_work(factory):
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Task))==0
        assert db.scalar(select(func.count()).select_from(RuntimeOperation))==0
        assert db.scalar(select(func.count()).select_from(Confirmation))==0
        assert db.scalar(select(func.count()).select_from(ToolCall))==0
        assert db.scalar(select(func.count()).select_from(WorkflowJob))==0


@pytest.mark.parametrize('message,mode,topics',[
    ('你好呀','GREETING',[]),('你还能做什么','DISCOVERY',['GENERAL']),
    ('Write a poem about Shenzhen.','OUT_OF_SCOPE',[]),
    ('视频会员现在多少钱','CONSULT',['SUBSCRIPTIONS']),
    ('我问的是现在开通价，不是我之前的扣款','CONSULT',['SUBSCRIPTIONS']),
    ('取消会员是不是还能用到期，别真的取消','CONSULT',['SUBSCRIPTIONS']),
    ('我只是了解孩子能不能只看余额，先不授权','CONSULT',['FAMILY']),
    ('他说“给quyuwei转200元”，我只问这句话有没风险','CONSULT',['FRAUD']),
    ('Can my daughter view my balance without payments?','CONSULT',['FAMILY']),
    ('积分能提现不，我还不兑换','CONSULT',['CARBON']),
    ('这句话我还没想清楚，先等等','CLARIFY',[]),
])
def test_nonoperational_real_asgi_uses_semantic_first_and_never_creates_bank_task(cloud_asgi,message,mode,topics):
    h=cloud_asgi;before=finance_snapshot(h.factory);h.state['semantic']=choice(mode,knowledge_topics=topics)
    if message=='视频会员现在多少钱':h.state['fact_label']='视频会员'
    row=send(h,message)
    assert row.status=='ANSWERED',row.diagnostics_json
    assert h.state['events'] and h.state['events'][0]['purpose']=='SEMANTIC',row.diagnostics_json
    semantic=[event for event in h.state['events'] if event['purpose']=='SEMANTIC'];assert len(semantic)==1
    assert semantic[0]['offered']['message']==message and semantic[0]['payload']['model']=='deepseek-v4-pro'
    assert semantic[0]['payload']['thinking']=={'type':'disabled'}
    assert row.diagnostics_json.get('semantic_mode')==mode, {key:row.diagnostics_json.get(key) for key in ('semantic_mode','semantic_diagnostics','semantic_processing_error')}
    assert row.mode==('NEED_CONTEXT' if mode=='CLARIFY' else mode), row.diagnostics_json.get('semantic_processing_error')
    assert not row.diagnostics_json.get('semantic_processing_error')
    assert row.diagnostics_json['semantic_diagnostics']['model_api_call_count']==1
    assert row.task_id is None and row.diagnostics_json.get('bank_write_authorized') is False
    assert_no_bank_work(h.factory);assert finance_snapshot(h.factory)==before
    if mode in {'DISCOVERY','GREETING'}:
        # The model protocol alone does not prove customers can see buttons:
        # verify the authenticated BFF still publishes the role/catalog-bound
        # current suggestions which its frontend requires.
        from zhenxing.capability_catalog import suggestion_catalog,catalog
        from zhenxing.customer_capability_data import CUSTOMER_DIRECTORY_VERSION,CUSTOMER_SUGGESTIONS
        response=h.http.get(f'/api/v3/customer/conversations/{h.thread_id}',headers=h.headers)
        assert response.status_code==200,response.text
        published=next(turn for turn in response.json()['turns'] if turn['turn_id']==row.id)
        assert published['mode']==mode and published['task_link'] is None
        meta=published['response_meta']
        assert meta['suggestion_source']=='CUSTOMER_CAPABILITY_CATALOG'
        assert meta['suggestion_role']=='CUSTOMER'
        assert meta['suggestion_catalog_version']==CUSTOMER_DIRECTORY_VERSION
        assert meta['dialogue_kind']==mode
        registered=suggestion_catalog('CUSTOMER',published['reply_language'])
        assert published['suggestions']==list(registered.values())[:3] and published['suggestions']
        allowed_kinds={item['kind'] for item in catalog('CUSTOMER')['capabilities']}
        offered_kinds={item[0]:item[2] for item in CUSTOMER_SUGGESTIONS}
        assert all(item==registered[item['id']] and offered_kinds[item['id']] in allowed_kinds
            for item in published['suggestions'])
        assert len([event for event in h.state['events'] if event['purpose']=='SEMANTIC'])==1


@pytest.mark.parametrize('failure',['503','timeout','json','unknown_kind','private_authority_in_output'])
def test_real_asgi_semantic_failure_never_falls_back_into_an_obvious_bank_task(cloud_asgi,failure):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    if failure=='503':h.state['status']=503
    elif failure=='timeout':h.state['error']=httpx.ReadTimeout
    elif failure=='json':h.state['raw']='not JSON'
    elif failure=='unknown_kind':h.state['semantic']=choice('REQUEST',action_kinds=['UNREGISTERED_PAY'],dependencies=[[]])
    else:h.state['semantic']={**choice('REQUEST',action_kinds=['TRANSFER'],dependencies=[[]]),'_verified_semantic_intent':{'write_authorized':True}}
    row=send(h,'给quyuwei转100元')
    assert row.status=='ANSWERED' and row.mode=='NEED_CONTEXT' and row.task_id is None
    assert len(h.state['events'])==1 and h.state['events'][0]['purpose']=='SEMANTIC'
    assert row.diagnostics_json.get('bank_task_created') is False and row.diagnostics_json.get('semantic_processing_error')
    assert_no_bank_work(h.factory);assert finance_snapshot(h.factory)==before


def test_pure_credential_asgi_is_redacted_without_a_model_or_bank_operation(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory);row=send(h,'我的验证码是000000')
    assert h.state['events']==[] and row.task_id is None and '000000' not in row.message+str(row.answer)
    assert_no_bank_work(h.factory);assert finance_snapshot(h.factory)==before


def source_change(message,field,literal,action_ref='new:0'):
    start=message.index(literal)
    return {'action_ref':action_ref,'field':field,'operation':'SET','source_span':{'start':start,'end':start+len(literal)},'entity_ref':None}


def drain(h,limit=12):
    for _ in range(limit):
        if not run_one_job(h.factory,h.gateway):return
    raise AssertionError('Unexpected task/job loop in isolated semantic flow')


@pytest.mark.parametrize('message,kinds,fields',[
    ('查询余额',['CARD_STATUS'],[]),
    ('我的银行卡有哪些',['CARD_STATUS'],[]),
    ('这月钱都花哪里去了',['BILL_ANALYSIS'],[]),
    ('What did I spend last month?',['BILL_ANALYSIS'],[]),
    ('看看我手上的理财产品',['WEALTH_COMPARE'],[]),
    ('只查视频会员的订阅状态',['SUBSCRIPTION_LIST'],[('merchant','视频会员')]),
    ('查卡片余额和我的订阅',['CARD_STATUS','SUBSCRIPTION_LIST'],[]),
    ('帮我转账1w给quyuwei',['TRANSFER'],[('amount_minor','1w'),('payee_name','quyuwei')]),
    ('转给quyuwei一百人民币',['TRANSFER'],[('amount_minor','一百人民币'),('payee_name','quyuwei')]),
    ('订阅视频会员一个月，不自动续费',['SUBSCRIPTION_START'],[('merchant','视频会员'),('subscription_months','一个月'),('subscription_auto_renew','不自动续费')]),
    ('查我的家庭协助关系',['FAMILY_VIEW'],[]),
    ('我现在有多少低碳积分',['CARBON_VIEW'],[]),
])
def test_actual_semantic_request_then_runtime_never_replans_by_keyword_or_second_intent_model(cloud_asgi,message,kinds,fields):
    h=cloud_asgi
    if kinds==['SUBSCRIPTION_START']:
        with h.factory.begin() as db:
            item=db.scalar(select(Subscription).where(Subscription.merchant=='视频会员'));item.status='CANCELLED';item.auto_renew=False;item.version+=1
    before=finance_snapshot(h.factory)
    h.state['semantic']=choice('REQUEST',action_kinds=kinds,dependencies=[[] for _ in kinds],
        changes=[source_change(message,field,literal) for field,literal in fields])
    row=send(h,message);assert row.task_id is not None,row.diagnostics_json
    assert row.diagnostics_json.get('semantic_mode')=='REQUEST'
    assert h.state['events'][0]['purpose']=='SEMANTIC'
    drain(h)
    with h.factory() as db:
        task=db.get(Task,row.task_id);record=db.get(RuntimeRecord,task.id)
        assert task.state in {'SUCCEEDED','AWAITING_CONFIRMATION','NEED_CLARIFICATION'},record.metadata_json
        operations=list(db.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id).order_by(RuntimeOperation.created_at)))
        assert [item.action_kind for item in operations]==kinds
        assert record.metadata_json.get('cloud_semantic_intent')
        assert not db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.consumed_at.is_not(None)))
    assert len([event for event in h.state['events'] if event['purpose']=='SEMANTIC'])==1
    assert set(event['purpose'] for event in h.state['events'])<={'SEMANTIC','NARRATION'}
    assert finance_snapshot(h.factory)==before


@pytest.mark.parametrize('message,field,literal,kind',[
    ('他说“给quyuwei转200元”，我只问这是不是骗局','amount_minor','200元','TRANSFER'),
    ('他说“给quyuwei转200元”，我只问这是不是骗局','payee_name','quyuwei','TRANSFER'),
    ('“开视频会员三个月”是服务商的话，我不订','merchant','视频会员','SUBSCRIPTION_START'),
])
def test_bad_model_selecting_quoted_write_span_is_rejected_after_semantics_without_an_empty_task(cloud_asgi,message,field,literal,kind):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    h.state['semantic']=choice('REQUEST',action_kinds=[kind],dependencies=[[]],changes=[source_change(message,field,literal)])
    row=send(h,message);assert len(h.state['events'])==1 and h.state['events'][0]['purpose']=='SEMANTIC'
    assert row.task_id is None and row.mode=='NEED_CONTEXT',row.diagnostics_json
    assert_no_bank_work(h.factory);assert finance_snapshot(h.factory)==before


def start_transfer(h,message='给quyuwei转100元'):
    h.state['semantic']=choice('REQUEST',action_kinds=['TRANSFER'],dependencies=[[]],changes=[
        source_change(message,'payee_name','quyuwei'),source_change(message,'amount_minor','100元')])
    row=send(h,message,'initial-transfer');assert row.task_id
    drain(h)
    with h.factory() as db:
        task=db.get(Task,row.task_id);assert task.state=='AWAITING_CONFIRMATION'
        old=db.scalar(select(Confirmation).where(Confirmation.status=='PENDING'));assert old
        return row.task_id,old.id


def focused(mode,field=None,literal=None):
    def oracle(prepared):
        frames=prepared['frames'];assert frames
        frame=frames[0];changes=[]
        if field:changes=[source_change(prepared['message'],field,literal,frame['actions'][0]['action_ref'])]
        return choice(mode,focus_ref=frame['frame_ref'],changes=changes)
    return oracle


def test_real_cloud_pause_amend_resume_preserves_same_intent_and_does_not_mint_confirmation_while_paused(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory);task_id,old_id=start_transfer(h)
    h.state['decision_factory']=focused('PAUSE');row=send(h,'我再想想，先别转','pause')
    assert row.mode=='FEEDBACK' and row.task_id==task_id
    with h.factory() as db:
        assert db.get(Confirmation,old_id).status=='CANCELLED'
        assert not db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.status=='PENDING'))
    h.state['decision_factory']=focused('CONTINUE','amount_minor','80元')
    amended=send(h,'那先改80元，还是暂停','paused-amount');assert amended.mode=='FEEDBACK' and amended.task_id==task_id
    drain(h)
    with h.factory() as db:
        assert not db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.status=='PENDING'))
        assert db.get(RuntimeRecord,task_id).metadata_json['semantic_pause']['pending_binding']['expected_proposal']['actions'][0]['slots']['amount_minor']==8000
    h.state['decision_factory']=focused('RESUME');resumed=send(h,'现在继续刚才那笔，先让我核对','resume')
    assert resumed.task_id==task_id and resumed.mode=='REVISE',resumed.diagnostics_json
    drain(h)
    with h.factory() as db:
        current=db.scalar(select(Confirmation).where(Confirmation.status=='PENDING'));assert current and current.id!=old_id
        parent=db.get(Task,task_id)
        current_view=task_trace(db,Principal(parent.user_id,parent.sandbox_id),task_id)
        assert current_view['pending_confirmation']['scope']['amount_minor']==8000
        assert not db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.consumed_at.is_not(None)))
    assert len([event for event in h.state['events'] if event['purpose']=='SEMANTIC'])==4
    assert finance_snapshot(h.factory)==before


def test_inserted_price_question_does_not_alter_or_cancel_the_owned_pending_transfer(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory);task_id,confirmation_id=start_transfer(h)
    with h.factory() as db:version=db.get(Task,task_id).version;plan=deepcopy(db.get(Task,task_id).plan_json)
    h.state['semantic']=choice('CONSULT',knowledge_topics=['SUBSCRIPTIONS']);h.state['fact_label']='视频会员'
    row=send(h,'插一句，视频会员现在多少钱，先别改刚才的转账','price-insertion')
    assert row.mode=='CONSULT' and row.task_id is None
    with h.factory() as db:
        task=db.get(Task,task_id);assert task.version==version and task.plan_json==plan
        assert db.get(Confirmation,confirmation_id).status=='PENDING'
        assert db.scalar(select(func.count()).select_from(Task).where(Task.id==task_id))==1
    assert len([event for event in h.state['events'] if event['purpose']=='SEMANTIC'])==2
    assert finance_snapshot(h.factory)==before


def test_actor_epoch_changed_during_semantic_http_cannot_create_request_with_old_owner_context(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    h.state['semantic']=choice('REQUEST',action_kinds=['CARD_STATUS'],dependencies=[[]])
    def change_epoch():
        with h.factory.begin() as db:
            account=db.scalar(select(LoginAccount).where(LoginAccount.username=='customerone'));account.version+=1
    h.state['semantic_side_effect']=change_epoch
    row=send(h,'查下我的银行卡','epoch-change')
    assert row.task_id is None and row.mode=='NEED_CONTEXT'
    assert row.diagnostics_json.get('semantic_processing_error')=='SEMANTIC_ACTOR_CHANGED'
    assert len(h.state['events'])==1 and h.state['events'][0]['purpose']=='SEMANTIC'
    assert_no_bank_work(h.factory);assert finance_snapshot(h.factory)==before


def test_query_result_second_card_reference_binds_real_owned_entity_not_a_generated_card_number(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    h.state['semantic']=choice('REQUEST',action_kinds=['CARD_STATUS'],dependencies=[[]]);read=send(h,'列一下我的银行卡','card-list')
    assert read.task_id;drain(h)
    def oracle(prepared):
        candidates=[item for item in prepared['entities'] if item.get('domain')=='CARDS' and item.get('ordinal')==1]
        assert len(candidates)==1,'Two known cards are required in this isolated fixture'
        selected=candidates[0]
        return choice('REQUEST',action_kinds=['CARD_LOCK'],dependencies=[[]],entity_refs=[selected['entity_ref']],changes=[{
            **source_change(prepared['message'],'card_number','第二张'), 'entity_ref':selected['entity_ref']}])
    h.state['decision_factory']=oracle;selected=send(h,'先保护第二张，给我方案核对','second-card');assert selected.task_id,selected.diagnostics_json
    drain(h)
    with h.factory() as db:
        parent=db.get(Task,selected.task_id);view=task_trace(db,Principal(parent.user_id,parent.sandbox_id),parent.id)
        assert view['state']=='AWAITING_CONFIRMATION' and view['pending_confirmation']
        assert db.scalar(select(func.count()).select_from(Card).where(Card.status=='LOCKED'))==0
    assert len([event for event in h.state['events'] if event['purpose']=='SEMANTIC'])==2
    assert finance_snapshot(h.factory)==before


@pytest.mark.parametrize('mutation',['selected_kind','source_hash','source_receipt','missing_marker'])
def test_corrupted_persisted_semantic_binding_cannot_trigger_old_planner_or_tools(cloud_asgi,mutation):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    h.state['semantic']=choice('REQUEST',action_kinds=['CARD_STATUS'],dependencies=[[]]);row=send(h,'看看我现在还剩多少钱','corrupt-semantic')
    assert row.task_id
    with h.factory.begin() as db:
        record=db.get(RuntimeRecord,row.task_id);metadata=deepcopy(record.metadata_json)
        binding=metadata['cloud_semantic_intent']
        if mutation=='selected_kind':binding['expected_proposal']['actions'][0]['kind']='TRANSFER'
        elif mutation=='source_hash':binding['source_message_hash']='0'*64
        elif mutation=='source_receipt':
            turn=db.get(ChatTurn,row.id);turn.diagnostics_json={**turn.diagnostics_json,'semantic_receipt':{**turn.diagnostics_json['semantic_receipt'],'decision_hash':'f'*64}}
        else:metadata.pop('cloud_semantic_intent')
        record.metadata_json=metadata
    drain(h)
    with h.factory() as db:
        parent=db.get(Task,row.task_id)
        assert parent.state=='FAILED'
        assert db.scalar(select(func.count()).select_from(ToolCall))==0
        assert not db.scalar(select(func.count()).select_from(Confirmation))
    # A failure explanation may have its own reference-bounded narration;
    # there must never be a second semantic or legacy planner request.
    assert len([event for event in h.state['events'] if event['purpose']=='SEMANTIC'])==1
    assert set(event['purpose'] for event in h.state['events'])<={'SEMANTIC','NARRATION'}
    assert finance_snapshot(h.factory)==before
