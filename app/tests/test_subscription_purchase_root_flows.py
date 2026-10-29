"""Actual isolated chat/kernel integration, no production funds or cloud calls."""
from copy import deepcopy
import pytest
from sqlalchemy import select,func
from test_subscription_commerce_dialogue import commerce_chat
from test_dialogue_reference_runtime_integration import send,business
from test_conversation_service import chat,plan
from test_conversation_service import turn
from zhenxing.models import Confirmation,SubscriptionProduct,SubscriptionPayment
from zhenxing.product_knowledge import scoped_sources,monetary_fact_catalog,sources_still_authorized,render_monetary_facts


@pytest.mark.parametrize("message", ["视频会员多少钱", "云存储多少钱", "How much is video membership?"])
def test_public_price_actual_chat_explains_catalog_without_task_or_private_read(commerce_chat, message):
    chat = commerce_chat; before = business(chat[0])
    response = turn(chat, message)
    assert response.status == "ANSWERED" and response.mode == "CONSULT" and response.task_id is None, (response.mode, response.answer)
    assert response.diagnostics_json["public_subscription_price_consultation"] is True
    assert all(row["kind"] == "PRODUCT_KNOWLEDGE" or row["source_id"] == "facts:subscription-products" for row in response.sources_json)
    assert "25.00" in response.answer if "视频" in message or "video" in message else "18.00" in response.answer
    assert "facts:subscriptions" not in {row["source_id"] for row in response.sources_json}
    assert business(chat[0]) == before


def test_public_price_does_not_replace_pending_purchase_or_consume_its_confirmation(commerce_chat):
    chat = commerce_chat; opened = plan(chat, "订阅视频会员")
    response = turn(chat, "视频会员多少钱")
    assert response.task_id is None and response.mode == "CONSULT"
    with chat[0]() as db:
        confirmation = db.get(Confirmation, opened["pending_confirmation"]["confirmation_id"])
        assert confirmation.status == "PENDING" and confirmation.consumed_at is None


@pytest.mark.parametrize("message",["不订了","我不订了","别开通","先别开通了","取消这次开通"])
def test_declining_purchase_closes_only_unexecuted_same_task(commerce_chat,message):
    chat=commerce_chat;before=business(chat[0])
    opened=plan(chat,"订阅视频会员")
    assert opened["state"]=="AWAITING_CONFIRMATION",opened
    confirmation=opened["pending_confirmation"]["confirmation_id"]
    response,trace,_view=send(chat,message)
    assert response.mode=="FEEDBACK" and response.task_id==opened["task_id"] and trace["state"]=="CLOSED",(response.mode,response.answer,trace)
    with chat[0]() as db:
        assert db.get(Confirmation,confirmation).status=="CANCELLED"
        assert not db.scalar(select(func.count()).select_from(SubscriptionPayment))
    assert business(chat[0])==before


def test_catalog_question_returns_video_only_exact_bank_price_and_invalidates_changed_terms(commerce_chat):
    chat=commerce_chat
    with chat[0]() as db:
        records=scoped_sources(db,chat[2],"视频会员多少钱")
        record=next(row for row in records if row["source_id"]=="facts:subscription-products")
        assert record["catalog_filter"]=="视频会员" and "云存储" not in record["content"]
        facts=monetary_fact_catalog([record])
        prices={key:value for key,value in facts.items() if value.get('unit')=='MINOR'}
        terms={key:value for key,value in facts.items() if value.get('unit')=='SUBSCRIPTION_TERMS'}
        assert len(prices)==1 and len(terms)==1 and len(facts)==2
        price_id,price=next(iter(prices.items()))
        assert price['field']=='monthly_price_minor' and price['currency']=='CNY' and price['value']==2500
        assert price['label']=='视频会员月价'
        assert set(terms)=={price_id+'.terms'}
        assert terms[price_id+'.terms']['source_id']==price['source_id']=='facts:subscription-products'
        assert terms[price_id+'.terms']['label']=='视频会员'
        assert terms[price_id+'.terms']['value']=={'min_months':1,'max_months':12,'default_months':1,'default_auto_renew':False}
        rendered=render_monetary_facts(facts,list(facts))
        assert "25.00" in rendered and "月价" in rendered
        assert sources_still_authorized(db,chat[2],[record])
        stored=deepcopy(record)
    with chat[0].begin() as db:
        product=db.scalar(select(SubscriptionProduct).where(SubscriptionProduct.merchant=="视频会员"))
        product.monthly_price_minor+=100;product.version+=1
    with chat[0]() as db:assert not sources_still_authorized(db,chat[2],[stored])
