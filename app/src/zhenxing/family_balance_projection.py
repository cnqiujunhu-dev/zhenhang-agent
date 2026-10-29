"""Cash-only projection after the caller verifies current READ_BALANCE grants."""
from sqlalchemy import select

from .contracts import DomainError
from .models import Account, User


def authorized_owner_cash_balances(session, sandbox_id, authorized_owner_ids):
    """No permission creation, card join, credit read, lock or financial write.

    Only family_view passes IDs from its valid incoming READ_BALANCE grants.
    Keep the legacy public row shape; balance_minor has always meant CNY here.
    """
    result=[]
    for owner_id in sorted(set(authorized_owner_ids)):
        owner=session.get(User,owner_id)
        accounts=list(session.scalars(select(Account).where(Account.sandbox_id==sandbox_id,
            Account.user_id==owner_id,Account.currency=='CNY').order_by(Account.id)))
        if owner is None or owner.sandbox_id!=sandbox_id or not accounts:
            raise DomainError('ACCOUNT_NOT_FOUND','未找到当前授权范围内的人民币现金账户。',404)
        result.append({'owner_name':owner.display_name,'balance_minor':sum(row.balance_minor for row in accounts)})
    return result
