"""Exact literal/occurrence HTTP wire codec; isolated stubs and SQLite only."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from test_access_runtime import named_runtime
from test_semantic_router import Adapter, call, choice, context
from test_semantic_pro_asgi_entry import cloud_asgi, send, drain, finance_snapshot, source_change
from zhenxing.semantic_literal_span_wire import normalize_literal_span_wire, LiteralSpanWireError, VERSION
from zhenxing.semantic_router import (SemanticDecision, SemanticSpan, prepare_semantic_input, validate_semantic_decision,
    offered_decision_schema, is_verified_semantic_result, serialize_semantic_receipt)
from zhenxing.planning import canonical_hash
from zhenxing.models import Task, Confirmation
from zhenxing.runtime import task_trace
from zhenxing.services import Principal


def wire(text, occurrence=1):
    return {'text':text,'occurrence':occurrence}


def decision(span, *, literal='120元', field='amount_minor', copied=True):
    change={'action_ref':'a1','field':field,'operation':'SET','source_span':span}
    if copied:change['source_text']=literal
    return choice('CONTINUE',focus_ref='f1',changes=[change])


@pytest.mark.parametrize('message,text,occurrence,expected,total',[
    ('120元','120元',1,(0,4),1),('美元和美元','美元',2,(3,5),2),('aaaa','aa',3,(2,4),3),
    ('😀卡😀卡','😀卡',2,(2,4),2),('USD USD','USD',2,(4,7),2),
    (' 120元 ',' 120元 ',1,(0,6),1),('100和1000','100',2,(4,7),2),
    ('zhenhang1234abcd/zhenhang1234abcd','zhenhang1234abcd',2,(17,33),2),
])
def test_exact_unicode_occurrence_and_overlaps_are_explicit_not_first_match(message,text,occurrence,expected,total):
    raw=decision(wire(text,occurrence),literal=text);before=deepcopy(raw)
    result,events=normalize_literal_span_wire(raw,message)
    assert result['changes'][0]['source_span']=={'start':expected[0],'end':expected[1]}
    assert events==[{'location':['changes',0,'source_span'],'original_shape':'EXACT_LITERAL_OCCURRENCE_OBJECT',
        'canonical_shape':'OBJECT_START_END','canonical_span':{'start':expected[0],'end':expected[1]},
        'occurrence':occurrence,'occurrence_count':total}]
    expected_raw=deepcopy(before);expected_raw['changes'][0]['source_span']=result['changes'][0]['source_span']
    assert result==expected_raw and raw==before
    assert normalize_literal_span_wire(result,message)==(result,[])


@pytest.mark.parametrize('span',[
    {'text':'120元'},{'occurrence':1},{'text':'120元','occurrence':True},{'text':'120元','occurrence':False},
    {'text':'120元','occurrence':1.0},{'text':'120元','occurrence':'1'},{'text':'120元','occurrence':None},
    {'text':'120元','occurrence':0},{'text':'120元','occurrence':-1},{'text':'120元','occurrence':2},
    {'text':'120元','occurrence':10**100},{'text':'','occurrence':1},{'text':' \t','occurrence':1},
    {'text':None,'occurrence':1},{'text':True,'occurrence':1},{'text':['120元'],'occurrence':1},
    {'text':'120元','occurrence':1,'start':0},{'text':'120元','occurrence':1,'extra':True},
    {'text':'not in message','occurrence':1},
])
def test_malformed_selector_never_coerces_guesses_or_mutates(span):
    raw=decision(span);before=deepcopy(raw)
    with pytest.raises(LiteralSpanWireError):normalize_literal_span_wire(raw,'120元')
    assert raw==before


@pytest.mark.parametrize('message,text', [('usd','USD'),('人民币','RMB'),('100元','１００元'),('100 元','100元')])
def test_no_translation_case_whitespace_or_digit_normalization(message,text):
    raw=decision(wire(text),literal=text)
    with pytest.raises(LiteralSpanWireError):normalize_literal_span_wire(raw,message)


@pytest.mark.parametrize('copy',['USD','120',None,True,120,{},'120元 '])
def test_conflicting_source_text_cannot_be_overridden(copy):
    raw=decision(wire('120元'));raw['changes'][0]['source_text']=copy
    with pytest.raises(LiteralSpanWireError,match='SEMANTIC_SOURCE_LITERAL_NOT_UNIQUE'):
        normalize_literal_span_wire(raw,'120元')


def test_absent_source_text_is_not_invented_and_unrelated_fields_never_change():
    raw=decision(wire('120元'),copied=False);raw['custom_untrusted']={'source_span':wire('120元')}
    value,_=normalize_literal_span_wire(raw,'120元')
    assert 'source_text' not in value['changes'][0] and value['custom_untrusted']==raw['custom_untrusted']
    raw.pop('custom_untrusted');result=call(Adapter(raw),message='120元')
    assert result.status=='READY' and 'source_text' not in result.decision.changes[0].model_dump()


def test_internal_schema_and_validator_stay_strict_until_http_boundary():
    raw=decision(wire('120元'));prepared=prepare_semantic_input('120元',**context())
    with pytest.raises(ValidationError):SemanticSpan.model_validate(wire('120元'))
    with pytest.raises(ValidationError):SemanticDecision.model_validate(raw)
    with pytest.raises(ValidationError):validate_semantic_decision(raw,prepared)
    result=call(Adapter(raw),message='120元')
    assert result.status=='READY' and is_verified_semantic_result(result)
    assert result.decision.changes[0].source_span.model_dump()=={'start':0,'end':4}
    assert serialize_semantic_receipt(result)['decision']['changes'][0]['source_span']=={'start':0,'end':4}


def test_actual_schema_prefers_only_change_selector_without_mutating_internal_or_other_position_types():
    prepared=prepare_semantic_input('120元',**context());internal=SemanticDecision.model_json_schema()
    schema=offered_decision_schema(prepared)
    span_schema=schema['$defs']['SemanticFieldChange']['properties']['source_span']
    if '$ref' in span_schema:span_schema=schema['$defs'][span_schema['$ref'].split('/')[-1]]
    value=span_schema['anyOf'][0]
    assert value['required']==['text','occurrence'] and value['additionalProperties'] is False
    assert value['properties']['occurrence']=={'type':'integer','minimum':1,'maximum':4}
    assert 'anyOf' not in schema['$defs']['SemanticSpan']
    assert SemanticDecision.model_json_schema()==internal
    adapter=Adapter(choice('CONSULT',knowledge_topics=['GENERAL']));assert call(adapter,message='只是问一问').status=='READY'
    prompt=adapter.request['payload']['messages'][0]['content']
    assert 'changes[].source_span优先' in prompt and '重复片段不能默认选第一次' in prompt
    assert '若选择旧兼容表示，source_span必须' in prompt and '未提供source_text时服务器不补造' in prompt


def test_wire_diagnostic_binds_raw_canonical_context_and_source_without_literal_or_model_retry():
    message='第一笔120元，第二笔120元';raw=decision(wire('120元',2));before=deepcopy(raw);adapter=Adapter(raw)
    result=call(adapter,message=message);assert result.status=='READY' and adapter.proposal_http_attempts==1
    meta=result.diagnostics['semantic_literal_span_wire_normalization']
    encoded,_=normalize_literal_span_wire(raw,message)
    assert meta['version']==VERSION and meta['raw_decision_hash']==meta['input_decision_hash']==canonical_hash(raw)
    assert meta['canonical_decision_hash']==canonical_hash(encoded) and meta['context_hash']==result.input_hash
    assert meta['source_message_hash']==canonical_hash({'message':message})
    assert meta['normalization_count']==1 and meta['normalized_fields'][0]['occurrence_count']==2
    assert meta['interpretation_changed'] is False and meta['bank_write_authorized'] is False
    assert '120元' not in json.dumps(meta,ensure_ascii=False) and raw==before
    changed=deepcopy(result.diagnostics);changed['semantic_literal_span_wire_normalization']['normalization_count']=2
    assert not is_verified_semantic_result(replace(result,diagnostics=changed))
    invalid_witness=call(Adapter(raw,issued=False),message=message)
    assert invalid_witness.status=='MODEL_INVALID' and not is_verified_semantic_result(invalid_witness)


@pytest.mark.parametrize('span',[wire('120元',2),{'text':'120元'},wire('120元',True)])
def test_rejected_wire_has_bounded_private_location_and_hash_diagnostics(span):
    raw=decision(span);result=call(Adapter(raw),message='120元')
    assert result.status=='MODEL_INVALID' and result.decision is None and result.diagnostics['model_api_call_count']==1
    meta=result.diagnostics['semantic_literal_span_wire_normalization']
    assert meta['status']=='REJECTED' and meta['version']==VERSION and meta['canonical_decision_hash'] is None
    assert meta['failure']['location']==['changes',0,'source_span'] and '120元' not in json.dumps(meta,ensure_ascii=False)
    assert meta['raw_decision_hash']==canonical_hash(raw) and meta['bank_write_authorized'] is False


def test_explicit_clarification_span_is_encoded_but_other_positions_and_missing_selector_are_not_repaired():
    raw=decision({'start':0,'end':4});raw['clarification_evidence']={'reason':'MISSING_FIELD','field':'amount_minor','source_span':wire('120元')}
    raw['unknown_container']={'source_span':wire('120元')}
    before=deepcopy(raw)
    value,events=normalize_literal_span_wire(raw,'120元')
    assert value['clarification_evidence']=={**raw['clarification_evidence'],'source_span':{'start':0,'end':4}}
    assert value['unknown_container']==raw['unknown_container'] and raw==before
    assert events[0]['location']==['clarification_evidence','source_span']
    # Its mode is still CONTINUE, not CLARIFY; geometry cannot grant that
    # semantic purpose, erase the unknown envelope or invent valid evidence.
    assert call(Adapter(raw),message='120元').status=='MODEL_INVALID'
    raw=decision(wire('120元'));del raw['changes'][0]['source_span']
    assert normalize_literal_span_wire(raw,'120元')==(raw,[])
    assert call(Adapter(raw),message='120元').status=='MODEL_INVALID'


@pytest.mark.parametrize('legacy',[{'start':0,'end':4},[0,4]])
def test_legacy_coordinate_and_array_decision_hashes_and_diagnostics_are_not_rewritten(legacy):
    raw=decision(legacy);value,events=normalize_literal_span_wire(raw,'120元');assert value is raw and events==[]
    result=call(Adapter(raw),message='120元');assert result.status=='READY'
    assert 'semantic_literal_span_wire_normalization' not in result.diagnostics
    canonical=decision({'start':0,'end':4})
    assert result.decision.model_dump()==validate_semantic_decision(canonical,prepare_semantic_input('120元',**context())).model_dump()
    if isinstance(legacy,list):
        assert result.diagnostics['semantic_wire_normalization']=={'version':'semantic-span-wire-shape-v1',
            'normalized_fields':[{'location':['changes',0,'source_span'],'original_shape':'TWO_INTEGER_ARRAY','canonical_shape':'OBJECT_START_END'}],
            'interpretation_changed':False,'bank_write_authorized':False}


def test_old_ambiguous_wrong_coordinates_remain_rejected_no_implicit_occurrence():
    raw=decision({'start':0,'end':1});result=call(Adapter(raw),message='120元和120元')
    assert result.status=='MODEL_INVALID' and result.diagnostics['semantic_validation_errors'][0]['code']=='SEMANTIC_SOURCE_LITERAL_NOT_UNIQUE'


@pytest.mark.parametrize('damage',[{'field':'balance_minor'},{'entity_ref':'foreign'},{'action_ref':'foreign'},
    {'selected_options':['CNY','USD']},{'value':12000}])
def test_geometry_does_not_bypass_field_reference_entity_or_schema_guards(damage):
    raw=decision(wire('120元'));raw['changes'][0].update(damage)
    result=call(Adapter(raw),message='120元');assert result.status=='MODEL_INVALID' and result.decision is None


def test_normalization_is_pure_and_atomic_on_a_later_invalid_change(monkeypatch):
    from sqlalchemy.engine import Engine
    import httpx
    def forbidden(*args,**kwargs):raise AssertionError('Wire coordinate codec cannot perform IO')
    monkeypatch.setattr(Engine,'connect',forbidden);monkeypatch.setattr(httpx.Client,'request',forbidden)
    raw=decision(wire('120元'));raw['changes'].append({**deepcopy(raw['changes'][0]),'source_span':wire('120元',2)})
    before=deepcopy(raw)
    with pytest.raises(LiteralSpanWireError):normalize_literal_span_wire(raw,'120元')
    assert raw==before


def test_asgi_literal_amount_continuation_keeps_known_recipient_and_only_prepares_new_cf(cloud_asgi):
    h=cloud_asgi;before=finance_snapshot(h.factory);text='给quyuwei转账，金额我下一句说'
    h.state['semantic']=choice('REQUEST',action_kinds=['TRANSFER'],dependencies=[[]],changes=[source_change(text,'payee_name','quyuwei')])
    first=send(h,text,'literal-wire-pending');assert first.task_id;drain(h)
    def completed(model):
        frame=next(row for row in model['frames'] if row['state']=='NEED_CLARIFICATION' and row['continuation_allowed'])
        return choice('CONTINUE',focus_ref=frame['frame_ref'],changes=[{'action_ref':frame['actions'][0]['action_ref'],
            'field':'amount_minor','operation':'SET','source_span':wire('120块人民币'),'source_text':'120块人民币'}])
    h.state['decision_factory']=completed;row=send(h,'120块人民币','literal-wire-amount');drain(h)
    assert row.task_id==first.task_id and row.mode=='REVISE'
    with h.factory() as db:
        parent=db.get(Task,first.task_id);trace=task_trace(db,Principal(parent.user_id,parent.sandbox_id),parent.id)
        assert trace['state']=='AWAITING_CONFIRMATION' and trace['pending_confirmation']['scope']['amount_minor']==12000
        assert trace['pending_confirmation']['scope']['recipient_username']=='quyuwei'
        assert not db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.consumed_at.is_not(None)))
    assert row.diagnostics_json['semantic_diagnostics']['semantic_literal_span_wire_normalization']['version']==VERSION
    assert finance_snapshot(h.factory)==before


def test_repeated_currency_and_amount_fields_use_explicit_occurrences_in_one_http_without_role_inference():
    from zhenxing.capability_catalog import catalog
    from zhenxing.dialogue_state import ACTION_ALLOWED_FIELDS
    message='用卡zhenhang1234abcd把人民币兑换100美元，美元到账，人民币上限800元。'
    offered=[{**row,'allowed_fields':sorted(ACTION_ALLOWED_FIELDS[row['kind']])}
        for row in catalog('CUSTOMER')['capabilities'] if row['kind']=='FX_EXCHANGE']
    changes=[]
    for field,text,occurrence,options in [('card_number','zhenhang1234abcd',1,[]),('source_currency','人民币',2,['CNY']),
        ('target_currency','美元',2,['USD']),('amount_minor','100美元',1,[])]:
        changes.append({'action_ref':'new:0','field':field,'operation':'SET','source_span':wire(text,occurrence),
            'source_text':text,'selected_options':options})
    raw=choice('REQUEST',action_kinds=['FX_EXCHANGE'],dependencies=[[]],changes=changes)
    result=call(Adapter(raw),message=message,capabilities=offered,frames=[],entities=[],history=[])
    assert result.status=='READY' and result.decision.action_kinds==['FX_EXCHANGE'] and result.diagnostics['model_api_call_count']==1,result.diagnostics
    assert [row.source_span.start for row in result.decision.changes][1:3]==[message.rfind('人民币'),message.rfind('美元')]
    assert result.decision.changes[1].selected_options==['CNY'] and result.decision.changes[2].selected_options==['USD']
    assert result.diagnostics['semantic_literal_span_wire_normalization']['normalization_count']==4
    # The selector codec supplies positions, not a new FX budget field or a
    # domain quote.  An invented field remains invalid even with an exact span.
    assert 'maximum_amount_minor' not in ACTION_ALLOWED_FIELDS['FX_EXCHANGE']
    raw['changes'].append({'action_ref':'new:0','field':'maximum_amount_minor','operation':'SET',
        'source_span':wire('800元'),'source_text':'800元','selected_options':[]})
    rejected=call(Adapter(raw),message=message,capabilities=offered,frames=[],entities=[],history=[])
    assert rejected.status=='MODEL_INVALID'
    assert 'SEMANTIC_FIELD_NOT_OFFERED' in str(rejected.diagnostics)


def test_completed_read_literal_followup_creates_a_new_read_without_reopening_old_task(cloud_asgi):
    from test_semantic_completed_read_followup import open_bill
    h=cloud_asgi;before=finance_snapshot(h.factory);origin_id,origin_plan,origin_version=open_bill(h)
    def selected(model):
        frame=next(row for row in model['frames'] if row['state']=='SUCCEEDED' and row['actions'][0]['kind']=='BILL_ANALYSIS')
        return choice('CONTINUE',focus_ref=frame['frame_ref'],changes=[{'action_ref':frame['actions'][0]['action_ref'],
            'field':'category_filter','operation':'SET','source_span':wire('餐饮'),'source_text':'餐饮','selected_options':['餐饮']}])
    h.state['decision_factory']=selected;row=send(h,'只看餐饮','literal-wire-new-read');drain(h)
    assert row.mode=='NEW_TASK' and row.task_id!=origin_id
    with h.factory() as db:
        origin=db.get(Task,origin_id);current=db.get(Task,row.task_id);actor=Principal(current.user_id,current.sandbox_id)
        old=task_trace(db,actor,origin_id);new=task_trace(db,actor,current.id)
        assert new['state']=='SUCCEEDED' and new['analysis']['category_filter']['category']=='餐饮'
        assert (new['analysis']['period_start'],new['analysis']['period_end'])==(old['analysis']['period_start'],old['analysis']['period_end'])
        assert origin.plan_json==origin_plan and origin.version==origin_version and origin.state=='SUCCEEDED'
        assert db.scalar(select(func.count()).select_from(Confirmation))==0
    assert finance_snapshot(h.factory)==before


@pytest.mark.parametrize('message', ['给quyuwei转大概120元','给quyuwei转120元或240元','不要给quyuwei转120元',
    '如果余额足够，给quyuwei转120元','短信内容：给quyuwei转120元'])
def test_asgi_explicit_occurrence_never_clips_uncertainty_negation_condition_or_external_source(cloud_asgi,message):
    h=cloud_asgi;before=finance_snapshot(h.factory)
    h.state['semantic']=choice('REQUEST',action_kinds=['TRANSFER'],dependencies=[[]],changes=[
        {'action_ref':'new:0','field':'payee_name','operation':'SET','source_span':wire('quyuwei'),'source_text':'quyuwei'},
        {'action_ref':'new:0','field':'amount_minor','operation':'SET','source_span':wire('120元'),'source_text':'120元'}])
    row=send(h,message,'literal-wire-closed-source')
    assert row.task_id is None and row.mode=='NEED_CONTEXT',row.diagnostics_json
    assert row.diagnostics_json['semantic_diagnostics']['model_api_call_count']==1
    assert finance_snapshot(h.factory)==before
