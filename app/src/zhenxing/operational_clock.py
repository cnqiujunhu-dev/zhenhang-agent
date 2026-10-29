"""Timestamp new registered bank operations without moving a stored test clock."""
from sqlalchemy import select

from .access_models import LoginAccount
from .models import utcnow
from .simulation import as_utc, business_now


def registered_customer(session, sandbox_id, user_id=None):
    query = select(LoginAccount.id).where(LoginAccount.sandbox_id == sandbox_id,
        LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"}))
    if user_id is not None:
        query = query.where(LoginAccount.user_id == user_id)
    return session.scalar(query.limit(1)) is not None


def posting_now(session, sandbox_id, user_id=None, *, controlled=False):
    """Only new records use this clock. Never rewrite past timestamps or grants."""
    return as_utc(utcnow()) if not controlled and registered_customer(session, sandbox_id, user_id) else business_now(session, sandbox_id)
