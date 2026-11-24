'use client';

/**
 * Tablero Kanban por estados del workflow (GET /catalog) con arrastrar y soltar
 * (ratón, táctil y teclado), filtros sincronizados con la URL y actualización en vivo.
 */
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  pointerWithin,
  rectIntersection,
  useSensor,
  useSensors,
  type Announcements,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
  type KeyboardCoordinateGetter,
} from '@dnd-kit/core';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Icon } from '@/components/icons';
import { Alert, CollapsibleFilters, EmptyState, ErrorAlert, Loading, PageHeader } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { formatNumber, formatRelative } from '@/lib/format';
import { useCatalog } from '@/lib/hooks';
import type { TicketOut, UserOut, WorkflowState } from '@/lib/types';
import {
  activeFilterCount,
  buildTargetIndex,
  EMPTY_FILTERS,
  filtersToSearch,
  NO_TARGETS,
  parseFilters,
  targetsFor,
  type KanbanFilters as Filters,
  type MoveTarget,
} from './boardLogic';
import KanbanColumn, { type DropRole } from './KanbanColumn';
import KanbanFilters from './KanbanFilters';
import { KanbanCardOverlay } from './KanbanCard';
import { useKanbanBoard } from './useKanbanBoard';
import styles from './kanban.module.css';

/** Con teclado, las flechas izquierda/derecha saltan a la columna válida siguiente. */
const columnCoordinateGetter: KeyboardCoordinateGetter = (event, { currentCoordinates, context }) => {
  const { collisionRect, droppableRects, droppableContainers } = context;
  if (event.code === 'ArrowUp' || event.code === 'ArrowDown') {
    event.preventDefault();
    return currentCoordinates;
  }
  const direction = event.code === 'ArrowRight' ? 1 : event.code === 'ArrowLeft' ? -1 : 0;
  if (!direction || !collisionRect) return undefined;
  event.preventDefault();
  const centerX = collisionRect.left + collisionRect.width / 2;
  const candidates = droppableContainers
    .getEnabled()
    .map((container) => droppableRects.get(container.id))
    .filter((rect): rect is NonNullable<typeof rect> => Boolean(rect))
    .map((rect) => ({ rect, center: rect.left + rect.width / 2 }))
    .filter(({ center }) => (direction > 0 ? center > centerX + 1 : center < centerX - 1))
    .sort((a, b) => (direction > 0 ? a.center - b.center : b.center - a.center));
  const target = candidates[0];
  if (!target) return currentCoordinates;
  const top = Math.max(target.rect.top + 48, Math.min(collisionRect.top, target.rect.bottom - collisionRect.height - 8));
  return {
    x: currentCoordinates.x + (target.center - centerX),
    y: currentCoordinates.y + (top - collisionRect.top),
  };
};

/** Puntero dentro de una columna; con teclado (sin puntero), intersección de rectángulos. */
const collisionDetection: CollisionDetection = (args) => {
  const within = pointerWithin(args);
  return within.length > 0 ? within : rectIntersection(args);
};

function ticketFrom(data: unknown): TicketOut | null {
  const ticket = (data as { ticket?: TicketOut } | undefined)?.ticket;
  return ticket ?? null;
}

function stateFrom(data: unknown): WorkflowState | null {
  const state = (data as { state?: WorkflowState } | undefined)?.state;
  return state ?? null;
}

export default function KanbanBoard() {
  const router = useRouter();
  const pathname = usePathname() || '/kanban';
  const searchParams = useSearchParams();
  const auth = useAuth();
  const me = auth.user;
  const isAgent = auth.isAgent;
  const { catalog, error: catalogError, loading: catalogLoading, reload: reloadCatalog } = useCatalog();

  const searchKey = searchParams.toString();
  const filters = useMemo(() => parseFilters(new URLSearchParams(searchKey)), [searchKey]);

  const updateFilters = useCallback(
    (patch: Partial<Filters>) => {
      const next = { ...filters, ...patch };
      const qs = filtersToSearch(next);
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [filters, pathname, router],
  );

  // Agentes para el filtro "Asignado" (GET /users requiere agent+).
  const [agents, setAgents] = useState<UserOut[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  useEffect(() => {
    if (!isAgent) return;
    const controller = new AbortController();
    apiFetch<UserOut[]>('/users', { query: { role: 'agent', limit: 200 }, signal: controller.signal })
      .then((list) => {
        setAgents(list.filter((u) => u.active));
        setAgentsError(null);
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setAgentsError(errorMessage(e));
      });
    return () => controller.abort();
  }, [isAgent]);

  const workflow = catalog?.workflow ?? null;
  const board = useKanbanBoard({ workflow, filters, me, isAgent });

  const targetIndex = useMemo(() => (workflow ? buildTargetIndex(workflow) : null), [workflow]);
  const getTargets = useCallback(
    (ticket: TicketOut): readonly MoveTarget[] =>
      targetIndex ? targetsFor(targetIndex, ticket, me, isAgent) : NO_TARGETS,
    [targetIndex, me, isAgent],
  );
  const canDrag = useCallback(
    (ticket: TicketOut) => Boolean(me) && (isAgent || ticket.requester.id === me?.id),
    [me, isAgent],
  );

  // ---------- Arrastrar y soltar ----------

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: columnCoordinateGetter }),
  );

  const [activeTicket, setActiveTicket] = useState<TicketOut | null>(null);
  const activeTargets = useMemo(
    () => (activeTicket ? getTargets(activeTicket) : NO_TARGETS),
    [activeTicket, getTargets],
  );

  const dropRoleFor = useCallback(
    (state: WorkflowState): DropRole => {
      if (!activeTicket) return 'idle';
      if (state.id === activeTicket.status.id) return 'source';
      const target = activeTargets.find((t) => t.state.id === state.id);
      if (!target) return 'invalid';
      return target.transition.requires_approval ? 'approval' : 'valid';
    },
    [activeTicket, activeTargets],
  );

  const onDragStart = useCallback((event: DragStartEvent) => {
    setActiveTicket(ticketFrom(event.active.data.current));
  }, []);

  const { moveTicket } = board;
  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      const ticket = ticketFrom(event.active.data.current);
      setActiveTicket(null);
      const state = stateFrom(event.over?.data.current);
      if (!ticket || !state || state.id === ticket.status.id) return;
      void moveTicket(ticket, state.key);
    },
    [moveTicket],
  );

  const onDragCancel = useCallback(() => setActiveTicket(null), []);

  const announcements = useMemo<Announcements>(
    () => ({
      onDragStart({ active }) {
        const ticket = ticketFrom(active.data.current);
        return `Ticket ${ticket?.key ?? ''} tomado. Use las flechas izquierda y derecha para elegir una columna válida, Espacio o Enter para soltar y Escape para cancelar.`;
      },
      onDragOver({ active, over }) {
        const ticket = ticketFrom(active.data.current);
        const state = stateFrom(over?.data.current);
        return state
          ? `Ticket ${ticket?.key ?? ''} sobre la columna ${state.name}.`
          : `Ticket ${ticket?.key ?? ''} fuera de una columna válida.`;
      },
      onDragEnd({ active, over }) {
        const ticket = ticketFrom(active.data.current);
        const state = stateFrom(over?.data.current);
        return state
          ? `Ticket ${ticket?.key ?? ''} soltado en ${state.name}.`
          : `Ticket ${ticket?.key ?? ''} soltado sin cambios.`;
      },
      onDragCancel({ active }) {
        const ticket = ticketFrom(active.data.current);
        return `Movimiento cancelado. ${ticket?.key ?? 'El ticket'} vuelve a su columna.`;
      },
    }),
    [],
  );

  const onMove = useCallback((ticket: TicketOut, toStateKey: string) => void moveTicket(ticket, toStateKey), [moveTicket]);
  const onRetry = useCallback(() => board.reload(), [board]);

  // ---------- Render ----------

  const totalVisible = board.states.reduce((sum, s) => sum + (board.columns[s.key]?.total ?? 0), 0);
  const allLoaded = board.states.length > 0 && board.states.every((s) => board.columns[s.key] && !board.columns[s.key].loading);
  const filterCount = activeFilterCount(filters);
  const [, setTick] = useState(0);
  useEffect(() => {
    // Refresca el texto "actualizado hace…" cada 30 s.
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="page">
      <PageHeader
        title="Tablero Kanban"
        subtitle="Arrastre los tickets entre columnas para cambiar su estado. Los cambios de otros usuarios aparecen en vivo."
        actions={
          <>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => board.reload()}
              disabled={!catalog}
              aria-label="Recargar el tablero"
            >
              <Icon name="refresh" width={16} height={16} aria-hidden="true" />
              Recargar
            </button>
            <Link href="/tickets" className="btn btn-sm">
              Vista de lista
            </Link>
          </>
        }
      />

      {catalogError && <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />}

      {!isAgent && me && (
        <Alert tone="info">
          Como solicitante puede cerrar o reabrir sus propios tickets arrastrándolos; el resto de cambios los realiza el
          equipo de soporte.
        </Alert>
      )}

      {catalog && (
        <CollapsibleFilters activeCount={filterCount}>
          <KanbanFilters
            filters={filters}
            catalog={catalog}
            agents={agents}
            agentsError={agentsError}
            canFilterByAssignee={isAgent}
            onChange={updateFilters}
          />
        </CollapsibleFilters>
      )}

      {board.notices.length > 0 && (
        <div className={styles.notices} aria-live="polite">
          {board.notices.map((notice) => (
            <Alert
              key={notice.id}
              tone={notice.tone}
              action={
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => board.dismissNotice(notice.id)}
                  aria-label="Descartar aviso"
                >
                  ✕
                </button>
              }
            >
              {notice.text}
              {notice.href && (
                <Link className={styles.noticeLink} href={notice.href}>
                  Ver ticket
                </Link>
              )}
            </Alert>
          ))}
        </div>
      )}

      {!catalog ? (
        catalogLoading ? <Loading label="Cargando flujo de trabajo…" /> : null
      ) : board.states.length === 0 ? (
        <section className="panel">
          <EmptyState title="No hay estados para mostrar">
            {filters.hideFinal
              ? 'Todos los estados del flujo son finales. Desactive «Ocultar estados finales».'
              : 'El flujo de trabajo no tiene estados configurados.'}
          </EmptyState>
        </section>
      ) : (
        <>
          <div className={styles.statusBar}>
            <span>
              {allLoaded ? `${formatNumber(totalVisible)} tickets visibles` : 'Cargando tickets…'}
              {filterCount > 0 ? ` con ${filterCount} filtro${filterCount === 1 ? '' : 's'}` : ''}
            </span>
            {board.lastLiveUpdate && <span>Última actualización en vivo {formatRelative(new Date(board.lastLiveUpdate))}</span>}
            {allLoaded && totalVisible === 0 && filterCount > 0 && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => updateFilters(EMPTY_FILTERS)}>
                Ningún ticket coincide: limpiar filtros
              </button>
            )}
          </div>

          <DndContext
            sensors={sensors}
            collisionDetection={collisionDetection}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
            onDragCancel={onDragCancel}
            accessibility={{
              announcements,
              screenReaderInstructions: {
                draggable:
                  'Para mover un ticket, presione Espacio o Enter. Use las flechas izquierda y derecha para elegir la columna destino y Espacio o Enter para soltar. Escape cancela.',
              },
            }}
          >
            <div className={styles.boardWrap}>
              <div className={styles.board} role="region" aria-label="Columnas del tablero" tabIndex={-1}>
                {board.states.map((state) => (
                  <KanbanColumn
                    key={state.id}
                    state={state}
                    column={board.columns[state.key]}
                    dropRole={dropRoleFor(state)}
                    pending={board.pending}
                    canDrag={canDrag}
                    targetsFor={getTargets}
                    onMove={onMove}
                    onLoadMore={board.loadMore}
                    onRetry={onRetry}
                  />
                ))}
              </div>
            </div>
            <DragOverlay dropAnimation={null}>{activeTicket ? <KanbanCardOverlay ticket={activeTicket} /> : null}</DragOverlay>
          </DndContext>
        </>
      )}
    </div>
  );
}
