"""Finite semantic recovery: stubbed HTTP only, never a live service/model."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from test_semantic_router import Adapter, choice, context, gateway
from zhenxing.planning import canonical_hash
from zhenxing.routing_models import ModelUsage, RoutingPolicy
from zhenxing.semantic_router import (route_semantic_turn, semantic_input_hash,
    is_verified_semantic_result, serialize_semantic_receipt)
from zhenxing.semantic_reunderstanding import (KEY, VERSION, ERROR, attempt_budget, start_ledger,
    record_attempt, reserve_second, retry_available, metric_attempts, verified_feedback)


POLICY=RoutingPolicy(mode='CLOUD_ONLY',cloud_model='deepseek-v4-pro',cloud_enabled=True,
    max_http_attempts=2,max_elapsed_seconds=90)
TEXT='查询我的银行卡。'


class UsageAdapter(Adapter):
    def __init__(self,decision=None,**kw):
        super().__init__(decision,**kw)
        self.proposal_usage=ModelUsage()
        self.requests=[]
    def _post_proposal(self,*args,**kw):
        response=super()._post_proposal(*args,**kw)
        self.requests.append(deepcopy(self.request['payload']))
        return response
    def _capture_response_metadata(self,body):
        usage=body.get('usage') or {}
        self.proposal_usage=ModelUsage(input_tokens=usage.get('prompt_tokens'),
            output_tokens=usage.get('completion_tokens'),total_tokens=usage.get('total_tokens'))


def body(raw,**changes):
    return {'model':'deepseek-v4-pro','usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12},
        'choices':[{'finish_reason':'stop','message':{'content':__import__('json').dumps(raw)}}],**changes}


def setup():
    ctx=context()
    ctx['frames'][0].update(state='SUCCEEDED',continuation_allowed=False,write_effect_possible=True,paused=False)
    invalid=choice('REVISE',focus_ref='f1')
    ledger=start_ledger(POLICY,input_hash=semantic_input_hash(TEXT,**ctx),
        source_hash=canonical_hash({'message':TEXT}),now=datetime.now(timezone.utc))
    return ctx,invalid,ledger,attempt_budget(ledger,POLICY,now=datetime.now(timezone.utc))


def first(**kw):
    ctx,invalid,ledger,budget=setup()
    adapter=UsageAdapter(body=body(invalid),**kw)
    result=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget)
    return ctx,ledger,budget,adapter,result


def test_private_failure_then_new_model_decision_each_has_exactly_one_http_and_same_full_source():
    ctx,ledger,budget,adapter,result=first()
    assert result.status=='MODEL_INVALID' and not is_verified_semantic_result(result)
    assert result.diagnostics['semantic_validation_errors']==[{'code':ERROR,'location':[]}]
    assert result.reunderstanding_feedback
    saved=deepcopy(result.diagnostics)
    ledger=record_attempt(ledger,result)
    assert reserve_second(ledger,POLICY,now=datetime.now(timezone.utc))['reserved_attempts']==2
    adapter.body=body(choice('REQUEST'))
    final=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget,
        _validation_feedback=result.reunderstanding_feedback)
    assert final.status=='READY' and is_verified_semantic_result(final),final.diagnostics
    assert final.decision.mode=='REQUEST' and result.diagnostics==saved  # model, not a REVISE codec
    assert adapter.proposal_http_attempts==2
    assert final.diagnostics['model_api_call_count']==result.diagnostics['model_api_call_count']==1
    assert final.input_hash==result.input_hash and final.source_message_hash==result.source_message_hash
    assert adapter.requests[0]['messages'][1]==adapter.requests[1]['messages'][1]
    assert ERROR not in adapter.requests[0]['messages'][0]['content'].split('schema=')[0]
    assert ERROR in adapter.requests[1]['messages'][0]['content']
    assert final.diagnostics['request_payload_hash']!=result.diagnostics['request_payload_hash']
    assert final.reunderstanding_feedback is None
    receipt=serialize_semantic_receipt(final)
    assert receipt['version']=='unified-cloud-semantics-v1' and receipt['diagnostics']['model_api_call_count']==1
    outer=record_attempt(reserve_second(ledger,POLICY,now=datetime.now(timezone.utc)),final)
    assert outer['model_api_call_count']==2 and outer['usage']=={'input_tokens':20,'output_tokens':4,'total_tokens':24}
    assert metric_attempts(outer)==outer['attempts'] and not retry_available(outer,now=datetime.now(timezone.utc))
    assert 0<adapter.budgets[2]<=adapter.budgets[0]<=45


def test_write_read_reference_contract_gets_its_own_finite_feedback_not_a_server_queryref_deletion():
    ctx,_invalid,ledger,budget=setup()
    raw=choice('REQUEST',action_kinds=['TRANSFER'],dependencies=[[]],query_reference={
        'selection':'INDEPENDENT','source_span':{'start':0,'end':len(TEXT)}})
    before=deepcopy(raw);adapter=UsageAdapter(body=body(raw))
    result=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget)
    assert raw==before and result.status=='MODEL_INVALID' and result.decision is None
    assert result.reunderstanding_feedback.data['code']=='SEMANTIC_QUERY_REFERENCE_READ_ONLY'
    adapter.body=body(choice('REQUEST'))
    final=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget,
        _validation_feedback=result.reunderstanding_feedback)
    assert final.status=='READY' and adapter.proposal_http_attempts==2
    assert final.diagnostics['validated_reunderstanding_feedback']['validation_code']=='SEMANTIC_QUERY_REFERENCE_READ_ONLY'
    assert 'SEMANTIC_QUERY_REFERENCE_READ_ONLY' in adapter.requests[1]['messages'][0]['content']
    assert adapter.requests[0]['messages'][1]==adapter.requests[1]['messages'][1]


@pytest.mark.parametrize('mutation',['plain','seal','data','source','policy','model','used'])
def test_feedback_is_private_bound_and_one_use(mutation):
    ctx,ledger,budget,adapter,result=first();feedback=result.reunderstanding_feedback
    if mutation=='plain':feedback={'version':VERSION,'code':ERROR}
    elif mutation=='seal':feedback=replace(feedback,seal='0'*64)
    elif mutation=='data':feedback=replace(feedback,data={**feedback.data,'code':'OTHER'})
    elif mutation=='source':ctx['history'].append({'role':'user','content':'changed'})
    elif mutation=='policy':pass
    elif mutation=='model':feedback=replace(feedback,data={**feedback.data,'model':'other'})
    elif mutation=='used':
        adapter.body=body(choice('REQUEST'))
        assert route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget,
            _validation_feedback=feedback).status=='READY'
    before=adapter.proposal_http_attempts
    policy=POLICY.model_copy(update={'version':2}) if mutation=='policy' else POLICY
    denied=route_semantic_turn(gateway(adapter),TEXT,policy=policy,**ctx,_attempt_budget=budget,
        _validation_feedback=feedback)
    assert denied.status=='INPUT_INVALID' and adapter.proposal_http_attempts==before


@pytest.mark.parametrize('case',['usage_missing','usage_bool','usage_sum','finish','status','json','code','count_zero','count_two','timeout'])
def test_no_private_permit_for_incomplete_unknown_or_other_failures(case):
    ctx,invalid,ledger,budget=setup();value=body(invalid);args={}
    if case=='usage_missing':value.pop('usage')
    elif case=='usage_bool':value['usage']['prompt_tokens']=True
    elif case=='usage_sum':value['usage']['total_tokens']=99
    elif case=='finish':value['choices'][0]['finish_reason']='length'
    elif case=='json':value['choices'][0]['message']['content']='bad JSON'
    elif case=='code':value=body(choice('REQUEST',action_kinds=['UNKNOWN'],dependencies=[[]]))
    elif case=='count_zero':args['issued']=False
    elif case=='timeout':args['error']=httpx.ReadTimeout('unknown')
    adapter=UsageAdapter(body=value,**args)
    if case=='count_two':
        original=adapter._post_proposal
        def twice(*a,**kw):
            response=original(*a,**kw);adapter.proposal_http_attempts+=1;return response
        adapter._post_proposal=twice
    if case=='status':
        adapter._post_proposal=lambda *a,**kw: httpx.Response(503,request=httpx.Request('POST','https://stub.invalid'))
    result=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget)
    assert result.status!='READY' and result.reunderstanding_feedback is None


def test_failure_diagnostic_mutation_invalidates_permit_and_ledger_tamper_is_not_complete_metric():
    ctx,ledger,budget,adapter,result=first()
    result.diagnostics['usage']['total_tokens']=13
    assert not verified_feedback(result.reunderstanding_feedback,result=result,policy=POLICY,
        input_hash=result.input_hash,source_hash=result.source_message_hash)
    outer=record_attempt(ledger,result);outer['attempts'][0]['diagnostics']['model_api_call_count']=0
    assert metric_attempts(outer) is None


def test_shared_deadline_and_reservation_never_reset_to_another_45_seconds():
    ctx,ledger,budget,adapter,result=first()
    ledger=record_attempt(ledger,result)
    expired=datetime.fromisoformat(ledger['deadline_at'])+timedelta(seconds=1)
    assert reserve_second(ledger,POLICY,now=expired) is None
    expired_budget=attempt_budget(ledger,POLICY,now=expired)
    adapter.body=body(choice())
    denied=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=expired_budget,
        _validation_feedback=result.reunderstanding_feedback)
    assert denied.status=='MODEL_UNAVAILABLE' and adapter.proposal_http_attempts==1
    once=reserve_second(ledger,POLICY,now=datetime.now(timezone.utc))
    assert reserve_second(once,POLICY,now=datetime.now(timezone.utc)) is None
    assert reserve_second(ledger,POLICY.model_copy(update={'max_http_attempts':1}),now=datetime.now(timezone.utc)) is None


def test_old_v1_unparameterized_route_and_receipt_do_not_gain_recovery_fields():
    adapter=UsageAdapter(body=body(choice()))
    result=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**context())
    assert is_verified_semantic_result(result) and result.reunderstanding_feedback is None
    assert 'request_payload_hash' not in result.diagnostics
    assert 'validated_reunderstanding_feedback' not in serialize_semantic_receipt(result)['diagnostics']
    assert adapter.budgets==[45.0,None]


def test_invalid_multi_http_witness_cannot_use_manual_retry_for_a_third_http():
    ctx,ledger,budget,adapter,result=first()
    result=replace(result,diagnostics={**result.diagnostics,'model_api_call_count':2})
    ledger=record_attempt(ledger,result)
    assert not retry_available(ledger,now=datetime.now(timezone.utc))
    assert reserve_second(ledger,POLICY,now=datetime.now(timezone.utc)) is None


def test_private_feedback_also_checks_runtime_attempt_limit_not_only_orchestrator_enable():
    ctx,invalid,ledger,_budget=setup();policy=POLICY.model_copy(update={'max_http_attempts':1})
    ledger=start_ledger(policy,input_hash=semantic_input_hash(TEXT,**ctx),
        source_hash=canonical_hash({'message':TEXT}),now=datetime.now(timezone.utc))
    adapter=UsageAdapter(body=body(invalid))
    result=route_semantic_turn(gateway(adapter),TEXT,policy=policy,**ctx,
        _attempt_budget=attempt_budget(ledger,policy,now=datetime.now(timezone.utc)))
    assert result.status=='MODEL_INVALID' and result.reunderstanding_feedback is None


def test_manual_second_ordinal_does_not_mint_a_new_failure_permit_or_extend_deadline():
    ctx,ledger,budget,adapter,result=first();ledger=record_attempt(ledger,result)
    second=reserve_second(ledger,POLICY,now=datetime.now(timezone.utc))
    new_budget=attempt_budget(second,POLICY,now=datetime.now(timezone.utc))
    result=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=new_budget)
    assert result.status=='MODEL_INVALID' and result.reunderstanding_feedback is None
    second['deadline_at']=(datetime.fromisoformat(second['started_at'])+timedelta(seconds=46)).isoformat()
    assert attempt_budget(second,POLICY,now=datetime.now(timezone.utc)) is None


@pytest.mark.parametrize('case',['two','domain_rejected','reserved_unknown','tamper','old_single','old_manual'])
def test_metrics_count_outer_attempts_not_final_and_manual_copies(case):
    from zhenxing.observation_metrics import _understanding_samples
    ctx,ledger,budget,adapter,result=first();ledger=record_attempt(ledger,result)
    first_diag=deepcopy(result.diagnostics)
    if case in {'two','tamper','domain_rejected'}:
        adapter.body=body(choice())
        final=route_semantic_turn(gateway(adapter),TEXT,policy=POLICY,**ctx,_attempt_budget=budget,
            _validation_feedback=result.reunderstanding_feedback)
        ledger=record_attempt(reserve_second(ledger,POLICY,now=datetime.now(timezone.utc)),final)
        current=final.diagnostics
    else:current=first_diag
    meta={KEY:ledger,'semantic_cloud_reserved':True,'semantic_diagnostics':current,
        'understanding_retry_history':[{'retry_version':0,'model_diagnostics':first_diag,'failure_code':'MODEL_INVALID'}]}
    if case=='reserved_unknown':meta[KEY]=reserve_second(ledger,POLICY,now=datetime.now(timezone.utc))
    elif case=='domain_rejected':meta['semantic_processing_error']='SEMANTIC_QUOTED_SOURCE'
    elif case=='tamper':meta[KEY]['attempts'][0]['diagnostics']['model_api_call_count']=0
    elif case.startswith('old_'):
        meta.pop(KEY)
        if case=='old_single':meta.pop('understanding_retry_history')
    row=SimpleNamespace(status='ANSWERED',diagnostics_json=meta)
    fake=SimpleNamespace(scalar=lambda query:1,scalars=lambda query:[row])
    observations,_sampling=_understanding_samples(fake,10)
    counts=[r['calls'] for r in observations]
    assert counts=={'two':[1,1],'domain_rejected':[1,1],'reserved_unknown':[1,None],'tamper':[None],
        'old_single':[1],'old_manual':[1,1]}[case]
    if case=='domain_rejected':assert all(r['adoption']=='REJECTED' for r in observations)


def test_archived_attempt_hash_survives_existing_acceptance_redaction_without_a_client_change():
    from scripts.feedback_acceptance_client import safe_json
    _ctx,ledger,_budget,_adapter,result=first()
    outer=record_attempt(ledger,result)
    assert safe_json(outer)==outer
    assert metric_attempts(safe_json(outer))==outer['attempts']
    assert outer['attempts'][0]['diagnostics_hash']==canonical_hash(result.diagnostics)
    assert outer['usage_complete'] and outer['model_elapsed_ms']==result.diagnostics['elapsed_ms']
