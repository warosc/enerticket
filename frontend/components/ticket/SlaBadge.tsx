'use client';

import styles from '@/components/sla/sla.module.css';
import { formatDateTime } from '@/lib/format';
import {
  SLA_METRIC_LABELS,
  SLA_METRIC_SHORT,
  SLA_METRICS,
  TONE_BADGE_CLASS,
  formatBusinessDuration,
  primaryMetric,
  remainingLabel,
  slaTone,
  type SlaMetricInfo,
} from '@/lib/sla';
import type { Ticket } from '@/lib/types';

function shortText(info: SlaMetricInfo): string {
  const metric = SLA_METRIC_SHORT[info.metric];
  const remaining = info.remaining_business_seconds;
  if (info.status === 'cancelled') return `${metric} cancelado`;
  if (!info.open) return info.breached ? `${metric} vencido` : `${metric} cumplido`;
  if (remaining !== null && remaining < 0) return `${metric} +${formatBusinessDuration(remaining)}`;
  if (info.paused) return `${metric} en pausa`;
  return `${metric} ${formatBusinessDuration(remaining)}`;
}

function tooltip(ticket: Ticket): string {
  const sla = ticket.sla;
  if (!sla) return '';
  const lines = [sla.policy ? `Política: ${sla.policy.name}` : 'SLA'];
  for (const metric of SLA_METRICS) {
    const info = sla[metric];
    if (!info) continue;
    const due = info.target_at ? ` · vence ${formatDateTime(info.target_at)}` : '';
    lines.push(`${SLA_METRIC_LABELS[metric]}: ${remainingLabel(info)}${info.open ? due : ''}`);
  }
  lines.push('Tiempos en horario hábil.');
  return lines.join('\n');
}

/**
 * Indicador compacto de SLA para listas y Kanban: verde (en tiempo), ámbar (en riesgo),
 * rojo (vencido), gris (pausado o finalizado). Muestra el tiempo hábil restante.
 */
export default function SlaBadge({ ticket }: { ticket: Ticket }) {
  const info = primaryMetric(ticket.sla);
  if (!info) return <span className="text-xs subtle">Sin SLA</span>;
  const tone = slaTone(info);
  const text = shortText(info);
  const title = tooltip(ticket);
  return (
    <span className={`badge ${TONE_BADGE_CLASS[tone]} ${styles.badge}`} title={title} aria-label={`SLA: ${title}`}>
      <svg className={styles.badgeIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
        <circle cx="12" cy="13" r="8" />
        <path d="M12 9v4l2.5 2.5M10 2h4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {text}
    </span>
  );
}
