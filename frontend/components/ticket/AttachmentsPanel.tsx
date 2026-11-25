'use client';

import { useCallback, useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import styles from '@/components/reports/attachments.module.css';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import {
  FALLBACK_ATTACHMENT_SETTINGS,
  acceptAttribute,
  extensionOf,
  fetchAttachmentSettings,
  formatBytes,
  precheckAttachment,
  uploadAttachment,
  type Attachment,
  type AttachmentSettings,
} from '@/lib/attachments';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatRelative } from '@/lib/format';
import { useTicketEvents } from '@/lib/realtime';
import { downloadAuthenticated } from '@/lib/reports';
import type { Ticket } from '@/lib/types';

interface UploadItem {
  id: number;
  name: string;
  size: number;
  progress: number;
  status: 'queued' | 'uploading' | 'error' | 'done';
  error?: string;
  xhr?: XMLHttpRequest;
}

/**
 * Panel de adjuntos del detalle del ticket: subida con arrastrar/soltar y progreso,
 * listado, descarga autenticada (fetch + blob) y eliminación.
 */
export default function AttachmentsPanel({ ticket }: { ticket: Ticket }) {
  const { user, isAgent } = useAuth();
  const [items, setItems] = useState<Attachment[]>([]);
  const [settings, setSettings] = useState<AttachmentSettings>(FALLBACK_ATTACHMENT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadSeq = useRef(0);
  const requestSeq = useRef(0);
  const mounted = useRef(true);
  const activeXhrs = useRef(new Set<XMLHttpRequest>());

  const ticketId = ticket.id;
  const closed = Boolean(ticket.closed_at);
  const canUpload =
    !closed &&
    (isAgent || user?.id === ticket.requester.id || (ticket.assignee !== null && user?.id === ticket.assignee.id));

  const load = useCallback(
    async (silent = false) => {
      const seq = ++requestSeq.current;
      if (!silent) setLoading(true);
      try {
        const list = await apiFetch<Attachment[]>(`/tickets/${ticketId}/attachments`);
        if (seq !== requestSeq.current || !mounted.current) return;
        setItems(list);
        setLoadError(null);
      } catch (e) {
        if (seq !== requestSeq.current || !mounted.current) return;
        setLoadError(`No se pudieron cargar los adjuntos: ${errorMessage(e)}`);
      } finally {
        if (seq === requestSeq.current && mounted.current) setLoading(false);
      }
    },
    [ticketId],
  );

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void fetchAttachmentSettings().then((s) => {
      if (!cancelled) setSettings(s);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Cancela las subidas en curso al salir del ticket.
  useEffect(() => {
    const active = activeXhrs.current;
    return () => {
      active.forEach((xhr) => xhr.abort());
      active.clear();
    };
  }, [ticketId]);

  useTicketEvents(
    (event) => {
      if (event.type === 'attachment.added' || event.type === 'attachment.deleted') void load(true);
    },
    { ticketId, types: ['attachment.added', 'attachment.deleted'] },
  );

  const patchUpload = (id: number, patch: Partial<UploadItem>) =>
    setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...patch } : u)));

  const startUploads = async (files: File[]) => {
    if (!canUpload || files.length === 0) return;
    setActionError(null);
    const queued: { item: UploadItem; file: File }[] = files.map((file) => {
      const error = precheckAttachment(file, settings);
      return {
        file,
        item: {
          id: ++uploadSeq.current,
          name: file.name,
          size: file.size,
          progress: 0,
          status: error ? 'error' : 'queued',
          error: error ?? undefined,
        },
      };
    });
    setUploads((prev) => [...prev.filter((u) => u.status !== 'done'), ...queued.map((q) => q.item)]);

    let added = false;
    for (const { item, file } of queued) {
      if (item.status === 'error') continue;
      if (!mounted.current) return;
      patchUpload(item.id, { status: 'uploading' });
      try {
        const created = await uploadAttachment(
          ticketId,
          file,
          (fraction) => patchUpload(item.id, { progress: fraction }),
          (xhr) => {
            activeXhrs.current.add(xhr);
            patchUpload(item.id, { xhr });
          },
        );
        if (!mounted.current) return;
        added = true;
        setItems((prev) => (prev.some((a) => a.id === created.id) ? prev : [...prev, created]));
        patchUpload(item.id, { status: 'done', progress: 1, xhr: undefined });
      } catch (e) {
        if (!mounted.current) return;
        patchUpload(item.id, { status: 'error', error: errorMessage(e), xhr: undefined });
      } finally {
        activeXhrs.current.forEach((xhr) => {
          if (xhr.readyState === XMLHttpRequest.DONE) activeXhrs.current.delete(xhr);
        });
      }
    }
    if (added) {
      void load(true);
      // Las subidas exitosas desaparecen tras un momento; los errores quedan visibles.
      window.setTimeout(() => {
        if (mounted.current) setUploads((prev) => prev.filter((u) => u.status !== 'done'));
      }, 2500);
    }
  };

  const onFilesSelected = (list: FileList | null) => {
    if (!list) return;
    void startUploads(Array.from(list));
    if (inputRef.current) inputRef.current.value = '';
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!canUpload) return;
    onFilesSelected(event.dataTransfer.files);
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (canUpload) {
      event.dataTransfer.dropEffect = 'copy';
      setDragging(true);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      inputRef.current?.click();
    }
  };

  const download = async (attachment: Attachment) => {
    setBusyId(attachment.id);
    setActionError(null);
    try {
      await downloadAuthenticated(`/attachments/${attachment.id}/download`, attachment.filename);
    } catch (e) {
      setActionError(`No se pudo descargar «${attachment.filename}»: ${errorMessage(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (attachment: Attachment) => {
    if (!window.confirm(`¿Eliminar el adjunto «${attachment.filename}»?`)) return;
    setBusyId(attachment.id);
    setActionError(null);
    try {
      await apiFetch(`/attachments/${attachment.id}`, { method: 'DELETE' });
      setItems((prev) => prev.filter((a) => a.id !== attachment.id));
    } catch (e) {
      setActionError(`No se pudo eliminar «${attachment.filename}»: ${errorMessage(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const accept = acceptAttribute(settings);
  const remaining = Math.max(0, settings.max_per_ticket - items.length);

  return (
    <section className="panel" aria-labelledby={`attachments-title-${ticketId}`}>
      <div className="panel-header">
        <div>
          <h2 className="panel-title" id={`attachments-title-${ticketId}`}>
            Adjuntos {items.length > 0 && <span className="badge badge-neutral">{items.length}</span>}
          </h2>
          <p className="panel-subtitle">Máximo {settings.max_mb} MB por archivo</p>
        </div>
      </div>

      <div className="stack">
        {canUpload && remaining > 0 && (
          <div
            role="button"
            tabIndex={0}
            className={`${styles.dropzone} ${dragging ? styles.dropzoneActive : ''}`}
            onClick={() => inputRef.current?.click()}
            onKeyDown={onKeyDown}
            onDragOver={onDragOver}
            onDragEnter={onDragOver}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            aria-describedby={`attachments-hint-${ticketId}`}
          >
            <div className="strong">{dragging ? 'Suelte los archivos aquí' : 'Arrastre archivos o haga clic para seleccionar'}</div>
            <div className="text-xs" id={`attachments-hint-${ticketId}`}>
              Documentos, hojas de cálculo, imágenes, capturas de red y comprimidos · máx. {settings.max_mb} MB
            </div>
            <input
              ref={inputRef}
              type="file"
              multiple
              hidden
              accept={accept}
              onChange={(e) => onFilesSelected(e.target.files)}
            />
          </div>
        )}
        {canUpload && remaining > 0 && settings.allowed_extensions.length > 0 && (
          <details className={styles.allowed}>
            <summary>Ver tipos permitidos</summary>
            <p className="text-xs muted">
              {[...settings.allowed_extensions]
                .sort()
                .map((ext) => `.${ext}`)
                .join(' · ')}
            </p>
          </details>
        )}
        {closed && <p className="text-sm muted">El ticket está cerrado: no se pueden agregar adjuntos.</p>}
        {canUpload && remaining === 0 && (
          <p className="text-sm muted">Se alcanzó el máximo de {settings.max_per_ticket} adjuntos para este ticket.</p>
        )}

        {uploads.length > 0 && (
          <ul className={styles.fileList} aria-label="Subidas" aria-live="polite">
            {uploads.map((u) => (
              <li key={u.id} className={styles.fileItem}>
                <span className={styles.fileIcon} aria-hidden="true">
                  {extensionOf(u.name).slice(0, 4) || '—'}
                </span>
                <div className={styles.fileMain}>
                  <div className={styles.fileName} title={u.name}>
                    {u.name}
                  </div>
                  {u.status === 'error' ? (
                    <div className="field-error">{u.error}</div>
                  ) : (
                    <>
                      <div
                        className={styles.progress}
                        role="progressbar"
                        aria-label={`Progreso de ${u.name}`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(u.progress * 100)}
                      >
                        <div className={styles.progressFill} style={{ width: `${Math.round(u.progress * 100)}%` }} />
                      </div>
                      <div className={styles.fileMeta}>
                        {u.status === 'done'
                          ? 'Subido'
                          : u.status === 'queued'
                            ? 'En cola'
                            : `${Math.round(u.progress * 100)} % de ${formatBytes(u.size)}`}
                      </div>
                    </>
                  )}
                </div>
                <div className={styles.fileActions}>
                  {u.status === 'uploading' && u.xhr && (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => u.xhr?.abort()}>
                      Cancelar
                    </button>
                  )}
                  {u.status === 'error' && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setUploads((prev) => prev.filter((x) => x.id !== u.id))}
                    >
                      Descartar
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        <ErrorAlert error={actionError} />
        <ErrorAlert error={loadError} onRetry={() => void load()} />

        {loading && items.length === 0 ? (
          <Loading label="Cargando adjuntos…" />
        ) : items.length === 0 && !loadError ? (
          <EmptyState title="Sin adjuntos">Este ticket aún no tiene archivos adjuntos.</EmptyState>
        ) : (
          <ul className={styles.fileList} aria-label="Archivos adjuntos">
            {items.map((a) => (
              <li key={a.id} className={styles.fileItem}>
                <span className={styles.fileIcon} aria-hidden="true">
                  {extensionOf(a.filename).slice(0, 4) || 'file'}
                </span>
                <div className={styles.fileMain}>
                  <div className={styles.fileName} title={a.filename}>
                    {a.filename}
                  </div>
                  <div className={styles.fileMeta}>
                    {formatBytes(a.size)} · {a.uploaded_by.display_name} ·{' '}
                    <time dateTime={a.created_at} title={formatDateTime(a.created_at)}>
                      {formatRelative(a.created_at)}
                    </time>
                  </div>
                </div>
                <div className={styles.fileActions}>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busyId === a.id}
                    onClick={() => void download(a)}
                    aria-label={`Descargar ${a.filename}`}
                  >
                    {busyId === a.id ? 'Procesando…' : 'Descargar'}
                  </button>
                  {a.can_delete && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={busyId === a.id}
                      onClick={() => void remove(a)}
                      aria-label={`Eliminar ${a.filename}`}
                    >
                      Eliminar
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
