"""Full isolated ASGI chains: only provider output is stubbed, no live network.

Existing debt/draft/invitation rows are explicit fixture preconditions. Every
chat-triggered write below uses production semantic guards, OTP and confirmation.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func

from test_access_runtime import named_runtime, authorization
from test_semantic_pro_asgi_entry import cloud_asgi, choice, source_change, send, drain, finance_snapshot
from test_semantic_aa_identity_integration import create_obligation
from zhenxing.access_models import LoginAccount
from zhenxing.credit_bootstrap import initialize_customer_payment_services
from zhenxing.credit_models import CreditCard, CreditCurrencyBalance, CreditInstallment, CreditObligation
from zhenxing.extensions_models import FamilyMember
from zhenxing.life_plan_models import LifePlan, LifePlanMandate
from zhenxing.life_plan_migration import initialize_life_plan_services
from zhenxing.life_plan_service import create_life_plan_draft
from zhenxing.merchant_service import merchant_catalog
from zhenxing.models import Account, Confirmation, Task, Transfer, User, utcnow
from zhenxing.occasion_service import put_family_occasion
from zhenxing.payment_instruments import post_merchant_payment, resolve_source_instrument
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.services import Principal


def requested(h, text, kind, key, changes=None, factory=None):
    h.state["decision_factory"] = factory
    h.state["semantic"] = choice("REQUEST", action_kinds=[kind], dependencies=[[]], changes=changes or [])
    turn = send(h, text, key)
    assert turn.task_id, (turn.diagnostics_json.get("semantic_processing_error"),
        (turn.diagnostics_json.get("semantic_diagnostics") or {}).get("failure_reasons"),
        h.state.get("input_error_codes"))
    assert not turn.diagnostics_json.get("semantic_processing_error"), turn.diagnostics_json
    drain(h)
    trace = h.http.get(f"/api/v2/tasks/{turn.task_id}/trace", headers=h.headers).json()
    assert trace["nodes"][0]["action"] == kind, trace
    return trace


def approve(h, trace):
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    assert trace["pending_confirmation"]["requires_step_up"]
    absent = {key: trace["pending_confirmation"][key] for key in ("confirmation_id", "plan_hash", "expected_task_version")}
    assert h.http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=h.headers, json=absent).status_code == 403
    approval = authorization(h.http, h.headers, trace)
    accepted = h.http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=h.headers, json=approval)
    assert accepted.status_code == 202, accepted.text
    drain(h)
    final = h.http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=h.headers).json()
    assert final["state"] == "SUCCEEDED", final
    assert h.http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=h.headers, json=approval).status_code == 409
    return final


def entity_change(prepared, text, field, literal, read_kind, source_field):
    entities = [row for row in prepared["entities"] if row.get("read_kind") == read_kind and row.get("field") == source_field]
    assert entities, prepared["entities"]
    change = source_change(text, field, literal)
    change["entity_ref"] = entities[0]["entity_ref"]
    return change


def owner(session):
    login = session.scalar(select(LoginAccount).where(LoginAccount.username == "customerone"))
    return Principal(login.user_id, login.sandbox_id)


def test_credit_view_then_debit_scope_then_return_to_card_repayment_and_actual_consumption_installment(cloud_asgi, monkeypatch):
    h = cloud_asgi; h.state["allow_isolated_confirmations"] = True
    from zhenxing import semantic_router
    original_input = semantic_router.prepare_semantic_input
    h.state["input_error_codes"] = []
    def checked_input(*args, **kwargs):
        try:
            return original_input(*args, **kwargs)
        except ValueError as error:
            h.state["input_error_codes"].append(str(error))
            raise
    monkeypatch.setattr(semantic_router, "prepare_semantic_input", checked_input)
    with h.factory.begin() as session:
        actor = owner(session)
        initialize_customer_payment_services(session, actor.sandbox_id, actor.user_id, adult_eligible=True,
            eligibility_source="ISOLATED_CONFIRMED_ADULT_PRECONDITION")
        card = session.scalar(select(CreditCard).where(CreditCard.user_id == actor.user_id))
        payment = post_merchant_payment(session, actor, instrument_snapshot=resolve_source_instrument(session, actor,
            "credit:"+card.id, purpose="MERCHANT"), amount_minor=10001, merchant_ref="isolated-existing-consumption", operation_key="preexisting-debt")
        before_cash = session.scalar(select(func.sum(Account.balance_minor)).where(Account.user_id == actor.user_id))
    assert requested(h, "查我的信用卡。", "CREDIT_VIEW", "credit-view")["state"] == "SUCCEEDED"
    assert requested(h, "再查我的借记卡。", "CARD_STATUS", "debit-view")["state"] == "SUCCEEDED"
    text = "回到这张信用卡，用这张借记卡还款10元。"
    def repay_decision(prepared):
        changes = [entity_change(prepared, text, "card_number", "这张信用卡", "CREDIT_VIEW", "credit_card_status"),
            entity_change(prepared, text, "source_card", "这张借记卡", "CARD_STATUS", "card_number"),
            source_change(text, "amount_minor", "10元")]
        return choice("REQUEST", action_kinds=["CREDIT_REPAY"], dependencies=[[]], changes=changes,
            entity_refs=[row["entity_ref"] for row in changes if row.get("entity_ref")])
    repayment = requested(h, text, "CREDIT_REPAY", "credit-repay", factory=repay_decision)
    with h.factory() as session:
        assert session.scalar(select(func.sum(Account.balance_minor)).where(Account.user_id == actor.user_id)) == before_cash
    approve(h, repayment)
    with h.factory() as session:
        assert session.scalar(select(func.sum(Account.balance_minor)).where(Account.user_id == actor.user_id)) == before_cash - 1000
        balance = session.scalar(select(CreditCurrencyBalance).where(CreditCurrencyBalance.currency == "CNY", CreditCurrencyBalance.credit_account_id == card.credit_account_id))
        assert balance.principal_minor == 9001
    # Query a new current statement after the actual repayment; the old card
    # identity may still identify the owned card, never authorize an old debt.
    assert requested(h, "现在查询信用卡消费账单。", "CREDIT_STATEMENT", "current-statement")["state"] == "SUCCEEDED"
    text = "把这笔消费办理3期分期。"
    def installment_decision(prepared):
        identity = entity_change(prepared, text, "payment_id", "这笔消费", "CREDIT_STATEMENT", "credit_payment")
        return choice("REQUEST", action_kinds=["CREDIT_INSTALLMENT"], dependencies=[[]], entity_refs=[identity["entity_ref"]],
            changes=[identity, source_change(text, "periods", "3期")])
    installment = requested(h, text, "CREDIT_INSTALLMENT", "installment-current-obligation", factory=installment_decision)
    approve(h, installment)
    with h.factory() as session:
        item = session.scalar(select(CreditInstallment).join(CreditObligation, CreditObligation.id == CreditInstallment.obligation_id)
            .where(CreditObligation.payment_id == payment["payment_id"]))
        assert item and item.periods == 3
        assert session.scalar(select(func.sum(Account.balance_minor)).where(Account.user_id == actor.user_id)) == before_cash - 1000
    assert not any(row["purpose"] == "UNEXPECTED_PLANNER" for row in h.state["events"])
    assert len([row for row in h.state["events"] if row["purpose"] == "SEMANTIC"]) == 5


def birthday_draft(h):
    with h.factory.begin() as session:
        actor = owner(session)
        initialize_customer_payment_services(session, actor.sandbox_id, actor.user_id, adult_eligible=True,
            eligibility_source="ISOLATED_CONFIRMED_ADULT_PRECONDITION")
        member_user = User(sandbox_id=actor.sandbox_id, display_name="生日收件成员")
        session.add(member_user); session.flush()
        member = FamilyMember(sandbox_id=actor.sandbox_id, owner_user_id=actor.user_id,
            helper_user_id=member_user.id, display_name="生日收件成员")
        session.add(member); session.flush()
        future = (utcnow() + timedelta(days=40)).replace(day=20)
        occasion = put_family_occasion(session, actor, family_member_id=member.id, month=future.month, day=20,
            source_reference="isolated-confirmed-birthday-precondition")["occasions"][0]
        initialize_life_plan_services(session, actor.sandbox_id)
        items = [{"merchant_id": row["merchant_id"], "product_id": row["product_id"], "quantity": 1}
            for row in merchant_catalog(session, actor)["products"] if row["category"] in {"FLOWER", "CAKE"}]
        source = resolve_source_instrument(session, actor, purpose="LIFE_PLAN")
        day = future.date().isoformat()
        plan = create_life_plan_draft(session, actor, occasion_id=occasion["occasion_id"], year=future.year,
            account_id=source["account_id"], card_reference=source["card_number"], items=items, budget_minor=100000,
            recipient={"name": "生日收件成员", "phone": "isolated-only", "address": "隔离夹具明确地址"},
            delivery_start=day+"T10:00:00+08:00", delivery_end=day+"T12:00:00+08:00", request_key="isolated-preexisting-draft")["plans"][0]
        return plan["plan_id"]


def test_birthday_and_aa_cross_scope_return_keeps_separate_identity_consent_and_payment(cloud_asgi):
    h = cloud_asgi; h.state["allow_isolated_confirmations"] = True
    create_obligation(h); plan_id = birthday_draft(h)
    assert requested(h, "查我的生日安排。", "LIFE_PLAN_VIEW", "birthday-view")["state"] == "SUCCEEDED"
    assert requested(h, "转去查询我应付的AA。", "AA_VIEW", "aa-other-scope")["state"] == "SUCCEEDED"
    text = "回到这个生日安排，授权本次计划。"
    def birthday_decision(prepared):
        identity = entity_change(prepared, text, "life_plan_id", "这个生日安排", "LIFE_PLAN_VIEW", "life_plan_id")
        return choice("REQUEST", action_kinds=["LIFE_PLAN_AUTHORIZE"], dependencies=[[]], changes=[identity], entity_refs=[identity["entity_ref"]])
    birthday = requested(h, text, "LIFE_PLAN_AUTHORIZE", "authorize-birthday-return", factory=birthday_decision)
    with h.factory() as session:
        assert session.scalar(select(func.count()).select_from(LifePlanMandate)) == 0
        assert session.scalar(select(func.count()).select_from(Transfer)) == 0
    approve(h, birthday)
    with h.factory() as session:
        plan = session.get(LifePlan, plan_id)
        assert plan.status == "ACTIVE"
        from zhenxing.life_status_summary import completed_life_status_summary
        from zhenxing.task_dialogue_context import task_context
        parent=session.get(Task,birthday['task_id'])
        summary=completed_life_status_summary(session,owner(session),[parent],[task_context(session,owner(session),parent)])
        assert summary and '已完成授权，还没有为商品付款' in summary['answer'] and '已花费0.00元' in summary['answer']
        assert session.scalar(select(func.count()).select_from(LifePlanMandate)) == 1
        assert session.scalar(select(func.count()).select_from(Transfer)) == 0
    text = "再回到这笔AA，支付本人份额。"
    def aa_decision(prepared):
        identity = entity_change(prepared, text, "aa_obligation_ref", "这笔AA", "AA_VIEW", "aa_obligation_ref")
        return choice("REQUEST", action_kinds=["AA_PAY"], dependencies=[[]], changes=[identity], entity_refs=[identity["entity_ref"]])
    aa = requested(h, text, "AA_PAY", "aa-return-after-birthday", factory=aa_decision)
    assert aa["task_id"] != birthday["task_id"]
    approve(h, aa)
    with h.factory() as session:
        assert session.scalar(select(func.count()).select_from(Transfer)) == 1
        assert session.scalar(select(Transfer.amount_minor)) == 501
        assert session.scalar(select(func.count()).select_from(LifePlanMandate)) == 1
    assert len([row for row in h.state["events"] if row["purpose"] == "SEMANTIC"]) == 4
    assert not any(row["purpose"] == "UNEXPECTED_PLANNER" for row in h.state["events"])


@pytest.mark.parametrize("kind,field,text,literal", [
    ("CREDIT_REPAY", "card_number", "用这张卡作为信用卡还款目标，还款10元。", "这张卡"),
    ("AA_PAY", "aa_obligation_ref", "支付这笔AA。", "这笔AA"),
])
def test_semantic_cross_scope_identity_cannot_relabel_debit_result_as_credit_target_or_aa_obligation(
        cloud_asgi, kind, field, text, literal):
    h = cloud_asgi
    assert requested(h, "查我的借记卡。", "CARD_STATUS", "owned-debit-source")["state"] == "SUCCEEDED"
    before = finance_snapshot(h.factory)
    with h.factory() as session:
        tasks = session.scalar(select(func.count()).select_from(Task))
        confirmations = session.scalar(select(func.count()).select_from(Confirmation))
    def wrong_domain(prepared):
        selected = entity_change(prepared, text, field, literal, "CARD_STATUS", "card_number")
        changes = [selected] + ([source_change(text, "amount_minor", "10元")] if kind == "CREDIT_REPAY" else [])
        return choice("REQUEST", action_kinds=[kind], dependencies=[[]], changes=changes, entity_refs=[selected["entity_ref"]])
    h.state["decision_factory"] = wrong_domain
    turn = send(h, text, "wrong-domain-reference")
    assert turn.task_id is None and turn.mode == "NEED_CONTEXT", turn.diagnostics_json
    assert turn.diagnostics_json.get("semantic_processing_error")
    assert finance_snapshot(h.factory) == before
    with h.factory() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == tasks
        assert session.scalar(select(func.count()).select_from(Confirmation)) == confirmations
    assert len([row for row in h.state["events"] if row["purpose"] == "SEMANTIC"]) == 2
