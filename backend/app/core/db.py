"""Motor SQLAlchemy, sesión, base declarativa y tipos portables (PostgreSQL / SQLite)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Fecha/hora actual en UTC con zona horaria."""
    return datetime.now(timezone.utc)


class TZDateTime(TypeDecorator):
    """DateTime(timezone=True) que siempre devuelve valores con zona UTC.

    SQLite no conserva la zona horaria; este tipo normaliza a UTC al guardar y
    marca como UTC los valores leídos, de modo que las comparaciones son
    consistentes en PostgreSQL (runtime) y SQLite (pruebas).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Base declarativa compartida por todos los modelos (core y módulos)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _configure_sqlite(engine: Engine, *, explicit_transactions: bool) -> None:
    """Activa claves foráneas y, en SQLite de archivo, transacciones explícitas (SAVEPOINT fiable).

    En SQLite en memoria (pruebas, StaticPool con una única conexión compartida entre
    sesiones) se mantiene el modo de transacción de pysqlite, que no abre transacciones
    para lecturas; así varias sesiones pueden turnarse sobre la misma conexión.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover - trivial
        if explicit_transactions:
            dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    if explicit_transactions:

        @event.listens_for(engine, "begin")
        def _on_begin(conn: Any) -> None:  # pragma: no cover - trivial
            conn.exec_driver_sql("BEGIN")


def is_memory_sqlite(url: str) -> bool:
    return url in ("sqlite://", "sqlite:///:memory:") or "mode=memory" in url


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Crea un engine adecuado para la URL (SQLite en memoria usa StaticPool)."""
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        memory = is_memory_sqlite(url)
        if memory:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, echo=echo, **kwargs)
        _configure_sqlite(engine, explicit_transactions=not memory)
        return engine
    return create_engine(url, echo=echo, pool_pre_ping=True, pool_size=10, max_overflow=20)


engine: Engine = make_engine(settings.database_url, echo=settings.db_echo)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependencia FastAPI: abre una sesión por request y la cierra al final."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
