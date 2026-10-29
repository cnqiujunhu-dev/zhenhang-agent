from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import DomainError
from .models import Account, Card, Transaction
from .simulation import business_now


def record_cash_transaction(session: Session, *, sandbox_id: str, account_id: str,
                            delta_minor: int, merchant: str, category: str,
                            source_kind: str, operation_ref: str, controlled_clock: bool = False,
                            card_id: str | None = None) -> Transaction:
    """Write the statement representation of an already posted ledger operation."""
    if isinstance(delta_minor, bool) or not isinstance(delta_minor, int) or delta_minor == 0:
        raise DomainError("INVALID_CASH_POSTING", "现金流水金额无效", 500)
    if source_kind not in {"TRANSFER", "INVESTMENT", "AA_COLLECTION", "OWN_ACCOUNT_TRANSFER"} or not operation_ref:
        raise DomainError("INVALID_CASH_SOURCE", "现金流水来源无效", 500)
    account = session.scalar(select(Account).where(Account.id == account_id, Account.sandbox_id == sandbox_id))
    if account is None:
        raise DomainError("CASH_ACCOUNT_SCOPE_MISMATCH", "现金镜像账户不属于该账套", 403)
    if card_id is not None and (not isinstance(card_id, str) or not card_id):
        raise DomainError("CASH_CARD_SCOPE_MISMATCH", "现金流水卡片归属无效", 403)
    existing = session.scalar(select(Transaction).where(
        Transaction.sandbox_id == sandbox_id, Transaction.operation_ref == operation_ref))
    if existing:
        if existing.account_id != account_id or existing.amount_minor != delta_minor or existing.source_kind != source_kind \
                or existing.card_id != card_id:
            raise DomainError("CASH_POSTING_CONFLICT", "同一操作的现金流水内容不一致", 409)
        return existing
    if card_id is not None:
        card = session.get(Card, card_id)
        if card is None or (card.sandbox_id, card.user_id, card.account_id) != (sandbox_id, account.user_id, account_id):
            raise DomainError("CASH_CARD_SCOPE_MISMATCH", "流水卡片与已记账现金账户不一致", 403)
    from .operational_clock import posting_now
    result = Transaction(sandbox_id=sandbox_id, account_id=account_id, card_id=card_id,
                         occurred_at=posting_now(session, sandbox_id, account.user_id, controlled=controlled_clock), amount_minor=delta_minor,
                         merchant=merchant[:200], category=category, flag="NORMAL",
                         source_kind=source_kind, operation_ref=operation_ref)
    session.add(result)
    session.flush()
    return result
