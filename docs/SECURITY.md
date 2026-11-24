# Seguridad

## Alcance y activos

| Activo | Dónde vive | Sensibilidad |
|---|---|---|
| Tickets, comentarios (incl. internos), historial | PostgreSQL `servicedesk` | Interna/confidencial (incidentes de seguridad del SOC, cambios de red) |
| Adjuntos | MinIO (bucket privado) | Confidencial (evidencias, configuraciones, capturas) |
| Identidades, sesiones, claves de firma | Keycloak + PostgreSQL `keycloak` | Crítica |
| Credencial de la cuenta de servicio de AD | `.env` del servidor → configuración LDAP de Keycloak | Crítica |
| Secretos de servicio (BD, Redis, MinIO, SMTP) | `.env` del servidor | Alta |
| Copias de seguridad | `BACKUP_DIR` y su réplica | Igual que los datos originales |

## Modelo de amenazas (resumen STRIDE)

| Amenaza | Vector | Controles |
|---|---|---|
| **Suplantación** de usuario | Robo de credenciales, fuerza bruta, token falsificado | Login delegado en Keycloak + AD; protección contra fuerza bruta (5 fallos → bloqueo creciente hasta 15 min); PKCE S256 obligatorio; sin *implicit* ni *password grant*; la API valida firma RS256 (JWKS), `iss`, `aud=servicedesk-api`, `exp`. `AUTH_MODE=dev` imposible en producción (fijado a `oidc`). |
| Suplantación de origen (IP) | Cabeceras `X-Forwarded-*` falsas | Solo se confía en la IP fija de Caddy (`FORWARDED_ALLOW_IPS`, `KC_PROXY_TRUSTED_ADDRESSES`); Caddy sobrescribe `X-Forwarded-For`. |
| **Manipulación** de datos | Cambios no autorizados vía API | Autorización en el backend en cada endpoint (`require_roles`, `visible_tickets_filter`, `can_edit_ticket`); el frontend nunca es fuente de verdad. Historial (`ticket_history`) de cada cambio. Validación de transiciones y *guards* (aprobaciones). |
| **Repudio** | Negar una acción | `ticket_history` con actor y marca de tiempo; eventos de login y de administración de Keycloak (30 días); logs de acceso de Caddy en JSON. |
| **Divulgación** | Ver tickets de otra área, comentarios internos, adjuntos ajenos | Visibilidad por área/equipo/rol aplicada en SQL; comentarios `internal` solo admin y agentes que atienden el ticket (área o asignado), nunca por ser solicitante o aprobador; adjuntos servidos por la API tras verificar permisos (MinIO nunca expuesto); TLS en todo el tráfico externo; LDAPS/StartTLS hacia el AD; tokens redactados en logs. |
| Divulgación por WebSocket | Recibir eventos de tickets no visibles | El servidor filtra cada evento con las mismas reglas de visibilidad (`can_view_event`). |
| **Denegación de servicio** | Inundación de peticiones, subidas grandes | Límite de cuerpo en Caddy (`MAX_REQUEST_BODY`) y en la API (`MAX_ATTACHMENT_MB`); límites de CPU/RAM por contenedor; reinicio automático; Keycloak con bloqueo por fuerza bruta. |
| **Elevación de privilegios** | Auto-asignarse un rol | Roles solo desde Keycloak (grupos AD → `KC_GROUP_ROLE_MAP`); la API ignora roles desconocidos; la consola de Keycloak y el realm `master` bloqueados por IP; contenedores sin root (`no-new-privileges`). |
| Movimiento lateral | Compromiso de un contenedor | Red `backend` interna sin salida; BD/Redis/MinIO sin puertos publicados; credenciales distintas por servicio; cuenta S3 de la API limitada a un bucket; Redis con contraseña. |
| Cadena de suministro | Imágenes o dependencias comprometidas | Versiones fijadas (`requirements.txt`, `package-lock.json`, imágenes por tag/digest); CI sin secretos; `npm ci`/`pip` con lockfiles. |

## Controles implementados

### Identidad y sesión (Keycloak, realm `energuate`)
- Cliente `servicedesk-web` público con Authorization Code + PKCE `S256`; *direct access grants*,
  *implicit* y *service accounts* desactivados (y re-forzados por `keycloak-config` en cada arranque).
- Redirect URIs y web origins restringidos a `https://APP_DOMAIN/*`.
- Access token 5 min; inactividad SSO 30 min; sesión máxima 10 h; rotación de refresh tokens
  (`revokeRefreshToken`, sin reutilización).
- Sin tokens offline: `offline_access` no está en los roles por defecto ni como scope del portal y
  las sesiones offline duran como mucho 7 días (`keycloak-config` lo re-fuerza en cada arranque,
  también en realms importados antes).
- Política de contraseñas locales: 12+ caracteres, distinta de usuario y correo, historial de 5
  (las cuentas de AD siguen la política del dominio).
- Fuerza bruta: 5 fallos → espera incremental de 60 s hasta 15 min.
- Eventos de login y administración habilitados (retención 30 días).
- Federación AD **solo lectura**, cifrada (LDAPS/StartTLS obligatorio en producción) y validando el
  certificado con la CA corporativa (sin opción de "confiar en todo").
- Usuarios de laboratorio deshabilitados a la fuerza en producción.

### API
- JWT validado (firma, emisor, audiencia, expiración) con JWKS en caché; claims mínimos. Antes de
  consultar el JWKS se rechazan algoritmos no permitidos y `kid` con formato inválido; un `kid`
  desconocido fuerza como mucho un refresco cada `OIDC_JWKS_MIN_REFRESH_SECONDS` (60 s) y tras un
  fallo de Keycloak se responde 503 sin reintentar durante 5 s (sin amplificación hacia Keycloak).
  Los valores del cliente se registran escapados (`%r`, truncados).
- Comentarios internos: solo admin y agentes que atienden el ticket (equipo/departamento del ticket
  o asignado), en API, historial y WebSocket.
- Aprobaciones: segregación de funciones también para administradores (nadie decide su propia
  solicitud); un aprobador ve el ticket solo mientras su etapa está activa o si ya decidió; una
  etapa por departamento sin aprobadores recurre a los administradores, no a toda la empresa.
- Límites anti-abuso: prioridad máxima para solicitantes (`REQUESTER_MAX_PRIORITY=high`), tickets
  por hora (`TICKETS_PER_USER_HOUR`), cuota diaria de adjuntos por usuario
  (`ATTACHMENT_USER_DAILY_MB` / `_COUNT`, 429), WebSocket con tramas de 16 KB como máximo
  (`--ws-max-size`), 8 conexiones por usuario y proceso y 60 mensajes entrantes por minuto.
- Cuentas sin inicio de sesión en `ACCOUNT_STALE_DAYS` (45) días no reciben notificaciones ni se
  designan como aprobadores o asignados (sus roles AD solo se sincronizan al iniciar sesión).
- Autorización por rol y por área en el servidor; consultas filtradas en SQL.
- Provisión JIT de usuarios y sincronización de equipos desde los grupos del token.
- Errores de integridad devuelven 409 sin detalles internos.
- Imagen sin root (UID 10001), dependencias fijadas (Starlette fijada ≥ 0.49.1: CVE-2025-54121 y
  CVE-2025-62727) y auditadas con `pip-audit` en la CI.
- `ENVIRONMENT=production` rechaza al arrancar valores de plantilla (`replace-...`) o triviales en
  `DATABASE_URL`, `REDIS_URL`, `S3_*` y `SMTP_*`.

### Red y transporte
- Único punto de entrada: Caddy (80→443). TLS 1.2/1.3, HSTS 1 año, HTTP/2 y HTTP/3.
- Cabeceras: `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`,
  `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `X-Frame-Options` y una
  `Content-Security-Policy` por defecto (el portal envía la suya con *nonce* por petición y Keycloak
  también la suya); se eliminan `Server` y `X-Powered-By`.
- Consola de Keycloak y realm `master` accesibles solo desde `ADMIN_ALLOWED_CIDRS`.
- Health y métricas de Keycloak solo en el puerto de gestión interno (9000).
- Redes Docker: `edge` (proxy), `backend` (interna, sin salida), `egress` (AD/SMTP).
- Contenedores: `no-new-privileges` en todos; `cap_drop: [ALL]` salvo bases de datos (Caddy solo
  conserva `NET_BIND_SERVICE`); `api`, `worker`, `beat` y `web` con raíz de solo lectura y `tmpfs`.
- Servicio `preflight` (`deploy/preflight.sh`): ningún servicio arranca si una credencial conserva el
  valor `replace-...`, es trivial o mide menos de 16 caracteres, o si `APP_DOMAIN`/`SMTP_FROM` no son
  válidos.

### Datos
- Bucket privado (`mc anonymous set none`), cuenta de servicio con política mínima.
- Copias con permisos `700`, verificadas (SHA-256, `pg_restore --list`) y con retención.
- Secretos solo en `.env` (ignorado por git, `chmod 600`); plantillas con `replace-...`.

## Checklist de hardening

### Servidor
- [ ] SO actualizado y con parches automáticos de seguridad; Docker Engine actualizado.
- [ ] SSH solo con llave, sin root, restringido a la red de administración; `fail2ban` o equivalente.
- [ ] Firewall del host: entrantes 80/443 (+22 restringido); salientes solo DC, SMTP, DNS, NTP y, si
      aplica, registro de imágenes / ACME.
- [ ] Usuarios del grupo `docker` = administradores (equivale a root): lista mínima y nominal.
- [ ] Disco cifrado (LUKS) o cifrado del almacenamiento subyacente, incluyendo `BACKUP_DIR`.
- [ ] `/etc/docker/daemon.json` con `"live-restore": true` y el driver de logs corporativo.
- [ ] Antivirus/EDR corporativo excluyendo `/var/lib/docker` de escaneo en tiempo real (rendimiento),
      pero escaneando los adjuntos si la política lo exige.

### Aplicación y secretos
- [ ] Ningún `replace-...` en `.env`; secretos de 32+ caracteres aleatorios y distintos entre sí.
- [ ] `.env` con `chmod 600`, propietario el usuario de operación; copia en el gestor de secretos.
- [ ] `ADMIN_ALLOWED_CIDRS` limitado a la VLAN/equipos de TI.
- [ ] Cuenta bootstrap de Keycloak eliminada; administradores nominales con OTP (realm `master`).
- [ ] Opcional: OTP obligatorio para los roles `admin` del realm `energuate` (flujo *Conditional OTP*).
- [ ] SMTP con TLS (`SMTP_STARTTLS=true` o puerto 465) y remitente autorizado (SPF/DKIM del dominio).
- [ ] Revisión trimestral de miembros de `GG-SD-Admins`, `GG-SD-Agentes` y `GG-SD-Aprobadores`.
- [ ] Rotación anual de secretos (ver [OPERATIONS.md](OPERATIONS.md#rotación-de-secretos)).

### Monitoreo y respuesta
- [ ] Logs de Caddy, Keycloak y API enviados al SIEM; alertas por ráfagas de `LOGIN_ERROR`, 401/403
      masivos y cambios en la consola de administración (eventos *admin*).
- [ ] `scripts/healthcheck.sh` en la monitorización; alertas de certificado y de copias.
- [ ] Procedimiento ante compromiso: rotar claves de firma de Keycloak (invalida todos los tokens),
      cerrar sesiones (*Realm → Sessions → Sign out all*), rotar secretos de `.env`, revisar
      `ticket_history` y eventos de Keycloak.

### Antes de cada versión
- [ ] CI en verde (pruebas backend, `tsc`, `next build`, validación de compose/Caddy, ShellCheck).
- [ ] Revisión de dependencias con avisos de seguridad (`pip-audit`, `npm audit`) y de las imágenes
      base (`docker scout cves` / Trivy).
- [ ] Copia de seguridad previa y plan de vuelta atrás.

## Riesgos aceptados / pendientes

- **CSP del proxy con `'unsafe-inline'`**: el portal envía su propia CSP con *nonce* por petición
  (`frontend/middleware.ts`) y Keycloak la suya; la CSP genérica de Caddy solo se aplica a respuestas
  que no traen ninguna (p. ej. errores del proxy) y por eso es más permisiva.
- **JWT del WebSocket en la query** (`/ws?token=`): necesario porque el navegador no permite
  cabeceras en WebSocket. Se redacta en los logs de Caddy; el token dura 5 min.
- **Un solo servidor** (sin alta disponibilidad): mitigado con copias diarias y restauración probada.
- **Sin límite de frecuencia en el proxy**: la imagen oficial de Caddy no incluye `rate_limit` (requiere
  un build con el plugin `caddy-ratelimit`). Los vectores caros (JWKS, adjuntos, creación de tickets,
  WebSocket) tienen límites en la API; si se expone a Internet, añada límite por IP en el balanceador
  o WAF corporativo.
- **Roles por inicio de sesión**: `users.roles` y los equipos se actualizan al iniciar sesión; quien
  sale de un grupo AD y no vuelve a entrar conserva el último estado hasta `ACCOUNT_STALE_DAYS`
  (no recibe avisos ni se le asignan tickets, pero su fila no se desactiva sola). Deshabilite la
  cuenta en el portal (*Admin → usuarios*) o en el AD en las bajas.
- **Modo `dev`** de autenticación existe en el código para laboratorio; en producción queda anulado
  por `docker-compose.prod.yml` (y la API debe rechazarlo con `ENVIRONMENT=production`).

## Reporte de vulnerabilidades

Reporte cualquier hallazgo de seguridad de forma privada al equipo de Ciberseguridad / SOC de
Energuate (no mediante issues públicos). Incluya pasos de reproducción, impacto y versión (tag).
