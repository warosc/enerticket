/**
 * Módulo "flows": tipos y llamadas a la API de aprobaciones, automatizaciones y notificaciones.
 */
import { ApiError, apiFetch } from './api';
import type { IsoDateTime, StateCategory } from './types';

/** Campo que el backend añade a TicketOut (enriquecedor de aprobaciones). */
export interface TicketApprovalsSummary {
  pending: number;
}

declare module './types' {
  interface TicketOut {
    approvals?: TicketApprovalsSummary | null;
  }
}

// ---------- Aprobaciones ----------

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'cancelled';
export type StepStatus = 'waiting' | 'pending' | 'approved' | 'rejected' | 'cancelled';
export type DecisionValue = 'pending' | 'approved' | 'rejected' | 'skipped';
export type ApproverType = 'user' | 'role' | 'team' | 'team_lead';
export type StepRule = 'all' | 'any';

export interface ApprovalDecision {
  id: number;
  approver: { id: number; display_name: string };
  decision: DecisionValue;
  comment: string | null;
  is_override: boolean;
  decided_at: IsoDateTime | null;
}

export interface ApprovalStep {
  id: number;
  order: number;
  name: string;
  approver_type: ApproverType;
  approver_label: string;
  rule: StepRule;
  status: StepStatus;
  decided_at: IsoDateTime | null;
  decisions: ApprovalDecision[];
}

export interface ApprovalTicketRef {
  id: number;
  key: string;
  title: string;
  status: { key: string; name: string; category: StateCategory };
  priority: { key: string; name: string; rank: number };
  type: { key: string; name: string };
  department: { id: number; name: string };
}

export interface ApprovalRequest {
  id: number;
  status: ApprovalStatus;
  ticket: ApprovalTicketRef;
  transition: {
    id: number;
    name: string;
    from_state: { key: string; name: string } | null;
    to_state: { key: string; name: string } | null;
  };
  policy: { id: number; name: string } | null;
  reason: string | null;
  requested_by: { id: number; display_name: string } | null;
  created_at: IsoDateTime;
  decided_at: IsoDateTime | null;
  executed_at: IsoDateTime | null;
  execution_error: string | null;
  current_step: number | null;
  steps: ApprovalStep[];
  can_decide: boolean;
  can_cancel: boolean;
}

export interface ApprovalPage {
  items: ApprovalRequest[];
  total: number;
  page: number;
  page_size: number;
}

export interface StepSpec {
  name: string;
  approver_type: ApproverType;
  user_id?: number | null;
  role?: 'approver' | 'agent' | 'admin' | null;
  team_id?: number | null;
  scope?: 'department' | 'global';
  rule: StepRule;
}

export interface ApprovalTransitionRef {
  id: number;
  name: string;
  from_key: string | null;
  to_key: string | null;
  from_name: string | null;
  to_name: string | null;
  requires_approval: boolean;
}

export interface ApprovalPolicy {
  id: number;
  name: string;
  active: boolean;
  transition_id: number;
  transition: ApprovalTransitionRef;
  ticket_type_keys: string[];
  department_ids: number[];
  steps: StepSpec[];
  order: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface ApprovalPolicyInput {
  name: string;
  active: boolean;
  transition_id: number;
  ticket_type_keys: string[];
  department_ids: number[];
  steps: StepSpec[];
  order: number;
}

export interface TransitionApprovalInfo extends ApprovalTransitionRef {
  active_policies: number;
}

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: 'Pendiente',
  approved: 'Aprobada',
  rejected: 'Rechazada',
  cancelled: 'Cancelada',
};

export const APPROVAL_STATUS_BADGE: Record<ApprovalStatus, string> = {
  pending: 'badge-warning',
  approved: 'badge-success',
  rejected: 'badge-danger',
  cancelled: 'badge-neutral',
};

export const STEP_STATUS_LABELS: Record<StepStatus, string> = {
  waiting: 'En espera',
  pending: 'En curso',
  approved: 'Aprobada',
  rejected: 'Rechazada',
  cancelled: 'Cancelada',
};

export const DECISION_LABELS: Record<DecisionValue, string> = {
  pending: 'Pendiente',
  approved: 'Aprobó',
  rejected: 'Rechazó',
  skipped: 'No requerida',
};

export const APPROVER_TYPE_LABELS: Record<ApproverType, string> = {
  user: 'Usuario específico',
  role: 'Rol',
  team: 'Miembros de un equipo',
  team_lead: 'Líder del equipo',
};

export const RULE_LABELS: Record<StepRule, string> = {
  any: 'Basta un aprobador',
  all: 'Todos deben aprobar',
};

export function getPendingApprovals(all = false): Promise<ApprovalRequest[]> {
  return apiFetch<ApprovalRequest[]>('/approvals/pending', { query: { all: all || undefined } });
}

export function listApprovals(params: {
  scope?: 'mine' | 'all';
  status?: ApprovalStatus | '';
  page?: number;
  page_size?: number;
}): Promise<ApprovalPage> {
  return apiFetch<ApprovalPage>('/approvals', { query: { ...params } });
}

export function getTicketApprovals(ticketId: number, signal?: AbortSignal): Promise<ApprovalRequest[]> {
  return apiFetch<ApprovalRequest[]>(`/tickets/${ticketId}/approvals`, { signal });
}

export function decideApproval(
  requestId: number,
  decision: 'approve' | 'reject',
  comment?: string,
): Promise<ApprovalRequest> {
  return apiFetch<ApprovalRequest>(`/approvals/${requestId}/decide`, {
    method: 'POST',
    body: { decision, comment: comment?.trim() || undefined },
  });
}

export function cancelApproval(requestId: number, reason?: string): Promise<ApprovalRequest> {
  return apiFetch<ApprovalRequest>(`/approvals/${requestId}/cancel`, {
    method: 'POST',
    body: { reason: reason?.trim() || undefined },
  });
}

export const listPolicies = () => apiFetch<ApprovalPolicy[]>('/approvals/policies');
export const createPolicy = (body: ApprovalPolicyInput) =>
  apiFetch<ApprovalPolicy>('/approvals/policies', { method: 'POST', body });
export const updatePolicy = (id: number, body: Partial<ApprovalPolicyInput>) =>
  apiFetch<ApprovalPolicy>(`/approvals/policies/${id}`, { method: 'PATCH', body });
export const deletePolicy = (id: number) => apiFetch<void>(`/approvals/policies/${id}`, { method: 'DELETE' });
export const listApprovalTransitions = () => apiFetch<TransitionApprovalInfo[]>('/approvals/transitions');
export const setTransitionApproval = (id: number, requiresApproval: boolean) =>
  apiFetch<ApprovalTransitionRef>(`/approvals/transitions/${id}`, {
    method: 'PUT',
    body: { requires_approval: requiresApproval },
  });

/** Detalle del 409 `approval_required` que devuelve POST /tickets/{id}/transition. */
export interface ApprovalRequiredDetail {
  code: 'approval_required';
  message: string;
  approval_request_id: number;
  created: boolean;
}

export function approvalRequiredDetail(error: unknown): ApprovalRequiredDetail | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = (error.detail as { detail?: unknown } | undefined)?.detail;
  if (detail && typeof detail === 'object' && (detail as { code?: unknown }).code === 'approval_required') {
    return detail as ApprovalRequiredDetail;
  }
  return null;
}

// ---------- Automatizaciones ----------

export type ConditionOperator =
  | 'eq'
  | 'neq'
  | 'in'
  | 'not_in'
  | 'contains'
  | 'not_contains'
  | 'starts_with'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'is_empty'
  | 'is_not_empty';

export interface RuleCondition {
  field: string;
  op: ConditionOperator;
  value?: unknown;
}

export interface RuleConditions {
  match: 'all' | 'any';
  rules: RuleCondition[];
}

export type ActionType =
  | 'assign_user'
  | 'assign_team'
  | 'set_priority'
  | 'transition'
  | 'add_comment'
  | 'notify'
  | 'request_approval';

export interface RuleAction {
  type: ActionType;
  user_id?: number;
  team_id?: number;
  priority_key?: string;
  to_state?: string;
  body?: string;
  visibility?: 'public' | 'internal';
  targets?: string[];
  title?: string;
}

export interface AutomationRule {
  id: number;
  name: string;
  description: string;
  active: boolean;
  trigger: string;
  conditions: RuleConditions;
  actions: RuleAction[];
  order: number;
  stop_processing: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  last_execution_at: IsoDateTime | null;
  execution_count: number;
}

export interface AutomationRuleInput {
  name: string;
  description: string;
  active: boolean;
  trigger: string;
  conditions: RuleConditions;
  actions: RuleAction[];
  order: number;
  stop_processing: boolean;
}

export type FieldKind =
  | 'priority'
  | 'number'
  | 'state'
  | 'category'
  | 'ticket_type'
  | 'department'
  | 'team'
  | 'user'
  | 'text';

export interface AutomationMeta {
  triggers: { key: string; label: string }[];
  fields: { key: string; label: string; kind: FieldKind }[];
  operators: { key: ConditionOperator; label: string }[];
  actions: { key: ActionType; label: string }[];
  notify_targets: { key: string; label: string }[];
  max_depth: number;
  template_help: string;
}

export interface ActionResult {
  type: string;
  ok: boolean;
  detail: string;
}

export interface AutomationExecution {
  id: number;
  rule: { id: number; name: string };
  ticket: { id: number; key: string; title: string } | null;
  event_type: string;
  depth: number;
  status: 'success' | 'partial' | 'error';
  result: ActionResult[];
  error: string | null;
  created_at: IsoDateTime;
}

export interface ExecutionPage {
  items: AutomationExecution[];
  total: number;
  page: number;
  page_size: number;
}

export interface DryRunResult {
  matched: boolean;
  conditions: { field: string; op: string; value: unknown; actual: unknown; result: boolean }[];
  actions: {
    type: ActionType;
    description: string;
    rendered_title?: string | null;
    rendered_body?: string | null;
    recipient_ids?: number[];
  }[];
  ticket: { id: number; key: string; title: string };
}

export const EXECUTION_STATUS_LABELS: Record<AutomationExecution['status'], string> = {
  success: 'Correcta',
  partial: 'Parcial',
  error: 'Error',
};

export const EXECUTION_STATUS_BADGE: Record<AutomationExecution['status'], string> = {
  success: 'badge-success',
  partial: 'badge-warning',
  error: 'badge-danger',
};

export const getAutomationMeta = () => apiFetch<AutomationMeta>('/automations/meta');
export const listRules = () => apiFetch<AutomationRule[]>('/automations/rules');
export const createRule = (body: AutomationRuleInput) =>
  apiFetch<AutomationRule>('/automations/rules', { method: 'POST', body });
export const updateRule = (id: number, body: Partial<AutomationRuleInput>) =>
  apiFetch<AutomationRule>(`/automations/rules/${id}`, { method: 'PATCH', body });
export const deleteRule = (id: number) => apiFetch<void>(`/automations/rules/${id}`, { method: 'DELETE' });

export function listExecutions(params: {
  rule_id?: number;
  ticket_id?: number;
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<ExecutionPage> {
  return apiFetch<ExecutionPage>('/automations/executions', { query: { ...params } });
}

export function dryRunRule(
  ticketId: number,
  rule: AutomationRuleInput,
  eventData?: Record<string, unknown>,
): Promise<DryRunResult> {
  return apiFetch<DryRunResult>('/automations/dry-run', {
    method: 'POST',
    body: { ticket_id: ticketId, rule, event_data: eventData ?? {} },
  });
}

// ---------- Notificaciones ----------

export type NotificationKind =
  | 'ticket_assigned'
  | 'ticket_commented'
  | 'ticket_resolved'
  | 'approval_requested'
  | 'approval_decided'
  | 'sla_warning'
  | 'sla_breached'
  | 'automation'
  | (string & {});

export interface AppNotification {
  id: number;
  kind: NotificationKind;
  title: string;
  body: string;
  ticket: { id: number; key: string } | null;
  data: Record<string, unknown>;
  read: boolean;
  read_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface NotificationPage {
  items: AppNotification[];
  total: number;
  unread: number;
  page: number;
  page_size: number;
}

export const NOTIFICATION_KIND_LABELS: Record<string, string> = {
  ticket_assigned: 'Asignación',
  ticket_commented: 'Comentario',
  ticket_resolved: 'Resolución',
  approval_requested: 'Aprobación',
  approval_decided: 'Aprobación',
  sla_warning: 'SLA en riesgo',
  sla_breached: 'SLA incumplido',
  automation: 'Automatización',
};

export function listNotifications(
  params: { unread?: boolean; page?: number; page_size?: number },
  signal?: AbortSignal,
): Promise<NotificationPage> {
  return apiFetch<NotificationPage>('/notifications', {
    query: { unread: params.unread || undefined, page: params.page, page_size: params.page_size },
    signal,
  });
}

export const getUnreadCount = (signal?: AbortSignal) =>
  apiFetch<{ count: number }>('/notifications/unread-count', { signal });
export const markNotificationRead = (id: number) =>
  apiFetch<AppNotification>(`/notifications/${id}/read`, { method: 'POST' });
export const markAllNotificationsRead = () =>
  apiFetch<{ updated: number; unread: number }>('/notifications/read-all', { method: 'POST' });
