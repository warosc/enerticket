'use client';

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatNumber } from '@/lib/format';
import { formatHours, type ReportByDepartment } from '@/lib/reports';
import { AXIS_PROPS, ChartPanel, ChartTooltip, type LegendEntry } from './ChartPanel';
import styles from './reports.module.css';

const SERIES: (LegendEntry & { dataKey: 'created' | 'resolved' | 'backlog' })[] = [
  { key: 'created', dataKey: 'created', label: 'Creados', color: 'var(--series-1)' },
  { key: 'resolved', dataKey: 'resolved', label: 'Resueltos', color: 'var(--series-2)' },
  { key: 'backlog', dataKey: 'backlog', label: 'Backlog actual', color: 'var(--series-3)' },
];

/** Recorta nombres largos de departamento para el eje (el tooltip muestra el nombre completo). */
function shortName(name: string): string {
  return name.length > 18 ? `${name.slice(0, 17)}…` : name;
}

export default function DepartmentChart({ data, className }: { data: ReportByDepartment; className?: string }) {
  const empty = data.items.every((d) => d.created === 0 && d.resolved === 0 && d.backlog === 0);
  return (
    <ChartPanel
      className={className}
      title="Por departamento"
      subtitle="Creados y resueltos en el periodo; backlog abierto hoy"
      legend={SERIES}
      empty={empty}
      chart={
        <div
          className={styles.chartTall}
          role="img"
          aria-label={`Tickets por departamento: ${data.items
            .map((d) => `${d.department} ${d.created} creados, ${d.resolved} resueltos, ${d.backlog} en backlog`)
            .join('; ')}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.items} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="24%" barGap={2}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis
                dataKey="department"
                {...AXIS_PROPS}
                axisLine={{ stroke: 'var(--chart-grid)' }}
                tickFormatter={shortName}
                interval={0}
              />
              <YAxis allowDecimals={false} axisLine={false} {...AXIS_PROPS} />
              <Tooltip cursor={{ fill: 'var(--color-primary-soft)' }} content={<ChartTooltip />} />
              {SERIES.map((s) => (
                <Bar
                  key={s.key}
                  dataKey={s.dataKey}
                  name={s.label}
                  fill={s.color}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={28}
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
              <th scope="col">Departamento</th>
              <th scope="col" className="col-num">Creados</th>
              <th scope="col" className="col-num">Resueltos</th>
              <th scope="col" className="col-num">Backlog</th>
              <th scope="col" className="col-num">MTTR</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((d) => (
              <tr key={d.department_id}>
                <td>{d.department}</td>
                <td className="col-num">{formatNumber(d.created)}</td>
                <td className="col-num">{formatNumber(d.resolved)}</td>
                <td className="col-num">{formatNumber(d.backlog)}</td>
                <td className="col-num">{formatHours(d.mttr_hours)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  );
}
