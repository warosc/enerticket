"""Usuario técnico "Automatización" con el que actúan las reglas y las ejecuciones diferidas.

El usuario se guarda con ``keycloak_sub = "system:automation"`` (no puede obtenerse por OIDC
ni por los headers dev, que usan ``dev:<usuario>``) y sin roles en BD para no aparecer en
los selectores de agentes; el ``Principal`` en memoria tiene privilegios de administrador.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.security import ROLE_ADMIN, ROLE_AGENT, Principal
from app.models.core import User

SYSTEM_SUB = "system:automation"
SYSTEM_USERNAME = "sistema.automatizacion"
SYSTEM_DISPLAY_NAME = "Automatización"


def get_system_user(session: Session) -> User:
    user = session.scalar(select(User).where(User.keycloak_sub == SYSTEM_SUB))
    if user is None:
        user = User(
            keycloak_sub=SYSTEM_SUB,
            username=SYSTEM_USERNAME,
            display_name=SYSTEM_DISPLAY_NAME,
            email=None,
            roles=[],
            active=True,
            created_at=utcnow(),
        )
        session.add(user)
        session.flush()
    return user


def get_system_principal(session: Session) -> Principal:
    user = get_system_user(session)
    return Principal(
        user_id=user.id,
        sub=user.keycloak_sub,
        username=user.username,
        display_name=user.display_name,
        email=None,
        roles={ROLE_ADMIN, ROLE_AGENT},
        team_ids=set(),
        department_ids=set(),
    )


def is_system_user_id(session: Session, user_id: int | None) -> bool:
    if user_id is None:
        return False
    user = session.get(User, user_id)
    return bool(user and user.keycloak_sub == SYSTEM_SUB)
