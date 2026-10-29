"""Inspectable once-only effects for the technical report, on disposable data.

The real application, authorization and money code run unchanged. The planner is
deterministic here: these cases measure idempotency, not language-model quality.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from test_access_runtime import named_runtime, login, authorization
from test_transfer_pending_payment_scope import payment_fixture
from test_runtime_v2 import runtime as recovery_runtime, begin as recovery_begin, authorize as recovery_authorize
from test_internal_banking import internal_bank, receive_payee
from zhenxing.models import Account, Transfer, LedgerEntry, Transaction, ToolCall, utcnow
from zhenxing.runtime_models import RuntimeTaskRequest, WorkflowJob
from zhenxing.runtime import run_one_job
from zhenxing.internal_banking import commit_transfer_money, recipient_scope, settlement_integrity


def publish(name, value):
    requested=os.getenv('ZX_IDEMPOTENCY_EVIDENCE_DIR')
    if not requested:return
    root=Path(__file__).resolve().parents[1]
    folder=Path(requested).resolve()
    assert (folder.is_relative_to(root/'var/acceptance/2026-10-23/idempotency-report-v2')
        or folder == root/'var/acceptance/2026-10-24/release-closeout/idempotency-process')
    folder.mkdir(parents=True,exist_ok=True)
    value.update(case=name,measured_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        environment='ISOLATED_SQLITE_APPLICATION_AND_DOMAIN',model='DETERMINISTIC_PLANNER',
        model_calls=0,source='tests/test_idempotency_report_evidence.py',
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (folder/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def bank_snapshot(db,source_id,target_id=None):
    return {'sender_minor':db.get(Account,source_id).balance_minor,
        'receiver_minor':db.get(Account,target_id).balance_minor if target_id else None,
        'transfers':db.scalar(select(func.count()).select_from(Transfer)),
        'ledger_entries':db.scalar(select(func.count()).select_from(LedgerEntry)),
        'cash_statements':db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind=='TRANSFER')),
        'tool_calls':db.scalar(select(func.count()).select_from(ToolCall))}


def test_request_and_confirmation_replay_produce_one_internal_transfer(named_runtime):
    source,target=payment_fixture(named_runtime)
    http,factory,gateway,_=named_runtime
    headers=login(http,'customerone','customer88')
    with factory() as db:before=bank_snapshot(db,source['account_id'],target['account_id'])
    submit_headers={**headers,'Idempotency-Key':'release-same-transfer-12'}
    replies=[http.post('/api/v2/tasks',headers=submit_headers,json={'message':'给wangwei转12.34元，备注聚餐分摊'}) for _ in range(12)]
    assert all(r.status_code==202 for r in replies)
    assert len({r.json()['task_id'] for r in replies})==1
    task_id=replies[0].json()['task_id']
    conflict=http.post('/api/v2/tasks',headers=submit_headers,json={'message':'给wangwei转24.68元，备注聚餐分摊'})
    assert conflict.status_code==409
    assert run_one_job(factory,gateway)
    prepared=http.get('/api/v2/tasks/'+task_id+'/trace',headers=headers).json()
    approval=authorization(http,headers,prepared)
    first=http.post('/api/v2/tasks/'+task_id+'/confirm',headers=headers,json=approval)
    assert first.status_code==202
    duplicates=[http.post('/api/v2/tasks/'+task_id+'/confirm',headers=headers,json=approval).status_code for _ in range(4)]
    assert duplicates==[409]*4
    assert run_one_job(factory,gateway)
    duplicates += [http.post('/api/v2/tasks/'+task_id+'/confirm',headers=headers,json=approval).status_code for _ in range(4)]
    assert duplicates==[409]*8
    with factory() as db:
        after=bank_snapshot(db,source['account_id'],target['account_id'])
        transfer=db.scalar(select(Transfer))
        assert transfer.amount_minor==1234 and settlement_integrity(db,transfer)['balanced']
        assert after['sender_minor']==before['sender_minor']-1234
        assert after['receiver_minor']==before['receiver_minor']+1234
        assert (after['transfers'],after['ledger_entries'],after['cash_statements'],after['tool_calls'])==(1,4,2,1)
    independent=http.post('/api/v2/tasks',headers={**headers,'Idempotency-Key':'release-new-independent-transfer'},json={'message':'给wangwei转12.34元，备注聚餐分摊'})
    assert independent.status_code==202 and independent.json()['task_id']!=task_id
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeTaskRequest))==2
        assert bank_snapshot(db,source['account_id'],target['account_id'])==after
    publish('same-request-and-confirmation',{'request_attempts':12,'unique_task_count':1,'confirmation_attempts':9,
        'accepted_confirmation_count':1,'replayed_confirmation_rejections':8,'payload_conflict_http':409,
        'amount_minor':1234,'currency':'CNY','before':before,'after':after,
        'new_request_key_creates_independent_task':True,'new_independent_task_executed':False,
        'observations':[
            {'label':'同一请求重复提交 12 次','result':'只创建 1 个任务'},
            {'label':'同一确认提交 9 次','result':'1 次接受，8 次 HTTP 409'},
            {'label':'核对账本','result':'1 笔转账、4 条分录、双方各 1 条流水'},
            {'label':'换用新请求键','result':'创建独立待办；没有执行新付款'}]})


def test_lost_response_is_reconciled_instead_of_a_second_debit(recovery_runtime):
    runtime=recovery_runtime
    http,headers,factory,gateway=runtime
    trace=recovery_begin(runtime,'给房东转100元')
    with factory() as db:
        account=db.scalar(select(Account));account_id=account.id;before=bank_snapshot(db,account_id)
    fault=http.post('/api/v2/simulation/fault',headers=headers,json={'mode':'AFTER_COMMIT_RESPONSE_LOST'})
    assert fault.status_code==200
    approval=recovery_authorize(runtime,trace)
    assert http.post('/api/v2/tasks/'+trace['task_id']+'/confirm',headers=headers,json=approval).status_code==202
    assert run_one_job(factory,gateway)
    unknown=http.get('/api/v2/tasks/'+trace['task_id']+'/trace',headers=headers).json()
    assert unknown['state']=='UNKNOWN'
    with factory() as db:committed=bank_snapshot(db,account_id)
    assert committed['sender_minor']==before['sender_minor']-10000
    with factory.begin() as db:
        db.scalar(select(WorkflowJob).where(WorkflowJob.kind=='RECONCILE')).next_run_at=utcnow()
    assert run_one_job(factory,gateway)
    current=http.get('/api/v2/tasks/'+trace['task_id']+'/trace',headers=headers).json()
    assert current['state']=='SUCCEEDED' and current['audit_chain_valid']
    with factory() as db:after=bank_snapshot(db,account_id)
    assert after==committed and after['transfers']==after['tool_calls']==1
    publish('committed-response-loss',{'amount_minor':10000,'currency':'CNY','before':before,
        'after_commit_response_lost':committed,'after_reconcile':after,'states':['AWAITING_CONFIRMATION','UNKNOWN','SUCCEEDED'],
        'second_debit_minor':0,'observations':[
            {'label':'付款已经提交，响应丢失','result':'状态 UNKNOWN，已有 1 笔扣款'},
            {'label':'执行 RECONCILE 查原单','result':'找回原回执，状态 SUCCEEDED'},
            {'label':'再次核对现金与流水','result':'未新增扣款、业务流水或工具执行'}]})


def test_mid_transaction_failure_restores_both_accounts_then_same_key_can_succeed(internal_bank,monkeypatch):
    db,sender,_,source,target,_=internal_bank
    payee=receive_payee(db,sender);db.commit()
    scope=recipient_scope(db,payee);before=bank_snapshot(db,source.id,target.id)
    import zhenxing.internal_banking as banking
    original=banking.record_cash_transaction
    def interrupt(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('interruption after first cash statement')
    monkeypatch.setattr(banking,'record_cash_transaction',interrupt)
    with pytest.raises(RuntimeError):
        commit_transfer_money(db,source,payee,1234,'聚餐分摊','release-rollback-key',scope)
    # Caller catches the failure and commits: the domain savepoint must already
    # have rolled back partial effects without relying on an outer rollback.
    db.commit();after_failure=bank_snapshot(db,source.id,target.id)
    assert after_failure==before
    monkeypatch.setattr(banking,'record_cash_transaction',original)
    first=commit_transfer_money(db,source,payee,1234,'聚餐分摊','release-rollback-key',scope);db.commit()
    duplicate=commit_transfer_money(db,source,payee,1234,'聚餐分摊','release-rollback-key',scope);db.commit()
    assert first.id==duplicate.id
    final=bank_snapshot(db,source.id,target.id)
    assert final['sender_minor']==before['sender_minor']-1234 and final['receiver_minor']==before['receiver_minor']+1234
    assert final['transfers']==1 and final['ledger_entries']==4 and final['cash_statements']==2
    publish('mid-transaction-rollback',{'amount_minor':1234,'currency':'CNY','before':before,
        'after_failure':after_failure,'after_same_key_retry_and_replay':final,'observations':[
            {'label':'第一条现金流水后注入异常','result':'事务保存点撤销双方现金与部分分录'},
            {'label':'外层捕获并提交后检查','result':'双方余额及流水数量与操作前相同'},
            {'label':'故障消除后原键重试并再次回放','result':'最终只完成 1 笔转账'}]})
