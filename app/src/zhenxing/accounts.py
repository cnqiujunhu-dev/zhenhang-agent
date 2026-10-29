from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import os
import re
import secrets

from fastapi import Depends, Header
from pydantic import Field, SecretStr
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from .access_models import AccessAudit, LoginAccount, LoginThrottle
from .contracts import DomainError, StrictModel
from .extensions import ensure_extension_fixtures
from .models import DemoSession, Sandbox, User, utcnow
from .seed import create_demo_sandbox
from .deployment_credentials import initial_customer_password, initial_admin_password
from .simulation import as_utc


PASSWORD_ROUNDS = 310000
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_ATTEMPT_LIMIT = 5
PROFILE_CHOICES = ("young_professional", "family", "pensioner", "investor")
SEED_USERNAMES = ("wangwei", "qiujunhu", "quyuwei", "wangmingzhe", "liyanliang", "panzhinuo", "test", "chenyue", "zhanghao", "linxinyi")


def normalized_username(value):
    if not isinstance(value, str):
        raise DomainError("INVALID_USERNAME", "账号应为3至32位小写字母、数字或下划线，并以字母开头", 422)
    username = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username):
        raise DomainError("INVALID_USERNAME", "账号应为3至32位小写字母、数字或下划线，并以字母开头", 422)
    return username


def valid_password(value):
    if not isinstance(value, str) or not 6 <= len(value) <= 128:
        raise DomainError("INVALID_PASSWORD_LENGTH", "密码长度应为6至128字符", 422)
    return value


def password_hash(password):
    valid_password(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
    return f"pbkdf2_sha256${PASSWORD_ROUNDS}${salt}${digest}"


def password_matches(password, encoded):
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100000 <= int(rounds) <= 1000000:
            return False
        calculated = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(calculated, digest)
    except (ValueError, TypeError, AttributeError):
        return False


DUMMY_PASSWORD_HASH = password_hash("inert-auth-timing-value")


def account_for_principal(session, principal):
    return session.scalar(select(LoginAccount).where(LoginAccount.user_id == principal.user_id,
                                                     LoginAccount.sandbox_id == principal.sandbox_id))


def require_account_role(session, principal, roles, *, lock=False):
    if lock:
        account = session.scalar(select(LoginAccount).where(LoginAccount.user_id == principal.user_id,
            LoginAccount.sandbox_id == principal.sandbox_id).with_for_update().execution_options(populate_existing=True))
    else:
        account = account_for_principal(session, principal)
    if account is None or account.status != "ACTIVE" or account.role not in set(roles):
        raise DomainError("ACCOUNT_ROLE_REQUIRED", "当前账号没有此操作权限", 403)
    return account


def public_account(account):
    return {"account_id": account.id, "username": account.username, "role": account.role,
            "status": account.status, "version": account.version, "profile": account.profile,
            "user_id": account.user_id, "sandbox_id": account.sandbox_id,
            "created_at": as_utc(account.created_at).isoformat(),
            "last_login_at": as_utc(account.last_login_at).isoformat() if account.last_login_at else None}


def _access_audit(session, action, actor=None, target=None, payload=None):
    session.add(AccessAudit(actor_account_id=actor.id if actor else None,
                           target_account_id=target.id if target else None,
                           action=action, payload_json=payload or {}))


def create_customer_account(session, username, password, profile="young_professional", actor=None, display_name=None, *, seed=None, role="CUSTOMER", business_time=None, subscription_history_cutoff=None):
    username = normalized_username(username)
    valid_password(password)
    if profile not in PROFILE_CHOICES or role not in {"CUSTOMER", "DEV_TEST"}:
        raise DomainError("INVALID_ACCOUNT_PROFILE", "请选择已有丰富账套类型", 422)
    if session.scalar(select(LoginAccount.id).where(LoginAccount.username == username)):
        raise DomainError("USERNAME_UNAVAILABLE", "该账号不可使用", 409)
    if role == "DEV_TEST" and session.scalar(select(LoginAccount.id).where(LoginAccount.role == "DEV_TEST")):
        raise DomainError("DEV_TEST_ALREADY_EXISTS", "唯一测试身份已存在", 409)
    fixed_seed = seed if seed is not None else int(hashlib.sha256(username.encode()).hexdigest()[:8], 16) % 2147483647
    sandbox, user, _card = create_demo_sandbox(session, profile=profile, seed=fixed_seed, business_time=business_time,
                                            subscription_history_cutoff=subscription_history_cutoff)
    user.display_name = display_name.strip()[:80] if display_name and display_name.strip() else username
    ensure_extension_fixtures(session, sandbox.id, user.id)
    from .extensions_models import CashflowGoal, FraudLabel
    from .models import Account, Payee
    from .simulation import business_now
    bank_account = session.scalar(select(Account).where(Account.user_id == user.id))
    clock = business_now(session, sandbox.id)
    for goal_name, target, days in [("旅行", 800000, 180), ("学习", 300000, 120), ("养老", 2000000, 365)]:
        session.add(CashflowGoal(sandbox_id=sandbox.id, user_id=user.id, account_id=bank_account.id,
                                name=goal_name, target_minor=target, reserved_minor=0, due_at=clock + timedelta(days=days)))
    pending_contact = session.scalar(select(Payee).where(Payee.user_id == user.id, Payee.display_name == "旅行伙伴"))
    if pending_contact:
        risk = session.scalar(select(FraudLabel).where(FraudLabel.payee_id == pending_contact.id))
        if risk:
            risk.risk_level, risk.reason_code = "UNKNOWN", "SECONDARY_SYNTHETIC_RISK_FEED_PENDING"
    account = LoginAccount(username=username, password_hash=password_hash(password), user_id=user.id,
                           sandbox_id=sandbox.id, role=role, profile=profile)
    session.add(account)
    session.flush()
    from .resource_defaults import RESOURCE_DEFAULTS_REVISION
    _access_audit(session, "ACCOUNT_CREATED", actor, account, {"role": role, "profile": profile,
        "resource_defaults_revision": RESOURCE_DEFAULTS_REVISION})
    from .card_identifiers import bootstrap_card_identifiers
    bootstrap_card_identifiers(session, user_id=user.id)
    return account


def bootstrap_named_accounts(session, *, upgrade_payment_accounts=False):
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(9360400)"))
    created = 0
    for index, username in enumerate(SEED_USERNAMES):
        if session.scalar(select(LoginAccount.id).where(LoginAccount.username == username)):
            continue
        create_customer_account(session, username, initial_customer_password(username), PROFILE_CHOICES[index % 4],
                                seed=20261001 + index * 101, role="DEV_TEST" if username == "test" else "CUSTOMER")
        created += 1
    admin = session.scalar(select(LoginAccount).where(LoginAccount.username == "admin"))
    admin_created = admin is None
    if admin_created:
        sandbox = Sandbox()
        session.add(sandbox)
        session.flush()
        user = User(sandbox_id=sandbox.id, display_name="系统管理员")
        session.add(user)
        session.flush()
        admin = LoginAccount(username="admin", password_hash=password_hash(initial_admin_password()), user_id=user.id,
                             sandbox_id=sandbox.id, role="ADMIN", profile=None)
        session.add(admin)
        session.flush()
        _access_audit(session, "ADMIN_BOOTSTRAPPED", target=admin)
        created += 1
    from .fx_banking import bootstrap_dual_currency_accounts
    dual_currency = bootstrap_dual_currency_accounts(session)
    from .card_identifiers import bootstrap_card_identifiers
    identifiers = bootstrap_card_identifiers(session)
    from .demo_family_accounts import bootstrap_demo_family_accounts
    family_accounts = bootstrap_demo_family_accounts(session)
    from .demo_carbon_funding import bootstrap_demo_carbon_points
    carbon_funding = bootstrap_demo_carbon_points(session)
    created += family_accounts["created_accounts"]
    payment_upgrade = initialize_named_payment_samples(session) if upgrade_payment_accounts else None
    return {"created_count": created, "seeded_usernames": list(SEED_USERNAMES), "admin_created": admin_created,
            "dual_currency": dual_currency, "card_identifiers": identifiers,
            "family_accounts": family_accounts, "carbon_funding": carbon_funding, "payment_upgrade": payment_upgrade}


def initialize_personal_services(session, account):
    """Initialize a newly registered local customer after its bank records exist."""
    from .asset_samples import seed_asset_samples_for_account
    from .customer_profile_migration import initialize_customer_profile
    initialize_customer_profile(session, account.sandbox_id, account.user_id)
    seed_asset_samples_for_account(session, account.sandbox_id, account.user_id)
    from .credit_bootstrap import initialize_customer_payment_services
    initialize_customer_payment_services(session, account.sandbox_id, account.user_id)
    from .life_plan_migration import initialize_life_plan_services
    initialize_life_plan_services(session, account.sandbox_id)


def initialize_named_payment_samples(session):
    """Explicit rollout hook; app startup does not silently move existing money.

    These named fixtures are registered adult-customer samples, including the
    grown-child family accounts. No conclusion is drawn from kinship words or
    the role of an unknown/newly registered customer.
    """
    from .credit_bootstrap import seed_payment_upgrades_for_usernames
    from .demo_family_accounts import FAMILY_USERNAMES
    names = SEED_USERNAMES+FAMILY_USERNAMES
    result = seed_payment_upgrades_for_usernames(session, names, adult_usernames=names,
        eligibility_source="EXPLICIT_NAMED_ADULT_CUSTOMER_FIXTURES_V1")
    from .life_plan_migration import initialize_life_plan_services
    for account in session.scalars(select(LoginAccount).where(LoginAccount.username.in_(names))):
        initialize_life_plan_services(session, account.sandbox_id)
    return result


def _new_session(session, account):
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=4)
    session.add(DemoSession(sandbox_id=account.sandbox_id, user_id=account.user_id,
                           token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires))
    session.flush()
    return {"token": token, "expires_at": expires.isoformat(), "username": account.username,
            "role": account.role, "user_id": account.user_id, "sandbox_id": account.sandbox_id,
            "account_id": account.id, "profile": account.profile}


def login_account(session, username, password):
    normalized = username.strip().lower() if isinstance(username, str) else ""
    key = hashlib.sha256(normalized.encode()).hexdigest()
    now = utcnow()
    if session.bind.dialect.name in {"sqlite", "postgresql"}:
        if session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        session.execute(insert(LoginThrottle).values(username_key=key, attempts=0, window_started_at=now)
                        .on_conflict_do_nothing(index_elements=["username_key"]))
    throttle = session.scalar(select(LoginThrottle).where(LoginThrottle.username_key == key).with_for_update())
    if throttle is None:
        throttle = LoginThrottle(username_key=key, attempts=0, window_started_at=now)
        session.add(throttle)
        session.flush()
    if now - as_utc(throttle.window_started_at) >= LOGIN_WINDOW:
        throttle.attempts, throttle.locked_until, throttle.window_started_at = 0, None, now
    account = session.scalar(select(LoginAccount).where(LoginAccount.username == normalized).with_for_update())
    match = password_matches(password if isinstance(password, str) else "", account.password_hash if account else DUMMY_PASSWORD_HASH)
    locked = throttle.locked_until is not None and as_utc(throttle.locked_until) > now
    if locked or account is None or account.status != "ACTIVE" or not match:
        if not locked:
            throttle.attempts += 1
            if throttle.attempts >= LOGIN_ATTEMPT_LIMIT:
                throttle.locked_until = now + LOGIN_WINDOW
        return {"authenticated": False, "error": "INVALID_CREDENTIALS"}
    throttle.attempts, throttle.locked_until = 0, None
    account.last_login_at = now
    _access_audit(session, "LOGIN_SUCCEEDED", target=account)
    return {"authenticated": True, **_new_session(session, account)}


def authenticate_access_session(session, authorization):
    from .services import Principal
    if not authorization or not authorization.startswith("Bearer "):
        raise DomainError("AUTH_REQUIRED", "请先登录", 401)
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    login = session.scalar(select(DemoSession).where(DemoSession.token_hash == token_hash))
    if login is None or as_utc(login.expires_at) <= utcnow():
        raise DomainError("INVALID_SESSION", "登录已过期或已撤销，请重新登录", 401)
    from .family_account_context import validate_family_context_session
    family_context = validate_family_context_session(session, login)
    account = session.scalar(select(LoginAccount).where(LoginAccount.user_id == login.user_id,
                                                       LoginAccount.sandbox_id == login.sandbox_id))
    if account and account.status != "ACTIVE":
        raise DomainError("INVALID_SESSION", "登录已过期或已撤销，请重新登录", 401)
    if account:
        return Principal(account.user_id, account.sandbox_id, role=account.role)
    from .extensions import is_family_helper
    return Principal(login.user_id, login.sandbox_id,
                     role="FAMILY_HELPER" if is_family_helper(session, login.sandbox_id, login.user_id) else "DEMO_OWNER",
                     family_context=family_context)


def revoke_user_sessions(session, user_id):
    result = session.execute(delete(DemoSession).where(DemoSession.user_id == user_id))
    return result.rowcount


def change_password(session, principal, current_password, new_password):
    account = require_account_role(session, principal, {"CUSTOMER", "DEV_TEST", "ADMIN"}, lock=True)
    if not password_matches(current_password, account.password_hash):
        raise DomainError("INVALID_CREDENTIALS", "当前凭据不正确", 403)
    account.password_hash = password_hash(new_password)
    account.version += 1
    revoked = revoke_user_sessions(session, account.user_id)
    _access_audit(session, "PASSWORD_CHANGED", account, account, {"sessions_revoked": revoked})
    return {"password_changed": True, "sessions_revoked": revoked, "requires_login": True}


def set_account_status(session, actor, target_id, status, expected_version, reason):
    actual = session.scalar(select(LoginAccount).where(LoginAccount.id == actor.id, LoginAccount.role == "ADMIN", LoginAccount.status == "ACTIVE"))
    if actual is None:
        raise DomainError("ACCOUNT_ROLE_REQUIRED", "需要管理员权限", 403)
    target = session.scalar(select(LoginAccount).where(LoginAccount.id == target_id).with_for_update())
    if target is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    if target.role == "ADMIN":
        raise DomainError("ADMIN_SELF_MANAGEMENT_FORBIDDEN", "管理员凭据请通过本人改密接口管理", 403)
    if target.version != expected_version:
        raise DomainError("ACCOUNT_VERSION_CHANGED", "账号版本变化，请刷新", 409)
    if status not in {"ACTIVE", "DISABLED"}:
        raise DomainError("INVALID_ACCOUNT_STATUS", "账号状态无效", 422)
    target.status, target.version = status, target.version + 1
    revoked = revoke_user_sessions(session, target.user_id)
    _access_audit(session, "ACCOUNT_STATUS_CHANGED", actor, target, {"status": status, "reason": reason[:200], "sessions_revoked": revoked})
    return {**public_account(target), "sessions_revoked": revoked}


def reset_account_password(session, actor, target_id, new_password, expected_version, reason):
    actual = session.scalar(select(LoginAccount).where(LoginAccount.id == actor.id, LoginAccount.role == "ADMIN", LoginAccount.status == "ACTIVE"))
    if actual is None:
        raise DomainError("ACCOUNT_ROLE_REQUIRED", "需要管理员权限", 403)
    target = session.scalar(select(LoginAccount).where(LoginAccount.id == target_id).with_for_update())
    if target is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    if target.role == "ADMIN":
        raise DomainError("ADMIN_SELF_MANAGEMENT_FORBIDDEN", "管理员凭据请通过本人改密接口管理", 403)
    if target.version != expected_version:
        raise DomainError("ACCOUNT_VERSION_CHANGED", "账号版本变化，请刷新", 409)
    target.password_hash, target.version = password_hash(new_password), target.version + 1
    revoked = revoke_user_sessions(session, target.user_id)
    _access_audit(session, "PASSWORD_RESET_BY_ADMIN", actor, target, {"reason": reason[:200], "sessions_revoked": revoked})
    return {**public_account(target), "password_reset": True, "sessions_revoked": revoked, "requires_login": True}


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(max_length=128)


class RegisterRequest(StrictModel):
    username: str = Field(min_length=3, max_length=32)
    password: SecretStr = Field(max_length=128)
    display_name: str | None = Field(default=None, max_length=80)
    profile: str = "young_professional"


class PasswordRequest(StrictModel):
    current_password: SecretStr = Field(max_length=128)
    new_password: SecretStr = Field(max_length=128)


def install_account_routes(app, get_session, get_principal):
    @app.post("/api/v2/auth/login")
    def login(payload: LoginRequest, session=Depends(get_session)):
        result = login_account(session, payload.username, payload.password.get_secret_value())
        session.commit()  # Failed-attempt counters must survive an HTTP401.
        if not result["authenticated"]:
            raise DomainError("INVALID_CREDENTIALS", "账号或密码不正确，或暂时无法登录", 401)
        return {key: value for key, value in result.items() if key != "authenticated"}

    @app.post("/api/v2/auth/register")
    def register(payload: RegisterRequest, session=Depends(get_session)):
        # The local project keeps self-registration available. A restricted
        # server profile explicitly disables only this anonymous endpoint;
        # invalid/blank configured values fail closed and grant no new role.
        if os.getenv("ZX_ALLOW_PUBLIC_REGISTRATION", "1") != "1":
            raise DomainError("PUBLIC_REGISTRATION_DISABLED", "当前入口不开放自助注册，请联系管理员", 403)
        username = normalized_username(payload.username)
        from .demo_family_accounts import FAMILY_USERNAMES
        if username in {*SEED_USERNAMES, *FAMILY_USERNAMES, "admin"}:
            raise DomainError("USERNAME_UNAVAILABLE", "该账号不可使用", 409)
        try:
            account = create_customer_account(session, username, payload.password.get_secret_value(), payload.profile,
                                              display_name=payload.display_name)
            from .demo_carbon_funding import fund_demo_carbon_points
            fund_demo_carbon_points(session, account.sandbox_id, account.user_id)
            initialize_personal_services(session, account)
            result = _new_session(session, account)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise DomainError("USERNAME_UNAVAILABLE", "该账号不可使用", 409) from None
        return result

    @app.get("/api/v2/auth/me")
    def me(session=Depends(get_session), principal=Depends(get_principal)):
        account = account_for_principal(session, principal)
        user = session.get(User, principal.user_id)
        return {"authenticated": True, "registered": account is not None,
                "username": account.username if account else None, "role": account.role if account else principal.role,
                "user_id": principal.user_id, "sandbox_id": principal.sandbox_id,
                "account_id": account.id if account else None, "profile": account.profile if account else None,
                "family_context": principal.family_context,
                "version": account.version if account else None, "display_name": user.display_name if user else None}

    @app.post("/api/v2/auth/logout")
    def logout(session=Depends(get_session), principal=Depends(get_principal), authorization: str | None = Header(default=None)):
        token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
        session.execute(delete(DemoSession).where(DemoSession.token_hash == token_hash, DemoSession.user_id == principal.user_id))
        session.commit()
        return {"logged_out": True}

    @app.post("/api/v2/auth/refresh")
    def refresh(session=Depends(get_session), principal=Depends(get_principal), authorization: str | None = Header(default=None)):
        account = require_account_role(session, principal, {"CUSTOMER", "DEV_TEST", "ADMIN"}, lock=True)
        token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
        # DELETE...RETURNING consumes the original session exactly once even if two
        # refresh requests passed authentication before either transaction commits.
        consumed = session.scalar(delete(DemoSession).where(DemoSession.token_hash == token_hash,
            DemoSession.user_id == principal.user_id, DemoSession.expires_at > utcnow()).returning(DemoSession.id))
        if consumed is None:
            raise DomainError("INVALID_SESSION", "登录已过期或已撤销，请重新登录", 401)
        result = _new_session(session, account)
        session.commit()
        return result

    @app.post("/api/v2/auth/password")
    def password(payload: PasswordRequest, session=Depends(get_session), principal=Depends(get_principal)):
        result = change_password(session, principal, payload.current_password.get_secret_value(), payload.new_password.get_secret_value())
        session.commit()
        return result
