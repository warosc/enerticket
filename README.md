# Energuate Service Desk

Mesa de servicio interna de Energuate: tickets por área (Telecomunicaciones, Seguridad de Redes,
Ciberseguridad / SOC, Riesgo y Cumplimiento, Proyectos TI, General), SLA con calendario hábil de
Guatemala, tablero Kanban en tiempo real, aprobaciones, automatizaciones, notificaciones (aplicación
y correo), adjuntos y reportes. La identidad se delega en **Keycloak**, federado en solo lectura con
el **Active Directory de Energuate**.

Stack: Next.js 15 · React 19 · TypeScript · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 ·
Redis 7 · Celery · Keycloak 26 · MinIO (S3) · Caddy 2 · Docker Compose.

## Documentación

| Documento | Contenido |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Servicios, redes, flujos de autenticación, eventos, WebSocket y SLA |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Tablas y migraciones (core, SLA, flujos, adjuntos) |
| [docs/AD_INTEGRATION.md](docs/AD_INTEGRATION.md) | Conexión con el AD real: cuenta de servicio, LDAPS, grupos → roles/equipos, diagnóstico |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Requisitos, DNS/TLS, primer despliegue y checklist de go-live |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Copias, restauración, actualizaciones, rotación de secretos, logs, monitoreo, escalado |
| [docs/SECURITY.md](docs/SECURITY.md) | Modelo de amenazas, controles y checklist de hardening |
| [docs/FASE2_PLAN.md](docs/FASE2_PLAN.md) | Contrato técnico de desarrollo |

## Entornos

| | Laboratorio | Producción |
|---|---|---|
| Archivo | `docker-compose.yml` | `docker-compose.prod.yml` |
| Variables | `.env.example` → `.env` | `.env.production.example` → `.env` (en el servidor) |
| Acceso | `http://localhost:*` (puertos solo en 127.0.0.1) | `https://APP_DOMAIN` (Caddy, único puerto 80/443) |
| Keycloak | `start-dev`, `http://localhost:8081` | `start` detrás del proxy, `https://APP_DOMAIN/auth` |
| Autenticación | `oidc` (o `dev` para UI) | `oidc` fijado |
| Usuarios de prueba | `KC_LAB_USERS=true` opcional | Deshabilitados a la fuerza |

## Estado de implementación

Fase 2 completa e integrada (verificación de integración del 24-11-2026, stack de laboratorio real):

| Módulo | Estado | Verificación |
|---|---|---|
| Núcleo (tickets, workflow, permisos por área, eventos) | Listo | pytest; E2E con tokens OIDC reales |
| Identidad (Keycloak, AD de solo lectura, roles y equipos por grupo) | Listo · AD real pendiente de conectar con `LDAP_*` | Login PKCE de 6 usuarios lab; *direct grants* rechazados |
| SLA mixto (calendario hábil GT, feriados, pausas, avisos) | Listo | `TicketOut.sla`, `/sla/*`, tarea beat `sla.check_deadlines` cada 60 s |
| Kanban + tiempo real (WebSocket) | Listo | Eventos filtrados por permisos (interno oculto al solicitante), Origin y token validados |
| Aprobaciones (etapas, segregación de funciones) | Listo | Cambio → 409 → líder aprueba → CAB aprueba → transición ejecutada |
| Automatizaciones y notificaciones (app + correo) | Listo · correo requiere `SMTP_HOST` | Regla «Crítico» dispara; notificaciones in-app y por WebSocket |
| Adjuntos (MinIO/S3) | Listo | Subida/descarga por la API, lista blanca + firma mágica, purga de borrados |
| Reportes y CSV | Listo | `/reports/*` y `export.csv` (UTF-8 BOM) |
| Producción (Caddy TLS, compose prod, CI, backups) | Listo | `docker compose -f docker-compose.prod.yml config`; arranque rechazado si la configuración es insegura |

Resultados: backend `pytest` 337/337, Alembic con una sola cabeza (`0001_core → 0004_attachments`,
upgrade/downgrade/upgrade), frontend `tsc` y `next build` sin errores, E2E real 72/72.

Pendiente antes del go-live (no bloquea el laboratorio): conectar el AD real (`LDAP_*`, CA en
`keycloak/truststore`), configurar SMTP, certificado/DNS de `APP_DOMAIN` y un ensayo completo de
`scripts/restore.sh` en un entorno desechable (ver [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## Arranque en laboratorio

Requisitos: Docker Desktop (Windows 11 + WSL2) o Docker Engine con Compose v2.24+, 8 GB de RAM
libres, puertos 3000, 8000, 8081, 9000 y 9001 libres en `127.0.0.1`, y acceso a `docker.io`,
`quay.io` y `cgr.dev`. Detrás de un proxy con inspección TLS instale la CA corporativa en Docker;
**no** desactive la verificación TLS.

```bash
cp .env.example .env            # PowerShell: Copy-Item .env.example .env
# Edite .env: sustituya TODOS los replace-... (y KC_LAB_USERS=true si quiere usuarios de prueba)
docker compose up -d --build
docker compose ps               # espere a "healthy" (Keycloak tarda 1-3 min)
docker compose logs -f keycloak-config
```

| Servicio | URL | Uso |
|---|---|---|
| Portal | http://localhost:3000 | Aplicación |
| API | http://localhost:8000/docs · `/health` · `/ws` | REST, Swagger y WebSocket |
| Keycloak | http://localhost:8081 | Login (realm `energuate`) y consola `/admin` |
| MinIO | http://localhost:9001 | Consola de almacenamiento (credenciales root) |

PostgreSQL, Redis y el puerto de gestión de Keycloak (9000) no se publican.

```bash
docker compose up -d keycloak-config      # reaplicar configuración de Keycloak/AD (idempotente)
docker compose build web && docker compose up -d web   # tras cambiar NEXT_PUBLIC_*
docker compose down                        # detener (conserva datos)
docker compose down -v                     # detener y BORRAR todos los datos
```

> **Docker Compose v5 en Windows:** si `docker compose build` (o `up --build`) falla al preparar
> el build con Buildx Bake (error de *entitlements* / permisos `fs`), defina
> `BUILDX_BAKE_ENTITLEMENTS_FS=0` solo para ese comando:
> PowerShell `$env:BUILDX_BAKE_ENTITLEMENTS_FS=0; docker compose build web` ·
> Git Bash/WSL `BUILDX_BAKE_ENTITLEMENTS_FS=0 docker compose build web`.

La pantalla de login usa el tema propio `energuate` (`keycloak/themes/energuate`, montado en
Keycloak y asignado al realm por `keycloak-config`); en laboratorio (`start-dev`) los cambios de
CSS se ven al recargar, sin caché de temas.

### Usuarios de laboratorio

Con `KC_LAB_USERS=true`, `keycloak-config` crea usuarios **locales** de Keycloak con la contraseña
`KC_LAB_PASSWORD` (12+ caracteres). Son los mismos nombres que los datos demo del backend
(`SEED_DEMO=true`):

| Usuario | Roles | Grupos |
|---|---|---|
| `demo.admin` | admin, agent | `GG-SD-Admins`, `GG-SD-Agentes` |
| `ana.telecom` | agent | `GG-SD-Agentes`, `GG-SD-Telecomunicaciones` |
| `luis.soc` | agent | `GG-SD-Agentes`, `GG-SD-SOC` |
| `rosa.redes` | agent | `GG-SD-Agentes`, `GG-SD-SeguridadRedes` |
| `marta.aprobadora` | approver | `GG-SD-Aprobadores` |
| `carlos.solicitante` | requester | — |
| `sofia.solicitante` | requester | — |

Todos tienen además `requester`. Con `KC_LAB_USERS=false` se deshabilitan en la siguiente ejecución.
Con `SEED_DEMO=true`, el primer login de cada usuario lab hereda los tickets y equipos de su usuario
demo homónimo (nunca con `ENVIRONMENT=production`).

Token de prueba (mismo flujo PKCE que el portal; no requiere *password grant*):

```bash
SD_TEST_PASSWORD='<KC_LAB_PASSWORD>' scripts/get-test-token.sh -u ana.telecom --claims
TOKEN=$(SD_TEST_PASSWORD='<KC_LAB_PASSWORD>' scripts/get-test-token.sh -u ana.telecom)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me
```

## Arranque en producción

Resumen (detalle y checklist en [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)):

```bash
git clone https://github.com/warosc/enerticket.git /opt/energuate-sd && cd /opt/energuate-sd
git checkout <tag>
cp .env.production.example .env && chmod 600 .env    # APP_DOMAIN, TLS_MODE, secretos, LDAP_*, SMTP_*
cp <certificado> deploy/certs/tls.crt; cp <clave> deploy/certs/tls.key   # TLS_MODE=files
cp <ca-corporativa>.pem keycloak/truststore/                              # LDAPS
docker compose -f docker-compose.prod.yml config -q
docker compose -f docker-compose.prod.yml up -d --build
scripts/healthcheck.sh
```

Rutas públicas: `/` portal, `/api/*` API, `/ws` WebSocket, `/auth/*` Keycloak (consola solo desde
`ADMIN_ALLOWED_CIDRS`). Copias: `scripts/backup.sh` (cron diario) y `scripts/restore.sh`.

## Modos de autenticación

| | `oidc` (por defecto) | `dev` (solo laboratorio) |
|---|---|---|
| Variables | `AUTH_MODE=oidc`, `NEXT_PUBLIC_AUTH_MODE=oidc` | `AUTH_MODE=dev`, `NEXT_PUBLIC_AUTH_MODE=dev` |
| Login | Keycloak (Authorization Code + PKCE S256) | Selector de usuario/roles/grupos en el portal |
| API | Valida JWT: firma (JWKS), `iss`, `exp`, `aud=servicedesk-api` | Confía en `X-Dev-User`, `X-Dev-Roles`, `X-Dev-Groups` |

La API valida contra `OIDC_ISSUER` (URL pública: lab `http://localhost:8081/realms/energuate`, prod
`https://APP_DOMAIN/auth/realms/energuate`) y descarga las claves de `OIDC_JWKS_URL` (interna).
Claims: `sub`, `preferred_username`, `name`, `email`, `realm_access.roles`, `groups`. Los usuarios se
crean al primer acceso (JIT) y su pertenencia a equipos se sincroniza con `groups` ↔
`teams.external_group`.

> **`AUTH_MODE=dev` permite suplantar a cualquier usuario con una cabecera HTTP.** Úselo solo en un
> equipo aislado. En producción está fijado a `oidc`.

### Roles

| Rol | Permisos |
|---|---|
| `requester` | Rol por defecto: crea tickets, ve y comenta los suyos. |
| `agent` | Gestiona tickets de sus equipos/departamentos, asignados a él o creados por él. |
| `approver` | Decide aprobaciones dirigidas a él (y ve esos tickets). |
| `admin` | Acceso total: catálogos, equipos, SLA, flujos y todos los tickets. |

Roles por grupo con `KC_GROUP_ROLE_MAP` (por defecto
`GG-SD-Admins:admin,GG-SD-Agentes:agent,GG-SD-Aprobadores:approver`); equipos por grupo con
`SEED_TEAM_GROUPS` (`GG-SD-Telecomunicaciones`, `GG-SD-SeguridadRedes`, `GG-SD-SOC`, `GG-SD-Riesgo`,
`GG-SD-ProyectosTI`, `GG-SD-General`).

## Variables de entorno

Listas completas y comentadas: [`.env.example`](.env.example) (laboratorio) y
[`.env.production.example`](.env.production.example) (producción). Grupos: entorno
(`ENVIRONMENT`), bases de datos (`DB_PASSWORD`, `KC_DB_PASSWORD`), Redis (`REDIS_PASSWORD`, prod),
Keycloak (`KC_*`), autenticación (`AUTH_MODE`, `OIDC_*`), API (`LOG_LEVEL`, `UVICORN_WORKERS`,
`FORWARDED_ALLOW_IPS`, `CELERY_*`), frontend (`NEXT_PUBLIC_*`, se aplican en el build), AD
(`LDAP_*`), seeds (`SEED_DEMO`, `SEED_TEAM_GROUPS`), SLA (`TZ_BUSINESS`), adjuntos (`MINIO_ROOT_*`,
`S3_*`, `MAX_ATTACHMENT_MB`), correo (`SMTP_*`), proxy (`APP_DOMAIN`, `TLS_MODE`, `ADMIN_ALLOWED_CIDRS`)
y copias (`BACKUP_*`).

## Desarrollo y pruebas

```bash
# Backend (desde backend/)
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest

# Frontend (desde frontend/)
npm ci && npx tsc --noEmit
```

CI (GitHub Actions, `.github/workflows/ci.yml`): pruebas del backend y migraciones Alembic,
`tsc` + `next build`, validación de ambos compose y del Caddyfile, garantías de producción
(solo Caddy publica puertos, valores fijados), ShellCheck y build de imágenes. Sin secretos.

## Estructura

```
backend/            API FastAPI, Alembic, Celery, pruebas
frontend/           Portal Next.js
keycloak/           realm-energuate.json, configure-ldap.sh, truststore/ (CA corporativa, no versionada)
deploy/             Caddyfile, certs/ (certificados TLS, no versionados)
scripts/            backup.sh, restore.sh, healthcheck.sh, get-test-token.sh
docs/               Documentación
```
