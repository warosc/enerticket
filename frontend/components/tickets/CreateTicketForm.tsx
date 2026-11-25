'use client';

import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from 'react';
import styles from '@/components/reports/attachments.module.css';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import {
  FALLBACK_ATTACHMENT_SETTINGS,
  acceptAttribute,
  extensionOf,
  fetchAttachmentSettings,
  formatBytes,
  precheckAttachment,
  uploadAttachment,
  type AttachmentSettings,
} from '@/lib/attachments';
import { useAuth } from '@/lib/auth';
import { categoriesFor, categoryAllowsType, providersFor, ticketDepartments } from '@/lib/catalog';
import type { Catalog, TicketChannel, TicketCreate, TicketOut } from '@/lib/types';
import CategoryFields, { type CategoryValue } from './CategoryFields';

/** Archivo elegido en el alta, pendiente de subir cuando exista el ticket. */
interface PendingFile {
  id: number;
  file: File;
  error: string | null;
}

/** Adjunto que no se pudo subir tras crear el ticket. */
export interface FailedUpload {
  name: string;
  error: string;
}

/**
 * Alta de ticket: tipo → departamento de atención (solo los que atienden tickets) → categoría
 * (filtrada por departamento y tipo) → subcategoría. Los agentes indican además canal y proveedor.
 * Los adjuntos se suben uno a uno justo después de crear el ticket (la API los asocia a su id).
 */
export default function CreateTicketForm({
  catalog,
  onCreated,
  onCancel,
}: {
  catalog: Catalog;
  onCreated: (ticket: TicketOut, failedUploads: FailedUpload[]) => void;
  onCancel: () => void;
}) {
  const { user, isAgent } = useAuth();
  const departments = useMemo(() => ticketDepartments(catalog), [catalog]);
  const defaultDepartment = useMemo<number | ''>(() => {
    // Agentes: su propio departamento si atiende tickets. Si solo hay uno, se preselecciona.
    const own = user?.department_ids.find((id) => departments.some((d) => d.id === id));
    if (isAgent && own) return own;
    return departments.length === 1 ? departments[0].id : '';
  }, [departments, isAgent, user]);

  const defaultType = catalog.ticket_types.find((t) => t.key === 'incident')?.key ?? catalog.ticket_types[0]?.key ?? '';
  const defaultPriority = catalog.priorities.find((p) => p.key === 'medium')?.key ?? catalog.priorities[0]?.key ?? '';
  const defaultChannel: TicketChannel = 'portal';

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [typeKey, setTypeKey] = useState(defaultType);
  const [priorityKey, setPriorityKey] = useState(defaultPriority);
  const [departmentId, setDepartmentId] = useState<number | ''>(defaultDepartment);
  const [classification, setClassification] = useState<CategoryValue>({ categoryId: null, subcategoryId: null });
  const [projectId, setProjectId] = useState<number | ''>('');
  const [channel, setChannel] = useState<TicketChannel>(defaultChannel);
  const [providerId, setProviderId] = useState<number | ''>('');
  const [submitting, setSubmitting] = useState(false);
  const [progressLabel, setProgressLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [attachmentSettings, setAttachmentSettings] = useState<AttachmentSettings>(FALLBACK_ATTACHMENT_SETTINGS);
  const fileInput = useRef<HTMLInputElement>(null);
  const fileSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void fetchAttachmentSettings().then((s) => {
      if (!cancelled) setAttachmentSettings(s);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const incoming = Array.from(list);
    const room = Math.max(0, attachmentSettings.max_per_ticket - files.length);
    const accepted = incoming.slice(0, room).map((file) => ({
      id: ++fileSeq.current,
      file,
      error: precheckAttachment(file, attachmentSettings),
    }));
    setFiles((prev) => [...prev, ...accepted]);
    if (incoming.length > room) {
      setError(`Se permiten como máximo ${attachmentSettings.max_per_ticket} adjuntos por ticket.`);
    }
    if (fileInput.current) fileInput.current.value = '';
  }

  function removeFile(id: number) {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!submitting) addFiles(event.dataTransfer.files);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (submitting) return;
    event.dataTransfer.dropEffect = 'copy';
    setDragging(true);
  }

  function onDropzoneKey(event: KeyboardEvent<HTMLDivElement>) {
    if (submitting) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.current?.click();
    }
  }

  const departmentTeamIds = useMemo(
    () => new Set(catalog.teams.filter((t) => t.department_id === departmentId).map((t) => t.id)),
    [catalog.teams, departmentId],
  );
  const projects = useMemo(() => {
    const related = catalog.projects.filter((p) => p.team_id !== null && departmentTeamIds.has(p.team_id));
    const unrelated = catalog.projects.filter((p) => !(p.team_id !== null && departmentTeamIds.has(p.team_id)));
    return { related, unrelated };
  }, [catalog.projects, departmentTeamIds]);
  const providers = useMemo(
    () => (departmentId === '' ? [] : providersFor(catalog, departmentId)),
    [catalog, departmentId],
  );
  const hasCategories = departmentId !== '' && categoriesFor(catalog, departmentId, typeKey).length > 0;

  // Quien no es agente elige hasta «Alta» (la API rechaza «Crítica»: REQUESTER_MAX_PRIORITY);
  // un agente escala la prioridad si corresponde.
  const sortedPriorities = useMemo(() => {
    const sorted = [...catalog.priorities].sort((a, b) => a.rank - b.rank);
    if (isAgent) return sorted;
    const cap = catalog.priorities.find((p) => p.key === 'high')?.rank;
    return cap === undefined ? sorted : sorted.filter((p) => p.rank <= cap);
  }, [catalog.priorities, isAgent]);

  function changeType(next: string) {
    setTypeKey(next);
    const category = catalog.categories.find((c) => c.id === classification.categoryId);
    if (category && !categoryAllowsType(category, next)) setClassification({ categoryId: null, subcategoryId: null });
  }

  function changeDepartment(next: number | '') {
    setDepartmentId(next);
    setClassification({ categoryId: null, subcategoryId: null });
    setProjectId('');
    if (providerId !== '' && next !== '' && !providersFor(catalog, next).some((p) => p.id === providerId)) {
      setProviderId('');
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (title.trim().length < 3) {
      setError('El título debe tener al menos 3 caracteres.');
      return;
    }
    if (departmentId === '') {
      setError('Seleccione el departamento que atenderá el ticket.');
      return;
    }
    if (files.some((f) => f.error)) {
      setError('Quite los adjuntos marcados con error antes de crear el ticket.');
      return;
    }
    setSubmitting(true);
    setProgressLabel('Creando…');
    setError(null);
    const payload: TicketCreate = {
      title: title.trim(),
      description: description.trim(),
      type_key: typeKey || undefined,
      priority_key: priorityKey || undefined,
      department_id: departmentId,
      project_id: projectId === '' ? null : projectId,
      category_id: classification.categoryId,
      subcategory_id: classification.subcategoryId,
    };
    if (isAgent) {
      payload.channel = channel;
      payload.provider_id = providerId === '' ? null : providerId;
    }
    let ticket: TicketOut;
    try {
      ticket = await apiFetch<TicketOut>('/tickets', { method: 'POST', body: payload });
    } catch (e) {
      setError(errorMessage(e));
      setSubmitting(false);
      setProgressLabel(null);
      return;
    }
    // El ticket ya existe: un adjunto fallido no lo deshace; se informa y puede reintentarse
    // desde el panel de adjuntos del detalle.
    const failed: FailedUpload[] = [];
    for (const [index, pending] of files.entries()) {
      setProgressLabel(`Subiendo adjuntos (${index + 1} de ${files.length})…`);
      try {
        await uploadAttachment(ticket.id, pending.file);
      } catch (e) {
        failed.push({ name: pending.file.name, error: errorMessage(e) });
      }
    }
    setSubmitting(false);
    setProgressLabel(null);
    onCreated(ticket, failed);
  }

  return (
    <form className="panel form" onSubmit={submit} aria-labelledby="create-ticket-title">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h2 className="panel-title" id="create-ticket-title">
          Nuevo ticket
        </h2>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {departments.length === 0 && (
        <Alert tone="warning">
          No hay departamentos de atención configurados. Un administrador debe marcar al menos un departamento como
          «Atiende tickets».
        </Alert>
      )}
      <div className="form-grid">
        <label className="field field-full">
          <span className="field-label">
            Título<span className="required">*</span>
          </span>
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            minLength={3}
            maxLength={200}
            placeholder="Resumen breve del problema o solicitud"
            autoFocus
          />
        </label>
        <label className="field field-full">
          <span className="field-label">Descripción</span>
          <textarea
            className="textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={20000}
            placeholder="Detalle, impacto, ubicación y pasos para reproducir"
          />
        </label>
        <label className="field">
          <span className="field-label">
            Tipo<span className="required">*</span>
          </span>
          <select className="select" value={typeKey} onChange={(e) => changeType(e.target.value)} required>
            {catalog.ticket_types.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">
            Departamento que atiende<span className="required">*</span>
          </span>
          <select
            className="select"
            value={departmentId}
            onChange={(e) => changeDepartment(e.target.value === '' ? '' : Number(e.target.value))}
            required
          >
            <option value="">Seleccione…</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <CategoryFields
          catalog={catalog}
          departmentId={departmentId === '' ? null : departmentId}
          typeKey={typeKey}
          value={classification}
          onChange={setClassification}
          categoryHint={
            departmentId === ''
              ? 'Elija primero el departamento.'
              : hasCategories
                ? 'Opcional: ayuda a dirigir el ticket al equipo correcto.'
                : undefined
          }
        />
        <label className="field">
          <span className="field-label">Prioridad</span>
          <select className="select" value={priorityKey} onChange={(e) => setPriorityKey(e.target.value)}>
            {sortedPriorities.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
          {!isAgent && (
            <span className="field-hint">Si es una urgencia crítica, indíquelo en la descripción: el equipo escalará la prioridad.</span>
          )}
        </label>
        <label className="field">
          <span className="field-label">Proyecto</span>
          <select
            className="select"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value === '' ? '' : Number(e.target.value))}
          >
            <option value="">Sin proyecto</option>
            {projects.related.length > 0 && (
              <optgroup label="Del departamento">
                {projects.related.map((p) => (
                  <option key={p.id} value={p.id}>
                    {`${p.code} — ${p.name}`}
                  </option>
                ))}
              </optgroup>
            )}
            {projects.unrelated.length > 0 && (
              <optgroup label="Otros proyectos">
                {projects.unrelated.map((p) => (
                  <option key={p.id} value={p.id}>
                    {`${p.code} — ${p.name}`}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {isAgent && (
          <>
            <label className="field">
              <span className="field-label">Canal</span>
              <select className="select" value={channel} onChange={(e) => setChannel(e.target.value as TicketChannel)}>
                {catalog.channels.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.label}
                  </option>
                ))}
              </select>
              <span className="field-hint">Por dónde llegó la solicitud.</span>
            </label>
            <label className="field">
              <span className="field-label">Proveedor</span>
              <select
                className="select"
                value={providerId}
                disabled={departmentId === '' || providers.length === 0}
                onChange={(e) => setProviderId(e.target.value === '' ? '' : Number(e.target.value))}
              >
                <option value="">{providers.length === 0 && departmentId !== '' ? 'Sin proveedores' : 'Sin proveedor'}</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <div className="field field-full">
          <span className="field-label" id="create-ticket-attachments">
            Adjuntos
          </span>
          <div
            role="button"
            tabIndex={submitting ? -1 : 0}
            aria-labelledby="create-ticket-attachments"
            aria-describedby="create-ticket-attachments-hint"
            aria-disabled={submitting}
            className={`${styles.dropzone} ${dragging ? styles.dropzoneActive : ''} ${submitting ? styles.dropzoneDisabled : ''}`}
            onClick={() => {
              if (!submitting) fileInput.current?.click();
            }}
            onKeyDown={onDropzoneKey}
            onDragOver={onDragOver}
            onDragEnter={onDragOver}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <div className="strong">{dragging ? 'Suelte los archivos aquí' : 'Arrastre archivos o haga clic para seleccionar'}</div>
            <div className="text-xs" id="create-ticket-attachments-hint">
              Capturas, documentos, hojas de cálculo o comprimidos · máx. {attachmentSettings.max_mb} MB por archivo
            </div>
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              accept={acceptAttribute(attachmentSettings)}
              onChange={(e) => addFiles(e.target.files)}
            />
          </div>
          {files.length > 0 && (
            <ul className={styles.fileList} aria-label="Adjuntos seleccionados">
              {files.map((f) => (
                <li key={f.id} className={styles.fileItem}>
                  <span className={styles.fileIcon} aria-hidden="true">
                    {extensionOf(f.file.name).slice(0, 4) || 'file'}
                  </span>
                  <div className={styles.fileMain}>
                    <div className={styles.fileName} title={f.file.name}>
                      {f.file.name}
                    </div>
                    {f.error ? (
                      <div className="field-error">{f.error}</div>
                    ) : (
                      <div className={styles.fileMeta}>{formatBytes(f.file.size)}</div>
                    )}
                  </div>
                  <div className={styles.fileActions}>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={submitting}
                      onClick={() => removeFile(f.id)}
                      aria-label={`Quitar ${f.file.name}`}
                    >
                      Quitar
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={submitting}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={submitting || departments.length === 0}>
          {submitting ? (progressLabel ?? 'Creando…') : 'Crear ticket'}
        </button>
      </div>
    </form>
  );
}
