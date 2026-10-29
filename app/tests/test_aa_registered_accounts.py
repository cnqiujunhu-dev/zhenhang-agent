"""AA accepts exact registered participants through the existing bank directory."""
from copy import deepcopy
import pytest
from sqlalchemy import select,func
from test_access_runtime import named_runtime,login,begin,authorization
from zhenxing.accounts import create_customer_account
from zhenxing.access_models import LoginAccount
from zhenxing.models import Account,Confirmation,Transfer,Payee,Task
from zhenxing.runtime import run_one_job
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.banking_extension_models import AACollectionRequest,AAParticipant


def setup_participants(runtime):
    with runtime[1].begin() as db:
        create_customer_account(db,"wangwei","wangwei88",seed=45)
        create_customer_account(db,"zhanghao","zhanghao88",seed=46)


def test_exact_bank_usernames_prepare_and_confirm_collection_not_payment(named_runtime):
    setup_participants(named_runtime)
    http,factory,gateway,_=named_runtime
    headers=login(http,"customerone","customer88")
    with factory() as db:
        before={row.id:(row.balance_minor,row.version) for row in db.scalars(select(Account))}
    trace=begin(named_runtime,headers,"AA总额300元，参与者：wangwei、zhanghao")
    assert trace["state"]=="AWAITING_CONFIRMATION",trace
    body=authorization(http,headers,trace)
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm",headers=headers,json=body).status_code==202
    assert run_one_job(factory,gateway)
    assert http.get(f"/api/v2/tasks/{trace['task_id']}/trace",headers=headers).json()["state"]=="SUCCEEDED"
    with factory() as db:
        assert {row.id:(row.balance_minor,row.version) for row in db.scalars(select(Account))}==before
        request=db.scalar(select(AACollectionRequest))
        assert request.total_minor==30000 and request.participant_count==2 and request.status=="COLLECTING"
        parts=list(db.scalars(select(AAParticipant).order_by(AAParticipant.ordinal)))
        assert [(row.display_name,row.amount_minor,row.status) for row in parts]==[("wangwei",15000,"PENDING"),("zhanghao",15000,"PENDING")]
        assert db.scalar(select(func.count()).select_from(Transfer))==0


def test_disabled_participant_after_preview_cannot_create_collection(named_runtime):
    setup_participants(named_runtime)
    http,factory,gateway,_=named_runtime
    headers=login(http,"customerone","customer88")
    trace=begin(named_runtime,headers,"AA总额300元，参与者：wangwei、zhanghao")
    assert trace["state"]=="AWAITING_CONFIRMATION"
    body=authorization(http,headers,trace)
    with factory.begin() as db:
        recipient=db.scalar(select(LoginAccount).where(LoginAccount.username=="wangwei"))
        recipient.status="DISABLED";recipient.version+=1
    accepted=http.post(f"/api/v2/tasks/{trace['task_id']}/confirm",headers=headers,json=body)
    if accepted.status_code==202:
        assert run_one_job(factory,gateway)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AACollectionRequest))==0
        assert db.scalar(select(func.count()).select_from(Transfer))==0
        assert db.get(Confirmation,body["confirmation_id"]).consumed_at is None


@pytest.mark.parametrize("participant",["admin","customerone","notregistered"])
def test_admin_self_and_unknown_participants_are_not_silently_external(named_runtime,participant):
    setup_participants(named_runtime)
    headers=login(named_runtime[0],"customerone","customer88")
    trace=begin(named_runtime,headers,f"AA总额300元，参与者：{participant}、zhanghao")
    assert trace["state"]=="NEED_CLARIFICATION",trace
    assert trace.get("pending_confirmation") is None
