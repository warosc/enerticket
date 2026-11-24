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

/** Referencia genérica `{id, name}` (categoría, subcategoría, proveedor, gerencia...). */
export interface IdNameRef {
  id: number;
  name: string;
}

/** Canal por el que llegó el ticket (Fase 3). */
export type TicketChannel = 'portal' | 'email' | 'phone' | 'other';

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
  // --- Catálogo de servicio (Fase 3) ---
  category: IdNameRef | null;
  subcategory: IdNameRef | null;
  provider: IdNameRef | null;
  channel: TicketChannel;
  /** Copiado del solicitante al crear el ticket. */
  vip: boolean;
  resolution_note: string | null;
  resolved_by: UserRef | null;
  requester_department: IdNameRef | null;
  /** Gerencia del departamento del solicitante. */
  gerencia: IdNameRef | null;
  parent_ticket_id: number | null;
  child_ticket_ids: number[];
  /** Minutos hábiles hasta la resolución (descuenta pausas del SLA). */
  business_resolution_minutes: number | null;
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
  category_id?: number;
  subcategory_id?: number;
  /** Claves de tipo separadas por coma. */
  type_key?: string;
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
  category_id?: number | null;
  subcategory_id?: number | null;
  /** Solo agentes/administradores (al solicitante la API le fija `portal`). */
  channel?: TicketChannel | null;
  /** Solo agentes/administradores. */
  provider_id?: number | null;
}

export interface TicketUpdate {
  title?: string;
  description?: string;
  priority_key?: string;
  assignee_id?: number | null;
  team_id?: number | null;
  department_id?: number;
  category_id?: number | null;
  subcategory_id?: number | null;
  provider_id?: number | null;
  channel?: TicketChannel;
  resolution_note?: string | null;
}

export interface TransitionRequest {
  to_state_key: string;
  comment?: string;
  /** Solución: obligatoria al resolver (pasar a un estado «done») si no estaba guardada. */
  resolution_note?: string;
}

/** POST /tickets/{id}/work-order: crea una orden de trabajo hija del ticket. */
export interface WorkOrderRequest {
  title?: string;
  description?: string;
  category_id?: number | null;
  subcategory_id?: number | null;
  /** Resuelve el ticket original con `resolution_note` (o «Trasladado a orden de trabajo #id»). */
  resolve_parent?: boolean;
  resolution_note?: string;
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
  // --- Perfil del AD (Fase 3) ---
  /** Departamento del usuario (atributo `department` del AD o ajuste manual). */
  department?: IdNameRef | null;
  gerencia?: IdNameRef | null;
  vip?: boolean;
  /** Gerencias cuyos tickets solicitados puede ver (solo lectura). */
  viewer_gerencia_ids?: number[];
  /** Equipos en los que es líder (programación de reportes). */
  lead_team_ids?: number[];
}

export interface UserOut {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  roles: Role[];
  active: boolean;
  /** Fase 3 (opcionales para no romper referencias construidas en el cliente). */
  department?: IdNameRef | null;
  vip?: boolean;
}

// ---------- Catálogo ----------

export interface Department {
  id: number;
  name: string;
  parent_id: number | null;
  gerencia_id: number | null;
  /** Departamento de atención: recibe tickets. */
  serves_tickets: boolean;
  active: boolean;
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

/** Categoría activa de /catalog (con sus subcategorías activas, ya ordenadas). */
export interface CatalogCategory {
  id: number;
  department_id: number;
  name: string;
  /** Tipos admitidos; vacía = todos. */
  type_keys: string[];
  subcategories: IdNameRef[];
}

export interface CatalogProvider {
  id: number;
  name: string;
  /** Departamentos donde aplica; vacía = todos. */
  department_ids: number[];
}

export interface ChannelOption {
  key: TicketChannel;
  label: string;
}

export interface Catalog {
  departments: Department[];
  teams: Team[];
  priorities: Priority[];
  /** Solo tipos activos. */
  ticket_types: TicketType[];
  projects: Project[];
  workflow: Workflow;
  categories: CatalogCategory[];
  providers: CatalogProvider[];
  channels: ChannelOption[];
  gerencias: IdNameRef[];
}

// ---------- Administración ----------

export interface DepartmentInput {
  name: string;
  parent_id?: number | null;
  gerencia_id?: number | null;
  serves_tickets?: boolean;
  active?: boolean;
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

// ---------- Catálogo de servicio (administración, Fase 3) ----------

export interface Gerencia {
  id: number;
  name: string;
  /** Grupo del AD cuyos miembros ven los tickets solicitados por la gerencia. */
  external_group: string | null;
  active: boolean;
  department_ids: number[];
}

export interface GerenciaInput {
  name: string;
  external_group?: string | null;
  active?: boolean;
}

export interface GerenciaViewer {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
}

/** Valor del atributo `department` del AD y su departamento (null = pendiente de asignar). */
export interface DepartmentAlias {
  id: number;
  normalized_value: string;
  raw_value: string;
  department_id: number | null;
  department: IdNameRef | null;
  pending: boolean;
  created_at: IsoDateTime;
}

export interface AdminSubcategory {
  id: number;
  name: string;
  active: boolean;
  sort_order: number;
}

export interface AdminCategory {
  id: number;
  department_id: number;
  department: IdNameRef;
  name: string;
  type_keys: string[];
  active: boolean;
  sort_order: number;
  subcategories: AdminSubcategory[];
}

export interface CategoryInput {
  department_id: number;
  name: string;
  type_keys: string[];
  sort_order?: number;
}

export interface CategoryPatch {
  name?: string;
  type_keys?: string[];
  active?: boolean;
  sort_order?: number;
}

export interface SubcategoryInput {
  name: string;
  sort_order?: number;
}

export interface SubcategoryPatch {
  name?: string;
  active?: boolean;
  sort_order?: number;
}

export interface Provider {
  id: number;
  name: string;
  department_ids: number[];
  active: boolean;
}

export interface ProviderInput {
  name: string;
  department_ids: number[];
  active?: boolean;
}

/** Respuesta de POST /admin/catalog/import. */
export interface CatalogImportResult {
  dry_run: boolean;
  created: { categories: number; subcategories: number; policies: number };
  updated: { policies: number };
  errors: Array<{ line: number; message: string }>;
}

export interface UserProfilePatch {
  vip?: boolean;
  department_id?: number | null;
}

// ---------- Errores ----------

/** Cuerpo de error típico de FastAPI. */
export interface ApiErrorBody {
  detail?: string | Array<{ loc?: Array<string | number>; msg?: string; type?: string }> | JsonObject;
}
