/**
 * Módulo SLA: tipos de la API, llamadas y utilidades de presentación.
 *
 * El backend añade a cada `TicketOut` el campo `sla` (enriquecedor). Se declara aquí
 * mediante declaration merging para que `ticket.sla` quede tipado en toda la app.
 */
import { apiFetch } from './api';
import type { IsoDateTime, TicketOut } from './types';

// ---------- Tipos ----------

export type SlaMetric = 'first_response' | 'resolution';
export type SlaStatus = 'running' | 'paused' | 'met' | 'breached' | 'cancelled';

export const SLA_METRICS: readonly SlaMetric[] = ['first_response', 'resolution'];

/** Estado de una métrica (campo `ticket.sla.first_response` / `ticket.sla.resolution`). */
export interface SlaMetricInfo {
  instance_id: number;
  metric: SlaMetric;
  label: string;
  status: SlaStatus;
  policy_id: number;
  target_minutes: number;
  warning_threshold_pct: number;
  started_at: IsoDateTime;
  /** null mientras está pausada (se recalcula al reanudar). */
  target_at: IsoDateTime | null;
  paused: boolean;
  paused_at: IsoDateTime | null;
  paused_business_seconds: number;
  stopped_at: IsoDateTime | null;
  /** Segundos hábiles restantes al momento del cálculo (negativo = excedido). */
  remaining_business_seconds: number | null;
  elapsed_business_seconds: number | null;
  breached: boolean;
  breached_at: IsoDateTime | null;
  warned: boolean;
  warned_at: IsoDateTime | null;
  at_risk: boolean;
  /** true mientras el reloj no se detuvo (cumplida/vencida-y-cerrada/cancelada). */
  open: boolean;
}

export interface SlaPolicyRef {
  id: number;
  name: string;
}

export interface TicketSla {
  policy: SlaPolicyRef | null;
  first_response: SlaMetricInfo | null;
  resolution: SlaMetricInfo | null;
}

declare module './types' {
  interface TicketOut {
    /** Añadido por el módulo SLA; null si no aplica ninguna política. */
    sla?: TicketSla | null;
  }
}

export interface SlaInstanceDetail extends SlaMetricInfo {
  policy: SlaPolicyRef | null;
  cancelled: boolean;
}

export type SlaEventType =
  | 'started'
  | 'paused'
  | 'resumed'
  | 'met'
  | 'breached'
  | 'warning'
  | 'stopped'
  | 'reopened'
  | 'cancelled'
  | (string & {});

export interface SlaEvent {
  id: number;
  instance_id: number;
  metric: SlaMetric;
  type: SlaEventType;
  at: IsoDateTime;
  data: Record<string, unknown>;
}

/** GET /tickets/{id}/sla */
export interface TicketSlaDetail extends TicketSla {
  ticket_id: number;
  ticket_key: string;
  computed_at: IsoDateTime;
  instances: SlaInstanceDetail[];
  events: SlaEvent[];
}

export interface SlaWindow {
  /** 0 = lunes ... 6 = domingo. */
  weekday: number;
  start: string;
  end: string;
}

export interface SlaCalendar {
  id: number;
  name: string;
  description: string;
  timezone: string;
  windows: SlaWindow[];
  is_24x7: boolean;
  weekly_business_minutes: number;
  holiday_count: number;
  policy_count: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface SlaCalendarInput {
  name: string;
  description?: string;
  timezone?: string;
  windows: SlaWindow[];
}

export interface SlaHoliday {
  id: number;
  calendar_id: number;
  /** YYYY-MM-DD */
  date: string;
  name: string;
  recurring: boolean;
}

export interface SlaHolidayInput {
  date: string;
  name: string;
  recurring: boolean;
}

export type PauseCategory = 'todo' | 'in_progress' | 'waiting';

export interface SlaPolicy {
  id: number;
  name: string;
  description: string;
  calendar_id: number;
  calendar_name: string | null;
  priority_keys: string[];
  type_keys: string[];
  department_ids: number[];
  precedence: number;
  first_response_minutes: number | null;
  resolution_minutes: number | null;
  warning_threshold_pct: number;
  pause_categories: PauseCategory[];
  pause_states: string[];
  active: boolean;
  instance_count: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface SlaPolicyInput {
  name: string;
  description: string;
  calendar_id: number;
  priority_keys: string[];
  type_keys: string[];
  department_ids: number[];
  precedence: number;
  first_response_minutes: number | null;
  resolution_minutes: number | null;
  warning_threshold_pct: number;
  pause_categories: PauseCategory[];
  pause_states: string[];
  active: boolean;
}

export interface SlaCounts {
  total: number;
  met: number;
  breached: number;
  running: number;
  paused: number;
  compliance_pct: number | null;
}

export interface SlaSummaryRow {
  first_response: SlaCounts;
  resolution: SlaCounts;
}

export interface SlaSummary {
  date_from: string;
  date_to: string;
  totals: SlaSummaryRow;
  by_policy: Array<SlaSummaryRow & { policy_id: number; policy_name: string }>;
  by_department: Array<SlaSummaryRow & { department_id: number; department_name: string }>;
}

export interface SlaAtRiskItem {
  ticket: TicketOut;
  instance_id: number;
  metric: SlaMetric;
  label: string;
  status: SlaStatus;
  paused: boolean;
  target_at: IsoDateTime | null;
  breached_at: IsoDateTime | null;
  warned_at: IsoDateTime | null;
  remaining_business_seconds: number | null;
  target_minutes: number;
  policy_id: number;
  policy_name: string | null;
}

export interface SlaAtRisk {
  computed_at: IsoDateTime;
  counts: { breached: number; at_risk: number; total: number };
  items: SlaAtRiskItem[];
}

export interface SlaPreview {
  calendar_id: number;
  start: IsoDateTime;
  minutes: number;
  target_at: IsoDateTime;
  business_seconds: number;
}

export interface SlaRecalculateResult {
  tickets: number;
  created: number;
  cancelled: number;
  breached: number;
}

// ---------- API ----------

export const slaApi = {
  ticketDetail: (ticketId: number, signal?: AbortSignal) =>
    apiFetch<TicketSlaDetail>(`/tickets/${ticketId}/sla`, { signal }),
  calendars: () => apiFetch<SlaCalendar[]>('/sla/calendars'),
  createCalendar: (body: SlaCalendarInput) => apiFetch<SlaCalendar>('/sla/calendars', { method: 'POST', body }),
  updateCalendar: (id: number, body: Partial<SlaCalendarInput>) =>
    apiFetch<SlaCalendar>(`/sla/calendars/${id}`, { method: 'PATCH', body }),
  deleteCalendar: (id: number) => apiFetch<void>(`/sla/calendars/${id}`, { method: 'DELETE' }),
  preview: (id: number, minutes: number, start?: string) =>
    apiFetch<SlaPreview>(`/sla/calendars/${id}/preview`, { query: { minutes, start } }),
  holidays: (calendarId: number, year?: number) =>
    apiFetch<SlaHoliday[]>(`/sla/calendars/${calendarId}/holidays`, { query: { year } }),
  createHoliday: (calendarId: number, body: SlaHolidayInput) =>
    apiFetch<SlaHoliday>(`/sla/calendars/${calendarId}/holidays`, { method: 'POST', body }),
  updateHoliday: (id: number, body: Partial<SlaHolidayInput>) =>
    apiFetch<SlaHoliday>(`/sla/holidays/${id}`, { method: 'PATCH', body }),
  deleteHoliday: (id: number) => apiFetch<void>(`/sla/holidays/${id}`, { method: 'DELETE' }),
  importHolidays: (calendarId: number, years: number[], country = 'GT') =>
    apiFetch<{ created: number; skipped: number }>(`/sla/calendars/${calendarId}/holidays/import`, {
      method: 'POST',
      body: { years, country },
    }),
  policies: () => apiFetch<SlaPolicy[]>('/sla/policies'),
  createPolicy: (body: SlaPolicyInput) => apiFetch<SlaPolicy>('/sla/policies', { method: 'POST', body }),
  updatePolicy: (id: number, body: Partial<SlaPolicyInput>) =>
    apiFetch<SlaPolicy>(`/sla/policies/${id}`, { method: 'PATCH', body }),
  deletePolicy: (id: number) => apiFetch<void>(`/sla/policies/${id}`, { method: 'DELETE' }),
  recalculate: () => apiFetch<SlaRecalculateResult>('/sla/recalculate', { method: 'POST' }),
};

// ---------- Presentación ----------

export const SLA_METRIC_LABELS: Record<SlaMetric, string> = {
  first_response: 'Primera respuesta',
  resolution: 'Resolución',
};

export const SLA_METRIC_SHORT: Record<SlaMetric, string> = {
  first_response: '1.ª resp.',
  resolution: 'Resol.',
};

export const SLA_STATUS_LABELS: Record<SlaStatus, string> = {
  running: 'En curso',
  paused: 'Pausado',
  met: 'Cumplido',
  breached: 'Vencido',
  cancelled: 'Cancelado',
};

export const SLA_EVENT_LABELS: Record<string, string> = {
  started: 'Inicio del reloj',
  paused: 'Pausa',
  resumed: 'Reanudación',
  met: 'Objetivo cumplido',
  breached: 'Objetivo vencido',
  warning: 'Advertencia de vencimiento',
  stopped: 'Reloj detenido',
  reopened: 'Reapertura',
  cancelled: 'Reloj cancelado',
};

export const SLA_REASON_LABELS: Record<string, string> = {
  created: 'alta del ticket',
  recalculated: 'recálculo de política',
  policy_changed: 'cambio de política',
  first_response: 'primera respuesta registrada',
  reopened: 'ticket reabierto',
  done_without_response: 'ticket cerrado sin respuesta',
  ticket_done: 'ticket finalizado',
};

export const PAUSE_CATEGORY_LABELS: Record<PauseCategory, string> = {
  todo: 'Por atender',
  in_progress: 'En curso',
  waiting: 'En espera',
};

export const WEEKDAY_LABELS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'] as const;
export const WEEKDAY_SHORT = ['L', 'M', 'X', 'J', 'V', 'S', 'D'] as const;

export type SlaTone = 'ok' | 'warning' | 'breached' | 'paused' | 'done' | 'cancelled';

/** Tono visual de una métrica: verde (en tiempo), ámbar (en riesgo), rojo (vencida). */
export function slaTone(
  info: Pick<SlaMetricInfo, 'status' | 'at_risk' | 'breached' | 'paused' | 'open' | 'remaining_business_seconds'>,
): SlaTone {
  if (info.status === 'cancelled') return 'cancelled';
  if (info.breached) return 'breached';
  // Objetivo superado pero la tarea periódica aún no lo marcó (hasta 1 min de desfase).
  if (info.open && info.remaining_business_seconds !== null && info.remaining_business_seconds < 0) return 'breached';
  if (!info.open) return 'done';
  if (info.at_risk) return 'warning';
  if (info.paused) return 'paused';
  return 'ok';
}

export const TONE_BADGE_CLASS: Record<SlaTone, string> = {
  ok: 'badge-success',
  warning: 'badge-warning',
  breached: 'badge-danger',
  paused: 'badge-neutral',
  done: 'badge-outline',
  cancelled: 'badge-outline',
};

/** Métrica más relevante para un indicador compacto: la abierta (primero 1.ª respuesta). */
export function primaryMetric(sla: TicketSla | null | undefined): SlaMetricInfo | null {
  if (!sla) return null;
  const metrics = SLA_METRICS.map((m) => sla[m]).filter((m): m is SlaMetricInfo => !!m);
  const breachedOpen = metrics.find((m) => m.open && m.breached);
  if (breachedOpen) return breachedOpen;
  const open = metrics.find((m) => m.open);
  if (open) return open;
  return sla.resolution ?? sla.first_response ?? null;
}

/** "45 min", "3 h 20 min", "26 h" — duración en tiempo hábil (siempre positiva). */
export function formatBusinessDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const abs = Math.abs(Math.round(seconds));
  if (abs < 60) return abs === 0 ? '0 min' : '< 1 min';
  const totalMinutes = Math.floor(abs / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes} min`;
  if (minutes === 0) return `${hours} h`;
  return `${hours} h ${minutes} min`;
}

/** Objetivo en minutos: "15 min", "4 h", "1 h 30 min". */
export function formatTargetMinutes(minutes: number | null | undefined): string {
  if (!minutes) return '—';
  return formatBusinessDuration(minutes * 60);
}

/** "Restan 12 min" / "Excedido por 2 h" / "Pausado (restan 1 h)". */
export function remainingLabel(info: SlaMetricInfo): string {
  const remaining = info.remaining_business_seconds;
  if (remaining === null) return SLA_STATUS_LABELS[info.status];
  if (info.status === 'cancelled') return 'Cancelado';
  if (!info.open) {
    return info.breached ? `Vencido (excedido por ${formatBusinessDuration(remaining)})` : 'Cumplido a tiempo';
  }
  if (remaining < 0) return `Excedido por ${formatBusinessDuration(remaining)}`;
  if (info.paused) return `Pausado · restan ${formatBusinessDuration(remaining)}`;
  return `Restan ${formatBusinessDuration(remaining)}`;
}

/** Porcentaje del objetivo consumido (0-100+). */
export function consumedPct(info: SlaMetricInfo): number {
  const total = info.target_minutes * 60;
  if (!total || info.remaining_business_seconds === null) return 0;
  return Math.max(0, ((total - info.remaining_business_seconds) / total) * 100);
}

export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value.toLocaleString('es-GT', { maximumFractionDigits: 1 })} %`;
}

/** Ventanas de servicio resumidas: "L-V 08:00-17:00", "24x7", "L 08:00-12:00, 13:00-17:00 · S 08:00-12:00". */
export function describeWindows(windows: SlaWindow[]): string {
  if (!windows.length) return 'Sin horario';
  const byDay = new Map<number, string>();
  for (const w of [...windows].sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start))) {
    const range = `${w.start}-${w.end}`;
    byDay.set(w.weekday, byDay.has(w.weekday) ? `${byDay.get(w.weekday)}, ${range}` : range);
  }
  if (byDay.size === 7 && [...byDay.values()].every((r) => r === '00:00-24:00')) return '24x7';
  const groups: Array<{ from: number; to: number; ranges: string }> = [];
  for (let day = 0; day < 7; day += 1) {
    const ranges = byDay.get(day);
    if (!ranges) continue;
    const last = groups[groups.length - 1];
    if (last && last.ranges === ranges && last.to === day - 1) last.to = day;
    else groups.push({ from: day, to: day, ranges });
  }
  return groups
    .map((g) => `${WEEKDAY_SHORT[g.from]}${g.to !== g.from ? `-${WEEKDAY_SHORT[g.to]}` : ''} ${g.ranges}`)
    .join(' · ');
}

export const BUSINESS_HOURS_PRESET: SlaWindow[] = [0, 1, 2, 3, 4].map((weekday) => ({
  weekday,
  start: '08:00',
  end: '17:00',
}));

export const FULL_TIME_PRESET: SlaWindow[] = [0, 1, 2, 3, 4, 5, 6].map((weekday) => ({
  weekday,
  start: '00:00',
  end: '24:00',
}));

/** Fecha local YYYY-MM-DD (para inputs type=date) en la zona de la UI. */
export function isoDateInZone(date: Date, timeZone = 'America/Guatemala'): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    date,
  );
  return parts;
}
