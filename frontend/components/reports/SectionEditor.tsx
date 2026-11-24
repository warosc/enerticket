'use client';

/** Editor de una sección del reporte: filas (1–2), columna (0–1), métricas (1–8), totales y filtros propios. */
import { useId } from 'react';
import {
  countActiveFilters,
  REPORT_LIMITS,
  type ReportCatalogOptions,
  type ReportDimensionMeta,
  type ReportMetricMeta,
  type ReportSectionDefinition,
} from '@/lib/reports';
import FiltersEditor from './FiltersEditor';
import styles from './builder.module.css';

const UNIT_HINTS: Record<string, string> = { count: 'cantidad', hours: 'horas', pct: '%' };

export default function SectionEditor({
  index,
  total,
  value,
  onChange,
  onRemove,
  onMove,
  onDuplicate,
  dimensions,
  metrics,
  options,
  canAdd,
}: {
  index: number;
  total: number;
  value: ReportSectionDefinition;
  onChange: (next: ReportSectionDefinition) => void;
  onRemove: () => void;
  onMove: (delta: -1 | 1) => void;
  onDuplicate: () => void;
  dimensions: ReportDimensionMeta[];
  metrics: ReportMetricMeta[];
  options: ReportCatalogOptions;
  canAdd: boolean;
}) {
  const baseId = useId();
  const [row1 = '', row2 = ''] = value.rows;
  const column = value.columns[0] ?? '';
  const activeFilters = countActiveFilters(value.filters);
  const metricKeys = new Set(metrics.map((m) => m.key));
  const unknownMetrics = value.metrics.filter((m) => !metricKeys.has(m));

  const setRows = (first: string, second: string) => {
    const rows = [first, second].filter(Boolean);
    const unique = rows.filter((r, i) => rows.indexOf(r) === i);
    onChange({ ...value, rows: unique, columns: value.columns.filter((c) => !unique.includes(c)) });
  };

  const toggleMetric = (key: string) => {
    if (value.metrics.includes(key)) onChange({ ...value, metrics: value.metrics.filter((m) => m !== key) });
    else if (value.metrics.length < REPORT_LIMITS.maxMetrics) {
      // Conserva el orden del catálogo de métricas.
      const next = [...value.metrics, key];
      const order = new Map(metrics.map((m, i) => [m.key, i]));
      next.sort((a, b) => (order.get(a) ?? 99) - (order.get(b) ?? 99));
      onChange({ ...value, metrics: next });
    }
  };

  const dimensionOptions = (exclude: string[]) =>
    dimensions
      .filter((d) => !exclude.includes(d.key))
      .map((d) => (
        <option key={d.key} value={d.key}>
          {d.label}
        </option>
      ));

  return (
    <section className={`panel ${styles.section}`} aria-labelledby={`${baseId}-title`}>
      <div className={styles.sectionHeader}>
        <h3 className="panel-title" id={`${baseId}-title`}>
          Sección {index + 1}
          {value.title.trim() ? <span className="muted"> · {value.title.trim()}</span> : null}
        </h3>
        <div className="row-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onMove(-1)}
            disabled={index === 0}
            aria-label={`Subir sección ${index + 1}`}
            title="Subir"
          >
            ↑
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onMove(1)}
            disabled={index === total - 1}
            aria-label={`Bajar sección ${index + 1}`}
            title="Bajar"
          >
            ↓
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onDuplicate} disabled={!canAdd}>
            Duplicar
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onRemove} disabled={total <= 1}>
            Quitar
          </button>
        </div>
      </div>

      <div className={styles.sectionGrid}>
        <label className="field">
          <span className="field-label">Título de la sección</span>
          <input
            className="input"
            value={value.title}
            maxLength={120}
            placeholder="Automático (según filas y columnas)"
            onChange={(e) => onChange({ ...value, title: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Filas<span className="required">*</span>
          </span>
          <select className="select" value={row1} onChange={(e) => setRows(e.target.value, row2)}>
            {!row1 && <option value="">Elija una dimensión…</option>}
            {dimensionOptions(row2 ? [row2] : [])}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Subfilas</span>
          <select className="select" value={row2} onChange={(e) => setRows(row1, e.target.value)} disabled={!row1}>
            <option value="">Ninguna</option>
            {dimensionOptions(row1 ? [row1] : [])}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Columnas</span>
          <select
            className="select"
            value={column}
            onChange={(e) => onChange({ ...value, columns: e.target.value ? [e.target.value] : [] })}
          >
            <option value="">Sin columnas (un solo total)</option>
            {dimensionOptions(value.rows)}
          </select>
        </label>
      </div>

      <fieldset className={styles.metricsFieldset}>
        <legend className="field-label">
          Métricas<span className="required">*</span>{' '}
          <span className="field-hint">
            ({value.metrics.length} de máx. {REPORT_LIMITS.maxMetrics})
          </span>
        </legend>
        <div className={styles.metricsGrid}>
          {metrics.map((m) => {
            const checked = value.metrics.includes(m.key);
            return (
              <label key={m.key} className={`checkbox ${styles.metricOption}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && value.metrics.length >= REPORT_LIMITS.maxMetrics}
                  onChange={() => toggleMetric(m.key)}
                />
                <span>
                  {m.label} <span className="subtle text-xs">({UNIT_HINTS[m.unit] ?? m.unit})</span>
                </span>
              </label>
            );
          })}
        </div>
        {unknownMetrics.length > 0 && (
          <div className="field-hint">
            Métricas no disponibles: {unknownMetrics.join(', ')}.{' '}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onChange({ ...value, metrics: value.metrics.filter((m) => metricKeys.has(m)) })}
            >
              Quitar
            </button>
          </div>
        )}
      </fieldset>

      <label className="checkbox">
        <input type="checkbox" checked={value.totals} onChange={(e) => onChange({ ...value, totals: e.target.checked })} />
        Mostrar totales (fila «Total» y columna «Total» / «Acumulado»)
      </label>

      <details className={styles.sectionFilters}>
        <summary>
          Filtros de esta sección
          {activeFilters > 0 ? ` (${activeFilters} activo${activeFilters === 1 ? '' : 's'})` : ' (opcional)'}
        </summary>
        <p className="field-hint">Se aplican además de los filtros generales y solo a esta sección.</p>
        <FiltersEditor value={value.filters} onChange={(filters) => onChange({ ...value, filters })} options={options} />
      </details>
    </section>
  );
}
