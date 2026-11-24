'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import CountBarChart, { type CountDatum } from '@/components/dashboard/CountBarChart';
import { Icon } from '@/components/icons';
import SlaBadge from '@/components/ticket/SlaBadge';
import { EmptyState, ErrorAlert, Loading, PageHeader, PriorityBadge, StatusBadge } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatNumber, formatRelative } from '@/lib/format';
import { useCatalog } from '@/lib/hooks';
import { useTicketEvents } from '@/lib/realtime';
import type { TicketOut, TicketPage, TicketStats } from '@/lib/types';

interface DashboardData {
  stats: TicketStats;
  criticalOpen: number;
  mine: number;
  assignedToMe: number | null;
  unassigned: number | null;
  recent: TicketOut[];
}

const OPEN_CATEGORIES = 'todo,in_progress,waiting';

async function countTickets(query: Record<string, string | number | boolean>): Promise<number> {
  const page = await apiFetch<TicketPage>('/tickets', { query: { ...query, page: 1, page_size: 1 } });
  return page.total;
}

export default function DashboardPage() {
  const { user, isAgent } = useAuth();
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const requestId = useRef(0);

  const load = useCallback(
    async (silent = false) => {
      if (!user) return;
      const id = ++requestId.current;
      if (!silent) setLoading(true);
      try {
        const [stats, criticalOpen, mine, assignedToMe, unassigned, recent] = await Promise.all([
          apiFetch<TicketStats>('/stats'),
          countTickets({ priority: 'critical', category: OPEN_CATEGORIES }),
          countTickets({ mine: true, category: OPEN_CATEGORIES }),
          isAgent ? countTickets({ assignee_id: user.id, category: OPEN_CATEGORIES }) : Promise.resolve(null),
          isAgent ? countTickets({ unassigned: true, category: OPEN_CATEGORIES }) : Promise.resolve(null),
          apiFetch<TicketPage>('/tickets', { query: { page: 1, page_size: 8, sort: '-updated_at' } }),
        ]);
        if (id !== requestId.current) return;
        setData({ stats, criticalOpen, mine, assignedToMe, unassigned, recent: recent.items });
        setError(null);
      } catch (e) {
        if (id === requestId.current) setError(`No se pudieron cargar los indicadores: ${errorMessage(e)}`);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [user, isAgent],
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
    if (!data) return [];
    if (!catalog) {
      return Object.entries(data.stats.by_status).map(([key, count]) => ({ key, label: key, count }));
    }
    return [...catalog.workflow.states]
      .sort((a, b) => a.order - b.order)
      .map((s) => ({ key: s.key, label: s.name, count: data.stats.by_status[s.key] ?? 0 }));
  }, [catalog, data]);

  const priorityData: CountDatum[] = useMemo(() => {
    if (!data) return [];
    if (!catalog) {
      return Object.entries(data.stats.by_priority).map(([key, count]) => ({ key, label: key, count }));
    }
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
            <Kpi label="Tickets visibles" value={data.stats.total} accent="primary" href="/tickets" />
            <Kpi
              label="Abiertos"
              value={openCount}
              accent="neutral"
              sub="Por atender, en curso y en espera"
              href={`/tickets?category=${OPEN_CATEGORIES}`}
            />
            <Kpi label="En espera" value={byCategory.waiting ?? 0} accent="warning" href="/tickets?category=waiting" />
            <Kpi label="Críticos abiertos" value={data.criticalOpen} accent="danger" href={`/tickets?priority=critical&category=${OPEN_CATEGORIES}`} />
            <Kpi
              label="Finalizados"
              value={byCategory.done ?? 0}
              accent="success"
              sub="Resueltos y cerrados"
              href="/tickets?category=done"
            />
            {data.assignedToMe !== null ? (
              <Kpi label="Asignados a mí" value={data.assignedToMe} accent="primary" sub="Abiertos" href="/tickets?scope=assigned" />
            ) : (
              <Kpi label="Mis tickets abiertos" value={data.mine} accent="primary" href="/tickets?scope=mine" />
            )}
            {data.unassigned !== null && (
              <Kpi label="Sin asignar" value={data.unassigned} accent="warning" sub="Abiertos" href="/tickets?scope=unassigned" />
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
  href,
}: {
  label: string;
  value: number;
  sub?: string;
  accent: 'primary' | 'warning' | 'success' | 'danger' | 'neutral';
  href?: string;
}) {
  const content = (
    <>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{formatNumber(value)}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </>
  );
  if (href) {
    return (
      <Link href={href} className={`kpi kpi-link kpi-accent-${accent}`} aria-label={`${label}: ${formatNumber(value)}. Ver tickets`}>
        {content}
      </Link>
    );
  }
  return <div className={`kpi kpi-accent-${accent}`}>{content}</div>;
}
