"""Disposable bank tests: one daily allowance across cards and transfer paths."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from test_internal_banking import internal_bank, receive_payee
from zhenxing.banking_extension_models import TransferBlacklist, TransferDailyUsage, TransferRiskPosting
from zhenxing.contracts import DomainError
from zhenxing.extensions_models import FraudLabel
from zhenxing.internal_banking import commit_transfer_money, recipient_scope
from zhenxing.models import Account, LedgerEntry, Transfer, Payee
from zhenxing.transfer_risk import DAILY_LIMIT_MINOR, check_transfer_limits


def post(db, source, payee, amount, key):
    return commit_transfer_money(db, source, payee, amount, "", key, recipient_scope(db, payee))


def test_daily_quota_shared_by_original_account_and_replay_never_consumes_twice(internal_bank):
    db, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(db, sender)
    source.balance_minor = 20_000_000
    db.commit()
    preview = check_transfer_limits(db, source, payee, 4_000_000)
    assert preview["remaining_minor"] == DAILY_LIMIT_MINOR and preview["high_value"]
    assert db.scalar(select(func.count()).select_from(TransferDailyUsage)) == 0
    first = post(db, source, payee, 4_000_000, "shared-one")
    post(db, source, payee, 4_000_000, "shared-two")
    assert post(db, source, payee, 4_000_000, "shared-one").id == first.id
    with pytest.raises(DomainError) as error:
        post(db, source, payee, 2_000_001, "split-over-limit")
    assert error.value.code == "TRANSFER_DAILY_LIMIT_EXCEEDED"
    with pytest.raises(DomainError,match='大额转账'):
        post(db,source,payee,2_000_000,'same-recipient-third')
    other=db.scalar(select(Payee).where(Payee.user_id==sender.user_id,Payee.display_name=='房东'))
    assert other is not None
    post(db, source, other, 2_000_000, "remaining-exact")
    usage = db.scalar(select(TransferDailyUsage))
    assert (usage.posted_minor, usage.posted_count) == (DAILY_LIMIT_MINOR, 3)
    assert db.scalar(select(func.count()).select_from(TransferRiskPosting)) == 3
    assert db.scalar(select(func.sum(LedgerEntry.delta_minor))) == 0


def test_quota_and_money_rollback_together_on_outer_failure(internal_bank):
    db, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(db, sender)
    db.commit()
    balances = source.balance_minor, target.balance_minor
    post(db, source, payee, 100, "must-rollback")
    db.rollback()
    db.refresh(source); db.refresh(target)
    assert (source.balance_minor, target.balance_minor) == balances
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0
    assert db.scalar(select(func.count()).select_from(TransferRiskPosting)) == 0
    assert db.scalar(select(func.count()).select_from(TransferDailyUsage)) == 0


@pytest.mark.parametrize("kind", ["blacklist", "high", "unknown"])
def test_risk_sources_block_even_direct_original_money_entry(internal_bank, kind):
    db, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(db, sender)
    if kind == "blacklist":
        db.add(TransferBlacklist(sandbox_id=sender.sandbox_id, user_id=sender.user_id,
                                 target_ref="BANK:" + receiver.id))
    else:
        db.add(FraudLabel(sandbox_id=sender.sandbox_id, user_id=sender.user_id, payee_id=payee.id,
                         risk_level=kind.upper(), reason_code="ISOLATED_RISK_TEST"))
    db.flush()
    before = source.balance_minor, target.balance_minor
    with pytest.raises(DomainError) as error:
        post(db, source, payee, 100, "blocked-original")
    assert error.value.code == ("TRANSFER_RECIPIENT_REVIEW_REQUIRED" if kind == "unknown" else "TRANSFER_RECIPIENT_BLOCKED")
    assert (source.balance_minor, target.balance_minor) == before
    assert db.scalar(select(func.count()).select_from(Transfer)) == 0


def test_new_business_day_has_new_bucket_not_new_authority_or_money(internal_bank, monkeypatch):
    from zhenxing.operational_clock import posting_now
    db, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(db, sender)
    source.balance_minor = 25_000_000
    clock = posting_now(db, source.sandbox_id, source.user_id)
    post(db, source, payee, 5_000_000, "day-one-a")
    post(db, source, payee, 5_000_000, "day-one-b")
    monkeypatch.setattr("zhenxing.transfer_risk.posting_now", lambda *args: clock + timedelta(days=1))
    post(db, source, payee, 100, "day-two")
    assert db.scalar(select(func.count()).select_from(TransferDailyUsage)) == 2
    assert source.balance_minor == 14_999_900


def test_switching_independent_debit_accounts_cannot_reset_owner_daily_quota(internal_bank):
    db, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(db, sender)
    source.balance_minor = 20_000_000
    second = Account(sandbox_id=source.sandbox_id, user_id=source.user_id, name="独立第二借记账户", currency="CNY", balance_minor=20_000_000)
    db.add(second); db.flush()
    post(db, source, payee, 5_000_000, "card-account-one")
    post(db, second, payee, 5_000_000, "card-account-two")
    with pytest.raises(DomainError) as error:
        post(db, second, payee, 1, "split-one-more-cent")
    assert error.value.code == "TRANSFER_DAILY_LIMIT_EXCEEDED"
    assert db.scalar(select(func.count()).select_from(TransferDailyUsage)) == 1
    assert db.scalar(select(TransferDailyUsage)).posted_minor == DAILY_LIMIT_MINOR


def test_parallel_independent_accounts_share_one_bucket_and_post_only_within_limit(internal_bank):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.orm import Session
    db, sender, receiver, source, target, _ = internal_bank
    payee = receive_payee(db, sender)
    source.balance_minor = 20_000_000
    second = Account(sandbox_id=source.sandbox_id, user_id=source.user_id, name="独立第二借记账户", currency="CNY", balance_minor=20_000_000)
    db.add(second); db.flush()
    ids = source.id, second.id, payee.id
    engine = db.bind
    db.commit()
    def attempt(index):
        # SQLite has no row locks; this fixture explicitly serializes its
        # disposable write transactions. PostgreSQL's production path instead
        # uses the sorted LoginAccount + owner + daily-bucket FOR UPDATE locks.
        with Session(engine) as worker:
            worker.connection().exec_driver_sql("BEGIN IMMEDIATE")
            funding = worker.get(Account, ids[index % 2])
            from zhenxing.models import Payee
            recipient = worker.get(Payee, ids[2])
            try:
                post(worker, funding, recipient, 5_000_000, "concurrent-" + str(index))
                worker.commit()
                return "POSTED"
            except DomainError as error:
                worker.rollback()
                return error.code
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(attempt, range(3)))
    assert outcomes.count("POSTED") == 2 and outcomes.count("TRANSFER_DAILY_LIMIT_EXCEEDED") == 1
    db.expire_all()
    assert db.scalar(select(TransferDailyUsage)).posted_minor == DAILY_LIMIT_MINOR
    assert db.scalar(select(func.count()).select_from(Transfer)) == 2
