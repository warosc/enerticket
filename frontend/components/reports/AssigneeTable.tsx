'use client';

import { formatNumber } from '@/lib/format';
import { formatHours, type ReportByAssignee } from '@/lib/reports';
import styles from './reports.module.css';

/** Carga por agente: abiertos asignados (con barra relativa), críticos, resueltos y MTTR. */
export default function AssigneeTable({ data, className }: { data: ReportByAssignee; className?: string }) {
  const maxOpen = Math.max(1, ...data.items.map((i) => i.open));
  const empty = data.items.length === 0;
  return (
    <section className={`panel ${className ?? ''}`}>
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Carga por agente</h2>
          <p className="panel-subtitle">Abiertos asignados hoy y resueltos en el periodo</p>
        </div>
      </div>
      {empty ? (
        <p className="muted text-sm">Sin tickets asignados ni resueltos con los filtros actuales.</p>
      ) : (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th scope="col">Agente</th>
                <th scope="col" className="col-num">Abiertos</th>
                <th scope="col" style={{ width: '30%' }}>
                  <span className="visually-hidden">Carga relativa</span>
                </th>
                <th scope="col" className="col-num">Críticos</th>
                <th scope="col" className="col-num">Resueltos</th>
                <th scope="col" className="col-num">MTTR</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.assignee_id ?? 'unassigned'}>
                  <td className={row.assignee_id === null ? 'muted' : undefined}>{row.assignee}</td>
                  <td className="col-num strong">{formatNumber(row.open)}</td>
                  <td aria-hidden="true">
                    <div className={styles.meter}>
                      <div className={styles.meterFill} style={{ width: `${(row.open / maxOpen) * 100}%` }} />
                    </div>
                  </td>
                  <td className="col-num">
                    {row.open_critical > 0 ? (
                      <span className="badge badge-priority-critical">{formatNumber(row.open_critical)}</span>
                    ) : (
                      <span className="subtle">0</span>
                    )}
                  </td>
                  <td className="col-num">{formatNumber(row.resolved)}</td>
                  <td className="col-num">{formatHours(row.mttr_hours)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.truncated && <p className="text-xs subtle">Se muestran los primeros 100 agentes por carga.</p>}
        </div>
      )}
    </section>
  );
}
