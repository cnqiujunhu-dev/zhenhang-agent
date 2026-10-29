"""Owned result -> new subscription preview -> same-task amendments, SQLite only.

No cloud/model network, no confirmation consumption and no banking writes.
"""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select,func

from test_conversation_service import chat,plan,turn
from test_dialogue_references import no_network
from test_dialogue_reference_integration import stage
from test_dialogue_reference_runtime_integration import send,business,proposal
from zhenxing.conversation_models import ChatThread
from zhenxing.dialogue_reference_integration import METADATA_KEY,PRIVATE_KEY,load_verified_result_reference,ground_result_reference_proposal
from zhenxing.intelligence import ActionSlots,IntentProposal,ProposedAction
from zhenxing.model_gateway import ModelGateway
from zhenxing.models import Subscription,Task,Confirmation,Account
from zhenxing.runtime_models import RuntimeRecord


@pytest.fixture
def commerce_chat(chat):
    with chat[0].begin() as db:
        row=db.scalar(select(Subscription).where(Subscription.user_id==chat[2].user_id,Subscription.merchant=='视频会员'))
        assert row is not None
        # These tests exercise buying a new period. Disabling renewal alone
        # does not end paid access; explicitly create an expired historical
        # fixture instead of expecting a still-paid membership to charge again.
        from zhenxing.simulation import business_now
        from zhenxing.subscription_commerce import add_calendar_months
        from zhenxing.models import utcnow
        end=min(business_now(db,chat[2].sandbox_id),utcnow())-timedelta(days=1)
        row.status='CANCELLED';row.auto_renew=False;row.version+=1
        row.entitlement_end_at=end;row.next_due_at=end;row.last_paid_at=add_calendar_months(end,-1)
    return chat


@pytest.mark.parametrize('message,kind,fields',[
    ('恢复第一个会员','SUBSCRIPTION_START',{}),
    ('恢复第一个会员三个月，不自动续费','SUBSCRIPTION_START',{'subscription_months':3,'subscription_auto_renew':False}),
    ('续订第一个会员两个月','SUBSCRIPTION_RENEW',{'subscription_months':2}),
])
def test_verified_query_selection_builds_current_purchase_genesis_not_old_cancellation(commerce_chat,message,kind,fields):
    chat=commerce_chat;before=business(chat[0]);source=plan(chat,'查询视频会员订阅')
    binding,identifier=stage(chat,message)
    assert binding['mode']=='WRITE_PROPOSAL'
    assert binding['expected_actions']==[{'kind':kind,'slots':{'merchant':'视频会员',**fields},'depends_on':[]}]
    assert binding['write_authorized'] is False
    assert binding['catalog']['selected_task_id']==source['task_id']
    with chat[0]() as db:
        task=db.get(Task,identifier);proof=load_verified_result_reference(db,task,db.get(RuntimeRecord,identifier).metadata_json)
        supplied=IntentProposal(actions=[ProposedAction(kind=kind,slots=ActionSlots(**fields))])
        grounded=ground_result_reference_proposal(supplied,message,{PRIVATE_KEY:proof})
        assert grounded.actions==proof.expected_proposal.actions
        assert not db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.consumed_at.is_not(None)))
    assert business(chat[0])==before


def test_source_merchant_cannot_be_changed_or_price_borrowed_by_model(commerce_chat):
    chat=commerce_chat;plan(chat,'查询视频会员订阅');binding,identifier=stage(chat,'恢复第一个会员')
    with chat[0]() as db:
        proof=load_verified_result_reference(db,db.get(Task,identifier),db.get(RuntimeRecord,identifier).metadata_json)
        for values in ({'merchant':'云存储'},{'merchant':'视频会员','amount_minor':2500},{'subscription_auto_renew':True}):
            proposed=IntentProposal(actions=[ProposedAction(kind='SUBSCRIPTION_START',slots=ActionSlots(**values))])
            assert not ground_result_reference_proposal(proposed,'恢复第一个会员',{PRIVATE_KEY:proof}).actions


@pytest.mark.parametrize('initial,kind',[
    ('订阅视频会员','SUBSCRIPTION_START'),('续订视频会员','SUBSCRIPTION_RENEW'),
])
def test_actual_chat_amends_same_task_months_false_switch_and_invalidates_every_old_confirmation(commerce_chat,initial,kind):
    chat=commerce_chat;before=business(chat[0]);first=plan(chat,initial)
    assert first['state']=='AWAITING_CONFIRMATION',first
    identifier=first['task_id'];old_ids=[first['pending_confirmation']['confirmation_id']]
    for text,months,auto in [('开一个月',1,None),('三个月',3,None),('不自动续费',3,False),('改两个月',2,False)]:
        row,trace,_=send(chat,text)
        assert row.mode=='REVISE' and row.task_id==identifier,(row.mode,row.answer)
        assert trace['state']=='AWAITING_CONFIRMATION',trace
        slots=proposal(chat[0],identifier)['actions'][0]['slots']
        assert slots['merchant']=='视频会员' and slots['subscription_months']==months
        assert slots.get('subscription_auto_renew') is auto
        assert proposal(chat[0],identifier)['actions'][0]['kind']==kind
        fresh=trace['pending_confirmation']['confirmation_id'];assert fresh not in old_ids
        with chat[0]() as db:
            assert all(db.get(Confirmation,previous).status=='CANCELLED' and db.get(Confirmation,previous).consumed_at is None for previous in old_ids)
        old_ids.append(fresh)
    assert business(chat[0])==before


def test_stub_provider_query_then_restore_uses_one_explicit_new_model_proposal_and_no_rule_fallback(commerce_chat):
    chat=commerce_chat;plan(chat,'查询视频会员订阅');before=business(chat[0]);calls=[]
    class Provider:
        def propose(self,message,context=None):
            calls.append(message)
            return IntentProposal(actions=[ProposedAction(kind='SUBSCRIPTION_START',slots=ActionSlots(
                subscription_months=3,subscription_auto_renew=False))])
    changed=(chat[0],ModelGateway(Provider()),chat[2],chat[3])
    row,trace,_=send(changed,'恢复这个会员三个月，不自动续费')
    assert len(calls)==1 and trace['state']=='AWAITING_CONFIRMATION',(calls,row.mode,row.answer,trace)
    data=proposal(chat[0],row.task_id)
    assert data['fallback'] is False and data['actions'][0]['kind']=='SUBSCRIPTION_START'
    assert data['actions'][0]['slots']['merchant']=='视频会员'
    assert business(chat[0])==before


@pytest.mark.parametrize('text',[
    '开通视频会员13个月','开通视频会员1.5个月','免费开通视频会员','美元买视频会员',
    '开通视频会员每月10元','明天开通视频会员','下周恢复视频会员','开通腾讯视频会员',
])
def test_actual_chat_unsupported_price_time_duration_and_unknown_brand_never_defaults_to_paid_scope(commerce_chat,text):
    chat=commerce_chat;before=business(chat[0]);row=turn(chat,text)
    if row.task_id:
        from zhenxing.runtime import run_one_job
        assert run_one_job(chat[0],chat[1])
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Confirmation))==0,(text,row.mode,row.answer)
    assert business(chat[0])==before


def test_actual_half_year_scope_is_server_priced_six_months_not_default_one(commerce_chat):
    chat=commerce_chat;before=business(chat[0]);trace=plan(chat,'开通视频会员半年')
    assert trace['state']=='AWAITING_CONFIRMATION',trace
    assert proposal(chat[0],trace['task_id'])['actions'][0]['slots']['subscription_months']==6
    with chat[0]() as db:
        confirmation=db.get(Confirmation,trace['pending_confirmation']['confirmation_id'])
        child=db.get(Task,confirmation.task_id)
        commerce=next(node for node in child.plan_json['nodes'] if node.get('tool_name')=='subscription.start.v1')
        assert commerce['months']==6 and commerce['monthly_price_minor']==2500 and commerce['total_amount_minor']==15000
        assert commerce['auto_renew'] is False
    assert business(chat[0])==before


def test_existing_active_start_is_a_read_only_no_double_charge_result(chat):
    # This case requires currently paid rights, not merely an ACTIVE renewal
    # record whose seeded early-October entitlement has already expired.
    from zhenxing.models import utcnow
    with chat[0].begin() as db:
        row = db.scalar(select(Subscription).where(Subscription.user_id == chat[2].user_id,
            Subscription.merchant == '视频会员'))
        row.status = 'ACTIVE'; row.entitlement_end_at = utcnow() + timedelta(days=30)
        row.next_due_at = row.entitlement_end_at; row.version += 1
    before=business(chat[0]);trace=plan(chat,'订阅视频会员')
    assert trace['state']=='SUCCEEDED',trace
    with chat[0]() as db:
        assert db.scalar(select(func.count()).select_from(Confirmation))==0
    assert business(chat[0])==before


def test_active_renewal_with_expired_rights_still_requires_fresh_purchase_confirmation(chat):
    from zhenxing.models import utcnow
    with chat[0].begin() as db:
        row = db.scalar(select(Subscription).where(Subscription.user_id == chat[2].user_id,
            Subscription.merchant == '视频会员'))
        row.status = 'ACTIVE'; row.entitlement_end_at = utcnow() - timedelta(days=1)
        row.next_due_at = row.entitlement_end_at; row.version += 1
    before = business(chat[0])
    trace = plan(chat, '订阅视频会员一个月，不自动续费')
    assert trace['state'] == 'AWAITING_CONFIRMATION' and trace['pending_confirmation'], trace
    assert business(chat[0]) == before


@pytest.mark.parametrize('message,initial',[
    ('先查询银行卡，再开通视频会员3个月','CARD_STATUS'),
    ('先分析账单，再续订视频会员2个月','BILL_ANALYSIS'),
])
def test_actual_chat_composite_read_and_subscription_keep_source_order_and_dag_without_charge(commerce_chat,message,initial):
    chat=commerce_chat;before=business(chat[0]);trace=plan(chat,message)
    assert trace['state']=='AWAITING_CONFIRMATION',trace
    data=proposal(chat[0],trace['task_id'])
    assert len(data['actions'])==2 and data['actions'][0]['kind']==initial
    assert data['actions'][1]['kind'] in {'SUBSCRIPTION_START','SUBSCRIPTION_RENEW'}
    assert [action['depends_on'] for action in data['actions']]==[[],[0]]
    assert business(chat[0])==before
