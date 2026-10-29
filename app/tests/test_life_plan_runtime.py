"""Real isolated SQL/payment facade only. No HTTP, model, or main DB access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from zhenxing.access_models import LoginAccount
from zhenxing.authentication import create_challenge, verify_challenge
from zhenxing.contracts import DomainError
from zhenxing.credit_models import PaymentCashReservation, PaymentRecord, PaymentRefund
from zhenxing.database import Base, create_database
from zhenxing.extensions_models import FamilyMember
from zhenxing.life_plan_migration import TABLES, ensure_life_plan_schema, initialize_life_plan_services
from zhenxing.life_plan_models import LifePlan, LifePlanJob, LifePlanMandate, LifePlanOccurrence
from zhenxing.life_plan_runtime import TOOL_DEFINITIONS, commit_life_plan_task, prepare_life_plan_task
from zhenxing.life_plan_service import (amend_life_plan_draft, authorization_args, authorize_life_plan,
    cancel_life_plan, create_life_plan_draft, life_plan_view, pause_life_plan)
from zhenxing.life_plan_worker import run_one_due_life_plan_job
from zhenxing.merchant_models import MerchantOrder, MerchantProduct, MerchantRefund
from zhenxing.merchant_service import merchant_catalog, orders_view, place_authorized_order
from zhenxing.models import Account, Card, Confirmation, Sandbox, Task, ToolCall, Transaction, User
from zhenxing.occasion_service import put_family_occasion
from zhenxing.schema import ensure_schema


CLOCK = datetime(2026, 10, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def h(tmp_path, monkeypatch):
    # Shared registry wiring belongs to root; test the real owned contracts.
    from zhenxing.platform import TOOL_REGISTRY
    from zhenxing.occasion_runtime import TOOL_DEFINITIONS as OCCASION_TOOLS
    from zhenxing.merchant_runtime import TOOL_DEFINITIONS as MERCHANT_TOOLS
    for key, value in {**TOOL_DEFINITIONS, **OCCASION_TOOLS, **MERCHANT_TOOLS}.items():
        monkeypatch.setitem(TOOL_REGISTRY, key, value)
    for module in ("life_plan_service", "life_plan_runtime", "merchant_service", "merchant_runtime",
            "occasion_runtime", "payment_instruments", "authentication", "credit_service"):
        monkeypatch.setattr(f"zhenxing.{module}.utcnow", lambda: CLOCK)
    engine, factory = create_database("sqlite:///" + (tmp_path / "isolated-life.db").as_posix())
    ensure_schema(engine); ensure_life_plan_schema(engine)
    with factory.begin() as session:
        sandbox = Sandbox(); session.add(sandbox); session.flush()
        user = User(sandbox_id=sandbox.id, display_name="本人")
        child = User(sandbox_id=sandbox.id, display_name="成年子女")
        session.add_all([user, child]); session.flush()
        session.add(LoginAccount(username="isolated-parent", password_hash="unused", user_id=user.id,
            sandbox_id=sandbox.id, role="CUSTOMER", status="ACTIVE"))
        account = Account(sandbox_id=sandbox.id, user_id=user.id, name="生日现金账户", balance_minor=200000, currency="CNY")
        other = Account(sandbox_id=sandbox.id, user_id=user.id, name="其他现金账户", balance_minor=50000, currency="CNY")
        session.add_all([account, other]); session.flush()
        card = Card(sandbox_id=sandbox.id, user_id=user.id, account_id=account.id, last4="1234", status="ACTIVE", limit_minor=1000000)
        session.add(card)
        member = FamilyMember(sandbox_id=sandbox.id, owner_user_id=user.id, helper_user_id=child.id, display_name="成年子女")
        session.add(member); session.flush()
        principal = SimpleNamespace(user_id=user.id, sandbox_id=sandbox.id, family_context=None)
        occasion = put_family_occasion(session, principal, family_member_id=member.id, month=11, day=20,
            adult_subject_confirmed=True, source_reference="explicit-adult-birthday")
        initialize_life_plan_services(session, sandbox.id)
        catalog = merchant_catalog(session, principal)["products"]
        items = [{"merchant_id": row["merchant_id"], "product_id": row["product_id"], "quantity": 1}
            for row in catalog if row["category"] in {"FLOWER", "CAKE"}]
        yield_data = SimpleNamespace(engine=engine, factory=factory, principal=principal, account_id=account.id,
            other_account_id=other.id, card_id=card.id, member_id=member.id,
            occasion_id=occasion["occasions"][0]["occasion_id"], items=items)
    yield yield_data
    engine.dispose()


def draft(h, **changes):
    args = dict(occasion_id=h.occasion_id, year=2026, account_id=h.account_id, card_reference="debit:" + h.card_id,
        budget_minor=100000, items=h.items, recipient={"name": "成年子女", "phone": "local-test-only", "address": "本人确认的测试地址"},
        delivery_start="2026-11-20T10:00:00+08:00", delivery_end="2026-11-20T12:00:00+08:00",
        request_key="birthday-request", now=CLOCK)
    args.update(changes)
    with h.factory.begin() as session:
        return create_life_plan_draft(session, h.principal, **args)["plans"][0]


def explicit_dialogue_items(session, h):
    """Fixture-only declaration: retain the two chosen IDs, use fresh catalog names."""
    selected_ids = {item["product_id"] for item in h.items}
    chosen = [row for row in merchant_catalog(session, h.principal)["products"] if row["product_id"] in selected_ids]
    assert len(chosen) == len(selected_ids) == 2
    return "、".join(row["product_name"] for row in chosen)


def authorization_task(session, h, plan_id, *, verify=True):
    task = Task(sandbox_id=h.principal.sandbox_id, user_id=h.principal.user_id, message="授权本次有界生日计划",
        intent_type="LIFE_PLANNING", state="NEED_CLARIFICATION", model_label="isolated-server-test", fic_json={})
    session.add(task); session.flush()
    prepare_life_plan_task(session, task, {"kind": "LIFE_PLAN_AUTHORIZE", "slots": {"life_plan_id": plan_id}})
    session.flush()
    confirmation = session.scalar(select(Confirmation).where(Confirmation.task_id == task.id))
    challenge = create_challenge(session, task=task, confirmation=confirmation, user_id=h.principal.user_id)
    if verify:
        assert verify_challenge(session, challenge_id=challenge["challenge_id"], user_id=h.principal.user_id,
            sandbox_id=h.principal.sandbox_id, code=challenge["demo_code"])["verified"]
    return task, confirmation, challenge["challenge_id"]


def authorize(h, plan_id):
    with h.factory.begin() as session:
        task, confirmation, challenge_id = authorization_task(session, h, plan_id)
        commit_life_plan_task(session, task, h.principal, confirmation, "authorize:" + task.id, auth_challenge_id=challenge_id)
        return task.id


def tick(h, now, *, limit=30):
    total = 0
    for _ in range(limit):
        with h.factory.begin() as session:
            count = run_one_due_life_plan_job(session, now=now)
        if not count: break
        total += count
    return total


RESERVE = datetime(2026, 11, 1, 0, tzinfo=timezone.utc)
ORDER = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
DELIVER = datetime(2026, 11, 20, 2, tzinfo=timezone.utc)


def test_long_plan_survives_original_dialogue_ttl_and_restarts(h):
    plan = draft(h); origin = authorize(h, plan["plan_id"])
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentCashReservation)) == 0
        assert session.get(Task, origin).state == "SUCCEEDED"
    assert tick(h, RESERVE) == 1
    with h.factory.begin() as session:
        row = session.get(LifePlan, plan["plan_id"])
        assert row.reserved_minor == 100000 and row.reservation_id
        assert session.get(Account, h.account_id).balance_minor == 200000
        assert session.get(Card, h.card_id).status == "ACTIVE"
    # New session per node models a restart; no old in-memory timer is used.
    assert tick(h, ORDER) == 2
    with h.factory.begin() as session:
        orders = session.scalars(select(MerchantOrder)).all()
        assert len(orders) == 2 and all(row.status == "PAID" for row in orders)
        assert session.get(Account, h.account_id).balance_minor == 100200
        assert session.get(Account, h.other_account_id).balance_minor == 50000
        assert session.scalar(select(func.count()).select_from(PaymentRecord)) == 2
    assert tick(h, DELIVER) == 2
    assert tick(h, DELIVER) == 0
    with h.factory.begin() as session:
        row = session.get(LifePlan, plan["plan_id"])
        assert row.status == "SUCCEEDED" and row.reserved_minor == 0 and row.spent_minor == 99800
        assert all(order.status == "FULFILLED" for order in session.scalars(select(MerchantOrder)))
        assert session.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.status == "CONSUMED")) == 1
        assert session.scalar(select(func.count()).select_from(PaymentRecord)) == 2


def test_draft_has_no_funds_effect_and_request_key_cannot_change_scope(h):
    value = draft(h)
    assert draft(h)["plan_id"] == value["plan_id"]
    with pytest.raises(DomainError): draft(h, recipient={"name": "另一人", "phone": "x", "address": "y"})
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentCashReservation)) == 0
        assert session.scalar(select(func.count()).select_from(LifePlanMandate)) == 0
        assert session.get(Account, h.account_id).balance_minor == 200000


def test_compiled_postgresql_owner_lock_precedes_plan_and_cash_not_a_concurrency_claim(h):
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql
    plan=draft(h)
    with h.factory.begin() as session:
        task,confirmation,challenge=authorization_task(session,h,plan["plan_id"])
        statements=[]
        def capture(state):
            if getattr(state.statement,"_for_update_arg",None) is not None:
                statements.append(str(state.statement.compile(dialect=postgresql.dialect())))
        event.listen(session,"do_orm_execute",capture)
        commit_life_plan_task(session,task,h.principal,confirmation,"owner-first:"+task.id,auth_challenge_id=challenge)
        event.remove(session,"do_orm_execute",capture)
        first=lambda table:next(index for index,sql in enumerate(statements) if "FROM "+table+" " in sql)
        assert first("users")<first("life_plans")<first("accounts")<first("cards")
    # This only asserts emitted PostgreSQL SQL ordering; SQLite did not prove
    # row-lock blocking, worker races, or absence of PostgreSQL deadlocks.


def test_durable_worker_emits_owner_lock_before_plan_lock(h):
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql
    plan=draft(h);authorize(h,plan["plan_id"])
    with h.factory.begin() as session:
        statements=[]
        def capture(state):
            if getattr(state.statement,"_for_update_arg",None) is not None:
                statements.append(str(state.statement.compile(dialect=postgresql.dialect())))
        event.listen(session,"do_orm_execute",capture)
        assert run_one_due_life_plan_job(session,now=RESERVE)==1
        event.remove(session,"do_orm_execute",capture)
        first=lambda table:next(index for index,sql in enumerate(statements) if "FROM "+table+" " in sql)
        assert first("users")<first("life_plans")<first("accounts")


def test_locked_plan_refreshes_a_preloaded_stale_session_identity(h):
    from zhenxing.life_plan_service import owned_plan
    plan=draft(h)
    with h.factory() as waiting:
        cached=waiting.get(LifePlan,plan["plan_id"])
        with h.factory.begin() as changed:
            current=changed.get(LifePlan,plan["plan_id"])
            current.status="PAUSED_RECONFIRM";current.version+=1
        assert cached.status=="DRAFT"
        refreshed=owned_plan(waiting,h.principal,plan["plan_id"],lock=True)
        assert refreshed is cached and refreshed.status=="PAUSED_RECONFIRM" and refreshed.version==2


def test_locked_catalog_refreshes_stock_not_a_preloaded_cached_quote(h):
    from zhenxing.merchant_service import quote_products
    with h.factory() as waiting:
        cached=waiting.get(MerchantProduct,h.items[0]["product_id"])
        assert cached.stock>0
        with h.factory.begin() as changed:
            product=changed.get(MerchantProduct,cached.id);product.stock=0;product.version+=1
        with pytest.raises(DomainError) as caught:quote_products(waiting,h.principal,[h.items[0]],lock=True)
        assert caught.value.code=="MERCHANT_STOCK_INSUFFICIENT"


def test_multi_sku_lock_order_is_canonical_but_quote_presentation_order_is_preserved(h):
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql
    from zhenxing.merchant_service import quote_products
    with h.factory.begin() as session:
        statements=[]
        def capture(state):
            if getattr(state.statement,"_for_update_arg",None) is not None:
                statements.append(str(state.statement.compile(dialect=postgresql.dialect())))
        event.listen(session,"do_orm_execute",capture)
        items=list(reversed(h.items))
        quoted=quote_products(session,h.principal,items,lock=True)
        event.remove(session,"do_orm_execute",capture)
        assert [row["product_id"] for row in quoted]==[row["product_id"] for row in items]
        assert len(statements)==1 and "ORDER BY virtual_merchants.id, merchant_products.id" in statements[0]


def test_refund_restock_locks_and_refreshes_shared_sku_before_increment(h):
    from zhenxing.merchant_service import request_order_refund,complete_order_refund
    _,order_id=_purchase(h,"debit:"+h.card_id)
    with h.factory() as waiting:
        order=waiting.get(MerchantOrder,order_id)
        cached=waiting.get(MerchantProduct,order.product_id)
        before=cached.stock
        with h.factory.begin() as changed:
            product=changed.get(MerchantProduct,cached.id);product.stock-=2;product.version+=1
        assert cached.stock==before
        refund=request_order_refund(waiting,h.principal,order_id,now=CLOCK)
        complete_order_refund(waiting,h.principal,refund.id,now=CLOCK)
        assert cached.stock==before-2+order.quantity
        waiting.commit()


def test_no_automatic_authorization_from_pending_challenge(h):
    plan = draft(h)
    with h.factory.begin() as session:
        task, confirmation, challenge = authorization_task(session, h, plan["plan_id"], verify=False)
        with pytest.raises(DomainError) as caught:
            with session.begin_nested():
                commit_life_plan_task(session, task, h.principal, confirmation, "bad-auth", auth_challenge_id=challenge)
        assert caught.value.code == "STEP_UP_REQUIRED"
        assert session.scalar(select(func.count()).select_from(LifePlanMandate)) == 0
        assert confirmation.status == "PENDING"


@pytest.mark.parametrize("mutation", ["recipient", "scope_hash", "occasion", "card", "confirmation", "task", "receipt"])
def test_changed_scope_source_or_payment_control_pauses_before_debit(h, mutation):
    plan = draft(h); task_id = authorize(h, plan["plan_id"])
    with h.factory.begin() as session:
        row = session.get(LifePlan, plan["plan_id"])
        if mutation == "recipient":
            scope=deepcopy(row.scope_json); scope["recipient"]["address"]="未授权新地址"; row.scope_json=scope
        elif mutation == "scope_hash": row.scope_hash="0"*64
        elif mutation == "occasion":
            from zhenxing.occasion_models import FamilyOccasion
            session.get(FamilyOccasion,h.occasion_id).version+=1
        elif mutation == "card": session.get(Card,h.card_id).status="LOCKED"
        elif mutation == "confirmation":
            session.scalar(select(Confirmation).where(Confirmation.task_id==task_id)).status="CANCELLED"
        elif mutation == "task": session.get(Task,task_id).plan_hash="1"*64
        else: session.scalar(select(ToolCall).where(ToolCall.task_id==task_id)).result_json={"plans":[]}
    tick(h,RESERVE)
    with h.factory.begin() as session:
        assert session.get(LifePlan,plan["plan_id"]).status=="PAUSED_RECONFIRM"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0
        assert session.get(Account,h.account_id).balance_minor==200000


def test_price_increase_even_inside_total_budget_needs_reconfirmation(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE)
    with h.factory.begin() as session:
        product=session.get(MerchantProduct,h.items[0]["product_id"]);product.price_minor+=100;product.version+=1
    tick(h,ORDER)
    with h.factory.begin() as session:
        assert session.get(LifePlan,plan["plan_id"]).status=="PAUSED_RECONFIRM"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0
        assert session.scalar(select(func.count()).select_from(MerchantOrder))==0


def test_ordinary_cash_version_change_does_not_grant_a_different_account(h):
    plan=draft(h);authorize(h,plan["plan_id"])
    with h.factory.begin() as session:
        account=session.get(Account,h.account_id);account.balance_minor-=100;account.version+=1
    tick(h,RESERVE);tick(h,ORDER)
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==2
        assert session.get(Account,h.account_id).balance_minor==100100
        assert session.get(Account,h.other_account_id).balance_minor==50000


def test_cancel_before_order_releases_only_reservation_and_never_locks_card(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE)
    with h.factory.begin() as session:
        cancel_life_plan(session,h.principal,plan["plan_id"],expected_version=1,now=ORDER)
    assert tick(h,ORDER)==0
    with h.factory.begin() as session:
        assert session.get(LifePlan,plan["plan_id"]).status=="CANCELLED"
        assert session.get(Account,h.account_id).balance_minor==200000
        assert session.get(Card,h.card_id).status=="ACTIVE"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0
        assert session.scalar(select(PaymentCashReservation)).remaining_minor==0


def test_later_order_failure_does_not_roll_back_first_paid_order(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE)
    assert tick(h,ORDER,limit=1)==1
    with h.factory.begin() as session:
        order=session.scalar(select(MerchantOrder)); first_amount=order.amount_minor
        second=[x for x in h.items if x["product_id"]!=order.product_id][0]
        session.get(MerchantProduct,second["product_id"]).stock=0
    assert tick(h,ORDER,limit=1)==1
    with h.factory.begin() as session:
        assert session.get(LifePlan,plan["plan_id"]).status=="PARTIAL"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==1
        assert session.get(Account,h.account_id).balance_minor==200000-first_amount
        assert session.scalar(select(MerchantOrder)).status=="PAID"
        cancel_life_plan(session,h.principal,plan["plan_id"],expected_version=1,now=ORDER)
    assert tick(h,ORDER)==1
    with h.factory.begin() as session:
        assert session.get(LifePlan,plan["plan_id"]).status=="COMPENSATED"
        assert session.get(Account,h.account_id).balance_minor==200000
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==1
        assert session.scalar(select(func.count()).select_from(PaymentRefund))==1
        assert session.scalar(select(MerchantOrder)).status=="REFUNDED"
    assert tick(h,ORDER)==0


def test_paid_order_replay_is_single_shot(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE);tick(h,ORDER,limit=1)
    with h.factory.begin() as session:
        occurrence=session.scalar(select(LifePlanOccurrence))
        first=place_authorized_order(session,h.principal,occurrence.id,0,now=ORDER)
        second=place_authorized_order(session,h.principal,occurrence.id,0,now=ORDER)
        assert first["order_id"]==second["order_id"]
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==1


def test_january_first_cannot_silently_reserve_in_december(h):
    from zhenxing.occasion_models import FamilyOccasion
    with h.factory.begin() as session:
        row=session.get(FamilyOccasion,h.occasion_id);row.month,row.day=1,1
    with pytest.raises(DomainError) as caught:
        draft(h,year=2027,delivery_start="2027-01-01T10:00:00+08:00",delivery_end="2027-01-01T12:00:00+08:00")
    assert caught.value.code=="LIFE_PLAN_TIME_CONFLICT"


def _task(session, h, message):
    task = Task(sandbox_id=h.principal.sandbox_id, user_id=h.principal.user_id, message=message,
        intent_type="LIFE_PLANNING", state="NEED_CLARIFICATION", model_label="typed-adapter-test", fic_json={})
    session.add(task); session.flush()
    return task


def _challenge(session, h, task):
    confirmation=session.scalar(select(Confirmation).where(Confirmation.task_id==task.id))
    challenge=create_challenge(session,task=task,confirmation=confirmation,user_id=h.principal.user_id)
    assert verify_challenge(session,challenge_id=challenge["challenge_id"],user_id=h.principal.user_id,
        sandbox_id=h.principal.sandbox_id,code=challenge["demo_code"])["verified"]
    return confirmation,challenge["challenge_id"]


def _credit(h):
    from zhenxing.credit_models import CreditAccount,CreditCard,CreditCurrencyBalance
    with h.factory.begin() as session:
        facility=CreditAccount(sandbox_id=h.principal.sandbox_id,user_id=h.principal.user_id,base_credit_limit_minor=200000)
        session.add(facility);session.flush()
        card=CreditCard(sandbox_id=h.principal.sandbox_id,user_id=h.principal.user_id,credit_account_id=facility.id,
            card_number="xiaozhao1122abcd",status="ACTIVE",transaction_limit_minor=200000)
        session.add(card)
        for currency in ("CNY","USD"):
            session.add(CreditCurrencyBalance(credit_account_id=facility.id,currency=currency))
        session.flush()
        return card.id,facility.id


def test_typed_natural_plan_entry_needs_no_user_uuid_and_only_one_confirmation(h):
    with h.factory.begin() as session:
        task=_task(session,h,"给成年子女生日安排1000元，鲜花蛋糕")
        prepare_life_plan_task(session,task,{"kind":"LIFE_PLAN_AUTHORIZE","slots":{
            "family_member_name":"成年子女","card_last4":"1234","budget_minor":100000,
            "life_plan_items":"鲜花、蛋糕",
            "recipient_name":"孩子","recipient_phone":"local-test","recipient_address":"已确认的地址",
            "life_plan_name":"孩子本次生日"}})
        assert task.state=="AWAITING_CONFIRMATION"
        assert session.scalar(select(func.count()).select_from(LifePlan))==1
        assert session.scalar(select(func.count()).select_from(Confirmation))==1
        assert session.scalar(select(func.count()).select_from(PaymentCashReservation))==0
        confirmation,challenge=_challenge(session,h,task)
        commit_life_plan_task(session,task,h.principal,confirmation,"one-mandate",auth_challenge_id=challenge)
        assert session.scalar(select(func.count()).select_from(LifePlanMandate))==1
        assert session.scalar(select(func.count()).select_from(Confirmation))==1
        assert task.result_json["authorization_receipt"]["maximum_occurrences"]==1
        assert task.result_json["plans"][0]["name"]=="孩子本次生日"
        assert task.result_json["cash_debit_minor"]==0
    tick(h,RESERVE);tick(h,ORDER)
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==2
        assert session.scalar(select(func.count()).select_from(Confirmation))==1


def test_missing_recipient_clarifies_only_missing_fields_without_draft_or_funds(h):
    with h.factory.begin() as session:
        task=_task(session,h,"为孩子安排生日，选鲜花和蛋糕")
        prepare_life_plan_task(session,task,{"kind":"LIFE_PLAN_AUTHORIZE","slots":{
            "family_member_name":"成年子女","card_last4":"1234","recipient_name":"孩子","life_plan_items":"鲜花、蛋糕"}})
        assert task.state=="NEED_CLARIFICATION"
        assert "联系电话" in task.result_json["message"] and "收件地址" in task.result_json["message"]
        assert session.scalar(select(func.count()).select_from(LifePlan))==0
        assert session.scalar(select(func.count()).select_from(Confirmation))==0


def test_birthday_set_preview_does_not_save_until_confirm_and_age_is_optional(h):
    from zhenxing.occasion_runtime import prepare_occasion_task,commit_occasion_task
    from zhenxing.occasion_models import FamilyOccasion
    with h.factory.begin() as session:
        task=_task(session,h,"我孩子阳历生日是12月20日")
        prepare_occasion_task(session,task,{"kind":"OCCASION_SET","slots":{
            "family_member_name":"成年子女","birthday_month":12,"birthday_day":20,"birthday_calendar":"GREGORIAN"}})
        assert session.get(FamilyOccasion,h.occasion_id).month==11
        confirmation=session.scalar(select(Confirmation).where(Confirmation.task_id==task.id))
        commit_occasion_task(session,task,h.principal,confirmation,"birthday-set")
        saved=session.get(FamilyOccasion,h.occasion_id)
        assert saved.month==12 and saved.adult_subject_confirmed is None and saved.version==2
        assert task.state=="SUCCEEDED"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0


def test_child_recipient_can_receive_parent_cash_birthday_plan(h):
    from zhenxing.occasion_models import FamilyOccasion
    from zhenxing.credit_models import CreditAccount
    with h.factory.begin() as session:
        session.get(FamilyOccasion,h.occasion_id).adult_subject_confirmed=False
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE);tick(h,ORDER)
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==2
        assert session.scalar(select(func.count()).select_from(CreditAccount))==0


def test_birthday_credit_source_is_forbidden_not_silently_replaced(h):
    card_id,_=_credit(h)
    with pytest.raises(DomainError) as caught:
        draft(h,card_reference="credit:"+card_id)
    assert caught.value.code=="CREDIT_CARD_CASH_CHANNEL_FORBIDDEN"
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0
        assert session.scalar(select(func.count()).select_from(LifePlan))==0


def _purchase(h,card_reference,*,prepare_only=False):
    from zhenxing.merchant_runtime import prepare_merchant_task,commit_merchant_task
    with h.factory.begin() as session:
        task=_task(session,h,"用指定卡购买团圆餐厅家庭生日套餐")
        prepare_merchant_task(session,task,{"kind":"MERCHANT_PURCHASE","slots":{
            "merchant":"团圆餐厅","product_name":"家庭生日套餐","quantity":1,"card_reference":card_reference}})
        assert task.state=="AWAITING_CONFIRMATION"
        if not prepare_only:
            confirmation,challenge=_challenge(session,h,task)
            commit_merchant_task(session,task,h.principal,confirmation,"single:"+task.id,auth_challenge_id=challenge)
            return task.id,task.result_json["orders"][0]["order_id"]
        return task.id,None


@pytest.mark.parametrize("kind",["DEBIT","CREDIT"])
def test_ordinary_merchant_supports_cash_or_credit_without_faking_cash_balance(h,kind):
    from zhenxing.credit_models import CreditAccount,CreditCurrencyBalance
    if kind=="CREDIT":
        card_id,facility_id=_credit(h);reference="credit:"+card_id
    else:
        facility_id=None;reference="debit:"+h.card_id
    task_id,order_id=_purchase(h,reference)
    with h.factory.begin() as session:
        payment=session.scalar(select(PaymentRecord))
        assert payment.instrument_type==kind and payment.amount_minor==58800
        assert session.get(MerchantOrder,order_id).status=="PAID"
        if kind=="DEBIT":
            assert session.get(Account,h.account_id).balance_minor==141200
        else:
            assert session.get(Account,h.account_id).balance_minor==200000
            assert payment.receipt_json["cash_debit_minor"]==0
            assert session.get(CreditAccount,facility_id).posted_exposure_minor==58800
            book=session.scalar(select(CreditCurrencyBalance).where(CreditCurrencyBalance.credit_account_id==facility_id,
                CreditCurrencyBalance.currency=="CNY"))
            assert book.principal_minor==58800


@pytest.mark.parametrize("kind,status",[("DEBIT","LOCKED"),("DEBIT","LOST"),("CREDIT","LOCKED"),("CREDIT","LOST")])
def test_card_control_after_purchase_preview_prevents_new_payment(h,kind,status):
    from zhenxing.credit_models import CreditCard
    from zhenxing.merchant_runtime import commit_merchant_task
    if kind=="CREDIT": card_id,_=_credit(h);reference="credit:"+card_id;model=CreditCard
    else: card_id=h.card_id;reference="debit:"+card_id;model=Card
    task_id,_=_purchase(h,reference,prepare_only=True)
    with h.factory.begin() as session:
        session.get(model,card_id).status=status
        task=session.get(Task,task_id);confirmation,challenge=_challenge(session,h,task)
        with pytest.raises(DomainError):
            with session.begin_nested():
                commit_merchant_task(session,task,h.principal,confirmation,"blocked",auth_challenge_id=challenge)
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0
        assert session.scalar(select(func.count()).select_from(MerchantOrder))==0
        assert session.get(Account,h.account_id).balance_minor==200000


@pytest.mark.parametrize("kind",["DEBIT","CREDIT"])
def test_ordinary_refund_is_full_original_source_even_after_card_loss(h,kind):
    from zhenxing.credit_models import CreditAccount,CreditCard
    from zhenxing.merchant_service import request_order_refund,complete_order_refund
    if kind=="CREDIT": card_id,facility=_credit(h);reference="credit:"+card_id;model=CreditCard
    else: card_id=h.card_id;facility=None;reference="debit:"+card_id;model=Card
    _,order_id=_purchase(h,reference)
    with h.factory.begin() as session:
        session.get(model,card_id).status="LOST"
        refund=request_order_refund(session,h.principal,order_id,now=CLOCK)
        assert refund.status=="REQUESTED"
        receipt=complete_order_refund(session,h.principal,refund.id,now=CLOCK)
        again=complete_order_refund(session,h.principal,refund.id,now=CLOCK)
        assert receipt==again and receipt["status"]=="POSTED"
        assert session.get(MerchantOrder,order_id).status=="REFUNDED"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==1
        assert session.scalar(select(func.count()).select_from(PaymentRefund))==1
        assert session.get(Account,h.account_id).balance_minor==200000
        if facility: assert session.get(CreditAccount,facility).posted_exposure_minor==0


def test_order_node_savepoint_rolls_back_only_its_unverified_payment(h,monkeypatch):
    import zhenxing.payment_instruments as payments
    real=payments.post_merchant_payment
    def corrupt(*args,**kwargs):
        return {**real(*args,**kwargs),"ledger_balanced":False}
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE)
    monkeypatch.setattr(payments,"post_merchant_payment",corrupt)
    tick(h,ORDER)
    with h.factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0
        assert session.scalar(select(func.count()).select_from(MerchantOrder))==0
        assert session.get(Account,h.account_id).balance_minor==200000
        assert session.scalar(select(PaymentCashReservation)).remaining_minor==100000


def test_paused_plan_expiry_releases_cash_without_order_or_permanent_hold(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE)
    with h.factory.begin() as session:
        pause_life_plan(session,h.principal,plan["plan_id"],expected_version=1)
    tick(h,DELIVER+timedelta(hours=3))
    with h.factory.begin() as session:
        assert session.scalar(select(PaymentCashReservation)).remaining_minor==0
        assert session.get(LifePlan,plan["plan_id"]).reserved_minor==0
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0


def test_partial_failure_automatically_refunds_only_actual_unfulfilled_payment(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE);tick(h,ORDER,limit=1)
    with h.factory.begin() as session:
        first=session.scalar(select(MerchantOrder))
        other=next(row for row in h.items if row["product_id"]!=first.product_id)
        session.get(MerchantProduct,other["product_id"]).stock=0
    assert tick(h,ORDER)==2  # failed second node, then bounded compensation
    with h.factory.begin() as session:
        row=session.get(LifePlan,plan["plan_id"])
        assert row.status=="COMPENSATED" and row.reserved_minor==0
        assert session.get(Account,h.account_id).balance_minor==200000
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==1
        assert session.scalar(select(func.count()).select_from(PaymentRefund))==1
        assert session.scalar(select(MerchantOrder)).status=="REFUNDED"
        view=life_plan_view(session,h.principal,plan["plan_id"])["plans"][0]
        assert view["spent_minor"]==view["refunded_minor"] and view["net_cash_debit_minor"]==0


def test_changed_occurrence_time_cannot_bypass_sealed_t_minus_two_window(h):
    plan=draft(h);authorize(h,plan["plan_id"])
    with h.factory.begin() as session:
        session.scalar(select(LifePlanOccurrence)).order_at=CLOCK
    tick(h,RESERVE)
    with h.factory.begin() as session:
        assert session.get(LifePlan,plan["plan_id"]).status=="PAUSED_RECONFIRM"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0


def test_another_plans_reservation_cannot_be_consumed_by_swapping_sidecar_id(h):
    first=draft(h)
    second=draft(h,request_key="separate-explicit-request")
    authorize(h,first["plan_id"]);authorize(h,second["plan_id"]);tick(h,RESERVE)
    with h.factory.begin() as session:
        a=session.get(LifePlan,first["plan_id"]);b=session.get(LifePlan,second["plan_id"])
        occurrence=session.scalar(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id==a.id))
        a.reservation_id=b.reservation_id;session.flush()
        with pytest.raises(DomainError) as caught:
            with session.begin_nested():
                place_authorized_order(session,h.principal,occurrence.id,0,now=ORDER)
        assert caught.value.code=="LIFE_PLAN_RESERVATION_CHANGED"
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==0


def test_scope_amendment_preserves_old_hold_until_replacement_confirmation(h):
    plan=draft(h);authorize(h,plan["plan_id"]);tick(h,RESERVE)
    with h.factory.begin() as session:
        original=session.get(LifePlan,plan["plan_id"]).reservation_id
        pause_life_plan(session,h.principal,plan["plan_id"],expected_version=1)
        value=amend_life_plan_draft(session,h.principal,plan["plan_id"],expected_version=1,
            recipient={"name":"孩子","phone":"new-test","address":"本人重新确认的新地址"},now=RESERVE)["plans"][0]
        assert value["status"]=="DRAFT" and value["version"]==1 and value["plan_id"]!=plan["plan_id"]
        assert session.get(PaymentCashReservation,original).remaining_minor==100000
        assert session.get(LifePlan,plan["plan_id"]).reservation_id==original
    authorize(h,value["plan_id"]);tick(h,RESERVE);tick(h,ORDER)
    with h.factory.begin() as session:
        row=session.get(LifePlan,value["plan_id"])
        assert row.reservation_id!=original
        assert session.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.status=="CONSUMED"))==2
        assert all(order.recipient_json["address"]=="本人重新确认的新地址" for order in session.scalars(select(MerchantOrder)))


@pytest.mark.parametrize("tamper",[False,True])
def test_normal_merchant_fulfilment_is_separate_and_invalid_source_is_not_success(h,tamper):
    from zhenxing.merchant_runtime import run_due_merchant_orders
    task_id,order_id=_purchase(h,"debit:"+h.card_id)
    with h.factory.begin() as session:
        if tamper:
            session.scalar(select(ToolCall).where(ToolCall.task_id==task_id)).result_json={"payment":{}}
        else:
            session.get(Card,h.card_id).status="LOST"  # already-paid fulfilment is not a new card payment
    with h.factory.begin() as session:
        assert run_due_merchant_orders(session,now=CLOCK+timedelta(minutes=30))==1
        row=session.get(MerchantOrder,order_id)
        assert row.status==("SOURCE_UNVERIFIED" if tamper else "FULFILLED")
        assert row.payment_receipt_json["cash_debit_minor"]==58800
        assert session.scalar(select(func.count()).select_from(PaymentRecord))==1
