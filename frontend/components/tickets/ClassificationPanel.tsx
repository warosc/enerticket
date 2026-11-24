'use client';

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { channelLabel, departmentHasCategories, providersFor } from '@/lib/catalog';
import type { Catalog, CatalogProvider, TicketChannel, TicketOut, TicketUpdate } from '@/lib/types';
import CategoryFields, { type CategoryValue } from './CategoryFields';

/**
 * Clasificación del ticket (categoría, subcategoría, proveedor y canal). Editable por quien atiende
 * el ticket (`canEditTicket`); el resto la ve en solo lectura. Cambiar la categoría re-evalúa el SLA.
 */
export default function ClassificationPanel({
  ticket,
  catalog,
  canEdit,
  onSaved,
}: {
  ticket: TicketOut;
  catalog: Catalog;
  canEdit: boolean;
  onSaved: (ticket: TicketOut) => void;
}) {
  const initial = useMemo<CategoryValue>(
    () => ({ categoryId: ticket.category?.id ?? null, subcategoryId: ticket.subcategory?.id ?? null }),
    [ticket.category?.id, ticket.subcategory?.id],
  );
  const [classification, setClassification] = useState<CategoryValue>(initial);
  const [providerId, setProviderId] = useState<number | ''>(ticket.provider?.id ?? '');
  const [channel, setChannel] = useState<TicketChannel>(ticket.channel);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Sincroniza el formulario cuando el ticket cambia desde fuera (tiempo real, otro panel).
  useEffect(() => {
    setClassification(initial);
    setProviderId(ticket.provider?.id ?? '');
    setChannel(ticket.channel);
  }, [initial, ticket.provider?.id, ticket.channel]);

  const providers = useMemo<CatalogProvider[]>(() => {
    const list = providersFor(catalog, ticket.department.id);
    if (ticket.provider && !list.some((p) => p.id === ticket.provider?.id)) {
      list.unshift({ id: ticket.provider.id, name: ticket.provider.name, department_ids: [] });
    }
    return list;
  }, [catalog, ticket.department.id, ticket.provider]);

  const channels = catalog.channels.length
    ? catalog.channels
    : (['portal', 'email', 'phone', 'other'] as TicketChannel[]).map((key) => ({ key, label: channelLabel(key) }));

  const hasCatalog = departmentHasCategories(catalog, ticket.department.id, ticket.type?.key);

  const changes: TicketUpdate = {};
  if (classification.categoryId !== (ticket.category?.id ?? null)) changes.category_id = classification.categoryId;
  if (classification.subcategoryId !== (ticket.subcategory?.id ?? null)) changes.subcategory_id = classification.subcategoryId;
  if ((providerId === '' ? null : providerId) !== (ticket.provider?.id ?? null)) {
    changes.provider_id = providerId === '' ? null : providerId;
  }
  if (channel !== ticket.channel) changes.channel = channel;
  const dirty = Object.keys(changes).length > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!dirty) return;
    // Al cambiar la categoría se envía también la subcategoría (aunque quede vacía).
    const payload: TicketUpdate = { ...changes };
    if ('category_id' in payload && !('subcategory_id' in payload)) payload.subcategory_id = classification.subcategoryId;
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

  if (!canEdit) {
    return (
      <section className="panel" aria-labelledby="ticket-classification-title">
        <div className="panel-header">
          <h2 className="panel-title" id="ticket-classification-title">
            Clasificación
          </h2>
        </div>
        <dl className="dl">
          <dt>Tipo</dt>
          <dd>{ticket.type.name}</dd>
          <dt>Categoría</dt>
          <dd>{ticket.category?.name ?? <span className="subtle">Sin clasificar</span>}</dd>
          <dt>Subcategoría</dt>
          <dd>{ticket.subcategory?.name ?? <span className="subtle">—</span>}</dd>
          <dt>Proveedor</dt>
          <dd>{ticket.provider?.name ?? <span className="subtle">—</span>}</dd>
          <dt>Canal</dt>
          <dd>{channelLabel(ticket.channel, catalog)}</dd>
        </dl>
      </section>
    );
  }

  return (
    <form className="panel form" onSubmit={submit} aria-labelledby="ticket-classification-title">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h2 className="panel-title" id="ticket-classification-title">
          Clasificación
        </h2>
        <span className="badge badge-outline">{ticket.type.name}</span>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {saved && !dirty && <Alert tone="success">Clasificación guardada.</Alert>}
      {hasCatalog && !ticket.category && (
        <Alert tone="info">Clasifique el ticket: la categoría es obligatoria para resolverlo.</Alert>
      )}
      <CategoryFields
        catalog={catalog}
        departmentId={ticket.department.id}
        typeKey={ticket.type.key}
        value={classification}
        onChange={setClassification}
        current={{ category: ticket.category, subcategory: ticket.subcategory }}
      />
      <label className="field">
        <span className="field-label">Proveedor</span>
        <select
          className="select"
          value={providerId}
          onChange={(e) => setProviderId(e.target.value === '' ? '' : Number(e.target.value))}
          disabled={providers.length === 0}
        >
          <option value="">{providers.length === 0 ? 'Sin proveedores para el departamento' : 'Sin proveedor'}</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-label">Canal</span>
        <select className="select" value={channel} onChange={(e) => setChannel(e.target.value as TicketChannel)}>
          {channels.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <div className="form-actions">
        {dirty && (
          <button
            type="button"
            className="btn btn-ghost"
            disabled={saving}
            onClick={() => {
              setClassification(initial);
              setProviderId(ticket.provider?.id ?? '');
              setChannel(ticket.channel);
            }}
          >
            Descartar
          </button>
        )}
        <button type="submit" className="btn btn-primary" disabled={!dirty || saving}>
          {saving ? 'Guardando…' : 'Guardar clasificación'}
        </button>
      </div>
    </form>
  );
}
