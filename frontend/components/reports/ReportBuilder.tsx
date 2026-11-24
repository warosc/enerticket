'use client';

/**
 * Pestaña «Generador»: periodo y campo de fecha, filtros generales, secciones (filas × columnas × métricas),
 * vista previa en tabla dinámica, exportación a Excel y guardado como plantilla.
 */
import { useRef, useState } from 'react';
import { Alert, EmptyState, ErrorAlert } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { formatDate } from '@/lib/format';
import {
  countActiveFilters,
  createReportTemplate,
  emptyReportFilters,
  exportReportXlsx,
  newReportDefinition,
  newReportSection,
  periodRange,
  REPORT_LIMITS,
  REPORT_PERIODS,
  runReport,
  todayIso,
  updateReportTemplate,
  validateReportDefinition,
  type ReportCatalogOptions,
  type ReportDateFieldMeta,
  type ReportDefinition,
  type ReportDimensionMeta,
  type ReportMetricMeta,
  type ReportPeriod,
  type ReportResult,
  type ReportSectionDefinition,
  type ReportTemplate,
  type ReportTemplateInput,
} from '@/lib/reports';
import FiltersEditor from './FiltersEditor';
import ReportResultView from './ReportResultView';
import SectionEditor from './SectionEditor';
import TemplateForm from './TemplateForm';
import styles from './builder.module.css';

type SaveMode = 'update' | 'create' | null;

function displayDate(iso: string): string {
  return formatDate(`${iso}T12:00:00Z`);
}

export default function ReportBuilder({
  definition,
  onDefinitionChange,
  template,
  onTemplateChange,
  canEditTemplate,
  onTemplatesChanged,
  dimensions,
  metrics,
  dateFields,
  options,
  canSchedule,
}: {
  definition: ReportDefinition;
  onDefinitionChange: (next: ReportDefinition) => void;
  /** Plantilla que se está editando (null = definición libre). */
  template: ReportTemplate | null;
  onTemplateChange: (next: ReportTemplate | null) => void;
  canEditTemplate: (template: ReportTemplate) => boolean;
  onTemplatesChanged: () => void;
  dimensions: ReportDimensionMeta[];
  metrics: ReportMetricMeta[];
  dateFields: ReportDateFieldMeta[];
  options: ReportCatalogOptions;
  canSchedule: boolean;
}) {
  const [result, setResult] = useState<ReportResult | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [includeDetail, setIncludeDetail] = useState(true);
  const [validation, setValidation] = useState<string[]>([]);
  const [saveMode, setSaveMode] = useState<SaveMode>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  const dimensionMap = new Map(dimensions.map((d) => [d.key, d]));
  const metricMap = new Map(metrics.map((m) => [m.key, m]));
  const globalFilters = countActiveFilters(definition.filters);
  const editable = template ? canEditTemplate(template) : false;

  const set = (patch: Partial<ReportDefinition>) => {
    setValidation([]);
    onDefinitionChange({ ...definition, ...patch });
  };

  const setSections = (sections: ReportSectionDefinition[]) => set({ sections });

  const updateSection = (index: number, next: ReportSectionDefinition) =>
    setSections(definition.sections.map((s, i) => (i === index ? next : s)));

  const moveSection = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= definition.sections.length) return;
    const next = [...definition.sections];
    [next[index], next[target]] = [next[target], next[index]];
    setSections(next);
  };

  const setPeriod = (period: ReportPeriod) => {
    if (period === 'custom') {
      // Parte del rango que se estaba viendo para no perder el contexto.
      const range = definition.period === 'custom' ? null : periodRange(definition.period);
      set({ period, date_from: range?.from ?? definition.date_from, date_to: range?.to ?? definition.date_to });
    } else {
      const range = periodRange(period);
      set({ period, date_from: range.from, date_to: range.to });
    }
  };

  const check = (): boolean => {
    const errors = validateReportDefinition(definition);
    setValidation(errors);
    return errors.length === 0;
  };

  const preview = async () => {
    if (!check()) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setRunning(true);
    setRunError(null);
    try {
      const data = await runReport(definition, controller.signal);
      if (controller.signal.aborted) return;
      setResult(data);
      window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    } catch (e) {
      if (controller.signal.aborted) return;
      setRunError(errorMessage(e));
    } finally {
      if (!controller.signal.aborted) setRunning(false);
    }
  };

  const exportXlsx = async () => {
    if (!check()) return;
    setExporting(true);
    setExportError(null);
    try {
      await exportReportXlsx(definition, includeDetail);
    } catch (e) {
      setExportError(`No se pudo exportar: ${errorMessage(e)}`);
    } finally {
      setExporting(false);
    }
  };

  const openSave = (mode: Exclude<SaveMode, null>) => {
    if (!check()) return;
    setNotice(null);
    setSaveMode(mode);
  };

  const save = async (input: Omit<ReportTemplateInput, 'definition'>) => {
    if (saveMode === 'update' && template) {
      const saved = await updateReportTemplate(template.id, { ...input, definition });
      onTemplateChange(saved);
      setNotice(`Se guardaron los cambios en «${saved.name}».`);
    } else {
      const saved = await createReportTemplate({ ...input, definition });
      onTemplateChange(saved);
      setNotice(`Se creó la plantilla «${saved.name}».`);
    }
    setSaveMode(null);
    onTemplatesChanged();
  };

  const reset = () => {
    if (!window.confirm('¿Descartar la definición actual y empezar un reporte nuevo?')) return;
    controllerRef.current?.abort();
    onTemplateChange(null);
    onDefinitionChange(newReportDefinition());
    setResult(null);
    setRunError(null);
    setValidation([]);
    setSaveMode(null);
    setNotice(null);
  };

  const presetRange = definition.period === 'custom' ? null : periodRange(definition.period);
  const today = todayIso();

  return (
    <div className="stack-lg">
      {template && (
        <Alert
          tone="info"
          title={editable ? `Editando la plantilla «${template.name}»` : `Basado en la plantilla «${template.name}»`}
          action={
            <button type="button" className="btn btn-sm" onClick={() => onTemplateChange(null)}>
              Desvincular
            </button>
          }
        >
          {editable
            ? 'Use «Guardar cambios» para actualizarla o «Guardar como nueva» para crear una copia.'
            : 'No puede modificar esta plantilla; guarde sus cambios como una plantilla nueva.'}
        </Alert>
      )}
      {notice && (
        <Alert
          tone="success"
          action={
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setNotice(null)}>
              Cerrar
            </button>
          }
        >
          {notice}
        </Alert>
      )}

      <section className="panel stack" aria-label="Datos generales del reporte">
        <div className={styles.generalGrid}>
          <label className={`field ${styles.titleField}`}>
            <span className="field-label">
              Título del reporte<span className="required">*</span>
            </span>
            <input
              className="input"
              value={definition.title}
              maxLength={200}
              onChange={(e) => set({ title: e.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Periodo</span>
            <select className="select" value={definition.period} onChange={(e) => setPeriod(e.target.value as ReportPeriod)}>
              {REPORT_PERIODS.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Fecha de referencia</span>
            <select
              className="select"
              value={definition.date_field}
              onChange={(e) => set({ date_field: e.target.value as ReportDefinition['date_field'] })}
            >
              {dateFields.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          {definition.period === 'custom' && (
            <>
              <label className="field">
                <span className="field-label">Desde</span>
                <input
                  type="date"
                  className="input"
                  value={definition.date_from}
                  max={definition.date_to || today}
                  onChange={(e) => set({ date_from: e.target.value })}
                />
              </label>
              <label className="field">
                <span className="field-label">Hasta</span>
                <input
                  type="date"
                  className="input"
                  value={definition.date_to}
                  min={definition.date_from}
                  onChange={(e) => set({ date_to: e.target.value })}
                />
              </label>
            </>
          )}
        </div>
        <p className="field-hint">
          {presetRange
            ? `Del ${displayDate(presetRange.from)} al ${displayDate(presetRange.to)} (se recalcula al ejecutar; útil para plantillas programadas).`
            : `Rango máximo de ${REPORT_LIMITS.maxRangeDays} días. Fechas en hora de Guatemala, ambas incluidas.`}
        </p>
      </section>

      <section className="panel" aria-labelledby="report-global-filters">
        <div className="panel-header">
          <div>
            <h2 className="panel-title" id="report-global-filters">
              Filtros generales
            </h2>
            <div className="panel-subtitle">
              Se aplican a todas las secciones y solo reducen lo que usted puede ver.
              {globalFilters > 0 ? ` ${globalFilters} activo${globalFilters === 1 ? '' : 's'}.` : ''}
            </div>
          </div>
          {globalFilters > 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => set({ filters: emptyReportFilters() })}
            >
              Limpiar filtros
            </button>
          )}
        </div>
        <FiltersEditor value={definition.filters} onChange={(filters) => set({ filters })} options={options} />
      </section>

      <div className="stack">
        <div className="row-between">
          <h2 className="panel-title">
            Secciones <span className="muted text-sm">({definition.sections.length} de máx. {REPORT_LIMITS.maxSections})</span>
          </h2>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={definition.sections.length >= REPORT_LIMITS.maxSections}
            onClick={() => setSections([...definition.sections, newReportSection(definition.sections.length)])}
          >
            Agregar sección
          </button>
        </div>
        {definition.sections.map((section, index) => (
          <SectionEditor
            key={index}
            index={index}
            total={definition.sections.length}
            value={section}
            onChange={(next) => updateSection(index, next)}
            onRemove={() => setSections(definition.sections.filter((_, i) => i !== index))}
            onMove={(delta) => moveSection(index, delta)}
            onDuplicate={() =>
              setSections([
                ...definition.sections.slice(0, index + 1),
                { ...section, title: `${section.title} (copia)`, filters: { ...section.filters } },
                ...definition.sections.slice(index + 1),
              ])
            }
            canAdd={definition.sections.length < REPORT_LIMITS.maxSections}
            dimensions={dimensions}
            metrics={metrics}
            options={options}
          />
        ))}
      </div>

      {validation.length > 0 && (
        <Alert tone="warning" title="Revise la definición">
          <ul className={styles.errorList}>
            {validation.map((msg) => (
              <li key={msg}>{msg}</li>
            ))}
          </ul>
        </Alert>
      )}

      <section className={`panel ${styles.actionBar}`} aria-label="Acciones del reporte">
        <div className="row">
          <button type="button" className="btn btn-primary" onClick={() => void preview()} disabled={running}>
            {running ? 'Calculando…' : 'Vista previa'}
          </button>
          <button type="button" className="btn" onClick={() => void exportXlsx()} disabled={exporting}>
            {exporting ? 'Exportando…' : 'Exportar Excel'}
          </button>
          <label className="checkbox text-sm">
            <input type="checkbox" checked={includeDetail} onChange={(e) => setIncludeDetail(e.target.checked)} />
            Incluir hoja «Detalle» (un ticket por fila)
          </label>
        </div>
        <div className="row">
          {template && editable ? (
            <>
              <button type="button" className="btn btn-secondary" onClick={() => openSave('update')}>
                Guardar cambios
              </button>
              <button type="button" className="btn" onClick={() => openSave('create')}>
                Guardar como nueva
              </button>
            </>
          ) : (
            <button type="button" className="btn btn-secondary" onClick={() => openSave('create')}>
              Guardar como plantilla
            </button>
          )}
          <button type="button" className="btn btn-ghost" onClick={reset}>
            Nuevo reporte
          </button>
        </div>
      </section>

      {saveMode && (
        <TemplateForm
          key={`${saveMode}-${template?.id ?? 'new'}`}
          template={saveMode === 'update' ? template : null}
          defaultName={saveMode === 'create' && template ? `${template.name} (copia)` : definition.title}
          canSchedule={saveMode === 'update' && typeof template?.can_schedule === 'boolean' ? template.can_schedule : canSchedule}
          options={options}
          onSubmit={save}
          onCancel={() => setSaveMode(null)}
        />
      )}

      <ErrorAlert error={exportError} />
      <ErrorAlert error={runError} onRetry={() => void preview()} />

      <div ref={resultRef} className={styles.resultAnchor}>
        {result ? (
          <ReportResultView
            result={result}
            dimensions={dimensionMap}
            metrics={metricMap}
            busy={running}
            actions={
              <button type="button" className="btn btn-sm" onClick={() => void exportXlsx()} disabled={exporting}>
                {exporting ? 'Exportando…' : 'Exportar Excel'}
              </button>
            }
          />
        ) : (
          <section className="panel">
            <EmptyState title="Sin vista previa">
              Configure las secciones y pulse «Vista previa» para ver la tabla dinámica.
            </EmptyState>
          </section>
        )}
      </div>
    </div>
  );
}
