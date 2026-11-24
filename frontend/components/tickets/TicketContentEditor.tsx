'use client';

import { useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import type { TicketOut, TicketUpdate } from '@/lib/types';

/** Edición del título y la descripción del ticket (agentes del área y administradores). */
export default function TicketContentEditor({
  ticket,
  onSaved,
  onCancel,
}: {
  ticket: TicketOut;
  onSaved: (ticket: TicketOut) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(ticket.title);
  const [description, setDescription] = useState(ticket.description);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changes: TicketUpdate = {};
  if (title.trim() !== ticket.title) changes.title = title.trim();
  if (description.trim() !== ticket.description.trim()) changes.description = description.trim();
  const dirty = Object.keys(changes).length > 0;
  const titleInvalid = title.trim().length < 3;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (titleInvalid) {
      setError('El título debe tener al menos 3 caracteres.');
      return;
    }
    if (!dirty) {
      onCancel();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await apiFetch<TicketOut>(`/tickets/${ticket.id}`, { method: 'PATCH', body: changes });
      onSaved(updated);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="inline-edit" onSubmit={submit} aria-label="Editar título y descripción">
      {error && <Alert tone="error">{error}</Alert>}
      <label className="field">
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
          aria-invalid={titleInvalid}
          autoFocus
        />
      </label>
      <label className="field">
        <span className="field-label">Descripción</span>
        <textarea
          className="textarea"
          style={{ minHeight: 160 }}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={20000}
        />
      </label>
      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={saving}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving || !dirty || titleInvalid}>
          {saving ? 'Guardando…' : 'Guardar'}
        </button>
      </div>
    </form>
  );
}
