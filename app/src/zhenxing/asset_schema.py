"""Independent additive asset migration, called AFTER core ensure_schema."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from sqlalchemy import inspect, text

from .asset_models import ASSET_TABLES, AssetBase


def ensure_asset_schema(engine) -> dict:
    existing = set(inspect(engine).get_table_names())
    if not {"sandboxes", "users", "accounts"} <= existing:
        raise RuntimeError("Run core ensure_schema before asset schema migration")
    missing = sorted(ASSET_TABLES - existing)
    backup = None
    if missing and engine.dialect.name == "sqlite" and engine.url.database not in {None, ":memory:"}:
        source = Path(engine.url.database).resolve()
        target = source.with_name(f"{source.name}.pre-assets-v1-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}.bak")
        with sqlite3.connect(source) as original, sqlite3.connect(target) as destination:
            original.backup(destination)
        backup = str(target)
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(9360421)"))
        AssetBase.metadata.create_all(connection)
    inspector = inspect(engine)
    for table in AssetBase.metadata.sorted_tables:
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        if absent := set(table.columns.keys()) - actual:
            raise RuntimeError(f"Asset schema drift: {table.name} missing {sorted(absent)}")
    return {"revision": "asset-schema-v1", "created_tables": missing, "sqlite_backup": backup}
