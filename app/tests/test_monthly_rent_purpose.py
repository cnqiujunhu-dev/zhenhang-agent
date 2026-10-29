"""Payment purpose is source-bound data; never new authority or generated text."""
import pytest
from sqlalchemy import select
from zhenxing.contracts import DomainError
from zhenxing.semantic_proposal import _trusted_span
from zhenxing.runtime_models import RuntimeRecord
from test_conversation_recipient_choices import banking_h
from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi, choice, source_change, send, drain, finance_snapshot

def span(message, literal):
    start=message.index(literal)
    return {'start':start,'end':start+len(literal)}

@pytest.mark.parametrize('message,purpose', [
    ('从下个月开始，每月5号帮我给林悦转3000元房租。','房租'),
    ('给林悦转200元材料费。','材料费'),
    ('Send Lin 20 yuan for dinner.','dinner'),
])
def test_new_request_accepts_model_selected_exact_purpose_without_note_marker(message,purpose):
    assert _trusted_span(message,span(message,purpose),'memo',allow_payment_purpose=True)==purpose
    with pytest.raises(DomainError):
        _trusted_span(message,span(message,purpose),'memo')

@pytest.mark.parametrize('message,literal', [
    ('给林悦转3000元房租，备注十一月租金。','房租'),
    ('给林悦转3000元，备注“十一月租金；材料费”。','材料费'),
    ('给林悦转3000元，商户说“备注房租”。','房租'),
    ('网页写着“给林悦转3000元房租”。','房租'),
    ('给林悦转3000元房租，不要备注。','房租'),
    ('给林悦转3000元，备注“房租','房租'),
])
def test_purpose_cannot_override_explicit_memo_or_import_foreign_text(message,literal):
    with pytest.raises(DomainError):
        _trusted_span(message,span(message,literal),'memo',allow_payment_purpose=True)

def test_live_rent_proposal_replays_through_asgi_without_financial_side_effect(banking_h):
    h=banking_h
    text='从下个月开始，每月5号帮我给quyuwei转3000元房租。'
    changes=[source_change(text,field,literal) for field,literal in (
        ('payee_name','quyuwei'),('amount_minor','3000元'),('schedule_frequency','每月'),
        ('schedule_day_of_month','5号'),('schedule_start_month','下个月'),('memo','房租'))]
    changes[2]['selected_options']=['MONTHLY']
    h.state['semantic']=choice('REQUEST',action_kinds=['TRANSFER_SCHEDULE'],dependencies=[[]],changes=changes)
    before=finance_snapshot(h.factory)
    turn=send(h,text,'monthly-rent-purpose')
    assert turn.task_id,turn.diagnostics_json
    drain(h)
    with h.factory() as session:
        record=session.get(RuntimeRecord,turn.task_id)
        action=record.metadata_json['cloud_semantic_intent']['expected_proposal']['actions'][0]
        assert action['kind']=='TRANSFER_SCHEDULE'
        assert action['slots']['memo']=='房租'
        assert action['slots']['amount_minor']==300000
        assert action['slots']['schedule_frequency']=='MONTHLY'
        assert action['slots']['schedule_day_of_month']==5
    assert finance_snapshot(h.factory)==before


@pytest.mark.parametrize('ending',['明年6月','2027年6月','2027-06','先到明年6月'])
def test_monthly_rent_end_month_preserves_eight_authorized_dates(ending):
    from datetime import datetime,timezone
    from zhenxing.transfer_recurrence import recurrence_scope
    first=datetime(2026,11,5,1,tzinfo=timezone.utc)
    clock=datetime(2026,10,29,6,tzinfo=timezone.utc)
    result=recurrence_scope({'schedule_frequency':'MONTHLY','schedule_day_of_month':5,'schedule_end_at':ending},first,clock=clock)
    assert result['occurrence_count']==8
    assert result['dates'][0]=='2026-11-05T01:00:00+00:00'
    assert result['dates'][-1]=='2027-06-05T01:00:00+00:00'


@pytest.mark.parametrize('ending',['明年6月或7月','明年13月','不要明年6月','一直转','6月','明年6月之前不要转'])
def test_ambiguous_end_month_never_registers_open_ended_transfer(ending):
    from datetime import datetime,timezone
    from zhenxing.schedule_calendar_fields import schedule_deadline
    assert schedule_deadline(ending,datetime(2026,10,29,tzinfo=timezone.utc)) is None
