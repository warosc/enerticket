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
| `0005_seed_state` | `0004_attachments` | correcciones-finales | seed_state (marcas de seeds de una sola vez) |

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
- [frontend-shell] CSP con nonce por petición en `frontend/middleware.ts`; el layout raíz fuerza renderizado dinámico (`dynamic = 'force-dynamic'`) para que Next.js aplique el nonce. `public/silent-check-sso.html` carga `silent-check-sso.js` (sin script en línea) y tiene CSP propia en `next.config.mjs`.
- [frontend-shell] Nuevas variables de build opcionales del portal: `NEXT_PUBLIC_WS_URL` (URL del WebSocket; si falta se deriva de `NEXT_PUBLIC_API_URL`) y `NEXT_PUBLIC_CSP_EXTRA_SOURCES` (orígenes extra para la CSP, separados por espacio). Ambas declaradas como `ARG` en `frontend/Dockerfile`.
- [frontend-shell] Health check del contenedor web en `GET /healthz` (`{"status":"ok"}`); el HEALTHCHECK del Dockerfile usa `node` + `fetch` contra esa ruta.
- [frontend-shell] `ApiError` expone `code` (de `detail.code`) y `detailObject`; el detalle del ticket trata el 409 `approval_required` (muestra el mensaje, la solicitud `approval_request_id` y enlace a /approvals). Ante un 401 en OIDC `apiFetch` fuerza un refresco del token y reintenta una vez; si la sesión terminó vuelve al login.
- [frontend-shell] `lib/ticketRules.ts` replica `can_edit_ticket`/`can_requester_transition` solo para decidir qué acciones mostrar (la API sigue siendo la autoridad).
- [attachments-reports] Las descargas de adjuntos NO usan URLs presignadas: `GET /attachments/{id}/download` hace de proxy en streaming tras verificar permisos (el bucket nunca se expone al navegador), por lo que `S3_PUBLIC_ENDPOINT` no se usa para adjuntos. El bucket se crea si falta (perezosamente, una vez por proceso).
- [attachments-reports] Endpoints adicionales: `GET /attachments/settings` (`{max_mb, max_bytes, max_per_ticket, allowed_extensions}`) y borrado en `DELETE /attachments/{id}` (lógico, 204). Además de `attachment.added` (data: `attachment_id, filename, size, mime`) se emite `attachment.deleted` (data: `attachment_id, filename`). `AttachmentOut` = `{id, ticket_id, filename, size, mime, checksum, uploaded_by:{id,display_name}, created_at, can_delete}`.
- [attachments-reports] Reglas de adjuntos: listar/descargar = ver el ticket; subir = solicitante, asignado, agente del área (`can_edit_ticket`) o admin (un aprobador que solo ve por extensión no sube); borrar = autor o `can_edit_ticket`. No se adjunta a tickets en estado final (409) y hay un máximo de 50 adjuntos activos por ticket (409). Lista blanca por extensión con firma mágica (pdf, office/odf, doc/xls/ppt/msg, eml, txt/log/csv/json, png/jpg/gif/webp/bmp, mp4, zip/7z, pcap/pcapng); sin SVG/HTML/JS/ejecutables. El MIME guardado y servido es el canónico de la extensión, no el declarado.
- [attachments-reports] `ticket_attachments` usa un índice compuesto `(ticket_id, deleted_at)` en lugar de uno simple sobre `ticket_id`, más índice en `uploaded_by` y CHECK `size >= 0`.
- [attachments-reports] `/reports/*` exige rol `agent` (admin pasa); solicitantes y aprobadores reciben 403. `from`/`to` son fechas en `TZ_BUSINESS` (ambas incluidas, por defecto últimos 30 días, máximo 731 días). Backlog y aging son instantáneas actuales (no usan el rango). `/reports/trend` acepta `granularity=day|week|auto`. `/reports/sla` lee `app.models.sla.SlaInstance` de forma tolerante (`available:false` si no existe) y el módulo SLA puede sustituir el cálculo con `app.services.reports.register_sla_report_provider(fn)`. `/reports/export.csv` exporta como máximo 50 000 filas, UTF-8 con BOM y celdas protegidas contra inyección de fórmulas.
- [attachments-reports] Frontend: estilos en CSS Modules `components/reports/reports.module.css` y `components/reports/attachments.module.css` (este último lo usa `components/ticket/AttachmentsPanel.tsx`); `lib/reports.ts` también exporta `saveBlob`/`downloadAuthenticated`, que usa el panel de adjuntos para descargar con fetch autenticado + blob.
- [sla] Modelo ampliado (todo en `0002_sla`): `business_calendars` = `name, description, timezone, windows (JSON [{weekday 0=lunes..6, start "HH:MM", end "HH:MM"|"24:00"}]), created_at, updated_at` (varias ventanas por día permitidas, sin solaparse). `calendar_holidays` = `calendar_id, date, name, recurring` (único por calendario y fecha; recurrente = mismo día/mes cada año; solo días completos: los medios días del 24 y 31 de diciembre no se modelan). `sla_policies` agrega `description, precedence` (menor = primero; gana la primera activa que cumpla), condiciones `priority_keys / type_keys / department_ids` (listas JSON, vacía = cualquiera), `pause_categories` (todo/in_progress/waiting) y `pause_states` (claves; `done` no pausa: detiene). `sla_instances` agrega `calendar_id, target_minutes, warning_threshold_pct` (instantánea de la política), `warning_at` (umbral de advertencia, indexado para la tarea), `created_at, updated_at`.
- [sla] Semántica: como máximo una instancia vigente (no cancelada) por métrica y ticket. Las pausas aplican a ambas métricas. `first_response` se cumple con `ticket.first_response_at`; si el ticket llega a `done` sin respuesta se cancela (o queda vencida si ya lo estaba). `resolution` se detiene al entrar en `done` (usa `resolved_at`) y al reabrir se reanuda descontando el tiempo resuelto. Un cambio de prioridad, tipo o departamento (`ticket.updated`) re-evalúa la política: si cambia política/objetivo/calendario se cancela la instancia abierta y se crea otra con el mismo inicio y las mismas pausas (recalculadas en el nuevo calendario); las métricas ya cumplidas se conservan. Editar ventanas o feriados recalcula automáticamente los vencimientos abiertos del calendario; editar una política solo afecta a tickets nuevos hasta ejecutar `POST /sla/recalculate`.
- [sla] Eventos `sla.warning` / `sla.breached` (actor_id null): data = `metric, metric_label, instance_id, policy_id, policy_name, policy, status, target_at, due_at, breached_at, warned_at, detected (scheduler|on_stop|recalculation)` y en advertencias `remaining_business_seconds, warning_threshold_pct`. El vencimiento se detecta en la tarea beat `sla.check_deadlines` (cada 60 s, `FOR UPDATE SKIP LOCKED`, idempotente) o al detener el reloj después del objetivo; si advertencia y vencimiento coinciden solo se emite el vencimiento. `breached_at` = instante del objetivo vencido.
- [sla] `TicketOut.sla` = `null` (sin política) o `{policy:{id,name}, first_response, resolution}`; cada métrica = `{instance_id, metric, label, status, policy_id, target_minutes, warning_threshold_pct, started_at, target_at (null mientras está pausada), paused, paused_at, paused_business_seconds, stopped_at, remaining_business_seconds (negativo = excedido), elapsed_business_seconds, breached, breached_at, warned, warned_at, at_risk, open}` o `null`.
- [sla] Endpoints: lectura (agent+) `GET /sla/calendars[/{id}]`, `GET /sla/calendars/{id}/holidays?year=`, `GET /sla/calendars/{id}/preview?minutes=&start=`, `GET /sla/policies[/{id}]`, `GET /sla/at-risk?department_id=&mine=&limit=`; admin `POST/PATCH/DELETE /sla/calendars`, `POST /sla/calendars/{id}/holidays`, `POST /sla/calendars/{id}/holidays/import {years, country="GT"}`, `PATCH/DELETE /sla/holidays/{id}`, `POST/PATCH/DELETE /sla/policies` (borrar una política ya aplicada o un calendario en uso → 409: desactivar), `POST /sla/recalculate`; cualquier autenticado `GET /tickets/{id}/sla` (404 si no puede ver el ticket) y `GET /sla/summary?date_from=&date_to=&department_id=&policy_id=` (tickets visibles creados en el rango, fechas en `TZ_BUSINESS`, máx. 731 días; `compliance_pct` = cumplidas / (cumplidas + vencidas)).
- [sla] Seed `seeds/10_sla.py`: calendarios «Horario hábil Guatemala» (L-V 08:00-17:00, zona `TZ_BUSINESS`) y «24x7»; feriados oficiales de Guatemala (librería `holidays`, con respaldo fijo + Semana Santa calculada) del año anterior a 3 años adelante, solo para años sin feriados (respeta borrados del administrador; los años nuevos se agregan en cada arranque); políticas «Crítica 24x7» (15 min / 4 h), «Alta - horario hábil» (30 min / 8 h), «Media - horario hábil» (2 h / 24 h) y «Baja - horario hábil» (4 h / 40 h), pausa en categoría `waiting`, aviso al 75 %. No se crean si ya existen por nombre.
- [sla] Frontend: `SlaBadge` muestra «Sin SLA» si el ticket no tiene política; estilos en CSS Module `components/sla/sla.module.css`. `SlaPanel` consulta `GET /tickets/{id}/sla` cada 60 s y al recibir `sla.warning`/`sla.breached` del ticket (vía `useTicketEvents`). `/sla` usa `?tab=risk|compliance|policies|calendars`; agentes ven políticas y calendarios en solo lectura.
- [identity-infra] Producción en `docker-compose.prod.yml` (archivo completo, no overlay) + `.env.production.example`; en el servidor se copia a `.env`. Caddy (`deploy/Caddyfile`) es el único servicio con puertos (80/443): `/` → web, `/api/*` → api (sin prefijo), `/ws` → api, `/auth/*` → Keycloak. Keycloak bajo `/auth` en el mismo dominio (`KC_HTTP_RELATIVE_PATH=/auth`, `KC_HOSTNAME=https://APP_DOMAIN/auth`, modo `start`), por lo que en producción `OIDC_ISSUER=https://APP_DOMAIN/auth/realms/energuate` y `OIDC_JWKS_URL=http://keycloak:8080/auth/realms/energuate/protocol/openid-connect/certs`. MinIO no se publica (la API hace de proxy de adjuntos).
- [identity-infra] Valores fijados en producción (no configurables por `.env`): `ENVIRONMENT=production`, `AUTH_MODE=oidc`, `SEED_DEMO=false`, `KC_LAB_USERS=false`, `LDAP_ALLOW_INSECURE=false`, `NEXT_PUBLIC_AUTH_MODE=oidc`, `API_ROOT_PATH=/api`, `FORWARDED_ALLOW_IPS=<IP fija de Caddy>` (red `edge` con subred fija `EDGE_SUBNET`, por defecto `10.250.250.0/24`, Caddy en `10.250.250.10`). Redis con contraseña (`REDIS_PASSWORD`, `REDIS_URL=redis://:<pass>@redis:6379/0`).
- [identity-infra] Variables nuevas: `ENVIRONMENT` (development|production), `KC_WEB_URLS` (csv de URLs del portal; `keycloak-config` fuerza con ellas redirect URIs/web origins/post-logout del cliente `servicedesk-web` y desactiva direct grants en cada arranque, así cambiar de dominio no exige reimportar el realm), `CELERY_LOG_LEVEL`, `CELERY_CONCURRENCY`; solo prod: `APP_DOMAIN`, `TLS_MODE` (acme|files|internal), `ACME_EMAIL`, `TLS_CERTS_DIR`, `PUBLIC_BIND_IP`, `ADMIN_ALLOWED_CIDRS`, `MAX_REQUEST_BODY`, `EDGE_SUBNET`, `CADDY_EDGE_IP`, `REDIS_PASSWORD`, `KC_REALM`, `KC_LOG_FORMAT`, `KC_TRUSTSTORE_DIR`, `*_IMAGE`, `*_CPUS`/`*_MEMORY`, `LOG_MAX_SIZE`/`LOG_MAX_FILE`, `BACKUP_DIR`, `BACKUP_RETENTION_DAYS`.
- [identity-infra] Usuarios de laboratorio (`KC_LAB_USERS=true`) renombrados para coincidir con los usuarios demo del backend: `demo.admin` (admin, agent), `ana.telecom`, `luis.soc`, `rosa.redes` (agent + grupo de su equipo), `marta.aprobadora` (approver), `carlos.solicitante`, `sofia.solicitante`. Los nombres anteriores (`admin.lab`, `agente.*`, `aprobador.lab`, `usuario.lab`) se deshabilitan siempre. `keycloak-config` además crea los grupos de `SEED_TEAM_GROUPS`. Ojo: con `AUTH_MODE=oidc` los usuarios demo del seed (`keycloak_sub="dev:<usuario>"`) y los de Keycloak son filas distintas (mismo username).
- [identity-infra] Realm: política de contraseñas con `passwordHistory(5)` y rotación de refresh tokens (`revokeRefreshToken=true`, sin reutilización). Keycloak omite el claim `groups` cuando el usuario no pertenece a ningún grupo.
- [identity-infra] Herramientas: `scripts/get-test-token.sh` (token real vía Authorization Code + PKCE, sin direct grants), `scripts/backup.sh`, `scripts/restore.sh`, `scripts/healthcheck.sh` (+ `scripts/lib.sh`); CI en `.github/workflows/ci.yml`. Healthcheck del contenedor web contra `/healthz`. Nuevos docs: `DEPLOYMENT.md`, `OPERATIONS.md`, `SECURITY.md`.
- [realtime] `/ws`: además de los eventos del bus, el servidor envía mensajes de control `{"type":"ready","user_id","expires_at","ping_interval","subscribed"}` (primer mensaje, tras suscribirse a Redis o a los 3 s), `{"type":"ping","ts"}` cada 25 s, `{"type":"pong"}` si el cliente envía `ping` (texto o `{"type":"ping"}`) y `{"type":"resync"}` cuando el listener de Redis se recupera de una caída (el cliente debe recargar). Códigos de cierre: 4000 token/sesión expirada (reconectar con token nuevo), 4401 no autenticado, 4403 prohibido (usuario desactivado), 1008 Origin no permitido (antes del handshake, HTTP 403), 1013 reintentar (cliente lento, identidad no disponible), 1011 error interno, 1001 servidor reiniciándose. Los errores de autenticación se comunican aceptando y cerrando con código porque el navegador no expone el estado HTTP de un handshake rechazado.
- [realtime] Filtrado adicional al contrato: (a) cuando un usuario deja de ver un ticket por un cambio de alcance (`data.before` con `department_id/team_id/assignee_id` que sí veía) recibe un aviso redactado `{"type":"ticket.hidden","ticket_id","ticket_key","data":{"reason":"scope_changed","source_type"},...}` con los campos de alcance en null y sin datos nuevos; (b) los eventos `notification.*` con `data.recipient_id` o `data.user_id` solo se entregan a ese usuario (ni siquiera a admin). Las extensiones de visibilidad se evalúan con BD en lotes (un hilo y una sesión por vuelta del event loop, `CapacityLimiter` propio de 4 hilos para no competir con los endpoints síncronos), con caché por conexión (positivos 30 s, negativos 5 s; los eventos `approval.*` ignoran la caché).
- [realtime] Variables opcionales nuevas (con valores por defecto, no requieren `.env`): `WS_PING_INTERVAL_SECONDS` (25), `WS_QUEUE_SIZE` (500 mensajes pendientes por conexión; si se supera se cierra con 1013), `WS_MAX_CONNECTION_SECONDS` (43200; tope de vida de una conexión, también en dev), `WS_SEND_TIMEOUT_SECONDS` (10), `WS_VISIBILITY_CACHE_SECONDS` (30), `WS_REDIS_BACKOFF_MAX_SECONDS` (30). El origen del WebSocket se valida contra `CORS_ORIGINS` (sin Origin se permite: clientes no navegador; el token se exige igual).
- [realtime] Endpoint adicional `GET /realtime/stats` (admin): estado del hub del proceso (`connections, subscribed, dispatched, failures, last_error`). El router instala un filtro de logging que redacta `token=` en los logs de uvicorn (el handshake se registra con la query). El listener Redis se inicia con la primera conexión del proceso y se detiene con la última; el apagado se hace con el `lifespan` del router (no `on_shutdown`, obsoleto).
- [realtime] Frontend: `lib/realtime.ts` añade los tipos `ticket.hidden` y `realtime.resync` a `TicketEventType` y exporta `useRealtimeStatus()`, `REALTIME_STATUS_LABELS`, `resetRealtime()` y `RESYNC_EVENT`. Tras una reconexión (o un `resync` del servidor) cada suscriptor recibe un evento sintético `realtime.resync` (con `ticket_id` = su filtro `ticketId` o 0) salvo que haya filtrado por `types` sin incluirlo; así las páginas existentes recargan solas. La conexión se rota antes de que expire el token (sin hueco de eventos). Usa `NEXT_PUBLIC_WS_URL` si existe; si no, deriva `ws(s)://` de `NEXT_PUBLIC_API_URL`. Indicador reutilizable en `components/kanban/RealtimeIndicator.tsx`; estilos del tablero en CSS Module `components/kanban/kanban.module.css`.
- [flows] Tabla adicional `approval_policies` en `0003_flows` (configuración de aprobaciones: `name, active, transition_id, ticket_type_keys, department_ids, steps (JSON), order`). Regla: una transición exige aprobación solo si `requires_approval=true`; si tiene políticas activas se usa la primera (por `order`) cuyo filtro de tipo/departamento coincide con el ticket y, si ninguna coincide, NO se exige aprobación (así se distingue por tipo de ticket, ya que el workflow core no lo hace); sin políticas se usa la etapa por defecto «Aprobación del departamento» (rol `approver` del departamento del ticket; si no hay, cualquier `approver`). Crear/activar una política marca su transición como `requires_approval`.
- [flows] `approval_steps` guarda `approver_type` (`user`|`role`|`team`|`team_lead`), `approver_user_id`, `approver_role`, `approver_team_id`, `approver_department_id`, `rule` (`all`|`any`) y `status` (`waiting`|`pending`|`approved`|`rejected`|`cancelled`); `approval_decisions` se crean al abrir la solicitud, una por aprobador elegible con `decision=pending` (luego `approved`|`rejected`|`skipped`) y `is_override` (decisión de un admin no designado). Los aprobadores se fijan al crear la solicitud; se excluye a quien pidió la transición (segregación de funciones) y a usuarios sin rol approver/agent/admin; si no queda nadie se designa a los administradores. `approval_requests` añade `policy_id, reason (comentario de la transición), executed_at, execution_error`.
- [flows] Guard `ticket.transition`: responde 409 `{"detail": {"code": "approval_required", "message", "approval_request_id", "created"}}`. Para que la solicitud persista pese al 409, el guard hace `session.commit()` cuando no está dentro de un SAVEPOINT (petición HTTP normal); dentro de un SAVEPOINT o con `extra_guard_data["approval_inline"]=True` solo hace flush. Una aprobación concedida y no ejecutada (p.ej. la ejecución automática falló por permisos) permite aplicar la transición a mano una vez, mientras el ticket no haya cambiado de estado desde la aprobación. Las solicitudes pendientes se cancelan solas si el ticket sale del estado de origen. El rechazo exige comentario (422).
- [flows] Eventos propios: `approval.requested` (data: `approval_request_id, transition_id, transition_name, from_state, to_state, step_id, step_order, step_name, approver_ids, requested_by_id, reason, created`; se emite también al activarse cada etapa siguiente), `approval.decided` (data: `approval_request_id, step_id, step_name, decision, comment, override, request_status, transition_id, transition_name, requested_by_id`; uno por decisión, `request_status` indica si es final) y `approval.cancelled` (data: `approval_request_id, transition_id, reason`). `notification.created` NO pasa por `emit` (no escribe `ticket_history`): se encola directamente para publicarse tras el commit con `ticket_id` = id del ticket o 0, `requester_id` = destinatario y `data = {notification_id, user_id, kind, title, ticket_id, ticket_key}`.
- [flows] `TicketOut.approvals = {pending: <n>}` (enriquecedor). Endpoints adicionales: `GET /approvals?scope=mine|all&status=&page=&page_size=` (historial; `all` solo admin), `GET /approvals/{id}`, `POST /approvals/{id}/cancel {reason?}` (quien la pidió, agente del área o admin), `GET /approvals/pending?all=true` (admin: todas), admin `GET/POST /approvals/policies`, `PATCH/DELETE /approvals/policies/{id}`, `GET /approvals/transitions`, `PUT /approvals/transitions/{id} {requires_approval}`. `POST /approvals/{id}/decide` acepta `decision` = `approve|reject` (o `approved|rejected`). Automatizaciones (admin): `GET /automations/meta`, `GET/POST /automations/rules`, `GET/PATCH/DELETE /automations/rules/{id}`, `POST /automations/rules/{id}/dry-run {ticket_id, event_type?, event_data?}`, `POST /automations/dry-run {ticket_id, rule, event_data?}` (regla sin guardar), `GET /automations/executions?rule_id=&ticket_id=&status=&page=`. `GET /notifications` devuelve `{items, total, unread, page, page_size}`.
- [flows] Formato de reglas: `conditions = {"match": "all"|"any", "rules": [{"field", "op", "value"}]}` con campos `ticket.*` (priority, priority_rank, status, status_category, type, department_id, team_id, assignee_id, requester_id, title, description, text) y `event.<ruta>` sobre la data del evento; operadores `eq, neq, in, not_in, contains, not_contains, starts_with, gt, gte, lt, lte, is_empty, is_not_empty` (texto sin distinguir mayúsculas). `actions = [{"type": assign_user|assign_team|set_priority|transition|add_comment|notify|request_approval, ...}]`; plantillas `{{ticket.key}}`, `{{ticket.title}}`, `{{event.x}}`. Las acciones se ejecutan como el usuario técnico «Automatización» (`keycloak_sub="system:automation"`, sin roles en BD; lo crea el seed o la primera ejecución) cada una en su SAVEPOINT; la acción `transition` crea la solicitud de aprobación en lugar de fallar si la transición la requiere. Anti-bucles: profundidad máxima 1 (los eventos generados por acciones de reglas disparadas por eventos de automatización ya no se evalúan) y una regla no se repite sobre el mismo ticket en la misma cadena. `automation_executions` solo registra reglas que coincidieron (`status` success|partial|error, `result` por acción, `depth`).
- [flows] El modelo `Notification` vive en `models/automation.py` (no hay `models/notifications.py` en la tabla de dueños). Columnas: `user_id, kind, title, body, ticket_id, data (JSON), channel (in_app|email), is_read, read_at, delivery_status (delivered|pending|sent|failed|skipped), attempts, last_error, sent_at, created_at`. Las filas `email` solo se crean si `SMTP_HOST` está definido y el usuario tiene correo; se encolan tras el commit (`notifications.send_email`, hasta 5 intentos con backoff 60 s·2^n, tope 1 h) y la tarea beat `notifications.retry_pending` (cada 5 min) reencola las que nunca llegaron al broker. SMTP: puerto 465 = TLS implícito; si no, `SMTP_STARTTLS` (verificación TLS siempre activa).
- [flows] Variables nuevas opcionales: `APP_PUBLIC_URL` (URL pública del portal para los enlaces de los correos; por defecto el primer origen de `CORS_ORIGINS`) y `SEED_FLOWS_EXAMPLES` (por defecto `true`; `false` evita crear las políticas y reglas de ejemplo). Seed `seeds/20_flows.py`: marca `new|triage|assigned → in_progress` como `requires_approval` con la política «Cambios» (solo tipo `change`: etapa 1 líder del equipo, etapa 2 rol `approver` global) y crea las reglas «Crítico: avisar a los agentes del departamento», «Escalado a crítico…» y «SOC: posible phishing» (prioridad alta + equipo SOC + comentario interno); solo si sus tablas están vacías.
- [flows] Frontend: estilos en CSS Modules `components/approvals/approvals.module.css`, `components/automations/automations.module.css` y `components/notifications/notifications.module.css`; iconos propios en `components/notifications/FlowIcon.tsx`. `lib/flows.ts` amplía `TicketOut` con `approvals?: {pending}` y exporta `approvalRequiredDetail(error)`.
- [integración] `ENVIRONMENT=production` valida la configuración al construir `Settings` (API, worker, beat, alembic y seeds fallan al arrancar): exige `AUTH_MODE=oidc`, `OIDC_ISSUER` https, `SEED_DEMO=false`, `EVENTS_RAISE_ERRORS=false` y `CORS_ORIGINS` solo con orígenes https explícitos (sin `*`). Nuevas variables: `API_DOCS` (vacío = /docs, /redoc y /openapi.json activos salvo en producción), `API_ROOT_PATH` (lo aplica FastAPI como `root_path`, no uvicorn: Caddy quita `/api` y el enrutado funciona con o sin prefijo), `OIDC_MISSING_GROUPS_AS_EMPTY` (por defecto `true`) y `ATTACHMENT_PURGE_DAYS` (30).
- [integración] Cambia la desviación previa de backend-core sobre `groups`: en OIDC un token sin claim `groups` (Keycloak lo omite si el usuario no tiene grupos) se trata como "sin grupos" y retira las membresías de equipos mapeados con `external_group`; `OIDC_MISSING_GROUPS_AS_EMPTY=false` recupera el comportamiento anterior. En modo dev sigue igual (sin `X-Dev-Groups` no se sincroniza).
- [integración] Laboratorio: con `AUTH_MODE=oidc` el primer login de un usuario cuyo `preferred_username` coincide con un usuario demo (`keycloak_sub="dev:<usuario>"`) reasigna ese `keycloak_sub` al `sub` real, de modo que los usuarios lab de Keycloak heredan sus tickets y equipos demo. Nunca ocurre con `ENVIRONMENT=production`.
- [integración] CORS expone `Content-Disposition` y `X-Checksum-SHA256` (nombre del adjunto descargado). Los CHECK de `0001_core`, `0002_sla` y `0003_flows` usan `op.f(...)` para que Postgres no duplique el prefijo (`ck_tabla_ck_tabla_...`); los nombres resultantes coinciden con los modelos. Bases creadas antes de este cambio conservan los nombres duplicados (solo cosmético).
- [integración] Tarea `attachments.purge_deleted` (`app/tasks/attachments.py`, beat cada 6 h): borra de S3 el contenido de los adjuntos borrados lógicamente hace más de `ATTACHMENT_PURGE_DAYS` días y luego su fila (el historial conserva `attachment.deleted`); si S3 falla la fila se conserva y se reintenta.
- [integración] `tests/test_core_events.py` compara solo los eventos `ticket.*` (flows publica además `notification.created`). Frontend: `AppShell` muestra `RealtimeIndicator` en la barra superior (oculto en estado `idle`) y llama a `resetRealtime()` al cerrar sesión; `TicketHistory` describe `approval.*`, `attachment.*` y `sla.*`. `.gitignore` ignora `.env.*` salvo las plantillas. CI verifica que Alembic tenga una sola cabeza.

- [correcciones-finales] Comentarios internos dependen del ticket: `can_view_internal_comments(principal, ticket=None)` = admin, o agente con `can_edit_ticket` (sin ticket solo admin); nuevas `can_edit_scope(...)` y `can_view_internal_scope(...)` (sin BD, para eventos). Se aplica en `list_comments`, `list_history`, `add_comment(internal)` (403) y en el filtro del WebSocket. El portal solo ofrece «interno» a quien puede editar el ticket.
- [correcciones-finales] Aprobaciones: (a) la extensión de visibilidad solo cuenta decisiones de la etapa ACTIVA (`step.status=pending`, `decision=pending`, solicitud pendiente) o decisiones tomadas (`approved|rejected`); `waiting`/`skipped`/`cancelled` no dan acceso, y `GET /approvals?scope=mine` aplica la misma regla y además filtra por tickets visibles. Las filas de decisión se siguen creando al abrir la solicitud (instantánea para la UI) pero no dan acceso hasta que su etapa se activa. (b) Una etapa `role` con `scope=department` sin aprobadores en el departamento recurre a los administradores (aviso en el log), ya no a todos los aprobadores de la organización. (c) Segregación de funciones también para admin: `decide` responde 403 y `can_decide=false` si `principal.user_id == requested_by_id`. (d) `cancel_request` bloquea la fila del ticket (como `decide`) y recarga la solicitud antes de validar.
- [correcciones-finales] `Principal.is_system` (sub `system:*`). La primera respuesta (SLA) solo la marca un agente humano distinto del solicitante (`counts_as_first_response`), nunca las automatizaciones (comentario o transición).
- [correcciones-finales] SLA: `reevaluate_ticket` no reemplaza instancias `breached` (el incumplimiento es inmutable y no se vuelve a notificar), no recrea `first_response` si se canceló por `done_without_response` y conserva `warned_at` si el nuevo umbral ya pasó. `current_instances(..., for_update=True)` (FOR UPDATE + populate_existing) en `sync_ticket`/`reevaluate_ticket`; la tarea de vencimientos selecciona candidatas sin bloqueo y por cada una bloquea ticket (SKIP LOCKED) e instancia en ese orden y recarga su estado. `refresh_calendar_targets` recalcula `paused_business_seconds` con el calendario nuevo. `POST /sla/recalculate` procesa cada ticket en su propia transacción con la fila bloqueada.
- [correcciones-finales] Nueva migración `0005_seed_state` (down `0004_attachments`, tabla `seed_state(key, applied_at, data)`, modelo `app/models/seed_state.py`, ayudas en `app/core/seed_state.py`). Departamentos/equipos iniciales (`core.departments.v1`), calendarios y políticas SLA (`sla.defaults.v1`, feriados por id de calendario) y ejemplos de flujos (`flows.examples.v1`) se siembran UNA sola vez; una base con datos previos se «adopta» sin crear nada. `SEED_FLOWS_EXAMPLES` por defecto `false` en producción.
- [correcciones-finales] Seguridad de tokens: `ThrottledJWKClient` (refresco por `kid` desconocido como mucho cada `OIDC_JWKS_MIN_REFRESH_SECONDS`, 60 s; backoff de 5 s tras fallo de conexión; descargas serializadas) y validación previa de `alg`/`kid`. `ENVIRONMENT=production` rechaza valores `replace-...`/triviales en `DATABASE_URL`, `REDIS_URL`, `S3_*`, `SMTP_*`, `OIDC_ISSUER` y `CORS_ORIGINS`.
- [correcciones-finales] Límites anti-abuso (variables nuevas con valores por defecto): `REQUESTER_MAX_PRIORITY` (`high`; un no agente que pide una prioridad mayor recibe 422), `TICKETS_PER_USER_HOUR` (30; 429 con `Retry-After`), `ATTACHMENT_USER_DAILY_MB` (500) y `ATTACHMENT_USER_DAILY_COUNT` (100) por usuario en 24 h incluidos borrados (429; admin exento), `ACCOUNT_STALE_DAYS` (45; cuentas sin login reciente no reciben notificaciones, no son aprobadoras ni asignables: 422), `WS_MAX_CONNECTIONS_PER_USER` (8; cierre 1013), `WS_CLIENT_MESSAGES_PER_MINUTE` (60; cierre 1008) y `WS_MAX_MESSAGE_BYTES` (16384, `uvicorn --ws-max-size`). `create_ticket(..., enforce_limits=True)`: los seeds demo pasan `False`. Las pruebas desactivan los dos límites de tickets por entorno (tienen pruebas propias).
- [correcciones-finales] Producción: servicio one-shot `preflight` (`deploy/preflight.sh`, sin red, solo lectura) del que dependen todos los servicios; `cap_drop: [ALL]` (Caddy con `NET_BIND_SERVICE`; Postgres/Redis conservan las capacidades por defecto porque su entrypoint cambia de usuario) y `read_only` + `tmpfs` en api/worker/beat/web. `PREFLIGHT_MIN_SECRET_LENGTH` (16). Keycloak: `offline_access` fuera de los roles por defecto y del cliente del portal, sesiones offline de 1 día/7 días (realm y `keycloak-config`).
- [correcciones-finales] Dependencias: `fastapi==0.120.4` y `starlette==0.49.3` fijada. Constantes `HTTP_422_UNPROCESSABLE_CONTENT` / `HTTP_413_CONTENT_TOO_LARGE`. CI: `pip-audit`, job `backend-postgres` (migraciones sobre una base con las tablas de la Fase 1, seeds dos veces, ida y vuelta, y la suite completa con `TEST_DATABASE_URL`), prueba de `preflight` y comprobaciones de endurecimiento del compose de producción.
- [correcciones-finales] Portal: `getAccessToken` sigue usando el token vigente si Keycloak no responde y `handleUnauthorized` solo vuelve al login si la sesión terminó (refresh token vencido o rechazado con 400/401); el formulario de alta oculta prioridades superiores a «Alta» a quien no es agente.
- [ux-ronda2] Portal: nuevas utilidades en `globals.css` (`.kpi-grid` con `--kpi-cols`, `.table-stack` + `data-label`/`cell-head`/`cell-actions`/`cell-hide-sm` para tablas apiladas en móvil < 640px, `.row-actions`, `.ticket-layout`/`.ticket-slot-*` para reordenar el detalle en una columna) y componente `CollapsibleFilters` en `components/ui` (filtros plegables en móvil). `CountBarChart` acepta `layout="horizontal"`; «Por departamento» usa barras horizontales. Las transiciones a estados finales (`is_final`) piden confirmación en el detalle. El indicador «En vivo» solo aparece en la barra superior. `SlaPanel` rellena la barra con el tiempo hábil consumido (`elapsed_business_seconds`) y la muestra llena al cumplirse o vencer.
- [ux-ronda2] Keycloak: tema de login `energuate` (`keycloak/themes/energuate/login`, padre `keycloak.v2`, solo CSS y textos) montado de solo lectura en `/opt/keycloak/themes/energuate` (lab y prod); `loginTheme` en `realm-energuate.json` y `keycloak-config` lo asigna de forma idempotente si el tema está instalado (variable opcional `KC_LOGIN_THEME`, por defecto `energuate`; vacía = no tocar).

## Notas de contrato backend-core (firmas exactas)

FIRMAS (todas importables tal cual):
- app.core.db: `Base` (DeclarativeBase con naming convention), `engine`, `SessionLocal` (expire_on_commit=False), `get_db() -> Iterator[Session]`, `utcnow() -> datetime` (UTC con zona), `TZDateTime` (usar `mapped_column(TZDateTime())` para fechas: siempre devuelve UTC con zona, también en SQLite), `make_engine(url)`.
- app.core.security:
  - `@dataclass Principal(user_id:int, sub:str, username:str, display_name:str, email:str|None, roles:set[str], team_ids:set[int], department_ids:set[int], groups:set[str]=set(), lead_team_ids:set[int]=set())`.
  - Propiedades `is_admin`, `is_agent` (True con agent O admin), `is_approver` (approver O admin) y el método `has_role(*roles)` (admin cumple cualquier rol).
  - `get_principal(request: Request, session=Depends(get_db)) -> Principal`.
  - `require_roles(*roles: str) -> dependencia que devuelve Principal` (admin siempre pasa).
  - Para WebSocket u otros usos sin Depends: `principal_from_token(session, token) -> Principal` y `principal_from_dev(session, username, roles: csv|iterable|None, groups: csv|iterable|None) -> Principal`. Ambas lanzan `AuthError` (atributos `.status_code`, `.detail`; método `.to_http()`).
  - También: `build_principal(session, user, roles, groups=None)`, `decode_access_token(token)` y las constantes ROLE_ADMIN, ROLE_AGENT, ROLE_APPROVER, ROLE_REQUESTER.
- app.core.permissions:
  - `visible_tickets_filter(principal) -> ColumnElement[bool]` (cláusula WHERE sobre Ticket).
  - `register_visibility_extension(fn)`, con `fn(principal) -> ColumnElement[bool] | None`; se combina con OR, sirve como decorador y la expresión puede usar subconsultas, p.ej. `Ticket.id.in_(select(ApprovalStep.ticket_id).where(...))`.
  - `can_view_ticket(session, principal, ticket) -> bool`, `can_edit_ticket(principal, ticket) -> bool`, `can_requester_transition(principal, ticket, from_state, to_state)`, `can_view_internal_comments(principal)`.
  - Para realtime: `can_view_scope(principal, *, department_id, team_id, requester_id, assignee_id) -> bool` (sin BD) y `can_view_event(principal, payload, session=None) -> bool` (con session también consulta las extensiones).
- app.core.events:
  - `emit(session, event_type: str, ticket, actor_id: int|None, data: dict) -> None`. `data["before"]`/`data["after"]` van a before_json/after_json; si no hay "after", el resto de data va a after_json.
  - `@subscribe(*event_types)`, también `"*"`. Handler canónico `fn(session, event: Event)`, donde Event tiene .type, .ticket, .actor_id, .data, .ts, .history_id y .payload. También se acepta `fn(session, ticket, actor_id, data)`.
  - Los handlers corren en la misma transacción dentro de un SAVEPOINT; si fallan se registra el error y se descarta su trabajo. Con EVENTS_RAISE_ERRORS=true, que está activo en las pruebas, la excepción se propaga.
  - `@guard(name)` con `fn(session, ticket, actor: Principal, data) -> None`; lanza HTTPException para bloquear.
  - `run_guards(session, name, ticket, actor, data)`.
  - `register_ticket_enricher(fn)`, con `fn(session, tickets) -> dict[ticket_id, dict]` (también disponible en app.core.enrich). Los campos se agregan a TicketOut y un enriquecedor que falla se omite.
  - Utilidades: `set_publisher(fn(channel, message_json)|None)`, `publish_payload(payload)`, `get_redis()`, `flush_publications()`, `to_jsonable()`, `unsubscribe`, `remove_guard`.
  - Canal: settings.events_channel = "servicedesk.events". Payload exacto del plan: type, ticket_id, ticket_key, actor_id, data, ts, department_id, team_id, requester_id, assignee_id.
- Datos que recibe el guard "ticket.transition": transition_id, transition_name, requires_approval, from_state, to_state, from_category, to_category, comment, más extra_guard_data.
- Eventos de la fundación (con su data):
  - ticket.created: {after}
  - ticket.updated: {before, after, changes: {campo: {from, to}}}
  - ticket.assigned: {from_assignee_id, to_assignee_id, before, after}
  - ticket.transitioned: {from_state, to_state, from_category, to_category, transition_id, before, after}
  - ticket.commented: {comment_id, visibility, author_is_agent}
- app.services.tickets (no hace commit):
  - `create_ticket(session, principal, *, title, description, type_key, priority_key, department_id, project_id=None)`
  - `update_ticket(session, principal, ticket, changes: dict)`
  - `transition_ticket(session, principal, ticket, to_state_key, comment=None, *, extra_guard_data=None)`
  - `add_comment(session, principal, ticket, body, visibility)`
  - `get_visible_ticket(session, principal, ticket_id, for_update=False)` (404 si no lo puede ver)
  - `serialize_tickets(session, tickets)`, `ticket_out(session, ticket_id)`, `TICKET_LOAD_OPTIONS`
- Modelos en app.models.core: User (incluye `roles` JSON), Department, Team, TeamMember, Project, TicketType, Priority, WorkflowDefinition, WorkflowState, WorkflowTransition, Ticket (propiedad `.key`), TicketComment, TicketHistory. Un modelo nuevo en app/models/<x>.py queda registrado solo.
- Celery: `from app.tasks import celery_app`; definir `@celery_app.task(name="modulo.tarea")` y, opcionalmente, a nivel de módulo `beat_schedule = {"clave-unica": {"task": "modulo.tarea", "schedule": 60.0}}`. Se fusiona automáticamente y una clave duplicada es un error. Modelos y suscriptores se cargan en worker_init/beat_init. Worker: `celery -A app.tasks.celery_app worker`; beat: `celery -A app.tasks.celery_app beat` (el archivo de schedule va a /tmp).
- Seeds: app/seeds/NN_nombre.py con `run(session)` (sin commit; el runner confirma cada módulo). Orden alfabético; la demo core es 90_core_demo.
- Routers: app/routers/<x>.py con `router = APIRouter(...)`; se incluyen solos.
- Modo dev: headers X-Dev-User (obligatorio), X-Dev-Roles csv, X-Dev-Groups csv (si no se envía, no se sincronizan equipos), y opcionales X-Dev-Name y X-Dev-Email. El sub queda como "dev:<usuario>".
- Grupos por defecto de los equipos: GG-SD-Telecomunicaciones, GG-SD-SeguridadRedes, GG-SD-SOC, GG-SD-Riesgo, GG-SD-ProyectosTI y GG-SD-General (configurables con SEED_TEAM_GROUPS).
- API extra para el frontend:
  - GET /stats → {total, by_status, by_category, by_priority}
  - GET /tickets admite además category, requester_id, unassigned y sort (created_at|-created_at|updated_at|-updated_at|priority|-priority); page_size hasta 200.
  - DELETE /admin/teams/{id}/members/{user_id}
- FIXTURES de tests (tests/conftest.py, con DB SQLite en memoria recreada en cada test más el seed core):
  - `client` (TestClient), `db` (Session; hacer commit de las escrituras antes de llamar a la API), `ids` ({"departments": {nombre: id}, "teams": {nombre_departamento: team_id}}).
  - `make_user(username, roles=(...), departments=(...), team_role="member", groups=None) -> Persona(id, username, roles, headers)`.
  - Personas listas: `admin`, `agent` (Telecom, "ana.telecom"), `other_agent` (SOC, "luis.soc"), `requester`, `other_requester`, `approver`.
  - `create_ticket(persona, department="Telecomunicaciones", **payload) -> TicketOut dict` y `transition(persona, ticket_id, to_state, comment=None) -> Response`.
  - `published_events` (lista de payloads publicados tras el commit) e `isolate_registries` (autouse: restaura suscriptores, guards, extensiones y enriquecedores).
  - Helper `auth_headers(username, roles, groups=None)` y constantes DEPARTMENT_TELECOM, DEPARTMENT_SOC y DEPARTMENT_NETWORK, importables con `from tests.conftest import ...`.

### Cambios solicitados por backend-core a otros dueños
identity-infra (.env.example / docker-compose.yml): documentar las variables nuevas con valores no secretos: SEED_TEAM_GROUPS, LOG_LEVEL, UVICORN_WORKERS, FORWARDED_ALLOW_IPS, S3_REGION y CELERY_BEAT_SCHEDULE_FILE. EVENTS_RAISE_ERRORS es solo para pruebas.
identity-infra (docker-compose.yml): el servicio api usa el CMD por defecto de la imagen ('api'), que ya ejecuta alembic upgrade head, luego seeds y luego uvicorn, así que no hace falta definir command. worker: `command: celery -A app.tasks.celery_app worker --loglevel=info`; beat: `command: celery -A app.tasks.celery_app beat --loglevel=info`. Los tres servicios necesitan DATABASE_URL, REDIS_URL, AUTH_MODE, OIDC_ISSUER, OIDC_JWKS_URL (p.ej. http://keycloak:8080/realms/energuate/protocol/openid-connect/certs), OIDC_AUDIENCE, TZ_BUSINESS, SEED_DEMO y las variables S3_* y SMTP_*. Healthcheck sugerido para api: `python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"`.
identity-infra (keycloak): para que los equipos se asignen solos, crear o mapear los grupos AD/Keycloak con los nombres de SEED_TEAM_GROUPS (por defecto GG-SD-Telecomunicaciones, GG-SD-SeguridadRedes, GG-SD-SOC, GG-SD-Riesgo, GG-SD-ProyectosTI, GG-SD-General). El mapper 'groups' debe ir sin ruta completa y estar incluido en el access token.
frontend-shell: en AUTH_MODE=dev el backend exige X-Dev-User y responde 401 si falta. X-Dev-Name es opcional (nombre visible). Los usuarios demo (SEED_DEMO=true) son demo.admin, ana.telecom, luis.soc, rosa.redes, marta.aprobadora, carlos.solicitante y sofia.solicitante.