"""Modelos ORM. Importa automáticamente todos los submódulos de ``app.models``.

Cada módulo (core, sla, approvals, ...) define sus tablas sobre ``app.core.db.Base``;
al importar este paquete todas quedan registradas en ``Base.metadata`` (Alembic y pruebas).
"""

from app.core.discovery import import_submodules

import_submodules(__name__)
