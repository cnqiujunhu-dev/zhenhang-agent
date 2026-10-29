"""Typed credit preparation requirements, not a user-phrase classifier."""
from sqlalchemy import select

from .contracts import DomainError


def application_facility(session, principal):
    """One owned active pool is not a choice of an arbitrary existing card.

    This only selects the account for a pending additional-card application.
    It neither increases credit nor issues/activates a card. Multiple pools
    require an explicit existing-card selector; none means a first application.
    """
    from .credit_models import CreditAccount
    rows = list(session.scalars(select(CreditAccount).where(
        CreditAccount.sandbox_id == principal.sandbox_id, CreditAccount.user_id == principal.user_id,
        CreditAccount.status == 'ACTIVE')))
    if len(rows) > 1:
        raise DomainError('CREDIT_APPLICATION_POOL_SELECTION_REQUIRED',
            '你名下有多份授信账户，想基于哪张信用卡的授信申请新卡？请指定卡片，避免选错账户。', 422)
    return rows[0] if rows else None


def missing_credit_fields(kind, slots):
    if kind != 'CREDIT_INSTALLMENT':
        return []
    return [field for field in ('payment_id', 'periods') if slots.get(field) is None]


def prepare_credit_clarification(task, kind, missing):
    allowed = {'payment_id', 'periods', 'card_reference'}
    if not missing or len(set(missing)) != len(missing) or not set(missing) <= allowed:
        raise ValueError('CREDIT_CLARIFICATION_CONTRACT_INVALID')
    if task.state != 'NEED_CLARIFICATION' or (task.plan_json or {}).get('nodes'):
        raise DomainError('CREDIT_PREPARATION_STATE_CHANGED', '原方案仍需按当前记录处理，不能覆盖为新的补充信息请求。', 409)
    message = ('你想把哪笔信用卡消费分期？告诉我商户、日期或金额等线索，我再和你核对对应记录。'
        if 'payment_id' in missing else '你希望分多少期？确认前会核对这笔消费支持的期数和费用。'
        if missing == ['periods'] else '想查询哪张信用卡的账单？告诉我卡片尾号即可。'
        if kind == 'CREDIT_STATEMENT' else '想查询哪张信用卡？告诉我卡片尾号即可。'
        if kind == 'CREDIT_VIEW' else '你名下有多份授信账户，想基于哪张信用卡的授信申请新卡？')
    task.result_json = {'error': 'CREDIT_SELECTION_REQUIRED', 'action': kind, 'message': message,
        'missing_fields': missing, 'bank_state_changed': False}
    task.fic_json = {**task.fic_json, 'status': 'NEED_CLARIFICATION'}
    return task
