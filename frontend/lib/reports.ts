/**
 * Cliente de la API de reportes (/reports/*) y utilidades de descarga de archivos.
 * Todos los reportes los calcula el backend sobre los tickets visibles para el usuario.
 */
import { apiFetch, apiFetchRaw, type QueryParams } from './api';
import { UI_TIME_ZONE } from './format';

export interface ReportFilterValues {
  /** Fecha inicial YYYY-MM-DD (incluida, zona America/Guatemala). */
  from: string;
  /** Fecha final YYYY-MM-DD (incluida). */
  to: string;
  department_id: number | null;
  team_id: number | null;
}

export interface ReportFiltersEcho {
  from: string;
  to: string;
  timezone: string;
  department_id: number | null;
  team_id: number | null;
}

export interface ReportOverview {
  filters: ReportFiltersEcho;
  generated_at: string;
  open: number;
  created: number;
  resolved: number;
  closed: number;
  backlog: number;
  backlog_unassigned: number;
  backlog_waiting: number;
  mttr_hours: number | null;
  mean_first_response_hours: number | null;
  first_response_rate_pct: number | null;
  previous: {
    from: string;
    to: string;
    created: number;
    resolved: number;
    closed: number;
    mttr_hours: number | null;
    mean_first_response_hours: number | null;
  };
}

export type TrendGranularity = 'day' | 'week' | 'auto';

export interface TrendPoint {
  period: string;
  created: number;
  resolved: number;
  /** Acumulado creados − resueltos desde el inicio del rango. */
  net: number;
}

export interface ReportTrend {
  filters: ReportFiltersEcho;
  granularity: 'day' | 'week';
  points: TrendPoint[];
  totals: { created: number; resolved: number };
}

export interface DepartmentRow {
  department_id: number;
  department: string;
  created: number;
  resolved: number;
  backlog: number;
  mttr_hours: number | null;
}

export interface ReportByDepartment {
  filters: ReportFiltersEcho;
  items: DepartmentRow[];
}

export interface AssigneeRow {
  assignee_id: number | null;
  assignee: string;
  open: number;
  open_critical: number;
  resolved: number;
  mttr_hours: number | null;
}

export interface ReportByAssignee {
  filters: ReportFiltersEcho;
  items: AssigneeRow[];
  truncated: boolean;
}

export interface AgingBucket {
  bucket: string;
  label: string;
  min_hours: number;
  max_hours: number | null;
  total: number;
  by_priority: Record<string, number>;
}

export interface ReportAging {
  filters: ReportFiltersEcho;
  priorities: { key: string; name: string }[];
  items: AgingBucket[];
  total: number;
  oldest_age_hours: number | null;
}

export interface SlaMetricRow {
  metric: string;
  label: string;
  total: number;
  met: number;
  breached: number;
  running: number;
  paused: number;
  cancelled: number;
  at_risk: number;
  compliance_pct: number | null;
}

export interface SlaPriorityRow {
  priority: string;
  name: string;
  rank: number;
  metrics: Record<string, { met: number; breached: number; compliance_pct: number | null }>;
}

export interface ReportSla {
  filters: ReportFiltersEcho;
  available: boolean;
  reason: string | null;
  metrics: SlaMetricRow[];
  by_priority: SlaPriorityRow[];
}

export interface ReportBundle {
  overview: ReportOverview;
  trend: ReportTrend;
  byDepartment: ReportByDepartment;
  byAssignee: ReportByAssignee;
  aging: ReportAging;
  sla: ReportSla;
}

// ---------- Fechas ----------

const isoDateFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: UI_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/** Fecha de hoy (YYYY-MM-DD) en la zona de negocio. */
export function todayIso(): string {
  return isoDateFormatter.format(new Date());
}

/** Suma días a una fecha YYYY-MM-DD (aritmética de calendario, sin zona). */
export function addDaysIso(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

/** Primer día del mes de una fecha YYYY-MM-DD. */
export function monthStartIso(iso: string): string {
  return `${iso.slice(0, 7)}-01`;
}

export function daysBetweenInclusive(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  return Math.round((b - a) / 86_400_000) + 1;
}

export function defaultReportFilters(): ReportFilterValues {
  const to = todayIso();
  return { from: addDaysIso(to, -29), to, department_id: null, team_id: null };
}

/** Etiqueta corta de un periodo del eje (día o semana). */
export function formatPeriodLabel(iso: string, granularity: 'day' | 'week'): string {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  const label = new Intl.DateTimeFormat('es-GT', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date);
  return granularity === 'week' ? `Sem. ${label}` : label;
}

/** "3,5 h", "2 d 4 h"… null → "—". */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return '—';
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${hours.toLocaleString('es-GT', { maximumFractionDigits: 1 })} h`;
  const days = Math.floor(hours / 24);
  const rest = Math.round(hours - days * 24);
  return rest ? `${days} d ${rest} h` : `${days} d`;
}

export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value.toLocaleString('es-GT', { maximumFractionDigits: 1 })} %`;
}

// ---------- API ----------

export function reportQuery(filters: ReportFilterValues): QueryParams {
  return {
    from: filters.from,
    to: filters.to,
    department_id: filters.department_id,
    team_id: filters.team_id,
  };
}

export async function fetchReports(filters: ReportFilterValues, signal?: AbortSignal): Promise<ReportBundle> {
  const query = reportQuery(filters);
  const get = <T>(path: string, extra?: QueryParams) =>
    apiFetch<T>(`/reports/${path}`, { query: { ...query, ...extra }, signal });
  const [overview, trend, byDepartment, byAssignee, aging, sla] = await Promise.all([
    get<ReportOverview>('overview'),
    get<ReportTrend>('trend', { granularity: 'auto' }),
    get<ReportByDepartment>('by-department'),
    get<ReportByAssignee>('by-assignee'),
    get<ReportAging>('aging'),
    get<ReportSla>('sla'),
  ]);
  return { overview, trend, byDepartment, byAssignee, aging, sla };
}

// ---------- Descargas ----------

/** Extrae el nombre de archivo de Content-Disposition (prefiere filename*). */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* se usa el nombre ASCII */
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"/i.exec(header) ?? /filename\s*=\s*([^;]+)/i.exec(header);
  return plain ? plain[1].trim() : fallback;
}

/** Ofrece un Blob como descarga en el navegador. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Se libera después de que el navegador inicie la descarga.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** Descarga un recurso autenticado de la API (fetch + blob) con su nombre original. */
export async function downloadAuthenticated(path: string, fallbackName: string, query?: QueryParams): Promise<void> {
  const response = await apiFetchRaw(path, { query, headers: { Accept: '*/*' } });
  const blob = await response.blob();
  saveBlob(blob, filenameFromDisposition(response.headers.get('content-disposition'), fallbackName));
}

/** Descarga el CSV de tickets del periodo filtrado. */
export function downloadReportCsv(filters: ReportFilterValues): Promise<void> {
  return downloadAuthenticated(
    '/reports/export.csv',
    `tickets_${filters.from.replaceAll('-', '')}_${filters.to.replaceAll('-', '')}.csv`,
    reportQuery(filters),
  );
}
