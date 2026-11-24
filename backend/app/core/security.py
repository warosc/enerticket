"""Autenticación (OIDC Keycloak o modo dev), provisión JIT de usuarios y roles.

Uso típico en un router::

    @router.get("/algo")
    def algo(principal: Principal = Depends(get_principal)): ...

    @router.post("/admin/x", dependencies=[Depends(require_roles("admin"))])
    def solo_admin(): ...
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.models.core import Team, TeamMember, User

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_AGENT = "agent"
ROLE_APPROVER = "approver"
ROLE_REQUESTER = "requester"
KNOWN_ROLES = frozenset({ROLE_ADMIN, ROLE_AGENT, ROLE_APPROVER, ROLE_REQUESTER})
# Prefijo del ``keycloak_sub`` de usuarios técnicos (no obtenible por OIDC ni por headers dev).
SYSTEM_SUB_PREFIX = "system:"

LAST_LOGIN_REFRESH = timedelta(minutes=5)
_DEV_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,150}$")
# Formato aceptado para el ``kid`` del JWT antes de consultar el JWKS (Keycloak usa base64url).
_KID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,128}$")


@dataclass
class Principal:
    """Identidad autenticada de la petición actual."""

    user_id: int
    sub: str
    username: str
    display_name: str
    email: str | None
    roles: set[str]
    team_ids: set[int]
    department_ids: set[int]
    groups: set[str] = field(default_factory=set)
    lead_team_ids: set[int] = field(default_factory=set)

    @property
    def is_admin(self) -> bool:
        return ROLE_ADMIN in self.roles

    @property
    def is_agent(self) -> bool:
        """True si tiene rol ``agent`` o ``admin`` (privilegios de agente)."""
        return ROLE_AGENT in self.roles or self.is_admin

    @property
    def is_approver(self) -> bool:
        return ROLE_APPROVER in self.roles or self.is_admin

    @property
    def is_system(self) -> bool:
        """Usuario técnico (p.ej. automatizaciones, ``sub="system:automation"``): no es una persona."""
        return self.sub.startswith(SYSTEM_SUB_PREFIX)

    def has_role(self, *roles: str) -> bool:
        """True si tiene alguno de los roles indicados (admin cumple cualquier rol)."""
        return self.is_admin or any(role in self.roles for role in roles)


@dataclass
class Identity:
    """Datos de identidad extraídos del token o de los headers dev."""

    sub: str
    username: str
    display_name: str | None  # None = conservar el nombre ya registrado (modo dev sin X-Dev-Name)
    email: str | None
    roles: set[str]
    groups: set[str] | None  # None = el token no trae el claim; no se sincronizan equipos


class AuthError(Exception):
    """Error de autenticación con código HTTP asociado."""

    def __init__(self, detail: str, status_code: int = status.HTTP_401_UNAUTHORIZED) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code

    def to_http(self) -> HTTPException:
        headers = {"WWW-Authenticate": "Bearer"} if self.status_code == 401 else None
        return HTTPException(status_code=self.status_code, detail=self.detail, headers=headers)


# --------------------------------------------------------------------------- JWKS / JWT

def _log_value(value: object, limit: int = 120) -> str:
    """Representación segura para logs de valores controlados por el cliente (sin saltos)."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...'"


class ThrottledJWKClient(jwt.PyJWKClient):
    """``PyJWKClient`` que no se deja usar para amplificar peticiones contra Keycloak.

    PyJWT vuelve a descargar el JWKS cada vez que llega un ``kid`` desconocido; con un token
    basura y un ``kid`` aleatorio cualquiera podría forzar una petición HTTP síncrona por
    request. Aquí:

    - un ``kid`` desconocido refresca el JWKS como mucho una vez cada
      ``min_refresh_interval`` segundos (rotación de claves real: se detecta enseguida);
    - las descargas se serializan y se reutiliza una descarga recién hecha (sin estampida);
    - tras un fallo de conexión se responde 503 sin volver a intentar durante
      ``failure_backoff`` segundos.
    """

    def __init__(
        self,
        uri: str,
        *,
        min_refresh_interval: float = 60.0,
        failure_backoff: float = 5.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(uri, **kwargs)
        self.min_refresh_interval = min_refresh_interval
        self.failure_backoff = failure_backoff
        self._fetch_lock = threading.Lock()
        self._last_forced_refresh = float("-inf")
        self._last_fetch_ok = float("-inf")
        self._last_failure = float("-inf")
        self._last_data: Any = None

    def fetch_data(self) -> Any:
        with self._fetch_lock:
            now = time.monotonic()
            if self._last_data is not None and now - self._last_fetch_ok < 1.0:
                return self._last_data  # otro hilo acaba de descargarlo
            if now - self._last_failure < self.failure_backoff:
                raise jwt.PyJWKClientConnectionError(
                    "JWKS no disponible (se reintentará en unos segundos)"
                )
            try:
                data = super().fetch_data()
            except jwt.PyJWKClientConnectionError:
                self._last_failure = time.monotonic()
                raise
            self._last_fetch_ok = time.monotonic()
            self._last_data = data
            return data

    def get_signing_key(self, kid: str) -> jwt.PyJWK:
        signing_key = self.match_kid(self.get_signing_keys(), kid)
        if signing_key is not None:
            return signing_key
        now = time.monotonic()
        refresh = False
        with self._fetch_lock:
            if now - self._last_forced_refresh >= self.min_refresh_interval:
                self._last_forced_refresh = now
                refresh = True
        if refresh:
            signing_key = self.match_kid(self.get_signing_keys(refresh=True), kid)
        if signing_key is None:
            raise jwt.PyJWKClientError("El token usa una clave de firma desconocida")
        return signing_key


_jwks_client: jwt.PyJWKClient | None = None
_jwks_lock = threading.Lock()


def get_jwks_client() -> jwt.PyJWKClient:
    """Cliente JWKS con caché (se descarga de OIDC_JWKS_URL, URL interna)."""
    global _jwks_client
    if _jwks_client is None:
        with _jwks_lock:
            if _jwks_client is None:
                _jwks_client = ThrottledJWKClient(
                    settings.jwks_url,
                    cache_jwk_set=True,
                    lifespan=settings.oidc_jwks_cache_seconds,
                    timeout=5,
                    min_refresh_interval=settings.oidc_jwks_min_refresh_seconds,
                )
    return _jwks_client


def reset_jwks_client() -> None:
    """Descarta el cliente JWKS en caché (p.ej. tras cambiar la configuración)."""
    global _jwks_client
    with _jwks_lock:
        _jwks_client = None


def decode_access_token(token: str) -> dict[str, Any]:
    """Valida firma, emisor (issuer público), audiencia y expiración del JWT."""
    if not token:
        raise AuthError("Token de acceso requerido")
    if len(token) > 16384:
        raise AuthError("Token inválido")
    # Validación barata del encabezado ANTES de tocar el JWKS (no se consulta Keycloak por
    # tokens con algoritmos no permitidos o un ``kid`` con formato inválido).
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise AuthError("Token inválido") from exc
    alg = header.get("alg")
    kid = header.get("kid")
    if alg not in settings.oidc_algorithm_list:
        logger.info("Token rechazado: algoritmo no permitido %s", _log_value(alg))
        raise AuthError("Token inválido")
    if not isinstance(kid, str) or not _KID_RE.fullmatch(kid):
        logger.info("Token rechazado: kid inválido %s", _log_value(kid))
        raise AuthError("Token inválido")
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError as exc:
        logger.error("No se pudo descargar el JWKS de %s: %s", settings.jwks_url, _log_value(str(exc), 300))
        raise AuthError(
            "Servicio de identidad no disponible", status.HTTP_503_SERVICE_UNAVAILABLE
        ) from exc
    except (jwt.PyJWKClientError, jwt.InvalidTokenError) as exc:
        logger.info("Token rechazado (clave de firma, kid=%s): %s", _log_value(kid), _log_value(str(exc)))
        raise AuthError("Token inválido") from exc
    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=settings.oidc_algorithm_list,
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            leeway=settings.oidc_leeway_seconds,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expirado") from exc
    except jwt.InvalidTokenError as exc:
        logger.info("Token rechazado: %s", _log_value(str(exc)))
        raise AuthError("Token inválido") from exc


def _normalize_group(name: str) -> str:
    # Admite "/Grupo" o rutas completas "/Padre/Grupo": se usa el último segmento.
    return name.strip().rstrip("/").rsplit("/", 1)[-1].strip()


def identity_from_claims(claims: dict[str, Any]) -> Identity:
    """Convierte claims de Keycloak en una ``Identity``."""
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise AuthError("Token sin claim 'sub'")
    username = str(claims.get("preferred_username") or sub)
    display_name = str(claims.get("name") or username)
    email = claims.get("email") or None
    realm_access = claims.get("realm_access") or {}
    raw_roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    roles = {str(role) for role in raw_roles or [] if role in KNOWN_ROLES}
    roles.add(ROLE_REQUESTER)
    groups: set[str] | None = None
    if "groups" in claims or settings.oidc_missing_groups_as_empty:
        # Keycloak omite el claim cuando el usuario no tiene grupos: sin claim = sin grupos
        # (así quien sale de todos los grupos AD pierde sus equipos mapeados).
        raw_groups = claims.get("groups") or []
        if isinstance(raw_groups, str):
            raw_groups = [raw_groups]
        groups = {_normalize_group(str(g)) for g in raw_groups if str(g).strip()}
    return Identity(
        sub=sub,
        username=username[:150],
        display_name=display_name[:200],
        email=str(email)[:255] if email else None,
        roles=roles,
        groups=groups,
    )


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def identity_from_dev(
    username: str | None,
    roles: Iterable[str] | str | None = None,
    groups: Iterable[str] | str | None = None,
    display_name: str | None = None,
    email: str | None = None,
) -> Identity:
    """Identidad de laboratorio (AUTH_MODE=dev). ``groups=None`` no sincroniza equipos."""
    username = (username or "").strip()
    if not username:
        raise AuthError("Encabezado X-Dev-User requerido en AUTH_MODE=dev")
    if not _DEV_USERNAME_RE.match(username):
        raise AuthError("X-Dev-User contiene caracteres no permitidos")
    role_list = _split_csv(roles) if isinstance(roles, str) or roles is None else list(roles)
    role_set = {role for role in role_list if role in KNOWN_ROLES}
    role_set.add(ROLE_REQUESTER)
    group_set: set[str] | None = None
    if groups is not None:
        group_list = _split_csv(groups) if isinstance(groups, str) else list(groups)
        group_set = {_normalize_group(g) for g in group_list if g.strip()}
    return Identity(
        sub=f"dev:{username}",
        username=username,
        display_name=display_name.strip()[:200] if display_name and display_name.strip() else None,
        email=email or None,
        roles=role_set,
        groups=group_set,
    )


# --------------------------------------------------------------------------- Provisión JIT


def sync_team_memberships(session: Session, user: User, groups: set[str]) -> bool:
    """Sincroniza ``team_members`` según grupos ↔ ``teams.external_group``.

    Solo afecta a equipos con ``external_group`` definido; los equipos sin grupo
    externo se administran manualmente y no se tocan. Comparación sin distinguir
    mayúsculas (AD no las distingue). Devuelve True si hubo cambios.
    """
    wanted = {g.lower() for g in groups}
    mapped_teams = session.scalars(select(Team).where(Team.external_group.is_not(None))).all()
    current = {
        m.team_id: m
        for m in session.scalars(select(TeamMember).where(TeamMember.user_id == user.id)).all()
    }
    changed = False
    for team in mapped_teams:
        in_group = (team.external_group or "").strip().lower() in wanted
        membership = current.get(team.id)
        if in_group and membership is None:
            session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
            changed = True
        elif not in_group and membership is not None:
            session.delete(membership)
            changed = True
    if changed:
        session.flush()
    return changed


def _claim_demo_user(session: Session, identity: Identity) -> User | None:
    """Laboratorio: vincula un usuario demo (``dev:<usuario>``) con la cuenta OIDC homónima.

    Con ``AUTH_MODE=oidc`` y ``SEED_DEMO=true`` los tickets demo pertenecen a usuarios
    ``dev:<usuario>``; al primer login real del mismo ``preferred_username`` se reasigna el
    ``keycloak_sub`` para que la persona vea sus datos demo. Nunca en producción.
    """
    if settings.is_production or settings.auth_mode != "oidc" or identity.sub.startswith("dev:"):
        return None
    demo = session.scalar(select(User).where(User.keycloak_sub == f"dev:{identity.username}"))
    if demo is None:
        return None
    logger.info("Vinculando usuario demo %s con la cuenta OIDC %s", demo.username, identity.sub)
    demo.keycloak_sub = identity.sub
    session.flush()
    return demo


def _upsert_user(session: Session, identity: Identity) -> tuple[User, bool]:
    """Crea o actualiza el usuario por ``keycloak_sub``. Devuelve (usuario, hubo_cambios)."""
    user = session.scalar(select(User).where(User.keycloak_sub == identity.sub))
    now = utcnow()
    roles = sorted(identity.roles)
    claimed = False
    if user is None:
        user = _claim_demo_user(session, identity)
        claimed = user is not None
    if user is None:
        user = User(
            keycloak_sub=identity.sub,
            username=identity.username,
            display_name=identity.display_name or identity.username,
            email=identity.email,
            roles=roles,
            active=True,
            created_at=now,
            last_login_at=now,
        )
        session.add(user)
        session.flush()
        return user, True
    changed = claimed
    if user.username != identity.username:
        user.username = identity.username
        changed = True
    if identity.display_name and user.display_name != identity.display_name:
        user.display_name = identity.display_name
        changed = True
    if identity.email and user.email != identity.email:
        user.email = identity.email
        changed = True
    if list(user.roles or []) != roles:
        user.roles = roles
        changed = True
    if user.last_login_at is None or now - user.last_login_at > LAST_LOGIN_REFRESH:
        user.last_login_at = now
        changed = True
    return user, changed


def build_principal(
    session: Session, user: User, roles: set[str], groups: set[str] | None = None
) -> Principal:
    """Construye el ``Principal`` a partir del usuario y su pertenencia a equipos."""
    rows = session.execute(
        select(TeamMember.team_id, TeamMember.role, Team.department_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == user.id)
    ).all()
    return Principal(
        user_id=user.id,
        sub=user.keycloak_sub,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        roles=set(roles),
        team_ids={row.team_id for row in rows},
        department_ids={row.department_id for row in rows},
        groups=set(groups or ()),
        lead_team_ids={row.team_id for row in rows if row.role == "lead"},
    )


def provision_principal(session: Session, identity: Identity) -> Principal:
    """Provisión JIT: upsert del usuario, sincronización de equipos y commit si hubo cambios."""
    for attempt in range(2):
        try:
            user, changed = _upsert_user(session, identity)
            if not user.active:
                session.rollback()
                raise AuthError("Usuario desactivado", status.HTTP_403_FORBIDDEN)
            if identity.groups is not None and sync_team_memberships(session, user, identity.groups):
                changed = True
            if changed:
                session.commit()
            return build_principal(session, user, identity.roles, identity.groups)
        except IntegrityError:
            # Dos peticiones simultáneas del mismo usuario nuevo: reintentar una vez.
            session.rollback()
            if attempt == 1:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover


def principal_from_token(session: Session, token: str) -> Principal:
    """Valida un JWT y devuelve el ``Principal`` (usado también por WebSocket)."""
    return provision_principal(session, identity_from_claims(decode_access_token(token)))


def principal_from_dev(
    session: Session,
    username: str | None,
    roles: Iterable[str] | str | None = None,
    groups: Iterable[str] | str | None = None,
) -> Principal:
    """``Principal`` de laboratorio (solo si AUTH_MODE=dev)."""
    if settings.auth_mode != "dev":
        raise AuthError("Modo dev deshabilitado", status.HTTP_401_UNAUTHORIZED)
    return provision_principal(session, identity_from_dev(username, roles, groups))


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Se requiere encabezado Authorization: Bearer <token>")
    return token.strip()


def get_principal(request: Request, session: Session = Depends(get_db)) -> Principal:
    """Dependencia FastAPI: autentica la petición y devuelve el ``Principal``.

    - ``AUTH_MODE=oidc``: JWT Bearer validado contra Keycloak.
    - ``AUTH_MODE=dev``: headers ``X-Dev-User``, ``X-Dev-Roles`` (csv), ``X-Dev-Groups`` (csv).
    """
    try:
        if settings.auth_mode == "dev":
            headers = request.headers
            identity = identity_from_dev(
                headers.get("x-dev-user"),
                headers.get("x-dev-roles"),
                headers.get("x-dev-groups") if "x-dev-groups" in headers else None,
                headers.get("x-dev-name"),
                headers.get("x-dev-email"),
            )
            principal = provision_principal(session, identity)
        else:
            principal = principal_from_token(session, _bearer_token(request))
    except AuthError as exc:
        raise exc.to_http() from exc
    request.state.principal = principal
    return principal


def require_roles(*roles: str) -> Callable[..., Principal]:
    """Dependencia que exige alguno de los roles indicados (admin siempre pasa)."""
    if not roles:
        raise ValueError("require_roles necesita al menos un rol")

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_role(*roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"Se requiere uno de los roles: {', '.join(roles)}",
            )
        return principal

    dependency.__name__ = f"require_roles_{'_'.join(roles)}"
    return dependency
