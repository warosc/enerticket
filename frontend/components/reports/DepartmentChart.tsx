'use client';

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatNumber } from '@/lib/format';
import { formatHours, type ReportByDepartment } from '@/lib/reports';
import { AXIS_PROPS, ChartPanel, ChartTooltip, type LegendEntry } from './ChartPanel';

const SERIES: (LegendEntry & { dataKey: 'created' | 'resolved' | 'backlog' })[] = [
  { key: 'created', dataKey: 'created', label: 'Creados', color: 'var(--series-1)' },
  { key: 'resolved', dataKey: 'resolved', label: 'Resueltos', color: 'var(--series-2)' },
  { key: 'backlog', dataKey: 'backlog', label: 'Backlog actual', color: 'var(--series-3)' },
];

/** Recorta nombres largos de departamento para el eje (el tooltip muestra el nombre completo). */
function shortName(name: string): string {
  return name.length > 20 ? `${name.slice(0, 19)}…` : name;
}

/**
 * Etiqueta del eje de departamentos en una sola línea (Recharts parte el texto en varias líneas si
 * no cabe); el nombre completo queda en <title> y en el tooltip de la barra.
 */
function DepartmentTick({ x = 0, y = 0, payload }: { x?: number; y?: number; payload?: { value?: string | number } }) {
  const name = String(payload?.value ?? '');
  return (
    <g transform={`translate(${x},${y})`}>
      <title>{name}</title>
      <text x={-6} y={0} dy={4} textAnchor="end" fontSize={11} fill="var(--chart-axis)">
        {shortName(name)}
      </text>
    </g>
  );
}

/** Alto por departamento (tres barras agrupadas) en el gráfico de barras horizontales. */
const ROW_HEIGHT = 46;

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
          style={{ width: '100%', height: Math.max(320, data.items.length * ROW_HEIGHT + 36) }}
          role="img"
          aria-label={`Tickets por departamento: ${data.items
            .map((d) => `${d.department} ${d.created} creados, ${d.resolved} resueltos, ${d.backlog} en backlog`)
            .join('; ')}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            {/* Barras horizontales: los nombres largos de departamento quedan en el eje vertical sin superponerse. */}
            <BarChart
              data={data.items}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 0, left: 0 }}
              barCategoryGap="18%"
              barGap={1}
            >
              <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
              <XAxis type="number" allowDecimals={false} axisLine={false} {...AXIS_PROPS} />
              <YAxis
                type="category"
                dataKey="department"
                width={142}
                tickLine={false}
                tick={<DepartmentTick />}
                axisLine={{ stroke: 'var(--chart-grid)' }}
                interval={0}
              />
              <Tooltip cursor={{ fill: 'var(--color-primary-soft)' }} content={<ChartTooltip />} />
              {SERIES.map((s) => (
                <Bar
                  key={s.key}
                  dataKey={s.dataKey}
                  name={s.label}
                  fill={s.color}
                  radius={[0, 3, 3, 0]}
                  maxBarSize={12}
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
