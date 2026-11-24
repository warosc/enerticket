'use client';

import { useDraggable } from '@dnd-kit/core';
import Link from 'next/link';
import { memo, useEffect, useRef, useState, type ChangeEvent, type SyntheticEvent } from 'react';
import SlaBadge from '@/components/ticket/SlaBadge';
import { PriorityBadge, Spinner } from '@/components/ui';
import { formatDateTime, formatRelative, initials } from '@/lib/format';
import type { TicketOut } from '@/lib/types';
import type { MoveTarget } from './boardLogic';
import styles from './kanban.module.css';

export const cardDragId = (ticketId: number): string => `ticket-${ticketId}`;

function priorityClass(key: string): string {
  return styles[`prio_${key}`] ?? '';
}

/** Contenido visual de la tarjeta (se usa también en el DragOverlay). */
function CardContent({
  ticket,
  interactive,
  pending,
  targets,
  onMove,
}: {
  ticket: TicketOut;
  interactive: boolean;
  pending?: boolean;
  targets?: readonly MoveTarget[];
  onMove?: (ticket: TicketOut, toStateKey: string) => void;
}) {
  const location = [ticket.team?.name, ticket.department.name].filter(Boolean).join(' · ');
  // El selector no debe iniciar un arrastre (ratón, táctil ni teclado).
  const stop = (event: SyntheticEvent) => event.stopPropagation();
  const onSelect = (event: ChangeEvent<HTMLSelectElement>) => {
    const value = event.target.value;
    event.target.value = '';
    if (value && onMove) onMove(ticket, value);
  };

  return (
    <>
      <div className={styles.cardTop}>
        {interactive ? (
          <Link
            href={`/tickets/${ticket.id}`}
            className={styles.cardKey}
            title="Abrir detalle del ticket"
            draggable={false}
          >
            {ticket.key}
          </Link>
        ) : (
          <span className={styles.cardKey}>{ticket.key}</span>
        )}
        <span className={styles.cardTopRight}>
          {pending && <Spinner label="Guardando movimiento" />}
          <PriorityBadge priority={ticket.priority} />
        </span>
      </div>
      {interactive ? (
        <Link href={`/tickets/${ticket.id}`} className={styles.cardTitle} draggable={false}>
          {ticket.title}
        </Link>
      ) : (
        <span className={styles.cardTitle}>{ticket.title}</span>
      )}
      {location && (
        <div className={styles.cardMeta} title={location}>
          {location}
        </div>
      )}
      <div className={styles.cardFooter}>
        <span className={styles.assignee} title={ticket.assignee ? `Asignado a ${ticket.assignee.display_name}` : 'Sin asignar'}>
          <span className={`${styles.avatar} ${ticket.assignee ? '' : styles.avatarEmpty}`} aria-hidden="true">
            {ticket.assignee ? initials(ticket.assignee.display_name) : '—'}
          </span>
          <span className={styles.assigneeName}>{ticket.assignee ? ticket.assignee.display_name : 'Sin asignar'}</span>
        </span>
        <span className={styles.cardTime} title={`Actualizado: ${formatDateTime(ticket.updated_at)}`}>
          {formatRelative(ticket.updated_at)}
        </span>
      </div>
      <div className={styles.cardSla}>
        <SlaBadge ticket={ticket} />
      </div>
      {interactive && targets && targets.length > 0 && onMove && (
        <select
          className={`select select-sm ${styles.moveSelect}`}
          aria-label={`Mover ${ticket.key} a otro estado`}
          value=""
          disabled={pending}
          onChange={onSelect}
          onPointerDown={stop}
          onMouseDown={stop}
          onTouchStart={stop}
          onKeyDown={stop}
        >
          <option value="">Mover a…</option>
          {targets.map(({ state, transition }) => (
            <option key={state.id} value={state.key}>
              {state.name}
              {transition.requires_approval ? ' (requiere aprobación)' : ''}
            </option>
          ))}
        </select>
      )}
    </>
  );
}

function KanbanCardImpl({
  ticket,
  targets,
  canDrag,
  pending,
  onMove,
}: {
  ticket: TicketOut;
  targets: readonly MoveTarget[];
  canDrag: boolean;
  pending: boolean;
  onMove: (ticket: TicketOut, toStateKey: string) => void;
}) {
  const disabled = !canDrag || pending || targets.length === 0;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } = useDraggable({
    id: cardDragId(ticket.id),
    data: { ticket },
    disabled,
  });

  // Resalta brevemente la tarjeta cuando llega un cambio (en vivo o propio).
  const [flash, setFlash] = useState(false);
  const lastUpdate = useRef(ticket.updated_at);
  useEffect(() => {
    if (lastUpdate.current === ticket.updated_at) return;
    lastUpdate.current = ticket.updated_at;
    setFlash(true);
    const id = setTimeout(() => setFlash(false), 1400);
    return () => clearTimeout(id);
  }, [ticket.updated_at]);

  const setRefs = (node: HTMLElement | null) => {
    setNodeRef(node);
    setActivatorNodeRef(node);
  };

  const description = [
    `${ticket.key}: ${ticket.title}`,
    `Prioridad ${ticket.priority.name}`,
    ticket.assignee ? `asignado a ${ticket.assignee.display_name}` : 'sin asignar',
  ].join('. ');

  return (
    <article
      ref={setRefs}
      className={[
        styles.card,
        priorityClass(ticket.priority.key),
        disabled ? styles.cardStatic : '',
        isDragging ? styles.cardDragging : '',
        pending ? styles.cardPending : '',
        flash ? styles.cardFlash : '',
      ].join(' ')}
      {...attributes}
      {...listeners}
      aria-roledescription={disabled ? undefined : 'ticket arrastrable'}
      aria-label={description}
      aria-busy={pending || undefined}
      tabIndex={0}
      role={disabled ? 'group' : attributes.role}
    >
      <CardContent ticket={ticket} interactive pending={pending} targets={canDrag ? targets : []} onMove={onMove} />
    </article>
  );
}

export const KanbanCard = memo(KanbanCardImpl);

/** Tarjeta flotante mientras se arrastra. */
export function KanbanCardOverlay({ ticket }: { ticket: TicketOut }) {
  return (
    <div className={`${styles.card} ${styles.cardOverlay} ${priorityClass(ticket.priority.key)}`}>
      <CardContent ticket={ticket} interactive={false} />
    </div>
  );
}
