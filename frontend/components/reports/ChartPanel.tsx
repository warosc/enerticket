'use client';

import { useState, type ReactNode } from 'react';
import { formatNumber } from '@/lib/format';
import styles from './reports.module.css';

export interface LegendEntry {
  key: string;
  label: string;
  color: string;
  /** 'line' dibuja un trazo en lugar de un cuadro. */
  shape?: 'box' | 'line';
}

export function Legend({ items }: { items: LegendEntry[] }) {
  if (items.length < 2) return null;
  return (
    <ul className={styles.legend} aria-label="Leyenda" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
      {items.map((item) => (
        <li key={item.key} className={styles.legendItem}>
          <span
            className={item.shape === 'line' ? styles.swatchLine : styles.swatch}
            style={{ background: item.color }}
            aria-hidden="true"
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/**
 * Panel de gráfico con título, leyenda y alternativa de tabla accesible.
 * `table` se muestra cuando el usuario elige "Ver tabla" (o si no hay gráfico).
 */
export function ChartPanel({
  title,
  subtitle,
  legend,
  chart,
  table,
  empty,
  emptyText = 'Sin datos para el periodo seleccionado.',
  className,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  legend?: LegendEntry[];
  chart: ReactNode;
  table: ReactNode;
  empty?: boolean;
  emptyText?: string;
  className?: string;
  actions?: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className={`panel ${className ?? ''}`}>
      <div className="panel-header">
        <div>
          <h2 className="panel-title">{title}</h2>
          {subtitle && <p className="panel-subtitle">{subtitle}</p>}
        </div>
        <div className="row-sm">
          {actions}
          {!empty && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-pressed={asTable}
              onClick={() => setAsTable((v) => !v)}
            >
              {asTable ? 'Ver gráfico' : 'Ver tabla'}
            </button>
          )}
        </div>
      </div>
      {empty ? (
        <p className="muted text-sm">{emptyText}</p>
      ) : asTable ? (
        <div className="table-wrap">{table}</div>
      ) : (
        <>
          {legend && <Legend items={legend} />}
          {chart}
        </>
      )}
    </section>
  );
}

interface TooltipEntry {
  name?: string | number;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
}

/** Tooltip de Recharts con los tokens del portal (texto en tinta, color solo en la marca). */
export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
  valueFormatter = (v) => formatNumber(v),
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  labelFormatter?: (label: string) => string;
  valueFormatter?: (value: number) => string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const title = labelFormatter ? labelFormatter(String(label ?? '')) : String(label ?? '');
  return (
    <div className={styles.tooltip}>
      <div className={styles.tooltipTitle}>{title}</div>
      {payload.map((entry) => (
        <div key={String(entry.dataKey ?? entry.name)} className={styles.tooltipRow}>
          <span className={styles.swatch} style={{ background: entry.color }} aria-hidden="true" />
          <span>{entry.name}</span>
          <span className={styles.tooltipValue}>
            {typeof entry.value === 'number' ? valueFormatter(entry.value) : (entry.value ?? '—')}
          </span>
        </div>
      ))}
    </div>
  );
}

export const AXIS_PROPS = {
  tickLine: false,
  tick: { fontSize: 11, fill: 'var(--chart-axis)' },
} as const;
