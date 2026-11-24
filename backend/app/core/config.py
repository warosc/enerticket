"""Configuración de la aplicación cargada desde variables de entorno."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TEAM_GROUPS = (
    "Telecomunicaciones:GG-SD-Telecomunicaciones,"
    "Seguridad de Redes:GG-SD-SeguridadRedes,"
    "Ciberseguridad / SOC:GG-SD-SOC,"
    "Riesgo y Cumplimiento:GG-SD-Riesgo,"
    "Proyectos TI:GG-SD-ProyectosTI,"
    "General:GG-SD-General"
)


_TRIVIAL_SECRETS = frozenset(
    {"changeme", "change-me", "change_me", "password", "secret", "minioadmin", "admin"}
)


def _looks_like_placeholder(value: str | None) -> bool:
    """True si ``value`` es un valor de plantilla (``replace-...``) o un secreto trivial."""
    if not value:
        return False
    lowered = value.strip().lower()
    return "replace-" in lowered or "replace_with" in lowered or lowered in _TRIVIAL_SECRETS


def _url_password(url: str) -> str | None:
    try:
        return urlsplit(url).password
    except ValueError:
        return None


class Settings(BaseSettings):
    """Settings del backend. Cada campo se lee de la variable de entorno homónima en mayúsculas."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- Aplicación ---
    app_name: str = "Energuate Service Desk API"
    app_version: str = "0.2.0"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"
    # production activa validaciones de arranque estrictas (ver ``_check_production``).
    environment: Literal["development", "production"] = "development"
    # Documentación interactiva (/docs, /redoc, /openapi.json). En producción solo si API_DOCS=true.
    api_docs: bool | None = None
    # Prefijo con el que el proxy publica la API (p.ej. "/api"); lo pasa uvicorn como --root-path.
    api_root_path: str = ""

    # --- Persistencia ---
    database_url: str = "sqlite:///./local.db"
    db_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # --- Autenticación ---
    auth_mode: Literal["oidc", "dev"] = "oidc"
    oidc_issuer: str = "http://localhost:8081/realms/energuate"
    oidc_jwks_url: str = ""
    oidc_audience: str = "servicedesk-api"
    oidc_algorithms: str = "RS256"
    oidc_jwks_cache_seconds: int = Field(default=600, ge=30)
    # Un ``kid`` desconocido fuerza como mucho un refresco del JWKS cada N segundos.
    oidc_jwks_min_refresh_seconds: int = Field(default=60, ge=5)
    oidc_leeway_seconds: int = Field(default=30, ge=0, le=300)
    # Keycloak omite el claim "groups" cuando el usuario no pertenece a ningún grupo. Con True
    # (por defecto) un token sin el claim se trata como "sin grupos" y se retiran las membresías
    # de equipos mapeados (external_group); con False no se sincronizan equipos.
    oidc_missing_groups_as_empty: bool = True
    # Grupo AD/Keycloak cuyos miembros son usuarios VIP (se sincroniza en cada login cuando el
    # token trae el claim ``groups``). El departamento llega en el claim ``department``.
    vip_group: str = "GG-SD-VIP"

    # --- Eventos ---
    events_channel: str = "servicedesk.events"
    # Si es True, una excepción en un suscriptor aborta la operación (útil en pruebas).
    events_raise_errors: bool = False
    events_redis_retry_seconds: int = Field(default=10, ge=0)

    # --- Seeds ---
    seed_demo: bool = False
    # Mapa "Departamento:GrupoAD" separado por comas, usado al crear los equipos por defecto.
    seed_team_groups: str = DEFAULT_TEAM_GROUPS

    # --- Negocio / SLA ---
    tz_business: str = "America/Guatemala"
    # Prioridad máxima que puede elegir quien no es agente al crear un ticket (clave de
    # prioridad; vacío = sin límite). Evita que cualquier solicitante dispare el SLA 24x7 y
    # los avisos de tickets críticos; un agente escala la prioridad si corresponde.
    requester_max_priority: str = "high"
    # Tickets que puede crear por hora un usuario que no es agente (0 = sin límite).
    tickets_per_user_hour: int = Field(default=30, ge=0)
    # Días sin iniciar sesión tras los que una cuenta deja de recibir notificaciones y de ser
    # elegible como aprobador/asignado (sus roles solo se sincronizan al iniciar sesión).
    account_stale_days: int = Field(default=45, ge=0)

    # --- Adjuntos (S3/MinIO) ---
    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "servicedesk-attachments"
    s3_region: str = "us-east-1"
    max_attachment_mb: int = Field(default=20, ge=1)
    # Días que se conservan en S3 los adjuntos borrados (lógicamente) antes de purgarlos; 0 = nunca.
    attachment_purge_days: int = Field(default=30, ge=0)
    # Cuota por usuario (no admin) en las últimas 24 h, contando también adjuntos borrados
    # (siguen ocupando espacio hasta la purga). 0 = sin límite.
    attachment_user_daily_mb: int = Field(default=500, ge=0)
    attachment_user_daily_count: int = Field(default=100, ge=0)

    # --- Correo ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "servicedesk@localhost"
    smtp_starttls: bool = True

    # --- Reportes ---
    # Dominios permitidos para los destinatarios de envíos programados (separados por coma;
    # vacío = cualquier dominio).
    report_recipient_domains: str = ""

    # --- Celery ---
    celery_beat_schedule_file: str = "/tmp/celerybeat-schedule"

    @field_validator("api_docs", mode="before")
    @classmethod
    def _empty_docs_is_default(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("environment", mode="before")
    @classmethod
    def _normalize_environment(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower() or "development"
            return {"dev": "development", "prod": "production"}.get(value, value)
        return value

    @field_validator("api_root_path")
    @classmethod
    def _normalize_root_path(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith("/"):
            value = "/" + value
        return value

    @field_validator("oidc_issuer")
    @classmethod
    def _strip_issuer(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        """Rechaza configuraciones inseguras cuando ENVIRONMENT=production (fail-closed)."""
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.auth_mode != "oidc":
            problems.append("AUTH_MODE debe ser 'oidc' (el modo 'dev' autentica por headers)")
        if not self.oidc_issuer.lower().startswith("https://"):
            problems.append("OIDC_ISSUER debe usar https://")
        if self.seed_demo:
            problems.append("SEED_DEMO debe ser false")
        if self.events_raise_errors:
            problems.append("EVENTS_RAISE_ERRORS debe ser false")
        origins = self.cors_origin_list
        if not origins or any(o == "*" or not o.lower().startswith("https://") for o in origins):
            problems.append("CORS_ORIGINS debe listar solo orígenes https:// explícitos")
        # Valores de plantilla (.env.production.example) que nunca deben llegar a producción.
        candidates = {
            "contraseña de DATABASE_URL": _url_password(self.database_url),
            "contraseña de REDIS_URL": _url_password(self.redis_url),
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
            "SMTP_HOST": self.smtp_host,
            "SMTP_USER": self.smtp_user,
            "SMTP_PASSWORD": self.smtp_password,
            "SMTP_FROM": self.smtp_from,
            "OIDC_ISSUER": self.oidc_issuer,
            "CORS_ORIGINS": self.cors_origins,
        }
        placeholders = [name for name, value in candidates.items() if _looks_like_placeholder(value)]
        if placeholders:
            problems.append(
                "conservan valores de ejemplo (replace-...) o triviales: " + ", ".join(placeholders)
            )
        if problems:
            raise ValueError("Configuración no válida para ENVIRONMENT=production: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        """Swagger/ReDoc: activos por defecto salvo en producción (API_DOCS=true los habilita)."""
        if self.api_docs is None:
            return not self.is_production
        return self.api_docs

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def jwks_url(self) -> str:
        """URL interna del JWKS; si no se define se deriva del issuer."""
        if self.oidc_jwks_url.strip():
            return self.oidc_jwks_url.strip()
        return f"{self.oidc_issuer}/protocol/openid-connect/certs"

    @property
    def oidc_algorithm_list(self) -> list[str]:
        return [alg.strip() for alg in self.oidc_algorithms.split(",") if alg.strip()]

    @property
    def team_group_map(self) -> dict[str, str]:
        """Devuelve {nombre_departamento: grupo_externo} a partir de SEED_TEAM_GROUPS."""
        mapping: dict[str, str] = {}
        for item in self.seed_team_groups.split(","):
            if ":" not in item:
                continue
            department, group = item.rsplit(":", 1)
            if department.strip() and group.strip():
                mapping[department.strip()] = group.strip()
        return mapping


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
