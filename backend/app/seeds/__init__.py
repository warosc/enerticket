"""Seeds idempotentes. Cada módulo de ``app/seeds`` expone ``run(session)``.

Se ejecutan en orden alfabético (``00_core``, ``1x_sla``, ``2x_flows``, ``90_core_demo``...) y
cada uno se confirma por separado. Ejecutar con ``python -m app.seeds``.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.discovery import submodule_names

logger = logging.getLogger(__name__)


def run_all(session_factory: Callable[[], Session] | None = None) -> list[str]:
    """Ejecuta todos los seeds; devuelve los módulos ejecutados. Propaga el primer error."""
    from app.core.db import SessionLocal
    from app.core.discovery import load_plugins

    load_plugins()
    factory = session_factory or SessionLocal
    executed: list[str] = []
    for name in submodule_names(__name__):
        module = importlib.import_module(name)
        run: Callable[[Session], None] | None = getattr(module, "run", None)
        if run is None:
            logger.warning("El seed %s no define run(session); se omite", name)
            continue
        with factory() as session:
            try:
                run(session)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Falló el seed %s", name)
                raise
        executed.append(name)
        logger.info("Seed aplicado: %s", name)
    return executed
