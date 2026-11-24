'use client';

import { useMemo, useState } from 'react';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { formatDate, formatNumber } from '@/lib/format';
import { useApiResource, useCatalog } from '@/lib/hooks';
import {
  SLA_METRIC_LABELS,
  SLA_METRICS,
  formatPct,
  isoDateInZone,
  type SlaCounts,
  type SlaMetric,
  type SlaSummary,
  type SlaSummaryRow,
  type SlaTone,
} from '@/lib/sla';
import { SlaProgress } from './shared';
import styles from './sla.module.css';

function complianceTone(pct: number | null): SlaTone {
  if (pct === null) return 'done';
  if (pct >= 90) return 'ok';
  if (pct >= 75) return 'warning';
  return 'breached';
}

function PctCell({ counts }: { counts: SlaCounts }) {
  const pct = counts.compliance_pct;
  return (
    <div className={styles.pctCell}>
      <span className="strong nowrap" style={{ minWidth: 56 }}>
        {formatPct(pct)}
      </span>
      <div className={styles.pctBar}>
        <SlaProgress pct={pct ?? 0} tone={complianceTone(pct)} label={`Cumplimiento ${formatPct(pct)}`} />
      </div>
    </div>
  );
}

function MetricCells({ row, metric }: { row: SlaSummaryRow; metric: SlaMetric }) {
  const counts = row[metric];
  return (
    <>
      <td>
        <PctCell counts={counts} />
      </td>
      <td className="col-num">{formatNumber(counts.met)}</td>
      <td className="col-num">{formatNumber(counts.breached)}</td>
      <td className="col-num">{formatNumber(counts.running + counts.paused)}</td>
    </>
  );
}

function SummaryTable({
  title,
  rows,
}: {
  title: string;
  rows: Array<SlaSummaryRow & { key: number; name: string }>;
}) {
  return (
    <section className="panel panel-flush">
      <div className="panel-header">
        <h2 className="panel-title">{title}</h2>
      </div>
      {rows.length === 0 ? (
        <EmptyState title="Sin datos en el rango" />
      ) : (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th scope="col" rowSpan={2}>
                  Nombre
                </th>
                {SLA_METRICS.map((m) => (
                  <th key={m} scope="colgroup" colSpan={4} className="text-center">
                    {SLA_METRIC_LABELS[m]}
                  </th>
                ))}
              </tr>
              <tr>
                {SLA_METRICS.map((m) => (
                  <FragmentHeaders key={m} />
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td className="strong">{row.name}</td>
                  {SLA_METRICS.map((m) => (
                    <MetricCells key={m} row={row} metric={m} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FragmentHeaders() {
  return (
    <>
      <th scope="col">Cumplimiento</th>
      <th scope="col" className="col-num">
        Cumplidos
      </th>
      <th scope="col" className="col-num">
        Vencidos
      </th>
      <th scope="col" className="col-num">
        En curso
      </th>
    </>
  );
}

/** Cumplimiento de SLA por política y departamento (tickets creados en el rango y visibles). */
export default function ComplianceSection() {
  const { catalog } = useCatalog();
  const today = useMemo(() => new Date(), []);
  const [dateTo, setDateTo] = useState(() => isoDateInZone(today));
  const [dateFrom, setDateFrom] = useState(() => isoDateInZone(new Date(today.getTime() - 29 * 86_400_000)));
  const [departmentId, setDepartmentId] = useState<number | ''>('');
  const invalidRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);
  const query = useMemo(
    () => ({ date_from: dateFrom, date_to: dateTo, department_id: departmentId === '' ? undefined : departmentId }),
    [dateFrom, dateTo, departmentId],
  );
  const { data, error, loading, reload } = useApiResource<SlaSummary>(invalidRange ? null : '/sla/summary', query);

  const departments = useMemo(
    () => [...(catalog?.departments ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'es')),
    [catalog],
  );

  return (
    <div className="stack">
      <section className="panel">
        <div className="toolbar">
          <label className="field">
            <span className="field-label">Desde</span>
            <input className="input" type="date" value={dateFrom} max={dateTo} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Hasta</span>
            <input className="input" type="date" value={dateTo} min={dateFrom} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Departamento</span>
            <select
              className="select"
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value === '' ? '' : Number(e.target.value))}
            >
              <option value="">Todos los visibles</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="field-hint" style={{ marginTop: 'var(--space-2)' }}>
          Tickets creados en el rango. Cumplimiento = cumplidos ÷ (cumplidos + vencidos); los relojes en curso no
          cuentan hasta que se cumplen o vencen.
        </p>
      </section>

      {invalidRange && (
        <ErrorAlert error="La fecha inicial debe ser anterior o igual a la final." />
      )}
      <ErrorAlert error={error} onRetry={() => void reload()} />

      {loading && !data ? (
        <Loading label="Calculando cumplimiento…" />
      ) : data ? (
        <>
          <div className={styles.kpiRow}>
            {SLA_METRICS.map((m) => {
              const counts = data.totals[m];
              const tone = complianceTone(counts.compliance_pct);
              const accent = tone === 'ok' ? 'success' : tone === 'warning' ? 'warning' : tone === 'breached' ? 'danger' : 'neutral';
              return (
                <div key={m} className={`kpi kpi-accent-${accent}`}>
                  <span className="kpi-label">{SLA_METRIC_LABELS[m]}</span>
                  <span className="kpi-value">{formatPct(counts.compliance_pct)}</span>
                  <span className="kpi-sub">
                    {formatNumber(counts.met)} cumplidos · {formatNumber(counts.breached)} vencidos ·{' '}
                    {formatNumber(counts.running + counts.paused)} en curso
                  </span>
                </div>
              );
            })}
          </div>
          <SummaryTable
            title="Por política"
            rows={data.by_policy.map((r) => ({ ...r, key: r.policy_id, name: r.policy_name }))}
          />
          <SummaryTable
            title="Por departamento"
            rows={data.by_department.map((r) => ({ ...r, key: r.department_id, name: r.department_name }))}
          />
          <p className="text-xs subtle">
            Periodo: {formatDate(`${data.date_from}T12:00:00`)} – {formatDate(`${data.date_to}T12:00:00`)}.
          </p>
        </>
      ) : null}
    </div>
  );
}
