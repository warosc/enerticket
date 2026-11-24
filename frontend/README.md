# Portal web — Energuate Service Desk

Next.js 15 (App Router, salida `standalone`) + React 19. Toda la UI está en español.

## Variables (se fijan en el build)

| Variable | Uso |
|---|---|
| `NEXT_PUBLIC_API_URL` | URL pública de la API (por defecto `http://localhost:8000`). |
| `NEXT_PUBLIC_WS_URL` | Opcional. URL del WebSocket (p.ej. `wss://dominio/ws`); si falta se deriva de la API. La CSP usa su origen. |
| `NEXT_PUBLIC_AUTH_MODE` | `oidc` (producción) o `dev` (laboratorio, selector de identidad con cabeceras `X-Dev-*`). |
| `NEXT_PUBLIC_KEYCLOAK_URL` | URL pública de Keycloak. |
| `NEXT_PUBLIC_KEYCLOAK_REALM` | Realm (`energuate`). |
| `NEXT_PUBLIC_KEYCLOAK_CLIENT_ID` | Cliente público con PKCE S256 (`servicedesk-web`). |
| `NEXT_PUBLIC_CSP_EXTRA_SOURCES` | Opcional. Orígenes extra para la CSP separados por espacio (p.ej. el endpoint público de S3/MinIO). |

Como se incrustan en el bundle y en la Content-Security-Policy, cambiarlas requiere reconstruir la imagen (`docker compose build web`).

## Autenticación

- **OIDC** (`lib/auth.tsx`): keycloak-js con `check-sso` silencioso (`public/silent-check-sso.html`), PKCE S256 y redirección al login si no hay sesión. El token se refresca antes de vencer (temporizador, `onTokenExpired` y al volver a la pestaña). Ante un 401 de la API se fuerza un refresco y se repite la petición una vez; si la sesión terminó en Keycloak se vuelve al login (con protección contra bucles de redirección).
- **dev**: selector con los usuarios de demostración del seed (`demo.admin`, `ana.telecom`, `luis.soc`, `rosa.redes`, `marta.aprobadora`, `carlos.solicitante`, `sofia.solicitante`) o una identidad propia. Envía `X-Dev-User` (obligatorio), `X-Dev-Roles`, `X-Dev-Groups` y `X-Dev-Name`. Nunca usar en producción.

Los permisos se validan siempre en la API; el frontend solo decide qué acciones mostrar (`lib/ticketRules.ts` replica `core/permissions.py`).

## Seguridad HTTP

- `middleware.ts`: CSP por petición con nonce y `'strict-dynamic'`; `connect-src` admite la API, su WebSocket y Keycloak; `frame-src` admite Keycloak (check-sso). El layout raíz fuerza renderizado dinámico para que Next.js aplique el nonce.
- `next.config.mjs`: `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `X-Content-Type-Options`, `Cross-Origin-Opener-Policy`, y una CSP fija para `silent-check-sso.html`.
- HSTS y TLS se configuran en el proxy inverso.

## Salud del contenedor

`GET /healthz` → `{"status":"ok"}` (sin autenticación ni CSP). Lo usa el `HEALTHCHECK` del Dockerfile.

## Desarrollo

```bash
npm ci
NEXT_PUBLIC_AUTH_MODE=dev npm run dev   # http://localhost:3000
npx tsc --noEmit                        # verificación de tipos
```
