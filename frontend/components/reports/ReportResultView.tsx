'use client';

/** Vista previa de un ReportResult: encabezado (periodo, alcance, fecha de cálculo) y una tabla por sección. */
import type { ReactNode } from 'react';
import { formatDate, formatDateTime } from '@/lib/format';
import type { ReportDimensionMeta, ReportMetricMeta, ReportResult } from '@/lib/reports';
import PivotTable, { PivotCaption } from './PivotTable';
import styles from './builder.module.css';

export default function ReportResultView({
  result,
  dimensions,
  metrics,
  actions,
  busy,
}: {
  result: ReportResult;
  dimensions: Map<string, ReportDimensionMeta>;
  metrics: Map<string, ReportMetricMeta>;
  actions?: ReactNode;
  busy?: boolean;
}) {
  return (
    <div className="stack" aria-busy={busy} style={{ opacity: busy ? 0.6 : 1, transition: 'opacity 0.15s' }}>
      <section className="panel">
        <div className="row-between">
          <div className="stack-sm" style={{ minWidth: 0 }}>
            <h2 className="panel-title">{result.title}</h2>
            <div className="text-sm muted">
              Del {formatDate(`${result.date_from}T12:00:00Z`)} al {formatDate(`${result.date_to}T12:00:00Z`)} · hora de
              Guatemala
            </div>
            <div className="row-sm text-xs subtle">
              <span className="badge badge-info">Alcance: {result.scope?.label ?? '—'}</span>
              <span>Calculado el {formatDateTime(result.generated_at)}</span>
            </div>
          </div>
          {actions && <div className="page-actions">{actions}</div>}
        </div>
      </section>
      {result.sections.map((section, i) => (
        <section key={`${i}-${section.title}`} className={`panel ${styles.resultSection}`}>
          <div className="panel-header">
            <div className="stack-sm" style={{ gap: 2 }}>
              <h3 className="panel-title">{section.title}</h3>
              <PivotCaption section={section} metrics={metrics} />
            </div>
            <span className="text-xs subtle">
              {typeof section.ticket_count === 'number'
                ? `${section.ticket_count.toLocaleString('es-GT')} ticket${section.ticket_count === 1 ? '' : 's'} · `
                : ''}
              {section.rows.length} fila{section.rows.length === 1 ? '' : 's'}
            </span>
          </div>
          {section.truncated && (
            <p className="field-hint" role="note">
              Se muestran solo las primeras filas/columnas por límite de tamaño; acote los filtros o exporte a Excel.
            </p>
          )}
          <PivotTable section={section} dimensions={dimensions} metrics={metrics} />
        </section>
      ))}
      <p className="text-xs subtle">
        Horas hábiles según el calendario de la política SLA de cada ticket (en políticas 24x7, como «Crítica 24x7»,
        cuentan todas las horas, igual que su objetivo); sin política, el horario hábil por defecto. Promedios, medianas y porcentajes de los totales se calculan sobre
        los tickets, no como promedio de los subtotales.
      </p>
    </div>
  );
}
