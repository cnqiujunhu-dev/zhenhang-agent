"""One fake cloud HTTP interpretation -> source proof, temporary SQLite only.

No product source edits, local inference, real API or bank confirmation I/O.
"""
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest
from sqlalchemy import select

from zhenxing.database import create_database
from zhenxing.schema import ensure_schema
from zhenxing.services import Principal,create_session
from zhenxing.conversation_models import ChatThread
from zhenxing.conversation_service import create_conversation,submit_turn
from zhenxing.model_gateway import ModelGateway,DeepSeekIntentModel
from zhenxing.model_routing import ProviderRegistry
from zhenxing.routing_models import RoutingPolicy
from zhenxing.semantic_context import semantic_context
from zhenxing.semantic_router import route_semantic_turn,serialize_semantic_receipt
from zhenxing.semantic_proposal import (METADATA_KEY,PRIVATE_KEY,build_semantic_intent_binding,
    load_verified_semantic_intent,ground_semantic_proposal,validate_semantic_proposal,semantic_verified)
from zhenxing.intelligence import IntentProposal,ProposedAction,ActionSlots
from zhenxing.models import Task
from zhenxing.runtime import accept_task,clarify_runtime
from zhenxing.runtime_models import RuntimeRecord
from zhenxing.contracts import DomainError


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    engine,factory=create_database('sqlite:///'+(tmp_path/'semantic.db').as_posix());ensure_schema(engine)
    with factory.begin() as db:
        actor=create_session(db,'baseline');principal=Principal(actor['user_id'],actor['sandbox_id'])
        thread=create_conversation(db,principal);identifier=thread.id
    registry=ProviderRegistry();registry.register('deepseek','deepseek-v4-pro',DeepSeekIntentModel(
        'https://semantic.invalid','fixture-placeholder','deepseek-v4-pro'),is_cloud=True,credential_configured=True)
    gateway=ModelGateway(registry=registry);responses=[];calls=[]
    def post(url,**kwargs):
        calls.append(kwargs['json']);decision=responses.pop(0)
        return httpx.Response(200,request=httpx.Request('POST',url),json={'model':'deepseek-v4-pro',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(decision,ensure_ascii=False)}}]})
    monkeypatch.setattr(httpx,'post',post)
    monkeypatch.setattr(httpx.Client,'request',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No real HTTP')))
    yield factory,principal,identifier,gateway,responses,calls
    engine.dispose()


def decision(kinds,changes=(),*,mode='REQUEST',focus=None,deps=None):
    return {'mode':mode,'action_kinds':kinds,'dependencies':deps if deps is not None else [[] for _ in kinds],
        'focus_ref':focus,'entity_refs':[],'changes':list(changes),'knowledge_topics':[],
        'clarification':'','confidence':0.99}


def change(text,field,raw,ref='new:0',operation='SET'):
    left=text.index(raw)
    return {'action_ref':ref,'field':field,'operation':operation,'source_span':{'start':left,'end':left+len(raw)}}


def stage(fixture,text,semantic,*,parent_id=None):
    factory,principal,thread_id,gateway,responses,calls=fixture
    with factory.begin() as db:
        thread=db.get(ChatThread,thread_id);turn=submit_turn(db,principal,thread_id,message=text,
            client_request_id='semantic-'+str(thread.next_sequence))
        offered=semantic_context(db,principal,thread,turn);responses.append(semantic)
        model={**offered['model'],'message':text}
        result=route_semantic_turn(gateway,policy=RoutingPolicy(mode='CLOUD_ONLY',cloud_enabled=True,
            cloud_model='deepseek-v4-pro',max_http_attempts=1),**model)
        assert result.status=='READY',result.diagnostics
        turn.diagnostics_json={**turn.diagnostics_json,'semantic_receipt':serialize_semantic_receipt(result)}
        parent=db.get(Task,parent_id) if parent_id else accept_task(db,principal,text,'semantic-task:'+turn.id)
        binding=build_semantic_intent_binding(db,principal,thread,turn,result,offered,parent=parent)
        if parent_id:clarify_runtime(db,principal,parent.id,text,parent.version,revise=True)
        record=db.get(RuntimeRecord,parent.id);record.metadata_json={**record.metadata_json,METADATA_KEY:binding}
        turn.task_id=parent.id;turn.status='ANSWERED';turn.mode='REVISE' if parent_id else 'NEW_TASK';thread.current_task_id=parent.id
        db.flush();proof=load_verified_semantic_intent(db,parent,record.metadata_json)
        assert validate_semantic_proposal(db,parent,proof.expected_proposal,record.metadata_json)
        return parent.id,deepcopy(binding),proof


def test_catalog_chosen_kind_does_not_require_any_old_action_keyword(fixture,monkeypatch):
    text='请帮我处理这张，尾号4321'
    from zhenxing import intelligence
    monkeypatch.setattr(intelligence,'rule_proposal',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No rule planner')))
    monkeypatch.setattr(intelligence,'_allowed_evidence',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No keyword action gate')))
    identifier,binding,proof=stage(fixture,text,decision(['CARD_LOCK'],[change(text,'card_last4','4321')]))
    assert proof.expected_proposal.actions[0].kind=='CARD_LOCK' and proof.expected_proposal.actions[0].slots.card_last4=='4321'
    assert len(fixture[-1])==1 and binding['bank_write_authorized'] is False


def test_new_transfer_exact_fields_and_missing_proposal_slots_use_verified_source_not_model_values(fixture):
    text='请给房东处理200元'
    identifier,binding,proof=stage(fixture,text,decision(['TRANSFER'],[change(text,'payee_name','房东'),change(text,'amount_minor','200元')]))
    raw=IntentProposal(actions=[ProposedAction(kind='TRANSFER')])
    result=ground_semantic_proposal(raw,text,{PRIVATE_KEY:proof})
    assert result.actions[0].slots.payee_name=='房东' and result.actions[0].slots.amount_minor==20000
    assert result.fallback is False and result.model_label.endswith('deepseek-v4-pro')


@pytest.mark.parametrize('text,raw',[
    ('帮我转1w给quyuwei','quyuwe'),
    ('帮我转1w给quyuwei','uyuwei'),
    ('帮我转1w给quyuwei','yuwe'),
    ('给customer20261011转200元','customer2026'),
    ('给customer20261011转200元','20261011'),
    ('给customer_2026转200元','customer'),
    ('给customer_2026转200元','2026'),
    ('给customer-2026转200元','customer'),
])
def test_partial_ascii_identity_token_span_is_rejected_without_guessing_or_second_http(fixture,text,raw):
    before=len(fixture[-1])
    amount='1w' if '1w' in text else '200元'
    with pytest.raises(DomainError) as error:
        stage(fixture,text,decision(['TRANSFER'],[change(text,'payee_name',raw),change(text,'amount_minor',amount)]))
    assert error.value.code=='SEMANTIC_IDENTITY_SPAN_INCOMPLETE'
    assert len(fixture[-1])==before+1
    with fixture[0]() as db:
        assert not list(db.scalars(select(Task)))


@pytest.mark.parametrize('text,raw',[
    ('帮我转1w给quyuwei','quyuwei'),
    ('给customer20261011转200元','customer20261011'),
    ('给customer_2026转200元','customer_2026'),
    ('给customer-2026转200元','customer-2026'),
    ('给房东转200元','房东'),
    ('给张三转200元','张三'),
    ('给“customer20261011”转200元','customer20261011'),
    ('给“房东”转200元','房东'),
])
def test_complete_ascii_and_chinese_identity_spans_preserve_exact_literal(fixture,text,raw):
    amount='1w' if '1w' in text else '200元'
    _,_,proof=stage(fixture,text,decision(['TRANSFER'],[change(text,'payee_name',raw),change(text,'amount_minor',amount)]))
    assert proof.expected_proposal.actions[0].slots.payee_name==raw
    assert proof.expected_proposal.actions[0].slots.amount_minor==(1000000 if amount=='1w' else 20000)
    assert len(fixture[-1])==1


@pytest.mark.parametrize('field,text,raw',[
    ('merchant','订阅video2026','video202'),
    ('product_name','申购product2026 200元','product202'),
    ('grantee_name','让customer2026看余额','customer202'),
    ('participant_names','和customer2026、quyuwei分摊200元','customer202'),
    ('card_number','查zhenhanga1b2c3d4的银行卡','zhenhanga1b2c3d'),
])
def test_all_literal_identity_fields_share_ascii_boundary_guard(field,text,raw):
    from zhenxing.semantic_proposal import _trusted_span
    with pytest.raises(DomainError) as error:_trusted_span(text,change(text,field,raw)['source_span'],field)
    assert error.value.code=='SEMANTIC_IDENTITY_SPAN_INCOMPLETE'


@pytest.mark.parametrize('raw,months',[
    ('改成两个月',2),('期限设为三个月',3),('最终选十二个月',12),('调整到1个月',1),
    ('周期： 2 个月。',2),('进一步设为三个月',3),('改成半年',6),
    ('set the period to 2 months',2),('Please make this 3 months.',3),
    ('duration: 12months',12),('renew for 1 month',1),('length = 6 months',6),
    ('a month',1),('two months',2),('twelve months',12),
])
def test_month_scalar_inside_selected_assignment_span_does_not_require_a_phrase_classification(raw,months):
    from zhenxing.semantic_proposal import _literal_field
    assert _literal_field('subscription_months',raw,'SUBSCRIPTION_START',{})==months


@pytest.mark.parametrize('raw',[
    '改成0个月','改成13个月','改成负两个月','改成-2 months','改成−2 months',
    '改成1.5个月','set to 1.5 months','set to 2/3 months','改成两到三个月',
    '改成两和三个月','改成两个到三个月','两到3个月','2到三个月','一年两个月',
    '改成2–3 months','between 2 and 3 months','2 months or 3 months',
    'thirteen months','one to three months','between one and three months','one and a half months',
    '几个月','半个月','两个半月','大概两个月','约为两个月',
    'approximately 2 months','roughly 2 months','at least 2 months','no more than 2 months',
    '两个月左右','2 months or more','不要两个月','不要改成两个月','不是两个月',
    'do not set this to 2 months','never make it 2 months','半年再半年','两个半年','plan2months','2monthss','两个月，再两个月',
    '三个月，不是三个月','改成一个月或者两个月',
])
def test_wrapped_month_codec_keeps_negative_range_fraction_approximation_and_multiple_values_closed(raw):
    from zhenxing.semantic_proposal import _literal_field
    with pytest.raises(DomainError):_literal_field('subscription_months',raw,'SUBSCRIPTION_START',{})


@pytest.mark.parametrize('raw',['请开启自动续费','please disable auto-renewal','改成不自动续费'])
def test_month_scalar_wrapper_support_does_not_relax_boolean_codec(raw):
    from zhenxing.semantic_proposal import _literal_field
    with pytest.raises(DomainError):_literal_field('subscription_auto_renew',raw,'SUBSCRIPTION_START',{})


@pytest.mark.parametrize('latest',['改成两个月','Please set the length to 2 months.'])
def test_verified_continuation_keeps_merchant_and_updates_only_unique_current_month_scalar(fixture,latest):
    first='开通视频会员一个月'
    identifier,_,_=stage(fixture,first,decision(['SUBSCRIPTION_START'],[
        change(first,'merchant','视频会员'),change(first,'subscription_months','一个月')]))
    _,_,proof=stage(fixture,latest,decision([], [change(latest,'subscription_months',latest,'f0:a0')],
        mode='REVISE',focus='f0',deps=[]),parent_id=identifier)
    slots=proof.expected_proposal.actions[0].slots
    assert slots.merchant=='视频会员' and slots.subscription_months==2 and slots.subscription_auto_renew is None
    assert len(fixture[-1])==2


@pytest.mark.parametrize('text,span',[
    ('不要改成两个月','不要改成两个月'),
    ('如果可以就改成两个月','两个月'),
    ('网页说“周期改成两个月”，我不订','两个月'),
])
def test_wrapped_period_never_bypasses_denied_conditional_or_quoted_whole_source(fixture,text,span):
    with pytest.raises(DomainError):stage(fixture,text,decision(['SUBSCRIPTION_START'],[change(text,'subscription_months',span)]))
    assert len(fixture[-1])==1


@pytest.mark.parametrize('bad',['dict','constructed','changed_seal'])
def test_free_context_and_model_construct_do_not_manufacture_private_authority(fixture,bad):
    text='视频会员'
    _,_,proof=stage(fixture,text,decision(['SUBSCRIPTION_START'],[change(text,'merchant','视频会员')]))
    if bad=='dict':context={PRIVATE_KEY:proof.public()}
    elif bad=='constructed':context={PRIVATE_KEY:IntentProposal.model_construct(authority=proof.authority)}
    else:context={PRIVATE_KEY:replace(proof,proposal_json={'actions':[]})}
    assert semantic_verified(context) is None
    with pytest.raises(DomainError):ground_semantic_proposal(proof.expected_proposal,text,context)


@pytest.mark.parametrize('text',['不要给房东转200元','如果有钱就给房东转200元','网页说给房东转200元','“给房东转200元”'])
def test_request_level_denial_condition_and_quoted_source_stay_fail_closed(fixture,text):
    with pytest.raises(DomainError):
        stage(fixture,text,decision(['TRANSFER'],[change(text,'payee_name','房东'),change(text,'amount_minor','200元')]))


@pytest.mark.parametrize('amount',['-200元','200到300元','约200元','200美元'])
def test_model_span_cannot_convert_negative_range_approximation_or_foreign_money_into_cny(fixture,amount):
    text='给房东处理'+amount
    with pytest.raises(DomainError):stage(fixture,text,decision(['TRANSFER'],[change(text,'payee_name','房东'),change(text,'amount_minor',amount)]))


def test_continue_before_any_plan_snapshot_preserves_old_fields_and_clear_from_denied_identity(fixture):
    first='给房东处理200元'
    identifier,_,_=stage(fixture,first,decision(['TRANSFER'],[change(first,'payee_name','房东'),change(first,'amount_minor','200元')]))
    second='不是房东'
    _,binding,proof=stage(fixture,second,decision([], [change(second,'payee_name',second,'f0:a0','CLEAR')],mode='REVISE',focus='f0',deps=[]),parent_id=identifier)
    assert proof.expected_proposal.actions[0].slots.payee_name is None and proof.expected_proposal.actions[0].slots.amount_minor==20000
    assert binding['confirmation_inherited'] is False


def test_mixed_source_dag_cannot_be_dropped_or_model_constructed_to_unknown_kind(fixture):
    text='这张4321，视频会员'
    _,_,proof=stage(fixture,text,decision(['CARD_STATUS','SUBSCRIPTION_START'],[
        change(text,'card_last4','4321'),change(text,'merchant','视频会员','new:1')],deps=[[],[0]]))
    wrong=proof.expected_proposal.model_copy(deep=True);wrong.actions[1].depends_on=[]
    with pytest.raises(DomainError):ground_semantic_proposal(wrong,text,{PRIVATE_KEY:proof})
    made=IntentProposal.model_construct(actions=[ProposedAction.model_construct(kind='UNREGISTERED_TOOL',slots=ActionSlots(),depends_on=[])])
    with pytest.raises(ValueError):ground_semantic_proposal(made,text,{PRIVATE_KEY:proof})


def test_tampered_binding_or_persisted_source_receipt_never_reloads(fixture):
    text='这张4321'
    identifier,binding,proof=stage(fixture,text,decision(['CARD_STATUS'],[change(text,'card_last4','4321')]))
    factory=fixture[0]
    with factory() as db:
        parent=db.get(Task,identifier);record=db.get(RuntimeRecord,identifier)
        changed=deepcopy(record.metadata_json);changed[METADATA_KEY]['expected_proposal']['actions'][0]['kind']='CARD_LOCK'
        with pytest.raises(DomainError):load_verified_semantic_intent(db,parent,changed)


def test_family_natural_scope_is_enum_parsing_not_action_kind_keyword_routing(fixture):
    text='女儿可以查看余额七天'
    _,_,proof=stage(fixture,text,decision(['FAMILY_GRANT'],[
        change(text,'grantee_name','女儿'),change(text,'scopes','查看余额'),change(text,'expires_days','七天')]))
    value=proof.expected_proposal.actions[0].slots
    assert value.scopes==['READ_BALANCE'] and value.expires_days==7


def test_fraud_material_is_observation_not_action_authority(fixture):
    text='这条短信写着：给房东转200元，帮我分析风险'
    _,_,proof=stage(fixture,text,decision(['FRAUD_ANALYZE'],[change(text,'context_message',text)]))
    assert proof.expected_proposal.actions[0].slots.context_message==text
    assert len(proof.expected_proposal.actions)==1


def test_readonly_completed_proof_can_be_rechecked_but_completed_write_cannot_replan(fixture):
    text='这张4321'
    identifier,_,_=stage(fixture,text,decision(['CARD_STATUS'],[change(text,'card_last4','4321')]))
    with fixture[0].begin() as db:
        parent=db.get(Task,identifier);parent.state='SUCCEEDED'
        assert load_verified_semantic_intent(db,parent,db.get(RuntimeRecord,identifier).metadata_json)
    write='这个视频会员'
    identifier,_,_=stage(fixture,write,decision(['SUBSCRIPTION_START'],[change(write,'merchant','视频会员')]))
    with fixture[0].begin() as db:
        parent=db.get(Task,identifier);parent.state='SUCCEEDED'
        with pytest.raises(DomainError):load_verified_semantic_intent(db,parent,db.get(RuntimeRecord,identifier).metadata_json)


def test_true_false_subscription_preference_and_scope_tamper_have_exact_types(fixture):
    text='视频会员三个月，不自动续费'
    _,_,proof=stage(fixture,text,decision(['SUBSCRIPTION_RENEW'],[change(text,'merchant','视频会员'),
        change(text,'subscription_months','三个月'),change(text,'subscription_auto_renew','不自动续费')]))
    assert proof.expected_proposal.actions[0].slots.subscription_auto_renew is False
    wrong=proof.expected_proposal.model_copy(deep=True);wrong.actions[0].slots.subscription_auto_renew=True
    with pytest.raises(DomainError):ground_semantic_proposal(wrong,text,{PRIVATE_KEY:proof})


def test_live_actor_epoch_change_rejects_same_user_old_receipt(fixture):
    text='这张4321'
    identifier,_,_=stage(fixture,text,decision(['CARD_STATUS'],[change(text,'card_last4','4321')]))
    from zhenxing.access_models import LoginAccount
    from zhenxing.accounts import password_hash
    with fixture[0].begin() as db:
        db.add(LoginAccount(username='epoch_changed',password_hash=password_hash('fixture-password'),user_id=fixture[1].user_id,
            sandbox_id=fixture[1].sandbox_id,role='CUSTOMER',version=2))
    with fixture[0]() as db:
        with pytest.raises(DomainError):load_verified_semantic_intent(db,db.get(Task,identifier),db.get(RuntimeRecord,identifier).metadata_json)


@pytest.mark.parametrize('state',['UNKNOWN','EXECUTING','SUCCEEDED'])
def test_execution_or_unknown_write_frame_cannot_be_reinterpreted_as_unexecuted(fixture,state):
    text='房东200元'
    identifier,_,_=stage(fixture,text,decision(['TRANSFER'],[change(text,'payee_name','房东'),change(text,'amount_minor','200元')]))
    with fixture[0].begin() as db:
        parent=db.get(Task,identifier);parent.state=state
        with pytest.raises(DomainError):load_verified_semantic_intent(db,parent,db.get(RuntimeRecord,identifier).metadata_json)


def test_resume_verified_pause_retains_pending_fields_without_confirming_or_changing_original_message(fixture):
    factory,principal,thread_id,gateway,responses,calls=fixture
    first='房东200元'
    identifier,_,_=stage(fixture,first,decision(['TRANSFER'],[change(first,'payee_name','房东'),change(first,'amount_minor','200元')]))
    from zhenxing.semantic_lifecycle import pause_semantic_task,current_semantic_pause
    with factory.begin() as db:
        thread=db.get(ChatThread,thread_id);parent=db.get(Task,identifier)
        source=submit_turn(db,principal,thread_id,message='我再想想',client_request_id='pause-proof')
        offered=semantic_context(db,principal,thread,source);responses.append(decision([],mode='PAUSE',focus='f0',deps=[]))
        result=route_semantic_turn(gateway,policy=RoutingPolicy(mode='CLOUD_ONLY',cloud_enabled=True,cloud_model='deepseek-v4-pro',max_http_attempts=1),**{**offered['model'],'message':source.message})
        assert result.status=='READY'
        source.diagnostics_json={**source.diagnostics_json,'semantic_receipt':serialize_semantic_receipt(result),'semantic_control':'PAUSE','bank_write_authorized':False}
        pause_semantic_task(db,principal,thread,source,parent)
        source.task_id=identifier;source.mode='FEEDBACK';source.task_relationship='CURRENT';source.status='ANSWERED'
        db.flush();assert current_semantic_pause(db,principal,parent)
    from zhenxing.semantic_proposal import validate_paused_semantic_intent
    with factory.begin() as db:
        thread=db.get(ChatThread,thread_id);parent=db.get(Task,identifier)
        source=submit_turn(db,principal,thread_id,message='改成300元',client_request_id='pending-proof')
        offered=semantic_context(db,principal,thread,source)
        responses.append(decision([], [change(source.message,'amount_minor','300元','f0:a0')],mode='REVISE',focus='f0',deps=[]))
        result=route_semantic_turn(gateway,policy=RoutingPolicy(mode='CLOUD_ONLY',cloud_enabled=True,cloud_model='deepseek-v4-pro',max_http_attempts=1),**{**offered['model'],'message':source.message})
        source.diagnostics_json={**source.diagnostics_json,'semantic_receipt':serialize_semantic_receipt(result),'bank_write_authorized':False}
        pending=build_semantic_intent_binding(db,principal,thread,source,result,offered,parent=parent)
        record=db.get(RuntimeRecord,identifier)
        record.metadata_json={**record.metadata_json,'semantic_pause':{**record.metadata_json['semantic_pause'],'pending_binding':pending}}
        source.task_id=identifier;source.mode='FEEDBACK';source.task_relationship='CURRENT';source.status='ANSWERED';db.flush()
        proof=validate_paused_semantic_intent(db,principal,parent,pending)
        assert parent.message==first and proof.expected_proposal.actions[0].slots.amount_minor==30000
    _,binding,proof=stage(fixture,'继续吧',decision([],mode='RESUME',focus='f0',deps=[]),parent_id=identifier)
    assert proof.expected_proposal.actions[0].slots.amount_minor==30000
    assert proof.expected_proposal.actions[0].slots.payee_name=='房东'
    assert binding['resume_pause']['bank_write_authorized'] is False and binding['confirmation_inherited'] is False


def test_model_selected_consult_entity_uses_fresh_typed_facts_without_old_reference_keyword_resolution(fixture,monkeypatch):
    factory,principal,thread_id,gateway,responses,calls=fixture
    text='这张4321'
    identifier,_,_=stage(fixture,text,decision(['CARD_STATUS'],[change(text,'card_last4','4321')]))
    from zhenxing.runtime import run_one_job
    assert run_one_job(factory,gateway)
    from zhenxing.semantic_proposal import semantic_selected_object_sources,semantic_selected_object_sources_current
    from zhenxing import dialogue_references,dialogue_reference_integration
    monkeypatch.setattr(dialogue_references,'resolve_user_reference',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No old reference solver')))
    monkeypatch.setattr(dialogue_reference_integration,'build_result_reference_binding',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No old keyword binding')))
    with factory.begin() as db:
        thread=db.get(ChatThread,thread_id);turn=submit_turn(db,principal,thread_id,message='讲讲它',client_request_id='selected-consult')
        offered=semantic_context(db,principal,thread,turn);assert offered['model']['entities']
        target=offered['model']['entities'][0]['entity_ref']
        semantic=decision([],mode='CONSULT',deps=[]);semantic['entity_refs']=[target];responses.append(semantic)
        result=route_semantic_turn(gateway,policy=RoutingPolicy(mode='CLOUD_ONLY',cloud_enabled=True,cloud_model='deepseek-v4-pro',max_http_attempts=1),**{**offered['model'],'message':turn.message})
        assert result.status=='READY'
        turn.diagnostics_json={**turn.diagnostics_json,'semantic_receipt':serialize_semantic_receipt(result)}
        records=semantic_selected_object_sources(db,principal,thread,turn,result,offered)
        assert len(records)==1 and records[0]['source_binding']['mode']=='EXPLANATION'
        assert semantic_selected_object_sources_current(db,principal,records)
        from zhenxing.models import Card
        card=db.scalar(select(Card).where(Card.user_id==principal.user_id));card.limit_minor+=100
        db.flush();assert not semantic_selected_object_sources_current(db,principal,records)


def test_semantic_enum_options_accept_natural_scope_currency_and_renewal_meaning_without_phrase_lookup(fixture):
    from zhenxing.semantic_proposal import _literal_field
    assert _literal_field('scopes','只能知道余额','FAMILY_GRANT',{},['READ_BALANCE'])==['READ_BALANCE']
    assert _literal_field('currency','美刀','FX_TRANSFER',{},['USD'])=='USD'
    assert _literal_field('subscription_auto_renew','别再扣了','SUBSCRIPTION_RENEW',{},['DISABLED']) is False
    text='给wangwei处理100美刀'
    currency=change(text,'currency','美刀');currency['selected_options']=['USD']
    _,_,proof=stage(fixture,text,decision(['FX_TRANSFER'],[change(text,'payee_name','wangwei'),change(text,'amount_minor','100'),currency]))
    assert proof.expected_proposal.actions[0].slots.currency=='USD' and proof.expected_proposal.actions[0].slots.amount_minor==10000


@pytest.mark.parametrize('field,options',[
    ('amount_minor',['10000']),('merchant',['视频会员']),('scopes',['EXECUTE_TRANSFER']),
    ('currency',['CNY','USD']),('subscription_auto_renew',['False']),('proposal_kind',['EXECUTE_TRANSFER']),
])
def test_enum_channel_cannot_create_money_identity_new_permission_or_ambiguous_values(field,options):
    from zhenxing.semantic_proposal import _literal_field
    with pytest.raises(DomainError):_literal_field(field,'这句只是选项来源','TRANSFER',{},options)


def test_cny_transfer_currency_revision_becomes_registered_fx_without_converting_user_major_amount(fixture):
    first='房东200元'
    identifier,_,_=stage(fixture,first,decision(['TRANSFER'],[change(first,'payee_name','房东'),change(first,'amount_minor','200元')]))
    text='改美刀';currency=change(text,'currency','美刀','f0:a0');currency['selected_options']=['USD']
    _,binding,proof=stage(fixture,text,decision([], [currency],mode='REVISE',focus='f0',deps=[]),parent_id=identifier)
    assert proof.expected_proposal.actions[0].kind=='FX_TRANSFER'
    assert proof.expected_proposal.actions[0].slots.amount_minor==20000 and proof.expected_proposal.actions[0].slots.currency=='USD'
    assert binding['semantic_diagnostics']['server_normalization_reason']=='REGISTERED_DENOMINATION_TOOL_MAPPING'
    assert proof.expected_proposal.actions[0].slots.source_currency is None


def test_currency_contract_does_not_route_without_role_capability_or_discard_old_memo():
    from zhenxing.semantic_proposal import _normalize_currency_contract
    for values,allowed in [({'currency':'USD','amount_minor':20000},{'TRANSFER'}),
            ({'currency':'USD','amount_minor':20000,'memo':'房租'},{'TRANSFER','FX_TRANSFER'})]:
        with pytest.raises(DomainError):_normalize_currency_contract({'kind':'TRANSFER','slots':values,'depends_on':[]},allowed)


@pytest.mark.parametrize('text',["‘开视频会员三个月’是服务商的话，我不订",'我不订视频会员'])
def test_colloquial_quoted_merchant_instruction_or_request_denial_cannot_create_purchase(fixture,text):
    with pytest.raises(DomainError):stage(fixture,text,decision(['SUBSCRIPTION_START'],[change(text,'merchant','视频会员')]))


def test_deleting_metadata_flag_does_not_erase_actual_cloud_semantic_source_or_allow_legacy_fallback(fixture):
    text='这张4321'
    identifier,_,_=stage(fixture,text,decision(['CARD_STATUS'],[change(text,'card_last4','4321')]))
    with fixture[0].begin() as db:
        parent=db.get(Task,identifier);record=db.get(RuntimeRecord,identifier)
        record.metadata_json={key:value for key,value in record.metadata_json.items() if key!=METADATA_KEY}
        with pytest.raises(DomainError) as error:load_verified_semantic_intent(db,parent,record.metadata_json)
        assert error.value.code=='SEMANTIC_BINDING_NOT_STORED'
