'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '@/components/icons';
import SlaBadge from '@/components/ticket/SlaBadge';
import CreateTicketForm from '@/components/tickets/CreateTicketForm';
import { Alert, EmptyState, ErrorAlert, Loading, PageHeader, Pagination, PriorityBadge, StatusBadge } from '@/components/ui';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatRelative } from '@/lib/format';
import { useApiResource, useCatalog, useDebouncedValue } from '@/lib/hooks';
import { useTicketEvents } from '@/lib/realtime';
import type { TicketOut, TicketPage, TicketSort } from '@/lib/types';

const DEFAULT_PAGE_SIZE = 25;
const PAGE_SIZES = [10, 25, 50, 100];

interface Filters {
  status: string;
  priority: string;
  department_id: string;
  team_id: string;
  scope: '' | 'mine' | 'assigned';
  q: string;
  sort: TicketSort;
  page: number;
  page_size: number;
}

const SORT_OPTIONS: { value: TicketSort; label: string }[] = [
  { value: '-created_at', label: 'Más recientes' },
  { value: 'created_at', label: 'Más antiguos' },
  { value: '-updated_at', label: 'Actualizados recientemente' },
  { value: '-priority', label: 'Prioridad (mayor primero)' },
  { value: 'priority', label: 'Prioridad (menor primero)' },
];

function parseFilters(params: URLSearchParams): Filters {
  const page = Number(params.get('page'));
  const pageSize = Number(params.get('page_size'));
  const scope = params.get('scope');
  const sort = params.get('sort') as TicketSort | null;
  return {
    status: params.get('status') ?? '',
    priority: params.get('priority') ?? '',
    department_id: params.get('department_id') ?? '',
    team_id: params.get('team_id') ?? '',
    scope: scope === 'mine' || scope === 'assigned' ? scope : '',
    q: params.get('q') ?? '',
    sort: sort && SORT_OPTIONS.some((o) => o.value === sort) ? sort : '-created_at',
    page: Number.isInteger(page) && page > 0 ? page : 1,
    page_size: PAGE_SIZES.includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
  };
}

function TicketsView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { user, isAgent } = useAuth();
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();

  const filters = useMemo(() => parseFilters(new URLSearchParams(searchParams.toString())), [searchParams]);
  const [showCreate, setShowCreate] = useState(searchParams.get('new') === '1');
  const [created, setCreated] = useState<TicketOut | null>(null);
  const [search, setSearch] = useState(filters.q);
  const debouncedSearch = useDebouncedValue(search.trim(), 400);

  const updateFilters = useCallback(
    (changes: Partial<Filters>, resetPage = true) => {
      const next: Filters = { ...filters, ...changes, ...(resetPage && !('page' in changes) ? { page: 1 } : {}) };
      const params = new URLSearchParams();
      if (next.status) params.set('status', next.status);
      if (next.priority) params.set('priority', next.priority);
      if (next.department_id) params.set('department_id', next.department_id);
      if (next.team_id) params.set('team_id', next.team_id);
      if (next.scope) params.set('scope', next.scope);
      if (next.q) params.set('q', next.q);
      if (next.sort !== '-created_at') params.set('sort', next.sort);
      if (next.page > 1) params.set('page', String(next.page));
      if (next.page_size !== DEFAULT_PAGE_SIZE) params.set('page_size', String(next.page_size));
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [filters, pathname, router],
  );

  // Aplica la búsqueda con retardo. Depende solo del término: si dependiera de `filters`,
  // al limpiar filtros el valor aún no actualizado volvería a escribirse en la URL.
  const applySearch = useRef(updateFilters);
  applySearch.current = updateFilters;
  const currentQ = useRef(filters.q);
  currentQ.current = filters.q;
  useEffect(() => {
    if (debouncedSearch !== currentQ.current) applySearch.current({ q: debouncedSearch });
  }, [debouncedSearch]);

  const query = useMemo(
    () => ({
      status: filters.status || undefined,
      priority: filters.priority || undefined,
      department_id: filters.department_id || undefined,
      team_id: filters.team_id || undefined,
      mine: filters.scope === 'mine' ? true : undefined,
      assignee_id: filters.scope === 'assigned' && user ? user.id : undefined,
      q: filters.q || undefined,
      sort: filters.sort,
      page: filters.page,
      page_size: filters.page_size,
    }),
    [filters, user],
  );

  const { data, error, loading, reload } = useApiResource<TicketPage>('/tickets', query);

  // Si la página quedó fuera de rango (p.ej. tras filtrar), vuelve a la última válida.
  useEffect(() => {
    if (!data || data.items.length > 0 || data.total === 0 || filters.page === 1) return;
    updateFilters({ page: Math.max(1, Math.ceil(data.total / filters.page_size)) }, false);
  }, [data, filters.page, filters.page_size, updateFilters]);

  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useTicketEvents(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => void reload({ silent: true }), 1000);
  });
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    },
    [],
  );

  const teams = useMemo(() => {
    if (!catalog) return [];
    const dep = Number(filters.department_id);
    return catalog.teams.filter((t) => !dep || t.department_id === dep);
  }, [catalog, filters.department_id]);

  const states = useMemo(
    () => (catalog ? [...catalog.workflow.states].sort((a, b) => a.order - b.order) : []),
    [catalog],
  );
  const priorities = useMemo(() => (catalog ? [...catalog.priorities].sort((a, b) => b.rank - a.rank) : []), [catalog]);

  const hasFilters = Boolean(
    filters.status || filters.priority || filters.department_id || filters.team_id || filters.scope || filters.q,
  );

  function clearFilters() {
    setSearch('');
    router.replace(pathname, { scroll: false });
  }

  function handleCreated(ticket: TicketOut) {
    setShowCreate(false);
    setCreated(ticket);
    void reload({ silent: true });
  }

  return (
    <div className="page">
      <PageHeader
        title="Tickets"
        subtitle="Consulte, filtre y registre solicitudes e incidentes."
        actions={
          <>
            <button type="button" className="btn" onClick={() => void reload()} disabled={loading}>
              <Icon name="refresh" width={14} height={14} />
              Actualizar
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setShowCreate((v) => !v)}
              disabled={!catalog}
              aria-expanded={showCreate}
            >
              <Icon name="plus" width={14} height={14} />
              Nuevo ticket
            </button>
          </>
        }
      />

      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />

      {created && (
        <Alert
          tone="success"
          action={
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setCreated(null)}>
              Cerrar
            </button>
          }
        >
          Ticket <Link href={`/tickets/${created.id}`}>{created.key}</Link> creado correctamente.
        </Alert>
      )}

      {showCreate && catalog && (
        <CreateTicketForm catalog={catalog} onCreated={handleCreated} onCancel={() => setShowCreate(false)} />
      )}

      <section className="panel" aria-label="Filtros">
        <div className="toolbar">
          <label className="field field-search">
            <span className="field-label">Buscar</span>
            <input
              className="input"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Título, descripción o clave (ESD-123)"
              maxLength={200}
            />
          </label>
          <label className="field">
            <span className="field-label">Estado</span>
            <select className="select" value={filters.status} onChange={(e) => updateFilters({ status: e.target.value })}>
              <option value="">Todos</option>
              {states.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Prioridad</span>
            <select
              className="select"
              value={filters.priority}
              onChange={(e) => updateFilters({ priority: e.target.value })}
            >
              <option value="">Todas</option>
              {priorities.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Departamento</span>
            <select
              className="select"
              value={filters.department_id}
              onChange={(e) => updateFilters({ department_id: e.target.value, team_id: '' })}
            >
              <option value="">Todos</option>
              {catalog?.departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Equipo</span>
            <select className="select" value={filters.team_id} onChange={(e) => updateFilters({ team_id: e.target.value })}>
              <option value="">Todos</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Alcance</span>
            <select
              className="select"
              value={filters.scope}
              onChange={(e) => updateFilters({ scope: e.target.value as Filters['scope'] })}
            >
              <option value="">Todos los visibles</option>
              <option value="mine">Creados por mí o asignados a mí</option>
              {isAgent && <option value="assigned">Asignados a mí</option>}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Orden</span>
            <select
              className="select"
              value={filters.sort}
              onChange={(e) => updateFilters({ sort: e.target.value as TicketSort })}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {hasFilters && (
            <button type="button" className="btn btn-ghost" onClick={clearFilters}>
              Limpiar filtros
            </button>
          )}
        </div>
      </section>

      <section className="panel panel-flush" aria-busy={loading}>
        <ErrorAlert error={error} onRetry={() => void reload()} />
        {loading && !data ? (
          <Loading label="Cargando tickets…" />
        ) : data && data.items.length === 0 ? (
          <EmptyState title={hasFilters ? 'Ningún ticket coincide con los filtros' : 'No hay tickets visibles'}>
            {hasFilters ? 'Ajuste o limpie los filtros para ver más resultados.' : 'Registre uno con «Nuevo ticket».'}
          </EmptyState>
        ) : data ? (
          <>
            <div className="table-wrap" style={{ opacity: loading ? 0.6 : 1 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Clave</th>
                    <th scope="col">Título</th>
                    <th scope="col">Estado</th>
                    <th scope="col">Prioridad</th>
                    <th scope="col">SLA</th>
                    <th scope="col">Departamento / equipo</th>
                    <th scope="col">Asignado</th>
                    <th scope="col">Actualizado</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((t) => (
                    <tr key={t.id}>
                      <td className="col-key">
                        <Link href={`/tickets/${t.id}`}>{t.key}</Link>
                      </td>
                      <td className="col-title">
                        <Link href={`/tickets/${t.id}`} style={{ color: 'inherit', fontWeight: 500 }}>
                          {t.title}
                        </Link>
                        <div className="text-xs subtle">
                          {t.type.name} · {t.requester.display_name}
                        </div>
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
                      <td>
                        <div className="text-sm">{t.department.name}</div>
                        <div className="text-xs subtle">{t.team?.name ?? 'Sin equipo'}</div>
                      </td>
                      <td className="text-sm">{t.assignee?.display_name ?? <span className="subtle">Sin asignar</span>}</td>
                      <td className="nowrap text-sm muted" title={formatDateTime(t.updated_at)}>
                        {formatRelative(t.updated_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={filters.page}
              pageSize={filters.page_size}
              total={data.total}
              pageSizes={PAGE_SIZES}
              onPageChange={(page) => updateFilters({ page }, false)}
              onPageSizeChange={(page_size) => updateFilters({ page_size })}
            />
          </>
        ) : null}
      </section>
    </div>
  );
}

export default function TicketsPage() {
  return (
    <Suspense fallback={<Loading label="Cargando tickets…" />}>
      <TicketsView />
    </Suspense>
  );
}
