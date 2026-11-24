# Despliegue en producción

Producción usa **`docker-compose.prod.yml`** (archivo completo, independiente de
`docker-compose.yml`, que es solo para laboratorio) y un único dominio HTTPS servido por **Caddy**.

```text
https://APP_DOMAIN/          → web (Next.js)
https://APP_DOMAIN/api/*     → api (FastAPI; Caddy elimina el prefijo /api)
https://APP_DOMAIN/ws        → api (WebSocket de tiempo real)
https://APP_DOMAIN/auth/*    → keycloak (KC_HTTP_RELATIVE_PATH=/auth)
```

**Decisión: Keycloak bajo `/auth` en el mismo dominio** (no un `AUTH_DOMAIN` aparte):
un solo registro DNS y un solo certificado, sin CORS entre portal y Keycloak, cookies de primera
parte (el `check-sso` silencioso funciona aunque el navegador bloquee cookies de terceros) y la
consola de administración se filtra por IP en el mismo proxy.

## 1. Requisitos del servidor

| Recurso | Mínimo | Recomendado |
|---|---|---|
| SO | Linux x86_64 con systemd (RHEL/Rocky/Alma 9, Ubuntu 22.04/24.04, Debian 12) | — |
| CPU | 4 vCPU | 8 vCPU |
| RAM | 8 GB | 16 GB |
| Disco | 60 GB SSD (datos + imágenes) | 100 GB SSD + volumen aparte para copias |
| Software | Docker Engine 24+ y Docker Compose v2.24+ (`docker compose version`) | — |

- La suma de límites de `docker-compose.prod.yml` es ≈ 9 GB; ajústelos con las variables
  `*_MEMORY`/`*_CPUS` (ver `.env.production.example`).
- Reloj sincronizado (chrony/NTP): los tokens JWT y el SLA dependen de la hora.
- Usuario de operación miembro del grupo `docker`; el repositorio en `/opt/energuate-sd`.

### Red y firewall

| Origen → destino | Puerto | Motivo |
|---|---|---|
| Usuarios → servidor | 443/tcp (+ 443/udp HTTP/3) | Portal, API y login |
| Usuarios → servidor | 80/tcp | Redirección a HTTPS (y validación ACME HTTP-01) |
| Servidor → controladores de dominio | 636/tcp (o 389 con StartTLS) | Federación AD |
| Servidor → relay SMTP | 587/tcp (o el que corresponda) | Notificaciones por correo |
| Servidor → Internet (opcional) | 443/tcp | ACME (Let's Encrypt), descarga de imágenes, `npm`/`pip` en el build |
| Administradores → servidor | 22/tcp | SSH (túnel a la consola de Keycloak) |

Ningún otro puerto se publica: PostgreSQL, Redis, MinIO, la API, Next.js y Keycloak solo son
accesibles en las redes internas de Docker. La red `backend` está marcada `internal` (sin salida).

Si la red interna `edge` (`EDGE_SUBNET`, por defecto `10.250.250.0/24`) se solapa con rutas de la
LAN de Energuate, cambie `EDGE_SUBNET` y `CADDY_EDGE_IP` (deben ser coherentes).

## 2. DNS y TLS

1. Cree el registro `A` (y `AAAA` si aplica) `APP_DOMAIN` → IP del servidor, p. ej.
   `servicedesk.energuate.com`. Debe resolverse igual desde la LAN y desde el propio servidor.
2. Elija el modo de certificado (`TLS_MODE`):

| `TLS_MODE` | Cuándo | Requisitos |
|---|---|---|
| `files` | **Recomendado para un dominio interno.** Certificado emitido por la CA corporativa o comercial. | `deploy/certs/tls.crt` (servidor + intermedias) y `deploy/certs/tls.key` (`chmod 600`). |
| `acme` | Dominio público alcanzable desde Internet. | Puertos 80/443 accesibles desde la CA, `ACME_EMAIL`. Renovación automática. |
| `internal` | Preproducción / pruebas. | Los navegadores no confiarán en el certificado. |

Caddy aplica TLS 1.2/1.3, redirige HTTP→HTTPS y añade HSTS (1 año), `X-Content-Type-Options`,
`Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `X-Frame-Options` y una CSP
por defecto (ver `deploy/Caddyfile`). Los registros de acceso ocultan `?token=` y `?code=`.

Renovación de un certificado `files`: sustituya los archivos y ejecute
`docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile`.

## 3. Primer despliegue

```bash
# 1. Código
sudo mkdir -p /opt/energuate-sd && sudo chown "$USER": /opt/energuate-sd
git clone https://github.com/warosc/enerticket.git /opt/energuate-sd
cd /opt/energuate-sd
git checkout <tag-de-versión>          # p. ej. v1.0.0; nunca despliegue una rama en movimiento

# 2. Variables
cp .env.production.example .env && chmod 600 .env
vi .env                                # APP_DOMAIN, TLS_MODE, todos los replace-..., LDAP_*, SMTP_*
# Generar secretos:
for i in 1 2 3 4 5 6; do openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40; echo; done

# 3. Certificados (TLS_MODE=files) y CA del AD
cp /ruta/servicedesk.crt deploy/certs/tls.crt    # cadena completa
cp /ruta/servicedesk.key deploy/certs/tls.key && chmod 600 deploy/certs/tls.key
cp /ruta/energuate-root-ca.pem keycloak/truststore/

# 4. Validar
docker compose -f docker-compose.prod.yml config -q && echo "configuración válida"
docker compose -f docker-compose.prod.yml run --rm preflight   # credenciales: sin replace-..., >= 16 caracteres

# 5. Construir y arrancar
#    (Docker Compose v5 en Windows: si el build falla en Buildx Bake por entitlements "fs",
#     anteponga BUILDX_BAKE_ENTITLEMENTS_FS=0; en PowerShell: $env:BUILDX_BAKE_ENTITLEMENTS_FS=0)
docker compose -f docker-compose.prod.yml build --pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps          # todos "healthy" (Keycloak tarda 2-3 min)
docker compose -f docker-compose.prod.yml logs -f keycloak-config   # federación AD y roles

# 6. Verificar
scripts/healthcheck.sh                                 # añada --cacert <CA> si el certificado es interno
```

> El servicio one-shot **`preflight`** (`deploy/preflight.sh`) se ejecuta antes que todos los demás:
> si alguna credencial conserva el valor de la plantilla (`replace-...`), es trivial (`changeme`,
> `admin`, …) o mide menos de `PREFLIGHT_MIN_SECRET_LENGTH` (16) caracteres, o si `APP_DOMAIN` /
> `SMTP_FROM` no son válidos, **ningún servicio arranca**. Vea el motivo con
> `docker compose -f docker-compose.prod.yml logs preflight` (nunca muestra los valores).
>
> Los datos iniciales que el administrador puede editar (departamentos, calendarios y políticas
> SLA, ejemplos de flujos) se siembran **una sola vez** (tabla `seed_state`): renombrarlos o
> borrarlos es definitivo, no reaparecen al reiniciar. `SEED_FLOWS_EXAMPLES=false` (por defecto en
> producción) no crea la política de ejemplo «Cambios» ni sus reglas.

Orden de arranque (lo resuelven `depends_on` + healthchecks): `db`, `redis`, `keycloak-db`, `minio`
→ `keycloak` (importa el realm la primera vez) → `keycloak-config` y `minio-init` (one-shot) → `api`
(migraciones Alembic + seeds + uvicorn) → `worker`, `beat`, `web` → `caddy`.

Lo que fija `docker-compose.prod.yml` sin posibilidad de cambiarlo desde `.env`:
`ENVIRONMENT=production`, `AUTH_MODE=oidc`, `SEED_DEMO=false`, `KC_LAB_USERS=false`,
`LDAP_ALLOW_INSECURE=false`, `NEXT_PUBLIC_AUTH_MODE=oidc`, Keycloak con `start` (modo producción),
consola de MinIO desactivada y puertos publicados solo por Caddy.

### Imágenes desde un registro (opcional)

Para no compilar en el servidor, construya en CI y publique en el registro corporativo; después
defina en `.env` `BACKEND_IMAGE` y `WEB_IMAGE` y use `docker compose … pull && … up -d --no-build`.
La imagen `web` incrusta `APP_DOMAIN` en el build (`NEXT_PUBLIC_*`): constrúyala con los mismos
`build-args` que `docker-compose.prod.yml`.

## 4. Configuración inicial de Keycloak

1. Acceso a la consola: Caddy responde 403 en `/auth/admin` y en el realm `master` salvo para
   las redes de `ADMIN_ALLOWED_CIDRS`. El valor por defecto (`127.0.0.1/32`) equivale a "nadie":
   defina la VLAN o los equipos de administración de TI (p. ej. `10.10.20.0/24 10.10.30.15/32`),
   aplique con `docker compose -f docker-compose.prod.yml up -d caddy` y abra
   `https://APP_DOMAIN/auth/admin/` desde uno de esos equipos. (Un túnel SSH no sirve: el nombre y
   el puerto deben coincidir con `KC_HOSTNAME`.)
2. Inicie sesión con `KC_ADMIN` / `KC_ADMIN_PASSWORD` (realm `master`).
3. Cree un administrador **nominal** en `master` (rol `admin`), active OTP para él, cierre sesión,
   entre con el nominal y **elimine o deshabilite** la cuenta bootstrap.
4. Realm `energuate` → *Realm settings → Email*: configure el SMTP corporativo (lo usa Keycloak
   para avisos; las notificaciones de la aplicación usan `SMTP_*`).
5. Compruebe *User federation → energuate-ad* y *Groups* (grupos `GG-SD-*` con sus roles).

`keycloak-config` se ejecuta en cada `up` y vuelve a fijar: redirect URIs del cliente
`servicedesk-web` (`https://APP_DOMAIN/*`), federación AD, mapper de grupos, grupos de equipo,
roles por grupo y el tema de login `energuate` (`keycloak/themes/energuate`, montado de solo
lectura en `/opt/keycloak/themes/energuate`; `KC_LOGIN_THEME` vacío lo deja como esté). Con
`start` Keycloak guarda los temas en caché: tras cambiar el CSS del tema reinicie `keycloak`.
Los demás cambios hechos en la consola se conservan.

## 5. Actualizaciones

Ver [`OPERATIONS.md`](OPERATIONS.md#actualización-de-versión) (copia previa, `git checkout` del tag,
build, `up -d`; las migraciones se aplican solas al arrancar `api`).

## 6. Checklist de go-live

### Infraestructura

- [ ] DNS `APP_DOMAIN` resuelve desde la LAN y el servidor; certificado válido (cadena completa).
- [ ] Firewall: solo 80/443 (y 22 restringido) entrantes; salida a DC (636/389) y SMTP.
- [ ] `docker compose -f docker-compose.prod.yml config -q` sin errores; `.env` con `chmod 600`.
- [ ] Ningún valor `replace-...` en `.env` (`grep replace- .env` vacío).
- [ ] `scripts/healthcheck.sh` → `RESULTADO: OK`.
- [ ] NTP sincronizado; zona horaria del negocio `TZ_BUSINESS=America/Guatemala`.

### Identidad

- [ ] Federación AD con LDAPS/StartTLS y CA corporativa; prueba de conexión correcta en los logs.
- [ ] Usuarios reales de cada rol inician sesión y ven lo que corresponde (admin, agente de cada
      área, aprobador, solicitante).
- [ ] Cuenta bootstrap de Keycloak eliminada; administradores nominales con OTP.
- [ ] `/auth/admin` responde 403 desde una IP de usuario (healthcheck lo comprueba).
- [ ] Usuarios de laboratorio inexistentes o deshabilitados.

### Aplicación

- [ ] Equipos con su `external_group` correcto (Administración → Equipos).
- [ ] Calendario hábil, feriados de Guatemala y políticas de SLA revisados (módulo SLA).
- [ ] Flujos de aprobación y automatizaciones revisados; correo de prueba recibido.
- [ ] Adjuntos: subida y descarga correctas; tamaño máximo acordado (`MAX_ATTACHMENT_MB`).
- [ ] Tablero Kanban se actualiza en tiempo real entre dos navegadores.

### Operación

- [ ] `scripts/backup.sh` programado en cron y **restauración probada** en un entorno aparte.
- [ ] Copias replicadas fuera del servidor (NAS/almacenamiento corporativo).
- [ ] `scripts/healthcheck.sh` integrado en la monitorización (código de salida).
- [ ] Logs de contenedores enviados al SIEM/colector corporativo (ver OPERATIONS.md).
- [ ] Procedimiento de rotación de secretos y contacto de guardia documentados.
