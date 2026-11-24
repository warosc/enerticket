"""Entorno de Alembic: usa Base.metadata con todos los modelos y la URL de DATABASE_URL.

Orden de prioridad de la URL: ``config.attributes["sqlalchemy.url"]`` (uso programático,
p.ej. pruebas) → ``-x url=...`` en la línea de comandos → ``DATABASE_URL``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (autoimporta todos los modelos en Base.metadata)
from app.core.config import settings
from app.core.db import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("sqlalchemy.url")
    if url:
        return str(url)
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("url"):
        return x_args["url"]
    return settings.database_url


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    # Las tablas legacy_* (Fase 1, renombradas en 0001_core) no forman parte de los modelos:
    # autogenerate no debe proponer borrarlas.
    if type_ == "table" and name and name.startswith("legacy_"):
        return False
    return True


def _configure_kwargs(url: str) -> dict:
    return {
        "target_metadata": target_metadata,
        "include_object": _include_object,
        "compare_type": True,
        # SQLite no soporta ALTER completo: las migraciones usan modo batch.
        "render_as_batch": url.startswith("sqlite"),
    }


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_configure_kwargs(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, **_configure_kwargs(str(connection.engine.url)))
        with context.begin_transaction():
            context.run_migrations()
        return

    url = _database_url()
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            context.configure(connection=conn, **_configure_kwargs(url))
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
