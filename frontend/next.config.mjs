/**
 * Configuración de Next.js para producción (salida standalone para la imagen Docker).
 *
 * Cabeceras de seguridad:
 * - La Content-Security-Policy de las páginas se genera por petición (con nonce) en middleware.ts.
 * - Aquí van las cabeceras estáticas comunes y la CSP fija de /silent-check-sso.html, la página que
 *   keycloak-js carga en un iframe propio para el check-sso silencioso.
 * - HSTS debe configurarlo el proxy inverso que termina TLS (no se fuerza aquí para no romper
 *   despliegues de laboratorio en http://localhost).
 */

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
  {
    key: 'Permissions-Policy',
    value:
      'camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=(), browsing-topics=()',
  },
  { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
  { key: 'X-DNS-Prefetch-Control', value: 'off' },
];

const silentCheckSsoCsp = [
  "default-src 'none'",
  "script-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'self'",
].join('; ');

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  async headers() {
    return [
      { source: '/:path*', headers: securityHeaders },
      {
        source: '/silent-check-sso.html',
        headers: [
          { key: 'Content-Security-Policy', value: silentCheckSsoCsp },
          { key: 'Cache-Control', value: 'no-store' },
        ],
      },
      {
        source: '/healthz',
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      },
    ];
  },
};

export default nextConfig;
