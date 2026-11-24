'use client';

import { useMemo, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import type { Catalog, TicketCreate, TicketOut } from '@/lib/types';

export default function CreateTicketForm({
  catalog,
  onCreated,
  onCancel,
}: {
  catalog: Catalog;
  onCreated: (ticket: TicketOut) => void;
  onCancel: () => void;
}) {
  const { user, isAgent } = useAuth();
  const defaultDepartment = useMemo(() => {
    const own = user?.department_ids.find((id) => catalog.departments.some((d) => d.id === id));
    const general = catalog.departments.find((d) => d.name.toLowerCase() === 'general');
    return own ?? general?.id ?? catalog.departments[0]?.id ?? 0;
  }, [catalog.departments, user]);

  const defaultType = catalog.ticket_types.find((t) => t.key === 'incident')?.key ?? catalog.ticket_types[0]?.key ?? '';
  const defaultPriority = catalog.priorities.find((p) => p.key === 'medium')?.key ?? catalog.priorities[0]?.key ?? '';

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [typeKey, setTypeKey] = useState(defaultType);
  const [priorityKey, setPriorityKey] = useState(defaultPriority);
  const [departmentId, setDepartmentId] = useState<number>(defaultDepartment);
  const [projectId, setProjectId] = useState<number | ''>('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const departmentTeamIds = useMemo(
    () => new Set(catalog.teams.filter((t) => t.department_id === departmentId).map((t) => t.id)),
    [catalog.teams, departmentId],
  );
  const projects = useMemo(() => {
    const related = catalog.projects.filter((p) => p.team_id !== null && departmentTeamIds.has(p.team_id));
    const unrelated = catalog.projects.filter((p) => !(p.team_id !== null && departmentTeamIds.has(p.team_id)));
    return { related, unrelated };
  }, [catalog.projects, departmentTeamIds]);

  // Quien no es agente elige hasta «Alta» (la API rechaza «Crítica»: REQUESTER_MAX_PRIORITY);
  // un agente escala la prioridad si corresponde.
  const sortedPriorities = useMemo(() => {
    const sorted = [...catalog.priorities].sort((a, b) => a.rank - b.rank);
    if (isAgent) return sorted;
    const cap = catalog.priorities.find((p) => p.key === 'high')?.rank;
    return cap === undefined ? sorted : sorted.filter((p) => p.rank <= cap);
  }, [catalog.priorities, isAgent]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (title.trim().length < 3) {
      setError('El título debe tener al menos 3 caracteres.');
      return;
    }
    if (!departmentId) {
      setError('Seleccione el departamento responsable.');
      return;
    }
    setSubmitting(true);
    setError(null);
    const payload: TicketCreate = {
      title: title.trim(),
      description: description.trim(),
      type_key: typeKey || undefined,
      priority_key: priorityKey || undefined,
      department_id: departmentId,
      project_id: projectId === '' ? null : projectId,
    };
    try {
      const ticket = await apiFetch<TicketOut>('/tickets', { method: 'POST', body: payload });
      onCreated(ticket);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit} aria-labelledby="create-ticket-title">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h2 className="panel-title" id="create-ticket-title">
          Nuevo ticket
        </h2>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
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
            Departamento<span className="required">*</span>
          </span>
          <select
            className="select"
            value={departmentId || ''}
            onChange={(e) => {
              setDepartmentId(Number(e.target.value));
              setProjectId('');
            }}
            required
          >
            {catalog.departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Tipo</span>
          <select className="select" value={typeKey} onChange={(e) => setTypeKey(e.target.value)}>
            {catalog.ticket_types.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
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
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={submitting}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? 'Creando…' : 'Crear ticket'}
        </button>
      </div>
    </form>
  );
}
