# syntax=docker/dockerfile:1
# Imagen de producción del portal web (Next.js standalone, usuario sin privilegios).
#
# Las variables NEXT_PUBLIC_* se incrustan en el bundle y en la Content-Security-Policy durante el
# build, por eso se reciben como build args (docker compose: build.args). Cambiarlas requiere
# reconstruir la imagen.
#   NEXT_PUBLIC_API_URL            URL pública de la API (la usa el navegador)
#   NEXT_PUBLIC_WS_URL             opcional: URL del WebSocket (p.ej. wss://dominio/ws); la CSP usa su origen
#   NEXT_PUBLIC_AUTH_MODE          oidc (producción) | dev (solo laboratorio)
#   NEXT_PUBLIC_KEYCLOAK_URL       URL pública de Keycloak
#   NEXT_PUBLIC_KEYCLOAK_REALM     realm (energuate)
#   NEXT_PUBLIC_KEYCLOAK_CLIENT_ID cliente público PKCE (servicedesk-web)
#   NEXT_PUBLIC_CSP_EXTRA_SOURCES  opcional: orígenes extra para la CSP separados por espacio
#                                  (p.ej. el endpoint público de S3/MinIO para previsualizar adjuntos)

ARG NODE_IMAGE=node:24-alpine

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_WS_URL=
ARG NEXT_PUBLIC_AUTH_MODE=oidc
ARG NEXT_PUBLIC_KEYCLOAK_URL=http://localhost:8081
ARG NEXT_PUBLIC_KEYCLOAK_REALM=energuate
ARG NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=servicedesk-web
ARG NEXT_PUBLIC_CSP_EXTRA_SOURCES=
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_WS_URL=${NEXT_PUBLIC_WS_URL} \
    NEXT_PUBLIC_AUTH_MODE=${NEXT_PUBLIC_AUTH_MODE} \
    NEXT_PUBLIC_KEYCLOAK_URL=${NEXT_PUBLIC_KEYCLOAK_URL} \
    NEXT_PUBLIC_KEYCLOAK_REALM=${NEXT_PUBLIC_KEYCLOAK_REALM} \
    NEXT_PUBLIC_KEYCLOAK_CLIENT_ID=${NEXT_PUBLIC_KEYCLOAK_CLIENT_ID} \
    NEXT_PUBLIC_CSP_EXTRA_SOURCES=${NEXT_PUBLIC_CSP_EXTRA_SOURCES} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Solo lo necesario para ejecutar: servidor standalone, estáticos y public/.
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
# /healthz es una ruta liviana sin autenticación ni CSP (app/healthz/route.ts).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
# Se recomienda `init: true` en docker compose para reenviar señales y recolectar procesos huérfanos.
CMD ["node", "server.js"]
