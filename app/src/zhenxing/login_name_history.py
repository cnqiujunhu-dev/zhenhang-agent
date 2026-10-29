"""Resolve historical login labels only through explicit account rename audits.

For posted-receipt display/integrity, never new payment authorization.
"""
from sqlalchemy import select
from .access_models import AccessAudit


def recorded_login_name_matches(session, login, historical_name):
    if login is None or not isinstance(historical_name,str):return False
    if login.username==historical_name:return True
    name=historical_name
    events=session.scalars(select(AccessAudit).where(AccessAudit.target_account_id==login.id,
        AccessAudit.action=='LOGIN_USERNAME_CHANGED').order_by(AccessAudit.created_at,AccessAudit.id))
    for event in events:
        facts=event.payload_json or {}
        if facts.get('user_id')!=login.user_id or facts.get('sandbox_id')!=login.sandbox_id:continue
        if facts.get('old_username')==name and isinstance(facts.get('new_username'),str):
            name=facts['new_username']
    return name==login.username
