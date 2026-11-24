/**
 * Content-Security-Policy con nonce por petición para las páginas del portal.
 *
 * - Next.js lee el nonce de la cabecera `Content-Security-Policy` de la petición y lo aplica a sus
 *   propios scripts; por eso el layout raíz fuerza renderizado dinámico (una página prerenderizada no
 *   llevaría el nonce y sus scripts quedarían bloqueados).
 * - Orígenes permitidos (fijados en el build, igual que el resto de NEXT_PUBLIC_*):
 *   API (`NEXT_PUBLIC_API_URL`) y su WebSocket (`ws(s)://` derivado o `NEXT_PUBLIC_WS_URL`),
 *   Keycloak (`NEXT_PUBLIC_KEYCLOAK_URL`: token, check-sso en iframe, logout) y orígenes extra
 *   opcionales separados por espacio o coma en `NEXT_PUBLIC_CSP_EXTRA_SOURCES` (p.ej. el endpoint
 *   público de S3/MinIO para previsualizar adjuntos).
 * - `/silent-check-sso.html` no pasa por aquí: tiene su propia política estática en next.config.mjs.
 */
import { NextResponse, type NextRequest } from 'next/server';

function originOf(value: string | undefined): string | null {
  if (!value) return null;
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function websocketOrigin(httpOrigin: string | null): string | null {
  if (!httpOrigin) return null;
  if (httpOrigin.startsWith('https://')) return `wss://${httpOrigin.slice('https://'.length)}`;
  if (httpOrigin.startsWith('http://')) return `ws://${httpOrigin.slice('http://'.length)}`;
  return null;
}

/** Acepta solo orígenes http(s)/ws(s) bien formados; descarta cualquier otra cosa (evita inyección). */
function extraSources(raw: string | undefined): string[] {
  return (raw ?? '')
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      try {
        const url = new URL(item);
        return ['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) ? url.origin : null;
      } catch {
        return null;
      }
    })
    .filter((item): item is string => item !== null);
}

const isProduction = process.env.NODE_ENV === 'production';
const apiOrigin = originOf(process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000');
const wsOrigin = originOf(process.env.NEXT_PUBLIC_WS_URL) ?? websocketOrigin(apiOrigin);
const keycloakOrigin =
  process.env.NEXT_PUBLIC_AUTH_MODE === 'dev'
    ? null
    : originOf(process.env.NEXT_PUBLIC_KEYCLOAK_URL || 'http://localhost:8081');
const extras = extraSources(process.env.NEXT_PUBLIC_CSP_EXTRA_SOURCES);
const allHttps = [apiOrigin, keycloakOrigin].every((o) => o === null || o.startsWith('https://'));

function unique(values: Array<string | null | false | undefined>): string {
  return Array.from(new Set(values.filter((v): v is string => Boolean(v)))).join(' ');
}

function buildContentSecurityPolicy(nonce: string): string {
  const directives: Record<string, string> = {
    'default-src': "'self'",
    // 'strict-dynamic' permite los chunks que cargan los scripts con nonce; 'unsafe-eval' solo en `next dev`.
    'script-src': unique(["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", !isProduction && "'unsafe-eval'"]),
    // React/Recharts usan atributos style en línea.
    'style-src': "'self' 'unsafe-inline'",
    'img-src': unique(["'self'", 'data:', 'blob:', ...extras]),
    'font-src': "'self' data:",
    'connect-src': unique([
      "'self'",
      apiOrigin,
      wsOrigin,
      keycloakOrigin,
      ...extras,
      !isProduction && 'ws:',
    ]),
    'frame-src': unique(["'self'", keycloakOrigin]),
    'media-src': unique(["'self'", 'blob:', ...extras]),
    'worker-src': "'self' blob:",
    'manifest-src': "'self'",
    'object-src': "'none'",
    'base-uri': "'self'",
    'form-action': unique(["'self'", keycloakOrigin]),
    'frame-ancestors': "'self'",
  };
  const policy = Object.entries(directives).map(([name, value]) => `${name} ${value}`);
  if (isProduction && allHttps) policy.push('upgrade-insecure-requests');
  return policy.join('; ');
}

function createNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function middleware(request: NextRequest) {
  const nonce = createNonce();
  const csp = buildContentSecurityPolicy(nonce);

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('Content-Security-Policy', csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Páginas HTML; se excluyen recursos estáticos, health check y la página de check-sso.
      source:
        '/((?!_next/static|_next/image|favicon.ico|icon.svg|robots.txt|healthz|silent-check-sso\\.html|silent-check-sso\\.js).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
};
