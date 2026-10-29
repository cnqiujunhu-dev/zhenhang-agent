"""Exact username(display-name) annotation proof, never fuzzy payee choice."""
import re

from sqlalchemy import select

from .access_models import LoginAccount
from .contracts import DomainError
from .models import User


def verified_aa_participant_reference(session, value):
    """Keep a plain name unchanged; both parts of an annotation must agree.

    This is only an AA identity spelling codec after the model selected the
    literal participant list. Ordinary recipient ownership/status checks still
    run afterwards, and confirmation revalidates this same original spelling.
    """
    if not isinstance(value, str):
        raise DomainError('AA_PARTICIPANT_IDENTITY_INVALID', '请核对参与人的完整账号和姓名。', 422)
    raw = value.strip()
    if not any(mark in raw for mark in ('(', ')', '（', '）')):
        return raw
    match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_]{0,31})\s*(?:（([^（）()]{1,80})）|\(([^（）()]{1,80})\))', raw)
    if not match:
        raise DomainError('AA_PARTICIPANT_IDENTITY_INVALID', '参与人的账号与括号中的姓名需完整对应。', 422)
    username, annotation = match[1], (match[2] or match[3]).strip()
    rows = session.execute(select(LoginAccount, User).join(User, User.id == LoginAccount.user_id)
        .where(LoginAccount.username == username, LoginAccount.status == 'ACTIVE',
               LoginAccount.role.in_({'CUSTOMER', 'DEV_TEST'}))).all()
    if len(rows) != 1 or rows[0][1].display_name != annotation:
        raise DomainError('AA_PARTICIPANT_IDENTITY_MISMATCH', '参与人的账号与姓名不一致，请先核对；尚未发起收款。', 409)
    return username
