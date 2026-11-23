# Energuate Service Desk — MVP Fase 1

Stack: Next.js 15, React 19, TypeScript, FastAPI, PostgreSQL 16, Redis 7, Celery, Keycloak 26, Recharts, Docker Compose.

## Requisitos
Docker Engine + Docker Compose v2, o Docker Desktop con WSL2. Recomendado: 8 GB RAM libres para los contenedores.

## Ejecutar (solo entorno local de desarrollo)
```bash
cp .env.example .env
# Editar .env con contraseñas únicas antes de iniciar
# Mantener AUTH_MODE=dev únicamente en entorno aislado
docker compose up --build -d
docker compose ps
```

En Windows PowerShell, usar `Copy-Item .env.example .env`.

- Portal: http://localhost:3000
- API / Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/health
- Keycloak: http://localhost:8081

## Probar API
```bash
curl -X POST http://localhost:8000/tickets -H 'Content-Type: application/json' -d '{"title":"Prueba de conectividad","description":"Enlace de oficina","department":"Telecomunicaciones","priority":"high"}'
curl http://localhost:8000/tickets
```

## Estado de implementación
- Funcional: creación, listado, cambio de estado, comentarios vía API, conteo por estado, dashboard inicial y auditoría básica.
- Preparado, no integrado: Keycloak, validación JWT API, Redis/Celery.
- Pendiente: inicio de sesión OIDC frontend, integración AD, SLA mixto, Kanban, WebSockets, aprobaciones, automatizaciones, adjuntos, permisos por área, reportes avanzados.

## Advertencias
**No desplegar en producción**: `AUTH_MODE=dev` otorga rol admin a cualquier petición, sin autenticación. Puertos publicados en todas las interfaces por defecto. Usar únicamente en máquina/red de laboratorio y restringir exposición. Antes de un piloto corporativo, implementar login OIDC completo, autorización por equipo, TLS, secretos, migraciones, pruebas, backups y monitoreo.

Ver `docs/ARCHITECTURE.md` y `docs/DATA_MODEL.md`.
