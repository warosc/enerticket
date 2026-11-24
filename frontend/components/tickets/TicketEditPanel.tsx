'use client';

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { ticketDepartments } from '@/lib/catalog';
import type { Catalog, Me, TicketOut, TicketUpdate, UserOut } from '@/lib/types';

/** Edición de prioridad, departamento, equipo y asignado (agentes y administradores). */
export default function TicketEditPanel({
  ticket,
  catalog,
  agents,
  agentsError,
  me,
  onSaved,
}: {
  ticket: TicketOut;
  catalog: Catalog;
  agents: UserOut[];
  agentsError: string | null;
  me: Me;
  onSaved: (ticket: TicketOut) => void;
}) {
  const [priorityKey, setPriorityKey] = useState(ticket.priority.key);
  const [departmentId, setDepartmentId] = useState(ticket.department.id);
  const [teamId, setTeamId] = useState<number | ''>(ticket.team?.id ?? '');
  const [assigneeId, setAssigneeId] = useState<number | ''>(ticket.assignee?.id ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Sincroniza el formulario cuando el ticket cambia desde fuera (tiempo real, transiciones).
  useEffect(() => {
    setPriorityKey(ticket.priority.key);
    setDepartmentId(ticket.department.id);
    setTeamId(ticket.team?.id ?? '');
    setAssigneeId(ticket.assignee?.id ?? '');
  }, [ticket.priority.key, ticket.department.id, ticket.team?.id, ticket.assignee?.id]);

  const teams = useMemo(() => catalog.teams.filter((t) => t.department_id === departmentId), [catalog.teams, departmentId]);
  // Solo departamentos de atención activos (la API rechaza los demás); se conserva el actual.
  const departments = useMemo(() => ticketDepartments(catalog, ticket.department.id), [catalog, ticket.department.id]);
  const priorities = useMemo(() => [...catalog.priorities].sort((a, b) => a.rank - b.rank), [catalog.priorities]);
  const assigneeOptions = useMemo(() => {
    const list = [...agents];
    if (ticket.assignee && !list.some((u) => u.id === ticket.assignee?.id)) {
      list.unshift({
        id: ticket.assignee.id,
        username: '',
        display_name: ticket.assignee.display_name,
        email: null,
        roles: [],
        active: true,
      });
    }
    return list;
  }, [agents, ticket.assignee]);

  const changes: TicketUpdate = {};
  if (priorityKey !== ticket.priority.key) changes.priority_key = priorityKey;
  if (departmentId !== ticket.department.id) changes.department_id = departmentId;
  if ((teamId === '' ? null : teamId) !== (ticket.team?.id ?? null)) changes.team_id = teamId === '' ? null : teamId;
  if ((assigneeId === '' ? null : assigneeId) !== (ticket.assignee?.id ?? null)) {
    changes.assignee_id = assigneeId === '' ? null : assigneeId;
  }
  const dirty = Object.keys(changes).length > 0;

  async function save(payload: TicketUpdate) {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await apiFetch<TicketOut>(`/tickets/${ticket.id}`, { method: 'PATCH', body: payload });
      onSaved(updated);
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (dirty) void save(changes);
  }

  return (
    <form className="panel form" onSubmit={submit} aria-labelledby="ticket-edit-title">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h2 className="panel-title" id="ticket-edit-title">
          Gestión
        </h2>
        {ticket.assignee?.id !== me.id && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={saving}
            onClick={() => void save({ assignee_id: me.id })}
          >
            Asignarme
          </button>
        )}
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {saved && !dirty && <Alert tone="success">Cambios guardados.</Alert>}
      {agentsError && <Alert tone="warning">No se pudo cargar la lista de agentes: {agentsError}</Alert>}

      <label className="field">
        <span className="field-label">Prioridad</span>
        <select className="select" value={priorityKey} onChange={(e) => setPriorityKey(e.target.value)}>
          {priorities.map((p) => (
            <option key={p.key} value={p.key}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Departamento</span>
        <select
          className="select"
          value={departmentId}
          onChange={(e) => {
            const next = Number(e.target.value);
            setDepartmentId(next);
            const firstTeam = catalog.teams.find((t) => t.department_id === next);
            setTeamId(firstTeam ? firstTeam.id : '');
          }}
        >
          {departments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        {departmentId !== ticket.department.id && (ticket.category || ticket.provider) && (
          <span className="field-hint">
            Al cambiar de departamento se retiran la categoría y el proveedor que no le correspondan.
          </span>
        )}
      </label>
      <label className="field">
        <span className="field-label">Equipo</span>
        <select
          className="select"
          value={teamId}
          onChange={(e) => setTeamId(e.target.value === '' ? '' : Number(e.target.value))}
        >
          <option value="">Sin equipo</option>
          {teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Asignado a</span>
        <select
          className="select"
          value={assigneeId}
          onChange={(e) => setAssigneeId(e.target.value === '' ? '' : Number(e.target.value))}
        >
          <option value="">Sin asignar</option>
          {assigneeOptions.map((u) => (
            <option key={u.id} value={u.id}>
              {u.username ? `${u.display_name} (${u.username})` : u.display_name}
            </option>
          ))}
        </select>
      </label>
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!dirty || saving}>
          {saving ? 'Guardando…' : 'Guardar cambios'}
        </button>
      </div>
    </form>
  );
}
