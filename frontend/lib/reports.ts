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

/** Descarga el resultado de un POST autenticado (p.ej. exportación .xlsx de una definición). */
export async function downloadAuthenticatedPost(path: string, body: unknown, fallbackName: string): Promise<void> {
  const response = await apiFetchRaw(path, { method: 'POST', body, headers: { Accept: '*/*' } });
  const blob = await response.blob();
  saveBlob(blob, filenameFromDisposition(response.headers.get('content-disposition'), fallbackName));
}

// =====================================================================================
// Generador de reportes (Fase 3): /reports/builder/* y /reports/templates/*
// Formas JSON según docs/FASE3_PLAN.md («Generador de reportes»).
// =====================================================================================

export type ReportDimensionKind = 'category' | 'date';
export type ReportMetricUnit = 'count' | 'hours' | 'pct';
export type ReportDateField = 'created_at' | 'resolved_at';
export type ReportPeriod = 'custom' | 'this_month' | 'last_month' | 'last_30_days' | 'this_year' | 'last_year';
export type ReportScopeLevel = 'all' | 'areas' | 'own';

export interface ReportDimensionMeta {
  key: string;
  label: string;
  kind: ReportDimensionKind;
}

export interface ReportMetricMeta {
  key: string;
  label: string;
  unit: ReportMetricUnit;
}

export interface ReportDateFieldMeta {
  key: ReportDateField;
  label: string;
}

export interface ReportScope {
  level: ReportScopeLevel;
  label: string;
}

/** GET /reports/builder/meta */
export interface ReportBuilderMeta {
  dimensions: ReportDimensionMeta[];
  metrics: ReportMetricMeta[];
  date_fields: ReportDateFieldMeta[];
  scope: ReportScope;
  periods?: Array<{ key: ReportPeriod; label: string }>;
  detail_columns?: Array<{ key: string; label: string; default: boolean }>;
  limits?: Partial<Record<string, number>>;
  /** Admin o líder de equipo: puede programar envíos de plantillas. */
  can_schedule?: boolean;
}

/** Filtros de una definición (globales o de sección). Listas vacías = sin filtro. */
export interface ReportDefinitionFilters {
  type_keys: string[];
  department_ids: number[];
  team_ids: number[];
  category_ids: number[];
  subcategory_ids: number[];
  provider_ids: number[];
  gerencia_ids: number[];
  requester_department_ids: number[];
  assignee_ids: number[];
  priority_keys: string[];
  channels: string[];
  /** null = todos; true = solo VIP; false = sin VIP. */
  vip: boolean | null;
}

export interface ReportSectionDefinition {
  title: string;
  /** 1–2 dimensiones. */
  rows: string[];
  /** 0–1 dimensión. */
  columns: string[];
  /** 1–8 métricas. */
  metrics: string[];
  filters: ReportDefinitionFilters;
  totals: boolean;
}

export interface ReportDefinition {
  title: string;
  date_field: ReportDateField;
  period: ReportPeriod;
  /** YYYY-MM-DD; con `period` ≠ `custom` el backend recalcula las fechas. */
  date_from: string;
  date_to: string;
  filters: ReportDefinitionFilters;
  sections: ReportSectionDefinition[];
}

export interface ReportResultKey {
  key: string;
  label: string;
}

/** Valores por métrica; null donde no hay datos. */
export type ReportMetricValues = Record<string, number | null>;

export interface ReportResultRow {
  keys: ReportResultKey[];
  cells: Record<string, ReportMetricValues>;
  total?: ReportMetricValues | null;
}

export interface ReportResultSection {
  title: string;
  row_dims: string[];
  column_dim: string | null;
  metrics: string[];
  /** Sin `column_dim` hay una sola columna con key `"_all"`. */
  columns: ReportResultKey[];
  rows: ReportResultRow[];
  totals?: Record<string, ReportMetricValues> | null;
  grand_total?: ReportMetricValues | null;
  /** false si la sección se definió sin totales (la API igual envía los valores). */
  show_totals?: boolean;
  ticket_count?: number;
  /** true si se recortaron filas o columnas por los límites del servidor. */
  truncated?: boolean;
}

/** POST /reports/builder/run y POST /reports/templates/{id}/run */
export interface ReportResult {
  title: string;
  generated_at: string;
  date_from: string;
  date_to: string;
  scope: ReportScope;
  sections: ReportResultSection[];
  date_field?: ReportDateField;
  period?: ReportPeriod;
  timezone?: string;
}

export const ALL_COLUMNS_KEY = '_all';

export type ReportScheduleFrequency = 'monthly' | 'weekly';

export interface ReportSchedule {
  enabled: boolean;
  frequency: ReportScheduleFrequency;
  /** Mensual: día del mes (1–28). Semanal: 0 = lunes … 6 = domingo. */
  day: number;
  /** Hora de Guatemala (0–23). */
  hour: number;
  recipients: string[];
}

export interface ReportTemplate {
  id: number;
  name: string;
  description: string | null;
  /** Dueño (null en las de fábrica). */
  owner?: { id: number; display_name: string } | null;
  owner_id?: number | null;
  is_system: boolean;
  definition: ReportDefinition;
  shared_roles: string[];
  shared_department_ids: number[];
  schedule: ReportSchedule | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  created_at: string;
  updated_at: string;
  /** Permisos calculados por la API. Si faltan se deducen (dueño o admin, nunca de fábrica). */
  can_edit?: boolean;
  can_delete?: boolean;
  /** Puede programar envíos de esta plantilla (editable y admin/líder). */
  can_schedule?: boolean;
}

export interface ReportTemplateInput {
  name: string;
  description?: string;
  definition: ReportDefinition;
  shared_roles: string[];
  shared_department_ids: number[];
  /** Solo admin o líder de equipo; null la quita. Se omite para no tocar la programación existente. */
  schedule?: ReportSchedule | null;
}

// ---------- Límites del contrato ----------

export const REPORT_LIMITS = {
  maxRangeDays: 366,
  maxSections: 10,
  maxRowDims: 2,
  maxColumnDims: 1,
  maxMetrics: 8,
  maxRecipients: 20,
} as const;

// ---------- Metadatos por defecto (respaldo si /builder/meta no responde) ----------

export const DEFAULT_DIMENSIONS: ReportDimensionMeta[] = [
  { key: 'type', label: 'Tipo', kind: 'category' },
  { key: 'department', label: 'Departamento de atención', kind: 'category' },
  { key: 'team', label: 'Equipo', kind: 'category' },
  { key: 'gerencia', label: 'Gerencia solicitante', kind: 'category' },
  { key: 'requester_department', label: 'Departamento solicitante', kind: 'category' },
  { key: 'category', label: 'Categoría', kind: 'category' },
  { key: 'subcategory', label: 'Subcategoría', kind: 'category' },
  { key: 'provider', label: 'Proveedor', kind: 'category' },
  { key: 'assignee', label: 'Técnico asignado', kind: 'category' },
  { key: 'resolved_by', label: 'Resuelto por', kind: 'category' },
  { key: 'priority', label: 'Prioridad', kind: 'category' },
  { key: 'vip', label: 'VIP', kind: 'category' },
  { key: 'channel', label: 'Canal', kind: 'category' },
  { key: 'status', label: 'Estado', kind: 'category' },
  { key: 'day', label: 'Día', kind: 'date' },
  { key: 'week', label: 'Semana', kind: 'date' },
  { key: 'month', label: 'Mes', kind: 'date' },
  { key: 'year', label: 'Año', kind: 'date' },
];

export const DEFAULT_METRICS: ReportMetricMeta[] = [
  { key: 'count', label: 'Cantidad de tickets', unit: 'count' },
  { key: 'resolution_hours_avg', label: 'Promedio horas hábiles de solución', unit: 'hours' },
  { key: 'resolution_hours_sum', label: 'Total horas hábiles de solución', unit: 'hours' },
  { key: 'resolution_hours_median', label: 'Mediana horas hábiles de solución', unit: 'hours' },
  { key: 'resolution_hours_max', label: 'Máximo horas hábiles de solución', unit: 'hours' },
  { key: 'first_response_hours_avg', label: 'Promedio horas a primera respuesta', unit: 'hours' },
  { key: 'sla_target_hours', label: 'Objetivo SLA (horas)', unit: 'hours' },
  { key: 'sla_compliance_pct', label: 'Cumplimiento SLA', unit: 'pct' },
  { key: 'sla_breached_count', label: 'SLA vencidos', unit: 'count' },
  { key: 'open_count', label: 'Abiertos', unit: 'count' },
];

export const DEFAULT_DATE_FIELDS: ReportDateFieldMeta[] = [
  { key: 'created_at', label: 'Fecha de solicitud' },
  { key: 'resolved_at', label: 'Fecha de solución' },
];

export const REPORT_PERIODS: Array<{ key: ReportPeriod; label: string }> = [
  { key: 'this_month', label: 'Mes actual' },
  { key: 'last_month', label: 'Mes anterior' },
  { key: 'last_30_days', label: 'Últimos 30 días' },
  { key: 'this_year', label: 'Año actual' },
  { key: 'last_year', label: 'Año anterior' },
  { key: 'custom', label: 'Rango personalizado' },
];

export const ROLE_LABELS: Record<string, string> = {
  admin: 'Administradores',
  agent: 'Agentes',
  approver: 'Aprobadores',
  requester: 'Solicitantes',
};

export const WEEKDAY_LABELS: readonly string[] = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

export const TEMPLATE_STATUS_LABELS: Record<string, string> = {
  sent: 'Enviado',
  smtp_disabled: 'Correo no configurado',
  owner_inactive: 'Dueño inactivo',
  owner_stale: 'Dueño sin sesión reciente',
  not_allowed: 'Sin permiso para programar',
  no_recipients: 'Sin destinatarios',
  invalid_definition: 'Definición inválida',
  error: 'Error al enviar',
  retrying: 'Error al enviar (se reintentará)',
};

/** Id del dueño de una plantilla (`owner.id` o `owner_id`). */
export function templateOwnerId(template: Pick<ReportTemplate, 'owner' | 'owner_id'>): number | null {
  return template.owner?.id ?? template.owner_id ?? null;
}

// ---------- Construcción y normalización de definiciones ----------

export function emptyReportFilters(): ReportDefinitionFilters {
  return {
    type_keys: [],
    department_ids: [],
    team_ids: [],
    category_ids: [],
    subcategory_ids: [],
    provider_ids: [],
    gerencia_ids: [],
    requester_department_ids: [],
    assignee_ids: [],
    priority_keys: [],
    channels: [],
    vip: null,
  };
}

export function newReportSection(index = 0): ReportSectionDefinition {
  return {
    title: index === 0 ? 'Resumen' : `Sección ${index + 1}`,
    rows: ['category'],
    columns: ['month'],
    metrics: ['count', 'resolution_hours_avg'],
    filters: emptyReportFilters(),
    totals: true,
  };
}

/** Fechas que el backend calcularía para un periodo (hora de Guatemala). */
export function periodRange(period: ReportPeriod, today: string = todayIso()): { from: string; to: string } {
  const year = Number(today.slice(0, 4));
  switch (period) {
    case 'last_month': {
      const lastDayPrev = addDaysIso(monthStartIso(today), -1);
      return { from: monthStartIso(lastDayPrev), to: lastDayPrev };
    }
    case 'last_30_days':
      return { from: addDaysIso(today, -29), to: today };
    case 'this_year':
      return { from: `${year}-01-01`, to: today };
    case 'last_year':
      return { from: `${year - 1}-01-01`, to: `${year - 1}-12-31` };
    default:
      return { from: monthStartIso(today), to: today };
  }
}

export function newReportDefinition(): ReportDefinition {
  const range = periodRange('this_month');
  return {
    title: 'Reporte personalizado',
    date_field: 'created_at',
    period: 'this_month',
    date_from: range.from,
    date_to: range.to,
    filters: emptyReportFilters(),
    sections: [newReportSection(0)],
  };
}

function numberList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.map(Number).filter((n) => Number.isInteger(n) && n > 0);
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === 'string' && v !== '');
}

export function normalizeReportFilters(raw: unknown): ReportDefinitionFilters {
  const f = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  return {
    type_keys: stringList(f.type_keys),
    department_ids: numberList(f.department_ids),
    team_ids: numberList(f.team_ids),
    category_ids: numberList(f.category_ids),
    subcategory_ids: numberList(f.subcategory_ids),
    provider_ids: numberList(f.provider_ids),
    gerencia_ids: numberList(f.gerencia_ids),
    requester_department_ids: numberList(f.requester_department_ids),
    assignee_ids: numberList(f.assignee_ids),
    priority_keys: stringList(f.priority_keys),
    channels: stringList(f.channels),
    vip: typeof f.vip === 'boolean' ? f.vip : null,
  };
}

const PERIOD_KEYS = new Set<string>(REPORT_PERIODS.map((p) => p.key));
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Completa una definición recibida de la API (plantillas) con los valores por defecto del contrato. */
export function normalizeReportDefinition(raw: unknown): ReportDefinition {
  const base = newReportDefinition();
  const d = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const period = typeof d.period === 'string' && PERIOD_KEYS.has(d.period) ? (d.period as ReportPeriod) : base.period;
  const range = period === 'custom' ? null : periodRange(period);
  const sectionsRaw = Array.isArray(d.sections) ? d.sections : [];
  const sections = sectionsRaw.map((s, i): ReportSectionDefinition => {
    const sec = (s && typeof s === 'object' ? s : {}) as Record<string, unknown>;
    return {
      title: typeof sec.title === 'string' ? sec.title : `Sección ${i + 1}`,
      rows: stringList(sec.rows).slice(0, REPORT_LIMITS.maxRowDims),
      columns: stringList(sec.columns).slice(0, REPORT_LIMITS.maxColumnDims),
      metrics: stringList(sec.metrics).slice(0, REPORT_LIMITS.maxMetrics),
      filters: normalizeReportFilters(sec.filters),
      totals: typeof sec.totals === 'boolean' ? sec.totals : true,
    };
  });
  const rawFrom = typeof d.date_from === 'string' && ISO_DAY.test(d.date_from) ? d.date_from : base.date_from;
  const rawTo = typeof d.date_to === 'string' && ISO_DAY.test(d.date_to) ? d.date_to : base.date_to;
  return {
    title: typeof d.title === 'string' && d.title.trim() ? d.title : base.title,
    date_field: d.date_field === 'resolved_at' ? 'resolved_at' : 'created_at',
    period,
    date_from: range?.from ?? rawFrom,
    date_to: range?.to ?? rawTo,
    filters: normalizeReportFilters(d.filters),
    sections: sections.length ? sections : base.sections,
  };
}

/** Prepara la definición para la API: fechas del periodo y títulos recortados. */
export function prepareReportDefinition(definition: ReportDefinition): ReportDefinition {
  const range = definition.period === 'custom' ? null : periodRange(definition.period);
  return {
    ...definition,
    title: definition.title.trim(),
    date_from: range?.from ?? definition.date_from,
    date_to: range?.to ?? definition.date_to,
    sections: definition.sections.map((s) => ({ ...s, title: s.title.trim() })),
  };
}

/** Cantidad de filtros activos (para indicadores «N activos»). */
export function countActiveFilters(filters: ReportDefinitionFilters): number {
  let n = filters.vip === null ? 0 : 1;
  for (const value of Object.values(filters)) if (Array.isArray(value) && value.length) n += 1;
  return n;
}

/** Valida la definición con los límites del contrato; devuelve mensajes en español. */
export function validateReportDefinition(definition: ReportDefinition): string[] {
  const errors: string[] = [];
  if (!definition.title.trim()) errors.push('Indique un título para el reporte.');
  if (definition.period === 'custom') {
    if (!ISO_DAY.test(definition.date_from) || !ISO_DAY.test(definition.date_to)) {
      errors.push('Indique la fecha inicial y la final.');
    } else if (definition.date_from > definition.date_to) {
      errors.push('La fecha inicial no puede ser posterior a la final.');
    } else if (daysBetweenInclusive(definition.date_from, definition.date_to) > REPORT_LIMITS.maxRangeDays) {
      errors.push(`El rango máximo es de ${REPORT_LIMITS.maxRangeDays} días.`);
    }
  }
  const sections = definition.sections;
  if (sections.length < 1) errors.push('Agregue al menos una sección.');
  if (sections.length > REPORT_LIMITS.maxSections) errors.push(`Máximo ${REPORT_LIMITS.maxSections} secciones.`);
  sections.forEach((s, i) => {
    const name = s.title.trim() ? `«${s.title.trim()}»` : `${i + 1}`;
    if (s.title.trim().length > 120) errors.push(`Sección ${name}: el título admite máximo 120 caracteres.`);
    if (s.rows.length < 1) errors.push(`Sección ${name}: elija al menos una dimensión de filas.`);
    if (new Set(s.rows).size !== s.rows.length) errors.push(`Sección ${name}: las filas no pueden repetirse.`);
    if (s.columns.some((c) => s.rows.includes(c))) {
      errors.push(`Sección ${name}: la columna no puede ser también una fila.`);
    }
    if (s.metrics.length < 1) errors.push(`Sección ${name}: elija al menos una métrica.`);
    if (s.metrics.length > REPORT_LIMITS.maxMetrics) {
      errors.push(`Sección ${name}: máximo ${REPORT_LIMITS.maxMetrics} métricas.`);
    }
  });
  return errors;
}

// ---------- Formato de valores ----------

const decimalFormatter = new Intl.NumberFormat('es', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const pctFormatter = new Intl.NumberFormat('es', { minimumFractionDigits: 0, maximumFractionDigits: 1 });
const countFormatter = new Intl.NumberFormat('es-GT', { maximumFractionDigits: 0 });

/** Formato de una métrica del generador: "12,5 h", "95,2 %", "1,234". null → "—". */
export function formatMetricValue(value: number | null | undefined, unit: ReportMetricUnit): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (unit === 'hours') return `${decimalFormatter.format(value)} h`;
  if (unit === 'pct') return `${pctFormatter.format(value)} %`;
  return Number.isInteger(value) ? countFormatter.format(value) : decimalFormatter.format(value);
}

/** Nombre de archivo seguro a partir de un título. */
export function slugifyFilename(text: string, fallback = 'reporte'): string {
  const slug = text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60);
  return slug || fallback;
}

// ---------- Catálogo para los filtros ----------

export interface ReportOption<T extends string | number = number> {
  value: T;
  label: string;
  /** Agrupador visual (p.ej. el departamento de una categoría). */
  group?: string;
}

export interface ReportCatalogDepartment {
  id: number;
  name: string;
  gerencia_id: number | null;
  serves_tickets: boolean;
  active: boolean;
}

export interface ReportCatalogCategory {
  id: number;
  department_id: number;
  name: string;
  type_keys: string[];
  subcategories: Array<{ id: number; name: string }>;
}

export interface ReportCatalogOptions {
  types: ReportOption<string>[];
  priorities: ReportOption<string>[];
  channels: ReportOption<string>[];
  departments: ReportCatalogDepartment[];
  teams: Array<{ id: number; name: string; department_id: number }>;
  categories: ReportCatalogCategory[];
  providers: Array<{ id: number; name: string; department_ids: number[] }>;
  gerencias: Array<{ id: number; name: string }>;
}

const DEFAULT_CHANNELS: ReportOption<string>[] = [
  { value: 'portal', label: 'Portal' },
  { value: 'email', label: 'Correo' },
  { value: 'phone', label: 'Teléfono' },
  { value: 'other', label: 'Otro' },
];

function asArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((v): v is Record<string, unknown> => Boolean(v) && typeof v === 'object')
    : [];
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value);
}

/**
 * Extrae del catálogo (/catalog) las listas que usan los filtros del generador. Lee los campos de la Fase 3
 * de forma defensiva para no depender de la versión de `Catalog` en lib/types.ts.
 */
export function reportCatalogOptions(catalog: unknown): ReportCatalogOptions {
  const c = (catalog && typeof catalog === 'object' ? catalog : {}) as Record<string, unknown>;
  const channels = asArray(c.channels).map((ch) => ({ value: str(ch.key), label: str(ch.label) || str(ch.key) }));
  return {
    types: asArray(c.ticket_types).map((t) => ({ value: str(t.key), label: str(t.name) })),
    priorities: asArray(c.priorities).map((p) => ({ value: str(p.key), label: str(p.name) })),
    channels: channels.length ? channels : DEFAULT_CHANNELS,
    departments: asArray(c.departments).map((d) => ({
      id: Number(d.id),
      name: str(d.name),
      gerencia_id: typeof d.gerencia_id === 'number' ? d.gerencia_id : null,
      serves_tickets: d.serves_tickets !== false,
      active: d.active !== false,
    })),
    teams: asArray(c.teams).map((t) => ({ id: Number(t.id), name: str(t.name), department_id: Number(t.department_id) })),
    categories: asArray(c.categories).map((cat) => ({
      id: Number(cat.id),
      department_id: Number(cat.department_id),
      name: str(cat.name),
      type_keys: stringList(cat.type_keys),
      subcategories: asArray(cat.subcategories).map((s) => ({ id: Number(s.id), name: str(s.name) })),
    })),
    providers: asArray(c.providers).map((p) => ({
      id: Number(p.id),
      name: str(p.name),
      department_ids: numberList(p.department_ids),
    })),
    gerencias: asArray(c.gerencias).map((g) => ({ id: Number(g.id), name: str(g.name) })),
  };
}

// ---------- Permisos (solo para decidir qué mostrar; la API es la autoridad) ----------

/** Equipos que el usuario lidera según /me (`lead_team_ids` o `teams[].role === 'lead'`). */
export function leadTeamIds(me: unknown): number[] {
  if (!me || typeof me !== 'object') return [];
  const m = me as { lead_team_ids?: unknown; teams?: unknown };
  const ids = new Set(numberList(m.lead_team_ids));
  for (const team of asArray(m.teams)) {
    if (team.role === 'lead' && typeof team.id === 'number') ids.add(team.id);
  }
  return [...ids];
}

// ---------- API del generador ----------

export function fetchBuilderMeta(signal?: AbortSignal): Promise<ReportBuilderMeta> {
  return apiFetch<ReportBuilderMeta>('/reports/builder/meta', { signal });
}

export function runReport(definition: ReportDefinition, signal?: AbortSignal): Promise<ReportResult> {
  return apiFetch<ReportResult>('/reports/builder/run', {
    method: 'POST',
    body: prepareReportDefinition(definition),
    signal,
  });
}

export function exportReportXlsx(
  definition: ReportDefinition,
  includeDetail: boolean,
  detailColumns?: string[],
): Promise<void> {
  const prepared = prepareReportDefinition(definition);
  const body: Record<string, unknown> = { definition: prepared, include_detail: includeDetail };
  if (detailColumns?.length) body.detail_columns = detailColumns;
  const from = prepared.date_from.replaceAll('-', '');
  const to = prepared.date_to.replaceAll('-', '');
  return downloadAuthenticatedPost(
    '/reports/builder/export.xlsx',
    body,
    `${slugifyFilename(prepared.title)}_${from}_${to}.xlsx`,
  );
}

// ---------- API de plantillas ----------

export async function listReportTemplates(signal?: AbortSignal): Promise<ReportTemplate[]> {
  const data = await apiFetch<ReportTemplate[] | { items?: ReportTemplate[] }>('/reports/templates', { signal });
  const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
  return items.map((t) => ({ ...t, definition: normalizeReportDefinition(t.definition) }));
}

export function createReportTemplate(input: ReportTemplateInput): Promise<ReportTemplate> {
  return apiFetch<ReportTemplate>('/reports/templates', {
    method: 'POST',
    body: { ...input, definition: prepareReportDefinition(input.definition) },
  });
}

export function updateReportTemplate(id: number, input: Partial<ReportTemplateInput>): Promise<ReportTemplate> {
  const body = input.definition ? { ...input, definition: prepareReportDefinition(input.definition) } : input;
  return apiFetch<ReportTemplate>(`/reports/templates/${id}`, { method: 'PATCH', body });
}

export function deleteReportTemplate(id: number): Promise<void> {
  return apiFetch<void>(`/reports/templates/${id}`, { method: 'DELETE' });
}

export function duplicateReportTemplate(id: number): Promise<ReportTemplate> {
  return apiFetch<ReportTemplate>(`/reports/templates/${id}/duplicate`, { method: 'POST' });
}

export function runReportTemplate(id: number, signal?: AbortSignal): Promise<ReportResult> {
  return apiFetch<ReportResult>(`/reports/templates/${id}/run`, { method: 'POST', signal });
}

export function exportReportTemplateXlsx(template: Pick<ReportTemplate, 'id' | 'name'>): Promise<void> {
  return downloadAuthenticated(
    `/reports/templates/${template.id}/export.xlsx`,
    `${slugifyFilename(template.name, 'plantilla')}.xlsx`,
  );
}

/** Resumen legible de la programación de una plantilla. */
export function describeSchedule(schedule: ReportSchedule | null | undefined): string | null {
  if (!schedule || !schedule.enabled) return null;
  const hour = `${String(schedule.hour).padStart(2, '0')}:00`;
  const when =
    schedule.frequency === 'weekly'
      ? `Cada ${(WEEKDAY_LABELS[schedule.day] ?? 'semana').toLowerCase()}`
      : `Mensual, día ${schedule.day}`;
  const n = schedule.recipients?.length ?? 0;
  return `${when} a las ${hour} · ${n} destinatario${n === 1 ? '' : 's'}`;
}
