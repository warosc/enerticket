'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ApprovalsPanel from '@/components/ticket/ApprovalsPanel';
import AttachmentsPanel from '@/components/ticket/AttachmentsPanel';
import SlaBadge from '@/components/ticket/SlaBadge';
import SlaPanel from '@/components/ticket/SlaPanel';
import TicketComments from '@/components/tickets/TicketComments';
import TicketEditPanel from '@/components/tickets/TicketEditPanel';
import TicketHistory from '@/components/tickets/TicketHistory';
import { Alert, EmptyState, ErrorAlert, Loading, PageHeader, PriorityBadge, StatusBadge } from '@/components/ui';
import { ApiError, apiFetch, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';
import { useCatalog } from '@/lib/hooks';
import { useTicketEvents, type TicketEvent } from '@/lib/realtime';
import type {
  TicketComment,
  TicketHistoryEntry,
  TicketOut,
  TransitionRequest,
  UserOut,
  WorkflowTransition,
} from '@/lib/types';

type Tab = 'comments' | 'history';

export default function TicketDetailPage() {
  const params = useParams<{ id: string }>();
  const ticketId = Number(params?.id);
  const validId = Number.isInteger(ticketId) && ticketId > 0;

  const { user, isAgent } = useAuth();
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();

  const [ticket, setTicket] = useState<TicketOut | null>(null);
  const [comments, setComments] = useState<TicketComment[]>([]);
  const [history, setHistory] = useState<TicketHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [tab, setTab] = useState<Tab>('comments');

  const [agents, setAgents] = useState<UserOut[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);

  const [transitionComment, setTransitionComment] = useState('');
  const [transitioning, setTransitioning] = useState<number | null>(null);
  const [transitionError, setTransitionError] = useState<string | null>(null);

  const requestId = useRef(0);

  const load = useCallback(
    async (silent = false) => {
      if (!validId) return;
      const id = ++requestId.current;
      if (!silent) setLoading(true);
      try {
        const [t, c, h] = await Promise.all([
          apiFetch<TicketOut>(`/tickets/${ticketId}`),
          apiFetch<TicketComment[]>(`/tickets/${ticketId}/comments`),
          apiFetch<TicketHistoryEntry[]>(`/tickets/${ticketId}/history`),
        ]);
        if (id !== requestId.current) return;
        setTicket(t);
        setComments(c);
        setHistory(h);
        setLoadError(null);
        setNotFound(false);
      } catch (e) {
        if (id !== requestId.current) return;
        if (e instanceof ApiError && e.isNotFound) setNotFound(true);
        else setLoadError(errorMessage(e));
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [ticketId, validId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!isAgent) return;
    let active = true;
    apiFetch<UserOut[]>('/users', { query: { role: 'agent', limit: 200 } })
      .then((list) => {
        if (active) {
          setAgents(list);
          setAgentsError(null);
        }
      })
      .catch((e: unknown) => {
        if (active) setAgentsError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [isAgent]);

  // Tiempo real: recarga silenciosa del ticket ante eventos (agrupando ráfagas).
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onTicketEvent = useCallback(
    (event: TicketEvent) => {
      if (event.ticket_id !== ticketId) return;
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => void load(true), 400);
    },
    [load, ticketId],
  );
  useTicketEvents(onTicketEvent, validId ? { ticketId } : undefined);
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    },
    [],
  );

  const reloadSilently = useCallback(() => void load(true), [load]);

  const availableTransitions = useMemo<WorkflowTransition[]>(() => {
    if (!catalog || !ticket) return [];
    const states = new Map(catalog.workflow.states.map((s) => [s.id, s]));
    return catalog.workflow.transitions.filter((tr) => {
      if (tr.from_state_id !== ticket.status.id) return false;
      if (isAgent) return true;
      // Solicitantes: solo cerrar o reabrir sus propios tickets (la API valida el resto).
      if (ticket.requester.id !== user?.id) return false;
      const to = states.get(tr.to_state_id);
      return Boolean(to?.is_final || ticket.status.category === 'done');
    });
  }, [catalog, ticket, isAgent, user?.id]);

  async function runTransition(transition: WorkflowTransition) {
    if (!ticket) return;
    setTransitioning(transition.id);
    setTransitionError(null);
    try {
      const payload: TransitionRequest = { to_state_key: transition.to_key };
      if (transitionComment.trim()) payload.comment = transitionComment.trim();
      const updated = await apiFetch<TicketOut>(`/tickets/${ticket.id}/transition`, { method: 'POST', body: payload });
      setTicket(updated);
      setTransitionComment('');
      void load(true);
    } catch (e) {
      setTransitionError(errorMessage(e));
    } finally {
      setTransitioning(null);
    }
  }

  if (!validId || notFound) {
    return (
      <div className="page">
        <PageHeader title="Ticket no encontrado" breadcrumb={<Link href="/tickets">← Tickets</Link>} />
        <section className="panel">
          <EmptyState title="El ticket no existe o no tiene permiso para verlo">
            <Link href="/tickets">Volver a la lista de tickets</Link>
          </EmptyState>
        </section>
      </div>
    );
  }

  if (loading && !ticket) return <Loading label="Cargando ticket…" />;

  if (!ticket) {
    return (
      <div className="page">
        <PageHeader title="Ticket" breadcrumb={<Link href="/tickets">← Tickets</Link>} />
        <ErrorAlert error={loadError ?? 'No se pudo cargar el ticket.'} onRetry={() => void load()} />
      </div>
    );
  }

  const stateName = (id: number) => catalog?.workflow.states.find((s) => s.id === id)?.name ?? '';

  return (
    <div className="page">
      <PageHeader
        breadcrumb={
          <>
            <Link href="/tickets">Tickets</Link> / <span className="mono">{ticket.key}</span>
          </>
        }
        title={ticket.title}
        subtitle={
          <span className="row-sm" style={{ marginTop: 4 }}>
            <StatusBadge status={ticket.status} />
            <PriorityBadge priority={ticket.priority} />
            <span className="badge badge-outline">{ticket.type.name}</span>
            <SlaBadge ticket={ticket} />
          </span>
        }
        actions={
          <button type="button" className="btn" onClick={() => void load()} disabled={loading}>
            Actualizar
          </button>
        }
      />

      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
      <ErrorAlert error={loadError} onRetry={() => void load()} />

      <div className="grid-main-aside">
        <div className="stack-lg" style={{ minWidth: 0 }}>
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Descripción</h2>
            </div>
            {ticket.description ? (
              <p className="pre-wrap">{ticket.description}</p>
            ) : (
              <p className="muted">Sin descripción.</p>
            )}
          </section>

          {(availableTransitions.length > 0 || transitionError) && (
            <section className="panel stack" aria-labelledby="transitions-title">
              <div className="panel-header" style={{ marginBottom: 0 }}>
                <h2 className="panel-title" id="transitions-title">
                  Cambiar estado
                </h2>
                <span className="panel-subtitle">Estado actual: {ticket.status.name}</span>
              </div>
              {transitionError && <Alert tone="error">{transitionError}</Alert>}
              <label className="field">
                <span className="field-label">Comentario de la transición (opcional)</span>
                <textarea
                  className="textarea"
                  style={{ minHeight: 64 }}
                  value={transitionComment}
                  onChange={(e) => setTransitionComment(e.target.value)}
                  maxLength={20000}
                  placeholder="Motivo o detalle del cambio de estado"
                />
              </label>
              <div className="btn-group">
                {availableTransitions.map((tr) => {
                  const target = catalog?.workflow.states.find((s) => s.id === tr.to_state_id);
                  const primary = target?.category === 'done' || target?.category === 'in_progress';
                  return (
                    <button
                      key={tr.id}
                      type="button"
                      className={`btn ${primary ? 'btn-primary' : 'btn-secondary'}`}
                      disabled={transitioning !== null}
                      onClick={() => void runTransition(tr)}
                      title={tr.requires_approval ? 'Esta transición requiere aprobación' : `Pasar a ${stateName(tr.to_state_id)}`}
                    >
                      {transitioning === tr.id ? 'Aplicando…' : tr.name || stateName(tr.to_state_id)}
                      {tr.requires_approval && <span className="badge badge-warning">Aprobación</span>}
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          <ApprovalsPanel ticket={ticket} onChange={reloadSilently} />
          <AttachmentsPanel ticket={ticket} />

          <section className="panel">
            <div className="tabs" role="tablist" aria-label="Actividad del ticket">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'comments'}
                className={`tab${tab === 'comments' ? ' is-active' : ''}`}
                onClick={() => setTab('comments')}
              >
                Comentarios ({comments.length})
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'history'}
                className={`tab${tab === 'history' ? ' is-active' : ''}`}
                onClick={() => setTab('history')}
              >
                Historial ({history.length})
              </button>
            </div>
            <div role="tabpanel" style={{ paddingTop: 'var(--space-4)' }}>
              {tab === 'comments' ? (
                <TicketComments
                  ticketId={ticket.id}
                  comments={comments}
                  canComment
                  canUseInternal={isAgent}
                  onAdded={(comment) => {
                    setComments((prev) => [...prev, comment]);
                    void load(true);
                  }}
                />
              ) : (
                <TicketHistory entries={history} catalog={catalog} users={agents} />
              )}
            </div>
          </section>
        </div>

        <aside className="stack" style={{ minWidth: 0 }}>
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Detalles</h2>
              <span className="mono text-sm muted">{ticket.key}</span>
            </div>
            <dl className="dl">
              <dt>Solicitante</dt>
              <dd>{ticket.requester.display_name}</dd>
              <dt>Asignado</dt>
              <dd>{ticket.assignee?.display_name ?? <span className="subtle">Sin asignar</span>}</dd>
              <dt>Departamento</dt>
              <dd>{ticket.department.name}</dd>
              <dt>Equipo</dt>
              <dd>{ticket.team?.name ?? <span className="subtle">Sin equipo</span>}</dd>
              <dt>Proyecto</dt>
              <dd>{ticket.project ? `${ticket.project.code} — ${ticket.project.name}` : <span className="subtle">—</span>}</dd>
              <dt>Creado</dt>
              <dd>{formatDateTime(ticket.created_at)}</dd>
              <dt>Actualizado</dt>
              <dd>{formatDateTime(ticket.updated_at)}</dd>
              <dt>1.ª respuesta</dt>
              <dd>{formatDateTime(ticket.first_response_at)}</dd>
              <dt>Resuelto</dt>
              <dd>{formatDateTime(ticket.resolved_at)}</dd>
              <dt>Cerrado</dt>
              <dd>{formatDateTime(ticket.closed_at)}</dd>
            </dl>
          </section>

          <SlaPanel ticket={ticket} />

          {isAgent && catalog && user && (
            <TicketEditPanel
              ticket={ticket}
              catalog={catalog}
              agents={agents}
              agentsError={agentsError}
              me={user}
              onSaved={(updated) => {
                setTicket(updated);
                void load(true);
              }}
            />
          )}
        </aside>
      </div>
    </div>
  );
}
