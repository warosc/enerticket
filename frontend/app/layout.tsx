import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';
import AppShell from '@/components/AppShell';
import { AuthProvider } from '@/lib/auth';
import './globals.css';

export const metadata: Metadata = {
  title: { default: 'Energuate Service Desk', template: '%s · Energuate Service Desk' },
  description: 'Portal corporativo de gestión de tickets de Energuate.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#0e2a4a',
};

export default function RootLayout({ children }: { children: ReactNode }) {
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
