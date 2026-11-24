# Operación

Todos los comandos se ejecutan en el servidor, en la raíz del repositorio (`/opt/energuate-sd`).
Para abreviar:

```bash
alias dcp='docker compose -f docker-compose.prod.yml'
```

Los scripts de `scripts/` usan por defecto `docker-compose.prod.yml` y `.env`; para el laboratorio:
`SD_COMPOSE_FILE=docker-compose.yml`.

## Servicios y estado

| Servicio | Función | Estado esperado |
|---|---|---|
| `caddy` | Proxy TLS (80/443) | running (healthy) |
| `web` | Portal Next.js | running (healthy) |
| `api` | API REST + WebSocket; aplica migraciones y seeds al arrancar | running (healthy) |
| `worker` | Celery: SLA, notificaciones, automatizaciones diferidas | running (healthy) |
| `beat` | Planificador Celery (SLA cada 60 s, reintento de correos cada 5 min) | running — **una sola instancia** |
| `db` | PostgreSQL de la aplicación | running (healthy) |
| `redis` | Broker Celery y canal `servicedesk.events` | running (healthy) |
| `keycloak` / `keycloak-db` | Identidad / su base de datos | running (healthy) |
| `minio` | Adjuntos (S3) | running (healthy) |
| `keycloak-config`, `minio-init` | One-shot de configuración | exited (0) |

```bash
dcp ps
scripts/healthcheck.sh            # resumen OK/WARN/FAIL, código 0/2/1
```

## Copias de seguridad

`scripts/backup.sh` genera en `BACKUP_DIR/<AAAAMMDD-HHMMSS>/`:

| Archivo | Contenido |
|---|---|
| `servicedesk.dump` | `pg_dump -Fc` (comprimido) de la BD de la aplicación |
| `keycloak.dump` | `pg_dump -Fc` de la BD de Keycloak (realm, usuarios locales, federación, sesiones offline) |
| `attachments.tar.gz` | Copia puntual del bucket de adjuntos (`mc mirror` incremental + `tar`) |
| `MANIFEST` | Fecha, commit, revisión Alembic, tamaño y SHA-256 de cada archivo |

- Cada volcado se verifica con `pg_restore --list` antes de darse por bueno.
- Una copia fallida queda como `<fecha>.failed` (no cuenta como válida) y el script sale con error.
- Retención: `BACKUP_RETENTION_DAYS` (por defecto 14) días.
- Las copias contienen datos personales y hashes: la carpeta se crea con permisos `700`.
- **No incluyen `.env`** (secretos): guárdelo en el gestor de secretos corporativo. Sin él no se
  pueden reconstruir las contraseñas de servicio (sí los datos).

Programación (crontab del usuario de operación):

```cron
15 2 * * *  /opt/energuate-sd/scripts/backup.sh >>/var/log/energuate-sd/backup.log 2>&1
*/5 * * * * /opt/energuate-sd/scripts/healthcheck.sh >/var/log/energuate-sd/health.last 2>&1 || logger -t energuate-sd "healthcheck: $(tail -n1 /var/log/energuate-sd/health.last)"
```

Replique `BACKUP_DIR` fuera del servidor (NAS, almacenamiento de objetos corporativo, cinta), por
ejemplo con `rsync -a --delete` o `rclone sync` tras la copia. Objetivo orientativo: RPO 24 h
(reducible ejecutando el backup varias veces al día) y RTO < 2 h.

## Restauración

```bash
scripts/restore.sh /var/backups/energuate-sd/20261123-021500          # todo (pide escribir RESTAURAR)
scripts/restore.sh /var/backups/energuate-sd/20261123-021500 --only app
scripts/restore.sh /var/backups/energuate-sd/20261123-021500 --only keycloak
scripts/restore.sh /var/backups/energuate-sd/20261123-021500 --only minio
```

El script verifica los SHA-256, detiene los servicios que escriben, recrea cada base de datos
(`DROP` + `CREATE`), aplica `pg_restore --single-transaction --exit-on-error`, sincroniza el bucket
(`mc mirror --remove`) y vuelve a levantar todo. Si la copia es de una versión anterior del código,
`api` aplica las migraciones pendientes al arrancar. Para restaurar en **otro servidor**: despliegue
la misma versión (tag) con su `.env`, arranque una vez (`dcp up -d`), y ejecute `restore.sh`.

Pruebe la restauración al menos **trimestralmente** en un entorno aparte y registre el resultado.

## Actualización de versión

```bash
cd /opt/energuate-sd
scripts/backup.sh                                   # 1. copia previa obligatoria
git fetch --tags && git checkout v1.2.0             # 2. versión a desplegar
diff <(grep -oE '^[A-Z_]+=' .env | sort) <(grep -oE '^[A-Z_]+=' .env.production.example | sort)
                                                     # 3. ¿variables nuevas? añádalas a .env
dcp config -q                                       # 4. validar
dcp build --pull                                    # 5. construir imágenes
dcp up -d                                           # 6. recrear lo que cambió
dcp logs -f api                                     # 7. "alembic upgrade head", seeds, uvicorn
scripts/healthcheck.sh                              # 8. verificar
```

- Las migraciones Alembic se ejecutan solas al arrancar `api` (`worker` y `beat` esperan a que `api`
  esté sana). Para aplicarlas sin arrancar la API: `dcp run --rm api migrate`.
- Los seeds también corren en cada arranque, pero los datos de configuración inicial (departamentos,
  calendarios/políticas SLA y ejemplos de flujos) solo se crean la primera vez (`seed_state`); lo
  que el administrador borre o renombre no se restaura. Los feriados oficiales de años nuevos sí se
  agregan solos.
- Si `preflight` falla tras actualizar (variables nuevas o endurecidas), `dcp logs preflight` indica
  qué variable corregir.
- Interrupción esperada: segundos (recreación de `api`/`web`). Para cambios con migraciones largas,
  anuncie una ventana de mantenimiento.
- **Vuelta atrás**: `git checkout <tag-anterior>`, `dcp build && dcp up -d`. Si la versión nueva
  aplicó migraciones incompatibles, restaure la copia del paso 1 (`scripts/restore.sh … --only app`).
- Cambiar `APP_DOMAIN` exige reconstruir `web` (`NEXT_PUBLIC_*` se incrustan en el build) y
  reiniciar `keycloak` y `keycloak-config`.
- Actualizar Keycloak (`KEYCLOAK_IMAGE`): copia previa de `keycloak-db`; Keycloak migra su esquema
  al arrancar. Revise las notas de versión antes de saltos mayores.

## Rotación de secretos

Tras cambiar un valor en `.env`, recree solo los servicios afectados. Rote al menos una vez al año,
al salir un administrador del equipo y ante cualquier sospecha de exposición.

| Secreto | Procedimiento |
|---|---|
| `DB_PASSWORD` | `dcp exec db psql -U servicedesk -d servicedesk -c "ALTER USER servicedesk PASSWORD 'nueva'"`; actualice `.env`; `dcp up -d api worker beat`. (La variable de la imagen de Postgres solo se usa al crear el volumen.) |
| `KC_DB_PASSWORD` | `dcp exec keycloak-db psql -U keycloak -d keycloak -c "ALTER USER keycloak PASSWORD 'nueva'"`; `.env`; `dcp up -d keycloak`. |
| `REDIS_PASSWORD` | `.env`; `dcp up -d redis api worker beat` (breve corte de tiempo real). |
| `KC_ADMIN_PASSWORD` | Solo aplica al bootstrap. Cambie la contraseña del administrador en la consola de Keycloak (o elimine el bootstrap) y actualice `.env` para que `keycloak-config` pueda autenticarse. |
| `LDAP_BIND_CREDENTIAL` | Cambie la contraseña en AD; `.env`; `dcp up keycloak-config` (actualiza la federación). |
| `MINIO_ROOT_PASSWORD` | Actualice `.env` y ejecute `dcp up -d minio` (MinIO toma las credenciales root del entorno en cada arranque) y después `dcp up minio-init`. |
| `S3_SECRET_KEY` | `.env`; `dcp up minio-init` (actualiza la cuenta de servicio); `dcp up -d api worker`. |
| `SMTP_PASSWORD` | `.env`; `dcp up -d api worker`. |
| Claves de firma de Keycloak | Consola → realm `energuate` → *Realm settings → Keys → Providers*: añada un `rsa-generated` nuevo con prioridad mayor, espere > 10 h (vida de las sesiones) y desactive el anterior. La API recarga el JWKS sola. |
| Certificado TLS (`files`) | Sustituya `deploy/certs/tls.*`; `dcp exec caddy caddy reload --config /etc/caddy/Caddyfile`. |

## Logs

Todos los contenedores escriben en stdout/stderr con rotación local (`json-file`,
`LOG_MAX_SIZE`×`LOG_MAX_FILE`, por defecto 20 MB × 5).

```bash
dcp logs -f --tail=200 api worker          # aplicación
dcp logs --since 1h caddy                  # accesos HTTP (JSON)
dcp logs keycloak | grep -i 'type=LOGIN_ERROR'   # fallos de login (eventos de Keycloak)
```

- Caddy y Keycloak (`KC_LOG_FORMAT=json`) emiten JSON, listo para un colector (Fluent Bit, Vector,
  Wazuh, agente del SIEM) que lea `/var/lib/docker/containers/*/*-json.log` o use el driver de
  logging corporativo (`syslog`/`gelf`) configurado en `/etc/docker/daemon.json`.
- Caddy oculta `?token=` y `?code=` en los registros. La API recibe el token del WebSocket por query:
  no active logs de acceso adicionales que registren URLs completas.
- Eventos de seguridad de Keycloak (login, fallos, cambios de administración) se guardan 30 días
  en su BD y aparecen en sus logs (`jboss-logging`).

## Monitoreo

| Qué | Cómo |
|---|---|
| Disponibilidad | `scripts/healthcheck.sh` (cron/Nagios/Zabbix: 0 OK, 2 advertencia, 1 fallo) o sonda HTTP externa a `https://APP_DOMAIN/api/health` (200 = API + BD) |
| Contenedores | `docker inspect` / `dcp ps` (estado `healthy`), reinicios (`RestartCount`) |
| Keycloak | Métricas Prometheus en `keycloak:9000/metrics` (red interna; exponga solo a un Prometheus en la misma red) |
| Celery | `dcp exec worker celery -A app.tasks.celery_app inspect active` / `inspect stats` |
| Cola Redis | `dcp exec redis redis-cli llen celery` (cola acumulada = faltan workers) |
| PostgreSQL | `dcp exec db psql -U servicedesk -c "select count(*) from pg_stat_activity"`; tamaño: `\l+` |
| Disco | `healthcheck.sh` alerta con < 15 % libre (`MIN_FREE_DISK_PCT`) |
| Certificado | `healthcheck.sh` alerta 30/14 días antes del vencimiento |
| Copias | `healthcheck.sh` falla si la última copia tiene más de 26 h (`MAX_BACKUP_AGE_HOURS`) |

## Escalado

- **API**: aumente `UVICORN_WORKERS` (≈ núcleos asignados) y `API_CPUS`/`API_MEMORY`. Cada proceso
  mantiene sus propias conexiones WebSocket y su suscripción a Redis, por lo que el tiempo real
  funciona con varios procesos.
- **Workers**: `CELERY_CONCURRENCY` por contenedor o más contenedores:
  `dcp up -d --scale worker=3`. Los workers no publican puertos, así que escalan sin conflicto.
- **Beat**: nunca más de una instancia (duplicaría las tareas periódicas).
- **PostgreSQL**: `DB_MEMORY`; ajuste `shared_buffers`/`work_mem` si crece el volumen (añadiendo
  `command: postgres -c shared_buffers=512MB …` en un override local).
- Alta disponibilidad real (varios nodos, PostgreSQL replicado, MinIO distribuido) queda fuera de este
  despliegue de un solo servidor; los datos ya están separados en volúmenes para facilitar la
  migración a servicios gestionados (S3, PostgreSQL corporativo) cambiando `DATABASE_URL`/`S3_*`.

## Tareas frecuentes

```bash
dcp up keycloak-config                       # reaplicar configuración de Keycloak/AD (idempotente)
dcp restart keycloak                         # tras añadir una CA al truststore
dcp exec api python -m app.seeds             # reejecutar seeds (idempotentes)
dcp exec db psql -U servicedesk servicedesk  # consola SQL
dcp exec api alembic current                 # revisión de esquema aplicada
dcp down                                     # detener (conserva volúmenes)
```

**Reportes programados**: los destinatarios solo pueden ser de los dominios de
`REPORT_RECIPIENT_DOMAINS` (coma). Si la variable no está en `.env`, compose usa `energuate.com`;
solo si se define **vacía** (`REPORT_RECIPIENT_DOMAINS=`) se permite cualquier dominio, lo que deja a
administradores y líderes enviar el Excel con detalle de tickets a correos externos. Un envío que
falla (SMTP caído, timeout) se reintenta a los 15, 30 y 60 minutos antes de esperar al siguiente
periodo (estado «Error al enviar (se reintentará)»); si el dueño de la plantilla no inicia sesión en
`ACCOUNT_STALE_DAYS` días los envíos se suspenden (estado «Dueño sin sesión reciente») hasta que vuelva
a entrar.

**Nunca** ejecute `dcp down -v` en producción: borra bases de datos, adjuntos y la configuración de
Keycloak.

## Incidentes comunes

| Síntoma | Diagnóstico | Acción |
|---|---|---|
| Portal muestra error de login / bucle de redirección | `dcp logs keycloak`; `healthcheck.sh` (issuer) | Verifique `APP_DOMAIN`, que `web` se construyó con el dominio actual y que `keycloak-config` terminó bien. |
| API 401 con token válido | `iss`/`aud` del token (`scripts/get-test-token.sh --claims`) | `OIDC_ISSUER` debe ser `https://APP_DOMAIN/auth/realms/energuate`; reloj del servidor. |
| 502 en `/api` | `dcp ps api`, `dcp logs api` | Migración fallida o BD caída; corrija y `dcp up -d api`. |
| Kanban no se actualiza | `dcp logs api` (mensajes de `/ws`); estado de Redis | Revise `REDIS_PASSWORD` y que el proxy no corte `/ws`. |
| No llegan correos | `dcp logs worker`; tabla `notifications.delivery_status` | Conectividad al relay SMTP (red `egress`), credenciales. |
| SLA no avanza | `dcp logs beat worker` | `beat` en ejecución (una instancia), cola Redis. |
| Usuario sin rol/equipo | `get-test-token.sh --claims` | Ver [AD_INTEGRATION.md](AD_INTEGRATION.md#9-solución-de-problemas). |
| Ningún servicio arranca | `dcp logs preflight` | Corrija las variables indicadas en `.env` (sin `replace-...`, ≥ 16 caracteres). |
| 429 al crear tickets o subir adjuntos | Mensaje de la API | Límites anti-abuso (`TICKETS_PER_USER_HOUR`, `ATTACHMENT_USER_DAILY_*`); ajústelos en `.env` si el uso es legítimo. |
| «No ha iniciado sesión en los últimos N días» al asignar | `users.last_login_at` | La persona debe iniciar sesión (sincroniza roles AD) o ajuste `ACCOUNT_STALE_DAYS`. |
| Reporte programado en «Dueño sin sesión reciente» | `users.last_login_at` del dueño | El dueño debe iniciar sesión (revalida sus roles/equipos) o reasigne la plantilla a otra persona. |
| WebSocket cerrado con 1013 «Demasiadas conexiones» | Pestañas abiertas del usuario | Cerrar pestañas o subir `WS_MAX_CONNECTIONS_PER_USER`. |
