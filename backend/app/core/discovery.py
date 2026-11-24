"""Autodescubrimiento de submódulos (modelos, routers, suscriptores, seeds, tareas)."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

logger = logging.getLogger(__name__)


def submodule_names(package_name: str) -> list[str]:
    """Nombres completos de los submódulos directos de un paquete, en orden alfabético.

    Se omiten los módulos cuyo nombre empieza por "_" (p.ej. ``__main__``).
    """
    package = importlib.import_module(package_name)
    paths = getattr(package, "__path__", None)
    if paths is None:
        raise ValueError(f"{package_name} no es un paquete")
    names = [
        info.name
        for info in pkgutil.iter_modules(paths)
        if not info.name.startswith("_")
    ]
    return [f"{package_name}.{name}" for name in sorted(names)]


def import_submodules(package_name: str) -> list[ModuleType]:
    """Importa todos los submódulos directos de ``package_name`` en orden alfabético.

    Un error de importación se propaga: un módulo roto debe detener el arranque
    en lugar de dejar la aplicación a medio configurar.
    """
    modules: list[ModuleType] = []
    for full_name in submodule_names(package_name):
        try:
            modules.append(importlib.import_module(full_name))
        except Exception:
            logger.exception("No se pudo importar el módulo %s", full_name)
            raise
    return modules


_plugins_loaded = False


def load_plugins() -> None:
    """Importa todos los modelos y suscriptores de eventos (idempotente)."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    import_submodules("app.models")
    import_submodules("app.subscribers")
    _plugins_loaded = True
