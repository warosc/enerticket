'use client';

/**
 * Pestaña «Plantillas»: propias, compartidas conmigo y de fábrica. Acciones: ejecutar (vista previa),
 * exportar Excel, duplicar, editar en el generador y eliminar (dueño o admin; las de fábrica solo se duplican).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import {
  deleteReportTemplate,
  describeSchedule,
  duplicateReportTemplate,
  exportReportTemplateXlsx,
  listReportTemplates,
  REPORT_PERIODS,
  ROLE_LABELS,
  runReportTemplate,
  TEMPLATE_STATUS_LABELS,
  templateOwnerId,
  type ReportCatalogOptions,
  type ReportDimensionMeta,
  type ReportMetricMeta,
  type ReportResult,
  type ReportTemplate,
} from '@/lib/reports';
import ReportResultView from './ReportResultView';
import styles from './builder.module.css';

interface Group {
  key: 'mine' | 'shared' | 'system';
  title: string;
  empty: string;
  items: ReportTemplate[];
}

export default function TemplatesView({
  meId,
  isAdmin,
  canEditTemplate,
  version,
  onOpen,
  onDeleted,
  dimensions,
  metrics,
  options,
}: {
  meId: number | null;
  isAdmin: boolean;
  canEditTemplate: (template: ReportTemplate) => boolean;
  /** Cambia cuando otra pestaña crea o modifica plantillas (fuerza recarga). */
  version: number;
  /** Abre la plantilla en el generador (editable o como base). */
  onOpen: (template: ReportTemplate) => void;
  onDeleted: (id: number) => void;
  dimensions: ReportDimensionMeta[];
  metrics: ReportMetricMeta[];
  options: ReportCatalogOptions;
}) {
  const [templates, setTemplates] = useState<ReportTemplate[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [result, setResult] = useState<{ template: ReportTemplate; data: ReportResult } | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    try {
      const items = await listReportTemplates(controller.signal);
      if (controller.signal.aborted) return;
      setTemplates(items);
      setError(null);
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(`No se pudieron cargar las plantillas: ${errorMessage(e)}`);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load, version]);

  const dimensionMap = new Map(dimensions.map((d) => [d.key, d]));
  const metricMap = new Map(metrics.map((m) => [m.key, m]));
  const departmentName = new Map(options.departments.map((d) => [d.id, d.name]));

  const act = async (key: string, fn: () => Promise<void>) => {
    setBusyId(key);
    setActionError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const run = (t: ReportTemplate) =>
    act(`run-${t.id}`, async () => {
      const data = await runReportTemplate(t.id);
      setResult({ template: t, data });
      window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    });

  const exportXlsx = (t: ReportTemplate) => act(`export-${t.id}`, () => exportReportTemplateXlsx(t));

  const duplicate = (t: ReportTemplate) =>
    act(`dup-${t.id}`, async () => {
      const copy = await duplicateReportTemplate(t.id);
      setNotice(`Se creó «${copy.name}» en «Mis plantillas».`);
      await load();
    });

  const remove = (t: ReportTemplate) => {
    if (!window.confirm(`¿Eliminar la plantilla «${t.name}»? Esta acción no se puede deshacer.`)) return;
    void act(`del-${t.id}`, async () => {
      await deleteReportTemplate(t.id);
      if (result?.template.id === t.id) setResult(null);
      onDeleted(t.id);
      setNotice(`Se eliminó «${t.name}».`);
      await load();
    });
  };

  if (loading && !templates) return <Loading label="Cargando plantillas…" />;

  const all = templates ?? [];
  const groups: Group[] = [
    {
      key: 'mine',
      title: 'Mis plantillas',
      empty: 'Aún no tiene plantillas. Cree una desde el Generador o duplique una de fábrica.',
      items: all.filter((t) => !t.is_system && meId !== null && templateOwnerId(t) === meId),
    },
    {
      key: 'shared',
      // El administrador ve todas las plantillas; el resto, las compartidas por rol o departamento.
      title: isAdmin ? 'De otros usuarios' : 'Compartidas conmigo',
      empty: isAdmin ? 'Ningún otro usuario ha creado plantillas.' : 'Nadie ha compartido plantillas con usted.',
      items: all.filter((t) => !t.is_system && (meId === null || templateOwnerId(t) !== meId)),
    },
    {
      key: 'system',
      title: 'De fábrica',
      empty: 'No hay plantillas de fábrica.',
      items: all.filter((t) => t.is_system),
    },
  ];

  const sharing = (t: ReportTemplate): string => {
    const parts = [
      ...t.shared_roles.map((r) => ROLE_LABELS[r] ?? r),
      ...t.shared_department_ids.map((id) => departmentName.get(id) ?? `Departamento #${id}`),
    ];
    return parts.length ? parts.join(', ') : 'Solo usted';
  };

  const periodLabel = (t: ReportTemplate) =>
    REPORT_PERIODS.find((p) => p.key === t.definition.period)?.label ?? t.definition.period;

  return (
    <div className="stack-lg">
      <ErrorAlert error={error} onRetry={() => void load()} />
      <ErrorAlert error={actionError} />
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

      {groups.map((group) => (
        <section key={group.key} className="panel panel-flush" aria-labelledby={`tpl-group-${group.key}`}>
          <div className="panel-header">
            <h2 className="panel-title" id={`tpl-group-${group.key}`}>
              {group.title} <span className="muted text-sm">({group.items.length})</span>
            </h2>
          </div>
          {group.items.length === 0 ? (
            <EmptyState title="Sin plantillas">{group.empty}</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table table-stack">
                <thead>
                  <tr>
                    <th scope="col">Plantilla</th>
                    <th scope="col">Periodo</th>
                    {group.key !== 'system' && <th scope="col">Compartida con</th>}
                    <th scope="col">Envío programado</th>
                    <th scope="col" className="text-right">
                      Acciones
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map((t) => {
                    const editable = canEditTemplate(t);
                    const schedule = describeSchedule(t.schedule);
                    const status = t.last_status ? (TEMPLATE_STATUS_LABELS[t.last_status] ?? t.last_status) : null;
                    return (
                      <tr key={t.id}>
                        <td className="cell-head">
                          <div className="strong">{t.name}</div>
                          {t.description && <div className="text-xs muted">{t.description}</div>}
                          <div className="text-xs subtle">
                            {t.definition.sections.length} sección{t.definition.sections.length === 1 ? '' : 'es'}
                            {group.key === 'shared' && t.owner?.display_name ? ` · de ${t.owner.display_name}` : ''}
                          </div>
                        </td>
                        <td data-label="Periodo" className="nowrap">
                          {periodLabel(t)}
                        </td>
                        {group.key !== 'system' && <td data-label="Compartida con">{sharing(t)}</td>}
                        <td data-label="Envío">
                          {schedule ? (
                            <div className="stack-sm" style={{ gap: 2 }}>
                              <span>{schedule}</span>
                              {t.next_run_at && (
                                <span className="text-xs subtle">Próximo: {formatDateTime(t.next_run_at)}</span>
                              )}
                              {t.last_run_at && (
                                <span className="text-xs subtle">
                                  Último: {formatDateTime(t.last_run_at)}
                                  {status ? ` · ${status}` : ''}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="subtle">—</span>
                          )}
                        </td>
                        <td className="cell-actions">
                          <div className={`row-actions ${styles.templateActions}`}>
                            <button
                              type="button"
                              className="btn btn-sm btn-primary"
                              onClick={() => void run(t)}
                              disabled={busyId !== null}
                            >
                              {busyId === `run-${t.id}` ? 'Ejecutando…' : 'Ejecutar'}
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => void exportXlsx(t)}
                              disabled={busyId !== null}
                            >
                              {busyId === `export-${t.id}` ? 'Exportando…' : 'Exportar'}
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => void duplicate(t)}
                              disabled={busyId !== null}
                            >
                              Duplicar
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => onOpen(t)}
                              disabled={busyId !== null}
                              title={editable ? 'Editar en el generador' : 'Abrir en el generador como base de un reporte nuevo'}
                            >
                              {editable ? 'Editar' : 'Abrir'}
                            </button>
                            {editable && (
                              <button
                                type="button"
                                className="btn btn-sm btn-ghost"
                                onClick={() => remove(t)}
                                disabled={busyId !== null}
                              >
                                Eliminar
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}

      <div ref={resultRef} className={styles.resultAnchor}>
        {result && (
          <ReportResultView
            result={result.data}
            dimensions={dimensionMap}
            metrics={metricMap}
            actions={
              <>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => void exportXlsx(result.template)}
                  disabled={busyId !== null}
                >
                  Exportar Excel
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setResult(null)}>
                  Cerrar
                </button>
              </>
            }
          />
        )}
      </div>
    </div>
  );
}
