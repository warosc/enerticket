import { Suspense } from 'react';
import KanbanBoard from '@/components/kanban/KanbanBoard';
import { Loading } from '@/components/ui';

export const metadata = { title: 'Kanban' };

export default function KanbanPage() {
  return (
    <Suspense fallback={<Loading label="Cargando tablero…" />}>
      <KanbanBoard />
    </Suspense>
  );
}
