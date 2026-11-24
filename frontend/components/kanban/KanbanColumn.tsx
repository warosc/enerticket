'use client';

import { useDroppable } from '@dnd-kit/core';
import Link from 'next/link';
import { Spinner } from '@/components/ui';
import { formatNumber } from '@/lib/format';
import type { TicketOut, WorkflowState } from '@/lib/types';
import { COLUMN_MAX, COLUMN_PAGE, type MoveTarget } from './boardLogic';
import { KanbanCard } from './KanbanCard';
import type { ColumnData } from './useKanbanBoard';
import styles from './kanban.module.css';

export type DropRole = 'idle' | 'source' | 'valid' | 'approval' | 'invalid';

export const columnDropId = (stateKey: string): string => `state-${stateKey}`;

export default function KanbanColumn({
  state,
  column,
  dropRole,
  pending,
  canDrag,
  targetsFor,
  onMove,
  onLoadMore,
  onRetry,
}: {
  state: WorkflowState;
  column: ColumnData | undefined;
  dropRole: DropRole;
  pending: ReadonlySet<number>;
  canDrag: (ticket: TicketOut) => boolean;
  targetsFor: (ticket: TicketOut) => readonly MoveTarget[];
  onMove: (ticket: TicketOut, toStateKey: string) => void;
  onLoadMore: (stateKey: string) => void;
  onRetry: () => void;
}) {
  const droppable = dropRole === 'valid' || dropRole === 'approval';
  const { setNodeRef, isOver } = useDroppable({
    id: columnDropId(state.key),
    data: { state },
    disabled: !droppable,
  });

  const items = column?.items ?? [];
  const total = column?.total ?? 0;
  const remaining = Math.max(0, total - items.length);
  const titleId = `kanban-col-${state.key}`;

  const roleClass =
    dropRole === 'valid'
      ? styles.columnValid
      : dropRole === 'approval'
        ? styles.columnApproval
        : dropRole === 'invalid'
          ? styles.columnDisabled
          : dropRole === 'source'
            ? styles.columnSource
            : '';

  return (
    <section
      ref={setNodeRef}
      className={`${styles.column} ${roleClass} ${isOver && droppable ? styles.columnOver : ''}`}
      aria-labelledby={titleId}
    >
      <header className={`${styles.columnHeader} ${styles[`cat_${state.category}`] ?? ''}`}>
        <h2 id={titleId} className={styles.columnTitle} title={state.name}>
          {state.name}
        </h2>
        {dropRole === 'valid' && <span className={styles.dropBadge}>Soltar aquí</span>}
        {dropRole === 'approval' && (
          <span className={`${styles.dropBadge} ${styles.dropBadgeApproval}`}>Requiere aprobación</span>
        )}
        <span className={styles.columnCount} aria-label={`${total} tickets`}>
          {column?.loading && items.length === 0 ? '…' : formatNumber(total)}
        </span>
      </header>

      <div className={styles.columnBody}>
        {column?.error ? (
          <div className="stack-sm">
            <p className="text-xs" role="alert" style={{ color: 'var(--color-danger)' }}>
              {column.error}
            </p>
            <button type="button" className="btn btn-sm" onClick={onRetry}>
              Reintentar
            </button>
          </div>
        ) : column === undefined || (column.loading && items.length === 0) ? (
          <div className={styles.columnHint}>
            <Spinner label={`Cargando ${state.name}`} />
          </div>
        ) : items.length === 0 ? (
          <div className={styles.columnEmpty}>{droppable ? 'Suelte el ticket aquí' : 'Sin tickets'}</div>
        ) : (
          items.map((ticket) => (
            <KanbanCard
              key={ticket.id}
              ticket={ticket}
              targets={targetsFor(ticket)}
              canDrag={canDrag(ticket)}
              pending={pending.has(ticket.id)}
              onMove={onMove}
            />
          ))
        )}
      </div>

      {column && !column.error && remaining > 0 && (
        <footer className={styles.columnFooter}>
          {column.limit < COLUMN_MAX ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm btn-block"
              onClick={() => onLoadMore(state.key)}
              disabled={column.loading}
            >
              {column.loading ? 'Cargando…' : `Cargar ${Math.min(COLUMN_PAGE, remaining)} más (${formatNumber(remaining)} restantes)`}
            </button>
          ) : (
            <Link className={`btn btn-ghost btn-sm btn-block`} href={`/tickets?status=${encodeURIComponent(state.key)}`}>
              Ver los {formatNumber(remaining)} restantes en la lista
            </Link>
          )}
        </footer>
      )}
    </section>
  );
}
