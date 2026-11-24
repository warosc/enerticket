"""Catálogo de servicio de demostración (solo si ``SEED_DEMO=true``).

Se ejecuta UNA sola vez (marca ``catalog.demo.v1`` en ``seed_state``), después de
``90_core_demo`` (usuarios y tickets demo). Con ``SEED_DEMO=false`` no hace nada ni deja la
marca (si después se activa la demo, se aplica en el siguiente arranque).

Crea, sin tocar lo que ya exista con el mismo nombre:

- Gerencias demo (Comercial, Distribución, Servicios Corporativos y Tecnología) con su grupo
  AD de gerencia (``GG-SD-Gerencia-*``) y los departamentos de atención de TI asignados a la
  Gerencia de Tecnología (si no tenían gerencia).
- Departamentos solicitantes demo (no atienden tickets) con los valores del AD resueltos
  (``COMERCIAL``, ``DISTRIBUCION``, ``SERVICIOS CORPORATIVOS``, ``SISTEMAS Y
  TELECOMUNICACIONES``), departamento/VIP de los usuarios demo y un visor manual de gerencia.
- El proveedor «Proveedor de Telecomunicaciones» (solo Telecomunicaciones).
- Un catálogo GENÉRICO de TI de ejemplo (categorías y subcategorías inventadas) para los cinco
  departamentos de atención y políticas SLA demo «ANS <departamento> / <categoría>» más una
  «solo VIP». Usan precedencia 4-5 para que en la demo se evalúen antes que las políticas por
  prioridad sembradas (10-40); las importadas por CSV usan 50 (ver docs/REPORTS.md).
- Clasifica los tickets demo existentes y resuelve (con solución) los que el recorrido demo
  dejaba resueltos/cerrados, y agrega tickets históricos resueltos repartidos en los últimos
  meses (fechas, instancias SLA, historial y ``business_resolution_minutes`` coherentes) para
  que los reportes tengan datos. Las notificaciones de los tickets históricos se descartan.

Los datos son aleatorios pero deterministas (semilla fija). No se basa en ningún archivo
externo: los catálogos reales los carga el administrador (Administración → Importar catálogo).
"""

from __future__ import annotations

import calendar
import logging
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import normalize_ad_value
from app.core.config import settings
from app.core.db import utcnow
from app.core.security import Principal, build_principal
from app.core.seed_state import get_seed_state, mark_seed_applied
from app.models.automation import Notification
from app.models.catalog import (
    DepartmentAlias,
    Gerencia,
    GerenciaViewer,
    Provider,
    TicketCategory,
    TicketSubcategory,
)
from app.models.core import Department, Team, TeamMember, Ticket, TicketHistory, User, WorkflowState
from app.models.sla import SlaEvent, SlaInstance, SlaPolicy
from app.services import tickets as svc
from app.services.sla.calendar import CalendarSpec, add_business_minutes, get_zone
from app.services.sla.engine import (
    METRIC_FIRST_RESPONSE,
    METRIC_RESOLUTION,
    STATUS_BREACHED,
    STATUS_CANCELLED,
    STATUS_MET,
    STATUS_RUNNING,
    SpecCache,
    business_resolution_minutes,
    current_instances,
    default_business_spec,
    default_calendar,
    recompute_targets,
)

logger = logging.getLogger(__name__)

SEED_KEY = "catalog.demo.v1"
RANDOM_SEED = 2026

# Gerencia, grupo AD de la gerencia (sus miembros ven en solo lectura lo que solicita).
GERENCIAS = (
    ("Gerencia Comercial", "GG-SD-Gerencia-Comercial"),
    ("Gerencia de Distribución", "GG-SD-Gerencia-Distribucion"),
    ("Gerencia de Servicios Corporativos", "GG-SD-Gerencia-ServiciosCorporativos"),
    ("Gerencia de Tecnología", "GG-SD-Gerencia-Tecnologia"),
)
TECH_GERENCIA = "Gerencia de Tecnología"

# Departamento solicitante, gerencia, valor del atributo ``department`` en el AD.
REQUESTER_DEPARTMENTS = (
    ("Comercial", "Gerencia Comercial", "COMERCIAL"),
    ("Distribución", "Gerencia de Distribución", "DISTRIBUCION"),
    ("Servicios Corporativos", "Gerencia de Servicios Corporativos", "SERVICIOS CORPORATIVOS"),
    ("Sistemas y Telecomunicaciones", TECH_GERENCIA, "SISTEMAS Y TELECOMUNICACIONES"),
)

# Usuario demo → (departamento, VIP). Coherente con los usuarios de laboratorio de Keycloak
# (keycloak/configure-ldap.sh: atributo department y grupo VIP).
DEMO_PROFILES = {
    "demo.admin": ("Sistemas y Telecomunicaciones", False),
    "ana.telecom": ("Telecomunicaciones", False),
    "luis.soc": ("Sistemas y Telecomunicaciones", False),
    "rosa.redes": ("Sistemas y Telecomunicaciones", False),
    "marta.aprobadora": ("Servicios Corporativos", False),
    "carlos.solicitante": ("Comercial", False),
    "sofia.solicitante": ("Distribución", True),
}
# Visor manual (además del grupo AD): Marta ve en solo lectura lo que solicita la Gerencia Comercial.
DEMO_VIEWERS = (("Gerencia Comercial", "marta.aprobadora"),)

PROVIDER_NAME = "Proveedor de Telecomunicaciones"
PROVIDER_DEPARTMENT = "Telecomunicaciones"

# Agentes demo de cada departamento de atención: (usuario, rol en el equipo). El primero es el
# técnico al que se asignan los tickets históricos.
SERVICE_AGENTS = {
    "Sistemas Comerciales": (("rosa.redes", "member"),),
    "Sistemas Corporativos": (("demo.admin", "member"),),
    "Telecomunicaciones": (("ana.telecom", "lead"),),
    "Seguridad Informática": (("luis.soc", "lead"),),
    "Soporte Técnico": (("rosa.redes", "lead"),),
}

ALL_TYPES = ("incident", "service_request", "work_order")

# Catálogo GENÉRICO de ejemplo: departamento → (categoría, tipos admitidos (vacío = todos),
# subcategorías → título de ejemplo, ANS de resolución en horas hábiles o None).
DEMO_CATALOG: dict[str, tuple[tuple[str, tuple[str, ...], dict[str, str], int | None], ...]] = {
    "Sistemas Comerciales": (
        ("Aplicaciones comerciales", (), {
            "Error de aplicación": "Error al guardar en la aplicación comercial",
            "Lentitud": "La aplicación comercial responde con lentitud",
            "Consulta de información": "Consulta sobre datos de un cliente en el sistema",
        }, 16),
        ("Reportes comerciales", ("service_request", "work_order"), {
            "Nuevo reporte": "Nuevo reporte de gestión comercial",
            "Ajuste de reporte": "Ajuste de columnas en reporte comercial",
        }, 40),
        ("Accesos a sistemas comerciales", ("service_request",), {
            "Alta de usuario": "Alta de usuario en el sistema comercial",
            "Cambio de perfil": "Cambio de perfil de acceso comercial",
            "Baja de usuario": "Baja de usuario por traslado",
        }, 8),
    ),
    "Sistemas Corporativos": (
        ("ERP corporativo", (), {
            "Error de módulo": "Error en módulo del ERP",
            "Configuración": "Configuración de parámetro en el ERP",
            "Reportes": "Reporte del ERP no muestra datos",
        }, 24),
        ("Correo y colaboración", (), {
            "Buzón de correo": "Buzón de correo lleno",
            "Listas de distribución": "Agregar personas a lista de distribución",
            "Calendarios compartidos": "Permisos sobre calendario compartido",
        }, 8),
        ("Portal interno", ("incident", "service_request"), {
            "Publicación de contenido": "Publicar documento en el portal interno",
            "Error en el portal": "El portal interno muestra un error",
        }, None),
    ),
    "Telecomunicaciones": (
        ("Enlaces de datos", ("incident", "work_order"), {
            "Caída de enlace": "Sin enlace de datos en agencia",
            "Lentitud de enlace": "Enlace de datos con alta latencia",
            "Nuevo enlace": "Instalación de nuevo enlace de datos",
        }, 8),
        ("Telefonía", (), {
            "Telefonía IP": "Teléfono IP sin tono",
            "Telefonía móvil": "Línea móvil sin servicio de datos",
            "Extensiones": "Reasignación de extensión telefónica",
        }, 16),
        ("Red local", (), {
            "Punto de red": "Habilitar punto de red en oficina",
            "Red inalámbrica": "Señal inalámbrica débil en sala de reuniones",
            "Switches": "Puerto de switch sin conectividad",
        }, 24),
    ),
    "Seguridad Informática": (
        ("Accesos remotos", ("service_request",), {
            "VPN": "Acceso VPN para trabajo remoto",
            "Acceso de proveedor": "Acceso temporal para proveedor externo",
        }, 16),
        ("Incidentes de seguridad", ("incident",), {
            "Correo sospechoso": "Reporte de correo sospechoso",
            "Software malicioso": "Alerta de antivirus en equipo",
            "Fuga de información": "Posible envío de información a externos",
        }, 4),
        ("Reglas de firewall", ("service_request", "work_order"), {
            "Apertura de puerto": "Apertura de puerto para aplicación interna",
            "Publicación de servicio": "Publicar servicio web hacia internet",
        }, 24),
    ),
    "Soporte Técnico": (
        ("Equipo de cómputo", (), {
            "Falla de hardware": "Computadora no enciende",
            "Instalación de software": "Instalar software de oficina",
            "Reemplazo de equipo": "Reemplazo de computadora por obsolescencia",
        }, 16),
        ("Impresión", ("incident", "service_request"), {
            "Impresora": "Impresora atasca el papel",
            "Escáner": "El escáner no envía a la carpeta",
        }, 8),
        ("Periféricos", ("service_request",), {
            "Monitor": "Solicitud de monitor adicional",
            "Teclado y mouse": "Cambio de teclado y mouse",
            "Diadema": "Diadema para llamadas",
        }, None),
    ),
}

# Política «solo VIP» de ejemplo (departamento, primera respuesta y resolución en minutos).
VIP_POLICY = ("ANS VIP / Soporte Técnico", "Soporte Técnico", 30, 4 * 60)
DEMO_POLICY_PRECEDENCE = 5
VIP_POLICY_PRECEDENCE = 4

# Clasificación de los tickets de 90_core_demo: título → (departamento, categoría, subcategoría,
# canal, proveedor?, solución si queda resuelto, meses atrás para fecharlo si está resuelto).
EXISTING_TICKETS = {
    "Caída de enlace MPLS en agencia Mazatenango": (
        "Telecomunicaciones", "Enlaces de datos", "Caída de enlace", "phone", True, None, None,
    ),
    "Cambio de firmware en switches de núcleo": (
        "Telecomunicaciones", "Red local", "Switches", "portal", False,
        "Firmware actualizado en la ventana aprobada; switches verificados sin pérdida de servicio.", 1,
    ),
    "Reemplazo de teléfono IP en recepción": (
        None, None, None, "phone", False,
        "Se reemplazó el teléfono IP y se probó la extensión con el usuario.", 2,
    ),
}
# Estados a los que el recorrido de 90_core_demo lleva cada ticket que debe quedar resuelto.
EXISTING_TARGET_STATE = {
    "Cambio de firmware en switches de núcleo": "resolved",
    "Reemplazo de teléfono IP en recepción": "closed",
}

HISTORY_MONTHS = 6
HISTORY_PER_MONTH = 7
HISTORY_CURRENT_MONTH = 3
HISTORY_DEPARTMENT_WEIGHTS = (
    ("Telecomunicaciones", 3),
    ("Soporte Técnico", 3),
    ("Seguridad Informática", 2),
    ("Sistemas Comerciales", 2),
    ("Sistemas Corporativos", 2),
)
HISTORY_REQUESTERS = (
    ("carlos.solicitante", 4),
    ("sofia.solicitante", 3),
    ("marta.aprobadora", 2),
    ("luis.soc", 1),
)
TYPE_WEIGHTS = (("incident", 55), ("service_request", 35), ("work_order", 10))
PRIORITY_WEIGHTS = (("low", 25), ("medium", 45), ("high", 22), ("critical", 8))
CHANNEL_WEIGHTS = (("portal", 60), ("email", 20), ("phone", 15), ("other", 5))
RESOLUTION_NOTES = (
    "Se atendió la solicitud y se confirmó con el usuario que quedó funcionando.",
    "Se aplicó la corrección y se verificó el servicio junto con el solicitante.",
    "Se realizó la configuración solicitada y se documentó en la base de conocimiento.",
    "Se reinició el servicio afectado; el usuario confirma la normalidad.",
    "Se reemplazó el componente dañado y se hicieron pruebas de funcionamiento.",
    "Se coordinó con el proveedor la atención y se validó el restablecimiento.",
)
WORK_ORDER_NOTE = "Se requiere un enlace nuevo; se traslada a orden de trabajo para su instalación."


# --------------------------------------------------------------------------- utilidades


def _weighted(rng: random.Random, options: tuple[tuple[Any, int], ...]) -> Any:
    values = [value for value, _ in options]
    weights = [weight for _, weight in options]
    return rng.choices(values, weights=weights, k=1)[0]


def _get_or_create_department(session: Session, name: str, **defaults: Any) -> Department:
    department = session.scalar(select(Department).where(Department.name == name))
    if department is None:
        department = Department(name=name, **defaults)
        session.add(department)
        session.flush()
    return department


def _demo_users(session: Session) -> dict[str, User]:
    subs = {f"dev:{username}": username for username in DEMO_PROFILES}
    rows = session.scalars(select(User).where(User.keycloak_sub.in_(list(subs)))).all()
    return {subs[user.keycloak_sub]: user for user in rows}


def _principal(session: Session, user: User) -> Principal:
    return build_principal(session, user, set(user.roles or []))


# --------------------------------------------------------------------------- estructura


def _seed_gerencias(session: Session) -> dict[str, Gerencia]:
    gerencias: dict[str, Gerencia] = {}
    for name, group in GERENCIAS:
        gerencia = session.scalar(select(Gerencia).where(Gerencia.name == name))
        if gerencia is None:
            gerencia = Gerencia(name=name, external_group=group, active=True)
            session.add(gerencia)
            session.flush()
        gerencias[name] = gerencia
    return gerencias


def _seed_requester_departments(session: Session, gerencias: dict[str, Gerencia]) -> dict[str, Department]:
    departments: dict[str, Department] = {}
    for name, gerencia_name, ad_value in REQUESTER_DEPARTMENTS:
        department = _get_or_create_department(
            session, name, serves_tickets=False, active=True, gerencia_id=gerencias[gerencia_name].id
        )
        if department.gerencia_id is None:
            department.gerencia_id = gerencias[gerencia_name].id
        departments[name] = department
        normalized = normalize_ad_value(ad_value)
        alias = session.scalar(select(DepartmentAlias).where(DepartmentAlias.normalized_value == normalized))
        if alias is None:
            session.add(
                DepartmentAlias(
                    normalized_value=normalized,
                    raw_value=ad_value,
                    department_id=department.id,
                    created_at=utcnow(),
                )
            )
        elif alias.department_id is None:  # valor ya visto en un login y pendiente de asignar
            alias.department_id = department.id
    tech = gerencias[TECH_GERENCIA]
    for name in DEMO_CATALOG:
        department = session.scalar(select(Department).where(Department.name == name))
        if department is not None and department.gerencia_id is None:
            department.gerencia_id = tech.id
    session.flush()
    return departments


def _seed_profiles(session: Session, users: dict[str, User], gerencias: dict[str, Gerencia]) -> None:
    departments = {d.name: d for d in session.scalars(select(Department)).all()}
    for username, (department_name, vip) in DEMO_PROFILES.items():
        user = users.get(username)
        department = departments.get(department_name)
        if user is None:
            continue
        if user.department_id is None and department is not None:
            user.department_id = department.id
        if vip and not user.vip:
            user.vip = True
    for gerencia_name, username in DEMO_VIEWERS:
        user = users.get(username)
        gerencia = gerencias.get(gerencia_name)
        if user is not None and gerencia is not None and session.get(GerenciaViewer, (gerencia.id, user.id)) is None:
            session.add(GerenciaViewer(gerencia_id=gerencia.id, user_id=user.id))
    for department_name, members in SERVICE_AGENTS.items():
        department = departments.get(department_name)
        if department is None:
            continue
        team = session.scalar(select(Team).where(Team.department_id == department.id).order_by(Team.id))
        if team is None:
            continue
        for username, role in members:
            user = users.get(username)
            if user is not None and session.get(TeamMember, (team.id, user.id)) is None:
                session.add(TeamMember(team_id=team.id, user_id=user.id, role=role))
    session.flush()


def _seed_provider(session: Session) -> Provider | None:
    department = session.scalar(select(Department).where(Department.name == PROVIDER_DEPARTMENT))
    provider = session.scalar(select(Provider).where(Provider.name == PROVIDER_NAME))
    if provider is None:
        provider = Provider(
            name=PROVIDER_NAME,
            department_ids=[department.id] if department is not None else [],
            active=True,
        )
        session.add(provider)
        session.flush()
    return provider


def _seed_catalog(session: Session) -> tuple[dict[tuple[str, str], TicketCategory], dict[tuple[str, str, str], TicketSubcategory]]:
    categories: dict[tuple[str, str], TicketCategory] = {}
    subcategories: dict[tuple[str, str, str], TicketSubcategory] = {}
    for department_name, entries in DEMO_CATALOG.items():
        department = session.scalar(select(Department).where(Department.name == department_name))
        if department is None or not department.serves_tickets or not department.active:
            logger.info("Departamento de atención %s no disponible; se omite su catálogo demo", department_name)
            continue
        for order, (category_name, type_keys, subs, _hours) in enumerate(entries, start=1):
            category = session.scalar(
                select(TicketCategory).where(
                    TicketCategory.department_id == department.id, TicketCategory.name == category_name
                )
            )
            if category is None:
                category = TicketCategory(
                    department_id=department.id,
                    name=category_name,
                    type_keys=list(type_keys),
                    active=True,
                    sort_order=order * 10,
                )
                session.add(category)
                session.flush()
            categories[(department_name, category_name)] = category
            for sub_order, sub_name in enumerate(subs, start=1):
                subcategory = session.scalar(
                    select(TicketSubcategory).where(
                        TicketSubcategory.category_id == category.id, TicketSubcategory.name == sub_name
                    )
                )
                if subcategory is None:
                    subcategory = TicketSubcategory(
                        category_id=category.id, name=sub_name, active=True, sort_order=sub_order * 10
                    )
                    session.add(subcategory)
                    session.flush()
                subcategories[(department_name, category_name, sub_name)] = subcategory
    return categories, subcategories


def _seed_policies(session: Session, categories: dict[tuple[str, str], TicketCategory]) -> int:
    calendar_ = default_calendar(session)
    if calendar_ is None:
        logger.info("Sin calendario hábil por defecto: no se crean políticas SLA demo")
        return 0
    now = utcnow()
    created = 0

    def add(name: str, description: str, **fields: Any) -> None:
        nonlocal created
        if session.scalar(select(SlaPolicy.id).where(SlaPolicy.name == name)) is not None:
            return
        session.add(
            SlaPolicy(
                name=name,
                description=description,
                calendar_id=calendar_.id,
                priority_keys=[],
                type_keys=[],
                warning_threshold_pct=75,
                pause_categories=["waiting"],
                pause_states=[],
                active=True,
                created_at=now,
                updated_at=now,
                **fields,
            )
        )
        created += 1

    for department_name, entries in DEMO_CATALOG.items():
        for category_name, _types, _subs, hours in entries:
            category = categories.get((department_name, category_name))
            if category is None or hours is None:
                continue
            add(
                f"ANS {department_name} / {category_name}",
                "Política de demostración por categoría (catálogo demo).",
                department_ids=[category.department_id],
                category_ids=[category.id],
                subcategory_ids=[],
                vip_only=False,
                precedence=DEMO_POLICY_PRECEDENCE,
                first_response_minutes=30 if hours <= 4 else 60,
                resolution_minutes=hours * 60,
            )
    name, department_name, first_response, resolution = VIP_POLICY
    department = session.scalar(select(Department).where(Department.name == department_name))
    if department is not None:
        add(
            name,
            "Política de demostración: solicitantes VIP de Soporte Técnico.",
            department_ids=[department.id],
            category_ids=[],
            subcategory_ids=[],
            vip_only=True,
            precedence=VIP_POLICY_PRECEDENCE,
            first_response_minutes=first_response,
            resolution_minutes=resolution,
        )
    session.flush()
    return created


# --------------------------------------------------------------------------- fechas coherentes


def _apply_times(
    session: Session,
    cache: SpecCache,
    ticket: Ticket,
    *,
    created_at: datetime,
    first_response_at: datetime,
    resolved_at: datetime,
    closed_at: datetime | None,
) -> None:
    """Fecha el ticket resuelto en el pasado: ticket, instancias SLA, eventos e historial."""
    had_first_response = ticket.first_response_at is not None
    ticket.created_at = created_at
    ticket.first_response_at = first_response_at if had_first_response else None
    ticket.resolved_at = resolved_at
    ticket.closed_at = closed_at
    ticket.updated_at = closed_at or resolved_at

    instances = session.scalars(select(SlaInstance).where(SlaInstance.ticket_id == ticket.id)).all()
    for instance in instances:
        end = first_response_at if instance.metric == METRIC_FIRST_RESPONSE else resolved_at
        instance.started_at = created_at
        instance.created_at = created_at
        instance.paused_at = None
        instance.paused_business_seconds = 0
        instance.warned_at = None
        instance.breached_at = None
        if instance.status == STATUS_CANCELLED:
            instance.stopped_at = created_at
            instance.updated_at = created_at
        else:
            spec = cache.get(instance.calendar_id)
            instance.status = STATUS_RUNNING  # recompute_targets no recalcula vencidas
            recompute_targets(spec, instance)
            instance.stopped_at = end
            instance.updated_at = end
            if instance.warning_at is not None and end >= instance.warning_at:
                instance.warned_at = instance.warning_at
            if instance.target_at is not None and end > instance.target_at:
                instance.status = STATUS_BREACHED
                instance.breached_at = instance.target_at
            else:
                instance.status = STATUS_MET
        for event in session.scalars(select(SlaEvent).where(SlaEvent.instance_id == instance.id)).all():
            event.at = created_at if event.type == "started" else (instance.stopped_at or created_at)
    session.flush()
    ticket.business_resolution_minutes = business_resolution_minutes(session, ticket, resolved_at)

    after_assign = min(created_at + timedelta(minutes=2), first_response_at)
    for entry in session.scalars(select(TicketHistory).where(TicketHistory.ticket_id == ticket.id)).all():
        after = entry.after_json if isinstance(entry.after_json, dict) else {}
        if entry.event_type == "ticket.created":
            entry.timestamp = created_at
        elif entry.event_type == "ticket.transitioned":
            status = after.get("status")
            if status == "closed" and closed_at is not None:
                entry.timestamp = closed_at
            elif status in ("resolved", "closed"):
                entry.timestamp = resolved_at
            else:
                entry.timestamp = first_response_at
        elif entry.event_type in ("ticket.updated", "ticket.assigned") and "work_order_id" not in after:
            entry.timestamp = after_assign
        else:
            entry.timestamp = resolved_at
    session.flush()


def _spec_for(cache: SpecCache, instance: SlaInstance | None, fallback: CalendarSpec) -> CalendarSpec:
    if instance is None:
        return fallback
    try:
        return cache.get(instance.calendar_id)
    except (LookupError, ValueError):
        return fallback


def _durations(rng: random.Random, first_target: int | None, resolution_target: int | None) -> tuple[int, int]:
    """Minutos hábiles de primera respuesta y de resolución (~15 % fuera de objetivo)."""
    first_target = first_target or 120
    resolution_target = resolution_target or 8 * 60
    first_factor = rng.uniform(1.2, 2.0) if rng.random() < 0.12 else rng.uniform(0.1, 0.9)
    resolution_factor = rng.uniform(1.1, 2.2) if rng.random() < 0.18 else rng.uniform(0.08, 0.95)
    return max(3, int(first_target * first_factor)), max(15, int(resolution_target * resolution_factor))


def _backdate(
    session: Session,
    cache: SpecCache,
    ticket: Ticket,
    created_at: datetime,
    rng: random.Random,
    now: datetime,
    *,
    closed: bool = False,
) -> datetime:
    """Calcula fechas coherentes con el SLA del ticket desde ``created_at`` y las aplica."""
    instances = current_instances(session, ticket.id)
    first_instance = instances.get(METRIC_FIRST_RESPONSE)
    resolution_instance = instances.get(METRIC_RESOLUTION)
    fallback = default_business_spec(session, created_at, now)
    first_minutes, resolution_minutes = _durations(
        rng,
        first_instance.target_minutes if first_instance else None,
        resolution_instance.target_minutes if resolution_instance else None,
    )
    first_response_at = add_business_minutes(_spec_for(cache, first_instance, fallback), created_at, first_minutes)
    resolved_at = add_business_minutes(
        _spec_for(cache, resolution_instance, fallback), created_at, resolution_minutes
    )
    limit = now - timedelta(hours=1)
    resolved_at = max(min(resolved_at, limit), created_at + timedelta(minutes=15))
    first_response_at = max(min(first_response_at, resolved_at), created_at + timedelta(minutes=1))
    closed_at = min(resolved_at + timedelta(days=rng.randint(1, 3)), limit) if closed else None
    if closed_at is not None and closed_at < resolved_at:
        closed_at = resolved_at
    _apply_times(
        session,
        cache,
        ticket,
        created_at=created_at,
        first_response_at=first_response_at,
        resolved_at=resolved_at,
        closed_at=closed_at,
    )
    return resolved_at


def _business_moment(rng: random.Random, zone, year: int, month: int, max_day: int | None = None) -> datetime:
    """Instante en horario de oficina (día hábil) del mes indicado, en UTC."""
    last_day = calendar.monthrange(year, month)[1]
    if max_day is not None:
        last_day = max(1, min(last_day, max_day))
    day = 1
    for _ in range(20):
        day = rng.randint(1, last_day)
        if date(year, month, day).weekday() < 5:
            break
    local = datetime(year, month, day, rng.randint(8, 15), rng.choice((0, 10, 15, 20, 30, 40, 45, 50)), tzinfo=zone)
    return local.astimezone(timezone.utc)


def _months_back(today: date, count: int) -> list[tuple[int, int]]:
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        months.append((year, month))
    return list(reversed(months))


# --------------------------------------------------------------------------- tickets


def _state_key(session: Session, ticket: Ticket) -> str | None:
    state = session.get(WorkflowState, ticket.status_id)
    return state.key if state is not None else None


def _classify_existing(
    session: Session,
    users: dict[str, User],
    categories: dict[tuple[str, str], TicketCategory],
    subcategories: dict[tuple[str, str, str], TicketSubcategory],
    provider: Provider | None,
    rng: random.Random,
    now: datetime,
) -> int:
    """Clasifica los tickets de 90_core_demo y completa/fecha los que deben quedar resueltos."""
    demo_ids = [user.id for user in users.values()]
    if not demo_ids:
        return 0
    admin_user = users.get("demo.admin")
    if admin_user is None:
        return 0
    admin = _principal(session, admin_user)
    cache = SpecCache(session)
    zone = get_zone(settings.tz_business or "America/Guatemala")
    today = now.astimezone(zone).date()
    updated = 0
    tickets = session.scalars(
        select(Ticket).where(Ticket.requester_id.in_(demo_ids), Ticket.title.in_(list(EXISTING_TICKETS)))
    ).all()
    for ticket in tickets:
        department_name, category_name, sub_name, channel, with_provider, note, months_ago = EXISTING_TICKETS[ticket.title]
        requester = session.get(User, ticket.requester_id)
        if requester is not None:
            ticket.requester_department_id = requester.department_id
            ticket.vip = bool(requester.vip)
        department = session.get(Department, ticket.department_id)
        category = categories.get((department_name, category_name)) if category_name else None
        subcategory = subcategories.get((department_name, category_name, sub_name)) if sub_name else None
        if department is not None and department.name != department_name:
            category = subcategory = None
        target = EXISTING_TARGET_STATE.get(ticket.title)
        done = _state_key(session, ticket) in ("resolved", "closed")
        try:
            with session.begin_nested():
                if not done and target is None:
                    # Ticket abierto: se clasifica como lo haría un agente (re-evalúa el SLA).
                    changes: dict[str, Any] = {"channel": channel}
                    if category is not None:
                        changes["category_id"] = category.id
                    if subcategory is not None:
                        changes["subcategory_id"] = subcategory.id
                    if with_provider and provider is not None and provider.applies_to(ticket.department_id):
                        changes["provider_id"] = provider.id
                    svc.update_ticket(session, admin, ticket, changes)
                else:
                    # Resuelto (o por resolver): datos de clasificación directos, sin re-evaluar el SLA.
                    ticket.channel = channel
                    if category is not None:
                        ticket.category_id = category.id
                        ticket.subcategory_id = subcategory.id if subcategory is not None else None
                    session.flush()
            updated += 1
        except HTTPException as exc:
            logger.info("Ticket demo %s no se pudo clasificar: %s", ticket.key, exc.detail)
            continue
        if not done and target is not None:
            # 90_core_demo no pudo resolverlo (p.ej. le faltaba la solución o un guard de
            # aprobación lo detuvo): se intenta completar; si un guard lo impide, queda como está.
            try:
                with session.begin_nested():
                    for step in ("in_progress", "resolved", "closed"):
                        current = _state_key(session, ticket)
                        if current == target or current == "closed":
                            break
                        if step == "in_progress" and current in ("in_progress", "pending"):
                            continue
                        svc.transition_ticket(session, admin, ticket, step, resolution_note=note)
            except HTTPException as exc:
                logger.info("Ticket demo %s quedó en su estado actual: %s", ticket.key, exc.detail)
            done = _state_key(session, ticket) in ("resolved", "closed")
        if not done:
            continue
        if not (ticket.resolution_note or "").strip():
            ticket.resolution_note = note
        if ticket.resolved_by_id is None:
            ticket.resolved_by_id = ticket.assignee_id if ticket.assignee_id is not None else admin.user_id
        if months_ago:
            year, month = _months_back(today, months_ago)[0]
            created_at = _business_moment(rng, zone, year, month, max_day=20)
            _backdate(
                session, cache, ticket, created_at, rng, now,
                closed=_state_key(session, ticket) == "closed",
            )
    session.flush()
    return updated


def _resolve_flow(
    session: Session,
    admin: Principal,
    agent: Principal,
    ticket: Ticket,
    assignee: User,
    note: str,
) -> None:
    svc.update_ticket(session, admin, ticket, {"assignee_id": assignee.id})
    svc.transition_ticket(session, agent, ticket, "in_progress")
    svc.transition_ticket(session, agent, ticket, "resolved", resolution_note=note)


def _create_history(
    session: Session,
    users: dict[str, User],
    categories: dict[tuple[str, str], TicketCategory],
    subcategories: dict[tuple[str, str, str], TicketSubcategory],
    provider: Provider | None,
    rng: random.Random,
    now: datetime,
) -> list[int]:
    """Tickets históricos resueltos, repartidos en los últimos meses."""
    admin_user = users.get("demo.admin")
    if admin_user is None or not categories:
        return []
    admin = _principal(session, admin_user)
    zone = get_zone(settings.tz_business or "America/Guatemala")
    today = now.astimezone(zone).date()
    cache = SpecCache(session)
    departments = {d.name: d for d in session.scalars(select(Department)).all()}

    slots: list[tuple[int, int, int | None]] = []
    for year, month in _months_back(today, HISTORY_MONTHS):
        slots.extend((year, month, None) for _ in range(HISTORY_PER_MONTH))
    if today.day >= 4:
        slots.extend((today.year, today.month, today.day - 2) for _ in range(HISTORY_CURRENT_MONTH))

    created_ids: list[int] = []
    work_order_done = False
    for year, month, max_day in slots:
        department_name = _weighted(rng, HISTORY_DEPARTMENT_WEIGHTS)
        department = departments.get(department_name)
        entries = DEMO_CATALOG[department_name]
        category_name, type_keys, subs, _hours = rng.choice(entries)
        category = categories.get((department_name, category_name))
        agent_username = SERVICE_AGENTS[department_name][0][0]
        assignee = users.get(agent_username)
        if department is None or category is None or assignee is None:
            continue
        sub_name = rng.choice(list(subs))
        subcategory = subcategories.get((department_name, category_name, sub_name))
        allowed = type_keys or ALL_TYPES
        type_key = _weighted(rng, tuple((key, weight) for key, weight in TYPE_WEIGHTS if key in allowed))
        requester_name = _weighted(
            rng, tuple((name, weight) for name, weight in HISTORY_REQUESTERS if name != agent_username)
        )
        requester = users.get(requester_name)
        if requester is None:
            continue
        use_provider = (
            provider is not None
            and provider.applies_to(department.id)
            and category_name == "Enlaces de datos"
            and rng.random() < 0.7
        )
        created_at = _business_moment(rng, zone, year, month, max_day)
        agent = _principal(session, assignee)
        make_work_order = (
            not work_order_done
            and department_name == "Telecomunicaciones"
            and category_name == "Enlaces de datos"
            and type_key == "incident"
        )
        try:
            with session.begin_nested():
                ticket = svc.create_ticket(
                    session,
                    admin,
                    title=subs[sub_name],
                    description="Ticket histórico de demostración.",
                    type_key=type_key,
                    priority_key=_weighted(rng, PRIORITY_WEIGHTS),
                    department_id=department.id,
                    category_id=category.id,
                    subcategory_id=subcategory.id if subcategory is not None else None,
                    provider_id=provider.id if use_provider else None,
                    channel=_weighted(rng, CHANNEL_WEIGHTS),
                    enforce_limits=False,
                    requester_id=requester.id,
                )
                if make_work_order:
                    svc.update_ticket(session, admin, ticket, {"assignee_id": assignee.id})
                    svc.transition_ticket(session, agent, ticket, "in_progress")
                    new_link = subcategories.get((department_name, category_name, "Nuevo enlace"))
                    child = svc.create_work_order(
                        session,
                        agent,
                        ticket,
                        category_id=category.id,
                        subcategory_id=new_link.id if new_link is not None else None,
                        resolve_parent=True,
                        resolution_note=WORK_ORDER_NOTE,
                    )
                    parent_resolved = _backdate(session, cache, ticket, created_at, rng, now)
                    _resolve_flow(session, admin, agent, child, assignee, rng.choice(RESOLUTION_NOTES))
                    _backdate(session, cache, child, parent_resolved, rng, now)
                    created_ids.extend((ticket.id, child.id))
                    work_order_done = True
                else:
                    _resolve_flow(session, admin, agent, ticket, assignee, rng.choice(RESOLUTION_NOTES))
                    _backdate(session, cache, ticket, created_at, rng, now)
                    created_ids.append(ticket.id)
        except HTTPException as exc:
            logger.info("Ticket histórico demo omitido: %s", exc.detail)
    session.flush()
    return created_ids


def _discard_notifications(session: Session, ticket_ids: list[int]) -> None:
    """Las notificaciones (y correos pendientes) de los tickets históricos no tienen sentido."""
    if not ticket_ids:
        return
    from app.services.notifications import EMAIL_QUEUE_KEY

    rows = session.scalars(select(Notification).where(Notification.ticket_id.in_(ticket_ids))).all()
    removed = {row.id for row in rows}
    for row in rows:
        session.delete(row)
    queue = session.info.get(EMAIL_QUEUE_KEY)
    if queue:
        session.info[EMAIL_QUEUE_KEY] = [item for item in queue if item not in removed]
    session.flush()


# --------------------------------------------------------------------------- entrada


def run(session: Session) -> None:
    if not settings.seed_demo:
        logger.info("SEED_DEMO desactivado: no se crea el catálogo de demostración")
        return
    if get_seed_state(session, SEED_KEY) is not None:
        return
    rng = random.Random(RANDOM_SEED)
    now = utcnow()
    users = _demo_users(session)
    gerencias = _seed_gerencias(session)
    _seed_requester_departments(session, gerencias)
    _seed_profiles(session, users, gerencias)
    provider = _seed_provider(session)
    categories, subcategories = _seed_catalog(session)
    policies = _seed_policies(session, categories)
    classified = _classify_existing(session, users, categories, subcategories, provider, rng, now)
    history_ids = _create_history(session, users, categories, subcategories, provider, rng, now)
    _discard_notifications(session, history_ids)
    mark_seed_applied(
        session,
        SEED_KEY,
        {
            "categories": len(categories),
            "subcategories": len(subcategories),
            "policies": policies,
            "classified_tickets": classified,
            "history_ticket_ids": history_ids,
        },
    )
    logger.info(
        "Catálogo demo: %d categorías, %d subcategorías, %d políticas SLA, %d tickets históricos",
        len(categories),
        len(subcategories),
        policies,
        len(history_ids),
    )
