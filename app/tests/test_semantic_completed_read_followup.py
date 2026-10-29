"""Completed reads clone into fresh read tasks; never reopen bank operations."""
from copy import deepcopy

import pytest
from sqlalchemy import select,func

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi,choice,send,drain,source_change,finance_snapshot
from test_semantic_router import context
from zhenxing.contracts import DomainError
from zhenxing.conversation_models import ChatThread
from zhenxing.conversation_service import submit_turn,_finish
from zhenxing.runtime import accept_task,task_trace
from zhenxing.runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot
from zhenxing.models import Task,Confirmation
from zhenxing.services import Principal
from zhenxing.semantic_context import semantic_context
from zhenxing.semantic_router import (is_completed_read_followup,prepare_semantic_input,
    validate_semantic_decision,route_semantic_turn,serialize_semantic_receipt)
from zhenxing.semantic_proposal import (build_semantic_intent_binding,load_verified_semantic_intent,
    validate_semantic_source_snapshot,METADATA_KEY)
from zhenxing.routing_models import RoutingPolicy
from zhenxing.planning import canonical_hash


def read_context():
    data=context()
    data['frames'][0].update(state='SUCCEEDED',continuation_allowed=False,write_effect_possible=False,paused=False)
    data['frames'][0]['actions']=[{'action_ref':'a1','kind':'CARD_STATUS',
        'allowed_fields':['card_number','card_last4'],'slots':{}}]
    return data


@pytest.mark.parametrize('mode',['CONTINUE','REVISE'])
@pytest.mark.parametrize('kinds',[[],['CARD_STATUS']])
def test_completed_read_followup_is_model_eligible_but_does_not_claim_mutation_authority(mode,kinds):
    data=read_context();prepared=prepare_semantic_input('看第二张',**data)
    value=choice(mode,focus_ref='f1',action_kinds=kinds)
    assert is_completed_read_followup(value,data)
    assert is_completed_read_followup(value,prepared)
    parsed=validate_semantic_decision(value,prepared)
    assert parsed.mode==mode and parsed.focus_ref=='f1' and parsed.dependencies==[]


@pytest.mark.parametrize('change',[
    {'mode':'CANCEL'},{'mode':'PAUSE'},{'mode':'RESUME'},{'mode':'REQUEST'},
    {'action_kinds':['TRANSFER']},{'dependencies':[[]]},
    {'state':'UNKNOWN'},{'state':'CLOSED'},{'state':'FAILED'},
    {'write_effect_possible':True},{'paused':True},{'kind':'TRANSFER'},
    {'kind':'MIXED'},{'focus_ref':'missing'},
])
def test_completed_read_predicate_cannot_enable_write_mixed_unknown_or_control(change):
    data=read_context();value=choice('REVISE',focus_ref='f1')
    for key,item in change.items():
        if key in {'state','write_effect_possible','paused'}:data['frames'][0][key]=item
        elif key=='kind':
            if item=='MIXED':data['frames'][0]['actions'].append({'action_ref':'a2','kind':'TRANSFER','slots':{},'allowed_fields':[]})
            else:data['frames'][0]['actions'][0]['kind']=item
        else:value[key]=item
    assert is_completed_read_followup(value,data) is False


def test_read_scope_is_explicit_timezone_bound_hint_not_default_hash_change():
    data=read_context()
    data['capabilities'].append({'kind':'BILL_ANALYSIS','allowed_fields':['category_filter']})
    data['frames'][0]['actions']=[{'action_ref':'a1','kind':'BILL_ANALYSIS','slots':{},'allowed_fields':['category_filter']}]
    old=prepare_semantic_input('只看餐饮',**data)
    assert 'read_scope' not in old['frames'][0]
    scope={'read_kind':'BILL_ANALYSIS','period_start':'2026-09-01T00:00:00+08:00','period_end':'2026-10-01T00:00:00+08:00'}
    data['frames'][0]['read_scope']=scope
    new=prepare_semantic_input('只看餐饮',**data)
    assert new['frames'][0]['read_scope']==scope
    assert {key:value for key,value in new['frames'][0].items() if key!='read_scope'}==old['frames'][0]


@pytest.mark.parametrize('scope',[
    {'read_kind':'TRANSFER','period_start':'2026-09-01T00:00:00+08:00','period_end':'2026-10-01T00:00:00+08:00'},
    {'read_kind':'BILL_ANALYSIS','period_start':'2026-09-01','period_end':'2026-10-01'},
    {'read_kind':'BILL_ANALYSIS','period_start':'2026-10-01T00:00:00+08:00','period_end':'2026-09-01T00:00:00+08:00'},
    {'read_kind':'BILL_ANALYSIS','period_start':'2026-09-01T00:00:00+08:00','period_end':'2026-10-01T00:00:00+08:00','amount_minor':12000},
])
def test_malformed_or_bank_amount_read_scope_cannot_enter_model_projection(scope):
    data=read_context();data['capabilities'].append({'kind':'BILL_ANALYSIS','allowed_fields':['category_filter']})
    data['frames'][0]['actions']=[{'action_ref':'a1','kind':'BILL_ANALYSIS','slots':{},'allowed_fields':['category_filter']}]
    data['frames'][0]['read_scope']=scope
    with pytest.raises(ValueError,match='SEMANTIC_INPUT_READ_SCOPE_INVALID'):prepare_semantic_input('看看',**data)


def open_bill(h):
    h.state['semantic']=choice('REQUEST',action_kinds=['BILL_ANALYSIS'],dependencies=[[]])
    row=send(h,'分析2026年9月账单','bill-origin');assert row.task_id
    drain(h)
    with h.factory() as db:
        parent=db.get(Task,row.task_id)
        assert parent.state=='SUCCEEDED'
        return parent.id,deepcopy(parent.plan_json),parent.version


def stage_clone(h,origin_id,message,mode='REVISE',*,clear=False,same_parent=False):
    with h.factory.begin() as db:
        origin=db.get(Task,origin_id);principal=Principal(origin.user_id,origin.sandbox_id)
        thread=db.get(ChatThread,h.thread_id)
        turn=submit_turn(db,principal,thread.id,message=message,client_request_id='clone-'+str(thread.next_sequence))
        offered=semantic_context(db,principal,thread,turn)
        frame=next(row for row in offered['model']['frames'] if offered['frame_bindings'][row['frame_ref']]['task_id']==origin_id)
        changed=source_change(message,'category_filter',message,frame['actions'][0]['action_ref'])
        if clear:changed['operation']='CLEAR'
        else:changed['selected_options']=['餐饮']
        h.state['decision_factory']=None
        h.state['semantic']=choice(mode,focus_ref=frame['frame_ref'],changes=[changed])
        result=route_semantic_turn(h.gateway,policy=RoutingPolicy(mode='CLOUD_ONLY',cloud_enabled=True,
            cloud_model='deepseek-v4-pro',max_http_attempts=1,max_elapsed_seconds=45,version=10),**offered['model'])
        assert result.status=='READY',result.diagnostics
        receipt=serialize_semantic_receipt(result)
        turn.diagnostics_json={**turn.diagnostics_json,'semantic_receipt':receipt}
        parent=origin if same_parent else accept_task(db,principal,message,'new-read-'+turn.id)
        binding=build_semantic_intent_binding(db,principal,thread,turn,result,offered,parent=parent)
        record=db.get(RuntimeRecord,parent.id);record.metadata_json={**record.metadata_json,METADATA_KEY:binding}
        _finish(turn,'NEW_TASK','已开始新查询',parent,'CREATED')
        thread.current_task_id=parent.id
        db.flush();proof=load_verified_semantic_intent(db,parent,record.metadata_json)
        assert proof.expected_proposal.actions[0].kind=='BILL_ANALYSIS'
        return parent.id,deepcopy(binding)


def test_completed_bill_filter_then_clear_are_new_reads_with_original_receipt_and_unchanged_origin(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    first,plan,version=open_bill(h)
    second,binding=stage_clone(h,first,'只看吃饭的开销')
    assert second!=first and binding['decision']['mode']=='REVISE'
    assert binding['read_clone']['version']=='completed-read-followup-v1'
    assert binding['read_clone']['origin_task_id']==first
    assert binding['expected_proposal']['actions'][0]['slots']['category_filter']=='餐饮'
    drain(h)
    third,clear_binding=stage_clone(h,second,'分类不限了，全部账单都看',mode='CONTINUE',clear=True)
    assert third not in {first,second} and clear_binding['decision']['mode']=='CONTINUE'
    assert clear_binding['expected_proposal']['actions'][0]['slots']['category_filter'] is None
    drain(h)
    with h.factory() as db:
        assert db.get(Task,first).plan_json==plan and db.get(Task,first).version==version
        for task_id in (first,second,third):
            parent=db.get(Task,task_id);record=db.get(RuntimeRecord,task_id)
            assert parent.state=='SUCCEEDED'
            snapshot=db.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task_id,PlanSnapshot.version==record.plan_version))
            assert validate_semantic_source_snapshot(db,Principal(parent.user_id,parent.sandbox_id),parent,record.metadata_json,snapshot)
        assert db.scalar(select(func.count()).select_from(Confirmation))==0
    assert finance_snapshot(h.factory)==before


def test_actual_asgi_completed_bill_followup_creates_new_query_and_keeps_original_period(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory);origin,plan,version=open_bill(h)
    def selected(prepared):
        frame=next(row for row in prepared['frames'] if row['state']=='SUCCEEDED'
            and [action['kind'] for action in row['actions']]==['BILL_ANALYSIS'])
        assert frame['read_scope']['read_kind']=='BILL_ANALYSIS'
        assert frame['read_scope']['period_start'].startswith('2026-08-31') or frame['read_scope']['period_start'].startswith('2026-09-01')
        changed=source_change(prepared['message'],'category_filter',prepared['message'],frame['actions'][0]['action_ref'])
        changed['selected_options']=['餐饮']
        return choice('REVISE',focus_ref=frame['frame_ref'],changes=[changed])
    h.state['decision_factory']=selected
    filtered=send(h,'只看吃饭的开销','asgi-filter-followup')
    assert filtered.task_id and filtered.task_id!=origin and filtered.mode=='NEW_TASK',filtered.diagnostics_json
    assert filtered.diagnostics_json['semantic_receipt']['decision']['mode']=='REVISE'
    drain(h)
    def clear(prepared):
        frame=next(row for row in prepared['frames'] if row['state']=='SUCCEEDED'
            and row['actions'][0]['slots'].get('category_filter')=='餐饮')
        changed=source_change(prepared['message'],'category_filter',prepared['message'],frame['actions'][0]['action_ref'])
        changed['operation']='CLEAR'
        return choice('REVISE',focus_ref=frame['frame_ref'],changes=[changed])
    h.state['decision_factory']=clear
    unfiltered=send(h,'分类不限了，全部账单都看','asgi-clear-followup')
    assert unfiltered.task_id and unfiltered.task_id not in {origin,filtered.task_id},unfiltered.diagnostics_json
    drain(h)
    with h.factory() as db:
        old=db.get(Task,origin)
        assert old.plan_json==plan and old.version==version and old.state=='SUCCEEDED'
        from zhenxing.semantic_read_scope import inherited_bill_period
        from zhenxing.owned_user_dialogue import _bill_result_scope
        principal=Principal(old.user_id,old.sandbox_id)
        original_scope=_bill_result_scope(db,principal,old)
        assert original_scope and original_scope['period_start'] and original_scope['period_end']
        expected=None
        for task_id in (filtered.task_id,unfiltered.task_id):
            parent=db.get(Task,task_id);record=db.get(RuntimeRecord,task_id)
            assert parent.state=='SUCCEEDED',record.metadata_json
            scope=inherited_bill_period(db,Principal(parent.user_id,parent.sandbox_id),parent)
            assert scope and scope[0].year==2026 and scope[0].astimezone().month in {8,9}
            if expected is None:expected=scope
            else:assert scope==expected
            actual_scope=_bill_result_scope(db,principal,parent)
            assert actual_scope and (actual_scope['period_start'],actual_scope['period_end']) == (
                original_scope['period_start'],original_scope['period_end'])
        assert db.scalar(select(func.count()).select_from(Confirmation))==0
    assert finance_snapshot(h.factory)==before


def test_followup_cannot_reopen_the_same_completed_read_task(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory);origin,plan,version=open_bill(h)
    with pytest.raises(DomainError) as failure:stage_clone(h,origin,'只看餐饮',same_parent=True)
    assert failure.value.code=='SEMANTIC_TASK_NOT_MUTABLE'
    with h.factory() as db:assert db.get(Task,origin).version==version and db.get(Task,origin).plan_json==plan
    assert finance_snapshot(h.factory)==before


@pytest.mark.parametrize('mutation',['missing','origin','snapshot','plan_hash'])
def test_read_clone_marker_is_mandatory_and_tamper_does_not_bypass_source_proof(cloud_asgi,mutation):
    h=cloud_asgi;origin,_,_=open_bill(h);clone,binding=stage_clone(h,origin,'只看餐饮')
    with h.factory.begin() as db:
        record=db.get(RuntimeRecord,clone);changed=deepcopy(record.metadata_json)
        proof=changed[METADATA_KEY]
        if mutation=='missing':proof.pop('read_clone')
        elif mutation=='origin':proof['read_clone']['origin_task_id']=clone
        elif mutation=='snapshot':proof['read_clone']['origin_snapshot_id']='not-an-origin-snapshot'
        else:proof['read_clone']['origin_plan_hash']='0'*64
        # Even a caller recomputing the public binding hash does not gain a DB
        # source proof or authority to alter the original query.
        proof.pop('binding_hash');proof['binding_hash']=canonical_hash(proof)
        record.metadata_json=changed;db.flush()
        with pytest.raises(DomainError) as failure:load_verified_semantic_intent(db,db.get(Task,clone),changed)
        assert failure.value.code=='SEMANTIC_READ_CLONE_BINDING_INVALID'


@pytest.mark.parametrize('mutation',['origin_state','op_kind','op_status','origin_version','origin_plan_hash'])
def test_db_origin_changes_block_existing_read_clone_even_when_marker_hash_is_unchanged(cloud_asgi,mutation):
    h=cloud_asgi;origin,_,_=open_bill(h);clone,_=stage_clone(h,origin,'只看餐饮')
    with h.factory.begin() as db:
        old=db.get(Task,origin);op=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==origin))
        if mutation=='origin_state':old.state='UNKNOWN'
        elif mutation=='op_kind':op.action_kind='TRANSFER'
        elif mutation=='op_status':op.status='UNKNOWN'
        elif mutation=='origin_version':old.version+=1
        else:old.plan_hash='0'*64
        db.flush()
        with pytest.raises(DomainError):
            load_verified_semantic_intent(db,db.get(Task,clone),db.get(RuntimeRecord,clone).metadata_json)
