/**
 * Reglas de permisos de tickets del lado del cliente. Replican backend/app/core/permissions.py solo
 * para decidir qué acciones mostrar; la API siempre vuelve a validarlas (nunca se confía en el frontend).
 */
import type { Me, TicketOut, Workflow, WorkflowTransition } from './types';

type TicketScope = Pick<TicketOut, 'assignee' | 'team' | 'department' | 'requester'>;

/** ¿Puede editar/transicionar libremente? admin todo; agent los de su área o asignados a él. */
export function canEditTicket(me: Pick<Me, 'id' | 'roles' | 'teams' | 'department_ids'>, ticket: TicketScope): boolean {
  if (me.roles.includes('admin')) return true;
  if (!me.roles.includes('agent')) return false;
  if (ticket.assignee?.id === me.id) return true;
  if (ticket.team && me.teams.some((t) => t.id === ticket.team?.id)) return true;
  return me.department_ids.includes(ticket.department.id);
}

/**
 * Transiciones que el usuario puede intentar desde el estado actual del ticket:
 * - quien puede editar: todas las del flujo desde el estado actual;
 * - el solicitante: cerrar (destino final) o reabrir (desde un estado "done") sus propios tickets.
 * Los guards (p.ej. aprobaciones) se evalúan en la API y pueden bloquear con 409.
 */
export function transitionsFor(
  workflow: Workflow,
  ticket: TicketScope & Pick<TicketOut, 'status'>,
  me: Pick<Me, 'id' | 'roles' | 'teams' | 'department_ids'>,
): WorkflowTransition[] {
  const states = new Map(workflow.states.map((s) => [s.id, s]));
  const from = states.get(ticket.status.id);
  const editable = canEditTicket(me, ticket);
  const isRequester = ticket.requester.id === me.id;
  return workflow.transitions
    .filter((tr) => tr.from_state_id === ticket.status.id)
    .filter((tr) => {
      if (editable) return true;
      if (!isRequester) return false;
      const to = states.get(tr.to_state_id);
      if (!to) return false;
      const closing = to.is_final;
      const reopening = (from?.category ?? ticket.status.category) === 'done' && to.category !== 'done';
      return closing || reopening;
    })
    .sort((a, b) => (states.get(a.to_state_id)?.order ?? 0) - (states.get(b.to_state_id)?.order ?? 0));
}
