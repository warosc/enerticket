/**
 * Lógica pura del tablero Kanban: filtros, transiciones válidas, orden y consultas.
 * Sin React para que sea fácil de razonar y reutilizar.
 */
import type { QueryParams } from '@/lib/api';
import type { Me, TicketOut, Workflow, WorkflowState, WorkflowTransition } from '@/lib/types';

/** Tickets cargados inicialmente por columna y tope (page_size máximo de la API). */
export const COLUMN_PAGE = 25;
export const COLUMN_MAX = 200;

export type AssigneeFilter = '' | 'me' | 'none' | `${number}`;

export interface KanbanFilters {
  department_id: string;
  team_id: string;
  priority: string;
  assignee: AssigneeFilter;
  mine: boolean;
  q: string;
  hideFinal: boolean;
}

export const EMPTY_FILTERS: KanbanFilters = {
  department_id: '',
  team_id: '',
  priority: '',
  assignee: '',
  mine: false,
  q: '',
  hideFinal: false,
};

const POSITIVE_INT = /^[1-9]\d{0,9}$/;

function intParam(value: string | null): string {
  return value && POSITIVE_INT.test(value) ? value : '';
}

export function parseFilters(params: URLSearchParams): KanbanFilters {
  const assignee = params.get('assignee') ?? '';
  return {
    department_id: intParam(params.get('department_id')),
    team_id: intParam(params.get('team_id')),
    priority: /^[a-z_]{1,40}$/.test(params.get('priority') ?? '') ? (params.get('priority') as string) : '',
    assignee: assignee === 'me' || assignee === 'none' || POSITIVE_INT.test(assignee) ? (assignee as AssigneeFilter) : '',
    mine: params.get('mine') === '1',
    q: (params.get('q') ?? '').slice(0, 200),
    hideFinal: params.get('hide_final') === '1',
  };
}

export function filtersToSearch(filters: KanbanFilters): string {
  const params = new URLSearchParams();
  if (filters.department_id) params.set('department_id', filters.department_id);
  if (filters.team_id) params.set('team_id', filters.team_id);
  if (filters.priority) params.set('priority', filters.priority);
  if (filters.assignee) params.set('assignee', filters.assignee);
  if (filters.mine) params.set('mine', '1');
  if (filters.q.trim()) params.set('q', filters.q.trim());
  if (filters.hideFinal) params.set('hide_final', '1');
  return params.toString();
}

export function activeFilterCount(filters: KanbanFilters): number {
  return [
    filters.department_id,
    filters.team_id,
    filters.priority,
    filters.assignee,
    filters.mine,
    filters.q.trim(),
    filters.hideFinal,
  ].filter(Boolean).length;
}

/** Parámetros de GET /tickets para una columna (estado) con los filtros activos. */
export function columnQuery(stateKey: string, filters: KanbanFilters, me: Me | null, limit: number): QueryParams {
  const query: QueryParams = {
    status: stateKey,
    sort: '-priority',
    page: 1,
    page_size: Math.min(Math.max(limit, 1), COLUMN_MAX),
  };
  if (filters.department_id) query.department_id = Number(filters.department_id);
  if (filters.team_id) query.team_id = Number(filters.team_id);
  if (filters.priority) query.priority = filters.priority;
  if (filters.assignee === 'none') query.unassigned = true;
  else if (filters.assignee === 'me' && me) query.assignee_id = me.id;
  else if (filters.assignee && filters.assignee !== 'me') query.assignee_id = Number(filters.assignee);
  if (filters.mine) query.mine = true;
  if (filters.q.trim()) query.q = filters.q.trim();
  return query;
}

/** Réplica en cliente de los filtros del servidor (para inserciones en vivo). */
export function matchesFilters(ticket: TicketOut, filters: KanbanFilters, me: Me | null): boolean {
  if (filters.department_id && ticket.department.id !== Number(filters.department_id)) return false;
  if (filters.team_id && ticket.team?.id !== Number(filters.team_id)) return false;
  if (filters.priority && ticket.priority.key !== filters.priority) return false;
  if (filters.assignee === 'none' && ticket.assignee) return false;
  if (filters.assignee === 'me' && (!me || ticket.assignee?.id !== me.id)) return false;
  if (filters.assignee && filters.assignee !== 'me' && filters.assignee !== 'none') {
    if (ticket.assignee?.id !== Number(filters.assignee)) return false;
  }
  if (filters.mine && (!me || (ticket.requester.id !== me.id && ticket.assignee?.id !== me.id))) return false;
  const term = filters.q.trim().toLowerCase();
  if (term) {
    const digits = term.toUpperCase().replace(/^ESD-/, '');
    const byId = /^\d+$/.test(digits) && ticket.id === Number(digits);
    const haystack = `${ticket.key} ${ticket.title} ${ticket.description}`.toLowerCase();
    if (!byId && !haystack.includes(term)) return false;
  }
  return true;
}

/** Mismo orden que `sort=-priority` en la API: prioridad (rank) desc y luego id desc. */
export function compareTickets(a: TicketOut, b: TicketOut): number {
  if (b.priority.rank !== a.priority.rank) return b.priority.rank - a.priority.rank;
  return b.id - a.id;
}

export function sortedStates(workflow: Workflow): WorkflowState[] {
  return [...workflow.states].sort((a, b) => a.order - b.order || a.id - b.id);
}

export interface MoveTarget {
  state: WorkflowState;
  transition: WorkflowTransition;
}

/** Destinos por estado de origen, precalculados para agentes y para solicitantes. */
export interface TargetIndex {
  agent: Map<number, MoveTarget[]>;
  requester: Map<number, MoveTarget[]>;
}

export const NO_TARGETS: readonly MoveTarget[] = Object.freeze([]) as readonly MoveTarget[];

export function buildTargetIndex(workflow: Workflow): TargetIndex {
  const states = new Map(workflow.states.map((s) => [s.id, s]));
  const agent = new Map<number, MoveTarget[]>();
  const requester = new Map<number, MoveTarget[]>();
  for (const state of workflow.states) {
    agent.set(state.id, []);
    requester.set(state.id, []);
  }
  for (const transition of workflow.transitions) {
    const from = states.get(transition.from_state_id);
    const to = states.get(transition.to_state_id);
    if (!from || !to || from.id === to.id) continue;
    const target = { state: to, transition };
    agent.get(from.id)?.push(target);
    // Solicitante: cerrar (destino final) o reabrir (origen "done") sus propios tickets.
    if (to.is_final || from.category === 'done') requester.get(from.id)?.push(target);
  }
  for (const list of [...agent.values(), ...requester.values()]) {
    list.sort((x, y) => x.state.order - y.state.order || x.state.id - y.state.id);
  }
  return { agent, requester };
}

/**
 * Destinos válidos para un ticket según el workflow y el rol (la API vuelve a validar):
 * - agent/admin: todas las transiciones desde el estado actual.
 * - solicitante: solo en sus tickets, cerrar (destino final) o reabrir (origen "done").
 */
export function targetsFor(
  index: TargetIndex,
  ticket: TicketOut,
  me: Me | null,
  isAgent: boolean,
): readonly MoveTarget[] {
  if (!me) return NO_TARGETS;
  if (isAgent) return index.agent.get(ticket.status.id) ?? NO_TARGETS;
  if (ticket.requester.id !== me.id) return NO_TARGETS;
  return index.requester.get(ticket.status.id) ?? NO_TARGETS;
}

export function allowedTargets(
  ticket: TicketOut,
  workflow: Workflow,
  me: Me | null,
  isAgent: boolean,
): readonly MoveTarget[] {
  return targetsFor(buildTargetIndex(workflow), ticket, me, isAgent);
}

/** Ticket con el estado reemplazado (actualización optimista al soltar). */
export function withStatus(ticket: TicketOut, state: WorkflowState): TicketOut {
  return {
    ...ticket,
    status: { id: state.id, key: state.key, name: state.name, category: state.category },
  };
}
