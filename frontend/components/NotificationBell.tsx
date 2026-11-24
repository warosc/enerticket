'use client';

/**
 * Campana de notificaciones de la barra superior (módulo flows).
 *
 * - Contador de no leídas (GET /notifications/unread-count), actualizado en tiempo real con
 *   `useTicketEvents` (evento `notification.created` dirigido al usuario) y, como respaldo,
 *   cada 60 s y al volver a la pestaña.
 * - Desplegable con las últimas notificaciones, filtro "No leídas", marcar como leída al
 *   abrir una notificación y "Marcar todas como leídas".
 */
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { FlowIcon, type FlowIconName } from '@/components/notifications/FlowIcon';
import styles from '@/components/notifications/notifications.module.css';
import { Spinner } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  NOTIFICATION_KIND_LABELS,
  type AppNotification,
} from '@/lib/flows';
import { formatDateTime, formatRelative } from '@/lib/format';
import { RESYNC_EVENT, useTicketEvents, type TicketEvent } from '@/lib/realtime';

const PAGE_SIZE = 15;
const POLL_MS = 60_000;

const KIND_ICON: Record<string, { icon: FlowIconName; tone: string }> = {
  ticket_assigned: { icon: 'user', tone: styles.iconInfo },
  ticket_commented: { icon: 'comment', tone: styles.iconInfo },
  ticket_resolved: { icon: 'check', tone: styles.iconSuccess },
  approval_requested: { icon: 'shield', tone: styles.iconWarning },
  approval_decided: { icon: 'shield', tone: styles.iconSuccess },
  sla_warning: { icon: 'clock', tone: styles.iconWarning },
  sla_breached: { icon: 'alert', tone: styles.iconDanger },
  automation: { icon: 'bolt', tone: '' },
};

function iconFor(notification: AppNotification) {
  if (notification.kind === 'approval_decided' && notification.data?.decision === 'rejected') {
    return { icon: 'x' as FlowIconName, tone: styles.iconDanger };
  }
  return KIND_ICON[notification.kind] ?? { icon: 'bell' as FlowIconName, tone: '' };
}

export default function NotificationBell() {
  const { user, authenticated } = useAuth();
  const router = useRouter();
  const panelId = useId();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const [open, setOpen] = useState(false);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const enabled = authenticated && Boolean(user);

  const refreshCount = useCallback(async () => {
    if (!enabled) return;
    try {
      const result = await getUnreadCount();
      setCount(result.count);
    } catch {
      // Silencioso: el contador es informativo; el desplegable muestra los errores.
    }
  }, [enabled]);

  const loadList = useCallback(
    async (silent = false) => {
      if (!enabled) return;
      if (!silent) setLoading(true);
      try {
        const page = await listNotifications({ unread: unreadOnly, page: 1, page_size: PAGE_SIZE });
        setItems(page.items);
        setTotal(page.total);
        setCount(page.unread);
        setError(null);
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [enabled, unreadOnly],
  );

  // Contador inicial + sondeo de respaldo + al volver a la pestaña.
  useEffect(() => {
    if (!enabled) return;
    void refreshCount();
    const id = window.setInterval(() => void refreshCount(), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refreshCount();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [enabled, refreshCount]);

  useEffect(() => {
    if (open) void loadList();
  }, [open, loadList]);

  // Tiempo real: nuevas notificaciones del usuario.
  const onEvent = useCallback(
    (event: TicketEvent) => {
      if (event.type === RESYNC_EVENT) {
        // Reconexión: pudieron perderse eventos; se resincroniza con la API.
        if (open) void loadList(true);
        else void refreshCount();
        return;
      }
      const recipient = event.data?.user_id;
      if (user && typeof recipient === 'number' && recipient !== user.id) return;
      if (open) void loadList(true);
      else setCount((c) => c + 1);
    },
    [open, loadList, refreshCount, user],
  );
  useTicketEvents(onEvent, { types: ['notification.created', RESYNC_EVENT] });

  // Cerrar con clic fuera o Escape.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  async function openNotification(notification: AppNotification) {
    if (!notification.read) {
      setItems((prev) => prev.map((n) => (n.id === notification.id ? { ...n, read: true } : n)));
      setCount((c) => Math.max(0, c - 1));
      markNotificationRead(notification.id).catch(() => void refreshCount());
    }
    const approvalId = notification.data?.approval_request_id;
    if (notification.kind === 'approval_requested' && typeof approvalId === 'number') {
      setOpen(false);
      router.push('/approvals');
      return;
    }
    if (notification.ticket) {
      setOpen(false);
      router.push(`/tickets/${notification.ticket.id}`);
    }
  }

  async function markAll() {
    setBusy(true);
    try {
      await markAllNotificationsRead();
      setItems((prev) => prev.map((n) => ({ ...n, read: true })));
      setCount(0);
      if (unreadOnly) setItems([]);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!enabled) return null;

  const label = count > 0 ? `Notificaciones: ${count} sin leer` : 'Notificaciones';

  return (
    <div className={styles.bell} ref={containerRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`btn btn-ghost btn-icon ${styles.trigger}`}
        aria-label={label}
        title={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <FlowIcon name="bell" width={20} height={20} />
        {count > 0 && (
          <span className={styles.count} aria-hidden="true">
            {count > 99 ? '99+' : count}
          </span>
        )}
      </button>

      {open && (
        <div className={styles.dropdown} id={panelId} role="dialog" aria-label="Notificaciones">
          <div className={styles.header}>
            <span className={styles.headerTitle}>Notificaciones</span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => void markAll()}
              disabled={busy || count === 0}
              title="Marcar todas como leídas"
            >
              <FlowIcon name="checkAll" width={14} height={14} />
              Marcar todas
            </button>
          </div>
          <div className={styles.filters} role="tablist" aria-label="Filtro de notificaciones">
            <button
              type="button"
              role="tab"
              aria-selected={!unreadOnly}
              className={`${styles.filter} ${!unreadOnly ? styles.filterActive : ''}`}
              onClick={() => setUnreadOnly(false)}
            >
              Todas
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={unreadOnly}
              className={`${styles.filter} ${unreadOnly ? styles.filterActive : ''}`}
              onClick={() => setUnreadOnly(true)}
            >
              No leídas{count > 0 ? ` (${count})` : ''}
            </button>
          </div>

          {error && (
            <div className="alert alert-error" role="alert" style={{ margin: 'var(--space-3) var(--space-4) 0' }}>
              <div className="alert-body">{error}</div>
              <button type="button" className="btn btn-sm" onClick={() => void loadList()}>
                Reintentar
              </button>
            </div>
          )}

          {loading && items.length === 0 ? (
            <div className={styles.empty}>
              <Spinner label="Cargando notificaciones" />
            </div>
          ) : items.length === 0 && !error ? (
            <div className={styles.empty}>
              {unreadOnly ? 'No tiene notificaciones sin leer.' : 'Aún no tiene notificaciones.'}
            </div>
          ) : (
            <ul className={styles.list}>
              {items.map((n) => {
                const { icon, tone } = iconFor(n);
                return (
                  <li key={n.id}>
                    <button
                      type="button"
                      className={`${styles.item} ${n.read ? '' : styles.unread}`}
                      onClick={() => void openNotification(n)}
                    >
                      <span className={`${styles.icon} ${tone}`}>
                        <FlowIcon name={icon} width={16} height={16} />
                      </span>
                      <span className={styles.content}>
                        <span className={styles.title} style={{ display: 'block' }}>
                          {n.title}
                        </span>
                        {n.body && (
                          <span className={styles.body} title={n.body}>
                            {n.body}
                          </span>
                        )}
                        <span className={styles.meta}>
                          <span>{NOTIFICATION_KIND_LABELS[n.kind] ?? 'Aviso'}</span>
                          <span aria-hidden="true">·</span>
                          <time dateTime={n.created_at} title={formatDateTime(n.created_at)}>
                            {formatRelative(n.created_at)}
                          </time>
                        </span>
                      </span>
                      {!n.read && <span className={styles.dot} aria-label="Sin leer" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className={styles.footer}>
            <span className="text-xs muted">
              {total > items.length ? `Mostrando ${items.length} de ${total}` : `${total} en total`}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void loadList()} disabled={loading}>
              {loading ? 'Actualizando…' : 'Actualizar'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
