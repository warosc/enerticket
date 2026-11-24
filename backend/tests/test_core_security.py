"""Autenticación OIDC: firma (JWKS), issuer público, audiencia, expiración y provisión JIT."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.core import security
from app.core.config import settings
from app.models.core import Team, User
from tests.conftest import DEPARTMENT_SOC

ISSUER = "http://localhost:8081/realms/energuate"
AUDIENCE = "servicedesk-api"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc(monkeypatch, rsa_key):
    """Activa AUTH_MODE=oidc con un JWKS simulado que devuelve la clave pública de prueba."""
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    state = {"error": None}

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, token):
            if state["error"] is not None:
                raise state["error"]
            return SimpleNamespace(key=rsa_key.public_key())

    monkeypatch.setattr(security, "get_jwks_client", lambda: FakeJwksClient())
    return state


def make_token(rsa_key, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": [AUDIENCE, "account"],
        "sub": "0f6c1d9e-1111-2222-3333-444455556666",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "jperez",
        "name": "Juan Pérez",
        "email": "jperez@example.test",
        "realm_access": {"roles": ["agent", "offline_access"]},
        "groups": ["/GG-SD-SOC", "GG-SD-Agentes"],
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": "test-key"})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_valid_token_provisions_user_and_teams(client, db, oidc, rsa_key, ids):
    response = client.get("/me", headers=bearer(make_token(rsa_key)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "jperez"
    assert body["display_name"] == "Juan Pérez"
    assert body["email"] == "jperez@example.test"
    assert body["roles"] == ["agent", "requester"]  # roles desconocidos se ignoran
    soc_team = db.scalar(select(Team).where(Team.id == ids["teams"][DEPARTMENT_SOC]))
    assert [t["id"] for t in body["teams"]] == [soc_team.id]

    user = db.scalar(select(User).where(User.keycloak_sub == "0f6c1d9e-1111-2222-3333-444455556666"))
    assert user is not None and user.username == "jperez"

    # Cambios en el directorio se reflejan en el siguiente login.
    token = make_token(rsa_key, name="Juan A. Pérez", groups=[], realm_access={"roles": []})
    body = client.get("/me", headers=bearer(token)).json()
    assert body["display_name"] == "Juan A. Pérez"
    assert body["teams"] == []
    assert body["roles"] == ["requester"]


def test_token_without_groups_claim_keeps_memberships(client, oidc, rsa_key):
    client.get("/me", headers=bearer(make_token(rsa_key)))
    body = client.get("/me", headers=bearer(make_token(rsa_key, groups=None))).json()
    assert len(body["teams"]) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "otra-api"},
        {"iss": "http://keycloak:8080/realms/energuate"},  # issuer interno ≠ público
        {"exp": int(time.time()) - 3600},
        {"sub": None},
    ],
)
def test_invalid_claims_rejected(client, oidc, rsa_key, overrides):
    response = client.get("/me", headers=bearer(make_token(rsa_key, **overrides)))
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_wrong_signature_rejected(client, oidc, rsa_key):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.get("/me", headers=bearer(make_token(other_key))).status_code == 401


def test_missing_bearer_rejected(client, oidc):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401
    # En modo oidc los headers dev no autentican.
    assert client.get("/me", headers={"X-Dev-User": "admin", "X-Dev-Roles": "admin"}).status_code == 401


def test_jwks_unreachable_returns_503(client, oidc, rsa_key):
    oidc["error"] = jwt.PyJWKClientConnectionError("sin conexión")
    assert client.get("/me", headers=bearer(make_token(rsa_key))).status_code == 503


def test_jwks_client_uses_internal_url(monkeypatch):
    monkeypatch.setattr(settings, "oidc_jwks_url", "http://keycloak:8080/realms/energuate/protocol/openid-connect/certs")
    security.reset_jwks_client()
    try:
        client = security.get_jwks_client()
        assert client.uri == "http://keycloak:8080/realms/energuate/protocol/openid-connect/certs"
        assert security.get_jwks_client() is client  # cacheado
    finally:
        security.reset_jwks_client()


def test_require_roles_admin_passes_everything():
    principal = security.Principal(
        user_id=1, sub="s", username="u", display_name="U", email=None,
        roles={"admin", "requester"}, team_ids=set(), department_ids=set(),
    )
    assert principal.is_agent and principal.is_approver and principal.has_role("agent")
    with pytest.raises(ValueError):
        security.require_roles()
