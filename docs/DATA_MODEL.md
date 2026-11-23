# Modelo de datos

## Implementado en el MVP
- `tickets`: id, title, description, status, priority, department, requester, assignee, created_at.
- `comments`: id, ticket_id, author, body.
- `audit_events`: id, ticket_id, actor, action, timestamp.

## Modelo objetivo para producción
- `users`: id, keycloak_sub, display_name, email, active.
- `departments`: id, name, parent_id.
- `teams`: id, department_id, name.
- `team_members`: team_id, user_id, role.
- `projects`: id, code, name, owner_id, team_id.
- `tickets`: id, public_key, project_id, type_id, requester_id, assignee_id, team_id, priority_id, status_id, timestamps.
- `ticket_comments`: id, ticket_id, author_id, body, visibility.
- `ticket_attachments`: id, ticket_id, object_key, checksum, size, mime.
- `ticket_history`: id, ticket_id, actor_id, before_json, after_json, timestamp.
- `workflow_definitions`, `workflow_states`, `workflow_transitions`.
- `approval_requests`, `approval_steps`, `approval_decisions`.
- `sla_policies`, `business_calendars`, `calendar_holidays`, `sla_instances`, `sla_events`.
- `automation_rules`, `automation_executions`, `notifications`.
- `assets`, `asset_ticket_links` (CMDB futura).

## SLA mixto
Cada política referencia un calendario con zona horaria America/Guatemala, días hábiles, ventanas de servicio, feriados y reglas de pausa. Las métricas de primera respuesta y resolución se calculan por eventos, no solo por diferencia de timestamps.
