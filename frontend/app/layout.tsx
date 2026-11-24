import type { Metadata, Viewport } from 'next';
import { headers } from 'next/headers';
import type { ReactNode } from 'react';
import AppShell from '@/components/AppShell';
import { AuthProvider } from '@/lib/auth';
import './globals.css';

/**
 * Renderizado dinámico obligatorio: la Content-Security-Policy usa un nonce por petición
 * (middleware.ts) que Next.js solo puede aplicar a sus scripts si la página se genera en cada solicitud.
 */
export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: { default: 'Energuate Service Desk', template: '%s · Energuate Service Desk' },
  description: 'Portal corporativo de gestión de tickets de Energuate.',
  robots: { index: false, follow: false },
  icons: { icon: '/icon.svg' },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#0e2a4a',
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Leer las cabeceras también marca la ruta como dinámica (el nonce lo inyecta Next.js por sí mismo).
  await headers();
  return (
    <html lang="es">
      <body>
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
