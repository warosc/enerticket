'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ApprovalsPanel from '@/components/ticket/ApprovalsPanel';
import AttachmentsPanel from '@/components/ticket/AttachmentsPanel';
import SlaBadge from '@/components/ticket/SlaBadge';
import SlaPanel from '@/components/ticket/SlaPanel';
import TicketComments from '@/components/tickets/TicketComments';
import TicketContentEditor from '@/components/tickets/TicketContentEditor';
import TicketEditPanel from '@/components/tickets/TicketEditPanel';
import TicketHistory from '@/components/tickets/TicketHistory';
import { Alert, EmptyState, ErrorAlert, Loading, PageHeader, PriorityBadge, StatusBadge } from '@/components/ui';
import { ApiError, apiFetch, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';
import { useCatalog } from '@/lib/hooks';
import { useTicketEvents, type TicketEvent } from '@/lib/realtime';
import { canEditTicket, transitionsFor } from '@/lib/ticketRules';
import type {
  TicketComment,
  TicketHistoryEntry,
  TicketOut,
  TransitionRequest,
  UserOut,
  UserRef,
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
  const [transitionError, setTransitionError] = useState<TransitionFailure | null>(null);
  // Transición a un estado final (cerrar, rechazar) pendiente de confirmación.
  const [confirmTransition, setConfirmTransition] = useState<WorkflowTransition | null>(null);
  const confirmButton = useRef<HTMLButtonElement | null>(null);
  const [editingContent, setEditingContent] = useState(false);

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

  // La confirmación se descarta si el ticket cambia de estado (p.ej. en vivo) y recibe el foco al abrirse.
  const currentStatusId = ticket?.status.id;
  useEffect(() => {
    setConfirmTransition(null);
  }, [currentStatusId]);
  useEffect(() => {
    if (confirmTransition) confirmButton.current?.focus();
  }, [confirmTransition]);

  // Nombres conocidos para el historial: agentes (si se pudieron cargar) más las personas del ticket y comentarios.
  const knownUsers = useMemo<UserRef[]>(() => {
    const map = new Map<number, UserRef>();
    for (const u of agents) map.set(u.id, { id: u.id, display_name: u.display_name });
    for (const c of comments) if (c.author) map.set(c.author.id, c.author);
    if (ticket?.assignee) map.set(ticket.assignee.id, ticket.assignee);
    if (ticket?.requester) map.set(ticket.requester.id, ticket.requester);
    for (const h of history) if (h.actor) map.set(h.actor.id, h.actor);
    return Array.from(map.values());
  }, [agents, comments, history, ticket?.assignee, ticket?.requester]);

  // Mismas reglas que el backend (core/permissions.py); la API vuelve a validar todo.
  const canEdit = Boolean(user && ticket && canEditTicket(user, ticket));
  const availableTransitions = useMemo<WorkflowTransition[]>(
    () => (catalog && ticket && user ? transitionsFor(catalog.workflow, ticket, user) : []),
    [catalog, ticket, user],
  );

  const stateName = (id: number) => catalog?.workflow.states.find((s) => s.id === id)?.name ?? '';
  const isFinalTarget = (transition: WorkflowTransition) =>
    Boolean(catalog?.workflow.states.find((s) => s.id === transition.to_state_id)?.is_final);

  function requestTransition(transition: WorkflowTransition) {
    // Las transiciones a estados finales (cerrar, rechazar) piden confirmación a todos los roles.
    if (isFinalTarget(transition)) {
      setTransitionError(null);
      setConfirmTransition(transition);
      return;
    }
    void runTransition(transition);
  }

  async function runTransition(transition: WorkflowTransition) {
    if (!ticket) return;
    setConfirmTransition(null);
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
      const failure: TransitionFailure = {
        message: errorMessage(e),
        code: e instanceof ApiError ? e.code : null,
        status: e instanceof ApiError ? e.status : 0,
        transitionName: transition.name || stateName(transition.to_state_id),
        approvalRequestId: null,
      };
      const requestId = e instanceof ApiError ? e.detailObject?.approval_request_id : undefined;
      if (typeof requestId === 'number') failure.approvalRequestId = requestId;
      setTransitionError(failure);
      // Un 409 suele indicar que el ticket cambió (otro usuario, un guard que creó una aprobación...):
      // se recarga para mostrar el estado y las aprobaciones vigentes.
      if (failure.status === 409) void load(true);
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
          <div className="ticket-meta-row">
            <StatusBadge status={ticket.status} />
            <PriorityBadge priority={ticket.priority} />
            <span className="badge badge-outline">{ticket.type.name}</span>
            <SlaBadge ticket={ticket} />
          </div>
        }
        actions={
          <button type="button" className="btn" onClick={() => void load()} disabled={loading}>
            Actualizar
          </button>
        }
      />

      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
      <ErrorAlert error={loadError} onRetry={() => void load()} />

      <div className="grid-main-aside ticket-layout">
        <div className="stack-lg ticket-main" style={{ minWidth: 0 }}>
          <section className="panel ticket-slot ticket-slot-description" aria-labelledby="description-title">
            <div className="panel-header">
              <h2 className="panel-title" id="description-title">
                Descripción
              </h2>
              {canEdit && !editingContent && (
                <button type="button" className="btn btn-sm" onClick={() => setEditingContent(true)}>
                  Editar
                </button>
              )}
            </div>
            {editingContent ? (
              <TicketContentEditor
                ticket={ticket}
                onCancel={() => setEditingContent(false)}
                onSaved={(updated) => {
                  setTicket(updated);
                  setEditingContent(false);
                  void load(true);
                }}
              />
            ) : ticket.description ? (
              <p className="pre-wrap">{ticket.description}</p>
            ) : (
              <p className="muted">Sin descripción.</p>
            )}
          </section>

          {(availableTransitions.length > 0 || transitionError) && (
            <section className="panel stack ticket-slot ticket-slot-transitions" aria-labelledby="transitions-title">
              <div className="panel-header" style={{ marginBottom: 0 }}>
                <h2 className="panel-title" id="transitions-title">
                  {canEdit ? 'Cambiar estado' : 'Acciones'}
                </h2>
                <span className="panel-subtitle">Estado actual: {ticket.status.name}</span>
              </div>
              {transitionError && (
                <TransitionErrorAlert failure={transitionError} onDismiss={() => setTransitionError(null)} />
              )}
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
                      aria-expanded={isFinalTarget(tr) ? confirmTransition?.id === tr.id : undefined}
                      onClick={() => requestTransition(tr)}
                      title={tr.requires_approval ? 'Esta transición requiere aprobación' : `Pasar a ${stateName(tr.to_state_id)}`}
                    >
                      {transitioning === tr.id ? 'Aplicando…' : tr.name || stateName(tr.to_state_id)}
                      {tr.requires_approval && <span className="badge badge-warning">Aprobación</span>}
                    </button>
                  );
                })}
              </div>
              {confirmTransition && (
                <div
                  className="alert alert-warning"
                  role="alertdialog"
                  aria-labelledby="confirm-transition-title"
                  aria-describedby="confirm-transition-desc"
                >
                  <div className="alert-body stack-sm">
                    <div className="alert-title" id="confirm-transition-title">
                      ¿Confirma «{confirmTransition.name || stateName(confirmTransition.to_state_id)}» para {ticket.key}?
                    </div>
                    <p id="confirm-transition-desc">
                      El ticket pasará a «{stateName(confirmTransition.to_state_id)}», un estado final
                      {transitionComment.trim() ? ', con el comentario indicado' : ''}.
                    </p>
                    <div className="row-sm">
                      <button
                        ref={confirmButton}
                        type="button"
                        className="btn btn-sm btn-primary"
                        disabled={transitioning !== null}
                        onClick={() => void runTransition(confirmTransition)}
                      >
                        {transitioning === confirmTransition.id
                          ? 'Aplicando…'
                          : `Sí, ${(confirmTransition.name || stateName(confirmTransition.to_state_id)).toLowerCase()}`}
                      </button>
                      <button type="button" className="btn btn-sm" onClick={() => setConfirmTransition(null)}>
                        Cancelar
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </section>
          )}

          <div className="ticket-slot ticket-slot-approvals">
            <ApprovalsPanel ticket={ticket} onChange={reloadSilently} />
          </div>
          <div className="ticket-slot ticket-slot-attachments">
            <AttachmentsPanel ticket={ticket} />
          </div>

          <section className="panel ticket-slot ticket-slot-activity">
            <div className="tabs" role="tablist" aria-label="Actividad del ticket">
              <button
                type="button"
                role="tab"
                id="tab-comments"
                aria-controls="tabpanel-activity"
                aria-selected={tab === 'comments'}
                className={`tab${tab === 'comments' ? ' is-active' : ''}`}
                onClick={() => setTab('comments')}
              >
                Comentarios ({comments.length})
              </button>
              <button
                type="button"
                role="tab"
                id="tab-history"
                aria-controls="tabpanel-activity"
                aria-selected={tab === 'history'}
                className={`tab${tab === 'history' ? ' is-active' : ''}`}
                onClick={() => setTab('history')}
              >
                Historial ({history.length})
              </button>
            </div>
            <div
              role="tabpanel"
              id="tabpanel-activity"
              aria-labelledby={tab === 'comments' ? 'tab-comments' : 'tab-history'}
              style={{ paddingTop: 'var(--space-4)' }}
            >
              {tab === 'comments' ? (
                <TicketComments
                  ticketId={ticket.id}
                  comments={comments}
                  canComment
                  canUseInternal={canEdit}
                  onAdded={(comment) => {
                    setComments((prev) => [...prev, comment]);
                    void load(true);
                  }}
                />
              ) : (
                <TicketHistory entries={history} catalog={catalog} users={knownUsers} />
              )}
            </div>
          </section>
        </div>

        <aside className="stack ticket-aside" style={{ minWidth: 0 }}>
          <section className="panel ticket-slot ticket-slot-details">
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

          <div className="ticket-slot ticket-slot-sla">
            <SlaPanel ticket={ticket} />
          </div>

          {canEdit && catalog && user && (
            <div className="ticket-slot ticket-slot-manage">
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
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

interface TransitionFailure {
  message: string;
  code: string | null;
  status: number;
  transitionName: string;
  /** Solicitud de aprobación creada o pendiente (409 approval_required del módulo de flujos). */
  approvalRequestId: number | null;
}

/** Error de transición: destaca los bloqueos de guards (409), en especial la aprobación pendiente. */
function TransitionErrorAlert({ failure, onDismiss }: { failure: TransitionFailure; onDismiss: () => void }) {
  const dismiss = (
    <button type="button" className="btn btn-sm btn-ghost" onClick={onDismiss}>
      Cerrar
    </button>
  );
  if (failure.code === 'approval_required') {
    return (
      <Alert tone="warning" title="Aprobación requerida" action={dismiss}>
        <p>{failure.message}</p>
        <p className="text-sm" style={{ marginTop: 4 }}>
          Puede seguir su avance en el panel de aprobaciones de este ticket o en la{' '}
          <Link href="/approvals">bandeja de aprobaciones</Link>
          {failure.approvalRequestId !== null ? ` (solicitud #${failure.approvalRequestId})` : ''}.
        </p>
      </Alert>
    );
  }
  if (failure.status === 409) {
    return (
      <Alert tone="warning" title={`No se pudo aplicar «${failure.transitionName}»`} action={dismiss}>
        <p>{failure.message}</p>
        <p className="text-sm" style={{ marginTop: 4 }}>
          El ticket se recargó con su estado actual; verifique las acciones disponibles.
        </p>
      </Alert>
    );
  }
  return (
    <Alert tone="error" action={dismiss}>
      {failure.message}
    </Alert>
  );
}
