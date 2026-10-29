"""Independent money/identity tests; workflow confirmation is owned by root tests.

Only disposable SQLite is used. No formal DB, service or model is contacted.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from zhenxing.access_models import LoginAccount
from zhenxing.accounts import create_customer_account, password_hash
from zhenxing.contracts import DomainError
from zhenxing.database import Base
from zhenxing.extensions_models import CashflowGoal, GoalLedgerEntry
from zhenxing.internal_banking import (
    account_reference, commit_transfer_money, list_internal_recipients, lock_transfer_accounts,
    recipient_scope, resolve_transfer_payees, settlement_integrity,
)
from zhenxing.internal_banking_models import InternalPayeeBinding, InternalTransferSettlement
from zhenxing.models import Account, LedgerEntry, Payee, Sandbox, Transaction, Transfer, User
from zhenxing.reports import generate_bill_report
from zhenxing.schema import ensure_schema
from zhenxing.simulation import business_now, inspect_simulation


@pytest.fixture
def internal_bank(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'internal-bank.db').as_posix()}")
    ensure_schema(engine)
    with Session(engine, expire_on_commit=False) as db:
        sender = create_customer_account(db, "senderone", "sender88", seed=91)
        receiver = create_customer_account(db, "test", "test88", seed=92, role="DEV_TEST")
        sandbox = Sandbox()
        db.add(sandbox)
        db.flush()
        admin_user = User(sandbox_id=sandbox.id, display_name="admin")
        db.add(admin_user)
        db.flush()
        admin = LoginAccount(username="admin", user_id=admin_user.id, sandbox_id=sandbox.id,
                             role="ADMIN", password_hash=password_hash("admin88"))
        db.add(admin)
        db.commit()
        # These ledger/report tests deliberately exercise the September fixture.
        # Inject the trusted posting clock rather than restoring production's
        # obsolete bank-time fallback for new registered-customer operations.
        monkeypatch.setattr("zhenxing.operational_clock.utcnow", lambda: business_now(db, sender.sandbox_id))
        source = db.scalar(select(Account).where(Account.user_id == sender.user_id))
        target = db.scalar(select(Account).where(Account.user_id == receiver.user_id))
        yield db, sender, receiver, source, target, admin
    engine.dispose()


def receive_payee(db, sender):
    return resolve_transfer_payees(db, sender.sandbox_id, sender.user_id, "test")[0]


def rejected(code, operation):
    with pytest.raises(DomainError) as error:
        operation()
    assert error.value.code == code


def test_minimal_directory_server_binding_is_real_case_normalized_and_not_card4321(internal_bank):
    db, sender, receiver, source, target, admin = internal_bank
    directory = list_internal_recipients(db, sender.sandbox_id, sender.user_id)
    assert directory == [{"username": "test", "account_reference": account_reference(target.id), "transfer_channel": "INTERNAL_BANK"}]
    assert set(directory[0]) == {"username", "account_reference", "transfer_channel"}
    assert all(secret not in json.dumps(directory) for secret in ["balance", "user_id", "sandbox_id", "token", "password", target.id])
    before = db.scalar(select(func.count()).select_from(Payee).where(Payee.user_id == sender.user_id))
    first = resolve_transfer_payees(db, sender.sandbox_id, sender.user_id, " TeSt ")[0]
    second = receive_payee(db, sender)
    assert first.id == second.id and first.display_name == "test"
    assert db.scalar(select(func.count()).select_from(Payee).where(Payee.user_id == sender.user_id)) == before + 1
    binding = db.get(InternalPayeeBinding, first.id)
    assert (binding.recipient_login_account_id, binding.recipient_bank_account_id, binding.username_snapshot) == (receiver.id, target.id, "test")
    assert recipient_scope(db, first) == {"transfer_channel": "INTERNAL_BANK", "recipient_username": "test", "recipient_account_id": target.id}
    assert list_internal_recipients(db, sender.sandbox_id, sender.user_id, query="nonexisting") == []
    rejected("NAMED_BANK_ACCOUNT_REQUIRED", lambda: list_internal_recipients(db, admin.sandbox_id, admin.user_id))
    rejected("NAMED_BANK_ACCOUNT_REQUIRED", lambda: list_internal_recipients(db, sender.sandbox_id, receiver.user_id))


def test_self_admin_disabled_unknown_and_external_same_name_do_not_auto_map_or_fallback(internal_bank):
    db, sender, receiver, source, target, admin = internal_bank
    before = source.balance_minor, target.balance_minor
    for name, code in [("senderone", "SELF_TRANSFER_FORBIDDEN"), ("admin", "RECIPIENT_ACCOUNT_UNAVAILABLE"),
                       ("missingperson", "RECIPIENT_ACCOUNT_UNAVAILABLE")]:
        rejected(code, lambda name=name: resolve_transfer_payees(db, sender.sandbox_id, sender.user_id, name))
    receiver.status = "DISABLED"
    rejected("RECIPIENT_ACCOUNT_UNAVAILABLE", lambda: receive_payee(db, sender))
    receiver.status = "ACTIVE"
    external = Payee(sandbox_id=sender.sandbox_id, user_id=sender.user_id, display_name="test", account_last4="9999")
    db.add(external)
    db.flush()
    rejected("RECIPIENT_NAME_CONFLICT", lambda: receive_payee(db, sender))
    assert db.get(InternalPayeeBinding, external.id) is None
    assert db.scalar(select(func.count()).select_from(InternalPayeeBinding)) == 0
    assert (source.balance_minor, target.balance_minor) == before


def test_internal_commit_four_scoped_legs_two_statements_conserves_both_banks_not_income(internal_bank):
    db, sender, receiver, source, target, _admin = internal_bank
    payee = receive_payee(db, sender)
    before_source, before_target = source.balance_minor, target.balance_minor
    source_version, target_version = source.version, target.version
    source_report = generate_bill_report(db, sender.sandbox_id, sender.user_id, calendar_time=business_now(db, sender.sandbox_id))["summary"]
    target_report = generate_bill_report(db, receiver.sandbox_id, receiver.user_id, calendar_time=business_now(db, receiver.sandbox_id))["summary"]
    transfer = commit_transfer_money(db, source, payee, 12345, "own confirmed context", "review-internal-op", recipient_scope(db, payee))
    db.commit()
    assert source.balance_minor == before_source - 12345 and target.balance_minor == before_target + 12345
    assert source.balance_minor + target.balance_minor == before_source + before_target
    assert (source.version, target.version) == (source_version + 1, target_version + 1)
    settlement = db.get(InternalTransferSettlement, transfer.id)
    assert (settlement.sender_user_id, settlement.receiver_user_id, settlement.receiver_account_id) == (sender.user_id, receiver.user_id, target.id)
    ledger = list(db.scalars(select(LedgerEntry).where(LedgerEntry.transfer_id == transfer.id)))
    assert len(ledger) == 4 and sum(row.delta_minor for row in ledger) == 0
    for sid in (sender.sandbox_id, receiver.sandbox_id):
        assert sum(row.delta_minor for row in ledger if row.sandbox_id == sid) == 0
        assert inspect_simulation(db, sid)["reconciliation"]["balanced"]
    statement = list(db.scalars(select(Transaction).where(Transaction.operation_ref == "transfer:" + transfer.id)))
    assert {(row.sandbox_id, row.account_id, row.amount_minor) for row in statement} == {
        (sender.sandbox_id, source.id, -12345), (receiver.sandbox_id, target.id, 12345)}
    assert settlement_integrity(db, transfer)["balanced"]
    after_source = generate_bill_report(db, sender.sandbox_id, sender.user_id, calendar_time=business_now(db, sender.sandbox_id))["summary"]
    after_target = generate_bill_report(db, receiver.sandbox_id, receiver.user_id, calendar_time=business_now(db, receiver.sandbox_id))["summary"]
    assert after_source["movement_out_minor"] == source_report["movement_out_minor"] + 12345
    assert after_target["movement_in_minor"] == target_report["movement_in_minor"] + 12345
    for before, after in ((source_report, after_source), (target_report, after_target)):
        assert (before["income_minor"], before["spending_minor"], before["refund_minor"]) == (after["income_minor"], after["spending_minor"], after["refund_minor"])


def test_original_key_stable_receipt_replay_never_recredits_even_after_receiver_disabled(internal_bank):
    db, sender, receiver, source, target, _admin = internal_bank
    payee = receive_payee(db, sender)
    scope = recipient_scope(db, payee)
    transfer = commit_transfer_money(db, source, payee, 10000, "memo", "original-stable-key", scope)
    db.commit()
    after = source.balance_minor, target.balance_minor, source.version, target.version
    receiver.status = "DISABLED"
    db.commit()
    duplicate = commit_transfer_money(db, source, payee, 10000, "memo", "original-stable-key", scope)
    assert duplicate.id == transfer.id
    assert (source.balance_minor, target.balance_minor, source.version, target.version) == after
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.transfer_id == transfer.id)) == 4
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.operation_ref == "transfer:" + transfer.id)) == 2
    rejected("TRANSFER_IDEMPOTENCY_CONFLICT", lambda: commit_transfer_money(db, source, payee, 10001, "memo", "original-stable-key", scope))
    rejected("RECIPIENT_ACCOUNT_UNAVAILABLE", lambda: commit_transfer_money(db, source, payee, 10000, "memo", "new-key", scope))
    assert settlement_integrity(db, transfer)["balanced"]


def test_scope_fk_tampering_and_reserved_cash_never_post_either_bank(internal_bank):
    db, sender, receiver, source, target, _admin = internal_bank
    payee = receive_payee(db, sender)
    scope = recipient_scope(db, payee)
    before = source.balance_minor, target.balance_minor
    changed_scope = {**scope, "recipient_account_id": source.id}
    rejected("RECIPIENT_SCOPE_CHANGED", lambda: commit_transfer_money(db, source, payee, 10000, "memo", "bad-scope", changed_scope))
    binding = db.get(InternalPayeeBinding, payee.id)
    binding.recipient_bank_account_id = source.id
    rejected("RECIPIENT_BINDING_CHANGED", lambda: commit_transfer_money(db, source, payee, 10000, "memo", "bad-fk", scope))
    binding.recipient_bank_account_id = target.id
    goal = CashflowGoal(sandbox_id=sender.sandbox_id, user_id=sender.user_id, account_id=source.id,
                        name="review reserve all", target_minor=source.balance_minor, reserved_minor=source.balance_minor)
    db.add(goal)
    db.flush()
    db.add_all([GoalLedgerEntry(sandbox_id=sender.sandbox_id, user_id=sender.user_id, goal_id=goal.id,
                               operation_key="review-reserve", account_ref="GOAL:" + goal.id, delta_minor=source.balance_minor),
                GoalLedgerEntry(sandbox_id=sender.sandbox_id, user_id=sender.user_id, goal_id=goal.id,
                               operation_key="review-reserve", account_ref="AVAILABLE:" + source.id, delta_minor=-source.balance_minor)])
    rejected("INSUFFICIENT_FUNDS", lambda: commit_transfer_money(db, source, payee, 10000, "memo", "bad-reserve", scope))
    assert (source.balance_minor, target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
    assert db.scalar(select(func.count()).select_from(InternalTransferSettlement)) == 0


def test_mid_posting_exception_rolls_back_both_banks_and_external_legacy_remains_two_legs(internal_bank, monkeypatch):
    db, sender, receiver, source, target, _admin = internal_bank
    payee = receive_payee(db, sender)
    db.commit()
    before = source.balance_minor, target.balance_minor
    scope = recipient_scope(db, payee)
    import zhenxing.internal_banking as module
    original = module.record_cash_transaction

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic interruption after first statement")

    monkeypatch.setattr(module, "record_cash_transaction", interrupted)
    with pytest.raises(RuntimeError):
        commit_transfer_money(db, source, payee, 10000, "memo", "interrupted-key", scope)
    db.rollback()
    assert (source.balance_minor, target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0
    assert db.scalar(select(func.count()).select_from(InternalTransferSettlement)) == 0
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "TRANSFER")) == 0

    def interrupted_domain(*args, **kwargs):
        original(*args, **kwargs)
        raise DomainError("SYNTHETIC_MIRROR_FAILURE", "synthetic first-mirror failure", 409)

    monkeypatch.setattr(module, "record_cash_transaction", interrupted_domain)
    rejected("SYNTHETIC_MIRROR_FAILURE", lambda: commit_transfer_money(db, source, payee, 10000, "memo", "caught-domain-key", scope))
    # Model the scheduler's catch -> mark failed -> outer commit. No manual
    # rollback here: the helper's money savepoint must already have erased cash.
    db.commit()
    assert (source.balance_minor, target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0
    assert db.scalar(select(func.count()).select_from(InternalTransferSettlement)) == 0
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.source_kind == "TRANSFER")) == 0
    monkeypatch.setattr(module, "record_cash_transaction", original)
    external = resolve_transfer_payees(db, sender.sandbox_id, sender.user_id, "房东")[0]
    assert recipient_scope(db, external) == {"transfer_channel": "EXTERNAL_SIM", "recipient_username": None, "recipient_account_id": None}
    transfer = commit_transfer_money(db, source, external, 10000, "legacy external", "legacy-room-payee", recipient_scope(db, external))
    db.commit()
    assert target.balance_minor == before[1] and source.balance_minor == before[0] - 10000
    assert db.get(InternalTransferSettlement, transfer.id) is None
    assert settlement_integrity(db, transfer)["ledger_entry_count"] == 2
    assert settlement_integrity(db, transfer)["statement_count"] == 1
    assert settlement_integrity(db, transfer)["balanced"]


def test_postgresql_compiled_row_locks_order_both_logins_then_users_before_both_bank_accounts(internal_bank):
    db, sender, receiver, source, target, _admin = internal_bank
    captured = []

    def observe(execution):
        statement = execution.statement
        if getattr(statement, "_for_update_arg", None) is not None:
            captured.append(str(statement.compile(dialect=postgresql.dialect())))

    event.listen(db, "do_orm_execute", observe)
    try:
        payee = receive_payee(db, sender)
        assert lock_transfer_accounts(db, sender.sandbox_id, sender.user_id, source.id, payee).id == source.id
    finally:
        event.remove(db, "do_orm_execute", observe)
    assert len(captured) == 4
    # Initial carrier registration must not lock only sender before FK insertion.
    assert "ORDER BY login_accounts.id FOR UPDATE" in captured[0]
    assert "ORDER BY login_accounts.id FOR UPDATE" in captured[1]
    assert "ORDER BY users.id FOR UPDATE" in captured[2]
    assert "ORDER BY accounts.id FOR UPDATE" in captured[3]
    assert "login_accounts.id IN" in captured[0] and "login_accounts.id IN" in captured[1]
    assert "users.id IN" in captured[2] and "accounts.id IN" in captured[3]


def test_successful_money_savepoint_still_rolls_back_with_outer_workflow_failure(internal_bank):
    db, sender, receiver, source, target, _admin = internal_bank
    payee = receive_payee(db, sender)
    db.commit()
    before = source.balance_minor, target.balance_minor
    commit_transfer_money(db, source, payee, 10000, "memo", "outer-failure-key", recipient_scope(db, payee))
    # A failure in caller audit/outbox/state is still outside this released
    # savepoint but inside the one complete bank+workflow transaction.
    db.rollback()
    assert (source.balance_minor, target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0
    assert db.scalar(select(func.count()).select_from(InternalTransferSettlement)) == 0
