'use client';

/**
 * Eventos de tickets en tiempo real.
 *
 * STUB de la fundación: `useTicketEvents` todavía no se conecta a nada. El módulo
 * "realtime" reemplaza este archivo con la conexión WebSocket a `/ws` manteniendo
 * exactamente esta firma, de modo que las páginas que ya lo usan no cambian.
 */

/** Tipos de evento conocidos; los módulos pueden emitir otros (string libre). */
export type TicketEventType =
  | 'ticket.created'
  | 'ticket.updated'
  | 'ticket.assigned'
  | 'ticket.transitioned'
  | 'ticket.commented'
  | 'sla.warning'
  | 'sla.breached'
  | 'approval.requested'
  | 'approval.decided'
  | 'attachment.added'
  | 'notification.created'
  | (string & {});

/** Payload publicado en Redis `servicedesk.events` y reenviado por `/ws`. */
export interface TicketEvent {
  type: TicketEventType;
  ticket_id: number;
  ticket_key: string;
  actor_id: number | null;
  data: Record<string, unknown>;
  /** Fecha ISO 8601 del evento. */
  ts: string;
  department_id: number | null;
  team_id: number | null;
  requester_id: number | null;
  assignee_id: number | null;
}

/** Filtro opcional aplicado del lado del cliente. */
export interface TicketEventFilter {
  /** Solo eventos de este ticket. */
  ticketId?: number;
  /** Solo estos tipos de evento. */
  types?: TicketEventType[];
}

export type TicketEventHandler = (event: TicketEvent) => void;

/**
 * Se suscribe a los eventos de tickets visibles para el usuario actual.
 * El handler puede cambiar entre renders sin provocar una nueva suscripción.
 */
export function useTicketEvents(handler: TicketEventHandler, filter?: TicketEventFilter): void {
  // No-op hasta que el módulo realtime provea la conexión WebSocket.
  void handler;
  void filter;
}
