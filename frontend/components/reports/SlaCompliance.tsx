'use client';

import { formatNumber } from '@/lib/format';
import { formatPct, type ReportSla, type SlaMetricRow } from '@/lib/reports';
import styles from './reports.module.css';

const METRIC_ORDER = ['first_response', 'resolution'];

function MetricCard({ row }: { row: SlaMetricRow }) {
  const decided = row.met + row.breached;
  const metPct = decided ? (row.met / decided) * 100 : 0;
  return (
    <div className={styles.slaCard}>
      <div className="row-between">
        <span className="strong">{row.label}</span>
        <span className="text-xs subtle">{formatNumber(row.total)} relojes</span>
      </div>
      <div>
        <span className={styles.slaValue}>{formatPct(row.compliance_pct)}</span>
        <span className="text-sm muted"> de cumplimiento</span>
      </div>
      {decided > 0 && (
        <div
          className={styles.stackBar}
          role="img"
          aria-label={`${row.met} cumplidos y ${row.breached} incumplidos`}
        >
          {row.met > 0 && (
            <div className={styles.stackSeg} style={{ width: `${metPct}%`, background: 'var(--status-good)' }} />
          )}
          {row.breached > 0 && (
            <div className={styles.stackSeg} style={{ width: `${100 - metPct}%`, background: 'var(--status-critical)' }} />
          )}
        </div>
      )}
      <div className="row text-xs">
        <span className="row-sm">
          <span className={styles.statusDot} style={{ background: 'var(--status-good)' }} aria-hidden="true" />
          <span aria-hidden="true">✓</span> Cumplidos <strong>{formatNumber(row.met)}</strong>
        </span>
        <span className="row-sm">
          <span className={styles.statusDot} style={{ background: 'var(--status-critical)' }} aria-hidden="true" />
          <span aria-hidden="true">✕</span> Incumplidos <strong>{formatNumber(row.breached)}</strong>
        </span>
      </div>
      <div className="text-xs muted">
        En curso {formatNumber(row.running)} · En pausa {formatNumber(row.paused)}
        {row.at_risk > 0 && (
          <>
            {' · '}
            <span className="badge badge-warning">⚠ {formatNumber(row.at_risk)} en riesgo</span>
          </>
        )}
      </div>
    </div>
  );
}

export default function SlaCompliance({ data, className }: { data: ReportSla; className?: string }) {
  const metrics = [...data.metrics].sort(
    (a, b) => METRIC_ORDER.indexOf(a.metric) - METRIC_ORDER.indexOf(b.metric),
  );
  const metricKeys = metrics.map((m) => m.metric);

  return (
    <section className={`panel ${className ?? ''}`}>
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Cumplimiento de SLA</h2>
          <p className="panel-subtitle">Relojes iniciados en el periodo · cumplidos / (cumplidos + incumplidos)</p>
        </div>
      </div>
      {!data.available ? (
        <p className="muted text-sm">{data.reason ?? 'El módulo SLA no está disponible.'}</p>
      ) : metrics.length === 0 ? (
        <p className="muted text-sm">No hay relojes SLA en el periodo seleccionado.</p>
      ) : (
        <div className="stack">
          <div className={styles.slaGrid}>
            {metrics.map((row) => (
              <MetricCard key={row.metric} row={row} />
            ))}
          </div>
          {data.by_priority.length > 0 && (
            <div className="table-wrap">
              <table className="table table-compact">
                <caption className="visually-hidden">Cumplimiento por prioridad</caption>
                <thead>
                  <tr>
                    <th scope="col">Prioridad</th>
                    {metrics.map((m) => (
                      <th key={m.metric} scope="col" className="col-num">
                        {m.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.by_priority.map((p) => (
                    <tr key={p.priority}>
                      <td>{p.name}</td>
                      {metricKeys.map((key) => {
                        const cell = p.metrics[key];
                        return (
                          <td key={key} className="col-num">
                            {cell ? (
                              <>
                                <span className="strong">{formatPct(cell.compliance_pct)}</span>{' '}
                                <span className="text-xs subtle">
                                  ({formatNumber(cell.met)}/{formatNumber(cell.met + cell.breached)})
                                </span>
                              </>
                            ) : (
                              <span className="subtle">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
