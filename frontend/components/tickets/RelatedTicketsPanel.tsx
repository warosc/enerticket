'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { StatusBadge } from '@/components/ui';
import { apiFetch } from '@/lib/api';
import type { TicketOut } from '@/lib/types';
import styles from './tickets.module.css';

type Related = { id: number; ticket: TicketOut | null };

const MAX_LOADED = 20;

/** Ticket padre y órdenes de trabajo hijas. Los que el usuario no puede ver se muestran solo con su número. */
export default function RelatedTicketsPanel({ ticket }: { ticket: TicketOut }) {
  const parentId = ticket.parent_ticket_id;
  const childIds = ticket.child_ticket_ids ?? [];
  const idsKey = [parentId ?? '', ...childIds].join(',');
  const [loaded, setLoaded] = useState<Map<number, TicketOut | null>>(new Map());

  useEffect(() => {
    const ids = [parentId, ...childIds].filter((v): v is number => typeof v === 'number').slice(0, MAX_LOADED);
    if (ids.length === 0) return;
    const controller = new AbortController();
    void Promise.all(
      ids.map((id) =>
        apiFetch<TicketOut>(`/tickets/${id}`, { signal: controller.signal })
          .then((t) => [id, t] as const)
          .catch(() => [id, null] as const),
      ),
    ).then((entries) => {
      if (!controller.signal.aborted) setLoaded(new Map(entries));
    });
    return () => controller.abort();
    // idsKey resume parentId y childIds.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  if (!parentId && childIds.length === 0) return null;

  const parent: Related | null = parentId ? { id: parentId, ticket: loaded.get(parentId) ?? null } : null;
  const children: Related[] = childIds.map((id) => ({ id, ticket: loaded.get(id) ?? null }));

  return (
    <section className="panel" aria-labelledby="related-tickets-title">
      <div className="panel-header">
        <h2 className="panel-title" id="related-tickets-title">
          Tickets relacionados
        </h2>
      </div>
      <div className="stack">
        {parent && (
          <div className="stack-sm">
            <span className={styles.relatedLabel}>Ticket de origen</span>
            <ul className={styles.relatedList}>
              <RelatedItem item={parent} />
            </ul>
          </div>
        )}
        {children.length > 0 && (
          <div className="stack-sm">
            <span className={styles.relatedLabel}>Órdenes de trabajo ({children.length})</span>
            <ul className={styles.relatedList}>
              {children.map((child) => (
                <RelatedItem key={child.id} item={child} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function RelatedItem({ item }: { item: Related }) {
  const t = item.ticket;
  return (
    <li className={styles.relatedItem}>
      <div className={styles.relatedHead}>
        <Link href={`/tickets/${item.id}`} className="mono text-sm">
          {t?.key ?? `#${item.id}`}
        </Link>
        {t && <StatusBadge status={t.status} />}
      </div>
      {t && <span className={styles.relatedTitle}>{t.title}</span>}
    </li>
  );
}
