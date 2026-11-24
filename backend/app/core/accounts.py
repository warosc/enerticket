"""Vigencia de cuentas: roles y equipos solo se sincronizan con Keycloak/AD al iniciar sesión.

Si alguien deja un grupo de AD (o la empresa) y no vuelve a entrar, ``users.roles`` y
``team_members`` conservan el último estado conocido. Para que esa información vieja no
siga produciendo efectos, una cuenta sin inicio de sesión en ``ACCOUNT_STALE_DAYS`` días se
considera «no vigente» y:

- no recibe notificaciones (ni en la aplicación ni por correo),
- no se designa como aprobador,
- no puede ser asignada a tickets nuevos.

Al volver a iniciar sesión se sincronizan sus roles/equipos y vuelve a ser vigente.
``last_login_at`` nulo (cuentas creadas por seeds o importaciones) cuenta como vigente.
``ACCOUNT_STALE_DAYS=0`` desactiva la regla.

También sincroniza en cada login el perfil que viene del AD (``sync_profile``): departamento
(claim ``department`` resuelto con ``department_aliases``) y VIP (grupo ``VIP_GROUP``).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, or_, true
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.models.core import User


def stale_cutoff() -> datetime | None:
    days = settings.account_stale_days
    return utcnow() - timedelta(days=days) if days else None


def recent_login_clause() -> ColumnElement[bool]:
    """Cláusula WHERE: la cuenta inició sesión en el periodo de vigencia (o nunca se registró)."""
    cutoff = stale_cutoff()
    if cutoff is None:
        return true()
    return or_(User.last_login_at.is_(None), User.last_login_at >= cutoff)


def is_stale(user: User) -> bool:
    cutoff = stale_cutoff()
    return bool(cutoff and user.last_login_at is not None and user.last_login_at < cutoff)


# --------------------------------------------------------------------------- perfil (JIT)


def is_vip_member(groups: set[str]) -> bool:
    """¿Pertenece al grupo VIP (``VIP_GROUP``)? Sin distinguir mayúsculas, como el AD."""
    vip_group = (settings.vip_group or "").strip().lower()
    return bool(vip_group) and vip_group in {g.strip().lower() for g in groups}


def sync_profile(
    session: Session, user: User, department_raw: str | None, groups: set[str] | None
) -> bool:
    """Actualiza ``users.department_id`` y ``users.vip`` con los datos del login.

    - ``department_raw`` (claim ``department`` del AD): se guarda normalizado en
      ``users.ad_department_value``. Si resuelve a un departamento (``department_aliases``) se
      asigna. Si el alias está pendiente (valor nuevo) o sin departamento y el valor **cambió**
      respecto del último login (o no se conocía), se retira el departamento anterior: el
      usuario ya no pertenece a él y su gerencia no debe seguir viendo sus tickets nuevos. Si el
      valor no cambió se respeta el ajuste manual del administrador.
      ``None``/vacío = el token no lo trae: no se toca.
    - ``groups``: ``None`` = el token no trae grupos, no se toca ``vip``.
    Devuelve True si hubo cambios (incluida la creación de un alias nuevo).
    """
    from app.core.catalog import get_or_create_alias

    changed = False
    if department_raw is not None and str(department_raw).strip():
        alias, created = get_or_create_alias(session, department_raw)
        changed = created
        if alias is not None:
            value_changed = user.ad_department_value != alias.normalized_value
            if value_changed:
                user.ad_department_value = alias.normalized_value
                changed = True
            if alias.department_id is not None:
                if user.department_id != alias.department_id:
                    user.department_id = alias.department_id
                    changed = True
            elif value_changed and user.department_id is not None:
                user.department_id = None
                changed = True
    if groups is not None:
        vip = is_vip_member(groups)
        if bool(user.vip) != vip:
            user.vip = vip
            changed = True
    return changed


def apply_alias_to_users(session: Session, normalized_value: str, department_id: int | None) -> dict[str, int]:
    """Aplica de inmediato la asignación de un alias del AD (no hace commit).

    Los usuarios cuyo último valor del AD es ``normalized_value`` pasan a ``department_id``
    (o quedan sin departamento si se retira). Si se asigna un departamento, los tickets de esos
    usuarios que se crearon sin departamento solicitante (alias pendiente) lo completan, así la
    gerencia los ve y los reportes «por gerencia solicitante» los cuentan. Los tickets que ya
    tienen departamento solicitante no se tocan (es la foto del momento en que se crearon).
    """
    from sqlalchemy import select, update

    from app.models.core import Ticket

    session.flush()
    user_ids = list(
        session.scalars(select(User.id).where(User.ad_department_value == normalized_value)).all()
    )
    if not user_ids:
        return {"users": 0, "tickets": 0}
    if department_id is None:
        differs = User.department_id.is_not(None)
    else:
        differs = or_(User.department_id.is_(None), User.department_id != department_id)
    users = session.execute(
        update(User)
        .where(User.id.in_(user_ids), differs)
        .values(department_id=department_id)
        .execution_options(synchronize_session=False)
    ).rowcount or 0
    tickets = 0
    if department_id is not None:
        tickets = session.execute(
            update(Ticket)
            .where(Ticket.requester_id.in_(user_ids), Ticket.requester_department_id.is_(None))
            .values(requester_department_id=department_id)
            .execution_options(synchronize_session=False)
        ).rowcount or 0
    session.expire_all()
    return {"users": users, "tickets": tickets}
