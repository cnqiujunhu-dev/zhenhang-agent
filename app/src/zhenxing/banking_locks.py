"""Shared owner-before-cash ordering for multi-party monetary operations."""
from sqlalchemy import select

from .contracts import DomainError
from .models import User


def lock_financial_users(session, owners):
    """Call after sorted registry locks, before any cash/card/wallet row lock.

    Credit and central cash paths serialize on User first. A transfer must lock
    every involved owner in one stable order before it holds either cash row,
    including registry-less isolated banks and payments between two customers.
    """
    identities = set(owners)
    rows = list(session.scalars(select(User).where(User.id.in_({user_id for _, user_id in identities}))
        .order_by(User.id).with_for_update().execution_options(populate_existing=True)))
    if {(row.sandbox_id, row.id) for row in rows} != identities:
        raise DomainError("BANKING_OWNER_CHANGED", "收付主体已变化，未执行资金操作。", 409)
    return rows
