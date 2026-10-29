"""Dispatch adapters through the same confirmation gate as existing tools."""
from .contracts import DomainError


def prepare_upgraded_task(session, task, action, context=None):
    from .intelligence import ProposedAction
    # The model union serializes absent optional fields as null. Domain defaults
    # must see true absence, while explicit false/zero are never dropped.
    action = ProposedAction.model_validate(action).model_dump(exclude_none=True)
    kind = action['kind']
    if kind == 'OWN_ACCOUNT_TRANSFER':
        from .own_account_transfers import prepare_own_account_transfer_task
        return prepare_own_account_transfer_task(session, task, action, context)
    if kind.startswith('CREDIT_'):
        from .credit_runtime import prepare_credit_task
        return prepare_credit_task(session, task, action, context)
    if kind.startswith('OCCASION_'):
        from .occasion_runtime import prepare_occasion_task
        return prepare_occasion_task(session, task, action, context)
    if kind.startswith('LIFE_PLAN_'):
        from .life_plan_runtime import prepare_life_plan_task
        return prepare_life_plan_task(session, task, action, context)
    if kind.startswith('MERCHANT_'):
        from .merchant_runtime import prepare_merchant_task
        return prepare_merchant_task(session, task, action, context)
    raise DomainError('ACTION_NOT_SUPPORTED', '当前办理内容尚不可执行。', 422)


def dispatch_upgraded_task(session, *, task, principal, request, idempotency_key):
    from .tools import ToolGateway
    from .upgraded_capabilities import CAPABILITIES
    rows = task.plan_json.get('nodes') or []
    row = rows[0] if len(rows) == 1 else {}
    kind = row.get('action_kind')
    contract = CAPABILITIES.get(kind)
    if not contract or contract[4] or row.get('tool_name') != contract[3]:
        raise DomainError('ACTION_NOT_SUPPORTED', '没有可确认的办理内容。', 409)
    request_hash, node, confirmation, duplicate = ToolGateway().authorize_write(session,
        task=task, sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        request=request, idempotency_key=idempotency_key,
        tool_name=row['tool_name'], node_key=row['node_id'])
    if duplicate:
        return task
    extra = {'request_hash': request_hash, 'node': node}
    if kind == 'OWN_ACCOUNT_TRANSFER':
        from .own_account_transfers import commit_own_account_transfer_task as commit
        extra['auth_challenge_id'] = request.auth_challenge_id
    elif kind.startswith('OCCASION_'):
        from .occasion_runtime import commit_occasion_task as commit
    elif kind.startswith('LIFE_PLAN_'):
        from .life_plan_runtime import commit_life_plan_task as commit
        extra['auth_challenge_id'] = request.auth_challenge_id
    elif kind.startswith('MERCHANT_'):
        from .merchant_runtime import commit_merchant_task as commit
        extra['auth_challenge_id'] = request.auth_challenge_id
    else:
        raise DomainError('ACTION_NOT_SUPPORTED', '没有可确认的办理内容。', 409)
    return commit(session, task, principal, confirmation, idempotency_key, **extra)
