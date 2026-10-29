"""Real isolated confirmation/scheduler/AA callers retain verified card IDs.

Only disposable SQLite and the existing semantic MockTransport are used.
There is no request to any running service, bank database or real provider.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from test_access_runtime import named_runtime, authorization
from test_semantic_pro_asgi_entry import cloud_asgi, choice, drain, send, source_change
from test_banking_workflow_confirmation_asgi import banking_h
from test_aa_participant_payments import collection, prepare, pay
from zhenxing.access_models import LoginAccount
from zhenxing.banking_extension_models import AAParticipantIdentity, ScopedScheduledTransfer, ScheduledTransferOccurrence
from zhenxing.banking_extensions import execute_due_schedules
from zhenxing.card_identifiers import card_identifier_view
from zhenxing.credit_bootstrap import initialize_customer_payment_services
from zhenxing.internal_banking import settlement_integrity
from zhenxing.models import Account, Card, Task, Transaction, Transfer, utcnow
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.services import Principal


def scoped_node(task):
    nodes = [row for row in task.plan_json["nodes"] if "transfer_payment_scope" in row]
    assert len(nodes) == 1, task.plan_json
    return nodes[0]


def explicit_endpoints(h):
    with h.factory.begin() as session:
        selected = []
        for username in ("customerone", "quyuwei"):
            login = session.scalar(select(LoginAccount).where(LoginAccount.username == username))
            upgrade = initialize_customer_payment_services(session, login.sandbox_id, login.user_id,
                adult_eligible=True, eligibility_source="ISOLATED_EXPLICIT_ADULT_FIXTURE")
            card = session.get(Card, upgrade["debit_card_ids"][1])
            card.status = "ACTIVE"
            selected.append({"card_id": card.id, "account_id": card.account_id,
                "number": card_identifier_view(session, card)["card_number"], "other_card_id": upgrade["debit_card_ids"][0]})
        return selected


def prepare_payment(h, kind, frequency="ONCE"):
    source, target = explicit_endpoints(h)
    due = (utcnow() + timedelta(hours=2)).replace(microsecond=0)
    text = f"从我的{source['number']}借记卡给{target['number']}转0.13元。"
    changes = [source_change(text, field, literal) for field, literal in (
        ("card_number", source["number"]), ("payee_name", target["number"]), ("amount_minor", "0.13元"))]
    if kind == "TRANSFER_SCHEDULE":
        date = due.isoformat()
        text += f"预约在{date}执行。"
        changes.append(source_change(text, "scheduled_at", date))
        if frequency != "ONCE":
            text += "每天一次，共3次。"
            changes.extend([{**source_change(text, "schedule_frequency", "每天"), "selected_options": ["DAILY"]},
                source_change(text, "schedule_occurrence_count", "3次")])
    h.state["semantic"] = choice("REQUEST", action_kinds=[kind], dependencies=[[]], changes=changes)
    row = send(h, text, "selected-statement-" + kind + "-" + frequency)
    assert row.task_id, row.diagnostics_json
    drain(h)
    trace = h.http.get("/api/v2/tasks/" + row.task_id + "/trace", headers=h.headers).json()
    assert trace["state"] == "AWAITING_CONFIRMATION", trace
    with h.factory() as session:
        operation = session.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == row.task_id,
            RuntimeOperation.active == 1))
        scope = deepcopy(scoped_node(session.get(Task, operation.child_task_id))["transfer_payment_scope"])
    assert (scope["source"]["card_id"], scope["recipient"]["card_id"]) == (source["card_id"], target["card_id"])
    return trace, scope, source, target


def confirm(h, trace):
    h.state["allow_isolated_confirmations"] = True
    body = authorization(h.http, h.headers, trace)
    response = h.http.post("/api/v2/tasks/" + trace["task_id"] + "/confirm", headers=h.headers, json=body)
    assert response.status_code == 202, response.text
    drain(h)
    return h.http.get("/api/v2/tasks/" + trace["task_id"] + "/trace", headers=h.headers).json()


def assert_rows_and_card_bills(session, transfer, scope, *, other_cards=()):
    rows = list(session.scalars(select(Transaction).where(Transaction.operation_ref == "transfer:" + transfer.id)))
    assert len(rows) == 2 and settlement_integrity(session, transfer)["balanced"]
    source, target, amount = scope["source"], scope["recipient"], scope["amount_minor"]
    assert {(row.sandbox_id, row.account_id, row.card_id, row.amount_minor) for row in rows} == {
        (source["sandbox_id"], source["account_id"], source["card_id"], -amount),
        (target["sandbox_id"], target["account_id"], target["card_id"], amount)}
    from zhenxing.billing_evidence import collect_owned_billing_evidence
    now = utcnow() + timedelta(minutes=1)
    for index, instrument in enumerate((source, target)):
        actor = Principal(instrument["user_id"], instrument["sandbox_id"])
        selected = collect_owned_billing_evidence(session, actor, now - timedelta(days=2), now,
            business_time=now, card_reference="debit:" + instrument["card_id"])
        found = [row for row in selected["rows"] if row.get("operation_ref") == "transfer:" + transfer.id]
        assert len(found) == 1 and found[0]["card_id"] == instrument["card_id"]
        assert found[0]["economic_kind"] == "MOVEMENT"
        if other_cards:
            unselected = collect_owned_billing_evidence(session, actor, now - timedelta(days=2), now,
                business_time=now, card_reference="debit:" + other_cards[index])
            assert not any(row.get("operation_ref") == "transfer:" + transfer.id for row in unselected["rows"])


def test_confirmed_ordinary_transfer_posts_both_selected_card_ids_and_no_other_card_bill(banking_h):
    h = banking_h
    trace, scope, source, target = prepare_payment(h, "TRANSFER")
    final = confirm(h, trace)
    assert final["state"] == "SUCCEEDED", final
    with h.factory() as session:
        assert session.scalar(select(func.count()).select_from(Transfer)) == 1
        transfer = session.scalar(select(Transfer))
        assert_rows_and_card_bills(session, transfer, scope, other_cards=(source["other_card_id"], target["other_card_id"]))


@pytest.mark.parametrize("frequency", ["ONCE", "DAILY"])
def test_due_single_and_recurring_transfers_keep_saved_card_scope_across_each_posting(banking_h, frequency):
    h = banking_h
    trace, scope, source, target = prepare_payment(h, "TRANSFER_SCHEDULE", frequency)
    assert confirm(h, trace)["state"] == "WAITING"
    with h.factory.begin() as session:
        schedule = session.scalar(select(ScopedScheduledTransfer))
        assert schedule.scope_json["transfer_payment_scope"] == scope
        if frequency == "ONCE":
            assert execute_due_schedules(session, calendar_time=schedule.due_at) == 1
            assert execute_due_schedules(session, calendar_time=schedule.due_at) == 0
        else:
            dates = list(session.scalars(select(ScheduledTransferOccurrence).order_by(ScheduledTransferOccurrence.ordinal)))
            assert len(dates) == 3
            for occurrence in dates:
                assert execute_due_schedules(session, calendar_time=occurrence.due_at) == 1
            assert execute_due_schedules(session, calendar_time=dates[-1].due_at) == 0
        transfers = list(session.scalars(select(Transfer)))
        assert len(transfers) == (1 if frequency == "ONCE" else 3)
        for transfer in transfers:
            assert_rows_and_card_bills(session, transfer, scope, other_cards=(source["other_card_id"], target["other_card_id"]))


def test_real_aa_participant_selected_card_is_preserved_with_frozen_collector_card(named_runtime):
    _, payer, _, invitation, _ = collection(named_runtime)
    http, factory, _, _ = named_runtime
    with factory.begin() as session:
        login = session.scalar(select(LoginAccount).where(LoginAccount.username == "wangwei"))
        upgrade = initialize_customer_payment_services(session, login.sandbox_id, login.user_id,
            adult_eligible=True, eligibility_source="ISOLATED_AA_ADULT_FIXTURE")
        card = session.get(Card, upgrade["debit_card_ids"][1]); card.status = "ACTIVE"
        number = card_identifier_view(session, card)["card_number"]
        card_id = card.id
    _, task, trace = prepare(named_runtime, payer, invitation, reference=number)
    with factory() as session:
        operation = session.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task["task_id"], RuntimeOperation.active == 1))
        scope = deepcopy(scoped_node(session.get(Task, operation.child_task_id))["transfer_payment_scope"])
        assert scope["source"]["card_id"] == card_id
    final, body = pay(named_runtime, payer, trace)
    assert final["state"] == "SUCCEEDED", final
    with factory() as session:
        identity = session.get(AAParticipantIdentity, invitation["participant_id"])
        assert_rows_and_card_bills(session, session.get(Transfer, identity.transfer_id), scope)
        assert session.scalar(select(func.count()).select_from(Transfer)) == 1
    assert http.post("/api/v2/tasks/" + task["task_id"] + "/confirm", headers=payer, json=body).status_code == 409


@pytest.mark.parametrize("side", ["source", "recipient"])
def test_tampered_frozen_transfer_card_scope_never_creates_a_statement(banking_h, side):
    h = banking_h
    trace, _, _, _ = prepare_payment(h, "TRANSFER")
    h.state["allow_isolated_confirmations"] = True
    body = authorization(h.http, h.headers, trace)
    with h.factory.begin() as session:
        operation = session.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == trace["task_id"], RuntimeOperation.active == 1))
        child = session.get(Task, operation.child_task_id)
        changed = deepcopy(child.plan_json)
        selected = next(row for row in changed["nodes"] if "transfer_payment_scope" in row)
        selected["transfer_payment_scope"][side]["card_id"] = "unverified-card"
        child.plan_json = changed
        before = {row.id: (row.balance_minor, row.version) for row in session.scalars(select(Account))}
    response = h.http.post("/api/v2/tasks/" + trace["task_id"] + "/confirm", headers=h.headers, json=body)
    assert response.status_code in {202, 409}, response.text
    if response.status_code == 202: drain(h)
    with h.factory() as session:
        assert session.scalar(select(func.count()).select_from(Transfer)) == 0
        assert session.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "TRANSFER")) == 0
        assert {row.id: (row.balance_minor, row.version) for row in session.scalars(select(Account))} == before
