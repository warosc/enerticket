/** Health check del contenedor web (HEALTHCHECK del Dockerfile y orquestadores). Sin datos sensibles. */
export const dynamic = 'force-dynamic';

export function GET(): Response {
  return Response.json(
    { status: 'ok', service: 'web' },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
