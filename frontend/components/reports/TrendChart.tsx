'use client';

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatNumber } from '@/lib/format';
import { formatPeriodLabel, type ReportTrend } from '@/lib/reports';
import { AXIS_PROPS, ChartPanel, ChartTooltip, type LegendEntry } from './ChartPanel';
import styles from './reports.module.css';

const LEGEND: LegendEntry[] = [
  { key: 'created', label: 'Creados', color: 'var(--series-1)', shape: 'line' },
  { key: 'resolved', label: 'Resueltos', color: 'var(--series-2)', shape: 'line' },
];

export default function TrendChart({ data, className }: { data: ReportTrend; className?: string }) {
  const granularity = data.granularity;
  const format = (period: string) => formatPeriodLabel(period, granularity);
  const empty = data.totals.created === 0 && data.totals.resolved === 0;
  const showDots = data.points.length <= 31;

  return (
    <ChartPanel
      className={className}
      title="Creados vs. resueltos"
      subtitle={`${granularity === 'week' ? 'Por semana (inicia lunes)' : 'Por día'} · ${formatNumber(
        data.totals.created,
      )} creados, ${formatNumber(data.totals.resolved)} resueltos`}
      legend={LEGEND}
      empty={empty}
      chart={
        <div
          className={styles.chartTall}
          role="img"
          aria-label={`Tendencia: ${formatNumber(data.totals.created)} tickets creados y ${formatNumber(
            data.totals.resolved,
          )} resueltos en el periodo`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.points} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis
                dataKey="period"
                {...AXIS_PROPS}
                axisLine={{ stroke: 'var(--chart-grid)' }}
                tickFormatter={format}
                minTickGap={24}
              />
              <YAxis allowDecimals={false} axisLine={false} {...AXIS_PROPS} />
              <Tooltip
                cursor={{ stroke: 'var(--color-border-strong)', strokeWidth: 1 }}
                content={<ChartTooltip labelFormatter={format} />}
              />
              <Line
                type="monotone"
                dataKey="created"
                name="Creados"
                stroke="var(--series-1)"
                strokeWidth={2}
                dot={showDots ? { r: 3, strokeWidth: 0, fill: 'var(--series-1)' } : false}
                activeDot={{ r: 5, stroke: 'var(--color-surface)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="resolved"
                name="Resueltos"
                stroke="var(--series-2)"
                strokeWidth={2}
                dot={showDots ? { r: 3, strokeWidth: 0, fill: 'var(--series-2)' } : false}
                activeDot={{ r: 5, stroke: 'var(--color-surface)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      }
      table={
        <table className="table table-compact">
          <thead>
            <tr>
              <th scope="col">{granularity === 'week' ? 'Semana' : 'Día'}</th>
              <th scope="col" className="col-num">Creados</th>
              <th scope="col" className="col-num">Resueltos</th>
              <th scope="col" className="col-num">Acumulado neto</th>
            </tr>
          </thead>
          <tbody>
            {data.points.map((p) => (
              <tr key={p.period}>
                <td>{format(p.period)}</td>
                <td className="col-num">{formatNumber(p.created)}</td>
                <td className="col-num">{formatNumber(p.resolved)}</td>
                <td className="col-num">{p.net > 0 ? `+${formatNumber(p.net)}` : formatNumber(p.net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    />
  );
}
