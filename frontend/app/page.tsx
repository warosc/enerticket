'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import CountBarChart, { type CountDatum } from '@/components/dashboard/CountBarChart';
import { Icon } from '@/components/icons';
import SlaBadge from '@/components/ticket/SlaBadge';
import { EmptyState, ErrorAlert, Loading, PageHeader, PriorityBadge, StatusBadge } from '@/components/ui';
import { ApiError, apiFetch, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatNumber, formatRelative } from '@/lib/format';
import { useCatalog } from '@/lib/hooks';
import { useTicketEvents } from '@/lib/realtime';
import type { Catalog, TicketOut, TicketPage, TicketStats } from '@/lib/types';

interface DashboardData {
  stats: TicketStats;
  criticalOpen: number;
  mine: number;
  assignedToMe: number | null;
  recent: TicketOut[];
}

async function countTickets(query: Record<string, string | number | boolean>): Promise<number> {
  const page = await apiFetch<TicketPage>('/tickets', { query: { ...query, page: 1, page_size: 1 } });
  return page.total;
}

/** Usa /stats si existe; si no, cuenta con /tickets por estado y prioridad. */
async function loadStats(catalog: Catalog): Promise<TicketStats> {
  try {
    return await apiFetch<TicketStats>('/stats');
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 404) throw e;
  }
  const states = catalog.workflow.states;
  const [total, stateCounts, priorityCounts] = await Promise.all([
    countTickets({}),
    Promise.all(states.map((s) => countTickets({ status: s.key }))),
    Promise.all(catalog.priorities.map((p) => countTickets({ priority: p.key }))),
  ]);
  const by_status: Record<string, number> = {};
  const by_category: TicketStats['by_category'] = {};
  states.forEach((s, i) => {
    by_status[s.key] = stateCounts[i];
    by_category[s.category] = (by_category[s.category] ?? 0) + stateCounts[i];
  });
  const by_priority: Record<string, number> = {};
  catalog.priorities.forEach((p, i) => {
    by_priority[p.key] = priorityCounts[i];
  });
  return { total, by_status, by_priority, by_category };
}

export default function DashboardPage() {
  const { user, isAgent } = useAuth();
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const requestId = useRef(0);

  const openStateKeys = useMemo(
    () => (catalog ? catalog.workflow.states.filter((s) => s.category !== 'done').map((s) => s.key) : []),
    [catalog],
  );

  const load = useCallback(
    async (silent = false) => {
      if (!catalog || !user) return;
      const id = ++requestId.current;
      if (!silent) setLoading(true);
      try {
        const openKeys = openStateKeys.join(',');
        const [stats, criticalOpen, mine, assignedToMe, recent] = await Promise.all([
          loadStats(catalog),
          countTickets({ priority: 'critical', status: openKeys }),
          countTickets({ mine: true, status: openKeys }),
          isAgent ? countTickets({ assignee_id: user.id, status: openKeys }) : Promise.resolve(null),
          apiFetch<TicketPage>('/tickets', { query: { page: 1, page_size: 8, sort: '-updated_at' } }),
        ]);
        if (id !== requestId.current) return;
        setData({ stats, criticalOpen, mine, assignedToMe, recent: recent.items });
        setError(null);
      } catch (e) {
        if (id === requestId.current) setError(`No se pudieron cargar los indicadores: ${errorMessage(e)}`);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [catalog, user, isAgent, openStateKeys],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // Refresco con retardo ante eventos en tiempo real (agrupa ráfagas de eventos).
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useTicketEvents(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => void load(true), 1500);
  });
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    },
    [],
  );

  const stateData: CountDatum[] = useMemo(() => {
    if (!catalog || !data) return [];
    return [...catalog.workflow.states]
      .sort((a, b) => a.order - b.order)
      .map((s) => ({ key: s.key, label: s.name, count: data.stats.by_status[s.key] ?? 0 }));
  }, [catalog, data]);

  const priorityData: CountDatum[] = useMemo(() => {
    if (!catalog || !data) return [];
    return [...catalog.priorities]
      .sort((a, b) => a.rank - b.rank)
      .map((p) => ({ key: p.key, label: p.name, count: data.stats.by_priority[p.key] ?? 0 }));
  }, [catalog, data]);

  const byCategory = data?.stats.by_category ?? {};
  const openCount = (byCategory.todo ?? 0) + (byCategory.in_progress ?? 0) + (byCategory.waiting ?? 0);

  return (
    <div className="page">
      <PageHeader
        title="Dashboard"
        subtitle={user ? `Bienvenido, ${user.display_name || user.username}. Resumen de los tickets que puede ver.` : undefined}
        actions={
          <>
            <button type="button" className="btn" onClick={() => void load()} disabled={loading}>
              <Icon name="refresh" width={14} height={14} />
              Actualizar
            </button>
            <Link href="/tickets?new=1" className="btn btn-primary">
              <Icon name="plus" width={14} height={14} />
              Nuevo ticket
            </Link>
          </>
        }
      />

      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
      <ErrorAlert error={error} onRetry={() => void load()} />

      {loading && !data ? (
        <Loading label="Cargando indicadores…" />
      ) : data ? (
        <>
          <section className="grid-auto" aria-label="Indicadores clave">
            <Kpi label="Tickets visibles" value={data.stats.total} accent="primary" />
            <Kpi label="Abiertos" value={openCount} accent="neutral" sub="Por atender, en curso y en espera" />
            <Kpi label="En espera" value={byCategory.waiting ?? 0} accent="warning" />
            <Kpi label="Críticos abiertos" value={data.criticalOpen} accent="danger" />
            <Kpi label="Finalizados" value={byCategory.done ?? 0} accent="success" sub="Resueltos y cerrados" />
            {data.assignedToMe !== null ? (
              <Kpi label="Asignados a mí" value={data.assignedToMe} accent="primary" sub="Abiertos" />
            ) : (
              <Kpi label="Mis tickets abiertos" value={data.mine} accent="primary" />
            )}
          </section>

          <div className="grid-2">
            <CountBarChart title="Tickets por estado" subtitle="Según el flujo de trabajo" data={stateData} />
            <CountBarChart title="Tickets por prioridad" subtitle="Todas las categorías de estado" data={priorityData} />
          </div>

          <section className="panel panel-flush">
            <div className="panel-header">
              <h2 className="panel-title">Actividad reciente</h2>
              <Link href="/tickets" className="btn btn-ghost btn-sm">
                Ver todos
              </Link>
            </div>
            {data.recent.length === 0 ? (
              <EmptyState title="No hay tickets todavía">Cree el primero desde «Nuevo ticket».</EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Clave</th>
                      <th scope="col">Título</th>
                      <th scope="col">Estado</th>
                      <th scope="col">Prioridad</th>
                      <th scope="col">SLA</th>
                      <th scope="col">Actualizado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent.map((t) => (
                      <tr key={t.id}>
                        <td className="col-key">
                          <Link href={`/tickets/${t.id}`}>{t.key}</Link>
                        </td>
                        <td className="col-title">
                          <Link href={`/tickets/${t.id}`} style={{ color: 'inherit' }}>
                            {t.title}
                          </Link>
                        </td>
                        <td>
                          <StatusBadge status={t.status} />
                        </td>
                        <td>
                          <PriorityBadge priority={t.priority} />
                        </td>
                        <td>
                          <SlaBadge ticket={t} />
                        </td>
                        <td className="nowrap muted">{formatRelative(t.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

function Kpi({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: number;
  sub?: string;
  accent: 'primary' | 'warning' | 'success' | 'danger' | 'neutral';
}) {
  return (
    <div className={`kpi kpi-accent-${accent}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{formatNumber(value)}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}
