from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def default_database_url() -> str:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / "var"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'zhenxing.db').as_posix()}"


def create_database(database_url: str | None = None):
    url = database_url or os.getenv("ZX_DATABASE_URL") or default_database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory

