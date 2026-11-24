/**
 * Reglas de permisos de tickets del lado del cliente. Replican backend/app/core/permissions.py solo
 * para decidir qué acciones mostrar; la API siempre vuelve a validarlas (nunca se confía en el frontend).
 */
import type { Catalog, Me, TicketOut, Workflow, WorkflowTransition } from './types';

type TicketScope = Pick<TicketOut, 'assignee' | 'team' | 'department' | 'requester'>;
type MeScope = Pick<Me, 'id' | 'roles' | 'teams' | 'department_ids'>;

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

/**
 * ¿La transición resuelve el ticket? (entra en un estado «done» desde uno que no lo es).
 * Al resolver la API exige la solución (`resolution_note`) y, a quien atiende, la categoría.
 */
export function isResolvingTransition(
  workflow: Workflow,
  ticket: Pick<TicketOut, 'status'>,
  transition: Pick<WorkflowTransition, 'to_state_id'>,
): boolean {
  const target = workflow.states.find((s) => s.id === transition.to_state_id);
  const from = workflow.states.find((s) => s.id === ticket.status.id);
  return target?.category === 'done' && (from?.category ?? ticket.status.category) !== 'done';
}

/**
 * ¿Hay que clasificar (categoría) antes de resolver? Solo para quien puede editar el ticket y si el
 * departamento tiene categorías activas **que admiten el tipo del ticket** (como la API: si ninguna lo
 * admite no se puede clasificar). El solicitante que cierra su ticket solo indica la solución.
 */
export function needsCategoryToResolve(
  me: MeScope,
  ticket: TicketScope & Pick<TicketOut, 'category' | 'type'>,
  catalog: Pick<Catalog, 'categories'> | null,
): boolean {
  if (ticket.category || !catalog) return false;
  if (!canEditTicket(me, ticket)) return false;
  const typeKey = ticket.type?.key;
  return catalog.categories.some(
    (c) =>
      c.department_id === ticket.department.id &&
      (!typeKey || c.type_keys.length === 0 || c.type_keys.includes(typeKey)),
  );
}

/**
 * ¿Puede trasladar el ticket a una orden de trabajo? Requiere poder editarlo (como la API), que el tipo
 * «Orden de trabajo» esté activo y que el ticket no sea ya una orden de trabajo.
 */
export function canCreateWorkOrder(
  me: MeScope,
  ticket: TicketScope & Pick<TicketOut, 'type'>,
  catalog: Pick<Catalog, 'ticket_types'> | null,
): boolean {
  if (!catalog || ticket.type.key === 'work_order') return false;
  if (!catalog.ticket_types.some((t) => t.key === 'work_order')) return false;
  return canEditTicket(me, ticket);
}
