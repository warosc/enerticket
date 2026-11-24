"""Migración ``0001_core``: coincide con los modelos y renombra las tablas legacy de la Fase 1."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.db import Base
from app.models import core as core_models

BACKEND_DIR = Path(__file__).resolve().parents[1]
CORE_TABLES = sorted(
    {
        model.__table__.name
        for model in vars(core_models).values()
        if isinstance(model, type) and issubclass(model, Base) and model is not Base
    }
)


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["sqlalchemy.url"] = url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"


def create_legacy_schema(url: str) -> None:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, title VARCHAR(200), status VARCHAR(30), "
            "requester VARCHAR(200))"
        )
        conn.exec_driver_sql(
            "CREATE TABLE comments (id INTEGER PRIMARY KEY, ticket_id INTEGER REFERENCES tickets(id), "
            "author VARCHAR(200), body TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE audit_events (id INTEGER PRIMARY KEY, ticket_id INTEGER, actor VARCHAR(200), "
            "action VARCHAR(80))"
        )
        conn.exec_driver_sql("INSERT INTO tickets (id, title, status, requester) VALUES (1, 'MVP', 'new', 'x')")
    engine.dispose()


def test_revision_chain_starts_with_core():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    revision = script.get_revision("0001_core")
    assert revision is not None and revision.down_revision is None


def test_upgrade_matches_models_and_renames_legacy(sqlite_url):
    create_legacy_schema(sqlite_url)
    # Se compara en la cabeza: migraciones posteriores (0006_catalog) agregan columnas core.
    command.upgrade(alembic_config(sqlite_url), "head")

    engine = sa.create_engine(sqlite_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"legacy_tickets", "legacy_comments", "legacy_audit_events"} <= tables
        assert set(CORE_TABLES) <= tables
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT title FROM legacy_tickets").scalar() == "MVP"

        for table_name in CORE_TABLES:
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

    command.downgrade(alembic_config(sqlite_url), "base")
    engine = sa.create_engine(sqlite_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert {"tickets", "comments", "audit_events"} <= tables
        assert "users" not in tables and "legacy_tickets" not in tables
    finally:
        engine.dispose()


def test_upgrade_on_empty_database(sqlite_url):
    command.upgrade(alembic_config(sqlite_url), "0001_core")
    engine = sa.create_engine(sqlite_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert "legacy_tickets" not in tables
        assert set(CORE_TABLES) <= tables
    finally:
        engine.dispose()
