'use client';

import { useId, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { formatBusinessHours, formatBusinessMinutes } from '@/lib/catalog';
import { formatDateTime } from '@/lib/format';
import type { TicketOut } from '@/lib/types';
import styles from './tickets.module.css';

/**
 * Solución del ticket: texto, quién lo resolvió y horas hábiles de resolución. Quien atiende el
 * ticket puede corregir la solución (en un ticket resuelto no puede quedar vacía).
 */
export default function ResolutionPanel({
  ticket,
  canEdit,
  onSaved,
}: {
  ticket: TicketOut;
  canEdit: boolean;
  onSaved: (ticket: TicketOut) => void;
}) {
  const fieldId = useId();
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resolved = ticket.status.category === 'done';
  if (!ticket.resolution_note && !resolved) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = note.trim();
    if (!value && resolved) {
      setError('La solución de un ticket resuelto no puede quedar vacía.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await apiFetch<TicketOut>(`/tickets/${ticket.id}`, {
        method: 'PATCH',
        body: { resolution_note: value || null },
      });
      onSaved(updated);
      setEditing(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="resolution-title">
      <div className="panel-header">
        <h2 className="panel-title" id="resolution-title">
          Solución
        </h2>
        {canEdit && !editing && (
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setNote(ticket.resolution_note ?? '');
              setError(null);
              setEditing(true);
            }}
          >
            {ticket.resolution_note ? 'Editar' : 'Agregar'}
          </button>
        )}
      </div>
      {editing ? (
        <form className="stack" onSubmit={submit}>
          {error && <Alert tone="error">{error}</Alert>}
          <div className="field">
            <label className="field-label" htmlFor={fieldId}>
              Solución{resolved && <span className="required">*</span>}
            </label>
            <textarea
              id={fieldId}
              className="textarea"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={20000}
              rows={4}
              autoFocus
            />
          </div>
          <div className="form-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setEditing(false)} disabled={saving}>
              Cancelar
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Guardando…' : 'Guardar solución'}
            </button>
          </div>
        </form>
      ) : ticket.resolution_note ? (
        <div className={styles.resolution}>
          <p className="pre-wrap">{ticket.resolution_note}</p>
          <div className={styles.resolutionMeta}>
            {ticket.resolved_by && <span>Resuelto por {ticket.resolved_by.display_name}</span>}
            {ticket.resolved_at && <span>{formatDateTime(ticket.resolved_at)}</span>}
            {ticket.business_resolution_minutes !== null && (
              <span title={`${formatBusinessHours(ticket.business_resolution_minutes)} horas hábiles`}>
                Tiempo hábil de resolución: {formatBusinessMinutes(ticket.business_resolution_minutes)}
              </span>
            )}
          </div>
        </div>
      ) : (
        <p className="muted">Este ticket se resolvió sin registrar la solución.</p>
      )}
    </section>
  );
}
