import { Suspense } from 'react';
import ReportsView from '@/components/reports/ReportsView';
import { Loading } from '@/components/ui';

export const metadata = { title: 'Reportes' };

export default function Page() {
  return (
    <Suspense fallback={<Loading label="Cargando reportes…" />}>
      <ReportsView />
    </Suspense>
  );
}
