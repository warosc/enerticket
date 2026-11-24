'use client';

import { useId, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { WORK_ORDER_TYPE } from '@/lib/catalog';
import type { Catalog, TicketOut, WorkOrderRequest } from '@/lib/types';
import CategoryFields, { type CategoryValue } from './CategoryFields';
import Dialog from './Dialog';

/**
 * «Trasladar a orden de trabajo»: crea un ticket hijo de tipo Orden de trabajo (mismo departamento y
 * solicitante) y, opcionalmente, resuelve el original con una solución.
 */
export default function WorkOrderDialog({
  ticket,
  catalog,
  parentNeedsCategory,
  onCancel,
  onCreated,
}: {
  ticket: TicketOut;
  catalog: Catalog;
  /** El original no tiene categoría y su departamento tiene catálogo: no puede resolverse aún. */
  parentNeedsCategory: boolean;
  onCancel: () => void;
  onCreated: (child: TicketOut) => void;
}) {
  const id = useId();
  const parentDone = ticket.status.category === 'done';
  const [title, setTitle] = useState(`Orden de trabajo: ${ticket.title}`.slice(0, 200));
  const [description, setDescription] = useState(ticket.description);
  const [classification, setClassification] = useState<CategoryValue>({ categoryId: null, subcategoryId: null });
  const [resolveParent, setResolveParent] = useState(!parentDone && !parentNeedsCategory);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const titleValid = title.trim().length >= 3;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!titleValid) {
      setError('El título debe tener al menos 3 caracteres.');
      return;
    }
    setBusy(true);
    setError(null);
    const payload: WorkOrderRequest = {
      title: title.trim(),
      description: description.trim(),
      category_id: classification.categoryId,
      subcategory_id: classification.subcategoryId,
      resolve_parent: resolveParent && !parentDone,
    };
    if (payload.resolve_parent && note.trim()) payload.resolution_note = note.trim();
    try {
      const child = await apiFetch<TicketOut>(`/tickets/${ticket.id}/work-order`, { method: 'POST', body: payload });
      onCreated(child);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="Trasladar a orden de trabajo"
      subtitle={`Se creará una orden de trabajo vinculada a ${ticket.key}, con el mismo solicitante y departamento.`}
      onClose={onCancel}
      busy={busy}
    >
      <form className="stack" onSubmit={submit}>
        {error && <Alert tone="error">{error}</Alert>}
        <div className="field">
          <label className="field-label" htmlFor={`${id}-title`}>
            Título de la orden<span className="required">*</span>
          </label>
          <input
            id={`${id}-title`}
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            minLength={3}
            maxLength={200}
            autoFocus
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${id}-description`}>
            Descripción
          </label>
          <textarea
            id={`${id}-description`}
            className="textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={20000}
            rows={4}
          />
        </div>
        <div className="form-grid">
          <CategoryFields
            catalog={catalog}
            departmentId={ticket.department.id}
            typeKey={WORK_ORDER_TYPE}
            value={classification}
            onChange={setClassification}
          />
        </div>
        {parentDone ? (
          <p className="text-sm muted">El ticket original ya está resuelto; solo se creará la orden de trabajo.</p>
        ) : (
          <div className="stack-sm">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={resolveParent}
                disabled={parentNeedsCategory}
                onChange={(e) => setResolveParent(e.target.checked)}
              />
              Resolver {ticket.key} al crear la orden
            </label>
            {parentNeedsCategory && (
              <span className="field-hint">
                Para resolverlo, clasifique primero {ticket.key} (categoría) en el panel «Clasificación».
              </span>
            )}
            {resolveParent && !parentNeedsCategory && (
              <div className="field">
                <label className="field-label" htmlFor={`${id}-note`}>
                  Solución del ticket original
                </label>
                <textarea
                  id={`${id}-note`}
                  className="textarea"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={20000}
                  rows={3}
                  placeholder="Si se deja vacía: «Trasladado a orden de trabajo #<número>»"
                />
              </div>
            )}
          </div>
        )}
        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !titleValid}>
            {busy ? 'Creando…' : 'Crear orden de trabajo'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
