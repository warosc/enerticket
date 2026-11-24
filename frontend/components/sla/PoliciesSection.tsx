'use client';

import { useMemo, useState, type FormEvent } from 'react';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import '@/lib/catalog';
import { CATEGORY_LABELS } from '@/lib/format';
import { useApiResource, useCatalog } from '@/lib/hooks';
import {
  PAUSE_CATEGORY_LABELS,
  formatTargetMinutes,
  slaApi,
  type PauseCategory,
  type SlaCalendar,
  type SlaPolicy,
  type SlaPolicyInput,
} from '@/lib/sla';
import type { Catalog } from '@/lib/types';
import { CheckboxGroup, Chips, DurationInput, Feedback, useMutation } from './shared';

const PAUSE_CATEGORIES: PauseCategory[] = ['todo', 'in_progress', 'waiting'];

function emptyForm(calendarId: number | null): SlaPolicyInput {
  return {
    name: '',
    description: '',
    calendar_id: calendarId ?? 0,
    priority_keys: [],
    type_keys: [],
    department_ids: [],
    category_ids: [],
    subcategory_ids: [],
    vip_only: false,
    precedence: 100,
    first_response_minutes: 60,
    resolution_minutes: 8 * 60,
    warning_threshold_pct: 75,
    pause_categories: ['waiting'],
    pause_states: [],
    active: true,
  };
}

function toForm(policy: SlaPolicy): SlaPolicyInput {
  return {
    name: policy.name,
    description: policy.description,
    calendar_id: policy.calendar_id,
    priority_keys: policy.priority_keys,
    type_keys: policy.type_keys,
    department_ids: policy.department_ids,
    category_ids: policy.category_ids ?? [],
    subcategory_ids: policy.subcategory_ids ?? [],
    vip_only: policy.vip_only ?? false,
    precedence: policy.precedence,
    first_response_minutes: policy.first_response_minutes,
    resolution_minutes: policy.resolution_minutes,
    warning_threshold_pct: policy.warning_threshold_pct,
    pause_categories: policy.pause_categories,
    pause_states: policy.pause_states,
    active: policy.active,
  };
}

function PolicyForm({
  initial,
  editingId,
  catalog,
  calendars,
  busy,
  onSubmit,
  onCancel,
}: {
  initial: SlaPolicyInput;
  editingId: number | null;
  catalog: Catalog;
  calendars: SlaCalendar[];
  busy: boolean;
  onSubmit: (value: SlaPolicyInput) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<SlaPolicyInput>(initial);
  const set = <K extends keyof SlaPolicyInput>(key: K, value: SlaPolicyInput[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));
  const noTargets = !form.first_response_minutes && !form.resolution_minutes;
  const valid = form.name.trim().length >= 2 && form.calendar_id > 0 && !noTargets;
  const idPrefix = editingId ? `policy-${editingId}` : 'policy-new';

  const priorities = [...catalog.priorities].sort((a, b) => b.rank - a.rank);
  const pausableStates = catalog.workflow.states.filter((s) => s.category !== 'done');

  // Catálogo de servicio: categorías de los departamentos elegidos (o de todos) y sus subcategorías.
  const categoryIds = form.category_ids ?? [];
  const subcategoryIds = form.subcategory_ids ?? [];
  const departmentNames = new Map(catalog.departments.map((d) => [d.id, d.name]));
  const inScope = (departmentIds: number[]) =>
    catalog.categories.filter((c) => departmentIds.length === 0 || departmentIds.includes(c.department_id));
  const scopedCategories = inScope(form.department_ids);
  const knownCategoryIds = new Set(catalog.categories.map((c) => c.id));
  const categoryOptions = [
    ...scopedCategories.map((c) => ({
      value: c.id,
      label: form.department_ids.length === 1 ? c.name : `${departmentNames.get(c.department_id) ?? '—'} / ${c.name}`,
    })),
    // Categorías ya elegidas que ya no están activas en el catálogo: se conservan.
    ...categoryIds.filter((id) => !knownCategoryIds.has(id)).map((id) => ({ value: id, label: `Categoría #${id} (inactiva)` })),
  ];
  const subcategorySource = categoryIds.length
    ? catalog.categories.filter((c) => categoryIds.includes(c.id))
    : scopedCategories;
  const knownSubcategoryIds = new Set(catalog.categories.flatMap((c) => c.subcategories.map((s) => s.id)));
  const subcategoryOptions = [
    ...subcategorySource.flatMap((c) => c.subcategories.map((s) => ({ value: s.id, label: `${c.name} / ${s.name}` }))),
    ...subcategoryIds
      .filter((id) => !knownSubcategoryIds.has(id))
      .map((id) => ({ value: id, label: `Subcategoría #${id} (inactiva)` })),
  ];

  /** Al cambiar departamentos o categorías se retiran las selecciones que quedan fuera de alcance. */
  function setDepartments(departmentIds: number[]) {
    const allowedCategories = new Set(inScope(departmentIds).map((c) => c.id));
    const nextCategories = categoryIds.filter((id) => !knownCategoryIds.has(id) || allowedCategories.has(id));
    setForm((prev) => ({
      ...prev,
      department_ids: departmentIds,
      category_ids: nextCategories,
      subcategory_ids: pruneSubcategories(nextCategories, departmentIds),
    }));
  }
  function setCategories(nextCategories: number[]) {
    setForm((prev) => ({
      ...prev,
      category_ids: nextCategories,
      subcategory_ids: pruneSubcategories(nextCategories, prev.department_ids),
    }));
  }
  function pruneSubcategories(nextCategories: number[], departmentIds: number[]): number[] {
    const source = nextCategories.length
      ? catalog.categories.filter((c) => nextCategories.includes(c.id))
      : inScope(departmentIds);
    const allowed = new Set(source.flatMap((c) => c.subcategories.map((s) => s.id)));
    return subcategoryIds.filter((id) => !knownSubcategoryIds.has(id) || allowed.has(id));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    onSubmit({ ...form, name: form.name.trim(), description: form.description.trim() });
  }

  return (
    <form className="panel form" onSubmit={submit} aria-label={editingId ? 'Editar política' : 'Nueva política'}>
      <h2 className="panel-title">{editingId ? `Editar «${initial.name}»` : 'Nueva política de SLA'}</h2>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">
            Nombre<span className="required">*</span>
          </span>
          <input
            className="input"
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            required
            minLength={2}
            maxLength={150}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Calendario<span className="required">*</span>
          </span>
          <select
            className="select"
            value={form.calendar_id || ''}
            onChange={(e) => set('calendar_id', Number(e.target.value))}
            required
          >
            <option value="" disabled>
              Seleccione…
            </option>
            {calendars.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-full">
          <span className="field-label">Descripción</span>
          <input
            className="input"
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
            maxLength={2000}
          />
        </label>
        <div className="field">
          <label className="field-label" htmlFor={`${idPrefix}-fr`}>
            Primera respuesta
          </label>
          <DurationInput
            id={`${idPrefix}-fr`}
            label="Primera respuesta"
            value={form.first_response_minutes}
            onChange={(v) => set('first_response_minutes', v)}
          />
          <span className="field-hint">Tiempo hábil del calendario. Vacío = sin objetivo.</span>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${idPrefix}-res`}>
            Resolución
          </label>
          <DurationInput
            id={`${idPrefix}-res`}
            label="Resolución"
            value={form.resolution_minutes}
            onChange={(v) => set('resolution_minutes', v)}
          />
          <span className="field-hint">Se detiene al resolver y se reanuda si el ticket se reabre.</span>
        </div>
        <label className="field">
          <span className="field-label">Advertencia al (%) del objetivo</span>
          <input
            className="input"
            type="number"
            min={1}
            max={99}
            value={form.warning_threshold_pct}
            onChange={(e) => set('warning_threshold_pct', Math.min(99, Math.max(1, Number(e.target.value) || 75)))}
          />
        </label>
        <label className="field">
          <span className="field-label">Precedencia</span>
          <input
            className="input"
            type="number"
            min={0}
            max={100000}
            value={form.precedence}
            onChange={(e) => set('precedence', Math.max(0, Number(e.target.value) || 0))}
          />
          <span className="field-hint">
            Gana siempre la más específica (subcategoría, luego categoría, luego las generales). Dentro del mismo nivel, menor número = se evalúa antes.
          </span>
        </label>
      </div>

      <CheckboxGroup
        legend="Prioridades"
        hint="Sin selección = cualquier prioridad."
        options={priorities.map((p) => ({ value: p.key, label: p.name }))}
        value={form.priority_keys}
        onChange={(v) => set('priority_keys', v)}
      />
      <CheckboxGroup
        legend="Tipos de ticket"
        hint="Sin selección = cualquier tipo."
        options={catalog.ticket_types.map((t) => ({ value: t.key, label: t.name }))}
        value={form.type_keys}
        onChange={(v) => set('type_keys', v)}
      />
      <CheckboxGroup
        legend="Departamentos"
        hint="Sin selección = cualquier departamento."
        options={[...catalog.departments]
          .sort((a, b) => a.name.localeCompare(b.name, 'es'))
          .map((d) => ({ value: d.id, label: d.name }))}
        value={form.department_ids}
        onChange={setDepartments}
      />
      {categoryOptions.length > 0 ? (
        <CheckboxGroup
          legend="Categorías"
          hint={
            form.department_ids.length
              ? 'Categorías de los departamentos elegidos. Sin selección = cualquier categoría.'
              : 'Sin selección = cualquier categoría. Elija departamentos para acotar la lista.'
          }
          options={categoryOptions}
          value={categoryIds}
          onChange={setCategories}
        />
      ) : (
        <p className="field-hint">
          {form.department_ids.length
            ? 'Los departamentos elegidos no tienen categorías activas.'
            : 'No hay categorías en el catálogo de servicio.'}
        </p>
      )}
      {subcategoryOptions.length > 0 && (
        <CheckboxGroup
          legend="Subcategorías"
          hint={
            categoryIds.length
              ? 'Subcategorías de las categorías elegidas. Sin selección = cualquiera.'
              : 'Sin selección = cualquiera. Elija categorías para acotar la lista.'
          }
          options={subcategoryOptions}
          value={subcategoryIds}
          onChange={(v) => set('subcategory_ids', v)}
        />
      )}
      <label className="checkbox">
        <input type="checkbox" checked={form.vip_only ?? false} onChange={(e) => set('vip_only', e.target.checked)} />
        Solo tickets de solicitantes VIP
      </label>
      <CheckboxGroup
        legend="Pausar el reloj en categorías"
        hint="Mientras el ticket esté en estas categorías el tiempo no corre (p. ej. esperando al solicitante)."
        options={PAUSE_CATEGORIES.map((c) => ({ value: c, label: PAUSE_CATEGORY_LABELS[c] }))}
        value={form.pause_categories}
        onChange={(v) => set('pause_categories', v)}
      />
      <CheckboxGroup
        legend="Pausar también en estados específicos"
        options={pausableStates.map((s) => ({ value: s.key, label: `${s.name} (${CATEGORY_LABELS[s.category]})` }))}
        value={form.pause_states}
        onChange={(v) => set('pause_states', v)}
      />

      <label className="checkbox">
        <input type="checkbox" checked={form.active} onChange={(e) => set('active', e.target.checked)} />
        Política activa
      </label>

      {noTargets && <p className="field-error">Defina al menos un objetivo: primera respuesta o resolución.</p>}
      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy || !valid}>
          {editingId ? 'Guardar cambios' : 'Crear política'}
        </button>
      </div>
      <p className="field-hint">
        Los cambios aplican a tickets nuevos y a los que cambien de prioridad o categoría. Use «Recalcular tickets abiertos» para
        aplicarlos a los tickets en curso.
      </p>
    </form>
  );
}

/** Políticas de SLA: listado (agentes en lectura) y edición (administradores). */
export default function PoliciesSection({ canEdit }: { canEdit: boolean }) {
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const policies = useApiResource<SlaPolicy[]>('/sla/policies');
  const calendars = useApiResource<SlaCalendar[]>('/sla/calendars');
  const mutation = useMutation();
  const [editing, setEditing] = useState<SlaPolicy | 'new' | null>(null);

  const names = useMemo(() => {
    const priorities = new Map(catalog?.priorities.map((p) => [p.key, p.name]) ?? []);
    const types = new Map(catalog?.ticket_types.map((t) => [t.key, t.name]) ?? []);
    const departments = new Map(catalog?.departments.map((d) => [d.id, d.name]) ?? []);
    const states = new Map(catalog?.workflow.states.map((s) => [s.key, s.name]) ?? []);
    const categories = new Map(catalog?.categories.map((c) => [c.id, c.name]) ?? []);
    const subcategories = new Map(
      catalog?.categories.flatMap((c) => c.subcategories.map((s) => [s.id, `${c.name} / ${s.name}`] as const)) ?? [],
    );
    return { priorities, types, departments, states, categories, subcategories };
  }, [catalog]);

  async function save(value: SlaPolicyInput) {
    const isNew = editing === 'new';
    const result = await mutation.run(
      () => (isNew || !editing ? slaApi.createPolicy(value) : slaApi.updatePolicy(editing.id, value)),
      isNew ? `Política «${value.name}» creada.` : 'Política actualizada.',
    );
    if (result) {
      setEditing(null);
      void policies.reload({ silent: true });
    }
  }

  async function toggleActive(policy: SlaPolicy) {
    const result = await mutation.run(
      () => slaApi.updatePolicy(policy.id, { active: !policy.active }),
      policy.active ? `Política «${policy.name}» desactivada.` : `Política «${policy.name}» activada.`,
    );
    if (result) void policies.reload({ silent: true });
  }

  async function remove(policy: SlaPolicy) {
    if (!window.confirm(`¿Eliminar la política «${policy.name}»? Esta acción no se puede deshacer.`)) return;
    const ok = await mutation.run(() => slaApi.deletePolicy(policy.id).then(() => true), 'Política eliminada.');
    if (ok) void policies.reload({ silent: true });
  }

  async function recalculate() {
    if (
      !window.confirm(
        'Se re-evaluará la política de todos los tickets abiertos con la configuración actual. Los relojes conservan su inicio y sus pausas. ¿Continuar?',
      )
    )
      return;
    const result = await mutation.run(
      () => slaApi.recalculate(),
      (r) =>
        `Tickets abiertos revisados: ${r.tickets}. Relojes recalculados: ${r.created}` +
        (r.breached ? ` (${r.breached} quedaron vencidos con la nueva configuración).` : '.'),
    );
    if (result) void policies.reload({ silent: true });
  }

  const list = policies.data ?? [];
  const calendarList = calendars.data ?? [];

  return (
    <div className="stack">
      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
      {canEdit && (
        <div className="row-between">
          <p className="text-sm muted" style={{ maxWidth: 720 }}>
            Cada ticket toma la primera política activa (por precedencia) cuyas condiciones cumple. Las condiciones vacías
            aplican a cualquier valor.
          </p>
          <div className="btn-group">
            <button type="button" className="btn" onClick={() => void recalculate()} disabled={mutation.busy}>
              Recalcular tickets abiertos
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                mutation.clear();
                setEditing('new');
              }}
              disabled={!catalog || calendarList.length === 0}
            >
              Nueva política
            </button>
          </div>
        </div>
      )}

      <Feedback mutation={mutation} />

      {canEdit && editing && catalog && (
        <PolicyForm
          key={editing === 'new' ? 'new' : editing.id}
          initial={editing === 'new' ? emptyForm(calendarList[0]?.id ?? null) : toForm(editing)}
          editingId={editing === 'new' ? null : editing.id}
          catalog={catalog}
          calendars={calendarList}
          busy={mutation.busy}
          onSubmit={(v) => void save(v)}
          onCancel={() => setEditing(null)}
        />
      )}

      <section className="panel panel-flush">
        <ErrorAlert error={policies.error ?? calendars.error} onRetry={() => void policies.reload()} />
        {policies.loading && !policies.data ? (
          <Loading label="Cargando políticas…" />
        ) : list.length === 0 ? (
          !policies.error && (
            <EmptyState title="No hay políticas de SLA">
              {canEdit ? 'Cree una política para empezar a medir tiempos de servicio.' : 'Un administrador debe configurarlas.'}
            </EmptyState>
          )
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col" className="col-num">
                    Orden
                  </th>
                  <th scope="col">Política</th>
                  <th scope="col">Aplica a</th>
                  <th scope="col">Calendario</th>
                  <th scope="col">1.ª respuesta</th>
                  <th scope="col">Resolución</th>
                  <th scope="col">Pausa en</th>
                  <th scope="col">Estado</th>
                  {canEdit && (
                    <th scope="col" className="text-right">
                      Acciones
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.id} style={p.active ? undefined : { opacity: 0.6 }}>
                    <td className="col-num">{p.precedence}</td>
                    <td>
                      <div className="strong">{p.name}</div>
                      {p.description && <div className="text-xs subtle">{p.description}</div>}
                      <div className="text-xs subtle">Aviso al {p.warning_threshold_pct} %</div>
                    </td>
                    <td>
                      <div className="stack-sm">
                        <Chips
                          items={p.priority_keys.map((k) => names.priorities.get(k) ?? k)}
                          empty="Toda prioridad"
                        />
                        {p.type_keys.length > 0 && <Chips items={p.type_keys.map((k) => names.types.get(k) ?? k)} />}
                        {p.department_ids.length > 0 && (
                          <Chips items={p.department_ids.map((id) => names.departments.get(id) ?? `#${id}`)} />
                        )}
                        {(p.category_ids?.length ?? 0) > 0 && (
                          <Chips items={p.category_ids.map((id) => names.categories.get(id) ?? `Categoría #${id}`)} />
                        )}
                        {(p.subcategory_ids?.length ?? 0) > 0 && (
                          <Chips
                            items={p.subcategory_ids.map((id) => names.subcategories.get(id) ?? `Subcategoría #${id}`)}
                          />
                        )}
                        {p.vip_only && <span className="badge badge-warning">Solo VIP</span>}
                      </div>
                    </td>
                    <td className="text-sm">{p.calendar_name ?? '—'}</td>
                    <td className="nowrap">{formatTargetMinutes(p.first_response_minutes)}</td>
                    <td className="nowrap">{formatTargetMinutes(p.resolution_minutes)}</td>
                    <td>
                      <Chips
                        items={[
                          ...p.pause_categories.map((c) => PAUSE_CATEGORY_LABELS[c] ?? c),
                          ...p.pause_states.map((s) => names.states.get(s) ?? s),
                        ]}
                        empty="Nunca"
                      />
                    </td>
                    <td>
                      <span className={`badge ${p.active ? 'badge-success' : 'badge-neutral'}`}>
                        {p.active ? 'Activa' : 'Inactiva'}
                      </span>
                      <div className="text-xs subtle">{p.instance_count} relojes</div>
                    </td>
                    {canEdit && (
                      <td className="text-right nowrap">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            mutation.clear();
                            setEditing(p);
                          }}
                        >
                          Editar
                        </button>{' '}
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => void toggleActive(p)}
                          disabled={mutation.busy}
                        >
                          {p.active ? 'Desactivar' : 'Activar'}
                        </button>{' '}
                        {p.instance_count === 0 && (
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => void remove(p)}
                            disabled={mutation.busy}
                          >
                            Eliminar
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
