# Arquitectura

Browser -> Next.js -> FastAPI -> PostgreSQL
FastAPI -> Redis -> Celery worker
Keycloak -> LDAP/Active Directory (configuración futura)

## Límites de la fase 1
- El modo `AUTH_MODE=dev` es una **suplantación de identidad de desarrollo**. No exponer fuera de localhost/red aislada.
- Keycloak arranca, pero el realm, clientes, roles y federación LDAP no se crean automáticamente.
- La API puede validar JWT de Keycloak en `AUTH_MODE=oidc`, una vez configurado el realm y la audiencia; el frontend aún no implementa el flujo de login OIDC.
- La API expone endpoints CRUD parciales; no hay WebSockets ni SLA operativos todavía.
- Las notificaciones Celery son tareas de ejemplo, sin correo ni SMS.
- No hay alta disponibilidad, backups automáticos, adjuntos ni RBAC granular por unidad.
- Los esquemas se crean en el arranque para MVP; usar Alembic en producción.
- No usar datos reales de producción hasta completar controles y pruebas.
