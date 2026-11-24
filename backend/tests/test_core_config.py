"""Validaciones de arranque de producción, documentación de la API, root_path y CORS."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import config as config_module
from app.core.config import Settings

PROD_OK = {
    "environment": "production",
    "auth_mode": "oidc",
    "oidc_issuer": "https://sd.example.com/auth/realms/energuate",
    "cors_origins": "https://sd.example.com",
    "seed_demo": False,
    "events_raise_errors": False,  # conftest lo activa por entorno
}


def test_production_settings_accept_safe_configuration():
    settings = Settings(**PROD_OK)
    assert settings.is_production
    assert settings.docs_enabled is False


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"auth_mode": "dev"}, "AUTH_MODE"),
        ({"oidc_issuer": "http://sd.example.com/auth/realms/energuate"}, "OIDC_ISSUER"),
        ({"seed_demo": True}, "SEED_DEMO"),
        ({"cors_origins": "*"}, "CORS_ORIGINS"),
        ({"cors_origins": "http://sd.example.com"}, "CORS_ORIGINS"),
        ({"events_raise_errors": True}, "EVENTS_RAISE_ERRORS"),
        ({"s3_secret_key": "replace-with-strong-s3-secret-key"}, "S3_SECRET_KEY"),
        ({"database_url": "postgresql+psycopg://sd:replace-with-strong-db-password@db/sd"}, "DATABASE_URL"),
        ({"redis_url": "redis://:changeme@redis:6379/0"}, "REDIS_URL"),
        ({"smtp_from": "replace-with-servicedesk@energuate.com"}, "SMTP_FROM"),
        ({"cors_origins": "https://replace-with-servicedesk.example.com"}, "CORS_ORIGINS"),
    ],
)
def test_production_settings_reject_unsafe_configuration(override, fragment):
    with pytest.raises(ValidationError) as excinfo:
        Settings(**{**PROD_OK, **override})
    assert fragment in str(excinfo.value)


def test_development_allows_dev_auth_and_docs():
    settings = Settings(environment="development", auth_mode="dev", oidc_issuer="http://localhost:8081/realms/x")
    assert settings.docs_enabled is True
    assert not settings.is_production


@pytest.mark.parametrize(("raw", "expected"), [("", "development"), ("PROD", "production"), ("dev", "development")])
def test_environment_aliases(raw, expected, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", raw)
    monkeypatch.setenv("EVENTS_RAISE_ERRORS", "false")
    monkeypatch.setenv("SEED_DEMO", "false")
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("CORS_ORIGINS", "https://sd.example.com")
    monkeypatch.setenv("OIDC_ISSUER", "https://sd.example.com/auth/realms/energuate")
    assert Settings().environment == expected


def test_api_docs_flag_and_root_path_normalization(monkeypatch):
    monkeypatch.setenv("API_DOCS", "")
    monkeypatch.setenv("API_ROOT_PATH", "api/")
    settings = Settings()
    assert settings.api_docs is None and settings.docs_enabled is True
    assert settings.api_root_path == "/api"
    assert Settings(**PROD_OK, api_docs=True).docs_enabled is True


def _build_app(monkeypatch, **overrides):
    for key, value in overrides.items():
        monkeypatch.setattr(config_module.settings, key, value)
    from app.main import create_app

    return create_app()


def test_docs_disabled_in_production_app(monkeypatch):
    app = _build_app(monkeypatch, environment="production", api_docs=None)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/health").status_code == 200


def test_root_path_routes_and_cors_expose_headers(monkeypatch):
    app = _build_app(monkeypatch, api_root_path="/api", cors_origins="http://localhost:3000")
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 200
        exposed = response.headers.get("access-control-expose-headers", "")
        assert "Content-Disposition" in exposed and "X-Checksum-SHA256" in exposed
        assert client.get("/openapi.json").json()["servers"][0]["url"] == "/api"
