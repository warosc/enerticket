'use client';

/**
 * Bandeja de aprobaciones: pendientes de mi decisión, historial y (admin) políticas.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import ApprovalCard from '@/components/approvals/ApprovalCard';
import PoliciesSection from '@/components/approvals/PoliciesSection';
import { EmptyState, ErrorAlert, Loading, PageHeader, Pagination } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  APPROVAL_STATUS_LABELS,
  getPendingApprovals,
  listApprovals,
  type ApprovalPage,
  type ApprovalRequest,
  type ApprovalStatus,
} from '@/lib/flows';
import { RESYNC_EVENT, useTicketEvents, type TicketEvent } from '@/lib/realtime';

type Tab = 'pending' | 'history' | 'policies';

function PendingTab() {
  const { isAdmin } = useAuth();
  const [all, setAll] = useState(false);
  const [items, setItems] = useState<ApprovalRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const load = useCallback(
    async (silent = false) => {
      const id = ++seq.current;
      if (!silent) setLoading(true);
      try {
        const result = await getPendingApprovals(all);
        if (id !== seq.current) return;
        setItems(result);
        setError(null);
      } catch (e) {
        if (id === seq.current) setError(errorMessage(e));
      } finally {
        if (id === seq.current) setLoading(false);
      }
    },
    [all],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onEvent = useCallback(
    (event: TicketEvent) => {
      if (!event.type.startsWith('approval.') && event.type !== 'ticket.transitioned' && event.type !== RESYNC_EVENT) return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void load(true), 400);
    },
    [load],
  );
  useTicketEvents(onEvent);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function handleChanged(updated: ApprovalRequest) {
    setItems((prev) => {
      if (!prev) return prev;
      // Si ya no requiere mi decisión, sale de la bandeja (la tarjeta muestra el resultado hasta recargar).
      return prev.map((r) => (r.id === updated.id ? updated : r));
    });
    void load(true);
  }

  return (
    <div className="stack">
      <div className="row-between">
        <p className="muted text-sm">
          Solicitudes cuya etapa actual espera su decisión. Al aprobar la última etapa, el cambio de estado se aplica automáticamente.
        </p>
        <div className="row-sm">
          {isAdmin && (
            <label className="checkbox">
              <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
              Ver todas las pendientes
            </label>
          )}
          <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
            Actualizar
          </button>
        </div>
      </div>
      <ErrorAlert error={error} onRetry={() => void load()} />
      {loading && !items ? (
        <Loading label="Cargando aprobaciones…" />
      ) : items && items.length > 0 ? (
        <div className="stack">
          {items.map((request) => (
            <ApprovalCard key={request.id} request={request} onChanged={handleChanged} />
          ))}
        </div>
      ) : (
        !error && (
          <section className="panel">
            <EmptyState title="No tiene aprobaciones pendientes">
              Cuando un cambio requiera su aprobación aparecerá aquí y recibirá una notificación.
            </EmptyState>
          </section>
        )
      )}
    </div>
  );
}

function HistoryTab() {
  const { isAdmin } = useAuth();
  const [scope, setScope] = useState<'mine' | 'all'>('mine');
  const [status, setStatus] = useState<ApprovalStatus | ''>('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [data, setData] = useState<ApprovalPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    try {
      const result = await listApprovals({ scope, status, page, page_size: pageSize });
      if (id !== seq.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (id === seq.current) setError(errorMessage(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [scope, status, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <div className="toolbar">
        {isAdmin && (
          <label className="field">
            <span className="field-label">Alcance</span>
            <select
              className="select"
              value={scope}
              onChange={(e) => {
                setScope(e.target.value as 'mine' | 'all');
                setPage(1);
              }}
            >
              <option value="mine">En las que participo</option>
              <option value="all">Todas</option>
            </select>
          </label>
        )}
        <label className="field">
          <span className="field-label">Estado</span>
          <select
            className="select"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as ApprovalStatus | '');
              setPage(1);
            }}
          >
            <option value="">Todos</option>
            {(Object.keys(APPROVAL_STATUS_LABELS) as ApprovalStatus[]).map((key) => (
              <option key={key} value={key}>
                {APPROVAL_STATUS_LABELS[key]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorAlert error={error} onRetry={() => void load()} />
      {loading && !data ? (
        <Loading label="Cargando historial…" />
      ) : data && data.items.length > 0 ? (
        <>
          <div className="stack">
            {data.items.map((request) => (
              <ApprovalCard key={request.id} request={request} onChanged={() => void load()} />
            ))}
          </div>
          <section className="panel panel-flush">
            <Pagination
              page={data.page}
              pageSize={pageSize}
              total={data.total}
              onPageChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                setPage(1);
              }}
              pageSizes={[10, 25, 50]}
            />
          </section>
        </>
      ) : (
        !error && (
          <section className="panel">
            <EmptyState title="Sin solicitudes">No hay solicitudes de aprobación con estos filtros.</EmptyState>
          </section>
        )
      )}
    </div>
  );
}

export default function ApprovalsView() {
  const { isAdmin, hasRole } = useAuth();
  const [tab, setTab] = useState<Tab>('pending');
  const tabs: { key: Tab; label: string }[] = [
    { key: 'pending', label: 'Pendientes de mi decisión' },
    { key: 'history', label: 'Historial' },
    ...(isAdmin ? [{ key: 'policies' as Tab, label: 'Políticas' }] : []),
  ];

  return (
    <div className="page">
      <PageHeader
        title="Aprobaciones"
        subtitle={
          hasRole('approver', 'agent', 'admin')
            ? 'Decida sobre cambios de estado que requieren aprobación y consulte el historial de decisiones.'
            : 'Consulte las solicitudes de aprobación en las que participa.'
        }
      />
      <div className="tabs" role="tablist" aria-label="Secciones de aprobaciones">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`tab${tab === t.key ? ' is-active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === 'pending' && <PendingTab />}
        {tab === 'history' && <HistoryTab />}
        {tab === 'policies' && isAdmin && <PoliciesSection />}
      </div>
    </div>
  );
}
