'use client';

/**
 * Estado del tablero: carga por columna (GET /tickets?status=...), "cargar más",
 * movimientos con actualización optimista y actualización en vivo por eventos.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, apiFetch, errorMessage } from '@/lib/api';
import { RESYNC_EVENT, useTicketEvents, type TicketEvent } from '@/lib/realtime';
import type { Me, TicketOut, TicketPage, Workflow, WorkflowState } from '@/lib/types';
import {
  allowedTargets,
  COLUMN_MAX,
  COLUMN_PAGE,
  columnQuery,
  compareTickets,
  matchesFilters,
  withStatus,
  type KanbanFilters,
} from './boardLogic';

export interface ColumnData {
  items: TicketOut[];
  total: number;
  limit: number;
  loading: boolean;
  error: string | null;
}

export type BoardColumns = Record<string, ColumnData>;

export type NoticeTone = 'success' | 'error' | 'warning' | 'info';

export interface Notice {
  id: number;
  tone: NoticeTone;
  text: string;
  href?: string;
}

/** Eventos con más tickets que esto en una ráfaga provocan una recarga completa. */
const MAX_TARGETED_REFRESH = 15;
const LIVE_BATCH_MS = 400;
/** Conciliación de totales tras cambios en vivo (los contadores pueden desviarse). */
const RECONCILE_MS = 20_000;

function emptyColumn(limit = COLUMN_PAGE): ColumnData {
  return { items: [], total: 0, limit, loading: true, error: null };
}

function locate(columns: BoardColumns, ticketId: number): { key: string; ticket: TicketOut } | null {
  for (const [key, column] of Object.entries(columns)) {
    const ticket = column.items.find((t) => t.id === ticketId);
    if (ticket) return { key, ticket };
  }
  return null;
}

function removeFrom(columns: BoardColumns, ticketId: number, adjustTotal = true): BoardColumns {
  const found = locate(columns, ticketId);
  if (!found) return columns;
  const column = columns[found.key];
  return {
    ...columns,
    [found.key]: {
      ...column,
      items: column.items.filter((t) => t.id !== ticketId),
      total: adjustTotal ? Math.max(0, column.total - 1) : column.total,
    },
  };
}

/**
 * Coloca `ticket` en la columna de su estado (si existe), quitándolo de donde estuviera.
 *
 * Si el ticket no estaba cargado en el tablero, `knownNew` indica que con certeza entra a
 * la columna (creado o transicionado hacia ella). Si no se sabe y la columna tiene
 * tickets sin cargar, podría ser uno de ellos: no se altera el total y solo se muestra si
 * su orden cae dentro de lo cargado.
 */
function place(columns: BoardColumns, ticket: TicketOut, knownNew = false): BoardColumns {
  const found = locate(columns, ticket.id);
  const targetKey = ticket.status.key;
  let next = columns;
  if (found && found.key !== targetKey) next = removeFrom(next, ticket.id);
  const target = next[targetKey];
  if (!target) return next;
  if (found && found.key === targetKey) {
    const items = target.items.map((t) => (t.id === ticket.id ? ticket : t)).sort(compareTickets);
    return { ...next, [targetKey]: { ...target, items } };
  }
  const complete = target.items.length >= target.total;
  const counts = Boolean(found) || knownNew || complete;
  const last = target.items[target.items.length - 1];
  const fitsLoaded = complete || !last || compareTickets(ticket, last) < 0;
  const items = fitsLoaded ? [...target.items, ticket].sort(compareTickets) : target.items;
  return { ...next, [targetKey]: { ...target, items, total: target.total + (counts ? 1 : 0) } };
}

export interface KanbanBoardState {
  columns: BoardColumns;
  states: WorkflowState[];
  pending: ReadonlySet<number>;
  notices: Notice[];
  initialLoading: boolean;
  reload: (options?: { silent?: boolean }) => void;
  loadMore: (stateKey: string) => void;
  moveTicket: (ticket: TicketOut, toStateKey: string) => Promise<void>;
  /** Coloca un ticket actualizado fuera de `moveTicket` (p.ej. tras el diálogo de solución). */
  applyTicket: (updated: TicketOut, message?: string) => void;
  /** Muestra un aviso en el tablero. */
  notify: (tone: NoticeTone, text: string, href?: string) => void;
  dismissNotice: (id: number) => void;
  lastLiveUpdate: number | null;
}

export function useKanbanBoard({
  workflow,
  filters,
  me,
  isAgent,
}: {
  workflow: Workflow | null;
  filters: KanbanFilters;
  me: Me | null;
  isAgent: boolean;
}): KanbanBoardState {
  const states = useMemo(() => {
    if (!workflow) return [];
    const ordered = [...workflow.states].sort((a, b) => a.order - b.order || a.id - b.id);
    return filters.hideFinal ? ordered.filter((s) => !s.is_final) : ordered;
  }, [workflow, filters.hideFinal]);

  const [columns, setColumns] = useState<BoardColumns>({});
  const [pending, setPending] = useState<ReadonlySet<number>>(new Set());
  const [notices, setNotices] = useState<Notice[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [lastLiveUpdate, setLastLiveUpdate] = useState<number | null>(null);

  const columnsRef = useRef(columns);
  columnsRef.current = columns;
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const meRef = useRef(me);
  meRef.current = me;

  const noticeSeq = useRef(0);
  const noticeTimers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismissNotice = useCallback((id: number) => {
    setNotices((list) => list.filter((n) => n.id !== id));
    const timer = noticeTimers.current.get(id);
    if (timer) clearTimeout(timer);
    noticeTimers.current.delete(id);
  }, []);

  const notify = useCallback(
    (tone: NoticeTone, text: string, href?: string) => {
      const id = ++noticeSeq.current;
      setNotices((list) => [...list.slice(-2), { id, tone, text, href }]);
      const timeout = tone === 'error' || tone === 'warning' ? 12_000 : 5_000;
      noticeTimers.current.set(
        id,
        setTimeout(() => dismissNotice(id), timeout),
      );
    },
    [dismissNotice],
  );

  useEffect(() => {
    const timers = noticeTimers.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  // ---------- Carga ----------

  const loadSeq = useRef(0);
  const controllers = useRef(new Map<string, AbortController>());

  const loadColumn = useCallback(
    async (state: WorkflowState, limit: number, seq: number, silent: boolean) => {
      controllers.current.get(state.key)?.abort();
      const controller = new AbortController();
      controllers.current.set(state.key, controller);
      if (!silent) {
        setColumns((prev) => ({
          ...prev,
          [state.key]: { ...(prev[state.key] ?? emptyColumn(limit)), limit, loading: true, error: null },
        }));
      }
      try {
        const page = await apiFetch<TicketPage>('/tickets', {
          query: columnQuery(state.key, filtersRef.current, meRef.current, limit),
          signal: controller.signal,
        });
        if (controller.signal.aborted || seq !== loadSeq.current) return;
        setColumns((prev) => {
          const previous = prev[state.key];
          // Conserva la versión optimista de los tickets con un movimiento en curso.
          const inFlight = new Set(pendingRef.current);
          const items = page.items
            .filter((t) => !inFlight.has(t.id))
            .concat(previous ? previous.items.filter((t) => inFlight.has(t.id)) : []);
          items.sort(compareTickets);
          return { ...prev, [state.key]: { items, total: page.total, limit, loading: false, error: null } };
        });
      } catch (error) {
        if (controller.signal.aborted || seq !== loadSeq.current) return;
        setColumns((prev) => ({
          ...prev,
          [state.key]: {
            ...(prev[state.key] ?? emptyColumn(limit)),
            limit,
            loading: false,
            error: errorMessage(error),
          },
        }));
      } finally {
        if (controllers.current.get(state.key) === controller) controllers.current.delete(state.key);
      }
    },
    [],
  );

  const loadAll = useCallback(
    async (silent: boolean) => {
      if (states.length === 0) return;
      const seq = ++loadSeq.current;
      await Promise.all(
        states.map((state) => {
          const limit = columnsRef.current[state.key]?.limit ?? COLUMN_PAGE;
          return loadColumn(state, silent ? limit : COLUMN_PAGE, seq, silent);
        }),
      );
      if (seq === loadSeq.current) setInitialLoading(false);
    },
    [states, loadColumn],
  );

  // Recarga completa cuando cambian los filtros, la identidad o las columnas visibles.
  const filtersKey = JSON.stringify(filters);
  useEffect(() => {
    if (!workflow || !me) return;
    setColumns((prev) => {
      const next: BoardColumns = {};
      for (const state of states) next[state.key] = { ...(prev[state.key] ?? emptyColumn()), limit: COLUMN_PAGE };
      return next;
    });
    void loadAll(false);
    const active = controllers.current;
    return () => {
      for (const controller of active.values()) controller.abort();
      active.clear();
    };
  }, [workflow, me?.id, filtersKey, loadAll]);

  const reload = useCallback(
    (options?: { silent?: boolean }) => {
      void loadAll(options?.silent ?? false);
    },
    [loadAll],
  );

  const loadMore = useCallback(
    (stateKey: string) => {
      const state = states.find((s) => s.key === stateKey);
      const column = columnsRef.current[stateKey];
      if (!state || !column || column.loading) return;
      const limit = Math.min(COLUMN_MAX, column.limit + COLUMN_PAGE);
      void loadColumn(state, limit, loadSeq.current, false);
    },
    [states, loadColumn],
  );

  // ---------- Tiempo real ----------

  const queued = useRef(new Map<number, TicketEvent>());
  const batchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconcileTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleReconcile = useCallback(() => {
    if (reconcileTimer.current) return;
    reconcileTimer.current = setTimeout(() => {
      reconcileTimer.current = null;
      void loadAll(true);
    }, RECONCILE_MS);
  }, [loadAll]);

  const refreshTicket = useCallback(
    async (ticketId: number, event: TicketEvent) => {
      if (pendingRef.current.has(ticketId)) return; // lo actualiza la respuesta del movimiento
      try {
        const ticket = await apiFetch<TicketOut>(`/tickets/${ticketId}`);
        if (pendingRef.current.has(ticketId)) return;
        setColumns((prev) => {
          if (!matchesFilters(ticket, filtersRef.current, meRef.current)) return removeFrom(prev, ticketId);
          let next = prev;
          if (!locate(prev, ticketId) && event.type === 'ticket.transitioned') {
            // Estaba fuera de lo cargado en su columna de origen: solo ajustar el total.
            const fromKey = typeof event.data.from_state === 'string' ? event.data.from_state : null;
            const from = fromKey ? prev[fromKey] : undefined;
            if (fromKey && from && from.total > 0 && fromKey !== ticket.status.key) {
              next = { ...prev, [fromKey]: { ...from, total: from.total - 1 } };
            }
          }
          const knownNew = event.type === 'ticket.created' || event.type === 'ticket.transitioned';
          return place(next, ticket, knownNew);
        });
      } catch (error) {
        if (error instanceof ApiError && (error.isNotFound || error.isForbidden)) {
          setColumns((prev) => removeFrom(prev, ticketId));
        }
        // Otros errores (red): la conciliación periódica corrige el tablero.
      }
    },
    [],
  );

  const flushQueue = useCallback(() => {
    batchTimer.current = null;
    const batch = Array.from(queued.current.entries());
    queued.current.clear();
    if (batch.length === 0) return;
    setLastLiveUpdate(Date.now());
    if (batch.length > MAX_TARGETED_REFRESH) {
      void loadAll(true);
      return;
    }
    for (const [ticketId, event] of batch) void refreshTicket(ticketId, event);
    scheduleReconcile();
  }, [loadAll, refreshTicket, scheduleReconcile]);

  const onEvent = useCallback(
    (event: TicketEvent) => {
      if (event.type === RESYNC_EVENT) {
        queued.current.clear();
        void loadAll(true);
        return;
      }
      if (!event.ticket_id || event.ticket_id <= 0) return;
      if (event.type === 'ticket.hidden') {
        setColumns((prev) => removeFrom(prev, event.ticket_id));
        setLastLiveUpdate(Date.now());
        return;
      }
      if (event.type.startsWith('notification.')) return;
      queued.current.set(event.ticket_id, event);
      if (!batchTimer.current) batchTimer.current = setTimeout(flushQueue, LIVE_BATCH_MS);
    },
    [flushQueue, loadAll],
  );
  useTicketEvents(onEvent);

  useEffect(
    () => () => {
      if (batchTimer.current) clearTimeout(batchTimer.current);
      if (reconcileTimer.current) clearTimeout(reconcileTimer.current);
    },
    [],
  );

  // ---------- Movimientos ----------

  const moveTicket = useCallback(
    async (ticket: TicketOut, toStateKey: string) => {
      if (!workflow || pendingRef.current.has(ticket.id)) return;
      if (ticket.status.key === toStateKey) return;
      const target = allowedTargets(ticket, workflow, me, isAgent).find((t) => t.state.key === toStateKey);
      if (!target) {
        notify('warning', `No existe una transición permitida de «${ticket.status.name}» a ese estado para ${ticket.key}.`);
        return;
      }

      const original = ticket;
      let failed = false;
      setPending((prev) => new Set(prev).add(ticket.id));
      pendingRef.current = new Set(pendingRef.current).add(ticket.id);
      setColumns((prev) => place(prev, withStatus(ticket, target.state)));

      try {
        const updated = await apiFetch<TicketOut>(`/tickets/${ticket.id}/transition`, {
          method: 'POST',
          body: { to_state_key: toStateKey },
        });
        setColumns((prev) =>
          matchesFilters(updated, filtersRef.current, meRef.current)
            ? place(prev, updated)
            : removeFrom(prev, updated.id),
        );
        if (updated.status.key !== toStateKey) {
          notify(
            'info',
            `${ticket.key} quedó en «${updated.status.name}»${target.transition.requires_approval ? ': la transición requiere aprobación.' : '.'}`,
            `/tickets/${ticket.id}`,
          );
        } else {
          notify('success', `${ticket.key} movido a «${updated.status.name}».`);
        }
      } catch (error) {
        failed = true;
        setColumns((prev) => place(prev, original));
        if (error instanceof ApiError && error.isNotFound) {
          setColumns((prev) => removeFrom(prev, ticket.id));
          notify('error', `${ticket.key} ya no existe o dejó de estar visible para usted.`);
        } else if (target.transition.requires_approval && error instanceof ApiError && error.status === 409) {
          notify(
            'warning',
            `«${target.transition.name || target.state.name}» requiere aprobación: ${errorMessage(error)}`,
            `/tickets/${ticket.id}`,
          );
        } else {
          notify('error', `No se pudo mover ${ticket.key}: ${errorMessage(error)}`);
        }
      } finally {
        const next = new Set(pendingRef.current);
        next.delete(ticket.id);
        pendingRef.current = next;
        setPending(next);
      }
      if (failed) {
        // El ticket pudo cambiar en el servidor (p.ej. otro agente lo movió): refrescarlo.
        void refreshTicket(ticket.id, {
          type: 'ticket.updated',
          ticket_id: ticket.id,
          ticket_key: ticket.key,
          actor_id: null,
          data: {},
          ts: new Date().toISOString(),
          department_id: null,
          team_id: null,
          requester_id: null,
          assignee_id: null,
        });
      }
    },
    [workflow, me, isAgent, notify, refreshTicket],
  );

  const applyTicket = useCallback(
    (updated: TicketOut, message?: string) => {
      setColumns((prev) =>
        matchesFilters(updated, filtersRef.current, meRef.current) ? place(prev, updated) : removeFrom(prev, updated.id),
      );
      if (message) notify('success', message);
    },
    [notify],
  );

  return {
    columns,
    states,
    pending,
    notices,
    initialLoading,
    reload,
    loadMore,
    moveTicket,
    applyTicket,
    notify,
    dismissNotice,
    lastLiveUpdate,
  };
}
