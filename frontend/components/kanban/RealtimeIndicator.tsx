'use client';

import { useEffect, useState } from 'react';
import { REALTIME_STATUS_LABELS, useRealtimeStatus } from '@/lib/realtime';
import styles from './kanban.module.css';

/** Indicador compacto del estado de la conexión en tiempo real ("En vivo", "Reconectando…"). */
export default function RealtimeIndicator({ className }: { className?: string }) {
  const state = useRealtimeStatus();
  const [now, setNow] = useState(() => Date.now());

  // Cuenta regresiva del próximo reintento.
  useEffect(() => {
    if (!state.retryAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [state.retryAt]);

  const seconds = state.retryAt ? Math.max(0, Math.ceil((state.retryAt - now) / 1000)) : null;
  const label = REALTIME_STATUS_LABELS[state.status];
  const detail =
    state.status === 'open'
      ? 'Los cambios de otros usuarios aparecen automáticamente.'
      : [state.lastError, seconds !== null ? `Próximo intento en ${seconds} s.` : null].filter(Boolean).join(' ');

  return (
    <span
      className={`${styles.live} ${styles[`live_${state.status}`] ?? ''} ${className ?? ''}`}
      role="status"
      aria-live="polite"
      title={detail || label}
    >
      <span className={styles.liveDot} aria-hidden="true" />
      {label}
    </span>
  );
}
