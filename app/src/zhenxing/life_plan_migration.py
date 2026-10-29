"""Additive tables and explicitly requested virtual catalog initialization."""
from sqlalchemy import inspect

from .database import Base
from . import extensions_models  # noqa: F401; resolve existing family FK
from .life_plan_models import LIFE_PLAN_TABLES
from .merchant_models import MERCHANT_TABLES
from .occasion_models import OCCASION_TABLES
from .family_profile_models import FAMILY_PROFILE_TABLES
from .merchant_service import ensure_virtual_merchant_catalog


TABLES = LIFE_PLAN_TABLES | MERCHANT_TABLES | OCCASION_TABLES | FAMILY_PROFILE_TABLES


def ensure_life_plan_schema(engine):
    """Root must register TABLES with core schema's additive migration allowlist."""
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name in TABLES])
    return {"created_tables": sorted(TABLES - before), "data_reset": False}


def initialize_life_plan_services(session, sandbox_id):
    """Explicit per-sandbox call, not every historical QA account at startup."""
    ensure_virtual_merchant_catalog(session, sandbox_id)
