"""Migración ``0002_sla``: encadenada tras ``0001_core`` y coincidente con los modelos."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.db import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
SLA_TABLES = ("business_calendars", "calendar_holidays", "sla_policies", "sla_instances", "sla_events")


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["sqlalchemy.url"] = url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'sla_migration.db').as_posix()}"


def test_revision_chain():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    revision = script.get_revision("0002_sla")
    assert revision is not None and revision.down_revision == "0001_core"


def test_upgrade_matches_models_and_downgrade_removes_tables(sqlite_url):
    config = alembic_config(sqlite_url)
    command.upgrade(config, "0002_sla")
    engine = sa.create_engine(sqlite_url)
    try:
        inspector = sa.inspect(engine)
        assert set(SLA_TABLES) <= set(inspector.get_table_names())
        for table_name in SLA_TABLES:
            model_table = Base.metadata.tables[table_name]
            db_columns = {c["name"]: c for c in inspector.get_columns(table_name)}
            assert set(db_columns) == set(model_table.columns.keys()), table_name
            for column in model_table.columns:
                assert db_columns[column.name]["nullable"] == column.nullable, (table_name, column.name)
            model_indexes = {ix.name for ix in model_table.indexes}
            db_indexes = {ix["name"] for ix in inspector.get_indexes(table_name)}
            assert model_indexes <= db_indexes, table_name
    finally:
        engine.dispose()

    command.downgrade(config, "0001_core")
    engine = sa.create_engine(sqlite_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not set(SLA_TABLES) & tables
        assert "tickets" in tables
    finally:
        engine.dispose()
