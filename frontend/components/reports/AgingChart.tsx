'use client';

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatNumber } from '@/lib/format';
import { formatHours, type ReportAging } from '@/lib/reports';
import { AXIS_PROPS, ChartPanel, ChartTooltip, type LegendEntry } from './ChartPanel';
import styles from './reports.module.css';

/** Prioridad = escala ordinal: un solo tono, de claro (baja) a oscuro (crítica). */
const ORDINAL = ['var(--ordinal-1)', 'var(--ordinal-2)', 'var(--ordinal-3)', 'var(--ordinal-4)'];

function colorFor(index: number, count: number): string {
  if (count <= 1) return ORDINAL[ORDINAL.length - 1];
  const step = Math.round((index * (ORDINAL.length - 1)) / (count - 1));
  return ORDINAL[Math.min(ORDINAL.length - 1, step)];
}

export default function AgingChart({ data, className }: { data: ReportAging; className?: string }) {
  const priorities = data.priorities;
  const legend: LegendEntry[] = priorities.map((p, i) => ({
    key: p.key,
    label: p.name,
    color: colorFor(i, priorities.length),
  }));
  const rows = data.items.map((item) => ({ label: item.label, total: item.total, ...item.by_priority }));
  const lastIndex = priorities.length - 1;

  return (
    <ChartPanel
      className={className}
      title="Antigüedad del backlog"
      subtitle={`${formatNumber(data.total)} tickets abiertos hoy${
        data.oldest_age_hours !== null ? ` · el más antiguo: ${formatHours(data.oldest_age_hours)}` : ''
      }`}
      legend={legend}
      empty={data.total === 0}
      emptyText="No hay tickets abiertos con los filtros actuales."
      chart={
        <div
          className={styles.chartTall}
          role="img"
          aria-label={`Backlog por antigüedad: ${data.items.map((i) => `${i.label} ${i.total}`).join(', ')}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="label" {...AXIS_PROPS} axisLine={{ stroke: 'var(--chart-grid)' }} interval={0} />
              <YAxis allowDecimals={false} axisLine={false} {...AXIS_PROPS} />
              <Tooltip cursor={{ fill: 'var(--color-primary-soft)' }} content={<ChartTooltip />} />
              {priorities.map((p, i) => (
                <Bar
                  key={p.key}
                  dataKey={p.key}
                  name={p.name}
                  stackId="aging"
                  fill={colorFor(i, priorities.length)}
                  stroke="var(--color-surface)"
                  strokeWidth={1}
                  radius={i === lastIndex ? [4, 4, 0, 0] : 0}
                  maxBarSize={56}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      }
      table={
        <table className="table table-compact">
          <thead>
            <tr>
              <th scope="col">Antigüedad</th>
              {priorities.map((p) => (
                <th key={p.key} scope="col" className="col-num">
                  {p.name}
                </th>
              ))}
              <th scope="col" className="col-num">Total</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.bucket}>
                <td>{item.label}</td>
                {priorities.map((p) => (
                  <td key={p.key} className="col-num">
                    {formatNumber(item.by_priority[p.key] ?? 0)}
                  </td>
                ))}
                <td className="col-num strong">{formatNumber(item.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  );
}
