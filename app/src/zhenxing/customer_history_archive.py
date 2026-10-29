"""Reversible customer-list archive. Never deletes ledger rows or grants authority."""
from sqlalchemy import select, true
from .access_models import AccessAudit, LoginAccount
from .authentication import as_utc
from .contracts import DomainError

ARCHIVE = "CUSTOMER_HISTORY_ARCHIVED"
RESTORE = "CUSTOMER_HISTORY_RESTORED"


def history_cutoff(session, principal):
    row = session.execute(select(AccessAudit.action, AccessAudit.created_at)
        .join(LoginAccount, LoginAccount.id == AccessAudit.target_account_id)
        .where(LoginAccount.user_id == principal.user_id, LoginAccount.sandbox_id == principal.sandbox_id,
               AccessAudit.action.in_([ARCHIVE, RESTORE]))
        .order_by(AccessAudit.created_at.desc(), AccessAudit.id.desc()).limit(1)).first()
    return as_utc(row.created_at) if row and row.action == ARCHIVE else None


def visible_history(session, principal, created_at):
    cutoff = history_cutoff(session, principal)
    return created_at > cutoff if cutoff else true()


def require_visible_history(session, principal, model, record_id):
    cutoff = history_cutoff(session, principal)
    if cutoff is None:
        return
    created = session.scalar(select(model.created_at).where(model.id == record_id,
        model.user_id == principal.user_id, model.sandbox_id == principal.sandbox_id))
    if created is not None and as_utc(created) <= cutoff:
        raise DomainError("CUSTOMER_RECORD_ARCHIVED", "记录已归档，请发起新会话。", 404)
