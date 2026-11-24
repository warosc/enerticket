import { Suspense } from 'react';
import SlaPageView from '@/components/sla/SlaPageView';
import { Loading } from '@/components/ui';

export const metadata = { title: 'SLA' };

export default function Page() {
  return (
    <Suspense fallback={<Loading label="Cargando SLA…" />}>
      <SlaPageView />
    </Suspense>
  );
}
