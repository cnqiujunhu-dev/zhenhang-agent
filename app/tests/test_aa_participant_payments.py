"""Real two-sided AA settlement in disposable SQLite, no network/model API."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from test_access_runtime import named_runtime, login, begin, authorization
from test_aa_registered_accounts import setup_participants
from zhenxing import credit_models  # Register additive instrument tables in isolated schema.
from zhenxing.aa_participant_payments import expire_collections
from zhenxing.banking_extension_models import AACollectionRequest, AAParticipant, AARequestTerms, AAParticipantIdentity
from zhenxing.models import Account, Card, Transfer, Confirmation, LedgerEntry, utcnow, Task
from zhenxing.runtime import run_one_job


def collection(runtime):
    setup_participants(runtime)
    http, factory, gateway, _ = runtime
    collector = login(http, "customerone", "customer88")
    payer = login(http, "wangwei", "wangwei88")
    other = login(http, "zhanghao", "zhanghao88")
    trace = begin(runtime, collector, "AA总额10.01元，参与者：wangwei、zhanghao")
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    body = authorization(http, collector, trace)
    assert http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=collector, json=body).status_code == 202
    assert run_one_job(factory, gateway)
    own = http.get("/api/v2/transfers/aa/invitations", headers=payer)
    assert own.status_code == 200, own.text
    invitation = own.json()["invitations"][0]
    second = http.get("/api/v2/transfers/aa/invitations", headers=other).json()["invitations"][0]
    return collector, payer, other, invitation, second


def balances(factory):
    with factory() as db:
        return {row.id: row.balance_minor for row in db.scalars(select(Account))}


def prepare(runtime, headers, invitation, *, key="payment-one", reference=None):
    http = runtime[0]
    path = f"/api/v2/transfers/aa/{invitation['request_id']}/participants/{invitation['participant_id']}/payment-task"
    response = http.post(path, headers={**headers, "Idempotency-Key": key}, json={"source_card_reference": reference})
    assert response.status_code == 201, response.text
    task = response.json()
    trace = http.get(task["trace_url"], headers=headers)
    assert trace.status_code == 200
    return path, task, trace.json()


def pay(runtime, headers, trace):
    http, factory, gateway, _ = runtime
    body = authorization(http, headers, trace)
    accepted = http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=headers, json=body)
    assert accepted.status_code == 202, accepted.text
    assert run_one_job(factory, gateway)
    return http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=headers).json(), body


def test_same_actual_participant_cannot_be_counted_twice_using_username_and_owned_card_reference(named_runtime):
    from zhenxing.access_models import LoginAccount
    from zhenxing.banking_extensions import prepare_extra_task
    from zhenxing.card_identifiers import card_identifier_view
    from zhenxing.contracts import FinancialIntentContract
    from zhenxing.models import new_id
    from zhenxing.platform import get_or_create_conversation
    from zhenxing.runtime_models import RuntimeRecord
    setup_participants(named_runtime)
    before = balances(named_runtime[1])
    with named_runtime[1].begin() as db:
        collector = db.scalar(select(LoginAccount).where(LoginAccount.username == "customerone"))
        payer = db.scalar(select(LoginAccount).where(LoginAccount.username == "wangwei"))
        card = db.scalar(select(Card).where(Card.user_id == payer.user_id))
        number = card_identifier_view(db, card)["card_number"]
        task = Task(sandbox_id=collector.sandbox_id, user_id=collector.user_id,
            intent_type="AA_REQUEST", state="NEED_CLARIFICATION", message="AA总额10元，参与者：wangwei、"+number,
            model_label="isolated-identity-duplicate", fic_json=FinancialIntentContract(intent_id=new_id(), intent_version=1,
                intent_type="AA_REQUEST", actor_user_id=collector.user_id, sandbox_id=collector.sandbox_id,
                source_channel="WEB", slots={}, requested_capabilities=[], status="NEED_CLARIFICATION").model_dump())
        db.add(task); db.flush(); db.add(RuntimeRecord(task_id=task.id, kind="CHILD"))
        conversation = get_or_create_conversation(db, collector.sandbox_id, collector.user_id)
        prepare_extra_task(db, task=task, conversation_id=conversation.id,
            proposed_action={"kind": "AA_REQUEST", "slots": {"amount_minor": 1000, "participant_names": ["wangwei", number]}})
        assert task.state == "NEED_CLARIFICATION"
        assert task.result_json["error"] == "AA_PARTICIPANT_DUPLICATE"
        assert db.scalar(select(func.count()).select_from(Confirmation).where(Confirmation.task_id == task.id)) == 0
        assert db.scalar(select(func.count()).select_from(AACollectionRequest)) == 0
    assert balances(named_runtime[1]) == before


def test_each_participant_selects_own_card_and_confirmed_bank_transfer_posts_both_sides_once(named_runtime):
    collector, payer, other, invitation, second = collection(named_runtime)
    http, factory, gateway, _ = named_runtime
    before = balances(factory)
    assert invitation["amount_minor"] == 501 and second["amount_minor"] == 500
    assert "balance" not in str(invitation["recipient"]).lower()
    path, task, trace = prepare(named_runtime, payer, invitation)
    assert balances(factory) == before and trace["pending_confirmation"]["requires_step_up"]
    assert http.post(path, headers={**collector, "Idempotency-Key": "cannot-pay-for-peer"}, json={}).status_code == 404
    replay = http.post(path, headers={**payer, "Idempotency-Key": "payment-one"}, json={"source_card_reference": None})
    assert replay.status_code == 201 and replay.json()["task_id"] == task["task_id"]
    no_otp = {key: trace["pending_confirmation"][key] for key in ("confirmation_id", "plan_hash", "expected_task_version")}
    assert http.post(f"/api/v2/tasks/{task['task_id']}/confirm", headers=payer, json=no_otp).status_code == 403
    final, approval = pay(named_runtime, payer, trace)
    assert final["state"] == "SUCCEEDED", final
    committed = balances(factory)
    assert sum(committed.values()) == sum(before.values()) and committed != before
    with factory() as db:
        identity = db.get(AAParticipantIdentity, invitation["participant_id"])
        transfer = db.get(Transfer, identity.transfer_id)
        assert identity.status == "PAID" and transfer.amount_minor == 501
        assert db.get(AACollectionRequest, invitation["request_id"]).status == "PARTIAL"
        assert db.scalar(select(func.sum(LedgerEntry.delta_minor))) == 0
    assert http.post(f"/api/v2/tasks/{task['task_id']}/confirm", headers=payer, json=approval).status_code == 409
    _, second_task, second_trace = prepare(named_runtime, other, second, key="payment-two")
    second_final, _ = pay(named_runtime, other, second_trace)
    assert second_final["state"] == "SUCCEEDED"
    with factory() as db:
        row = db.get(AACollectionRequest, invitation["request_id"])
        assert row.status == "COMPLETED"
        assert db.scalar(select(func.count()).select_from(Transfer)) == 2
    assert sum(balances(factory).values()) == sum(before.values())


@pytest.mark.parametrize("change", ["decline", "cancel", "expire", "source_locked", "recipient_locked", "payer_epoch"])
def test_pending_payment_rechecks_current_invitation_and_card_before_actual_posting(named_runtime, change):
    collector, payer, other, invitation, second = collection(named_runtime)
    http, factory, gateway, _ = named_runtime
    _, task, trace = prepare(named_runtime, payer, invitation)
    before = balances(factory)
    approval = authorization(http, payer, trace)
    if change == "decline":
        live = http.get("/api/v2/transfers/aa/invitations", headers=payer).json()["invitations"][0]
        response = http.post(f"/api/v2/transfers/aa/{invitation['request_id']}/participants/{invitation['participant_id']}/decline",
                             headers=payer, json={"expected_version": live["version"]})
        assert response.status_code == 200
    elif change == "cancel":
        response = http.post(f"/api/v2/transfers/aa/{invitation['request_id']}/cancel", headers=collector,
                             json={"expected_version": 1})
        assert response.status_code == 200
    else:
        with factory.begin() as db:
            if change == "expire":
                db.get(AARequestTerms, invitation["request_id"]).expires_at = utcnow() - timedelta(seconds=1)
                assert expire_collections(db) == 1
            elif change == "recipient_locked":
                terms = db.get(AARequestTerms, invitation["request_id"])
                card = db.get(Card, terms.collector_scope_json["card_id"])
                card.status, card.version = "LOCKED", card.version + 1
            elif change == "payer_epoch":
                from zhenxing.access_models import LoginAccount
                identity = db.get(AAParticipantIdentity, invitation["participant_id"])
                db.get(LoginAccount, identity.login_account_id).version += 1
            else:
                participant = db.get(AAParticipantIdentity, invitation["participant_id"])
                from zhenxing.runtime_models import RuntimeOperation
                operation = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == participant.payment_task_id))
                child = db.get(Task, operation.child_task_id)
                card = db.get(Card, child.plan_json["nodes"][1]["source_card_context"]["card_id"])
                card.status, card.version = "LOCKED", card.version + 1
    response = http.post(f"/api/v2/tasks/{task['task_id']}/confirm", headers=payer, json=approval)
    if response.status_code == 202:
        assert run_one_job(factory, gateway)
    else:
        assert response.status_code in {403, 409}
    assert balances(factory) == before
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Transfer)) == 0
        assert db.get(Confirmation, approval["confirmation_id"]).status == "PENDING"


def test_collector_cannot_simulate_payment_or_borrow_another_participants_invitation(named_runtime):
    collector, payer, other, invitation, second = collection(named_runtime)
    http, factory, gateway, _ = named_runtime
    before = balances(factory)
    path = f"/api/v2/transfers/aa/{invitation['request_id']}/participants/{invitation['participant_id']}"
    assert http.post(path + "/simulate-payment", headers=collector).status_code == 403
    assert http.post(path + "/payment-task", headers={**other, "Idempotency-Key": "foreign"}, json={}).status_code == 404
    assert balances(factory) == before


def test_owned_read_offers_only_payer_obligations_and_collection_ref_never_authorizes_payment(named_runtime):
    from zhenxing.aa_obligations import aa_obligations_view, aa_read_result_current, aa_result_fact_entities, owned_obligation
    from zhenxing.access_models import LoginAccount
    from zhenxing.services import Principal
    from zhenxing.contracts import DomainError
    collector, payer, other, invitation, second = collection(named_runtime)
    http, factory, gateway, _ = named_runtime
    before = balances(factory)
    with factory() as db:
        payer_login = db.scalar(select(LoginAccount).where(LoginAccount.username == "wangwei"))
        collector_login = db.scalar(select(LoginAccount).where(LoginAccount.username == "customerone"))
        actor = Principal(payer_login.user_id, payer_login.sandbox_id)
        owner = Principal(collector_login.user_id, collector_login.sandbox_id)
        payable = aa_obligations_view(db, actor)
        assert len(payable["payable_obligations"]) == 1 and payable["collections"] == []
        assert payable["payable_obligations"][0]["paymentable"]
        assert aa_read_result_current(db, actor, payable)["source_fingerprint"] == payable["source_fingerprint"]
        entities = aa_result_fact_entities(payable)
        assert {entity["field"] for entity in entities} == {"aa_obligation_ref"}
        owned = aa_obligations_view(db, owner)
        assert owned["payable_obligations"] == [] and len(owned["collections"]) == 1
        with pytest.raises(DomainError): owned_obligation(db, owner, entities[0]["value"])
        with pytest.raises(DomainError): owned_obligation(db, actor, owned["collections"][0]["collection_ref"])
    assert balances(factory) == before
    path = f"/api/v2/transfers/aa/{invitation['request_id']}/participants/{invitation['participant_id']}/payment-task"
    assert http.post(path, headers={**payer, "Idempotency-Key": "cannot-change-obligation"},
        json={"amount_minor": 1, "source_card_reference": None}).status_code == 422


def test_self_hashed_tampered_obligation_and_state_change_invalidate_prior_read(named_runtime):
    from copy import deepcopy
    from zhenxing.aa_obligations import aa_obligations_view, aa_read_result_current
    from zhenxing.aa_participant_payments import decline_invitation
    from zhenxing.access_models import LoginAccount
    from zhenxing.services import Principal
    from zhenxing.contracts import DomainError
    from zhenxing.planning import canonical_hash
    collector, payer, other, invitation, second = collection(named_runtime)
    with named_runtime[1].begin() as db:
        login_row = db.scalar(select(LoginAccount).where(LoginAccount.username == "wangwei"))
        actor = Principal(login_row.user_id, login_row.sandbox_id)
        original = aa_obligations_view(db, actor, aa_scope="PAYABLE")
        tampered = deepcopy(original)
        tampered["payable_obligations"][0]["amount_minor"] = 1
        tampered["source_fingerprint"] = canonical_hash({key: value for key, value in tampered.items()
            if key not in {"observed_at", "source_fingerprint"}})
        with pytest.raises(DomainError): aa_read_result_current(db, actor, tampered)
        decline_invitation(db, actor, invitation["request_id"], invitation["participant_id"], 1)
        with pytest.raises(DomainError): aa_read_result_current(db, actor, original)
        assert aa_obligations_view(db, actor)["payable_obligations"][0]["paymentable"] is False
