import { Suspense } from 'react';
import ReportsPage from '@/components/reports/ReportsPage';
import { Loading } from '@/components/ui';

export const metadata = { title: 'Reportes' };

export default function Page() {
  return (
    <Suspense fallback={<Loading label="Cargando reportes…" />}>
      <ReportsPage />
    </Suspense>
  );
}
