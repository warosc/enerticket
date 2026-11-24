'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/icons';
import { Alert, ErrorAlert, Loading } from '@/components/ui';
import { ApiError, errorMessage } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/format';
import { useCatalog, useDebouncedValue } from '@/lib/hooks';
import {
  defaultReportFilters,
  downloadReportCsv,
  fetchReports,
  type ReportBundle,
  type ReportFilterValues,
} from '@/lib/reports';
import AgingChart from './AgingChart';
import AssigneeTable from './AssigneeTable';
import DepartmentChart from './DepartmentChart';
import KpiCards from './KpiCards';
import ReportFilters, { validateRange } from './ReportFilters';
import SlaCompliance from './SlaCompliance';
import TrendChart from './TrendChart';
import styles from './reports.module.css';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function positiveInt(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const n = Number(value);
  return n > 0 ? n : null;
}

function filtersFromParams(params: URLSearchParams | null): ReportFilterValues {
  const defaults = defaultReportFilters();
  if (!params) return defaults;
  const from = params.get('from');
  const to = params.get('to');
  return {
    from: from && ISO_DATE.test(from) ? from : defaults.from,
    to: to && ISO_DATE.test(to) ? to : defaults.to,
    department_id: positiveInt(params.get('department_id')),
    team_id: positiveInt(params.get('team_id')),
  };
}

function filtersToSearch(filters: ReportFilterValues): string {
  const params = new URLSearchParams({ from: filters.from, to: filters.to });
  if (filters.department_id !== null) params.set('department_id', String(filters.department_id));
  if (filters.team_id !== null) params.set('team_id', String(filters.team_id));
  return params.toString();
}

/** Pestaña «Resumen» de /reports: tablero de indicadores del periodo (Fase 2). */
export default function ReportsView() {
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [filters, setFilters] = useState<ReportFilterValues>(() => filtersFromParams(searchParams));
  const debounced = useDebouncedValue(filters, 350);
  const rangeError = validateRange(filters);

  const [data, setData] = useState<ReportBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async (current: ReportFilterValues) => {
    if (validateRange(current)) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    try {
      const bundle = await fetchReports(current, controller.signal);
      if (controller.signal.aborted) return;
      setData(bundle);
      setError(null);
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(e instanceof ApiError && e.isForbidden ? 'No tiene permisos para ver reportes.' : errorMessage(e));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(debounced);
    return () => controllerRef.current?.abort();
  }, [debounced, load]);

  // Mantiene los filtros en la URL para compartir/recargar el reporte.
  useEffect(() => {
    if (validateRange(debounced)) return;
    const search = filtersToSearch(debounced);
    if (search !== searchParams?.toString()) router.replace(`${pathname}?${search}`, { scroll: false });
  }, [debounced, pathname, router, searchParams]);

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await downloadReportCsv(filters);
    } catch (e) {
      setExportError(`No se pudo exportar: ${errorMessage(e)}`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className={`stack-lg ${styles.root}`}>
      <div className="row-between">
        <div className="text-sm muted">
          Del {formatDate(`${filters.from}T12:00:00Z`)} al {formatDate(`${filters.to}T12:00:00Z`)} · hora de Guatemala
        </div>
        <div className="page-actions">
          <button
            type="button"
            className="btn"
            onClick={() => void load(filters)}
            disabled={loading || Boolean(rangeError)}
          >
            <Icon name="refresh" aria-hidden="true" /> Actualizar
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void exportCsv()}
            disabled={exporting || Boolean(rangeError)}
          >
            {exporting ? 'Exportando…' : 'Exportar CSV'}
          </button>
        </div>
      </div>

      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
      <ReportFilters value={filters} onChange={setFilters} catalog={catalog} />
      {rangeError && <Alert tone="warning">{rangeError}</Alert>}
      <ErrorAlert error={exportError} />
      <ErrorAlert error={error} onRetry={() => void load(filters)} />

      {!data && loading && <Loading label="Calculando reportes…" />}

      {data && (
        <div className="stack-lg" aria-busy={loading} style={{ opacity: loading ? 0.6 : 1, transition: 'opacity 0.15s' }}>
          <KpiCards data={data.overview} />
          <div className={styles.chartsGrid}>
            <TrendChart data={data.trend} className={styles.span2} />
            <DepartmentChart data={data.byDepartment} />
            <AgingChart data={data.aging} />
            <AssigneeTable data={data.byAssignee} className={styles.span2} />
            <SlaCompliance data={data.sla} className={styles.span2} />
          </div>
          <p className="text-xs subtle">
            Datos calculados el {formatDateTime(data.overview.generated_at)} sobre los tickets que usted puede ver.
            El backlog y la antigüedad reflejan el estado actual; el resto, el periodo seleccionado.
          </p>
        </div>
      )}
    </div>
  );
}
