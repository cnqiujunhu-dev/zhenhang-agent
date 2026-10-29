"""Both confirmation endpoints are visible and bound before normal approval."""
from sqlalchemy import select, func
import pytest

from test_access_runtime import named_runtime, login, begin, authorization
from test_aa_registered_accounts import setup_participants
from zhenxing.access_models import LoginAccount
from zhenxing.banking_extension_models import ScopedScheduledTransfer
from zhenxing.credit_bootstrap import initialize_customer_payment_services
from zhenxing.credit_models import CustomerPaymentDefaults
from zhenxing.models import Account, Card, Transfer
from zhenxing.payment_instruments import resolve_source_instrument, resolve_recipient_instrument
from zhenxing.runtime import run_one_job
from zhenxing.services import Principal


def payment_fixture(runtime):
    setup_participants(runtime)
    with runtime[1].begin() as db:
        sender=db.scalar(select(LoginAccount).where(LoginAccount.username=="customerone"))
        receiver=db.scalar(select(LoginAccount).where(LoginAccount.username=="wangwei"))
        for actor in (sender,receiver): initialize_customer_payment_services(db,actor.sandbox_id,actor.user_id)
        source=resolve_source_instrument(db,Principal(sender.user_id,sender.sandbox_id))
        target=resolve_recipient_instrument(db,receiver.username)
        return source,target


@pytest.mark.parametrize("scheduled",[False,True])
def test_normal_username_default_confirmation_includes_both_actual_card_tails_currency_and_amount(named_runtime,scheduled):
    source,target=payment_fixture(named_runtime)
    http,factory,_,_=named_runtime
    headers=login(http,"customerone","customer88")
    trace=begin(named_runtime,headers,("明天" if scheduled else "")+"给wangwei转100元")
    assert trace["state"]=="AWAITING_CONFIRMATION",trace
    scope=trace["pending_confirmation"]["scope"]
    assert scope["source_card_display_last4"]==source["display_last4"]
    assert scope["recipient_card_display_last4"]==target["display_last4"]
    assert (scope["currency"],scope["amount_minor"])==("CNY",10000)
    assert not set(scope)&{"source_account_id","recipient_account_id","scope_hash","balance_minor","available_balance_minor"}
    with factory() as db: assert db.scalar(select(func.count()).select_from(Transfer))==0


@pytest.mark.parametrize("change",["source_locked","recipient_locked","source_default"])
def test_schedule_card_or_default_change_before_confirmation_cannot_register_future_authority(named_runtime,change):
    source,target=payment_fixture(named_runtime)
    http,factory,gateway,_=named_runtime
    headers=login(http,"customerone","customer88")
    trace=begin(named_runtime,headers,"明天给wangwei转100元")
    approval=authorization(http,headers,trace)
    with factory.begin() as db:
        if change=="source_default":
            defaults=db.scalar(select(CustomerPaymentDefaults).where(CustomerPaymentDefaults.user_id==source["user_id"]))
            defaults.version+=1
        else:
            card=db.get(Card,(source if change=="source_locked" else target)["card_id"])
            card.status,card.version="LOCKED",card.version+1
    changed=http.get(f"/api/v2/tasks/{trace['task_id']}/trace",headers=headers).json()
    assert changed["pending_confirmation"] is None
    accepted=http.post(f"/api/v2/tasks/{trace['task_id']}/confirm",headers=headers,json=approval)
    assert accepted.status_code in {202,409},accepted.text
    if accepted.status_code==202: assert run_one_job(factory,gateway)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ScopedScheduledTransfer))==0
        assert db.scalar(select(func.count()).select_from(Transfer))==0


def test_future_schedule_is_not_a_reservation_and_does_not_require_frozen_current_cash_version(named_runtime):
    source,_=payment_fixture(named_runtime)
    http,factory,gateway,_=named_runtime
    headers=login(http,"customerone","customer88")
    trace=begin(named_runtime,headers,"明天给wangwei转100元")
    approval=authorization(http,headers,trace)
    with factory.begin() as db:
        account=db.get(Account,source["account_id"])
        account.balance_minor=1;account.version+=1
    current=http.get(f"/api/v2/tasks/{trace['task_id']}/trace",headers=headers).json()
    assert current["pending_confirmation"] is not None,current
    accepted=http.post(f"/api/v2/tasks/{trace['task_id']}/confirm",headers=headers,json=approval)
    assert accepted.status_code==202,accepted.text
    assert run_one_job(factory,gateway)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ScopedScheduledTransfer))==1
        assert db.get(Account,source["account_id"]).balance_minor==1
        assert db.scalar(select(func.count()).select_from(Transfer))==0
