from sqlalchemy import select

from .contracts import DomainError
from .models import Account


def require_sandbox_owner(session, principal):
    if actor_role(session, principal.sandbox_id, principal.user_id) == "ADMIN":
        raise DomainError("ADMIN_BANK_EXECUTION_FORBIDDEN", "管理身份仅负责运维与账号管理，不能代客户执行银行操作", 403)
    account = session.scalar(select(Account).where(Account.sandbox_id == principal.sandbox_id,
                                                   Account.user_id == principal.user_id))
    if account is None:
        raise DomainError("SANDBOX_OWNER_REQUIRED", "仅银行账套本人可查看完整数据或操作账套控制", 403)
    return account


def actor_role(session, sandbox_id: str, user_id: str) -> str:
    from .access_models import LoginAccount
    account = session.scalar(select(LoginAccount).where(LoginAccount.sandbox_id == sandbox_id,
                                                        LoginAccount.user_id == user_id))
    if account:
        if account.status != "ACTIVE":
            raise DomainError("ACCOUNT_DISABLED", "账号已停用，不能建立新的Agent执行授权", 403)
        return account.role
    from .extensions import is_family_helper
    from .family_account_context import validate_family_context_actor
    validate_family_context_actor(session, sandbox_id, user_id)
    return "FAMILY_HELPER" if is_family_helper(session, sandbox_id, user_id) else "DEMO_OWNER"


def require_banking_actor(session, principal):
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    if role == "ADMIN":
        raise DomainError("ADMIN_BANK_EXECUTION_FORBIDDEN", "管理员不能代客户规划、确认或执行银行任务", 403)
    return role
