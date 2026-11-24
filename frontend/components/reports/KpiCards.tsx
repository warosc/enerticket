'use client';

import type { ReactNode } from 'react';
import { formatNumber } from '@/lib/format';
import { formatHours, formatPct, type ReportOverview } from '@/lib/reports';
import styles from './reports.module.css';

type Better = 'up' | 'down' | 'neutral';

function Delta({ current, previous, better, unit = 'count' }: {
  current: number | null;
  previous: number | null;
  better: Better;
  unit?: 'count' | 'hours';
}) {
  if (current === null || previous === null) return <span className={`${styles.delta} ${styles.deltaFlat}`}>Sin comparación</span>;
  const diff = current - previous;
  if (Math.abs(diff) < (unit === 'hours' ? 0.05 : 0.5)) {
    return <span className={`${styles.delta} ${styles.deltaFlat}`}>= igual que el periodo anterior</span>;
  }
  const improved = better === 'neutral' ? null : better === 'up' ? diff > 0 : diff < 0;
  const tone = improved === null ? styles.deltaFlat : improved ? styles.deltaGood : styles.deltaBad;
  const arrow = diff > 0 ? '▲' : '▼';
  const magnitude = unit === 'hours' ? formatHours(Math.abs(diff)) : formatNumber(Math.abs(diff));
  return (
    <span className={`${styles.delta} ${tone}`}>
      <span aria-hidden="true">{arrow}</span> {magnitude} vs. periodo anterior
      <span className="visually-hidden">{improved === null ? '' : improved ? ' (mejora)' : ' (empeora)'}</span>
    </span>
  );
}

function Kpi({
  label,
  value,
  sub,
  accent,
  children,
}: {
  label: string;
  value: string;
  sub?: string;
  accent: 'primary' | 'warning' | 'success' | 'danger' | 'neutral';
  children?: ReactNode;
}) {
  return (
    <div className={`kpi kpi-accent-${accent}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
      {children}
    </div>
  );
}

export default function KpiCards({ data }: { data: ReportOverview }) {
  const prev = data.previous;
  return (
    <section aria-label="Indicadores clave" className={styles.kpiGrid}>
      <Kpi label="Creados" value={formatNumber(data.created)} accent="primary" sub={`${formatNumber(data.open)} siguen abiertos`}>
        <Delta current={data.created} previous={prev.created} better="neutral" />
      </Kpi>
      <Kpi label="Resueltos" value={formatNumber(data.resolved)} accent="success" sub={`${formatNumber(data.closed)} cerrados en el periodo`}>
        <Delta current={data.resolved} previous={prev.resolved} better="up" />
      </Kpi>
      <Kpi
        label="Backlog actual"
        value={formatNumber(data.backlog)}
        accent="warning"
        sub={`${formatNumber(data.backlog_unassigned)} sin asignar · ${formatNumber(data.backlog_waiting)} en espera`}
      />
      <Kpi label="MTTR" value={formatHours(data.mttr_hours)} accent="neutral" sub="Tiempo medio de resolución (calendario)">
        <Delta current={data.mttr_hours} previous={prev.mttr_hours} better="down" unit="hours" />
      </Kpi>
      <Kpi
        label="Primera respuesta"
        value={formatHours(data.mean_first_response_hours)}
        accent="neutral"
        sub={`Promedio · ${formatPct(data.first_response_rate_pct)} con respuesta`}
      >
        <Delta current={data.mean_first_response_hours} previous={prev.mean_first_response_hours} better="down" unit="hours" />
      </Kpi>
    </section>
  );
}
