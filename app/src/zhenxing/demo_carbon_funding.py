"""One-time activity-point funding for registered customer test accounts.

This trusted bootstrap is deliberately separate from extension fixture creation
and from CARBON_CLAIM: it must not invent an attested low-carbon behaviour or
replenish points spent by an account. The caller owns the transaction.
"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .access_models import AccessAudit, LoginAccount
from .contracts import DomainError
from .extensions_models import CarbonPointLedger, CarbonPool, CarbonWallet
from .models import Account, User


DEMO_CARBON_FUNDING_REVISION = "demo-carbon-opening-v1"
DEMO_CARBON_FUNDING_POINTS = 100
DEMO_CARBON_FUNDING_AUDIT = "DEMO_CARBON_POINTS_GRANTED"
_ELIGIBLE_ROLES = {"CUSTOMER", "DEV_TEST"}


def _inconsistent() -> DomainError:
    return DomainError("DEMO_CARBON_FUNDING_INCONSISTENT", "积分初始化记录不完整，请先核验账目", 409)


def _operation_key(user_id: str) -> str:
    return f"{DEMO_CARBON_FUNDING_REVISION}:{user_id}"


def _eligible_account(session: Session, sandbox_id: str, user_id: str) -> LoginAccount | None:
    # A login identity alone does not grant a financial wallet. A helper token
    # has no LoginAccount; an administrator is never a customer point subject.
    login = session.scalar(select(LoginAccount).where(
        LoginAccount.sandbox_id == sandbox_id, LoginAccount.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if login is None or login.status != "ACTIVE" or login.role not in _ELIGIBLE_ROLES:
        return None
    if session.scalar(select(User.id).where(User.id == user_id, User.sandbox_id == sandbox_id)) is None:
        return None
    if session.scalar(select(Account.id).where(Account.user_id == user_id, Account.sandbox_id == sandbox_id)) is None:
        return None
    return login


def _audit_rows(session: Session, account_id: str, operation_key: str) -> list[AccessAudit]:
    # Filtering the JSON key in Python keeps the same strict contract on SQLite
    # and PostgreSQL and avoids pretending that a partial ledger is a grant.
    return [row for row in session.scalars(select(AccessAudit).where(
        AccessAudit.target_account_id == account_id,
        AccessAudit.action == DEMO_CARBON_FUNDING_AUDIT,
    )) if isinstance(row.payload_json, dict) and row.payload_json.get("operation_key") == operation_key]


def fund_demo_carbon_points(session: Session, sandbox_id: str, user_id: str) -> dict:
    """Append exactly one 100-point grant, or verify its prior posting.

    Returns SKIPPED_INELIGIBLE without creating a wallet for non-customers.
    Existing wallet points, claims, redemptions and reward stock are preserved.
    No commit is performed; all mutations roll back with the caller.
    """
    if session.bind.dialect.name == "postgresql":
        # This private namespace serializes creation of a missing wallet/pool
        # before row locks: two customers in the same sandbox must not take
        # separate login locks before one waits for the shared pool lock.
        session.execute(text("SELECT pg_advisory_xact_lock(9360411, hashtext(:sandbox_id))"),
                        {"sandbox_id": sandbox_id})
    login = _eligible_account(session, sandbox_id, user_id)
    if login is None:
        return {"status": "SKIPPED_INELIGIBLE", "user_id": user_id, "granted_points": 0}
    wallet = session.scalar(select(CarbonWallet).where(
        CarbonWallet.sandbox_id == sandbox_id, CarbonWallet.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    # Keep the wallet -> pool order used by CARBON_CLAIM / CARBON_REDEEM.
    pool = session.scalar(select(CarbonPool).where(CarbonPool.sandbox_id == sandbox_id)
                          .with_for_update().execution_options(populate_existing=True))
    key = _operation_key(user_id)
    ledger = list(session.scalars(select(CarbonPointLedger).where(
        CarbonPointLedger.sandbox_id == sandbox_id, CarbonPointLedger.operation_key == key,
    )))
    audits = _audit_rows(session, login.id, key)
    wallet_total = session.scalar(select(func.coalesce(func.sum(CarbonWallet.points), 0)).where(
        CarbonWallet.sandbox_id == sandbox_id))

    if ledger or audits:
        expected = {f"WALLET:{user_id}": DEMO_CARBON_FUNDING_POINTS,
                    f"POOL:{sandbox_id}": -DEMO_CARBON_FUNDING_POINTS}
        if wallet is None or pool is None or len(ledger) != 2 or len(audits) != 1 \
                or any(row.user_id != user_id for row in ledger) \
                or {row.account_ref: row.delta_points for row in ledger} != expected:
            raise _inconsistent()
        payload = audits[0].payload_json
        if payload.get("revision") != DEMO_CARBON_FUNDING_REVISION \
                or payload.get("user_id") != user_id or payload.get("sandbox_id") != sandbox_id \
                or payload.get("points") != DEMO_CARBON_FUNDING_POINTS \
                or payload.get("wallet_id") != wallet.id \
                or payload.get("source") != "REGISTERED_ACCOUNT_TEST_ACTIVITY_OPENING":
            raise _inconsistent()
        integer_fields = ("wallet_points_before", "wallet_points_after", "wallet_version_before",
                          "wallet_version_after", "pool_points_before", "pool_points_after")
        if any(type(payload.get(field)) is not int for field in integer_fields) \
                or payload["wallet_points_before"] < 0 or payload["wallet_version_before"] < 1 \
                or payload["wallet_points_after"] - payload["wallet_points_before"] != DEMO_CARBON_FUNDING_POINTS \
                or payload["wallet_version_after"] != payload["wallet_version_before"] + 1 \
                or payload["pool_points_before"] - payload["pool_points_after"] != DEMO_CARBON_FUNDING_POINTS \
                or payload["pool_points_after"] < 0 \
                or type(wallet.points) is not int or wallet.points < 0 \
                or type(wallet.version) is not int or wallet.version < payload["wallet_version_after"] \
                or pool.remaining_points + wallet_total != 10000:
            raise _inconsistent()
        return {"status": "ALREADY_GRANTED", "user_id": user_id, "operation_key": key,
                "granted_points": 0, "wallet_points": wallet.points, "wallet_version": wallet.version}

    if pool is None:
        if wallet_total != 0:
            raise _inconsistent()
        pool = CarbonPool(sandbox_id=sandbox_id, remaining_points=10000)
        session.add(pool)
    elif pool.remaining_points + wallet_total != 10000:
        raise _inconsistent()
    if pool.remaining_points < DEMO_CARBON_FUNDING_POINTS:
        raise DomainError("DEMO_CARBON_POOL_INSUFFICIENT", "活动积分池不足，请先核验账目", 409)
    if wallet is None:
        wallet = CarbonWallet(sandbox_id=sandbox_id, user_id=user_id, points=0, version=1)
        session.add(wallet)
        session.flush()
    if not isinstance(wallet.points, int) or wallet.points < 0 \
            or not isinstance(wallet.version, int) or wallet.version < 1:
        raise _inconsistent()
    before_points, before_version = wallet.points, wallet.version
    before_pool = pool.remaining_points
    wallet.points += DEMO_CARBON_FUNDING_POINTS
    wallet.version += 1
    pool.remaining_points -= DEMO_CARBON_FUNDING_POINTS
    session.add_all([
        CarbonPointLedger(sandbox_id=sandbox_id, user_id=user_id, operation_key=key,
                          account_ref=f"WALLET:{user_id}", delta_points=DEMO_CARBON_FUNDING_POINTS),
        CarbonPointLedger(sandbox_id=sandbox_id, user_id=user_id, operation_key=key,
                          account_ref=f"POOL:{sandbox_id}", delta_points=-DEMO_CARBON_FUNDING_POINTS),
        AccessAudit(target_account_id=login.id, action=DEMO_CARBON_FUNDING_AUDIT, payload_json={
            "revision": DEMO_CARBON_FUNDING_REVISION, "operation_key": key,
            "source": "REGISTERED_ACCOUNT_TEST_ACTIVITY_OPENING", "points": DEMO_CARBON_FUNDING_POINTS,
            "sandbox_id": sandbox_id, "user_id": user_id, "wallet_id": wallet.id,
            "wallet_points_before": before_points, "wallet_points_after": wallet.points,
            "wallet_version_before": before_version, "wallet_version_after": wallet.version,
            "pool_points_before": before_pool, "pool_points_after": pool.remaining_points,
        }),
    ])
    session.flush()
    return {"status": "GRANTED", "user_id": user_id, "operation_key": key,
            "granted_points": DEMO_CARBON_FUNDING_POINTS,
            "wallet_points": wallet.points, "wallet_version": wallet.version}


def bootstrap_demo_carbon_points(session: Session) -> dict:
    """Fund all active registered financial customers; preserve every old book."""
    accounts = list(session.scalars(select(LoginAccount).where(
        LoginAccount.status == "ACTIVE", LoginAccount.role.in_(sorted(_ELIGIBLE_ROLES)),
    ).order_by(LoginAccount.username)))
    results = [fund_demo_carbon_points(session, account.sandbox_id, account.user_id) for account in accounts]
    return {"revision": DEMO_CARBON_FUNDING_REVISION,
            "granted_accounts": sum(row["status"] == "GRANTED" for row in results),
            "already_granted_accounts": sum(row["status"] == "ALREADY_GRANTED" for row in results),
            "skipped_accounts": sum(row["status"] == "SKIPPED_INELIGIBLE" for row in results),
            "granted_points": sum(row["granted_points"] for row in results), "results": results}
