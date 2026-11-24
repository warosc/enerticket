"""Autenticación del WebSocket: Origin, token OIDC por query o identidad dev.

Los navegadores no permiten cabeceras personalizadas en el handshake de WebSocket, por eso
el token viaja en ``?token=`` (se redacta en los logs, ver ``log_redaction``). En
``AUTH_MODE=dev`` se aceptan ``?dev_user=&dev_roles=&dev_groups=``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import jwt
from fastapi import status

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import AuthError, Principal, principal_from_dev, principal_from_token
from app.services.realtime.config import realtime_settings
from app.services.realtime.connection import (
    CLOSE_FORBIDDEN,
    CLOSE_TRY_AGAIN_LATER,
    CLOSE_UNAUTHORIZED,
)

logger = logging.getLogger(__name__)


@dataclass
class AuthResult:
    principal: Principal
    #: Momento (epoch, segundos) en que la conexión debe cerrarse para renovar credenciales.
    expires_at: float


def _normalize_origin(origin: str) -> str | None:
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    host = parts.hostname.lower()
    default_port = {"http": 80, "https": 443}.get(parts.scheme.lower())
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port is None or port == default_port else f"{host}:{port}"
    return f"{parts.scheme.lower()}://{netloc}"


def origin_allowed(origin: str | None, allowed: list[str] | None = None) -> bool:
    """Valida el encabezado Origin contra ``CORS_ORIGINS``.

    Sin Origin (clientes que no son navegadores) se permite: el ataque que mitiga esta
    verificación (Cross-Site WebSocket Hijacking) solo es posible desde un navegador, que
    siempre envía Origin. La autenticación por token se exige igualmente.
    """
    if origin is None:
        return True
    allowed = settings.cors_origin_list if allowed is None else allowed
    if "*" in allowed:
        return True
    normalized = _normalize_origin(origin)
    if normalized is None:
        return False
    return normalized in {o for o in (_normalize_origin(a) for a in allowed) if o}


def close_code_for(error: AuthError) -> int:
    if error.status_code == status.HTTP_403_FORBIDDEN:
        return CLOSE_FORBIDDEN
    if error.status_code >= 500:
        return CLOSE_TRY_AGAIN_LATER
    return CLOSE_UNAUTHORIZED


def _token_expiry(token: str) -> float | None:
    """``exp`` del token (ya validado por ``principal_from_token``)."""
    try:
        claims: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    exp = claims.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def authenticate(params: Mapping[str, str]) -> AuthResult:
    """Autentica la conexión (síncrono: ejecutar en threadpool). Lanza ``AuthError``."""
    now = time.time()
    max_expiry = now + realtime_settings.max_connection_seconds
    with SessionLocal() as session:
        if settings.auth_mode == "dev":
            if "token" in params and "dev_user" not in params:
                raise AuthError("En AUTH_MODE=dev use dev_user/dev_roles/dev_groups")
            principal = principal_from_dev(
                session,
                params.get("dev_user"),
                params.get("dev_roles"),
                params.get("dev_groups") if "dev_groups" in params else None,
            )
            return AuthResult(principal=principal, expires_at=max_expiry)

        token = (params.get("token") or "").strip()
        if not token:
            raise AuthError("Parámetro token requerido")
        if len(token) > realtime_settings.max_token_length:
            raise AuthError("Token demasiado largo")
        principal = principal_from_token(session, token)
        exp = _token_expiry(token)
        expires_at = min(max_expiry, exp) if exp is not None else max_expiry
        return AuthResult(principal=principal, expires_at=expires_at)
