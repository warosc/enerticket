"""Tareas Celery del módulo SLA.

``sla.check_deadlines`` se ejecuta cada minuto (beat): detecta advertencias y vencimientos
y emite ``sla.warning`` / ``sla.breached`` (idempotente).
"""

from __future__ import annotations

from app.services.sla.deadlines import check_deadlines
from app.tasks import celery_app


@celery_app.task(name="sla.check_deadlines", ignore_result=True)
def check_deadlines_task() -> dict[str, int]:
    return check_deadlines()


beat_schedule = {
    "sla-check-deadlines": {
        "task": "sla.check_deadlines",
        "schedule": 60.0,
        # Si el worker está saturado, no acumular ejecuciones viejas: la siguiente cubre todo.
        "options": {"expires": 55},
    },
}
