"""Configuración de la aplicación cargada desde variables de entorno."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TEAM_GROUPS = (
    "Telecomunicaciones:GG-SD-Telecomunicaciones,"
    "Seguridad de Redes:GG-SD-SeguridadRedes,"
    "Ciberseguridad / SOC:GG-SD-SOC,"
    "Riesgo y Cumplimiento:GG-SD-Riesgo,"
    "Proyectos TI:GG-SD-ProyectosTI,"
    "General:GG-SD-General"
)


class Settings(BaseSettings):
    """Settings del backend. Cada campo se lee de la variable de entorno homónima en mayúsculas."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- Aplicación ---
    app_name: str = "Energuate Service Desk API"
    app_version: str = "0.2.0"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

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
    oidc_leeway_seconds: int = Field(default=30, ge=0, le=300)

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

    # --- Adjuntos (S3/MinIO) ---
    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "servicedesk-attachments"
    s3_region: str = "us-east-1"
    max_attachment_mb: int = Field(default=20, ge=1)

    # --- Correo ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "servicedesk@localhost"
    smtp_starttls: bool = True

    # --- Celery ---
    celery_beat_schedule_file: str = "/tmp/celerybeat-schedule"

    @field_validator("oidc_issuer")
    @classmethod
    def _strip_issuer(cls, value: str) -> str:
        return value.rstrip("/")

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
