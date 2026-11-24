# Energuate Service Desk — Fase 2

Mesa de servicio interna de Energuate: tickets por área (Telecomunicaciones, Seguridad de Redes,
Ciberseguridad / SOC, Riesgo y Cumplimiento, Proyectos TI), SLA mixto con calendario de Guatemala,
tablero Kanban en tiempo real, aprobaciones, automatizaciones, notificaciones, adjuntos y reportes.
La identidad se delega en **Keycloak**, federado con el **Active Directory de Energuate**.

Stack: Next.js 15 · React 19 · TypeScript · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 ·
Redis 7 · Celery · Keycloak 26 · MinIO (S3) · Docker Compose.

Documentación:
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — servicios, flujos de autenticación, eventos y WebSocket.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — modelo de datos y migraciones.
- [`docs/AD_INTEGRATION.md`](docs/AD_INTEGRATION.md) — conexión con el Active Directory real.
- [`docs/FASE2_PLAN.md`](docs/FASE2_PLAN.md) — contrato técnico de la Fase 2.

## Requisitos

- Docker Desktop (Windows 11 con WSL2) o Docker Engine + Docker Compose v2.20 o superior.
- 8 GB de RAM libres para los contenedores (Keycloak y el build de Next.js son los más exigentes).
- Puertos libres en `127.0.0.1`: 3000, 8000, 8081, 9000 y 9001.
- Acceso a los registros `docker.io`, `quay.io` y `cgr.dev`. Detrás de un proxy con inspección TLS,
  instale la CA corporativa en Docker Desktop; **no** desactive la verificación TLS.

## Arranque

```bash
cp .env.example .env            # PowerShell: Copy-Item .env.example .env
# Edite .env: sustituya TODOS los valores replace-... por secretos propios.
docker compose up -d --build
docker compose ps               # espere a que todos los servicios estén "healthy"
docker compose logs -f keycloak-config   # configuración de Keycloak (1-3 minutos)
```

Docker Compose se niega a arrancar si falta un secreto obligatorio (`DB_PASSWORD`, `KC_DB_PASSWORD`,
`KC_ADMIN_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`).

Orden de arranque (lo gestionan los `depends_on` + healthchecks):
`db`, `redis`, `keycloak-db`, `minio` → `keycloak` (importa el realm) → `keycloak-config` (one-shot) ·
`minio-init` (one-shot) → `api` (migraciones Alembic + seeds + uvicorn) → `worker`, `beat`, `web`.

| Servicio | URL (solo en este equipo) | Uso |
|---|---|---|
| Portal web | http://localhost:3000 | Aplicación para usuarios y agentes |
| API / Swagger | http://localhost:8000/docs | API REST y WebSocket (`/ws`) |
| Health API | http://localhost:8000/health | Comprobación de estado |
| Keycloak | http://localhost:8081 | Login (realm `energuate`) y consola de administración (`/admin`) |
| MinIO S3 | http://localhost:9000 | Descarga de adjuntos con URL prefirmada |
| Consola MinIO | http://localhost:9001 | Administración de almacenamiento (credenciales root) |

Todos los puertos se publican **solo en 127.0.0.1**. PostgreSQL, Redis y el puerto de gestión de
Keycloak (9000, health) no se publican: solo son accesibles en la red interna de Docker.

Comandos útiles:

```bash
docker compose up -d keycloak-config      # re-aplicar configuración de Keycloak/AD (idempotente)
docker compose build web && docker compose up -d web   # tras cambiar NEXT_PUBLIC_*
docker compose down                        # detener (conserva datos)
docker compose down -v                     # detener y BORRAR todos los datos (bases, adjuntos, realm)
```

## Modos de autenticación

| | `oidc` (por defecto) | `dev` (solo laboratorio) |
|---|---|---|
| Variables | `AUTH_MODE=oidc`, `NEXT_PUBLIC_AUTH_MODE=oidc` | `AUTH_MODE=dev`, `NEXT_PUBLIC_AUTH_MODE=dev` |
| Login | Keycloak (Authorization Code + PKCE S256) | Selector de usuario/roles/grupos en el portal |
| API | Valida JWT: firma (JWKS), `iss`, `exp`, `aud=servicedesk-api` | Confía en cabeceras `X-Dev-User`, `X-Dev-Roles`, `X-Dev-Groups` |
| Uso | Cualquier entorno | Máquina aislada para desarrollo de UI |

En modo `oidc` la API valida los tokens contra `OIDC_ISSUER` (URL pública,
`http://localhost:8081/realms/energuate`) y descarga las claves de `OIDC_JWKS_URL` (URL interna
`http://keycloak:8080/...`). Claims utilizados: `sub`, `preferred_username`, `name`, `email`,
`realm_access.roles` y `groups`. Los usuarios se crean en la base de datos al primer acceso (JIT) y su
pertenencia a equipos se sincroniza según `groups` ↔ `teams.external_group`.

> **`AUTH_MODE=dev` permite suplantar a cualquier usuario, incluido un administrador, con una simple
> cabecera HTTP.** Nunca lo active en un equipo accesible por otras personas. `NEXT_PUBLIC_AUTH_MODE`
> se incrusta en el build: reconstruya `web` al cambiarlo.

### Roles

| Rol de realm | Permisos |
|---|---|
| `requester` | Rol por defecto de todo usuario: crea tickets, ve y comenta solo los suyos. |
| `agent` | Gestiona tickets de sus equipos/departamentos, los asignados a él y los que creó. |
| `approver` | Además de lo anterior como solicitante, decide sobre aprobaciones dirigidas a él. |
| `admin` | Acceso total: catálogos, departamentos, equipos, miembros y todos los tickets. |

Los roles se asignan por grupo con `KC_GROUP_ROLE_MAP` (por defecto
`GG-SD-Admins:admin,GG-SD-Agentes:agent,GG-SD-Aprobadores:approver`). Los equipos del Service Desk se
vinculan con grupos de AD mediante `teams.external_group` (seeds: `SEED_TEAM_GROUPS`).

## Usuarios de laboratorio

Con `KC_LAB_USERS=true`, `keycloak-config` crea usuarios **locales** de Keycloak, todos con la
contraseña de `KC_LAB_PASSWORD` (mínimo 12 caracteres, no temporal):

| Usuario | Roles | Grupos |
|---|---|---|
| `admin.lab` | admin | `GG-SD-Admins` |
| `agente.telecom` | agent | `GG-SD-Agentes`, `GG-SD-Telecomunicaciones` |
| `agente.soc` | agent | `GG-SD-Agentes`, `GG-SD-SOC` |
| `aprobador.lab` | approver | `GG-SD-Aprobadores` |
| `usuario.lab` | requester | — |

Todos tienen además el rol por defecto `requester`. Con `KC_LAB_USERS=false` (valor por defecto) los
usuarios de laboratorio existentes se **deshabilitan** en la siguiente ejecución de `keycloak-config`.
No use usuarios de laboratorio en un entorno conectado al AD de producción.

## Active Directory de Energuate

La federación se activa definiendo `LDAP_URL` y el resto de variables `LDAP_*` en `.env`; después:

```bash
docker compose up -d keycloak-config && docker compose logs -f keycloak-config
```

Resumen (detalle en [`docs/AD_INTEGRATION.md`](docs/AD_INTEGRATION.md)):
- Cuenta de servicio **de solo lectura**; Keycloak no escribe en el AD (`READ_ONLY`).
- Usuario = `sAMAccountName`, identificador estable = `objectGUID`.
- Grupos del AD importados con `group-ldap-mapper` (solo lectura) y asignados a roles con `KC_GROUP_ROLE_MAP`.
- Cifrado obligatorio: `ldaps://` o `ldap://` + `LDAP_START_TLS=true`. La CA corporativa se copia en
  `keycloak/truststore/` (no se versiona) y Keycloak la carga mediante `KC_TRUSTSTORE_PATHS`.
- Con `LDAP_URL` vacío no se configura ninguna federación (solo usuarios locales).

## Variables de entorno

La lista completa, comentada, está en [`.env.example`](.env.example). Grupos principales:
base de datos y Keycloak (`DB_PASSWORD`, `KC_*`), autenticación (`AUTH_MODE`, `OIDC_*`),
frontend (`NEXT_PUBLIC_*`, se aplican en el build), AD (`LDAP_*`), seeds (`SEED_DEMO`,
`SEED_TEAM_GROUPS`), SLA (`TZ_BUSINESS`), adjuntos (`MINIO_ROOT_*`, `S3_*`, `MAX_ATTACHMENT_MB`) y
correo (`SMTP_*`).

## Desarrollo y pruebas

```bash
# Backend (desde backend/)
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest

# Frontend (desde frontend/)
npm ci && npx tsc --noEmit
```

## Advertencias

Esta configuración está pensada para **laboratorio y piloto en un único equipo**. Antes de exponerla
a usuarios de la red corporativa:

- **TLS de extremo a extremo**: Keycloak corre con `start-dev` (HTTP). En un despliegue real use
  `start` con certificado o un proxy inverso con TLS, y actualice `KC_HOSTNAME`, `OIDC_ISSUER`,
  `NEXT_PUBLIC_*`, las redirect URIs del cliente `servicedesk-web` y `CORS_ORIGINS` al dominio HTTPS.
- **Puertos**: se publican solo en `127.0.0.1`; publicarlos en otra interfaz requiere TLS y firewall.
- **Secretos**: `.env` nunca se versiona. Rote todas las contraseñas de ejemplo; cree un administrador
  nominal de Keycloak y elimine la cuenta bootstrap (`KC_ADMIN`).
- **`AUTH_MODE=dev`** y **`KC_LAB_USERS=true`** son exclusivos de laboratorio.
- **Imagen de MinIO**: la imagen oficial `minio/minio` dejó de publicarse en Docker Hub; se usa la
  compilación de Chainguard fijada por digest (`MINIO_IMAGE` / `MINIO_MC_IMAGE` permiten sustituirla
  por la imagen aprobada por Energuate).
- **Operación**: falta definir copias de seguridad (PostgreSQL ×2, volumen de MinIO), monitoreo,
  rotación de logs centralizada y alta disponibilidad.
