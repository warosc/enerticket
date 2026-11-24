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
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, or_, true

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
