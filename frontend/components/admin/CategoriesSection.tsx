'use client';

/**
 * Categorías del catálogo de servicio por departamento de atención y tipo de ticket, con sus
 * subcategorías en línea. Las categorías y subcategorías no se borran: se desactivan.
 */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { CheckboxGroup } from '@/components/sla/shared';
import Tags from './Tags';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { catalogApi } from '@/lib/catalog';
import { useApiResource, useCatalog } from '@/lib/hooks';
import type { AdminCategory, AdminSubcategory, Department } from '@/lib/types';
import { Feedback, useMutation } from './AdminSections';
import styles from './admin.module.css';

const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name, 'es');

interface CategoryForm {
  name: string;
  type_keys: string[];
  sort_order: number;
}

const EMPTY_FORM: CategoryForm = { name: '', type_keys: [], sort_order: 0 };

export default function CategoriesSection() {
  const { catalog } = useCatalog();
  const depsRes = useApiResource<Department[]>('/admin/departments');
  const departments = useMemo(() => {
    const list = [...(depsRes.data ?? [])].sort(byName);
    // Primero los departamentos de atención activos.
    return [...list.filter((d) => d.serves_tickets && d.active), ...list.filter((d) => !(d.serves_tickets && d.active))];
  }, [depsRes.data]);
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [typeFilter, setTypeFilter] = useState('');
  const [showInactive, setShowInactive] = useState(false);
  const categoriesRes = useApiResource<AdminCategory[]>(
    departmentId ? '/admin/categories' : null,
    departmentId ? { department_id: departmentId } : undefined,
  );
  const mutation = useMutation();
  const [form, setForm] = useState<CategoryForm>(EMPTY_FORM);

  useEffect(() => {
    if (departmentId !== null || departments.length === 0 || !catalog) return;
    // Por defecto, el primer departamento que ya tiene categorías (si no, el primero de atención).
    const withCategories = new Set((catalog?.categories ?? []).map((c) => c.department_id));
    setDepartmentId((departments.find((d) => withCategories.has(d.id)) ?? departments[0]).id);
  }, [departmentId, departments, catalog]);

  const types = catalog?.ticket_types ?? [];
  const typeName = (key: string) => types.find((t) => t.key === key)?.name ?? key;
  const department = departments.find((d) => d.id === departmentId) ?? null;

  const categories = useMemo(() => {
    const list = [...(categoriesRes.data ?? [])].sort((a, b) => a.sort_order - b.sort_order || byName(a, b));
    return list.filter(
      (c) =>
        (showInactive || c.active) &&
        (!typeFilter || c.type_keys.length === 0 || c.type_keys.includes(typeFilter)),
    );
  }, [categoriesRes.data, showInactive, typeFilter]);
  const inactiveCount = (categoriesRes.data ?? []).filter((c) => !c.active).length;

  function replaceCategory(updated: AdminCategory) {
    categoriesRes.setData((prev) => (prev ? prev.map((c) => (c.id === updated.id ? updated : c)) : prev));
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!departmentId) return;
    const name = form.name.trim();
    const created = await mutation.run(
      () => catalogApi.createCategory({ department_id: departmentId, name, type_keys: form.type_keys, sort_order: form.sort_order }),
      `Categoría «${name}» creada.`,
    );
    if (created) {
      setForm({ ...EMPTY_FORM, type_keys: form.type_keys });
      categoriesRes.setData((prev) => [...(prev ?? []), created]);
    }
  }

  return (
    <div className="stack">
      <section className="panel stack">
        <ErrorAlert error={depsRes.error} onRetry={() => void depsRes.reload()} />
        <div className="toolbar">
          <label className="field">
            <span className="field-label">Departamento</span>
            <select
              className="select"
              value={departmentId ?? ''}
              onChange={(e) => {
                mutation.clear();
                setDepartmentId(e.target.value === '' ? null : Number(e.target.value));
              }}
            >
              {departments.length === 0 && <option value="">No hay departamentos</option>}
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                  {d.serves_tickets && d.active ? '' : d.active ? ' (no atiende tickets)' : ' (inactivo)'}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Tipo de ticket</span>
            <select className="select" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">Todos</option>
              {types.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox" style={{ alignSelf: 'flex-end', paddingBottom: 8 }}>
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            Mostrar inactivas{inactiveCount ? ` (${inactiveCount})` : ''}
          </label>
        </div>
        {department && !(department.serves_tickets && department.active) && (
          <p className="field-hint">
            Este departamento no recibe tickets: sus categorías no aparecerán al crear tickets hasta marcarlo como «Atiende
            tickets» en Departamentos.
          </p>
        )}
      </section>

      {department && (
        <form className="panel form" onSubmit={create}>
          <h2 className="panel-title">Nueva categoría en {department.name}</h2>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">
                Nombre<span className="required">*</span>
              </span>
              <input
                className="input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
                maxLength={150}
                placeholder="Telefonía móvil"
              />
            </label>
            <label className="field">
              <span className="field-label">Orden</span>
              <input
                className="input"
                type="number"
                min={-100000}
                max={100000}
                value={form.sort_order}
                onChange={(e) => setForm({ ...form, sort_order: Math.trunc(Number(e.target.value) || 0) })}
              />
              <span className="field-hint">Menor número = aparece antes.</span>
            </label>
          </div>
          <CheckboxGroup
            legend="Tipos de ticket que admite"
            hint="Sin selección = todos los tipos."
            options={types.map((t) => ({ value: t.key, label: t.name }))}
            value={form.type_keys}
            onChange={(type_keys) => setForm({ ...form, type_keys })}
          />
          <div className="form-actions">
            <button className="btn btn-primary" disabled={mutation.busy || !form.name.trim()}>
              Crear categoría
            </button>
          </div>
        </form>
      )}

      <Feedback error={mutation.error} notice={mutation.notice} />

      {department && (
        <section className="panel panel-flush" aria-label={`Categorías de ${department.name}`}>
          <ErrorAlert error={categoriesRes.error} onRetry={() => void categoriesRes.reload()} />
          {categoriesRes.loading && !categoriesRes.data ? (
            <Loading />
          ) : categories.length === 0 ? (
            <EmptyState title="No hay categorías">
              {(categoriesRes.data ?? []).length > 0
                ? 'Ninguna coincide con el filtro.'
                : 'Créelas con el formulario o impórtelas desde un CSV en «Importar catálogo».'}
            </EmptyState>
          ) : (
            <ul className={styles.categoryList}>
              {categories.map((c) => (
                <CategoryItem
                  key={c.id}
                  category={c}
                  types={types.map((t) => ({ value: t.key, label: t.name }))}
                  typeName={typeName}
                  showInactive={showInactive}
                  mutation={mutation}
                  onUpdated={replaceCategory}
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

type Mutation = ReturnType<typeof useMutation>;

function CategoryItem({
  category,
  types,
  typeName,
  showInactive,
  mutation,
  onUpdated,
}: {
  category: AdminCategory;
  types: Array<{ value: string; label: string }>;
  typeName: (key: string) => string;
  showInactive: boolean;
  mutation: Mutation;
  onUpdated: (category: AdminCategory) => void;
}) {
  const [editing, setEditing] = useState<CategoryForm | null>(null);
  const [newSub, setNewSub] = useState('');
  const [editingSub, setEditingSub] = useState<{ id: number; name: string; sort_order: number } | null>(null);

  // Tipos inactivos ya guardados en la categoría se conservan como opción.
  const typeOptions = useMemo(() => {
    const extra = (editing?.type_keys ?? []).filter((k) => !types.some((t) => t.value === k));
    return [...types, ...extra.map((k) => ({ value: k, label: `${k} (inactivo)` }))];
  }, [types, editing?.type_keys]);

  const subcategories = [...category.subcategories]
    .sort((a, b) => a.sort_order - b.sort_order || byName(a, b))
    .filter((s) => showInactive || s.active);
  const hiddenSubs = category.subcategories.filter((s) => !s.active).length;

  async function saveCategory(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const updated = await mutation.run(
      () =>
        catalogApi.updateCategory(category.id, {
          name: editing.name.trim(),
          type_keys: editing.type_keys,
          sort_order: editing.sort_order,
        }),
      'Categoría actualizada.',
    );
    if (updated) {
      onUpdated(updated);
      setEditing(null);
    }
  }

  async function toggleCategory() {
    const updated = await mutation.run(
      () => catalogApi.updateCategory(category.id, { active: !category.active }),
      category.active ? `Categoría «${category.name}» desactivada.` : `Categoría «${category.name}» activada.`,
    );
    if (updated) onUpdated(updated);
  }

  async function addSub(event: FormEvent) {
    event.preventDefault();
    const name = newSub.trim();
    if (!name) return;
    const nextOrder = category.subcategories.reduce((max, s) => Math.max(max, s.sort_order), 0) + 10;
    const updated = await mutation.run(
      () => catalogApi.createSubcategory(category.id, { name, sort_order: nextOrder }),
      `Subcategoría «${name}» agregada.`,
    );
    if (updated) {
      onUpdated(updated);
      setNewSub('');
    }
  }

  async function saveSub(event: FormEvent) {
    event.preventDefault();
    if (!editingSub) return;
    const updated = await mutation.run(
      () => catalogApi.updateSubcategory(editingSub.id, { name: editingSub.name.trim(), sort_order: editingSub.sort_order }),
      'Subcategoría actualizada.',
    );
    if (updated) {
      onUpdated(updated);
      setEditingSub(null);
    }
  }

  async function toggleSub(sub: AdminSubcategory) {
    const updated = await mutation.run(
      () => catalogApi.updateSubcategory(sub.id, { active: !sub.active }),
      sub.active ? `Subcategoría «${sub.name}» desactivada.` : `Subcategoría «${sub.name}» activada.`,
    );
    if (updated) onUpdated(updated);
  }

  return (
    <li className={`${styles.categoryItem}${category.active ? '' : ` ${styles.inactive}`}`}>
      {editing ? (
        <form className="stack" onSubmit={saveCategory} aria-label={`Editar categoría ${category.name}`}>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">
                Nombre<span className="required">*</span>
              </span>
              <input
                className="input"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                required
                maxLength={150}
              />
            </label>
            <label className="field">
              <span className="field-label">Orden</span>
              <input
                className="input"
                type="number"
                min={-100000}
                max={100000}
                value={editing.sort_order}
                onChange={(e) => setEditing({ ...editing, sort_order: Math.trunc(Number(e.target.value) || 0) })}
              />
            </label>
          </div>
          <CheckboxGroup
            legend="Tipos de ticket que admite"
            hint="Sin selección = todos los tipos."
            options={typeOptions}
            value={editing.type_keys}
            onChange={(type_keys) => setEditing({ ...editing, type_keys })}
          />
          <div className="form-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setEditing(null)}>
              Cancelar
            </button>
            <button className="btn btn-primary" disabled={mutation.busy || !editing.name.trim()}>
              Guardar
            </button>
          </div>
        </form>
      ) : (
        <div className={styles.categoryHead}>
          <div style={{ minWidth: 0 }}>
            <div className={styles.categoryName}>{category.name}</div>
            <div className={styles.categoryMeta}>
              <Tags items={category.type_keys.map(typeName)} empty="Todos los tipos" />
              {!category.active && <span className="badge badge-neutral">Inactiva</span>}
              <span className="text-xs subtle">Orden {category.sort_order}</span>
            </div>
          </div>
          <span className="row-actions">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() =>
                setEditing({ name: category.name, type_keys: [...category.type_keys], sort_order: category.sort_order })
              }
            >
              Editar
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={mutation.busy} onClick={() => void toggleCategory()}>
              {category.active ? 'Desactivar' : 'Activar'}
            </button>
          </span>
        </div>
      )}

      <ul className={styles.subList} aria-label={`Subcategorías de ${category.name}`}>
        {subcategories.map((s) =>
          editingSub?.id === s.id ? (
            <li key={s.id} className={styles.subItem}>
              <form className={styles.inlineForm} onSubmit={saveSub} style={{ flex: 1 }}>
                <input
                  className="input input-sm"
                  value={editingSub.name}
                  onChange={(e) => setEditingSub({ ...editingSub, name: e.target.value })}
                  required
                  maxLength={150}
                  aria-label="Nombre de la subcategoría"
                  autoFocus
                />
                <input
                  className={`input input-sm ${styles.orderInput}`}
                  type="number"
                  min={-100000}
                  max={100000}
                  value={editingSub.sort_order}
                  onChange={(e) => setEditingSub({ ...editingSub, sort_order: Math.trunc(Number(e.target.value) || 0) })}
                  aria-label="Orden de la subcategoría"
                />
                <button type="submit" className="btn btn-primary btn-sm" disabled={mutation.busy || !editingSub.name.trim()}>
                  Guardar
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditingSub(null)}>
                  Cancelar
                </button>
              </form>
            </li>
          ) : (
            <li key={s.id} className={`${styles.subItem}${s.active ? '' : ` ${styles.inactive}`}`}>
              <span className={styles.subName}>
                {s.name}
                {!s.active && <span className="text-xs subtle"> (inactiva)</span>}
              </span>
              <span className="row-actions">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setEditingSub({ id: s.id, name: s.name, sort_order: s.sort_order })}
                  aria-label={`Editar subcategoría ${s.name}`}
                >
                  Editar
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={mutation.busy}
                  onClick={() => void toggleSub(s)}
                  aria-label={`${s.active ? 'Desactivar' : 'Activar'} subcategoría ${s.name}`}
                >
                  {s.active ? 'Desactivar' : 'Activar'}
                </button>
              </span>
            </li>
          ),
        )}
        {subcategories.length === 0 && (
          <li className="text-xs subtle">
            Sin subcategorías{hiddenSubs && !showInactive ? ` activas (${hiddenSubs} inactiva${hiddenSubs === 1 ? '' : 's'})` : ''}.
          </li>
        )}
        <li>
          <form className={styles.inlineForm} onSubmit={addSub}>
            <input
              className="input input-sm"
              value={newSub}
              onChange={(e) => setNewSub(e.target.value)}
              maxLength={150}
              placeholder="Nueva subcategoría"
              aria-label={`Nueva subcategoría de ${category.name}`}
            />
            <button type="submit" className="btn btn-sm" disabled={mutation.busy || !newSub.trim()}>
              Agregar
            </button>
          </form>
        </li>
      </ul>
    </li>
  );
}
