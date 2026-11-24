'use client';

import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatNumber } from '@/lib/format';

export interface CountDatum {
  key: string;
  label: string;
  count: number;
}

interface TooltipPayload {
  payload?: CountDatum;
}

function CountTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  const datum = active ? payload?.[0]?.payload : undefined;
  if (!datum) return null;
  return (
    <div
      className="panel"
      style={{ padding: '8px 12px', boxShadow: 'var(--shadow-md)', fontSize: 'var(--text-sm)' }}
    >
      <div className="strong">{datum.label}</div>
      <div className="muted">{formatNumber(datum.count)} tickets</div>
    </div>
  );
}

/** Barras de una sola serie (conteos) con alternativa de tabla accesible. */
export default function CountBarChart({
  title,
  subtitle,
  data,
  emptyText = 'Sin datos para mostrar.',
}: {
  title: string;
  subtitle?: string;
  data: CountDatum[];
  emptyText?: string;
}) {
  const [asTable, setAsTable] = useState(false);
  const total = data.reduce((sum, d) => sum + d.count, 0);

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">{title}</h2>
          {subtitle && <p className="panel-subtitle">{subtitle}</p>}
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAsTable((v) => !v)}>
          {asTable ? 'Ver gráfico' : 'Ver tabla'}
        </button>
      </div>
      {total === 0 ? (
        <p className="muted text-sm">{emptyText}</p>
      ) : asTable ? (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th scope="col">Categoría</th>
                <th scope="col" className="col-num">
                  Tickets
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.key}>
                  <td>{d.label}</td>
                  <td className="col-num">{formatNumber(d.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-box" role="img" aria-label={`${title}: ${data.map((d) => `${d.label} ${d.count}`).join(', ')}`}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={{ stroke: 'var(--chart-grid)' }}
                tick={{ fontSize: 11, fill: 'var(--chart-axis)' }}
                interval={0}
              />
              <YAxis
                allowDecimals={false}
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: 'var(--chart-axis)' }}
              />
              <Tooltip cursor={{ fill: 'var(--color-primary-soft)' }} content={<CountTooltip />} />
              <Bar dataKey="count" fill="var(--chart-series-1)" radius={[4, 4, 0, 0]} maxBarSize={44} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
