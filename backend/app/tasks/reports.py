"""Tareas Celery del generador de reportes.

``reports.run_scheduled`` (beat cada 15 min): ejecuta las plantillas con envío programado
vencido (``next_run_at <= ahora``) con el alcance de su dueño, genera el .xlsx y lo envía por
SMTP como adjunto. Sin ``SMTP_HOST`` deja ``last_status = "smtp_disabled"`` sin error.
"""

from __future__ import annotations

from app.services.reports.templates import run_due_templates
from app.tasks import celery_app


@celery_app.task(name="reports.run_scheduled", ignore_result=True)
def run_scheduled() -> dict[str, int]:
    return run_due_templates()


beat_schedule = {
    "reports-run-scheduled": {
        "task": "reports.run_scheduled",
        "schedule": 900.0,
        # Si el worker está saturado no se acumulan ejecuciones: la siguiente cubre lo pendiente.
        "options": {"expires": 840},
    },
}
