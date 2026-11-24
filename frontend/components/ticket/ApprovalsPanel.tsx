'use client';

/**
 * Panel de aprobaciones en el detalle del ticket (módulo flows).
 *
 * Muestra las solicitudes de aprobación del ticket (pendientes primero) y permite a los
 * aprobadores decidir. Tras decidir o cancelar invoca `onChange` para que la página recargue
 * el ticket (la aprobación final aplica la transición automáticamente). Se actualiza solo con
 * eventos en tiempo real del ticket y cuando cambia su estado o su resumen de aprobaciones.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import ApprovalCard from '@/components/approvals/ApprovalCard';
import { ErrorAlert, Spinner } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { getTicketApprovals, type ApprovalRequest } from '@/lib/flows';
import { RESYNC_EVENT, useTicketEvents, type TicketEvent } from '@/lib/realtime';
import type { Ticket } from '@/lib/types';

const RELOAD_EVENTS: string[] = [
  'approval.requested',
  'approval.decided',
  'approval.cancelled',
  'ticket.transitioned',
  RESYNC_EVENT,
];
const HISTORY_COLLAPSED = 2;

export default function ApprovalsPanel({ ticket, onChange }: { ticket: Ticket; onChange: () => void }) {
  const [requests, setRequests] = useState<ApprovalRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    try {
      const result = await getTicketApprovals(ticket.id, controller.signal);
      if (controller.signal.aborted) return;
      setRequests(result);
      setError(null);
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(errorMessage(e));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [ticket.id]);

  // Recarga al montar y cuando cambian el estado o el resumen de aprobaciones del ticket.
  const pendingCount = ticket.approvals?.pending ?? null;
  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load, ticket.status.id, ticket.updated_at, pendingCount]);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onEvent = useCallback(
    (event: TicketEvent) => {
      if (event.ticket_id !== ticket.id || !RELOAD_EVENTS.includes(event.type)) return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void load(), 300);
    },
    [load, ticket.id],
  );
  useTicketEvents(onEvent, { ticketId: ticket.id });
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function handleChanged(updated: ApprovalRequest) {
    setRequests((prev) => (prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : [updated]));
    onChange();
  }

  // Sin solicitudes (o aún sin datos): no ocupa espacio en la página.
  if (!error && (!requests || requests.length === 0)) return null;

  const pending = (requests ?? []).filter((r) => r.status === 'pending');
  const history = (requests ?? []).filter((r) => r.status !== 'pending');
  const visibleHistory = showAll ? history : history.slice(0, HISTORY_COLLAPSED);
  const actionable = pending.some((r) => r.can_decide);

  return (
    <section className="panel stack" aria-labelledby="approvals-panel-title">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h2 className="panel-title" id="approvals-panel-title">
          Aprobaciones
        </h2>
        <div className="row-sm">
          {loading && <Spinner label="Actualizando aprobaciones" />}
          {pending.length > 0 && (
            <span className={`badge ${actionable ? 'badge-warning' : 'badge-info'}`}>
              {actionable ? 'Requiere su decisión' : `${pending.length} pendiente${pending.length > 1 ? 's' : ''}`}
            </span>
          )}
        </div>
      </div>

      <ErrorAlert error={error} onRetry={() => void load()} />

      {pending.map((request) => (
        <ApprovalCard key={request.id} request={request} onChanged={handleChanged} showTicket={false} />
      ))}

      {history.length > 0 && (
        <div className="stack-sm">
          <h3 className="text-sm muted" style={{ fontWeight: 600 }}>
            Historial de aprobaciones
          </h3>
          {visibleHistory.map((request) => (
            <ApprovalCard key={request.id} request={request} onChanged={handleChanged} showTicket={false} />
          ))}
          {history.length > HISTORY_COLLAPSED && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Ver menos' : `Ver ${history.length - HISTORY_COLLAPSED} más`}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
