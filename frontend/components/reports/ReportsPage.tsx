'use client';

/**
 * Página /reports: pestañas «Resumen» (tablero actual), «Generador» y «Plantillas».
 * El alcance (`scope.label` de /reports/builder/meta) se muestra siempre: todo reporte se calcula solo sobre
 * los tickets que el usuario puede ver.
 */
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ErrorAlert, PageHeader } from '@/components/ui';
import { ApiError, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useCatalog } from '@/lib/hooks';
import {
  DEFAULT_DATE_FIELDS,
  DEFAULT_DIMENSIONS,
  DEFAULT_METRICS,
  fetchBuilderMeta,
  leadTeamIds,
  newReportDefinition,
  normalizeReportDefinition,
  reportCatalogOptions,
  templateOwnerId,
  type ReportBuilderMeta,
  type ReportDefinition,
  type ReportTemplate,
} from '@/lib/reports';
import ReportBuilder from './ReportBuilder';
import ReportsView from './ReportsView';
import TemplatesView from './TemplatesView';
import styles from './builder.module.css';

type ReportsTab = 'summary' | 'builder' | 'templates';

const TABS: Array<{ key: ReportsTab; label: string }> = [
  { key: 'summary', label: 'Resumen' },
  { key: 'builder', label: 'Generador' },
  { key: 'templates', label: 'Plantillas' },
];

function isTab(value: string | null): value is ReportsTab {
  return TABS.some((t) => t.key === value);
}

export default function ReportsPage() {
  const { user, isAdmin } = useAuth();
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const requested = params?.get('tab') ?? null;
  const tab: ReportsTab = isTab(requested) ? requested : 'summary';

  const [meta, setMeta] = useState<ReportBuilderMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [definition, setDefinition] = useState<ReportDefinition>(() => newReportDefinition());
  const [template, setTemplate] = useState<ReportTemplate | null>(null);
  const [templatesVersion, setTemplatesVersion] = useState(0);
  // Generador y Plantillas se montan al visitarlas y se conservan (no se pierde el borrador al cambiar de pestaña).
  const [visited, setVisited] = useState<Set<ReportsTab>>(() => new Set([tab]));

  useEffect(() => {
    setVisited((prev) => (prev.has(tab) ? prev : new Set(prev).add(tab)));
  }, [tab]);

  const loadMeta = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await fetchBuilderMeta(signal);
      if (signal?.aborted) return;
      setMeta(data);
      setMetaError(null);
    } catch (e) {
      if (signal?.aborted) return;
      setMetaError(
        e instanceof ApiError && e.isForbidden
          ? 'No tiene permisos para generar reportes.'
          : `No se pudo cargar la configuración del generador: ${errorMessage(e)}`,
      );
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadMeta(controller.signal);
    return () => controller.abort();
  }, [loadMeta]);

  const selectTab = useCallback(
    (next: ReportsTab) => {
      const query = new URLSearchParams(params?.toString() ?? '');
      if (next === 'summary') query.delete('tab');
      else query.set('tab', next);
      const qs = query.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const options = useMemo(() => reportCatalogOptions(catalog), [catalog]);
  const dimensions = meta?.dimensions?.length ? meta.dimensions : DEFAULT_DIMENSIONS;
  const metrics = meta?.metrics?.length ? meta.metrics : DEFAULT_METRICS;
  const dateFields = meta?.date_fields?.length ? meta.date_fields : DEFAULT_DATE_FIELDS;
  const meId = user?.id ?? null;
  const canSchedule =
    typeof meta?.can_schedule === 'boolean' ? meta.can_schedule : isAdmin || leadTeamIds(user).length > 0;

  const canEditTemplate = useCallback(
    (t: ReportTemplate) => {
      if (t.is_system) return false;
      if (typeof t.can_edit === 'boolean') return t.can_edit;
      return isAdmin || (meId !== null && templateOwnerId(t) === meId);
    },
    [isAdmin, meId],
  );

  const openTemplate = (t: ReportTemplate) => {
    setDefinition(normalizeReportDefinition(t.definition));
    setTemplate(t);
    selectTab('builder');
  };

  const scopeLabel = meta?.scope?.label;

  return (
    <div className={`page ${styles.page}`}>
      <PageHeader
        title="Reportes"
        subtitle={
          <span className={styles.scopeLine}>
            <span className={`badge ${scopeLabel ? 'badge-info' : 'badge-outline'}`} aria-live="polite">
              Viendo: {scopeLabel ?? (metaError ? 'alcance no disponible' : 'calculando alcance…')}
            </span>
            <span className="text-sm">Solo se incluyen los tickets que usted puede ver.</span>
          </span>
        }
      />

      <div className="tabs" role="tablist" aria-label="Secciones de reportes">
        {TABS.map((t) => (
          <button
            key={t.key}
            id={`reports-tab-${t.key}`}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            aria-controls={`reports-panel-${t.key}`}
            className={`tab${tab === t.key ? ' is-active' : ''}`}
            onClick={() => selectTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab !== 'summary' && (
        <>
          <ErrorAlert error={metaError} onRetry={() => void loadMeta()} />
          <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
        </>
      )}

      {/* Resumen se desmonta al salir: sincroniza sus filtros con la URL. */}
      {tab === 'summary' && (
        <div role="tabpanel" id="reports-panel-summary" aria-labelledby="reports-tab-summary">
          <ReportsView />
        </div>
      )}

      {visited.has('builder') && (
        <div
          role="tabpanel"
          id="reports-panel-builder"
          aria-labelledby="reports-tab-builder"
          hidden={tab !== 'builder'}
        >
          <ReportBuilder
            definition={definition}
            onDefinitionChange={setDefinition}
            template={template}
            onTemplateChange={setTemplate}
            canEditTemplate={canEditTemplate}
            onTemplatesChanged={() => setTemplatesVersion((v) => v + 1)}
            dimensions={dimensions}
            metrics={metrics}
            dateFields={dateFields}
            options={options}
            canSchedule={canSchedule}
          />
        </div>
      )}

      {visited.has('templates') && (
        <div
          role="tabpanel"
          id="reports-panel-templates"
          aria-labelledby="reports-tab-templates"
          hidden={tab !== 'templates'}
        >
          <TemplatesView
            meId={meId}
            isAdmin={isAdmin}
            canEditTemplate={canEditTemplate}
            version={templatesVersion}
            onOpen={openTemplate}
            onDeleted={(id) => {
              if (template?.id === id) setTemplate(null);
            }}
            dimensions={dimensions}
            metrics={metrics}
            options={options}
          />
        </div>
      )}
    </div>
  );
}
