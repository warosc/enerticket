# Fase 3 — Catálogo de servicio y generador de reportes (contrato técnico)

Documento de coordinación. Todo agente que trabaje en la Fase 3 respeta este contrato. Si algo es imposible,
documentar la desviación en "Desviaciones" al final (no cambiar silenciosamente). Las reglas generales de
`docs/FASE2_PLAN.md` siguen vigentes (código en inglés, UI/docs en español, sin secretos, los agentes NO
hacen `git commit`, cada archivo tiene un dueño, tipos portables SQLite/Postgres).

## Objetivo
1. El ticket se clasifica con **departamento de atención → categoría → subcategoría**, con **tipo**
   Incidencia / Petición / Orden de trabajo, proveedor, canal, solución y tiempo hábil de resolución.
2. El usuario trae su **departamento desde el AD** (atributo `department`) y cada departamento pertenece a una
   **gerencia**. Usuarios **VIP** por grupo AD.
3. Los **SLA** se definen por departamento + categoría/subcategoría (+ VIP).
4. Un **generador de reportes** genérico (filas × columnas × métricas, secciones, plantillas guardadas,
   exportación .xlsx, envío programado). El informe mensual de proveedor (tipo UFINET) es una plantilla de fábrica.
5. Cada rol genera reportes **solo dentro de lo que puede ver** (`visible_tickets_filter`).

El sistema NO se basa en los Excel de ejemplo (`*.xlsx` en la raíz, ignorados por git, con datos personales:
no leerlos ni copiarlos a código, tests ni docs). Los catálogos los carga el administrador.

## Dueños de archivos

| Agente | Archivos |
|---|---|
| **catalog-core** (fase A) | `backend/app/models/core.py`, `backend/app/models/sla.py`, nuevo `backend/app/models/catalog.py`, `alembic/versions/0006_catalog.py`, `backend/app/core/{security,accounts,permissions,config}.py`, nuevo `backend/app/core/catalog.py`, `backend/app/services/tickets.py`, `backend/app/services/sla/*`, `backend/app/schemas/core.py`, `backend/app/routers/{catalog,admin,tickets,users,me,sla}.py`, `backend/app/seeds/00_core.py`, `backend/app/seeds/10_sla.py`, `backend/tests/conftest.py`, tests nuevos `backend/tests/test_catalog*.py`, y los tests existentes que deban ajustarse por las reglas nuevas |
| **reports-backend** (fase B) | `backend/app/services/reports/*`, `backend/app/routers/reports.py`, nuevo `backend/app/models/reports.py`, `alembic/versions/0007_report_templates.py`, nuevo `backend/app/tasks/reports.py`, nuevo `backend/app/seeds/40_report_templates.py`, `backend/requirements.txt` (agrega `openpyxl`), `backend/tests/test_reports*.py` |
| **frontend-catalog** (fase B) | `frontend/lib/types.ts`, nuevo `frontend/lib/catalog.ts`, `frontend/lib/ticketRules.ts`, `frontend/app/admin/**`, `frontend/components/admin/**`, `frontend/app/tickets/**`, `frontend/components/tickets/**`, `frontend/components/sla/PoliciesSection.tsx`, `frontend/app/page.tsx` y `frontend/components/dashboard/**` (solo si hace falta mostrar categoría) |
| **frontend-reports** (fase B) | `frontend/app/reports/**`, `frontend/components/reports/**`, `frontend/lib/reports.ts` |
| **identity-docs** (fase B) | `keycloak/**`, `docs/**` (excepto este archivo: solo añadir en "Desviaciones"), `README.md`, `.env.example`, `.env.production.example`, `docker-compose*.yml`, nuevo `backend/app/seeds/95_catalog_demo.py` |
| **integration / review-fix** (fases C–E) | cualquiera, para corregir integración y hallazgos |

Los tipos de reportes del frontend viven en `frontend/lib/reports.ts` (no en `types.ts`).

## Migraciones
| revision | down_revision | dueño |
|---|---|---|
| `0006_catalog` | `0005_seed_state` | catalog-core |
| `0007_report_templates` | `0006_catalog` | reports-backend |

Cada una con `upgrade` y `downgrade` completos.

## Modelo (`0006_catalog`)

Nuevas tablas (`models/catalog.py`):
- `gerencias`: `id`, `name` (único, 150), `external_group` (nullable, grupo AD cuyos miembros pueden ver
  los tickets **solicitados** por departamentos de esta gerencia), `active` (bool, true).
- `department_aliases`: `id`, `normalized_value` (único, 200), `raw_value` (200), `department_id`
  (FK departments, **nullable** = pendiente de asignar), `created_at`.
- `ticket_categories`: `id`, `department_id` (FK, index), `name` (150), `type_keys` (JSON list; vacía = todos los
  tipos), `active`, `sort_order` (int). Único (`department_id`, `name`).
- `ticket_subcategories`: `id`, `category_id` (FK, ondelete cascade, index), `name`, `active`, `sort_order`.
  Único (`category_id`, `name`).
- `providers`: `id`, `name` (único), `department_ids` (JSON list; vacía = todos), `active`.
- `gerencia_viewers`: (`gerencia_id`, `user_id`) PK compuesta — asignación manual del admin, además del grupo AD.

Columnas nuevas:
- `departments`: `gerencia_id` (FK nullable), `serves_tickets` (bool, default false; la migración pone
  **true** a los departamentos existentes), `active` (bool, true).
- `users`: `department_id` (FK departments nullable, index), `vip` (bool, false).
- `ticket_types`: `active` (bool, true).
- `tickets`: `category_id`, `subcategory_id`, `provider_id` (FK nullable, index), `channel` (String 20, default
  `'portal'`, check in `portal|email|phone|other`), `vip` (bool, false), `resolution_note` (Text nullable),
  `resolved_by_id` (FK users nullable), `requester_department_id` (FK departments nullable, index),
  `parent_ticket_id` (FK tickets nullable, index), `business_resolution_minutes` (int nullable).
- `sla_policies`: `category_ids` (JSON list), `subcategory_ids` (JSON list), `vip_only` (bool, false).

Seeds (`seeds/00_core.py`, patrón `seed_state`):
- `core.ticket_types.v2` (una vez): `incident` → "Incidencia", `service_request` → "Petición", crea
  `work_order` → "Orden de trabajo", desactiva `change` y `problem` (no los borra).
- `core.departments.v2` (una vez): crea si no existen, con `serves_tickets=true`: Sistemas Comerciales, Sistemas
  Corporativos, Telecomunicaciones, Seguridad Informática, Soporte Técnico (cada uno con su "Equipo <nombre>").
  No borra los existentes. No crea categorías (las carga el admin; el demo las crea en `95_catalog_demo.py`).

## Identidad (catalog-core + identity-docs)
- `app/core/catalog.py`: `normalize_ad_value(raw) -> str` (trim, mayúsculas, sin tildes, quita prefijo numérico
  tipo `"07 "`/`"0903 "`, colapsa espacios) y `resolve_department_alias(session, raw) -> Department | None`
  (si no existe el alias lo crea con `department_id=NULL` y emite `catalog.alias_pending`).
- Claims del token: `department` (string, atributo AD), `groups` (ya existe). `settings.vip_group`
  (env `VIP_GROUP`, default `GG-SD-VIP`). En cada login (JIT, `core/accounts.py`) se actualizan
  `users.department_id` (si el alias resuelve) y `users.vip` (pertenencia al grupo; si el claim no trae grupos no se
  toca). Modo dev: cabecera opcional `X-Dev-Department`.
- `Principal` agrega: `home_department_id: int | None`, `home_gerencia_id: int | None`, `vip: bool`,
  `viewer_gerencia_ids: set[int]` (por `gerencias.external_group` ∈ groups o fila en `gerencia_viewers`).
- `core/permissions.py`: extensión de visibilidad: un usuario con `viewer_gerencia_ids` ve (solo lectura) los
  tickets cuyo `requester_department_id` pertenece a esas gerencias. `can_edit_ticket` no cambia.
- Keycloak (identity-docs): mapper LDAP `user-attribute-ldap-mapper` `department` → atributo de usuario
  `department` (env `LDAP_DEPARTMENT_ATTRIBUTE`, default `department`), protocol mapper `oidc-usermodel-attribute-mapper`
  al claim `department` en access token del cliente web; usuarios de laboratorio con atributo `department`.

## Reglas del ticket (catalog-core)
- Crear (`create_ticket`): acepta `category_id`, `subcategory_id`, `provider_id`, `channel`. Validaciones (422):
  la categoría debe estar activa, pertenecer al `department_id` del ticket y admitir el tipo (si `type_keys` no
  está vacía); la subcategoría debe pertenecer a la categoría; el proveedor activo y aplicable al departamento.
  `channel` y `provider_id` solo los fija un agente/admin (solicitante → `portal`, sin proveedor). Se copian
  `requester_department_id = requester.department_id` y `vip = requester.vip`. Tipos inactivos → 422.
- Actualizar (`update_ticket`): agentes con permiso de edición pueden cambiar categoría, subcategoría, proveedor,
  canal y `resolution_note` con las mismas validaciones; se registra en historial.
- Resolver (transición a estado `category == 'done'`): el payload de transición acepta `resolution_note`.
  Requiere solución (la del payload o la ya guardada) → 422 `"Indique la solución antes de resolver"`. Requiere
  `category_id` **si el departamento del ticket tiene categorías activas** → 422 `"Clasifique el ticket..."`.
  Guarda `resolved_by_id` y `business_resolution_minutes`: minutos hábiles de la instancia SLA `resolution` si
  existe (ya descuenta pausas); si no, minutos hábiles entre `created_at` y `resolved_at` con el calendario por
  defecto (`services/sla/calendar.py`). Reabrir limpia `resolved_by_id` y `business_resolution_minutes`.
  Los helpers de tests (`transition`) se actualizan para mandar `resolution_note` al resolver.
- Orden de trabajo: `POST /tickets/{id}/work-order` `{title?, description?, category_id?, subcategory_id?,
  resolve_parent: bool=false, resolution_note?}` → crea ticket `work_order` hijo (`parent_ticket_id`), mismo
  departamento/solicitante; si `resolve_parent`, resuelve el original con la nota (o
  `"Trasladado a orden de trabajo #<id>"`). Requiere `can_edit_ticket`. Devuelve `TicketOut` del hijo (201).
  Eventos `ticket.created` y `ticket.updated` como siempre.
- SLA (`services/sla/engine.py`): `policy_matches` añade `category_ids`, `subcategory_ids` y `vip_only`
  (vip_only → solo tickets con `vip`). Orden (ajustado al cierre, ver Desviaciones): más específica primero (subcategoría >
  categoría > resto), luego id. Cambiar categoría/subcategoría re-evalúa el SLA (como cambiar prioridad hoy).

## API catálogo / admin (catalog-core)
- `GET /catalog` (existente) agrega: `departments[]` con `gerencia_id`, `serves_tickets`; `ticket_types` solo
  activos; `categories: [{id, department_id, name, type_keys, subcategories: [{id, name}]}]` (activas, ordenadas);
  `providers: [{id, name, department_ids}]`; `channels: [{key, label}]`
  (`portal` Portal, `email` Correo, `phone` Teléfono, `other` Otro); `gerencias: [{id, name}]`.
- Admin (rol admin, prefijo `/admin`):
  - `GET|POST /admin/gerencias`, `PATCH /admin/gerencias/{id}` — `{name, external_group?, active?}`.
  - Departamentos existentes: `DepartmentIn/Out` agregan `gerencia_id`, `serves_tickets`, `active`.
  - `GET /admin/department-aliases?pending=true|false`, `POST /admin/department-aliases` `{value, department_id}`,
    `PATCH /admin/department-aliases/{id}` `{department_id}`, `DELETE /admin/department-aliases/{id}`.
    Asignar un alias actualiza `users.department_id` de usuarios que lo traían (guardar `users.ad_department_raw`
    no es necesario: se re-resuelve en el siguiente login; basta con el siguiente login).
  - `GET /admin/categories?department_id=`, `POST /admin/categories` `{department_id, name, type_keys, sort_order?}`,
    `PATCH /admin/categories/{id}` `{name?, type_keys?, active?, sort_order?}`;
    `POST /admin/categories/{id}/subcategories` `{name, sort_order?}`, `PATCH /admin/subcategories/{id}`.
    Respuesta de categoría: `{id, department_id, department, name, type_keys, active, sort_order,
    subcategories: [{id, name, active, sort_order}]}`.
  - `GET|POST /admin/providers`, `PATCH /admin/providers/{id}` — `{name, department_ids, active?}`.
  - `POST /admin/catalog/import?dry_run=true|false` (multipart `file`, CSV UTF-8 con encabezado
    `departamento,categoria,subcategoria,tipos,ans_horas`; `tipos` separados por `|` con claves
    `incident|service_request|work_order`; `ans_horas` opcional). Crea/reactiva lo que falte (idempotente). Si hay
    `ans_horas` crea/actualiza una `SlaPolicy` llamada `"ANS <depto> / <categoría>[ / <subcategoría>]"` con
    `department_ids`, `category_ids` o `subcategory_ids`, `resolution_minutes = horas*60`, calendario por defecto,
    precedencia 50. Respuesta `{dry_run, created: {categories, subcategories, policies}, updated: {policies},
    errors: [{line, message}]}`. Máx 2 MB / 5000 filas; celdas saneadas.
  - `PATCH /admin/users/{id}/profile` `{vip?, department_id?}` (override manual; el siguiente login con claim
    `department` lo vuelve a fijar) y `GET|PUT /admin/gerencias/{id}/viewers` `{user_ids}`.
- Tickets: `TicketCreate`/`TicketUpdate` agregan los campos anteriores; `TicketOut` agrega
  `category {id,name}|null`, `subcategory`, `provider`, `channel`, `vip`, `resolution_note`, `resolved_by`
  (user ref), `requester_department {id,name}|null`, `gerencia {id,name}|null` (del departamento del solicitante),
  `parent_ticket_id`, `child_ticket_ids: int[]`, `business_resolution_minutes`. El payload de transición agrega
  `resolution_note?`. Listado `GET /tickets` acepta filtros `category_id`, `type_key`.
- SLA: esquemas de política agregan `category_ids`, `subcategory_ids`, `vip_only`.
- `GET /me` agrega `department {id,name}|null`, `gerencia {id,name}|null`, `vip`, `viewer_gerencia_ids`.

## Generador de reportes (reports-backend)

Alcance: **todo reporte se calcula sobre `visible_tickets_filter(principal)`** (ya cubre admin, agente por
equipo/departamento, solicitante, aprobador y extensión de gerencia). Los filtros del usuario solo pueden
**reducir** ese alcance. `/reports/*` pasa de `require_roles(ROLE_AGENT)` a cualquier usuario autenticado.
Los endpoints actuales (`overview`, `trend`, …, `export.csv`) se mantienen.

- `GET /reports/builder/meta` →
  `{dimensions: [{key, label, kind: "category"|"date"}], metrics: [{key, label, unit: "count"|"hours"|"pct"}],
  date_fields: [{key, label}], scope: {level: "all"|"areas"|"own", label}}`.
- Dimensiones (lista blanca): `type`, `department` (de atención), `team`, `gerencia` (del solicitante),
  `requester_department`, `category`, `subcategory`, `provider`, `assignee`, `resolved_by`, `priority`, `vip`,
  `channel`, `status`, `day`, `week`, `month`, `year` (las de fecha usan `date_field`, zona America/Guatemala).
- Métricas: `count`, `resolution_hours_avg`, `resolution_hours_sum`, `resolution_hours_median`,
  `resolution_hours_max` (de `business_resolution_minutes`, horas hábiles), `first_response_hours_avg`,
  `sla_target_hours` (promedio del objetivo de resolución de la instancia SLA), `sla_compliance_pct`
  (resolución cumplida / evaluada), `sla_breached_count`, `open_count`.
- `ReportDefinition` (Pydantic, `extra="forbid"`):
  ```json
  {"title": "…", "date_field": "created_at|resolved_at", "period": "custom|this_month|last_month|last_30_days|this_year|last_year",
   "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD",
   "filters": {"type_keys": [], "department_ids": [], "team_ids": [], "category_ids": [], "subcategory_ids": [],
               "provider_ids": [], "gerencia_ids": [], "requester_department_ids": [], "assignee_ids": [],
               "priority_keys": [], "channels": [], "vip": null},
   "sections": [{"title": "…", "rows": ["category"], "columns": ["month"], "metrics": ["count", "resolution_hours_avg"],
                 "filters": {…mismo esquema…}, "totals": true}]}
  ```
  Límites: rango ≤ 366 días, 1–10 secciones, `rows` 1–2 dimensiones, `columns` 0–1, `metrics` 1–8; claves
  desconocidas → 422. `period` ≠ `custom` calcula las fechas (en hora de Guatemala) e ignora `date_from/to`.
- `POST /reports/builder/run` (body `ReportDefinition`) → `ReportResult`:
  ```json
  {"title": "…", "generated_at": "…", "date_from": "…", "date_to": "…", "scope": {"level": "…", "label": "…"},
   "sections": [{"title": "…", "row_dims": ["category"], "column_dim": "month", "metrics": ["count"],
     "columns": [{"key": "2026-01", "label": "Ene 2026"}],
     "rows": [{"keys": [{"key": "12", "label": "Telefonía Móvil"}], "cells": {"2026-01": {"count": 4}}, "total": {"count": 4}}],
     "totals": {"2026-01": {"count": 4}}, "grand_total": {"count": 4}}]}
  ```
  Sin `column_dim`, la única columna tiene key `"_all"`. Valores nulos donde no hay datos; horas con 2 decimales.
  Promedios/mediana/porcentaje de los totales se recalculan sobre los tickets (no promedio de promedios).
- `POST /reports/builder/export.xlsx` (body `{definition: ReportDefinition, include_detail: bool,
  detail_columns?: string[]}`) → `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, una hoja por
  sección + hoja "Detalle" (una fila por ticket; columnas por defecto: número, fecha solicitud, fecha solución,
  tipo, departamento, categoría, subcategoría, proveedor, canal, prioridad, VIP, solicitante, departamento y
  gerencia del solicitante, técnico, resuelto por, horas hábiles, objetivo, cumplió SLA, estado, título,
  solución). Todas las celdas de texto pasan por `sanitize_cell`. Máx 50 000 filas de detalle.
- Plantillas `report_templates` (`0007_report_templates`): `id`, `name`, `description`, `owner_id` (nullable),
  `is_system` (bool), `definition` (JSON), `shared_roles` (JSON list), `shared_department_ids` (JSON list),
  `schedule` (JSON: `{enabled, frequency: "monthly"|"weekly", day, hour, recipients: [emails]}`),
  `next_run_at`, `last_run_at`, `last_status`, `created_at`, `updated_at`.
  - `GET /reports/templates` → propias + compartidas conmigo (por rol o departamento) + sistema.
  - `POST /reports/templates`, `PATCH|DELETE /reports/templates/{id}` (dueño o admin; las de sistema solo se
    pueden **duplicar**: `POST /reports/templates/{id}/duplicate`).
  - `POST /reports/templates/{id}/run` → `ReportResult`; `GET /reports/templates/{id}/export.xlsx`.
  - Solo admin o líderes de equipo pueden programar envíos; destinatarios: correos con dominio en
    `REPORT_RECIPIENT_DOMAINS` (env, coma; vacío = cualquiera) y máx 20.
  - Seed de fábrica (`seeds/40_report_templates.py`, idempotente por nombre, `is_system`):
    1. **"Informe mensual de proveedor"**: periodo `last_month`, `date_field=resolved_at`, 3 secciones
       (Incidencias / Peticiones / Órdenes de trabajo vía `filters.type_keys`), filas `category`, columnas `month`,
       métricas `resolution_hours_avg, count, resolution_hours_sum, sla_target_hours, sla_compliance_pct`.
    2. **"Desempeño por técnico"**: filas `category`,`assignee`, métricas count + promedio + suma.
    3. **"Tickets por día"**: filas `day`, columnas `type`, métrica count, periodo `this_month`.
    4. **"Tickets por gerencia solicitante"**: filas `gerencia`,`requester_department`, columnas `type`.
    5. **"Cumplimiento de SLA por categoría"**: filas `department`,`category`, métricas cumplimiento/vencidos/objetivo.
- Envío programado (`tasks/reports.py`, beat cada 15 min): ejecuta plantillas con `schedule.enabled` y
  `next_run_at <= now`, con el **alcance del dueño** (Principal reconstruido desde BD), genera el .xlsx y lo envía
  por SMTP como adjunto (si `SMTP_HOST` vacío: `last_status="smtp_disabled"`, sin error). Calcula el siguiente
  `next_run_at` en hora de Guatemala.

## Frontend

### frontend-catalog
- `lib/catalog.ts`: llamadas al catálogo y admin (usa `lib/api.ts`), tipos en `lib/types.ts`.
- Administración: pestañas nuevas **Gerencias**, **Departamentos** (gerencia, "atiende tickets", activo),
  **Valores del AD** (pendientes resaltados, asignar a departamento), **Categorías** (por departamento y tipo, con
  subcategorías en línea), **Proveedores**, **Importar catálogo** (CSV con plantilla descargable, vista previa
  `dry_run`, luego aplicar), y en usuarios: VIP/departamento manual.
- Crear ticket: tipo → departamento (solo `serves_tickets`) → categoría (filtrada por departamento y tipo) →
  subcategoría; agentes además canal y proveedor.
- Detalle de ticket: panel de clasificación editable (categoría, subcategoría, proveedor, canal), solicitante con
  departamento/gerencia/VIP, ticket padre / órdenes de trabajo hijas, acción **"Trasladar a orden de trabajo"**,
  y al resolver un diálogo que exige la **solución** (y la categoría si el departamento tiene catálogo).
  Mostrar `resolution_note` y horas hábiles de resolución.
- Lista de tickets: columnas/filtros de tipo y categoría.
- SLA → Políticas: selección de categorías/subcategorías (filtradas por departamentos elegidos) y "solo VIP".

### frontend-reports
- `/reports` mantiene el tablero actual como pestaña **"Resumen"** y agrega **"Generador"** y **"Plantillas"**.
- Generador: rango/periodo, campo de fecha, filtros (multiselección desde `/catalog`), secciones (filas, columnas,
  métricas), vista previa en tabla dinámica con totales y "Acumulado", **Exportar Excel**, **Guardar como
  plantilla** (compartir por rol/departamento, programar envío si es admin/líder).
- Plantillas: lista (propias, compartidas, de fábrica), ejecutar, exportar, duplicar, editar, eliminar.
- El alcance (`scope.label` de `/reports/builder/meta`) se muestra siempre ("Viendo: tickets de Telecomunicaciones").
- Diseño coherente con el portal (CSS modules existentes), usable en móvil (tabla con scroll horizontal propio,
  sin desbordar la página).

## Verificación esperada
- Backend: `cd backend && .venv/Scripts/python -m pytest -q` todo verde (hoy 366 tests + nuevos).
- Frontend: `cd frontend && npx tsc --noEmit`; `next build` y `docker compose` solo en la fase de integración.
- Windows: para construir imágenes `BUILDX_BAKE_ENTITLEMENTS_FS=0 docker compose build ...`. Disco C: con poco
  espacio: no crear imágenes extra ni volúmenes nuevos; no borrar imágenes/volúmenes de otros proyectos.

## Desviaciones
(los agentes agregan aquí lo que no pudieron cumplir tal cual, con motivo)
- [catalog-core] Alias del AD: si al crear un alias nuevo existe exactamente un departamento cuyo nombre normalizado coincide (p.ej. AD `"07 COMERCIAL"` y departamento «Comercial»), el alias se crea ya asignado; solo queda pendiente (`department_id=NULL` + `catalog.alias_pending`) cuando no hay coincidencia. `catalog.alias_pending` no pasa por `emit` (no hay ticket): se encola para publicarse tras el commit con `ticket_id=0` y campos de alcance nulos, por lo que por WebSocket solo lo reciben administradores. `data = {alias_id, raw_value, normalized_value}`. El prefijo numérico se quita con `^\d+[\s._-]+` (`"12-Seguridad"` → `SEGURIDAD`).
- [catalog-core] La sincronización del perfil (departamento y VIP) vive en `core/accounts.py::sync_profile` y la llama la provisión JIT de `core/security.py`. `Principal` agrega los campos con valores por defecto (los constructores existentes siguen funcionando); `viewer_gerencia_ids` solo cuenta gerencias **activas**. Variable nueva `VIP_GROUP` (por defecto `GG-SD-VIP`) — documentar en `.env.example` (identity-docs). El claim `department` también se acepta como lista (atributo multivaluado): se toma el primer valor no vacío.
- [catalog-core] Visibilidad por gerencia implementada como extensión registrada en `core/permissions.py` (`gerencia_visibility`), así la usan también `can_view_ticket`, `can_view_event` y el WebSocket sin cambios. Solo lectura: no edita, no transiciona, no sube adjuntos; puede comentar en público como un aprobador (los comentarios internos siguen ocultos).
- [catalog-core] Reglas del ticket: (a) el departamento del ticket debe estar activo y con `serves_tickets` (422 al crear y al cambiar de departamento); (b) los tipos inactivos dan 422 salvo con `create_ticket(..., enforce_limits=False)` (seeds/procesos internos: la demo sigue creando `change`/`problem`); (c) a un no agente se le fija `channel="portal"` y sin proveedor en silencio (no 422); (d) una subcategoría sin categoría toma la de su categoría; (e) cambiar de departamento retira la categoría/subcategoría que no le pertenece y el proveedor que no aplica, cambiar de categoría retira la subcategoría ajena; (f) PATCH no permite dejar vacía la solución de un ticket resuelto (422).
- [catalog-core] Resolver = entrar en categoría `done` desde otra categoría. La solución se exige ahí (payload o ya guardada); `resolved → closed` no la vuelve a pedir (tickets resueltos antes de la Fase 3 pueden cerrarse). La categoría se exige solo si quien resuelve puede editar el ticket (agente/admin del área): el solicitante que cierra su propio ticket sin clasificar solo indica la solución. `business_resolution_minutes` se calcula al resolver (antes de emitir el evento, con el reloj de la instancia SLA hasta `resolved_at`, equivalente a detenerla); sin instancia usa el calendario hábil por defecto: el del seed SLA (id guardado en `seed_state` `sla.defaults.v1` o nombre «Horario hábil Guatemala») y, si no existe, el horario estándar L-V 08:00-17:00 en `TZ_BUSINESS` con feriados oficiales (`services/sla/calendar.py::standard_business_spec`). Helpers nuevos en `services/sla/engine.py`: `default_calendar`, `default_business_spec`, `business_resolution_minutes`, `policy_specificity`, `policy_sort_key`. El snapshot del historial incluye `category_id, subcategory_id, provider_id, channel, resolution_note`; `ticket.transitioned` incluye `resolution_note` en before/after cuando cambia.
- [catalog-core] Cambiar categoría/subcategoría re-evalúa el SLA llamando a `ensure_ticket_sla` desde `update_ticket` (el suscriptor `subscribers/sla.py` no es de catalog-core y solo mira prioridad/tipo/departamento). `GET /sla/policies` se ordena igual que la evaluación (precedencia, especificidad, id).
- [catalog-core] Orden de trabajo: copia del original departamento, solicitante, prioridad, proyecto y canal; título por defecto «Orden de trabajo: <título>» y descripción del original. `resolve_parent` usa la primera transición disponible hacia un estado `done` (prefiere no final: «Resuelto»), dentro de un SAVEPOINT; si falla (p.ej. falta categoría o un guard de aprobación) no se crea nada. Si el original ya está resuelto, `resolve_parent` se ignora. Sobre el original se emite `ticket.updated` con `changes = {work_order_id: {from: null, to: <id>}}`.
- [catalog-core] API: `GET /tickets` acepta además `subcategory_id`; `type_key` admite varias claves separadas por coma. `GET /catalog` devuelve todos los departamentos (con `active`) y solo los tipos activos. `UserOut` (`GET /users`, `PATCH /admin/users/{id}/profile`) agrega `department {id,name}|null` y `vip`. Respuesta de categoría: `department` es `{id, name}`. `GerenciaOut` agrega `department_ids`; `DepartmentAliasOut` = `{id, normalized_value, raw_value, department_id, department, pending, created_at}` y en `POST /admin/department-aliases` `department_id` es opcional. `GET|PUT /admin/gerencias/{id}/viewers` devuelve `[{id, username, display_name, email}]`.
- [catalog-core] Importación CSV: `dry_run` por defecto `true`; se acepta `;` como separador (Excel en español) y encabezados sin distinguir mayúsculas/tildes (`Categoría`, `ANS horas`); departamento por nombre sin distinguir mayúsculas/tildes (debe existir); `ans_horas` admite decimales con coma; las filas con error se omiten y el resto se aplica; si una categoría ya existe con tipos, se unen los tipos. Errores de archivo: >2 MB → 413; no UTF-8, sin columnas `departamento`/`categoria` o >5000 filas → 422. Con `ans_horas` hace falta el calendario hábil por defecto (seed SLA); si no existe es error de la línea. **Ojo**: las políticas importadas tienen precedencia 50 (contrato) y las sembradas por prioridad 10-40 se evalúan antes, así que las ANS del catálogo no aplican hasta que el administrador desactive o reordene las de prioridad (conviene avisarlo en la UI de importación/políticas).
- [catalog-core] Pruebas existentes adaptadas (solo datos): las de migración comparan con los modelos en `head` (0006 agrega columnas core/SLA); `test_approvals`/`test_automation` reactivan el tipo `change` en un fixture; una regla de automatización de prueba usa `in_progress`/`triage` en lugar de cerrar (cerrar exige solución); conteos del seed core (10 departamentos/equipos) y tipos del catálogo. `tests/test_core_api.py::test_demo_seed` falla hasta aplicar el cambio solicitado en `seeds/90_core_demo.py` (resolver/cerrar demo con `resolution_note`).
- [frontend-reports] «Acumulado»: la tabla dinámica muestra la columna de total por fila con el rótulo **«Acumulado»** cuando la dimensión de columnas es de fecha (día/semana/mes/año) y «Total» en otro caso, más la fila «Total» (`totals`/`grand_total`). Se respeta `show_totals` de la sección. No se calcula un acumulado progresivo columna a columna (no es válido para promedios/porcentajes).
- [frontend-reports] Programar envíos «si es admin/líder: usa /me»: `GET /me` no expone el rol de líder (`teams[]` no trae `role`), así que el portal usa `isAdmin`, `lead_team_ids` o `teams[].role === 'lead'` si la API los envía (`lib/reports.ts::leadTeamIds`) y, al editar, el `can_schedule` de la plantilla. Hasta que `/me` agregue `lead_team_ids`, un líder no administrador no ve la opción al **crear** una plantilla (sí al editarla); la API sigue siendo la autoridad. Los filtros `assignee_ids` se conservan si vienen en una plantilla pero no tienen selector (no están en `/catalog`). Con `schedule` desactivado el portal envía `schedule: null` (quita la programación); sin permiso de programar lo omite.
- [reports-backend] Acceso: `/reports/*` (incluidos `overview`, `trend`, `by-department`, `by-assignee`, `aging`, `sla` y `export.csv`) usa `get_principal`; el alcance es `visible_tickets_filter` (un solicitante ve solo lo suyo, un aprobador lo que le toca aprobar). La prueba anterior que esperaba 403 se reemplazó por una de alcance.
- [reports-backend] Cálculo: SQL devuelve una fila por ticket (claves de dimensiones + insumos) y Python agrega celda/fila/columna/total, así promedio, mediana y porcentaje de los totales se recalculan sobre los tickets. Instancia SLA de resolución considerada = la última **no cancelada** del ticket. `sla_breached_count` y `sla_compliance_pct` usan solo la métrica `resolution` (cumplidas / (cumplidas + vencidas)); `first_response_hours_avg` son horas **calendario** entre `created_at` y `first_response_at` (no hay minutos hábiles guardados para la primera respuesta). `open_count` = estado actual con categoría distinta de `done`. Días/semanas/meses/años: claves `YYYY-MM-DD`, lunes ISO `YYYY-MM-DD`, `YYYY-MM`, `YYYY`; etiquetas `02/11/2026`, «Semana del 02/11/2026», «Nov 2026», `2026`. Una dimensión de fecha como columna (o como única fila) se completa con los periodos sin datos. Claves nulas: `"_none"` con etiqueta «Sin categoría», «Sin asignar», etc.; `vip` usa `"true"/"false"` («Sí»/«No»).
- [reports-backend] Celdas sin tickets: todas las métricas `null` (también `count`); `grand_total.count` es `0` cuando la sección no tiene tickets. Horas con 2 decimales y porcentajes con 1.
- [reports-backend] `ReportResult` agrega `date_field`, `period`, `timezone` y por sección `show_totals` (= `totals` de la definición; los totales se calculan siempre), `ticket_count` y `truncated` (más de 1000 filas o 100 columnas por sección: se recortan). Más de 200 000 tickets en una sección → 422. Sin `column_dim` la columna `_all` tiene etiqueta «Total». Un `title` de sección vacío se completa («Por categoría según mes»).
- [reports-backend] `ReportDefinition`: `period` por defecto `last_30_days`; `this_month` y `this_year` llegan hasta hoy (no hasta fin de mes/año); `custom` exige `date_from` y `date_to` (422). Rango máx. 366 días (ambas fechas incluidas). Filtros: listas de máx. 200 valores, `channels` limitado a `portal|email|phone|other`. Filas/columnas/métricas repetidas → 422.
- [reports-backend] `GET /reports/builder/meta` agrega `periods`, `detail_columns` (`{key, label, default}`), `limits` y `can_schedule` (admin o líder de equipo; el portal puede usarlo para ofrecer «programar envío» al crear). Etiquetas de `scope.label`: «Todos los tickets», «Tickets de Telecomunicaciones, además de los tuyos», «Tickets solicitados por Gerencia X…», «Tus tickets (solicitados o asignados a ti)».
- [reports-backend] Exportación: `include_detail` por defecto `true`; `detail_columns` admite además `team`, `closed_at` y `first_response_hours` (opcionales, no van por defecto); clave desconocida → 422. La hoja «Detalle» usa solo los filtros globales de la definición (no los de cada sección) y el mismo rango/campo de fecha. Fechas como fecha real de Excel en hora de Guatemala; porcentajes como fracción con formato `0.0%`; textos con `sanitize_cell` y sin caracteres de control no válidos en XML (máx. 32 000 caracteres). Hojas con nombre saneado (31 caracteres, sin `[]:*?/\`, únicos). Libro en modo `write_only` (streaming); título, periodo, alcance y fecha de generación al inicio de cada hoja; títulos inmovilizados y autofiltro en «Detalle».
- [reports-backend] Plantillas: además de lo del contrato existen `GET /reports/templates/{id}`; `run` y `export.xlsx` de plantillas aceptan `?period=` o `?date_from=&date_to=` (este último implica `custom`) para ejecutarla con otro periodo, y `export.xlsx` acepta `?include_detail=false`. `TemplateOut` = `{id, name, description, owner {id, display_name}|null, is_system, definition, shared_roles, shared_department_ids, schedule, next_run_at, last_run_at, last_status, created_at, updated_at, can_edit, can_delete, can_schedule}`; `schedule`, `next_run_at`, `last_run_at` y `last_status` solo se devuelven a quien puede editarla (los destinatarios no se exponen a quien la recibe compartida). El administrador ve todas las plantillas. Compartir por departamento usa los departamentos de los equipos del usuario y su departamento del AD. Máx. 200 plantillas propias por usuario (409). Duplicar crea «<nombre> (copia)» propia, sin compartir ni programación.
- [reports-backend] Programación (`schedule`): `day` 1–28 para `monthly` y 0 (lunes)–6 para `weekly`, `hour` 0–23 (hora de Guatemala), destinatarios únicos en minúsculas; `enabled=true` exige al menos uno. Programar con `enabled=true` sin ser admin/líder → 403; guardar con `enabled=false` o `schedule: null` está permitido a cualquiera y deja `next_run_at = null`. `REPORT_RECIPIENT_DOMAINS` se lee de `settings.report_recipient_domains` si existe y si no de la variable de entorno (ver cambios solicitados).
- [reports-backend] Tarea `reports.run_scheduled` (beat `reports-run-scheduled`, cada 900 s, `expires` 840 s): reserva cada plantilla vencida (avanza `next_run_at` y confirma antes de generar, así no se envía dos veces) y deja `last_status` = `sent` | `smtp_disabled` | `owner_inactive` | `not_allowed` (el dueño ya no es admin ni líder) | `no_recipients` | `invalid_definition` | `error`. El Principal del dueño se reconstruye desde la BD sin grupos AD (`groups=None`): la visibilidad por gerencia solo cuenta las asignaciones manuales (`gerencia_viewers`). El correo reutiliza el cliente SMTP de notificaciones (`services/notifications/email.py::send_message`), un mensaje con todos los destinatarios en `To` y el .xlsx (con detalle) adjunto.
- [reports-backend] Seed `40_report_templates.py`: crea las 5 plantillas si no existe una de sistema con ese nombre y actualiza la definición/descripción de las de sistema existentes (no son editables por usuarios). «Desempeño por técnico» y «Cumplimiento de SLA por categoría» usan `date_field=resolved_at` y `last_month`; «Tickets por gerencia solicitante» `created_at` y `last_month`; «Cumplimiento de SLA por categoría» incluye además `count`. `openpyxl==3.1.5` en `requirements.txt` (hay que reconstruir las imágenes api/worker/beat).
- [frontend-catalog] Los campos de política `category_ids`, `subcategory_ids` y `vip_only` se tipan con *declaration merging* desde `lib/catalog.ts` (`declare module './sla'`), porque `lib/sla.ts` no es de este rol; conviene moverlos a `lib/sla.ts` en integración. `UserOut.department/vip` y los campos nuevos de `Me` son opcionales en `lib/types.ts` para no romper los `UserOut` construidos en el cliente por otros módulos (aprobaciones, automatizaciones).
- [frontend-catalog] Lista de tickets: el parámetro de URL `category` sigue siendo la etapa del flujo (categoría de estado; la etiqueta pasa a «Etapa», el dashboard enlaza con él); la categoría del catálogo usa `category_id` y el tipo `type_key`. Administración usa `?tab=` (`departments|gerencias|aliases|categories|providers|import|users|teams|members|projects`) para poder enlazar, p. ej., `/admin?tab=aliases` desde la notificación `catalog.alias_pending`.
- [frontend-catalog] Resolver: toda transición que entra en un estado «done» desde otro abre el diálogo de solución (sirve de confirmación). Si falta la categoría y el departamento tiene catálogo, el diálogo la pide y la guarda con `PATCH /tickets/{id}` antes de la transición (la transición no acepta `category_id`). «Trasladar a orden de trabajo» se muestra solo a quien puede editar el ticket, si el tipo `work_order` está activo y el ticket no es ya una orden de trabajo; la opción «resolver el original» se deshabilita mientras al original le falte la categoría obligatoria. La plantilla CSV descargable se genera en el navegador (UTF-8 con BOM, datos ficticios).
- [identity-docs] Keycloak 26: en lugar de habilitar `unmanagedAttributePolicy` se **declara** el atributo `department` en el perfil de usuario (lectura admin/usuario, edición solo admin; `keycloak-config` lo agrega de forma idempotente con `GET/PUT users/profile` y `realm-energuate.json` trae el perfil completo para instalaciones nuevas). El mapper LDAP es `department-ad` (`user-attribute-ldap-mapper`, solo lectura, `always.read.value.from.ldap=true`) y el protocol mapper `department` del cliente `servicedesk-web` emite el claim en access token, id token, userinfo e introspección (cadena, no multivalor). `LDAP_DEPARTMENT_ATTRIBUTE` definido pero vacío = sin mapper LDAP. El grupo VIP se crea con el valor de `VIP_GROUP` (compose lo pasa también a `keycloak-config`); los grupos de gerencia no se crean en Keycloak (los trae el AD).
- [identity-docs] `REPORT_RECIPIENT_DOMAINS` y `VIP_GROUP` se pasan a api/worker/beat en ambos compose (`REPORT_RECIPIENT_DOMAINS` no está en `core/config.py`; `services/reports/templates.py` lo lee del entorno como respaldo).
- [identity-docs] `seeds/95_catalog_demo.py` (marca `catalog.demo.v1`, solo con `SEED_DEMO=true`): además de clasificar/resolver los tickets de `90_core_demo`, **crea tickets históricos resueltos** (≈46: 7 por mes en los 6 meses anteriores + 3 del mes en curso, semilla fija) con fechas, instancias SLA, eventos SLA, historial y `business_resolution_minutes` coherentes, porque con los 7 tickets demo los reportes quedaban casi vacíos. Sus ids quedan en `seed_state.data["history_ticket_ids"]`; quedan en estado «Resuelto» (no cerrados) y se descartan sus notificaciones. Consecuencia: `tests/test_core_api.py::test_demo_seed` debe contar `7 + len(history_ticket_ids)` tickets (ver cambios solicitados). Si `90_core_demo` no pudo resolver/cerrar sus tickets (p. ej. por falta de solución) el seed 95 los completa con solución; si un guard de aprobación lo impide quedan como están (el de firmware, tipo `change`, queda en «Nuevo» con aprobación pendiente cuando `SEED_FLOWS_EXAMPLES=true`).
- [identity-docs] Políticas SLA demo por categoría con precedencia 5 y «ANS VIP / Soporte Técnico» (`vip_only`) con 4, para que en la demo se evalúen antes que las políticas por prioridad (10-40). Los agentes demo se agregan a los equipos de los departamentos de atención nuevos (luis.soc → Seguridad Informática, rosa.redes → Soporte Técnico y Sistemas Comerciales, demo.admin → Sistemas Corporativos) y marta.aprobadora es visora manual de la Gerencia Comercial. Las gerencias demo usan grupos `GG-SD-Gerencia-*`.
- [integration] Cambios solicitados aplicados: `GET /me` agrega `lead_team_ids` (equipos donde es líder); `Settings.report_recipient_domains` (env `REPORT_RECIPIENT_DOMAINS`) es la única fuente de dominios permitidos (se retiró el respaldo por `os.environ`); `seeds/90_core_demo.py` resuelve/cierra con una solución de demostración; `test_demo_seed` cuenta `7 + history_ticket_ids` de `catalog.demo.v1`; `lib/sla.ts` declara `category_ids`, `subcategory_ids` y `vip_only` (se quitó el *declaration merging* de `lib/catalog.ts`); el enlace «Reportes» del menú es visible para todo usuario autenticado; el portal usa `meta.can_schedule`.
- [integration] Resolver con aprobación o automatización: (a) `transition_ticket` guarda la solución del payload **antes** de los guards, así una transición que queda pendiente de aprobación (409) conserva la solución y la transición aprobada la encuentra (la solución queda guardada aunque el ticket aún no esté resuelto); (b) las acciones `transition` / `request_approval` de automatizaciones aceptan `body` como **solución** (plantilla con variables) cuando el destino es un estado `done`; solo se usa si el ticket aún no tiene solución. La UI de reglas muestra el campo «Solución» al elegir un estado resuelto. Sin `body`, la acción falla con «Indique la solución…» y queda registrada en la ejecución.
- [integration] Kanban: soltar un ticket en un estado «done» abre el mismo diálogo de solución (`ResolveDialog`, con la categoría si hace falta) antes de transicionar. Campana de notificaciones: para administradores muestra los valores del AD pendientes (`GET /admin/department-aliases?pending=true`, refrescado con el evento `catalog.alias_pending`) con enlace a `/admin?tab=aliases`; suman al contador. Modo dev: campo «Departamento AD» → cabecera `X-Dev-Department`. La barra lateral muestra VIP y el departamento del usuario.
- [review-fix] Categoría al resolver según el tipo: `department_has_categories(session, department_id, type_key)` solo cuenta categorías activas sin restricción de tipo o que admiten el tipo del ticket; si ninguna lo admite (p. ej. una orden de trabajo o un `change` antiguo en un departamento con categorías solo de incidencias) se resuelve sin categoría. Aplica a transiciones, al SAVEPOINT de la orden de trabajo y a las automatizaciones (todas pasan por `transition_ticket`); el portal usa la misma regla (`needsCategoryToResolve`, `departmentHasCategories(catalog, depto, tipo)`).
- [review-fix] Solución oficial: en una transición `done → done` (p. ej. cerrar un ticket resuelto) la `resolution_note` del payload solo se acepta de quien puede editar el ticket; la del solicitante se ignora en silencio (no 422, para no romper el cierre). Al resolver (entrar en `done`) se sigue aceptando de quien transiciona.
- [review-fix] Primera respuesta SLA: `counts_as_first_response` exige además `can_edit_ticket` (atiende el ticket); un agente que solo lo ve por aprobación o por visibilidad de gerencia ya no la cumple al comentar en público.
- [review-fix] Departamento del AD: nueva columna `users.ad_department_value` (migración `0008_phase3_fixes`, valor normalizado del último login). Si el valor cambia y el alias está pendiente o sin departamento, `users.department_id` pasa a `NULL`; si no cambia se respeta el ajuste manual. Las filas existentes quedan nulas y en su primer login cuentan como «cambio» (un ajuste manual de un usuario con valor pendiente se pierde una vez; asigne el alias). `POST|PATCH /admin/department-aliases` aplican la asignación al instante a los usuarios con ese valor y completan `requester_department_id` **solo** de sus tickets que lo tienen nulo (no reescribe la foto de los que ya lo tienen); retirar el departamento del alias lo retira de los usuarios (no de los tickets). Esto reemplaza el «basta con el siguiente login» del contrato.
- [review-fix] Envío programado: si el dueño no inicia sesión en `ACCOUNT_STALE_DAYS` (`core/accounts.py::is_stale`) el estado es `owner_stale` y no se envía. Ante `error` se reintenta a los 15, 30 y 60 min (`RETRY_DELAYS_MINUTES`, columna `report_templates.retry_count` en `0008_phase3_fixes`) con `last_status = "retrying"`, sin pasar del siguiente periodo y conservando la reserva previa al envío; guardar la programación reinicia el contador.
- [review-fix] Hoja «Detalle»: `render_xlsx` cuenta antes los tickets (`builder.count_detail_rows`); si superan `MAX_DETAIL_ROWS` la fila de títulos de «Detalle» lleva a la derecha el aviso «Detalle recortado: se incluyen los primeros N de M tickets…», cada hoja de sección lo muestra en la fila 5 («Aviso:», que antes iba en blanco, sin mover la tabla) y el correo programado lo menciona. El resultado de `render_xlsx` agrega `detail = {total, rows, truncated}` (el JSON de `/reports/builder/run` no cambia: no incluye detalle).
- [review-fix] Horas hábiles en políticas 24x7: se mantiene el contrato (minutos de la instancia SLA con su calendario) porque el informe compara esas horas con `sla_target_hours`, que está en las mismas unidades; cambiar solo la resolución al calendario hábil dejaría el promedio y el objetivo en unidades distintas. Se aclaró el rótulo en el portal y en `docs/REPORTS.md`.
- [review-fix] `REPORT_RECIPIENT_DOMAINS` en ambos compose: `${REPORT_RECIPIENT_DOMAINS-energuate.com}` (sin definir → `energuate.com`; definida vacía → cualquier dominio, decisión explícita del operador).
- [coordinador] Orden de políticas SLA: la especificidad va **antes** que la precedencia (subcategoría > categoría > general; precedencia solo desempata dentro del nivel). Decisión del usuario: los SLA son por departamento con categoría/subcategoría, así que una ANS de catálogo (importada con precedencia 50) siempre gana a las políticas por prioridad sembradas (10-40). Se quitaron los avisos de "política sombreada" de la UI.
