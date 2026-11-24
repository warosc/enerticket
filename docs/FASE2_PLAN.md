# Fase 2 — Plan y contrato técnico

Documento de coordinación. Todo agente/desarrollador que trabaje en la Fase 2 debe respetar este contrato.
Si algo del contrato es imposible, documentar la desviación en la sección "Desviaciones" al final (no cambiar silenciosamente).

## Alcance Fase 2
1. **Base + seguridad**: Alembic, modelo de datos de producción, login OIDC en frontend, realm Keycloak importado, federación con el **AD real de Energuate** (configurable por `.env`, sin credenciales en el repo), roles y permisos por área.
2. **SLA mixto**: calendario America/Guatemala, horario hábil, feriados, pausas, métricas por eventos.
3. **Kanban + tiempo real**: tablero con arrastrar/soltar, WebSockets, detalle de ticket con comentarios e historial.
4. **Flujos y extras**: aprobaciones, automatizaciones, notificaciones, adjuntos (MinIO/S3), reportes avanzados.

## Reglas generales
- Código (identificadores) en inglés; UI, mensajes al usuario y docs en español.
- No commitear secretos. Todo secreto vía `.env` (documentado en `.env.example` con valores `replace-...`).
- Puertos publicados solo en `127.0.0.1`.
- Los agentes NO hacen `git commit`; el coordinador commitea.
- Cada archivo tiene un **dueño** (ver tabla). No editar archivos de otro dueño; si se necesita un cambio, reportarlo en la salida final como "cambio solicitado".
- Dependencias: solo el dueño de `requirements.txt` / `package.json` las modifica. Las de la Fase 2 ya se declaran en la fundación (lista abajo).

## Backend — estructura
```
backend/
  requirements.txt, requirements-dev.txt (pytest, etc.)
  alembic.ini, alembic/env.py, alembic/versions/
  app/
    main.py               # crea FastAPI, CORS, incluye routers por autodescubrimiento
    core/config.py        # Settings (pydantic-settings) desde env
    core/db.py            # engine, SessionLocal, Base, get_db
    core/security.py      # Principal, get_principal, require_roles(...)
    core/permissions.py   # visible_tickets_filter(principal), can_view_ticket, can_edit_ticket
    core/events.py        # emit(), subscribe(), guard(), run_guards(), publicación Redis
    models/__init__.py    # importa automáticamente todos los módulos de app/models (pkgutil)
    models/core.py        # tablas core (fundación)
    routers/*.py          # cada módulo expone `router = APIRouter(...)`; main.py los incluye todos (pkgutil)
    subscribers/*.py      # handlers de eventos; importados automáticamente al arrancar
    seeds/*.py            # cada módulo expone run(session); se ejecutan en orden alfabético, idempotentes
    tasks/__init__.py     # celery_app; autodescubre app/tasks/*.py; cada módulo puede añadir beat_schedule
    services/             # lógica de negocio por módulo (services/sla/, etc.)
  tests/                  # pytest; conftest con SQLite en memoria + create_all (tipos portables: JSON, no JSONB)
```
Tipos de columna portables (Postgres en runtime, SQLite en tests): usar `sqlalchemy.JSON`, `DateTime(timezone=True)`, `String`, no tipos exclusivos de Postgres.

### Migraciones Alembic (cadena fija, revision ids predeterminados)
| revision | down_revision | dueño | contenido |
|---|---|---|---|
| `0001_core` | None | backend-core | tablas core + renombrar tablas legacy de la Fase 1 (`tickets`, `comments`, `audit_events` → `legacy_*`) si existen |
| `0002_sla` | `0001_core` | sla | sla_policies, business_calendars, calendar_holidays, sla_instances, sla_events |
| `0003_flows` | `0002_sla` | flows | approval_requests, approval_steps, approval_decisions, automation_rules, automation_executions, notifications |
| `0004_attachments` | `0003_flows` | attachments-reports | ticket_attachments |

Cada migración solo crea/borra sus propias tablas. El arranque del contenedor `api` ejecuta `alembic upgrade head` y luego los seeds.

### Modelo core (`0001_core`, models/core.py)
- `users`: id, keycloak_sub (único), username, display_name, email, active, created_at, last_login_at
- `departments`: id, name (único), parent_id
- `teams`: id, department_id, name, external_group (nombre del grupo AD/Keycloak que mapea al equipo, nullable)
- `team_members`: team_id, user_id, role (`member`|`lead`) — PK compuesta
- `projects`: id, code, name, owner_id, team_id
- `ticket_types`: id, key, name  (seed: incident, service_request, change, problem)
- `priorities`: id, key, name, rank  (seed: low=1, medium=2, high=3, critical=4)
- `workflow_definitions`: id, key, name, is_default
- `workflow_states`: id, workflow_id, key, name, category (`todo`|`in_progress`|`waiting`|`done`), order, is_initial, is_final
- `workflow_transitions`: id, workflow_id, from_state_id, to_state_id, name, requires_approval (bool, default false)
- `tickets`: id, public_key (`ESD-<id>`), title, description, project_id, type_id, priority_id, status_id, department_id, team_id, requester_id, assignee_id, created_at, updated_at, first_response_at, resolved_at, closed_at
- `ticket_comments`: id, ticket_id, author_id, body, visibility (`public`|`internal`), created_at
- `ticket_history`: id, ticket_id, actor_id, event_type, before_json, after_json, timestamp

Seeds de fundación: departamentos (Telecomunicaciones, Seguridad de Redes, Ciberseguridad / SOC, Riesgo y Cumplimiento, Proyectos TI, General), un equipo por departamento (con `external_group` configurable), tipos, prioridades, workflow por defecto con estados `new, triage, assigned, in_progress, pending, resolved, closed` y transiciones razonables (incluye reabrir resolved→in_progress). `SEED_DEMO=true` crea tickets de ejemplo.

### Identidad, roles y permisos por área
- Roles de realm Keycloak: `admin`, `agent`, `approver`, `requester` (todo usuario autenticado es al menos requester).
- `AUTH_MODE=oidc` (por defecto) | `dev` (solo laboratorio; headers `X-Dev-User`, `X-Dev-Roles` csv, `X-Dev-Groups` csv).
- JWT validado contra `OIDC_ISSUER` (URL pública, p.ej. `http://localhost:8081/realms/energuate`) pero JWKS descargado de `OIDC_JWKS_URL` (URL interna `http://keycloak:8080/...`). Audiencia `OIDC_AUDIENCE=servicedesk-api`. Caché de JWKS.
- Claims usados: `sub`, `preferred_username`, `name`, `email`, `realm_access.roles`, `groups` (mapper de grupos sin ruta completa).
- Provisión JIT: en cada request autenticada se hace upsert del `users` por `keycloak_sub`; se sincronizan `team_members` según `groups` ↔ `teams.external_group`.
- `Principal`: `user_id, sub, username, display_name, email, roles:set[str], team_ids:set[int], department_ids:set[int]`, helpers `is_admin`, `is_agent`.
- Visibilidad de tickets (`core/permissions.py`): admin → todos; agent → tickets de sus equipos o departamentos, asignados a él o creados por él; approver → los suyos + aquellos con aprobación dirigida a él (el módulo flows añade esto vía `register_visibility_extension`); requester → solo los propios. Comentarios `internal` solo visibles para agent/admin.
- Mutaciones: agent puede editar/transicionar tickets visibles de su área; admin todo; requester solo comenta y puede cerrar/reabrir los suyos si la transición lo permite.

### Federación AD (real)
- `keycloak/realm-energuate.json` importado con `start-dev --import-realm`: clientes `servicedesk-web` (público, PKCE S256, redirects `http://localhost:3000/*`) y `servicedesk-api` (audiencia; mapper de audiencia en el cliente web), roles, mapper `groups`.
- `keycloak/configure-ldap.sh` ejecutado por un contenedor one-shot `keycloak-config` (imagen keycloak, `kcadm.sh`): si `LDAP_URL` está definido crea/actualiza el user federation provider (vendor `ad`, `READ_ONLY`, username `sAMAccountName`, RDN `cn`, UUID `objectGUID`, `LDAP_USERS_DN`, `LDAP_BIND_DN`, `LDAP_BIND_CREDENTIAL`, `LDAP_USER_FILTER` opcional, `LDAP_START_TLS`, `LDAP_CONNECTION_TIMEOUT`), un `group-ldap-mapper` sobre `LDAP_GROUPS_DN` (READ_ONLY), y asigna roles a grupos según `KC_GROUP_ROLE_MAP="GG-SD-Admins:admin,GG-SD-Agentes:agent,GG-SD-Aprobadores:approver"`. Idempotente. Si `LDAP_URL` está vacío no hace nada. Opcional `KC_LAB_USERS=true` crea usuarios locales de laboratorio con `KC_LAB_PASSWORD`.
- Para AD con certificado corporativo (LDAPS), documentar cómo montar el CA en el truststore de Keycloak (`KC_TRUSTSTORE_PATHS`).

### Bus de eventos (`core/events.py`) — contrato compartido
```python
emit(session, event_type: str, ticket, actor_id: int | None, data: dict) -> None
#  1) inserta ticket_history (before/after en data si aplica)
#  2) llama a handlers síncronos registrados con @subscribe(event_type) (misma transacción)
#  3) encola publicación a Redis canal "servicedesk.events" que se envía DESPUÉS del commit
#     (usar event listener after_commit de la sesión). Payload JSON:
#     {"type", "ticket_id", "ticket_key", "actor_id", "data", "ts", "department_id", "team_id", "requester_id", "assignee_id"}
@subscribe("ticket.created")              # decorador
@guard("ticket.transition")               # guard: fn(session, ticket, actor: Principal, data) -> None, lanza HTTPException(409/403) para bloquear
run_guards(session, name, ticket, actor, data)
```
Eventos emitidos por la fundación: `ticket.created`, `ticket.updated` (data: changes), `ticket.assigned`, `ticket.transitioned` (data: from_state, to_state, from_category, to_category), `ticket.commented` (data: comment_id, visibility, author_is_agent). Otros módulos pueden emitir los suyos (`sla.warning`, `sla.breached`, `approval.requested`, `approval.decided`, `attachment.added`, `notification.created`).
`first_response_at` lo fija la fundación con el primer comentario público de un agente distinto del solicitante, o la primera transición fuera de `new` por un agente.

## API (prefijo raíz, JSON) — fundación
- `GET /health`
- `GET /me` → `{id, sub, username, display_name, email, roles, teams:[{id,name,department_id}], department_ids}`
- `GET /catalog` → `{departments, teams, priorities, ticket_types, projects, workflow:{states:[{id,key,name,category,order,is_initial,is_final}], transitions:[{id,from_state_id,to_state_id,from_key,to_key,name,requires_approval}]}}`
- `GET /users?q=&role=` (agent+)
- `GET /tickets?status=&priority=&department_id=&team_id=&assignee_id=&mine=&q=&page=1&page_size=25` → `{items:[TicketOut], total, page, page_size}`
- `POST /tickets` `{title, description, type_key="incident", priority_key="medium", department_id, project_id?}` → TicketOut (201)
- `GET /tickets/{id}` → TicketOut
- `PATCH /tickets/{id}` `{title?, description?, priority_key?, assignee_id?, team_id?, department_id?}` (agent+)
- `POST /tickets/{id}/transition` `{to_state_key, comment?}` → TicketOut (valida transición + run_guards)
- `GET|POST /tickets/{id}/comments` `{body, visibility}`
- `GET /tickets/{id}/history`
- Admin (`admin`): `GET/POST/PATCH /admin/departments`, `/admin/teams`, `/admin/teams/{id}/members`, `/admin/projects`

`TicketOut = {id, key, title, description, status:{id,key,name,category}, priority:{id,key,name,rank}, type:{id,key,name}, department:{id,name}, team:{id,name}|null, project:{id,code,name}|null, requester:{id,display_name}, assignee:{id,display_name}|null, created_at, updated_at, first_response_at, resolved_at, closed_at}`
Los módulos pueden añadir campos opcionales mediante `register_ticket_enricher(fn(session, tickets)->dict[ticket_id, dict])` en `core/events.py` o un módulo `core/enrich.py` (p.ej. SLA añade `sla: {...}`). La fundación lo implementa.

## Infra (docker-compose.yml)
Servicios: `db`, `redis`, `keycloak-db`, `keycloak` (`start-dev --import-realm`, `KC_HOSTNAME=http://localhost:8081`), `keycloak-config` (one-shot), `minio` + `minio-init` (bucket `servicedesk-attachments`), `api` (alembic upgrade head + seeds + uvicorn), `worker` (celery worker), `beat` (celery beat), `web`. Healthchecks donde aplique. Todos los puertos en `127.0.0.1`.

Variables nuevas en `.env.example`: `AUTH_MODE`, `OIDC_ISSUER`, `OIDC_JWKS_URL`, `OIDC_AUDIENCE`, `NEXT_PUBLIC_AUTH_MODE`, `NEXT_PUBLIC_KEYCLOAK_URL`, `NEXT_PUBLIC_KEYCLOAK_REALM`, `NEXT_PUBLIC_KEYCLOAK_CLIENT_ID`, `LDAP_*`, `KC_GROUP_ROLE_MAP`, `KC_LAB_USERS`, `KC_LAB_PASSWORD`, `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `MAX_ATTACHMENT_MB`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`, `SEED_DEMO`, `TZ_BUSINESS=America/Guatemala`.

### Dependencias declaradas en la fundación
- Backend: fastapi, uvicorn[standard], sqlalchemy, psycopg[binary], pydantic, pydantic-settings, alembic, celery[redis], redis, PyJWT[crypto], httpx, python-multipart, boto3, tzdata, holidays. Dev: pytest, httpx.
- Frontend: next 15, react 19, recharts, keycloak-js, @dnd-kit/core, @dnd-kit/sortable, @dnd-kit/utilities.

## Frontend — estructura
```
frontend/
  app/layout.tsx              # AuthProvider + AppShell (fundación)
  app/globals.css             # tokens de diseño y clases utilitarias (fundación)
  app/page.tsx                # dashboard inicial (fundación)
  app/tickets/page.tsx        # lista con filtros + crear (fundación)
  app/tickets/[id]/page.tsx   # detalle: datos, transiciones, comentarios, historial + SLOTS (fundación)
  app/admin/page.tsx          # departamentos, equipos, miembros (fundación)
  app/kanban/page.tsx         # realtime
  app/sla/page.tsx            # sla
  app/approvals/page.tsx      # flows
  app/automations/page.tsx    # flows
  app/reports/page.tsx        # attachments-reports
  lib/api.ts                  # apiFetch(path, init) con Bearer/headers dev, manejo de errores (fundación)
  lib/auth.tsx                # AuthProvider, useAuth(): {ready, authenticated, user(/me), roles, token(), login, logout, hasRole} (fundación)
  lib/types.ts                # tipos TS de la API core (fundación)
  lib/realtime.ts             # useTicketEvents(handler, filter?) — STUB no-op en fundación; lo implementa realtime
  components/AppShell.tsx     # navegación con todos los enlaces (visibles por rol) + slot NotificationBell (fundación)
  components/NotificationBell.tsx        # STUB (devuelve null) → flows
  components/ticket/SlaPanel.tsx         # STUB → sla        (props: {ticket: Ticket})
  components/ticket/SlaBadge.tsx         # STUB → sla        (props: {ticket: Ticket}) usado en listas/kanban
  components/ticket/ApprovalsPanel.tsx   # STUB → flows      (props: {ticket: Ticket, onChange: () => void})
  components/ticket/AttachmentsPanel.tsx # STUB → attachments-reports (props: {ticket: Ticket})
```
Cada módulo puede crear archivos nuevos bajo `components/<modulo>/` y `lib/<modulo>.ts`.
Login OIDC con `keycloak-js` (PKCE, `check-sso` + `login-required` en rutas protegidas, refresh de token). En `NEXT_PUBLIC_AUTH_MODE=dev` se usa un selector de usuario/roles de laboratorio que envía los headers dev.

### WebSocket (realtime)
`GET /ws?token=<jwt>` (en dev: `?dev_user=&dev_roles=&dev_groups=`). El servidor se suscribe a Redis `servicedesk.events` y reenvía a cada cliente solo eventos de tickets que puede ver (mismas reglas que `core/permissions.py`, usando los campos de alcance del payload). Mensajes servidor→cliente: el payload del evento tal cual. Ping cada 25 s.

## Dueños de archivos
| Dueño | Archivos |
|---|---|
| backend-core | `backend/**` excepto lo asignado a módulos; incluye `requirements*.txt`, `Dockerfile`, `alembic/env.py`, `0001_core`, `core/*`, `models/core.py`, `routers/{health,me,catalog,users,tickets,admin}.py`, `seeds/0*_core*.py`, `tasks/__init__.py`, `tests/conftest.py`, `tests/test_core*.py` |
| frontend-shell | `frontend/**` excepto páginas/archivos de módulos; incluye `package.json`, `Dockerfile`, stubs |
| identity-infra | `docker-compose.yml`, `.env.example`, `keycloak/**`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/AD_INTEGRATION.md` |
| sla | `backend/app/models/sla.py`, `alembic/versions/0002_sla.py`, `routers/sla.py`, `services/sla/**`, `subscribers/sla.py`, `tasks/sla.py`, `seeds/1*_sla*.py`, `tests/test_sla*.py`, `frontend/app/sla/**`, `components/ticket/Sla*.tsx`, `components/sla/**`, `lib/sla.ts` |
| realtime | `backend/app/routers/ws.py`, `services/realtime/**`, `tests/test_ws*.py`, `frontend/app/kanban/**`, `components/kanban/**`, `lib/realtime.ts` |
| flows | `models/{approvals,automation}.py`, `0003_flows.py`, `routers/{approvals,automations,notifications}.py`, `services/{approvals,automation,notifications}/**`, `subscribers/{approvals,automation,notifications}.py`, `tasks/notifications.py`, `seeds/2*_flows*.py`, `tests/test_{approvals,automation,notifications}*.py`, `frontend/app/{approvals,automations}/**`, `components/{approvals,automations,notifications}/**`, `components/NotificationBell.tsx`, `components/ticket/ApprovalsPanel.tsx`, `lib/flows.ts` |
| attachments-reports | `models/attachments.py`, `0004_attachments.py`, `routers/{attachments,reports}.py`, `services/{storage,reports}/**`, `tests/test_{attachments,reports}*.py`, `frontend/app/reports/**`, `components/reports/**`, `components/ticket/AttachmentsPanel.tsx`, `lib/reports.ts` |

## Verificación por módulo
- Backend: `backend/.venv` (creado por la fundación). `cd backend && .venv/Scripts/python -m pytest tests/<mis tests>`.
- Frontend: `cd frontend && npx tsc --noEmit`. No ejecutar `next build` ni `docker compose` en paralelo con otros agentes (lo hace la integración).

## Desviaciones
(registrar aquí cualquier cambio al contrato)
- [backend-core] `users.roles` (JSON, lista de roles de realm sincronizada en cada login) añadida al modelo core: necesaria para `GET /users?role=` y para validar que un asignado sea agente.
- [backend-core] Nueva variable `SEED_TEAM_GROUPS` (csv `Departamento:GrupoAD`, valores por defecto `GG-SD-Telecomunicaciones`, `GG-SD-SeguridadRedes`, `GG-SD-SOC`, `GG-SD-Riesgo`, `GG-SD-ProyectosTI`, `GG-SD-General`) para el `external_group` de los equipos sembrados. Otras nuevas opcionales: `EVENTS_RAISE_ERRORS` (pruebas), `LOG_LEVEL`, `UVICORN_WORKERS`, `FORWARDED_ALLOW_IPS`, `S3_REGION`, `CELERY_BEAT_SCHEDULE_FILE` (por defecto `/tmp/celerybeat-schedule`, la imagen corre sin root). Pendiente de documentar en `.env.example` (identity-infra).
- [backend-core] Seed demo en `seeds/90_core_demo.py` (no `0*_core*`) para que corra después de los seeds de módulos (SLA/flows) y sus suscriptores reaccionen a los tickets demo.
- [backend-core] Endpoints adicionales: `GET /stats` (conteos visibles por estado/categoría/prioridad) y `DELETE /admin/teams/{id}/members/{user_id}`. Filtros extra en `GET /tickets`: `category`, `requester_id`, `unassigned`, `sort`.
- [backend-core] Sincronización de equipos por grupos: solo afecta a equipos con `external_group`; si el token no trae el claim `groups` (o en dev no se envía `X-Dev-Groups`) no se sincroniza. En dev, si falta `X-Dev-User` la API responde 401 (ya no hay admin implícito de la Fase 1).
- [backend-core] Los handlers de `@subscribe` corren dentro de un SAVEPOINT: si fallan se registra el error y se descarta su trabajo sin abortar la operación (con `EVENTS_RAISE_ERRORS=true` se propaga). La publicación a Redis tras el commit se hace en un hilo de fondo para no bloquear la petición.
