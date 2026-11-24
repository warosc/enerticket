'use client';

import { useId, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { ApiError, apiFetch, errorMessage } from '@/lib/api';
import type { Catalog, TicketOut, TransitionRequest, WorkflowTransition } from '@/lib/types';
import CategoryFields, { type CategoryValue } from './CategoryFields';
import Dialog from './Dialog';

export interface ResolveFailure {
  error: unknown;
  /** El ticket se clasificó antes de fallar la transición (hay que recargarlo). */
  classified: boolean;
}

/**
 * Diálogo para resolver un ticket (transición hacia un estado «done»): exige la solución y, si quien
 * resuelve atiende el ticket y el departamento tiene catálogo, la categoría (se guarda antes de resolver).
 * Los errores que no son de validación (p.ej. 409 de aprobación) se devuelven al llamador.
 */
export default function ResolveDialog({
  ticket,
  transition,
  targetName,
  catalog,
  needsCategory,
  comment,
  onCancel,
  onResolved,
  onFailed,
}: {
  ticket: TicketOut;
  transition: WorkflowTransition;
  targetName: string;
  catalog: Catalog;
  needsCategory: boolean;
  /** Comentario de la transición escrito en el panel de estado (se envía junto con la solución). */
  comment: string;
  onCancel: () => void;
  onResolved: (ticket: TicketOut) => void;
  onFailed: (failure: ResolveFailure) => void;
}) {
  const noteId = useId();
  const [note, setNote] = useState(ticket.resolution_note ?? '');
  const [classification, setClassification] = useState<CategoryValue>({ categoryId: null, subcategoryId: null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const label = transition.name || targetName;
  const noteValid = note.trim().length > 0;
  const categoryValid = !needsCategory || classification.categoryId !== null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!noteValid) {
      setError('Indique la solución antes de resolver.');
      return;
    }
    if (!categoryValid) {
      setError('Seleccione la categoría del ticket antes de resolverlo.');
      return;
    }
    setBusy(true);
    setError(null);
    let classified = false;
    try {
      if (needsCategory && classification.categoryId !== null) {
        await apiFetch<TicketOut>(`/tickets/${ticket.id}`, {
          method: 'PATCH',
          body: { category_id: classification.categoryId, subcategory_id: classification.subcategoryId },
        });
        classified = true;
      }
      const payload: TransitionRequest = { to_state_key: transition.to_key, resolution_note: note.trim() };
      if (comment.trim()) payload.comment = comment.trim();
      const updated = await apiFetch<TicketOut>(`/tickets/${ticket.id}/transition`, { method: 'POST', body: payload });
      onResolved(updated);
    } catch (e) {
      // Validaciones (422) se muestran en el diálogo para corregirlas; el resto lo maneja la página.
      if (e instanceof ApiError && (e.status === 422 || e.status === 0)) {
        setError(errorMessage(e));
        setBusy(false);
        return;
      }
      onFailed({ error: e, classified });
    }
  }

  return (
    <Dialog
      title={`${label}: ${ticket.key}`}
      subtitle={`El ticket pasará a «${targetName}». Registre la solución aplicada.`}
      onClose={onCancel}
      busy={busy}
    >
      <form className="stack" onSubmit={submit} noValidate>
        {error && <Alert tone="error">{error}</Alert>}
        <div className="field">
          <label className="field-label" htmlFor={noteId}>
            Solución<span className="required">*</span>
          </label>
          <textarea
            id={noteId}
            className="textarea"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            required
            maxLength={20000}
            rows={5}
            autoFocus
            aria-invalid={error !== null && !noteValid}
            placeholder="Qué se hizo para resolver la solicitud o el motivo del cierre"
          />
          <span className="field-hint">Se muestra al solicitante y en los reportes.</span>
        </div>
        {needsCategory && (
          <div className="stack-sm">
            <Alert tone="info">
              Este departamento tiene catálogo de servicio: clasifique el ticket antes de resolverlo.
            </Alert>
            <CategoryFields
              catalog={catalog}
              departmentId={ticket.department.id}
              typeKey={ticket.type.key}
              value={classification}
              onChange={setClassification}
              required
            />
          </div>
        )}
        {comment.trim() && (
          <p className="text-sm muted">También se publicará el comentario de la transición que escribió.</p>
        )}
        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !noteValid || !categoryValid}>
            {busy ? 'Aplicando…' : label}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
