"""Conversation selection, verified confirmation and bank replacement.

Only model transport is deterministic; all API, source, OTP and database gates
are the real application paths in an isolated SQLite database.
"""
from sqlalchemy import select
import json
from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi, choice, source_change, send, drain
from test_banking_workflow_confirmation_asgi import banking_h, customer
from test_transfer_statement_card_scope import prepare_payment, confirm
from test_transfer_statement_card_scope import explicit_endpoints
from zhenxing.banking_extension_models import ScopedScheduledTransfer
import pytest


def test_query_then_change_amount_keeps_same_cards_and_requires_fresh_confirmation(banking_h):
    h=banking_h
    before,scope,source,target=prepare_payment(h,'TRANSFER_SCHEDULE','DAILY')
    assert confirm(h,before)['state']=='WAITING'
    h.state['semantic']=choice('REQUEST',action_kinds=['TRANSFER_SCHEDULE_VIEW'],dependencies=[[]])
    queried=send(h,'查看我目前的定时转账','schedule-list-for-change');drain(h)
    assert queried.task_id
    text='把这份预约以后每次改成0.16元，其他不要动'
    def decision(prepared):
        entities=[row for row in prepared['entities'] if row.get('field')=='schedule_reference']
        assert len(entities)==1,prepared
        entity=entities[0]
        return choice('REQUEST',action_kinds=['TRANSFER_SCHEDULE'],dependencies=[[]],entity_refs=[entity['entity_ref']],changes=[
            {**source_change(text,'schedule_reference','这份预约'),'entity_ref':entity['entity_ref']},
            source_change(text,'amount_minor','0.16元')])
    h.state['decision_factory']=decision
    updated=send(h,text,'schedule-new-amount');drain(h)
    assert updated.task_id,json.dumps(updated.diagnostics_json,ensure_ascii=True)
    dto=customer(h,updated.task_id)
    assert dto['pending_confirmation'],dto
    domain=dto['pending_confirmation']['scope']['domain_confirmation']
    assert domain['amount_minor']==16 and domain['replaces_future_occurrences'] is True
    assert domain['source']['display_last4']==scope['source']['display_last4']
    assert domain['recipient']['display_last4']==scope['recipient']['display_last4']
    with h.factory() as db:
        assert list(db.scalars(select(ScopedScheduledTransfer.status)))==['WAITING']
    trace=h.http.get('/api/v2/tasks/'+updated.task_id+'/trace',headers=h.headers).json()
    finished=confirm(h,trace)
    assert finished['state']=='WAITING',finished
    with h.factory() as db:
        rows=list(db.scalars(select(ScopedScheduledTransfer).order_by(ScopedScheduledTransfer.created_at)))
        assert [(row.status,row.amount_minor) for row in rows]==[('CANCELLED',13),('WAITING',16)]
        assert {row.source_account_id for row in rows}=={source['account_id']}


def test_monthly_natural_separate_year_month_and_day_produces_full_confirmation(banking_h):
    h=banking_h;source,target=explicit_endpoints(h)
    message=f"从我的{source['number']}给quyuwei的{target['number']}安排每月31日转0.13元，从2027年1月开始，共3次，备注房租。"
    h.state['semantic']=choice('REQUEST',action_kinds=['TRANSFER_SCHEDULE'],dependencies=[[]],changes=[
        source_change(message,'card_number',source['number']),source_change(message,'payee_name','quyuwei'),
        source_change(message,'recipient_card_reference',target['number']),source_change(message,'amount_minor','0.13元'),
        {**source_change(message,'schedule_frequency','每月31日'),'selected_options':['MONTHLY']},
        source_change(message,'schedule_start_month','2027年1月'),source_change(message,'schedule_day_of_month','31日'),
        source_change(message,'schedule_occurrence_count','3次'),source_change(message,'memo','房租')])
    turn=send(h,message,'monthly-separate-calendar-fields');drain(h)
    assert turn.task_id,json.dumps(turn.diagnostics_json,ensure_ascii=True)
    dto=customer(h,turn.task_id);assert dto['pending_confirmation'],dto
    scope=dto['pending_confirmation']['scope']['domain_confirmation']
    assert scope['frequency']=='MONTHLY' and scope['total_occurrences']==3
    assert scope['due_at']=='2027-01-31T01:00:00+00:00' and scope['last_due_at']=='2027-03-31T01:00:00+00:00'
    assert scope['default_execution_time'] is True


@pytest.mark.parametrize('already_posted',[False,True])
def test_natural_cancel_registered_schedule_keeps_completed_occurrences(banking_h,already_posted):
    from datetime import datetime
    from zhenxing.banking_extensions import execute_due_schedules
    from zhenxing.banking_extension_models import ScheduledTransferOccurrence
    from zhenxing.models import Transfer
    from sqlalchemy import func
    h=banking_h;original,_,_,_=prepare_payment(h,'TRANSFER_SCHEDULE','DAILY');confirm(h,original)
    if already_posted:
        with h.factory.begin() as db:
            scheduled=db.scalar(select(ScopedScheduledTransfer))
            assert execute_due_schedules(db,calendar_time=scheduled.due_at)==1
    message='取消修改后的这份预约，后面都不用转了'
    def decision(prepared):
        frames=[f for f in prepared['frames'] if f['state']=='WAITING' and [a['kind'] for a in f['actions']]==['TRANSFER_SCHEDULE']]
        assert len(frames)==1
        return choice('CANCEL',focus_ref=frames[0]['frame_ref'])
    h.state['decision_factory']=decision
    turn=send(h,message,'cancel-remaining');drain(h)
    assert turn.mode=='FEEDBACK',json.dumps(turn.diagnostics_json,ensure_ascii=True)
    assert turn.diagnostics_json['registered_schedule_future_cancel'] is True
    assert turn.diagnostics_json['cancelled_future_occurrences']==(2 if already_posted else 3)
    with h.factory() as db:
        assert db.scalar(select(ScopedScheduledTransfer.status))=='CANCELLED'
        occurrences=list(db.scalars(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal)))
        assert [row.status for row in occurrences]==(['POSTED','CANCELLED','CANCELLED'] if already_posted else ['CANCELLED']*3)
        assert db.scalar(select(func.count()).select_from(Transfer))==int(already_posted)


def test_registered_cancel_offer_never_allows_revise_unknown_running_or_mixed_actions():
    from zhenxing.semantic_schedule_cancel_contract import offered_schedule_cancel
    from copy import deepcopy
    frame={'frame_ref':'f0','state':'WAITING','write_effect_possible':False,'paused':False,'actions':[{'kind':'TRANSFER_SCHEDULE'}]}
    decision={'mode':'CANCEL','focus_ref':'f0','action_kinds':[],'changes':[],'dependencies':[]}
    assert offered_schedule_cancel(decision,{'frames':[frame]})
    for state in ['UNKNOWN','EXECUTING','SUCCEEDED','CLOSED']:
        assert not offered_schedule_cancel(decision,{'frames':[{**frame,'state':state}]})
    assert not offered_schedule_cancel({**decision,'mode':'REVISE'},{'frames':[frame]})
    assert not offered_schedule_cancel(decision,{'frames':[{**frame,'write_effect_possible':True}]})
    assert not offered_schedule_cancel(decision,{'frames':[{**frame,'actions':[{'kind':'TRANSFER_SCHEDULE'},{'kind':'TRANSFER'}]}]})
