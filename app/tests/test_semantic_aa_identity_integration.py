"""Actual unified MockTransport READ -> AA_PAY -> OTP -> bank receipt."""
from copy import deepcopy

from sqlalchemy import select, func

from test_access_runtime import named_runtime, authorization
from test_semantic_pro_asgi_entry import cloud_asgi, choice, source_change, send, drain, finance_snapshot
from test_banking_extensions import confirm
from zhenxing.accounts import create_customer_account
from zhenxing.banking_extensions import prepare_extra_task
from zhenxing.banking_extension_models import AACollectionRequest, AAParticipantIdentity
from zhenxing.contracts import FinancialIntentContract
from zhenxing.models import Task, Transfer, Confirmation, ToolCall, new_id
from zhenxing.platform import get_or_create_conversation
from zhenxing.runtime_models import RuntimeRecord, RuntimeOperation
from zhenxing.services import Principal
from zhenxing.aa_participant_payments import completed_aa_payment_receipt


def create_obligation(h, note=None):
    with h.factory.begin() as db:
        owner = create_customer_account(db, "aacollector", "fixture-pass88", seed=81)
        create_customer_account(db, "otherpayer", "fixture-peer88", seed=82)
        conversation = get_or_create_conversation(db, owner.sandbox_id, owner.user_id)
        task = Task(sandbox_id=owner.sandbox_id, user_id=owner.user_id, message="AA总额10.01元，参与者：customerone、otherpayer",
            intent_type="AA_REQUEST", state="NEED_CLARIFICATION", model_label="isolated-collector-confirmation",
            fic_json=FinancialIntentContract(intent_id=new_id(), intent_version=1, intent_type="AA_REQUEST",
                actor_user_id=owner.user_id, sandbox_id=owner.sandbox_id, source_channel="WEB", slots={},
                requested_capabilities=[], status="NEED_CLARIFICATION").model_dump())
        db.add(task); db.flush(); db.add(RuntimeRecord(task_id=task.id, kind="CHILD"))
        prepare_extra_task(db, task=task, conversation_id=conversation.id, **({'proposed_action':{'kind':'AA_REQUEST','slots':{
            'amount_minor':1001,'participant_names':['customerone','otherpayer'],'aa_note':note}}} if note else {}))
        assert task.state == "AWAITING_CONFIRMATION"
        confirm(db, task, Principal(owner.user_id, owner.sandbox_id), key="isolated-create-obligation")
        assert task.state == "SUCCEEDED"


def test_native_semantic_aa_identity_is_consumed_by_own_preparation_and_actual_payment_without_second_model_plan(cloud_asgi):
    h = cloud_asgi
    # This fixture owns disposable SQLite engines and explicitly exercises the
    # normal OTP/confirmation chain; no shared application database is used.
    h.state["allow_isolated_confirmations"] = True
    create_obligation(h)
    message = "查询我应付的AA。"
    scope = source_change(message, "aa_scope", "应付")
    scope["selected_options"] = ["PAYABLE"]
    h.state["semantic"] = choice("REQUEST", action_kinds=["AA_VIEW"], dependencies=[[]], changes=[scope])
    read = send(h, message, "aa-read")
    assert read.task_id, read.diagnostics_json
    drain(h)
    before = finance_snapshot(h.factory)
    payment_message = "支付这笔AA。"
    def decision(prepared):
        entities = [row for row in prepared["entities"] if row.get("read_kind") == "AA_VIEW" and row.get("field") == "aa_obligation_ref"]
        assert len(entities) == 1, prepared["entities"]
        update = source_change(payment_message, "aa_obligation_ref", "这笔AA")
        update["entity_ref"] = entities[0]["entity_ref"]
        return choice("REQUEST", action_kinds=["AA_PAY"], dependencies=[[]], changes=[update], entity_refs=[update["entity_ref"]])
    h.state["decision_factory"] = decision
    requested = send(h, payment_message, "aa-pay")
    assert requested.task_id, requested.diagnostics_json
    drain(h)
    trace = h.http.get(f"/api/v2/tasks/{requested.task_id}/trace", headers=h.headers).json()
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    assert trace["nodes"][0]["action"] == "AA_PAY"
    assert trace["pending_confirmation"]["scope"]["source_card_display_last4"]
    assert trace["pending_confirmation"]["scope"]["recipient_card_display_last4"]
    assert trace["pending_confirmation"]["scope"]["currency"] == "CNY"
    assert trace["pending_confirmation"]["scope"]["amount_minor"] == 501
    assert finance_snapshot(h.factory) == before
    approved = authorization(h.http, h.headers, trace)
    accepted = h.http.post(f"/api/v2/tasks/{requested.task_id}/confirm", headers=h.headers, json=approved)
    assert accepted.status_code == 202, accepted.text
    drain(h)
    final = h.http.get(f"/api/v2/tasks/{requested.task_id}/trace", headers=h.headers).json()
    assert final["state"] == "SUCCEEDED", final
    with h.factory() as db:
        operation = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == requested.task_id))
        child = db.get(Task, operation.child_task_id)
        call = db.scalar(select(ToolCall).where(ToolCall.task_id == child.id, ToolCall.tool_name == "transfer.commit.v1"))
        confirmation = db.get(Confirmation, approved["confirmation_id"])
        receipt = completed_aa_payment_receipt(db, db.get(Task, requested.task_id), operation, child, call, confirmation)
        assert receipt and receipt["amount_minor"] == 501 and receipt["collector_username"] == "aacollector"
        assert db.scalar(select(func.count()).select_from(Transfer)) == 1
        assert db.scalar(select(AAParticipantIdentity).where(AAParticipantIdentity.payment_task_id == requested.task_id)).status == "PAID"
    assert not any(event["purpose"] == "UNEXPECTED_PLANNER" for event in h.state["events"])
    assert h.http.post(f"/api/v2/tasks/{requested.task_id}/confirm", headers=h.headers, json=approved).status_code == 409
