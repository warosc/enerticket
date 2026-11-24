/**
 * Tipos TypeScript de la API core (contrato de docs/FASE2_PLAN.md).
 *
 * `TicketOut` es una interfaz para que los módulos puedan añadir campos de sus
 * enriquecedores mediante declaration merging, por ejemplo en lib/sla.ts:
 *
 *   declare module '@/lib/types' {
 *     interface TicketOut { sla?: TicketSla | null }
 *   }
 */

export type Role = 'admin' | 'agent' | 'approver' | 'requester';

export const ROLES: readonly Role[] = ['admin', 'agent', 'approver', 'requester'];

export type StateCategory = 'todo' | 'in_progress' | 'waiting' | 'done';

export type CommentVisibility = 'public' | 'internal';

export type TeamMemberRole = 'member' | 'lead';

/** Fecha ISO 8601 tal como la serializa la API. */
export type IsoDateTime = string;

// ---------- Referencias embebidas en TicketOut ----------

export interface StatusRef {
  id: number;
  key: string;
  name: string;
  category: StateCategory;
}

export interface PriorityRef {
  id: number;
  key: string;
  name: string;
  rank: number;
}

export interface TicketTypeRef {
  id: number;
  key: string;
  name: string;
}

export interface DepartmentRef {
  id: number;
  name: string;
}

export interface TeamRef {
  id: number;
  name: string;
}

export interface ProjectRef {
  id: number;
  code: string;
  name: string;
}

export interface UserRef {
  id: number;
  display_name: string;
}

// ---------- Tickets ----------

export interface TicketOut {
  id: number;
  key: string;
  title: string;
  description: string;
  status: StatusRef;
  priority: PriorityRef;
  type: TicketTypeRef;
  department: DepartmentRef;
  team: TeamRef | null;
  project: ProjectRef | null;
  requester: UserRef;
  assignee: UserRef | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  first_response_at: IsoDateTime | null;
  resolved_at: IsoDateTime | null;
  closed_at: IsoDateTime | null;
}

/** Alias usado por los componentes de slot (props `{ticket: Ticket}`). */
export type Ticket = TicketOut;

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type TicketPage = Paginated<TicketOut>;

/** Parámetros de GET /tickets. */
export type TicketSort = 'created_at' | '-created_at' | 'updated_at' | '-updated_at' | 'priority' | '-priority';

/** Parámetros de GET /tickets (status, category y priority aceptan claves separadas por coma). */
export interface TicketQuery {
  status?: string;
  category?: string;
  priority?: string;
  department_id?: number;
  team_id?: number;
  assignee_id?: number;
  requester_id?: number;
  unassigned?: boolean;
  mine?: boolean;
  q?: string;
  sort?: TicketSort;
  page?: number;
  page_size?: number;
}

export interface TicketCreate {
  title: string;
  description: string;
  type_key?: string;
  priority_key?: string;
  department_id: number;
  project_id?: number | null;
}

export interface TicketUpdate {
  title?: string;
  description?: string;
  priority_key?: string;
  assignee_id?: number | null;
  team_id?: number | null;
  department_id?: number;
}

export interface TransitionRequest {
  to_state_key: string;
  comment?: string;
}

// ---------- Comentarios e historial ----------

export interface TicketComment {
  id: number;
  ticket_id: number;
  author: UserRef;
  body: string;
  visibility: CommentVisibility;
  created_at: IsoDateTime;
}

export interface CommentCreate {
  body: string;
  visibility: CommentVisibility;
}

export type JsonObject = { [key: string]: unknown };

/** Entrada de GET /tickets/{id}/history (`before`/`after` provienen de ticket_history). */
export interface TicketHistoryEntry {
  id: number;
  ticket_id: number;
  event_type: string;
  actor: UserRef | null;
  before: JsonObject | null;
  after: JsonObject | null;
  timestamp: IsoDateTime;
}

/** GET /stats — conteos de tickets visibles para el usuario. */
export interface TicketStats {
  total: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  by_category: Partial<Record<StateCategory, number>>;
}

// ---------- Identidad ----------

export interface MeTeam {
  id: number;
  name: string;
  department_id: number;
}

export interface Me {
  id: number;
  sub: string;
  username: string;
  display_name: string;
  email: string | null;
  roles: Role[];
  teams: MeTeam[];
  department_ids: number[];
}

export interface UserOut {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  roles: Role[];
  active: boolean;
}

// ---------- Catálogo ----------

export interface Department {
  id: number;
  name: string;
  parent_id: number | null;
}

export interface Team {
  id: number;
  department_id: number;
  name: string;
  external_group: string | null;
}

export interface Project {
  id: number;
  code: string;
  name: string;
  owner_id: number | null;
  team_id: number | null;
}

export interface Priority {
  id: number;
  key: string;
  name: string;
  rank: number;
}

export interface TicketType {
  id: number;
  key: string;
  name: string;
}

export interface WorkflowState {
  id: number;
  key: string;
  name: string;
  category: StateCategory;
  order: number;
  is_initial: boolean;
  is_final: boolean;
}

export interface WorkflowTransition {
  id: number;
  from_state_id: number;
  to_state_id: number;
  from_key: string;
  to_key: string;
  name: string;
  requires_approval: boolean;
}

export interface Workflow {
  states: WorkflowState[];
  transitions: WorkflowTransition[];
}

export interface Catalog {
  departments: Department[];
  teams: Team[];
  priorities: Priority[];
  ticket_types: TicketType[];
  projects: Project[];
  workflow: Workflow;
}

// ---------- Administración ----------

export interface DepartmentInput {
  name: string;
  parent_id?: number | null;
}

export interface TeamInput {
  department_id: number;
  name: string;
  external_group?: string | null;
}

export interface TeamMember {
  team_id: number;
  user_id: number;
  role: TeamMemberRole;
  username: string;
  display_name: string;
  email: string | null;
}

export interface TeamMemberInput {
  user_id: number;
  role: TeamMemberRole;
}

export interface ProjectInput {
  code: string;
  name: string;
  team_id?: number | null;
  owner_id?: number | null;
}

// ---------- Errores ----------

/** Cuerpo de error típico de FastAPI. */
export interface ApiErrorBody {
  detail?: string | Array<{ loc?: Array<string | number>; msg?: string; type?: string }> | JsonObject;
}
