'use client';

import { useCallback, useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import styles from '@/components/reports/attachments.module.css';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { apiFetch, apiUrl, errorMessage } from '@/lib/api';
import { getAuthHeaders, useAuth } from '@/lib/auth';
import { formatDateTime, formatRelative } from '@/lib/format';
import { useTicketEvents } from '@/lib/realtime';
import { downloadAuthenticated } from '@/lib/reports';
import type { Ticket, UserRef } from '@/lib/types';

/** GET /tickets/{id}/attachments */
export interface Attachment {
  id: number;
  ticket_id: number;
  filename: string;
  size: number;
  mime: string;
  checksum: string;
  uploaded_by: UserRef;
  created_at: string;
  can_delete: boolean;
}

/** GET /attachments/settings */
interface AttachmentSettings {
  max_mb: number;
  max_bytes: number;
  max_per_ticket: number;
  allowed_extensions: string[];
}

interface UploadItem {
  id: number;
  name: string;
  size: number;
  progress: number;
  status: 'queued' | 'uploading' | 'error' | 'done';
  error?: string;
  xhr?: XMLHttpRequest;
}

const FALLBACK_SETTINGS: AttachmentSettings = {
  max_mb: 20,
  max_bytes: 20 * 1024 * 1024,
  max_per_ticket: 50,
  allowed_extensions: [],
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString('es-GT', { maximumFractionDigits: value < 10 ? 1 : 0 })} ${units[unit]}`;
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf('.');
  return index > 0 ? name.slice(index + 1).toLowerCase() : '';
}

/** Mensaje legible de una respuesta de error de la API (JSON `{detail}`). */
function xhrErrorMessage(xhr: XMLHttpRequest): string {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail) return body.detail;
  } catch {
    /* respuesta no JSON */
  }
  switch (xhr.status) {
    case 0:
      return 'No se pudo conectar con la API.';
    case 401:
      return 'Su sesión expiró. Inicie sesión nuevamente.';
    case 403:
      return 'No tiene permisos para adjuntar archivos a este ticket.';
    case 413:
      return 'El archivo excede el tamaño permitido.';
    case 415:
      return 'Tipo de archivo no permitido.';
    default:
      return `Error al subir el archivo (${xhr.status}).`;
  }
}

/** Sube un archivo con XMLHttpRequest para poder reportar el progreso. */
async function uploadWithProgress(
  ticketId: number,
  file: File,
  onProgress: (fraction: number) => void,
  register: (xhr: XMLHttpRequest) => void,
): Promise<Attachment> {
  const headers = await getAuthHeaders();
  return new Promise<Attachment>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    register(xhr);
    xhr.open('POST', apiUrl(`/tickets/${ticketId}/attachments`));
    xhr.setRequestHeader('Accept', 'application/json');
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Attachment);
        } catch {
          reject(new Error('Respuesta inválida del servidor.'));
        }
      } else {
        reject(new Error(xhrErrorMessage(xhr)));
      }
    };
    xhr.onerror = () => reject(new Error(xhrErrorMessage(xhr)));
    xhr.onabort = () => reject(new Error('Subida cancelada.'));
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}

/**
 * Panel de adjuntos del detalle del ticket: subida con arrastrar/soltar y progreso,
 * listado, descarga autenticada (fetch + blob) y eliminación.
 */
export default function AttachmentsPanel({ ticket }: { ticket: Ticket }) {
  const { user, isAgent } = useAuth();
  const [items, setItems] = useState<Attachment[]>([]);
  const [settings, setSettings] = useState<AttachmentSettings>(FALLBACK_SETTINGS);
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
    apiFetch<AttachmentSettings>('/attachments/settings')
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .catch(() => {
        /* se usan los valores por defecto; el backend valida igualmente */
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

  const precheck = (file: File): string | null => {
    const ext = extensionOf(file.name);
    if (settings.allowed_extensions.length > 0 && !settings.allowed_extensions.includes(ext)) {
      return `Tipo no permitido (.${ext || 'sin extensión'}).`;
    }
    if (file.size === 0) return 'El archivo está vacío.';
    if (file.size > settings.max_bytes) return `Supera el máximo de ${settings.max_mb} MB.`;
    return null;
  };

  const startUploads = async (files: File[]) => {
    if (!canUpload || files.length === 0) return;
    setActionError(null);
    const queued: { item: UploadItem; file: File }[] = files.map((file) => {
      const error = precheck(file);
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
        const created = await uploadWithProgress(
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

  const accept = settings.allowed_extensions.map((ext) => `.${ext}`).join(',');
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
              {settings.allowed_extensions.length > 0
                ? `Permitidos: ${settings.allowed_extensions.join(', ')}`
                : 'Documentos, imágenes, capturas de red y archivos comprimidos'}
            </div>
            <input
              ref={inputRef}
              type="file"
              multiple
              hidden
              accept={accept || undefined}
              onChange={(e) => onFilesSelected(e.target.files)}
            />
          </div>
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
