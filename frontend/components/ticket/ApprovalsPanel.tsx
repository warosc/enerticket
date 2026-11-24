'use client';

import type { Ticket } from '@/lib/types';

/**
 * Panel de aprobaciones en el detalle del ticket. `onChange` debe invocarse tras
 * una decisión para que la página recargue el ticket y su historial.
 * STUB de la fundación: no renderiza nada. Lo implementa el módulo "flows".
 */
export default function ApprovalsPanel({ ticket, onChange }: { ticket: Ticket; onChange: () => void }): null {
  void ticket;
  void onChange;
  return null;
}
