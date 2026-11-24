"""Redacción de credenciales en logs.

uvicorn registra la ruta completa del handshake (``"WebSocket /ws?token=..." [accepted]``)
en el logger ``uvicorn.error`` y las peticiones HTTP en ``uvicorn.access``; este filtro
reemplaza el valor de ``token`` (y de ``access_token``) por ``***`` antes de escribirlo.
"""

from __future__ import annotations

import logging
import re

_TOKEN_RE = re.compile(r"((?:^|[?&;\s])(?:access_token|token)=)[^&\s\"']+", re.IGNORECASE)
REDACTED = "***"
_TARGET_LOGGERS = ("uvicorn.error", "uvicorn.access", "uvicorn")


def redact(text: str) -> str:
    return _TOKEN_RE.sub(lambda m: m.group(1) + REDACTED, text)


class TokenRedactingFilter(logging.Filter):
    """Filtro de logging que redacta tokens en el mensaje y en sus argumentos."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and "token=" in record.msg.lower():
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple) and record.args:
                record.args = tuple(
                    redact(arg) if isinstance(arg, str) and "token=" in arg.lower() else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    key: redact(value) if isinstance(value, str) and "token=" in value.lower() else value
                    for key, value in record.args.items()
                }
        except Exception:  # pragma: no cover - un filtro nunca debe romper el logging
            pass
        return True


_installed = False


def install_token_redaction() -> None:
    """Instala el filtro en los loggers de uvicorn (idempotente)."""
    global _installed
    if _installed:
        return
    for name in _TARGET_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactingFilter) for f in target.filters):
            target.addFilter(TokenRedactingFilter())
    _installed = True
