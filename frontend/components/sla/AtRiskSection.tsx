'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { EmptyState, ErrorAlert, Loading, PriorityBadge, StatusBadge } from '@/components/ui';
import { formatDateTime, formatNumber, formatRelative } from '@/lib/format';
import { useApiResource, useCatalog } from '@/lib/hooks';
import { useTicketEvents } from '@/lib/realtime';
import {
  SLA_METRIC_LABELS,
  TONE_BADGE_CLASS,
  formatBusinessDuration,
  type SlaAtRisk,
  type SlaAtRiskItem,
} from '@/lib/sla';
import styles from './sla.module.css';

const REFRESH_MS = 60_000;

function remainingText(item: SlaAtRiskItem): string {
  const remaining = item.remaining_business_seconds;
  if (remaining === null) return '—';
  if (remaining < 0) return `Excedido por ${formatBusinessDuration(remaining)}`;
  return `Restan ${formatBusinessDuration(remaining)}`;
}

/** Tickets visibles con métricas vencidas o que superaron el umbral de advertencia. */
export default function AtRiskSection() {
  const { catalog } = useCatalog();
  const [departmentId, setDepartmentId] = useState<number | ''>('');
  const [mine, setMine] = useState(false);
  const query = useMemo(
    () => ({ department_id: departmentId === '' ? undefined : departmentId, mine: mine || undefined, limit: 200 }),
    [departmentId, mine],
  );
  const { data, error, loading, reload } = useApiResource<SlaAtRisk>('/sla/at-risk', query);

  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') void reload({ silent: true });
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  useTicketEvents(
    () => {
      void reload({ silent: true });
    },
    { types: ['sla.warning', 'sla.breached', 'ticket.transitioned', 'ticket.updated'] },
  );

  const departments = useMemo(
    () => [...(catalog?.departments ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'es')),
    [catalog],
  );

  return (
    <div className="stack">
      <div className={styles.kpiRow}>
        <div className="kpi kpi-accent-danger">
          <span className="kpi-label">Métricas vencidas</span>
          <span className="kpi-value">{data ? formatNumber(data.counts.breached) : '—'}</span>
          <span className="kpi-sub">Objetivo superado y ticket aún abierto</span>
        </div>
        <div className="kpi kpi-accent-warning">
          <span className="kpi-label">En riesgo</span>
          <span className="kpi-value">{data ? formatNumber(data.counts.at_risk) : '—'}</span>
          <span className="kpi-sub">Superaron el umbral de advertencia</span>
        </div>
      </div>

      <section className="panel panel-flush">
        <div className="toolbar" style={{ padding: 'var(--space-4) var(--space-5)' }}>
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
          <label className="checkbox" style={{ alignSelf: 'flex-end', minHeight: 38 }}>
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
            Solo asignados a mí
          </label>
          <div className="spacer" />
          <button
            type="button"
            className="btn"
            style={{ alignSelf: 'flex-end' }}
            onClick={() => void reload()}
            disabled={loading}
          >
            Actualizar
          </button>
        </div>

        <ErrorAlert error={error} onRetry={() => void reload()} />

        {loading && !data ? (
          <Loading label="Buscando tickets en riesgo…" />
        ) : !data || data.items.length === 0 ? (
          !error && (
            <EmptyState title="Sin tickets en riesgo">
              Ningún ticket visible para usted tiene objetivos vencidos o próximos a vencer.
            </EmptyState>
          )
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Ticket</th>
                  <th scope="col">Métrica</th>
                  <th scope="col">Situación</th>
                  <th scope="col">Vencimiento</th>
                  <th scope="col">Prioridad</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Asignado</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => {
                  const breached = item.status === 'breached';
                  const tone = breached ? 'breached' : item.paused ? 'paused' : 'warning';
                  return (
                    <tr key={item.instance_id}>
                      <td className="col-title">
                        <Link href={`/tickets/${item.ticket.id}`} className="mono text-xs">
                          {item.ticket.key}
                        </Link>
                        <div>
                          <Link href={`/tickets/${item.ticket.id}`} style={{ color: 'inherit', fontWeight: 500 }}>
                            {item.ticket.title}
                          </Link>
                        </div>
                        <div className="text-xs subtle">
                          {item.ticket.department.name}
                          {item.policy_name ? ` · ${item.policy_name}` : ''}
                        </div>
                      </td>
                      <td className="text-sm nowrap">{SLA_METRIC_LABELS[item.metric]}</td>
                      <td>
                        <span className={`badge ${TONE_BADGE_CLASS[tone]}`}>
                          {breached ? 'Vencido' : item.paused ? 'En pausa' : 'En riesgo'}
                        </span>
                        <div className="text-xs muted nowrap" style={{ marginTop: 4 }}>
                          {remainingText(item)}
                        </div>
                      </td>
                      <td className="text-sm nowrap" title={item.target_at ? formatDateTime(item.target_at) : undefined}>
                        {item.target_at ? formatRelative(item.target_at) : '—'}
                        <div className="text-xs subtle">objetivo {formatBusinessDuration(item.target_minutes * 60)}</div>
                      </td>
                      <td>
                        <PriorityBadge priority={item.ticket.priority} />
                      </td>
                      <td>
                        <StatusBadge status={item.ticket.status} />
                      </td>
                      <td className="text-sm">
                        {item.ticket.assignee?.display_name ?? <span className="subtle">Sin asignar</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {data && (
          <div className="panel-footer text-xs subtle" style={{ padding: 'var(--space-3) var(--space-5)' }}>
            Calculado {formatDateTime(data.computed_at)} · tiempos en horario hábil de cada política · se actualiza cada
            minuto.
          </div>
        )}
      </section>
    </div>
  );
}
