"""One-time read-only stock/metal examples; never debit or reset a bank book."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text

from .access_models import LoginAccount
from .asset_models import AssetSampleBootstrap, AssetSamplePosition, AssetSnapshotPrice
from .contracts import DomainError
from .models import Account, User


ASSET_SAMPLE_REVISION = "asset-readonly-samples-20261017-v1"
SAMPLE_PRICE_TIME = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
SAMPLES = (
    {"instrument_id": "DEMO.STOCK.ZX001", "product_name": "科技股票 ZX001", "asset_class": "STOCK",
     "quantity_unit": "SHARE", "quantity": "100", "available": "80", "frozen": "20", "cost_minor": 100000, "price": "12.50"},
    {"instrument_id": "DEMO.METAL.AU9999", "product_name": "黄金 Au9999", "asset_class": "PRECIOUS_METAL",
     "quantity_unit": "GRAM", "quantity": "10.5", "available": "10.5", "frozen": "0", "cost_minor": 650000, "price": "650.00"},
)


def _inconsistent():
    return DomainError("ASSET_SAMPLE_BOOTSTRAP_INCONSISTENT", "样例资产初始化记录不完整，请先核验；不会重新填充或覆盖持仓。", 409)


def seed_asset_samples_for_account(session, sandbox_id: str, user_id: str) -> dict:
    """Append once; caller commits. A missing previously seeded row is an error."""
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(9360422)"))
    login = session.scalar(select(LoginAccount).where(LoginAccount.sandbox_id == sandbox_id,
        LoginAccount.user_id == user_id).with_for_update().execution_options(populate_existing=True))
    if not login or login.status != "ACTIVE" or login.role not in {"CUSTOMER", "DEV_TEST"}:
        return {"status": "SKIPPED_INELIGIBLE", "user_id": user_id, "created_positions": 0}
    if session.scalar(select(User.id).where(User.id == user_id, User.sandbox_id == sandbox_id)) is None \
            or session.scalar(select(Account.id).where(Account.user_id == user_id, Account.sandbox_id == sandbox_id)) is None:
        return {"status": "SKIPPED_INELIGIBLE", "user_id": user_id, "created_positions": 0}
    marker = session.scalar(select(AssetSampleBootstrap).where(AssetSampleBootstrap.sandbox_id == sandbox_id,
        AssetSampleBootstrap.user_id == user_id, AssetSampleBootstrap.revision == ASSET_SAMPLE_REVISION))
    positions = {row.instrument_id: row for row in session.scalars(select(AssetSamplePosition).where(
        AssetSamplePosition.sandbox_id == sandbox_id, AssetSamplePosition.user_id == user_id,
        AssetSamplePosition.instrument_id.in_([sample["instrument_id"] for sample in SAMPLES])))}
    if marker:
        expected = {sample["instrument_id"] for sample in SAMPLES}
        if not isinstance(marker.position_ids_json, dict) or set(marker.position_ids_json) != expected or set(positions) != expected:
            raise _inconsistent()
        if any(marker.position_ids_json[key] != row.id for key, row in positions.items()):
            raise _inconsistent()
        return {"status": "ALREADY_SEEDED", "user_id": user_id, "created_positions": 0}
    if positions:
        raise _inconsistent()  # Unmarked rows are not permission to overwrite/repair.
    created = {}
    for sample in SAMPLES:
        snapshot = session.scalar(select(AssetSnapshotPrice).where(AssetSnapshotPrice.instrument_id == sample["instrument_id"],
            AssetSnapshotPrice.revision == ASSET_SAMPLE_REVISION))
        if snapshot is None:
            snapshot = AssetSnapshotPrice(instrument_id=sample["instrument_id"], revision=ASSET_SAMPLE_REVISION,
                price=Decimal(sample["price"]), currency="CNY", quantity_unit=sample["quantity_unit"], observed_at=SAMPLE_PRICE_TIME)
            session.add(snapshot)
            session.flush()
        if snapshot.currency != "CNY" or snapshot.quantity_unit != sample["quantity_unit"] \
                or snapshot.source != "SYNTHETIC_SNAPSHOT_NOT_MARKET_DATA":
            raise _inconsistent()
        row = AssetSamplePosition(sandbox_id=sandbox_id, user_id=user_id, instrument_id=sample["instrument_id"],
            product_name=sample["product_name"], asset_class=sample["asset_class"], quantity_unit=sample["quantity_unit"],
            total_quantity=Decimal(sample["quantity"]), available_quantity=Decimal(sample["available"]),
            frozen_quantity=Decimal(sample["frozen"]), remaining_cost_minor=sample["cost_minor"], currency="CNY", snapshot_id=snapshot.id)
        session.add(row)
        session.flush()
        created[row.instrument_id] = row.id
    session.add(AssetSampleBootstrap(sandbox_id=sandbox_id, user_id=user_id, revision=ASSET_SAMPLE_REVISION, position_ids_json=created))
    session.flush()
    return {"status": "SEEDED", "user_id": user_id, "created_positions": len(created)}


def bootstrap_asset_samples(session) -> dict:
    from .accounts import SEED_USERNAMES
    from .demo_family_accounts import FAMILY_USERNAMES
    # Historical QA fixtures are not opted into this demo-data upgrade.
    usernames = tuple(SEED_USERNAMES) + tuple(FAMILY_USERNAMES)
    accounts = list(session.scalars(select(LoginAccount).where(LoginAccount.status == "ACTIVE",
        LoginAccount.role.in_(["CUSTOMER", "DEV_TEST"]), LoginAccount.username.in_(usernames)).order_by(LoginAccount.username)))
    results = [seed_asset_samples_for_account(session, account.sandbox_id, account.user_id) for account in accounts]
    return {"revision": ASSET_SAMPLE_REVISION, "seeded_accounts": sum(row["status"] == "SEEDED" for row in results),
        "created_positions": sum(row["created_positions"] for row in results), "results": results}
