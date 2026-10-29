"""Actual chat worker -> guarded planner -> bank preparation, no confirmation.

Every bank fixture is temporary SQLite; rules/stub model only. These tests
verify integration and no writes, not cloud semantics or a physical UI.
"""
from copy import deepcopy

import pytest
from sqlalchemy import select, func

from test_conversation_service import chat, plan, turn
from test_dialogue_references import two_cards, no_network
from zhenxing.conversation_models import ChatThread, ChatTurn
from zhenxing.conversation_service import conversation_view
from zhenxing.dialogue_reference_integration import METADATA_KEY, PRIVATE_KEY, reference_verified
from zhenxing.models import Account, Card, Subscription, Holding, Transfer, InvestmentOrder, LedgerEntry, Task, Confirmation
from zhenxing.model_gateway import ModelGateway, RuleIntentModel, _proposal_messages
from zhenxing.runtime import run_one_job, task_trace
from zhenxing.runtime_models import RuntimeRecord, RuntimeOperation, PlanSnapshot


def business(factory):
    with factory() as db:
        return {"accounts": sorted((row.id,row.balance_minor,row.version) for row in db.scalars(select(Account))),
            "cards": sorted((row.id,row.status,row.limit_minor,row.version) for row in db.scalars(select(Card))),
            "subscriptions": sorted((row.id,row.status,row.amount_minor,row.version) for row in db.scalars(select(Subscription))),
            "holdings": sorted((row.id,row.units,row.version) for row in db.scalars(select(Holding))),
            "writes": [db.scalar(select(func.count()).select_from(cls)) for cls in (Transfer,InvestmentOrder,LedgerEntry)]}


def send(chat, text):
    row = turn(chat,text)
    if row.mode in {"NEW_TASK","REVISE"} and row.task_id:
        assert run_one_job(chat[0],chat[1])
    with chat[0]() as db:
        trace = task_trace(db,chat[2],row.task_id) if row.task_id else None
        view = conversation_view(db,chat[2],chat[3])["turns"][-1]
    return row,trace,view


def proposal(factory, identifier):
    with factory() as db:
        record = db.get(RuntimeRecord,identifier)
        snapshot = db.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==identifier,PlanSnapshot.version==record.plan_version))
        return snapshot.proposal_json


@pytest.mark.parametrize("text,ordinal",[("锁第一张",1),("冻结第二张",2),("Can you freeze the first card?",1)])
def test_cards_actual_chat_query_then_ordinal_lock_keeps_correct_target_and_no_effects(two_cards,text,ordinal):
    chat=two_cards;before=business(chat[0])
    original=plan(chat,"我的银行卡有哪些")
    with chat[0]() as db:
        op=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==original["task_id"]))
        selected=db.get(Task,op.child_task_id).result_json["cards"][ordinal-1]
    row,trace,_view=send(chat,text)
    assert row.mode=="NEW_TASK" and trace["state"]=="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    assert [node["action"] for node in trace["nodes"]]==["CARD_LOCK"]
    with chat[0]() as db:
        confirmation=db.get(Confirmation,trace["pending_confirmation"]["confirmation_id"])
        child=db.get(Task,confirmation.task_id)
        assert confirmation.target_id==selected["card_id"]
        assert child.fic_json["slots"]["card_id"]["value"]==selected["card_id"]
    assert business(chat[0])==before


def test_cards_explain_specific_second_then_that_card_after_asides(two_cards):
    chat=two_cards;before=business(chat[0])
    plan(chat,"查询银行卡余额")
    selected,_trace,_view=send(chat,"第二张怎么样")
    assert selected.mode=="CONSULT"
    send(chat,"你好")
    send(chat,"你还能做什么")
    row,trace,_view=send(chat,"锁那张卡")
    assert row.mode=="NEW_TASK" and trace["state"]=="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    with chat[0]() as db:
        record=db.get(RuntimeRecord,row.task_id)
        b=record.metadata_json[METADATA_KEY]
        assert b["focus_receipt"]["turn_id"]==selected.id
        assert b["resolution"]["expected_slots"]["card_number"]==next(item["value"] for item in b["catalog"]["entities"] if item["ordinal"]==2)
    assert business(chat[0])==before


def test_subscriptions_actual_first_cancellation_keeps_bank_merchant_and_confirmation(chat):
    before=business(chat[0]);source=plan(chat,"查询订阅服务")
    with chat[0]() as db:
        op=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==source["task_id"]))
        name=db.get(Task,op.child_task_id).result_json["subscriptions"][0]["merchant"]
    row,trace,_view=send(chat,"取消第一个订阅")
    assert row.mode=="NEW_TASK" and trace["state"]=="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    assert proposal(chat[0],row.task_id)["actions"][0]["slots"]["merchant"]==name
    assert business(chat[0])==before


def test_subscriptions_named_timing_then_can_cancel_then_this_cancel_is_one_known_service(chat):
    before=business(chat[0]);source=plan(chat,"查询订阅服务")
    with chat[0]() as db:
        op=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==source["task_id"]))
        name=db.get(Task,op.child_task_id).result_json["subscriptions"][0]["merchant"]
    send(chat,name+"下次什么时候扣")
    inquiry,_trace,_view=send(chat,"这个可以取消吗")
    assert inquiry.mode=="CONSULT",(inquiry.mode,inquiry.answer)
    row,trace,_view=send(chat,"那就取消这个")
    assert row.mode=="NEW_TASK" and trace["state"]=="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    assert proposal(chat[0],row.task_id)["actions"][0]["slots"]["merchant"]==name
    assert business(chat[0])==before


def test_wealth_reference_100_then_amount_revision_200_retains_exact_product_and_genesis(chat):
    before=business(chat[0]);source=plan(chat,"比较理财产品")
    buy,trace,_view=send(chat,"买第一个100元")
    assert buy.mode=="NEW_TASK" and trace["state"] not in {"SUCCEEDED","UNKNOWN","EXECUTING","FAILED"},(buy.mode,buy.answer,trace)
    old=proposal(chat[0],buy.task_id)["actions"]
    assert len(old)==1 and old[0]["kind"]=="WEALTH_BUY" and old[0]["slots"]["amount_minor"]==10000
    revised,latest,_view=send(chat,"改成200元")
    assert revised.mode=="REVISE" and revised.task_id==buy.task_id,(revised.mode,revised.answer)
    new=proposal(chat[0],buy.task_id)["actions"]
    assert len(new)==1 and new[0]["slots"]["product_name"]==old[0]["slots"]["product_name"] and new[0]["slots"]["amount_minor"]==20000
    with chat[0]() as db:
        record=db.get(RuntimeRecord,buy.task_id)
        assert record.metadata_json["dialogue_slot_update"]["genesis"]["type"]=="VERIFIED_RESULT_REFERENCE"
        assert record.metadata_json[METADATA_KEY]["expected_actions"][0]["slots"]["amount_minor"]==10000
    assert business(chat[0])==before


def test_card_reference_limit_then_amount_revision_uses_slot_ledger_without_leaking_inherited_pan(two_cards):
    chat=two_cards;before=business(chat[0]);plan(chat,"查询银行卡状态")
    first,trace,_view=send(chat,"调整第二张限额1000元")
    assert first.mode=="NEW_TASK" and trace["state"]=="AWAITING_CONFIRMATION",(first.mode,first.answer,trace)
    row,latest,_view=send(chat,"改成2000元")
    assert row.mode=="REVISE" and row.task_id==first.task_id,(row.mode,row.answer)
    action=proposal(chat[0],row.task_id)["actions"][0]
    assert action["kind"]=="CARD_SET_LIMIT" and action["slots"]["amount_minor"]==200000
    assert business(chat[0])==before


@pytest.mark.parametrize("followup",["为什么这么高","这个扣款能说明什么"])
def test_largest_bill_explanation_and_followup_have_registered_fact_sources_not_raw_json(chat,followup):
    before=business(chat[0]);plan(chat,"分析2026年9月账单")
    first,_trace,_view=send(chat,"最大那笔是什么")
    assert first.mode=="CONSULT",(first.mode,first.answer)
    assert "{" not in first.answer and '"domain"' not in first.answer and "fact_" not in first.answer
    assert "记录" in first.answer or first.sources_json
    latest,_trace,_view=send(chat,followup)
    assert latest.mode=="CONSULT",(latest.mode,latest.answer)
    assert any(source["source_id"]=="facts:selected-dialogue-object" for source in latest.sources_json),(latest.sources_json,latest.answer)
    assert "{" not in latest.answer and "fact_" not in latest.answer
    assert business(chat[0])==before


def test_source_identity_changed_after_user_selection_does_not_default_to_other_card(two_cards):
    chat=two_cards;before=business(chat[0]);source=plan(chat,"查询银行卡状态")
    with chat[0].begin() as db:
        op=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==source["task_id"]))
        target=db.get(Task,op.child_task_id).result_json["cards"][0]
        db.get(Card,target["card_id"]).version+=1
    allowedbefore=business(chat[0])
    row,trace,_view=send(chat,"锁第一张")
    assert not trace or trace["state"]!="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    assert business(chat[0])==allowedbefore


def test_explicit_other_query_breaks_stale_subscription_this_focus(chat):
    before=business(chat[0]);source=plan(chat,"查询订阅服务")
    with chat[0]() as db:
        op=db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==source["task_id"]))
        name=db.get(Task,op.child_task_id).result_json["subscriptions"][0]["merchant"]
    send(chat,name+"下次什么时候扣")
    send(chat,"查询银行卡余额")
    row,trace,_view=send(chat,"那就取消这个")
    assert not trace or trace["state"]!="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    assert business(chat[0])==before


def test_actual_planning_private_reference_context_is_serialized_only_through_public_projection(two_cards):
    chat=two_cards;plan(chat,"查询银行卡状态")
    captured=[]
    class Spy(RuleIntentModel):
        def propose(self,message,context=None):
            proof=reference_verified(context)
            if proof:
                captured.append((proof,deepcopy(_proposal_messages(message,context))))
            return super().propose(message,context)
    changed=(chat[0],ModelGateway(Spy()),chat[2],chat[3])
    row,trace,_view=send(changed,"锁第二张")
    assert captured and trace["state"]=="AWAITING_CONFIRMATION",(row.mode,row.answer,trace)
    import json
    proof,prompt=captured[0];text=json.dumps(prompt,ensure_ascii=False)
    assert PRIVATE_KEY not in text and "VerifiedResultReference(" not in text
    assert proof.binding["resolution"]["expected_slots"]["card_number"] not in text


@pytest.mark.parametrize("query,question,meaning",[
    ("查询银行卡状态","第一张怎么样",("状态","可用","正常","启用","锁定","ACTIVE")),
    ("查询订阅服务","第一个可以取消吗",("可以取消","可以关闭","支持取消","取消续费","关闭续费","未接入取消")),
    ("比较理财产品","第一个怎么样",("风险","期限","赎回","适配")),
])
def test_reference_consultation_answers_actual_object_question_not_generic_bill_template(chat,query,question,meaning):
    before=business(chat[0]);plan(chat,query)
    row,_trace,_view=send(chat,question)
    assert row.mode=="CONSULT",(row.mode,row.answer)
    assert any(word in row.answer for word in meaning),row.answer
    if query!="分析账单": assert "这是账单中已有的记录" not in row.answer
    assert "{" not in row.answer and "fact_" not in row.answer
    assert business(chat[0])==before


@pytest.mark.parametrize("message",["尾号8765那张","锁那张尾号8765的卡","那张卡，卡号zhenhang11111111"])
def test_direct_resolver_does_not_ignore_current_explicit_card_target_for_a_sole_old_card(chat,message):
    from zhenxing.dialogue_references import build_result_reference_catalog,resolve_user_reference
    plan(chat,"查询银行卡状态")
    with chat[0]() as db:
        thread=db.get(ChatThread,chat[3]);catalog=build_result_reference_catalog(db,chat[2],thread)
        result=resolve_user_reference(db,chat[2],thread,message,catalog)
        assert result and result["status"] in {"MISSING","DENIED"},result


@pytest.mark.parametrize("uppercase",[False,True])
def test_direct_resolver_known_complete_card_identifier_selects_that_card_among_two(two_cards,uppercase):
    from zhenxing.dialogue_references import build_result_reference_catalog,resolve_user_reference
    chat=two_cards;plan(chat,"查询银行卡状态")
    with chat[0]() as db:
        thread=db.get(ChatThread,chat[3]);catalog=build_result_reference_catalog(db,chat[2],thread)
        expected=next(row for row in catalog["entities"] if row["ordinal"]==2)
        target=expected["value"].upper() if uppercase else expected["value"]
        result=resolve_user_reference(db,chat[2],thread,"锁那张卡，卡号"+target,catalog)
        assert result and result["status"]=="RESOLVED" and result["entity_ref"]==expected["entity_ref"],result
