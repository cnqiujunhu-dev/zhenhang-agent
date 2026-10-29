"""Real isolated conversation/plan/customer DTO; model HTTP alone is stubbed."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select,func

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi,choice,source_change,send,drain,finance_snapshot
from zhenxing.access_models import LoginAccount
from zhenxing.models import Account,Card,Task,Confirmation,ToolCall
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.services import Principal
from zhenxing.runtime import task_trace
from zhenxing.banking_workflow_confirmation import customer_banking_confirmation
from zhenxing.planning import canonical_hash


@pytest.fixture
def banking_h(cloud_asgi):
    from zhenxing.credit_bootstrap import seed_payment_upgrades_for_usernames
    h=cloud_asgi
    with h.factory.begin() as db:
        seed_payment_upgrades_for_usernames(db,['customerone','quyuwei','test'],adult_usernames=['customerone','quyuwei','test'],
            eligibility_source='ISOLATED_BANKING_CONFIRMATION_ADULT_FIXTURE')
        for card in db.scalars(select(Card)):
            if card.status!='ACTIVE':card.status='ACTIVE';card.version+=1
    return h


def prepared(h,kind,frequency='ONCE'):
    due=(datetime.now(timezone.utc)+timedelta(hours=2)).replace(microsecond=0)
    if frequency=='MONTHLY':due=datetime(2027,1,31,2,0,tzinfo=timezone.utc)
    if kind=='TRANSFER_SCHEDULE':
        date=due.isoformat();text=f'预约在{date}给quyuwei转账0.13元，'+('仅一次。' if frequency=='ONCE' else f'频率{frequency}，共3次。')
        changes=[source_change(text,'scheduled_at',date),source_change(text,'payee_name','quyuwei'),source_change(text,'amount_minor','0.13元')]
        if frequency!='ONCE':
            changes.extend([{**source_change(text,'schedule_frequency',frequency),'selected_options':[frequency]},
                source_change(text,'schedule_occurrence_count','3次')])
    else:
        deadline=(datetime.now(timezone.utc)+timedelta(hours=1)).replace(microsecond=0).isoformat()
        text=f'向quyuwei和test发起AA收款，合计0.05元，截止时间为{deadline}。'
        changes=[source_change(text,'amount_minor','0.05元'),source_change(text,'participant_names','quyuwei和test'),
            source_change(text,'aa_expires_at',deadline)]
    h.state['semantic']=choice('REQUEST',action_kinds=[kind],dependencies=[[]],changes=changes)
    row=send(h,text,'prepared-'+kind+'-'+frequency)
    assert row.task_id,row.diagnostics_json
    drain(h)
    return row,due


def rows(db,parent_id):
    parent=db.get(Task,parent_id)
    operation=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,RuntimeOperation.active==1))
    child=db.get(Task,operation.child_task_id)
    confirmation=db.scalar(select(Confirmation).where(Confirmation.task_id==child.id,Confirmation.status=='PENDING'))
    return parent,operation,child,confirmation


def customer(h,parent_id):
    result=h.http.get('/api/v3/customer/tasks/'+parent_id,headers=h.headers)
    assert result.status_code==200,result.text
    return result.json()


def export_case(label,kind,dto):
    directory=os.environ.get('ZX_EXPORT_BANKING_CONFIRMATION_DTO_DIR')
    if directory:
        out=Path(directory);out.mkdir(parents=True,exist_ok=True)
        (out/(label+'.json')).write_text(json.dumps({'evidence_type':'ISOLATED_API_PROVIDER_STUB_NOT_ACTUAL_PRO',
            'cases':[{'action':kind,'customer_task':dto}]},ensure_ascii=False,indent=2),encoding='utf-8')


def test_schedule_name_is_from_verified_card_owner_without_changing_authorization(banking_h):
    from zhenxing.models import User
    h=banking_h
    row,_=prepared(h,'TRANSFER_SCHEDULE','MONTHLY')
    with h.factory.begin() as db:
        login=db.scalar(select(LoginAccount).where(LoginAccount.username=='quyuwei'))
        db.get(User,login.user_id).display_name='林悦'
        parent,op,child,confirmation=rows(db,row.task_id)
        original=deepcopy(child.plan_json);plan_hash=child.plan_hash
    before=finance_snapshot(h.factory)
    dto=customer(h,row.task_id)
    shown=dto['pending_confirmation']['scope']['domain_confirmation']['recipient']
    assert shown['display_name']=='林悦' and shown['username']=='quyuwei'
    with h.factory() as db:
        _,_,child,_=rows(db,row.task_id)
        assert child.plan_json==original and child.plan_hash==plan_hash
    assert finance_snapshot(h.factory)==before


@pytest.mark.parametrize('frequency',['ONCE','DAILY','MONTHLY'])
def test_actual_schedule_customer_scope_is_complete_and_read_only(banking_h,frequency):
    h=banking_h;row,due=prepared(h,'TRANSFER_SCHEDULE',frequency);before=finance_snapshot(h.factory)
    dto=customer(h,row.task_id);pending=dto['pending_confirmation'];assert pending,dto
    scope=pending['scope']['domain_confirmation']
    assert scope['schema_version']=='customer-banking-confirmation-v1' and scope['action']=='TRANSFER_SCHEDULE' and scope['scope_verified'] is True
    assert scope['amount_minor']==13 and scope['currency']=='CNY' and scope['memo']==''
    assert datetime.fromisoformat(scope['due_at'])==due and scope['timezone']=='Asia/Shanghai' and scope['clock_basis']=='CALENDAR_REAL'
    assert scope['recipient']['username']=='quyuwei' and scope['source']['instrument_type']==scope['recipient']['instrument_type']=='DEBIT'
    count=1 if frequency=='ONCE' else 3
    assert scope['frequency']==frequency and scope['total_occurrences']==count and scope['total_budget_minor']==13*count
    assert scope['funds_reserved'] is False and scope['future_execution_revalidation'] is True
    assert scope['confirmation_expires_at']==pending['expires_at']
    if frequency=='ONCE':assert scope['end_at'] is None and scope['last_due_at']==scope['due_at']
    else:assert scope['end_at']==scope['last_due_at']
    if frequency=='MONTHLY':assert scope['last_due_at'].startswith('2027-03-31T02:00:00')
    with h.factory() as db:
        parent,op,child,confirmation=rows(db,row.task_id)
        assert child.plan_hash==canonical_hash(child.plan_json)
        assert db.scalar(select(func.count()).select_from(ToolCall))==0
        assert not any(key in json.dumps(scope) for key in ('user_id','account_id','card_id','payee_id','scope_hash','sandbox_id'))
        for account_id in db.scalars(select(Account.id)):assert account_id not in json.dumps(scope)
    assert finance_snapshot(h.factory)==before
    export_case('schedule-'+frequency.lower(),'TRANSFER_SCHEDULE',dto)


def test_actual_aa_customer_scope_lists_all_people_cents_and_invitation_boundary(banking_h):
    h=banking_h;row,_=prepared(h,'AA_REQUEST');before=finance_snapshot(h.factory)
    dto=customer(h,row.task_id);pending=dto['pending_confirmation'];assert pending,dto
    scope=pending['scope']['domain_confirmation']
    assert scope['action']=='AA_REQUEST' and scope['scope_verified'] is True and scope['currency']=='CNY'
    assert scope['collector']['username']=='customerone' and scope['collector']['instrument_type']=='DEBIT'
    assert scope['total_minor']==5 and scope['participant_count']==2
    assert [(item['username'],item['amount_minor']) for item in scope['participants']]==[('quyuwei',3),('test',2)]
    assert scope['remainder_rule']=='LIST_ORDER_EXTRA_CENT' and scope['timezone']=='Asia/Shanghai'
    assert scope['invitation_only'] is True and scope['automatic_debit'] is False
    assert datetime.fromisoformat(scope['expires_at'])>datetime.now(timezone.utc)
    assert scope['confirmation_expires_at']==pending['expires_at']
    assert not any(key in json.dumps(scope) for key in ('user_id','account_id','card_id','payee_id','scope_hash','sandbox_id'))
    assert finance_snapshot(h.factory)==before
    export_case('aa-request','AA_REQUEST',dto)


@pytest.mark.parametrize('kind',['TRANSFER_SCHEDULE','AA_REQUEST'])
@pytest.mark.parametrize('mutation',['missing','view_hash','owner','plan_version','card_version'])
def test_missing_tampered_wrong_owner_or_version_never_returns_customer_pending(banking_h,kind,mutation):
    h=banking_h;row,_=prepared(h,kind)
    assert customer(h,row.task_id)['pending_confirmation']
    with h.factory.begin() as db:
        parent,op,child,confirmation=rows(db,row.task_id)
        if mutation=='missing':
            plan=deepcopy(child.plan_json);plan['nodes'][0].pop('due_at' if kind=='TRANSFER_SCHEDULE' else 'participants');child.plan_json=plan
        elif mutation=='view_hash':confirmation.view_hash='0'*64
        elif mutation=='owner':confirmation.user_id=db.scalar(select(LoginAccount).where(LoginAccount.username=='quyuwei')).user_id
        elif mutation=='plan_version':op.plan_version+=1
        else:
            node=child.plan_json['nodes'][0]
            snapshot=node['transfer_payment_scope']['source'] if kind=='TRANSFER_SCHEDULE' else node['aa_collector_scope']
            db.get(Card,snapshot['card_id']).version+=1
    before=finance_snapshot(h.factory);dto=customer(h,row.task_id)
    assert dto['pending_confirmation'] is None and 'confirm' not in dto.get('allowed_actions',[])
    assert finance_snapshot(h.factory)==before


def rehash(child,confirmation,scope):
    plan=deepcopy(child.plan_json);node=plan['nodes'][0]
    for key in list(node):
        if key not in {'node_id','tool_name','read_only','requires_confirmation'}:node.pop(key)
    node.update(scope);child.plan_json=plan;child.plan_hash=canonical_hash(plan)
    confirmation.plan_hash=child.plan_hash;confirmation.view_hash=canonical_hash({'plan_hash':child.plan_hash,'scope':scope})
    fic=deepcopy(child.fic_json)
    for key,value in scope.items():fic['slots'].setdefault(key,{'source':'AUTHORITATIVE'})['value']=value
    child.fic_json=fic


@pytest.mark.parametrize('mutation',['shares','order','expired','unbounded_expiry','hidden_fee'])
def test_rehashed_aa_reordered_people_or_unequal_shares_do_not_replace_the_frozen_selection(banking_h,mutation):
    h=banking_h;row,_=prepared(h,'AA_REQUEST')
    with h.factory.begin() as db:
        parent,op,child,confirmation=rows(db,row.task_id)
        scope={key:deepcopy(value) for key,value in child.plan_json['nodes'][0].items() if key not in {'node_id','tool_name','read_only','requires_confirmation'}}
        if mutation=='shares':scope['participants'][0]['amount_minor']=4;scope['participants'][1]['amount_minor']=1
        elif mutation=='order':
            scope['participants'].reverse();scope['participants'][0]['amount_minor']=3;scope['participants'][1]['amount_minor']=2
        elif mutation=='expired':scope['aa_expires_at']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        elif mutation=='unbounded_expiry':scope['aa_expires_at']=(datetime.now(timezone.utc)+timedelta(days=90)).isoformat()
        else:scope['participants'][0]['fee_minor']=100
        rehash(child,confirmation,scope)
    assert customer(h,row.task_id)['pending_confirmation'] is None


@pytest.mark.parametrize('mutation',['naive_date','timezone','limit','duplicate_dates'])
def test_rehashed_schedule_cannot_supply_a_different_or_unbounded_review(banking_h,mutation):
    h=banking_h;row,_=prepared(h,'TRANSFER_SCHEDULE','DAILY')
    with h.factory.begin() as db:
        parent,op,child,confirmation=rows(db,row.task_id)
        scope={key:deepcopy(value) for key,value in child.plan_json['nodes'][0].items() if key not in {'node_id','tool_name','read_only','requires_confirmation'}}
        if mutation=='naive_date':scope['due_at']=datetime.fromisoformat(scope['due_at']).replace(tzinfo=None).isoformat()
        elif mutation=='timezone':scope['time_zone']='UTC'
        elif mutation=='limit':scope['max_amount_minor']=1
        else:scope['recurrence']['dates'][1]=scope['recurrence']['dates'][0]
        rehash(child,confirmation,scope)
    assert customer(h,row.task_id)['pending_confirmation'] is None


def test_participant_role_change_never_presents_a_usable_aa_invitation_confirmation(banking_h):
    h=banking_h;row,_=prepared(h,'AA_REQUEST')
    with h.factory.begin() as db:
        actor=db.scalar(select(LoginAccount).where(LoginAccount.username=='quyuwei'));actor.role='ADMIN';actor.version+=1
    assert customer(h,row.task_id)['pending_confirmation'] is None


def test_false_result_preview_never_repairs_a_missing_frozen_scope(banking_h):
    h=banking_h;row,_=prepared(h,'AA_REQUEST')
    valid=customer(h,row.task_id)['pending_confirmation']['scope']['domain_confirmation']
    with h.factory.begin() as db:
        parent,op,child,confirmation=rows(db,row.task_id)
        plan=deepcopy(child.plan_json);plan['nodes'][0].pop('participants');child.plan_json=plan
        child.result_json={'preview':valid,'scope_verified':True,'message':'伪造结果正文不应提供确认权限'}
    assert customer(h,row.task_id)['pending_confirmation'] is None


def test_schedule_balance_change_does_not_invalidate_future_identity_bound_authorization(banking_h):
    h=banking_h;row,_=prepared(h,'TRANSFER_SCHEDULE')
    before=customer(h,row.task_id)['pending_confirmation']['scope']['domain_confirmation']
    with h.factory.begin() as db:
        parent,op,child,confirmation=rows(db,row.task_id)
        source=child.plan_json['nodes'][0]['transfer_payment_scope']['source']
        account=db.get(Account,source['account_id']);account.balance_minor+=7;account.version+=1
    after=customer(h,row.task_id)['pending_confirmation']['scope']['domain_confirmation']
    assert after==before
