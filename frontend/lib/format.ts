/** Utilidades de formato para la UI (es-GT, zona America/Guatemala). */
import type { StateCategory } from './types';

export const UI_LOCALE = 'es-GT';
export const UI_TIME_ZONE = 'America/Guatemala';

const dateTimeFormatter = new Intl.DateTimeFormat(UI_LOCALE, {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: UI_TIME_ZONE,
});

const dateFormatter = new Intl.DateTimeFormat(UI_LOCALE, { dateStyle: 'medium', timeZone: UI_TIME_ZONE });

const relativeFormatter = new Intl.RelativeTimeFormat('es', { numeric: 'auto' });

const numberFormatter = new Intl.NumberFormat(UI_LOCALE);

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value: string | Date | null | undefined, fallback = '—'): string {
  const date = toDate(value);
  return date ? dateTimeFormatter.format(date) : fallback;
}

export function formatDate(value: string | Date | null | undefined, fallback = '—'): string {
  const date = toDate(value);
  return date ? dateFormatter.format(date) : fallback;
}

/** "hace 5 minutos", "en 2 horas"... */
export function formatRelative(
  value: string | Date | null | undefined,
  now: Date = new Date(),
  fallback = '—',
): string {
  const date = toDate(value);
  if (!date) return fallback;
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 60) return relativeFormatter.format(seconds, 'second');
  if (abs < 3600) return relativeFormatter.format(Math.round(seconds / 60), 'minute');
  if (abs < 86_400) return relativeFormatter.format(Math.round(seconds / 3600), 'hour');
  if (abs < 2_592_000) return relativeFormatter.format(Math.round(seconds / 86_400), 'day');
  return formatDate(date);
}

export function formatNumber(value: number): string {
  return numberFormatter.format(value);
}

export const CATEGORY_LABELS: Record<StateCategory, string> = {
  todo: 'Por atender',
  in_progress: 'En curso',
  waiting: 'En espera',
  done: 'Finalizado',
};

export const ROLE_LABELS: Record<string, string> = {
  admin: 'Administrador',
  agent: 'Agente',
  approver: 'Aprobador',
  requester: 'Solicitante',
};

export function initials(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}
