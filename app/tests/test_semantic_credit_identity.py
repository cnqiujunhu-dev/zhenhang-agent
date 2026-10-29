"""Completed, genuinely confirmed credit writes can select fresh READs only."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi, choice, drain, send, source_change
from test_semantic_upgraded_scope_integration import approve, entity_change, owner, requested
from zhenxing.access_models import LoginAccount
from zhenxing.contracts import DomainError
from zhenxing.conversation_models import ChatThread, ChatTurn
from zhenxing.credit_bootstrap import initialize_customer_payment_services
from zhenxing.credit_models import (CreditAccount, CreditCard, CreditCurrencyBalance, CreditFeeAllocation,
    CreditFeeCharge, CreditInstallment, CreditObligation, CreditOperation, CreditRepayment, CreditRepaymentAllocation,
    CreditStatement, PaymentJournal, PaymentLedgerEntry, PaymentRecord, PaymentInstrumentBinding)
from zhenxing.credit_service import credit_view
from zhenxing.models import Account, Card, Confirmation, Task, ToolCall
from zhenxing.payment_instruments import post_merchant_payment, resolve_source_instrument
from zhenxing.runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord
from zhenxing.semantic_credit_identity import KIND, card_identities, checked_identity
from zhenxing.services import Principal


def _setup(h):
    h.state["allow_isolated_confirmations"] = True
    with h.factory.begin() as session:
        actor = owner(session)
        initialize_customer_payment_services(session, actor.sandbox_id, actor.user_id, adult_eligible=True,
            eligibility_source="ISOLATED_CONFIRMED_ADULT_PRECONDITION")
        card = session.scalar(select(CreditCard).where(CreditCard.user_id == actor.user_id))
        credit = resolve_source_instrument(session, actor, "credit:" + card.id, purpose="MERCHANT")
        debit = resolve_source_instrument(session, actor, purpose="REPAYMENT")
        payment = post_merchant_payment(session, actor, instrument_snapshot=credit, amount_minor=59900,
            merchant_ref="isolated-existing-purchase", operation_key="preexisting-credit-identity-purchase")
        return {"card_id": card.id, "number": card.card_number, "debit": debit["card_number"],
            "payment_id": payment["payment_id"], "facility_id": card.credit_account_id}


def _repay(h, data, *, confirm=True):
    text = f"用{data['debit']}向{data['number']}信用卡还款10元。"
    trace = requested(h, text, "CREDIT_REPAY", "credit-identity-repay", changes=[
        source_change(text, field, literal) for field, literal in (
            ("source_card", data["debit"]), ("card_reference", data["number"]), ("amount_minor", "10元"))])
    return approve(h, trace) if confirm else trace


def _finance(session):
    return {model.__tablename__: sorted([tuple(deepcopy(getattr(row, column.name)) for column in model.__table__.columns)
        for row in session.scalars(select(model))], key=repr)
        for model in (Account, Card, CreditCard, CreditAccount, CreditCurrencyBalance, PaymentInstrumentBinding,
            PaymentRecord, PaymentJournal, PaymentLedgerEntry, CreditOperation, CreditRepayment,
            CreditRepaymentAllocation, CreditFeeAllocation, CreditFeeCharge, CreditInstallment, CreditObligation, CreditStatement)}


def _binding(session, h, parent_id):
    actor = owner(session); thread = session.get(ChatThread, h.thread_id); parent = session.get(Task, parent_id)
    values = card_identities(session, actor, thread, parent, thread.next_sequence)
    assert len(values) == 1
    return actor, thread, parent, values[0]


def _select(prepared, text, source_kind, *, kind="CREDIT_VIEW", explicit=True):
    frames = [frame for frame in prepared["frames"] if any(action["kind"] == source_kind for action in frame["actions"])]
    assert frames
    offered_kind = 'COMPLETED_REPAYMENT_TARGET_IDENTITY' if source_kind == 'CREDIT_REPAY' else KIND
    source = next(row for row in prepared["entities"] if row.get("kind") == offered_kind and row.get("frame_ref") == frames[0]["frame_ref"])
    assert source["domain"] == "CREDIT" and source["read_kind"] == "CREDIT_VIEW"
    assert source["allowed_fields"] == ["card_reference"] and source["available_aspects"] == ["BALANCE"]
    assert "amount_minor" not in source and "available_base_credit_minor" not in source
    selected = source_change(text, "card_reference", "这张信用卡")
    selected["entity_ref"] = source["entity_ref"]
    return choice("REQUEST", action_kinds=[kind], dependencies=[[]], entity_refs=[source["entity_ref"]],
        changes=[selected] if explicit else [], question_aspects=["BALANCE"])


def _fresh(h, data, source_kind, consumer, key):
    with h.factory() as session:
        before = _finance(session)
        expected = credit_view(session, owner(session), "credit:" + data["card_id"])["cards"][0]
        confirmations = len(list(session.scalars(select(Confirmation))))
        calls = len(list(session.scalars(select(ToolCall))))
    text = "现在这张信用卡还有欠款吗？可用额度恢复了吗？"
    trace = requested(h, text, consumer, key,
        factory=lambda prepared: _select(prepared, text, source_kind, kind=consumer))
    assert trace["state"] == "SUCCEEDED" and trace["pending_confirmation"] is None, trace
    result = trace["nodes"][0]["result"]
    assert result["query"]["card_reference"] == "credit:" + data["card_id"]
    assert result["bank_state_changed"] is False and result["action"] == consumer
    if consumer == "CREDIT_VIEW":
        card = result["cards"][0]
        assert card["card_id"] == data["card_id"]
        assert card["balances"] == expected["balances"]
        assert card["available_base_credit_minor"] == expected["available_base_credit_minor"]
        assert card["used_base_exposure_minor"] == expected["used_base_exposure_minor"]
    else:
        actual = {row["currency"]: row["outstanding_minor"] for row in result["statements_by_currency"]}
        assert actual == {row["currency"]: row["outstanding_minor"] for row in expected["balances"]}
    with h.factory() as session:
        assert _finance(session) == before
        assert len(list(session.scalars(select(Confirmation)))) == confirmations
        assert len(list(session.scalars(select(ToolCall)))) == calls
    return trace


def test_actual_repay_installment_and_settlement_offer_only_identity_then_read_current_debt_and_limit(cloud_asgi):
    h = cloud_asgi; data = _setup(h)
    repayment = _repay(h, data)
    for consumer in ("CREDIT_VIEW", "CREDIT_STATEMENT"):
        _fresh(h, data, "CREDIT_REPAY", consumer, "after-repay-" + consumer)
    text = "把这笔消费办理3期分期。"
    def installment(prepared):
        selected = entity_change(prepared, text, "payment_id", "这笔消费", "CREDIT_STATEMENT", "credit_payment")
        return choice("REQUEST", action_kinds=["CREDIT_INSTALLMENT"], dependencies=[[]], entity_refs=[selected["entity_ref"]],
            changes=[selected, source_change(text, "periods", "3期")])
    installed = approve(h, requested(h, text, "CREDIT_INSTALLMENT", "credit-identity-installment", factory=installment))
    for consumer in ("CREDIT_VIEW", "CREDIT_STATEMENT"):
        _fresh(h, data, "CREDIT_INSTALLMENT", consumer, "after-installment-" + consumer)
    text = f"用{data['debit']}结清这笔分期。"
    def settle(prepared):
        selected = entity_change(prepared, text, "installment_id", "这笔分期", "CREDIT_STATEMENT", "credit_installment")
        return choice("REQUEST", action_kinds=["CREDIT_SETTLE_INSTALLMENT"], dependencies=[[]], entity_refs=[selected["entity_ref"]],
            changes=[selected, source_change(text, "source_card", data["debit"])])
    settled = approve(h, requested(h, text, "CREDIT_SETTLE_INSTALLMENT", "credit-identity-settle", factory=settle))
    for consumer in ("CREDIT_VIEW", "CREDIT_STATEMENT"):
        read = _fresh(h, data, "CREDIT_SETTLE_INSTALLMENT", consumer, "after-settle-" + consumer)
        if consumer == "CREDIT_VIEW":
            card = read["nodes"][0]["result"]["cards"][0]
            assert all(row["outstanding_minor"] == 0 for row in card["balances"])
            assert card["available_base_credit_minor"] == card["base_credit_limit_minor"]
    with h.factory.begin() as session:
        actor, thread, parent, stable = _binding(session, h, settled["task_id"])
        post_merchant_payment(session, actor, instrument_snapshot=resolve_source_instrument(session, actor,
            "credit:" + data["card_id"], purpose="MERCHANT"), amount_minor=500,
            merchant_ref="isolated-later-legitimate-purchase", operation_key="later-credit-identity-purchase")
        assert checked_identity(session, actor, thread, stable, "card_reference", "CREDIT_VIEW") == "credit:" + data["card_id"]
    current = _fresh(h, data, "CREDIT_SETTLE_INSTALLMENT", "CREDIT_VIEW", "after-later-real-purchase")
    assert current["nodes"][0]["result"]["cards"][0]["balances"][0]["outstanding_minor"] == 500
    with h.factory() as session:
        for source in (repayment, installed, settled):
            actor, thread, parent, binding = _binding(session, h, source["task_id"])
            forbidden = {"facts", "amount_minor", "currency", "source_card", "source_snapshot", "card_number", "card_last4",
                "balances", "status", "version", "available_base_credit_minor", "base_credit_limit_minor", "plan_json", "args"}
            def keys(value):
                if isinstance(value, dict): return set(value).union(*(keys(item) for item in value.values()))
                if isinstance(value, list): return set().union(*(keys(item) for item in value))
                return set()
            assert not forbidden.intersection(keys(binding))
            assert checked_identity(session, actor, thread, binding, "card_reference", "CREDIT_STATEMENT") == "credit:" + data["card_id"]


def test_source_before_confirmation_foreign_scope_tamper_and_write_consumers_are_rejected(cloud_asgi):
    h = cloud_asgi; data = _setup(h); pending = _repay(h, data, confirm=False)
    with h.factory() as session:
        thread = session.get(ChatThread, h.thread_id)
        assert card_identities(session, owner(session), thread, session.get(Task, pending["task_id"]), thread.next_sequence) == []
    completed = approve(h, pending)
    with h.factory.begin() as session:
        actor, thread, parent, entry = _binding(session, h, completed["task_id"])
        other = session.scalar(select(LoginAccount).where(LoginAccount.username == "quyuwei"))
        assert card_identities(session, Principal(other.user_id, other.sandbox_id), thread, parent, thread.next_sequence) == []
        wrong_thread = ChatThread(user_id=actor.user_id, sandbox_id=actor.sandbox_id, request_hash="another-credit-thread")
        session.add(wrong_thread); session.flush()
        assert card_identities(session, actor, wrong_thread, parent, thread.next_sequence) == []
        source = session.get(ChatTurn, entry["source_turn_id"])
        assert card_identities(session, actor, thread, parent, source.sequence) == []
        for kind in ("CREDIT_REPAY", "CREDIT_INSTALLMENT", "CREDIT_SETTLE_INSTALLMENT", "CREDIT_APPLY", "CARD_LOCK", "TRANSFER", "MERCHANT_PURCHASE"):
            with pytest.raises(DomainError): checked_identity(session, actor, thread, entry, "card_reference", kind)
        for field in ("card_last4", "card_number", "source_card", "amount_minor"):
            with pytest.raises(DomainError): checked_identity(session, actor, thread, entry, field, "CREDIT_VIEW")
        for target in ("parent_state", "native", "source_turn", "snapshot", "operation", "child_link", "child_hash",
                "call_hash", "call_result", "confirmation", "confirmation_target", "journal", "card_owner", "facility", "actor_version"):
            savepoint = session.begin_nested()
            try:
                child = session.get(Task, entry["child_task_id"])
                call = session.scalar(select(ToolCall).where(ToolCall.task_id == child.id))
                confirmation = session.get(Confirmation, entry["confirmation_id"])
                if target == "parent_state": session.get(Task, entry["task_id"]).state = "UNKNOWN"
                elif target == "native": session.get(RuntimeRecord, entry["task_id"]).metadata_json = {}
                elif target == "source_turn": session.get(ChatTurn, entry["source_turn_id"]).status = "FAILED"
                elif target == "snapshot": session.get(PlanSnapshot, entry["snapshot_id"]).plan_hash = "0" * 64
                elif target == "operation": session.get(RuntimeOperation, entry["operation_id"]).active = 0
                elif target == "child_link": session.get(RuntimeRecord, child.id).root_task_id = child.id
                elif target == "child_hash": child.plan_hash = "0" * 64
                elif target == "call_hash": call.request_hash = "0" * 64
                elif target == "call_result": call.result_json = {}
                elif target == "confirmation": confirmation.status = "PENDING"
                elif target == "confirmation_target": confirmation.target_id = data["card_id"] + "foreign"
                elif target == "journal":
                    row = session.scalar(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == child.result_json["journal_id"]))
                    row.delta_minor += 1
                elif target == "card_owner": session.get(CreditCard, data["card_id"]).user_id = other.user_id
                elif target == "facility": session.get(CreditCard, data["card_id"]).credit_account_id = "foreign-facility"
                else: session.scalar(select(LoginAccount).where(LoginAccount.user_id == actor.user_id)).version += 1
                try:
                    session.flush()
                except DomainError as error:
                    # Card ownership/facility are immutable before this READ
                    # bridge runs. Preserve and assert that stronger boundary;
                    # do not disable it to manufacture a corrupted fixture.
                    assert target in {"card_owner", "facility"} and error.code == "CREDIT_POSTING_IMMUTABLE"
                else:
                    with pytest.raises(DomainError): checked_identity(session, actor, thread, entry, "card_reference", "CREDIT_VIEW")
            finally:
                savepoint.rollback(); session.expire_all()
        assert checked_identity(session, actor, session.get(ChatThread, h.thread_id), entry,
            "card_reference", "CREDIT_VIEW") == "credit:" + data["card_id"]
    with h.factory() as session: before = _finance(session)
    text = "现在这张信用卡还有欠款吗？"
    for form in ("unconsumed_ref", "literal_tail", "write"):
        def decision(prepared):
            result = _select(prepared, text, "CREDIT_REPAY", explicit=form != "unconsumed_ref")
            if form == "literal_tail":
                result["entity_refs"] = []; result["changes"] = [source_change(text, "card_last4", "这张信用卡")]
            elif form == "write": result["action_kinds"] = ["CREDIT_REPAY"]
            return result
        h.state["decision_factory"] = decision
        turn = send(h, text, "denied-credit-identity-" + form)
        assert turn.task_id is None and turn.mode == "NEED_CONTEXT", turn.diagnostics_json
    with h.factory() as session: assert _finance(session) == before
