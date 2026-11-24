'use client';

import { useEffect, useId, useRef, type ReactNode } from 'react';
import styles from './tickets.module.css';

/**
 * Diálogo modal accesible basado en `<dialog>` nativo: atrapa el foco, se cierra con Escape y
 * devuelve el foco al elemento que lo abrió. Se monta solo mientras está abierto.
 */
export default function Dialog({
  title,
  subtitle,
  onClose,
  children,
  busy = false,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** Mientras hay una operación en curso no se cierra con Escape. */
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const titleId = useId();
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) {
      try {
        dialog.showModal();
      } catch {
        dialog.setAttribute('open', '');
      }
    }
    const onCancel = (event: Event) => {
      event.preventDefault();
      if (!busyRef.current) onCloseRef.current();
    };
    dialog.addEventListener('cancel', onCancel);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
      if (dialog.open) dialog.close();
      opener?.focus();
    };
  }, []);

  return (
    <dialog ref={ref} className={styles.dialog} aria-labelledby={titleId} aria-modal="true">
      <div className={styles.dialogHeader}>
        <div style={{ minWidth: 0 }}>
          <h2 className={styles.dialogTitle} id={titleId}>
            {title}
          </h2>
          {subtitle && <div className={styles.dialogSubtitle}>{subtitle}</div>}
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onClose}
          disabled={busy}
          aria-label="Cerrar diálogo"
        >
          ✕
        </button>
      </div>
      <div className={styles.dialogBody}>{children}</div>
    </dialog>
  );
}
