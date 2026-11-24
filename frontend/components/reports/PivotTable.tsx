'use client';

/**
 * Tabla dinámica de una sección del ReportResult: filas (1–2 dimensiones) × columnas (0–1 dimensión) ×
 * métricas, con columna «Total» (o «Acumulado» si las columnas son fechas) y fila «Total».
 * Scroll horizontal dentro de su contenedor (no desborda la página en móvil).
 */
import { EmptyState } from '@/components/ui';
import {
  ALL_COLUMNS_KEY,
  formatMetricValue,
  type ReportDimensionMeta,
  type ReportMetricMeta,
  type ReportMetricValues,
  type ReportResultSection,
} from '@/lib/reports';
import styles from './builder.module.css';

export default function PivotTable({
  section,
  dimensions,
  metrics,
}: {
  section: ReportResultSection;
  dimensions: Map<string, ReportDimensionMeta>;
  metrics: Map<string, ReportMetricMeta>;
}) {
  const rowDims = section.row_dims.length ? section.row_dims : ['_row'];
  const metricKeys = section.metrics;
  const hasColumns = Boolean(section.column_dim);
  const columns = section.columns.length ? section.columns : [{ key: ALL_COLUMNS_KEY, label: 'Total' }];
  const multi = metricKeys.length > 1;
  const columnKind = section.column_dim ? dimensions.get(section.column_dim)?.kind : undefined;
  const totalLabel = columnKind === 'date' ? 'Acumulado' : 'Total';
  const totalsEnabled = section.show_totals !== false;
  const showRowTotal = totalsEnabled && hasColumns && section.rows.some((r) => r.total);
  const showFooter = totalsEnabled && Boolean(section.totals || section.grand_total) && section.rows.length > 0;

  const dimLabel = (key: string) => dimensions.get(key)?.label ?? key;
  const metricLabel = (key: string) => metrics.get(key)?.label ?? key;
  const fmt = (values: ReportMetricValues | null | undefined, metric: string) =>
    formatMetricValue(values?.[metric] ?? null, metrics.get(metric)?.unit ?? 'count');

  if (!section.rows.length) {
    return <EmptyState title="Sin datos">No hay tickets que cumplan los filtros en este periodo.</EmptyState>;
  }

  const valueCells = (values: (columnKey: string) => ReportMetricValues | null | undefined, keyPrefix: string) => (
    <>
      {columns.map((col) =>
        metricKeys.map((m, mi) => (
          <td
            key={`${keyPrefix}-${col.key}-${m}`}
            className={`col-num${multi && mi === 0 ? ` ${styles.groupStart}` : ''}`}
          >
            {fmt(values(col.key), m)}
          </td>
        )),
      )}
    </>
  );

  return (
    <div className={styles.pivotWrap} role="region" aria-label={`Tabla: ${section.title}`} tabIndex={0}>
      <table className={`table table-compact ${styles.pivot}`}>
        <thead>
          {hasColumns && multi ? (
            <>
              <tr>
                {rowDims.map((d, i) => (
                  <th key={d} rowSpan={2} scope="col" className={i === 0 ? styles.stickyCol : undefined}>
                    {dimLabel(d)}
                  </th>
                ))}
                {columns.map((col) => (
                  <th key={col.key} colSpan={metricKeys.length} scope="colgroup" className={`text-center ${styles.groupStart}`}>
                    {col.label}
                  </th>
                ))}
                {showRowTotal && (
                  <th colSpan={metricKeys.length} scope="colgroup" className={`text-center ${styles.groupStart} ${styles.totalCol}`}>
                    {totalLabel}
                  </th>
                )}
              </tr>
              <tr>
                {[...columns, ...(showRowTotal ? [{ key: '__total', label: totalLabel }] : [])].map((col) =>
                  metricKeys.map((m, mi) => (
                    <th
                      key={`${col.key}-${m}`}
                      scope="col"
                      className={`text-right ${styles.metricHead}${mi === 0 ? ` ${styles.groupStart}` : ''}${
                        col.key === '__total' ? ` ${styles.totalCol}` : ''
                      }`}
                    >
                      {metricLabel(m)}
                    </th>
                  )),
                )}
              </tr>
            </>
          ) : (
            <tr>
              {rowDims.map((d, i) => (
                <th key={d} scope="col" className={i === 0 ? styles.stickyCol : undefined}>
                  {dimLabel(d)}
                </th>
              ))}
              {hasColumns
                ? columns.map((col) => (
                    <th key={col.key} scope="col" className="text-right">
                      {col.label}
                    </th>
                  ))
                : metricKeys.map((m) => (
                    <th key={m} scope="col" className={`text-right ${styles.metricHead}`}>
                      {metricLabel(m)}
                    </th>
                  ))}
              {showRowTotal && (
                <th scope="col" className={`text-right ${styles.totalCol}`}>
                  {totalLabel}
                </th>
              )}
            </tr>
          )}
        </thead>
        <tbody>
          {section.rows.map((row, ri) => {
            const prev = ri > 0 ? section.rows[ri - 1] : null;
            const sameGroup = rowDims.length > 1 && prev !== null && prev.keys[0]?.key === row.keys[0]?.key;
            return (
              <tr key={row.keys.map((k) => k.key).join('|') || ri} className={!sameGroup && ri > 0 && rowDims.length > 1 ? styles.groupRow : undefined}>
                {rowDims.map((_, di) => {
                  const k = row.keys[di];
                  const hidden = di === 0 && sameGroup;
                  return (
                    <th
                      key={di}
                      scope="row"
                      className={`${styles.rowHead}${di === 0 ? ` ${styles.stickyCol}` : ''}`}
                      aria-label={hidden ? k?.label : undefined}
                    >
                      {hidden ? '' : (k?.label ?? '—')}
                    </th>
                  );
                })}
                {valueCells((ck) => row.cells?.[ck], `r${ri}`)}
                {showRowTotal &&
                  metricKeys.map((m, mi) => (
                    <td
                      key={`t-${m}`}
                      className={`col-num ${styles.totalCol}${mi === 0 ? ` ${styles.groupStart}` : ''}`}
                    >
                      {fmt(row.total, m)}
                    </td>
                  ))}
              </tr>
            );
          })}
        </tbody>
        {showFooter && (
          <tfoot>
            <tr className={styles.totalRow}>
              <th scope="row" colSpan={rowDims.length} className={styles.stickyCol}>
                Total
              </th>
              {hasColumns ? (
                <>
                  {valueCells((ck) => section.totals?.[ck], 'f')}
                  {showRowTotal &&
                    metricKeys.map((m, mi) => (
                      <td key={`g-${m}`} className={`col-num ${styles.totalCol}${mi === 0 ? ` ${styles.groupStart}` : ''}`}>
                        {fmt(section.grand_total, m)}
                      </td>
                    ))}
                </>
              ) : (
                metricKeys.map((m) => (
                  <td key={`g-${m}`} className="col-num">
                    {fmt(section.totals?.[ALL_COLUMNS_KEY] ?? section.grand_total, m)}
                  </td>
                ))
              )}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/** Leyenda de la métrica cuando hay una sola y las columnas son valores de una dimensión. */
export function PivotCaption({
  section,
  metrics,
}: {
  section: ReportResultSection;
  metrics: Map<string, ReportMetricMeta>;
}) {
  if (!section.column_dim || section.metrics.length !== 1) return null;
  const m = section.metrics[0];
  return <span className="text-sm muted">Métrica: {metrics.get(m)?.label ?? m}</span>;
}
