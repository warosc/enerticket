"""Datos de demostración (solo si ``SEED_DEMO=true``).

Crea usuarios de laboratorio con ``keycloak_sub = "dev:<usuario>"`` (coinciden con los
headers de ``AUTH_MODE=dev``) y tickets de ejemplo en varios estados. Se ejecuta después
de los seeds de módulos (prefijo 90) para que SLA/flujos reaccionen a los eventos.
Idempotente: si ya existen tickets de los usuarios demo no crea más.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.security import build_principal
from app.models.core import Department, Team, TeamMember, Ticket, User
from app.services import tickets as svc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DemoUser:
    username: str
    display_name: str
    roles: tuple[str, ...]
    department: str | None = None
    team_role: str = "member"


DEMO_USERS = (
    DemoUser("demo.admin", "Administrador Demo", ("admin", "agent", "requester")),
    DemoUser("ana.telecom", "Ana López (Telecom)", ("agent", "requester"), "Telecomunicaciones", "lead"),
    DemoUser("luis.soc", "Luis Pérez (SOC)", ("agent", "requester"), "Ciberseguridad / SOC"),
    DemoUser("rosa.redes", "Rosa Méndez (Redes)", ("agent", "requester"), "Seguridad de Redes"),
    DemoUser("marta.aprobadora", "Marta Díaz (Aprobadora)", ("approver", "requester")),
    DemoUser("carlos.solicitante", "Carlos Ramírez", ("requester",)),
    DemoUser("sofia.solicitante", "Sofía Hernández", ("requester",)),
)

# título, descripción, departamento, prioridad, tipo, solicitante, asignado, estados a recorrer
DEMO_TICKETS = (
    ("Caída de enlace MPLS en agencia Mazatenango", "La agencia reporta sin conectividad desde las 08:00.",
     "Telecomunicaciones", "critical", "incident", "carlos.solicitante", "ana.telecom",
     ("triage", "in_progress")),
    ("Solicitud de VPN para contratista", "Acceso temporal por 30 días para proveedor de medidores.",
     "Seguridad de Redes", "medium", "service_request", "sofia.solicitante", "rosa.redes",
     ("assigned",)),
    ("Alerta de phishing dirigido a finanzas", "Correo sospechoso con adjunto .html reportado por 3 usuarios.",
     "Ciberseguridad / SOC", "high", "incident", "carlos.solicitante", "luis.soc",
     ("in_progress", "pending")),
    ("Revisión trimestral de accesos privilegiados", "Evidencia para auditoría interna.",
     "Riesgo y Cumplimiento", "low", "service_request", "sofia.solicitante", None, ()),
    ("Cambio de firmware en switches de núcleo", "Ventana propuesta: sábado 22:00 a 02:00.",
     "Telecomunicaciones", "high", "change", "ana.telecom", "ana.telecom",
     ("in_progress", "resolved")),
    ("Lentitud recurrente en portal de clientes", "Tiempos de respuesta superiores a 8 s en horas pico.",
     "Proyectos TI", "medium", "problem", "carlos.solicitante", None, ("triage",)),
    ("Reemplazo de teléfono IP en recepción", "El equipo no enciende.",
     "General", "low", "incident", "sofia.solicitante", None, ("in_progress", "resolved", "closed")),
)


def _ensure_user(session: Session, demo: DemoUser) -> User:
    sub = f"dev:{demo.username}"
    user = session.scalar(select(User).where(User.keycloak_sub == sub))
    if user is None:
        user = User(
            keycloak_sub=sub,
            username=demo.username,
            display_name=demo.display_name,
            email=f"{demo.username}@demo.local",
            roles=sorted(demo.roles),
            active=True,
            created_at=utcnow(),
        )
        session.add(user)
        session.flush()
    if demo.department:
        team = session.scalar(
            select(Team)
            .join(Department, Department.id == Team.department_id)
            .where(Department.name == demo.department)
            .order_by(Team.id)
        )
        if team is not None and session.get(TeamMember, (team.id, user.id)) is None:
            session.add(TeamMember(team_id=team.id, user_id=user.id, role=demo.team_role))
            session.flush()
    return user


def run(session: Session) -> None:
    if not settings.seed_demo:
        logger.info("SEED_DEMO desactivado: no se crean datos de demostración")
        return
    users = {demo.username: (_ensure_user(session, demo), demo) for demo in DEMO_USERS}
    demo_ids = [user.id for user, _ in users.values()]
    existing = session.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.requester_id.in_(demo_ids))
    )
    if existing:
        logger.info("Ya existen %s tickets de demostración; no se crean más", existing)
        return

    def principal_for(username: str):
        user, demo = users[username]
        return build_principal(session, user, set(demo.roles))

    admin = principal_for("demo.admin")
    departments = {d.name: d.id for d in session.scalars(select(Department)).all()}
    created = 0
    for title, description, dept, priority, type_key, requester, assignee, path in DEMO_TICKETS:
        if dept not in departments:
            logger.warning("Departamento %s inexistente; se omite ticket demo", dept)
            continue
        ticket = svc.create_ticket(
            session,
            principal_for(requester),
            title=title,
            description=description,
            type_key=type_key,
            priority_key=priority,
            department_id=departments[dept],
            enforce_limits=False,  # datos de demostración: sin límites anti-abuso
        )
        created += 1
        try:
            if assignee:
                svc.update_ticket(session, admin, ticket, {"assignee_id": users[assignee][0].id})
            for state_key in path:
                svc.transition_ticket(session, admin, ticket, state_key)
        except HTTPException as exc:
            # Un guard de otro módulo (p.ej. aprobaciones) puede bloquear el recorrido demo.
            logger.info("Ticket demo %s quedó en su estado actual: %s", ticket.key, exc.detail)
    session.flush()
    logger.info("Creados %d tickets de demostración", created)
