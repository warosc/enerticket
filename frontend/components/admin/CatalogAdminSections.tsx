'use client';

/**
 * Administración del catálogo de servicio (Fase 3): gerencias (con acceso por grupo AD o manual),
 * valores del atributo `department` del AD, proveedores y perfil de usuarios (departamento y VIP).
 * Las categorías y la importación CSV están en CategoriesSection.tsx y CatalogImportSection.tsx.
 */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { CheckboxGroup } from '@/components/sla/shared';
import Tags from './Tags';
import UserPicker from '@/components/users/UserPicker';
import { Alert, EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { catalogApi } from '@/lib/catalog';
import { formatDateTime, ROLE_LABELS } from '@/lib/format';
import { useApiResource, useDebouncedValue } from '@/lib/hooks';
import type { Department, DepartmentAlias, Gerencia, GerenciaViewer, Provider, UserOut } from '@/lib/types';
import { Feedback, useMutation } from './AdminSections';
import styles from './admin.module.css';

const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name, 'es');

function useDepartments() {
  const res = useApiResource<Department[]>('/admin/departments');
  const departments = useMemo(() => [...(res.data ?? [])].sort(byName), [res.data]);
  return { ...res, departments };
}

// ---------------------------------------------------------------- Gerencias

export function GerenciasSection() {
  const { data, error, loading, reload } = useApiResource<Gerencia[]>('/admin/gerencias');
  const deps = useDepartments();
  const gerencias = useMemo(() => [...(data ?? [])].sort(byName), [data]);
  const mutation = useMutation();
  const [form, setForm] = useState({ name: '', external_group: '' });
  const [editing, setEditing] = useState<Gerencia | null>(null);
  const [viewersOf, setViewersOf] = useState<Gerencia | null>(null);

  const depName = (id: number) => deps.departments.find((d) => d.id === id)?.name ?? `#${id}`;

  async function create(event: FormEvent) {
    event.preventDefault();
    const name = form.name.trim();
    const created = await mutation.run(
      () => catalogApi.createGerencia({ name, external_group: form.external_group.trim() || null, active: true }),
      `Gerencia «${name}» creada.`,
    );
    if (created) {
      setForm({ name: '', external_group: '' });
      void reload({ silent: true });
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const updated = await mutation.run(
      () =>
        catalogApi.updateGerencia(editing.id, {
          name: editing.name.trim(),
          external_group: editing.external_group?.trim() || null,
        }),
      'Gerencia actualizada.',
    );
    if (updated) {
      setEditing(null);
      void reload({ silent: true });
    }
  }

  async function toggleActive(gerencia: Gerencia) {
    const updated = await mutation.run(
      () => catalogApi.updateGerencia(gerencia.id, { active: !gerencia.active }),
      gerencia.active ? `Gerencia «${gerencia.name}» desactivada.` : `Gerencia «${gerencia.name}» activada.`,
    );
    if (updated) void reload({ silent: true });
  }

  return (
    <div className="stack">
      <form className="panel form" onSubmit={create}>
        <div>
          <h2 className="panel-title">Nueva gerencia</h2>
          <p className="panel-subtitle">
            Cada departamento pertenece a una gerencia (se asigna en «Departamentos»). Los miembros del grupo del AD
            indicado, y los usuarios con acceso manual, ven en solo lectura los tickets solicitados por los departamentos
            de la gerencia.
          </p>
        </div>
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
              minLength={2}
              maxLength={150}
            />
          </label>
          <label className="field">
            <span className="field-label">Grupo del AD con acceso</span>
            <input
              className="input mono"
              value={form.external_group}
              onChange={(e) => setForm({ ...form, external_group: e.target.value })}
              maxLength={255}
              placeholder="GG-SD-Gerencia-Comercial"
            />
            <span className="field-hint">Opcional. También puede dar acceso manual a usuarios concretos.</span>
          </label>
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={mutation.busy || form.name.trim().length < 2}>
            Crear gerencia
          </button>
        </div>
      </form>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <ErrorAlert error={error ?? deps.error} onRetry={() => void (reload(), deps.reload())} />
        {loading && !data ? (
          <Loading />
        ) : gerencias.length === 0 ? (
          <EmptyState title="No hay gerencias">Cree la primera con el formulario de arriba.</EmptyState>
        ) : (
          <form onSubmit={saveEdit} className="table-wrap">
            <table className="table table-stack">
              <thead>
                <tr>
                  <th scope="col">Gerencia</th>
                  <th scope="col">Grupo del AD</th>
                  <th scope="col">Departamentos</th>
                  <th scope="col">Estado</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {gerencias.map((g) =>
                  editing?.id === g.id ? (
                    <tr key={g.id}>
                      <td className="cell-head">
                        <input
                          className="input input-sm"
                          value={editing.name}
                          onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                          required
                          minLength={2}
                          maxLength={150}
                          aria-label="Nombre de la gerencia"
                        />
                      </td>
                      <td data-label="Grupo del AD">
                        <input
                          className="input input-sm mono"
                          value={editing.external_group ?? ''}
                          onChange={(e) => setEditing({ ...editing, external_group: e.target.value })}
                          maxLength={255}
                          aria-label="Grupo del AD con acceso"
                        />
                      </td>
                      <td data-label="Departamentos">
                        <Tags items={g.department_ids.map(depName)} empty="Ninguno" />
                      </td>
                      <td data-label="Estado">
                        <span className={`badge ${g.active ? 'badge-success' : 'badge-neutral'}`}>
                          {g.active ? 'Activa' : 'Inactiva'}
                        </span>
                      </td>
                      <td className="text-right nowrap cell-actions">
                        <span className="row-actions">
                          <button type="submit" className="btn btn-primary btn-sm" disabled={mutation.busy}>
                            Guardar
                          </button>
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>
                            Cancelar
                          </button>
                        </span>
                      </td>
                    </tr>
                  ) : (
                    <tr key={g.id} style={g.active ? undefined : { opacity: 0.65 }}>
                      <td className="strong cell-head">{g.name}</td>
                      <td data-label="Grupo del AD">
                        {g.external_group ? (
                          <span className="mono text-xs">{g.external_group}</span>
                        ) : (
                          <span className="subtle">Sin grupo</span>
                        )}
                      </td>
                      <td data-label="Departamentos">
                        <Tags items={g.department_ids.map(depName)} empty="Ninguno" />
                      </td>
                      <td data-label="Estado">
                        <span className={`badge ${g.active ? 'badge-success' : 'badge-neutral'}`}>
                          {g.active ? 'Activa' : 'Inactiva'}
                        </span>
                      </td>
                      <td className="text-right cell-actions">
                        <span className="row-actions">
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => {
                              mutation.clear();
                              setEditing({ ...g });
                            }}
                          >
                            Editar
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm"
                            aria-expanded={viewersOf?.id === g.id}
                            onClick={() => setViewersOf(viewersOf?.id === g.id ? null : g)}
                          >
                            Acceso manual
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={mutation.busy}
                            onClick={() => void toggleActive(g)}
                          >
                            {g.active ? 'Desactivar' : 'Activar'}
                          </button>
                        </span>
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </form>
        )}
      </section>

      {viewersOf && <GerenciaViewersPanel key={viewersOf.id} gerencia={viewersOf} onClose={() => setViewersOf(null)} />}
    </div>
  );
}

function GerenciaViewersPanel({ gerencia, onClose }: { gerencia: Gerencia; onClose: () => void }) {
  const viewers = useApiResource<GerenciaViewer[]>(`/admin/gerencias/${gerencia.id}/viewers`);
  const mutation = useMutation();
  const [newUser, setNewUser] = useState<UserOut | null>(null);
  const list = viewers.data ?? [];

  async function save(userIds: number[], success: string) {
    const result = await mutation.run(() => catalogApi.setGerenciaViewers(gerencia.id, userIds), success);
    if (result) viewers.setData(result);
    return result;
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!newUser) return;
    if (list.some((v) => v.id === newUser.id)) {
      setNewUser(null);
      return;
    }
    const ok = await save([...list.map((v) => v.id), newUser.id], `${newUser.display_name} ahora ve los tickets de la gerencia.`);
    if (ok) setNewUser(null);
  }

  return (
    <section className="panel stack" aria-labelledby={`viewers-${gerencia.id}`}>
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <div>
          <h2 className="panel-title" id={`viewers-${gerencia.id}`}>
            Acceso manual a {gerencia.name}
          </h2>
          <p className="panel-subtitle">
            Además del grupo del AD{gerencia.external_group ? ` (${gerencia.external_group})` : ''}, estos usuarios ven en
            solo lectura los tickets solicitados por los departamentos de la gerencia.
          </p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Cerrar
        </button>
      </div>
      <Feedback error={mutation.error} notice={mutation.notice} />
      <ErrorAlert error={viewers.error} onRetry={() => void viewers.reload()} />
      <form className="form-grid" onSubmit={add}>
        <UserPicker value={newUser} onChange={setNewUser} label="Agregar usuario" />
        <div className="field" style={{ justifyContent: 'flex-end' }}>
          <button className="btn btn-primary" disabled={mutation.busy || !newUser}>
            Dar acceso
          </button>
        </div>
      </form>
      {viewers.loading && !viewers.data ? (
        <Loading />
      ) : list.length === 0 ? (
        <p className="muted text-sm">Sin usuarios con acceso manual.</p>
      ) : (
        <ul className={styles.viewerList}>
          {list.map((v) => (
            <li key={v.id} className={styles.viewerItem}>
              <span className={styles.viewerName}>
                <span className="strong">{v.display_name}</span>{' '}
                <span className="mono text-xs muted">{v.username}</span>
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={mutation.busy}
                onClick={() =>
                  void save(
                    list.filter((x) => x.id !== v.id).map((x) => x.id),
                    `Se retiró el acceso de ${v.display_name}.`,
                  )
                }
              >
                Quitar
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- Valores del AD

type AliasFilter = 'pending' | 'all' | 'assigned';

export function AliasesSection({ onPendingChange }: { onPendingChange?: (count: number) => void }) {
  const { data, error, loading, reload } = useApiResource<DepartmentAlias[]>('/admin/department-aliases');
  const deps = useDepartments();
  const activeDepartments = useMemo(() => deps.departments.filter((d) => d.active), [deps.departments]);
  const mutation = useMutation();
  const [filter, setFilter] = useState<AliasFilter>('all');
  const [choice, setChoice] = useState<Record<number, number | ''>>({});
  const [form, setForm] = useState<{ value: string; department_id: number | '' }>({ value: '', department_id: '' });

  const aliases = data ?? [];
  const pendingCount = aliases.filter((a) => a.pending).length;
  useEffect(() => {
    if (data) onPendingChange?.(pendingCount);
  }, [data, pendingCount, onPendingChange]);

  const visible = aliases.filter((a) => (filter === 'pending' ? a.pending : filter === 'assigned' ? !a.pending : true));

  async function assign(alias: DepartmentAlias) {
    const selected = choice[alias.id] ?? alias.department_id ?? '';
    if (selected === '') return;
    const department = deps.departments.find((d) => d.id === selected);
    const updated = await mutation.run(
      () => catalogApi.assignAlias(alias.id, selected),
      `«${alias.raw_value}» asignado a ${department?.name ?? 'el departamento'}. Se aplicó a los usuarios que ya lo traían y a sus tickets sin departamento solicitante.`,
    );
    if (updated) {
      setChoice((prev) => {
        const next = { ...prev };
        delete next[alias.id];
        return next;
      });
      void reload({ silent: true });
    }
  }

  async function remove(alias: DepartmentAlias) {
    if (!window.confirm(`¿Eliminar el valor «${alias.raw_value}»? Si vuelve a llegar en un inicio de sesión se registrará como pendiente.`)) return;
    const ok = await mutation.run(() => catalogApi.deleteAlias(alias.id).then(() => true), 'Valor eliminado.');
    if (ok) void reload({ silent: true });
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const value = form.value.trim();
    const created = await mutation.run(
      () => catalogApi.createAlias(value, form.department_id === '' ? null : form.department_id),
      `Valor «${value}» registrado.`,
    );
    if (created) {
      setForm({ value: '', department_id: '' });
      void reload({ silent: true });
    }
  }

  return (
    <div className="stack">
      <Alert tone="info" title="¿Qué son los valores del AD?">
        Al iniciar sesión, el portal lee el atributo «department» del usuario en el Active Directory (p. ej. «07
        COMERCIAL») y lo traduce a un departamento. Los valores nuevos sin equivalencia quedan <strong>pendientes</strong>{' '}
        hasta que los asigne aquí. Al asignarlo, los usuarios que ya traían ese valor toman el departamento de inmediato
        (y sus tickets creados mientras estaba pendiente); si el valor de un usuario cambia a uno pendiente, queda sin
        departamento hasta que lo asigne.
      </Alert>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <ErrorAlert error={error ?? deps.error} onRetry={() => void (reload(), deps.reload())} />
        <div className="toolbar" style={{ padding: 'var(--space-3) var(--space-4)' }}>
          <label className="field" style={{ maxWidth: 280 }}>
            <span className="field-label">Mostrar</span>
            <select className="select" value={filter} onChange={(e) => setFilter(e.target.value as AliasFilter)}>
              <option value="all">Todos ({aliases.length})</option>
              <option value="pending">Pendientes ({pendingCount})</option>
              <option value="assigned">Asignados ({aliases.length - pendingCount})</option>
            </select>
          </label>
          {pendingCount > 0 && (
            <span className="badge badge-warning" role="status">
              {pendingCount} pendiente{pendingCount === 1 ? '' : 's'} de asignar
            </span>
          )}
        </div>
        {loading && !data ? (
          <Loading />
        ) : visible.length === 0 ? (
          <EmptyState title={filter === 'pending' ? 'No hay valores pendientes' : 'No hay valores registrados'}>
            Se registran automáticamente cuando los usuarios inician sesión.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table table-stack">
              <thead>
                <tr>
                  <th scope="col">Valor del AD</th>
                  <th scope="col">Departamento</th>
                  <th scope="col">Registrado</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((a) => {
                  const selected = choice[a.id] ?? a.department_id ?? '';
                  const changed = selected !== '' && selected !== a.department_id;
                  return (
                    <tr key={a.id} className={a.pending ? styles.pendingRow : undefined}>
                      <td className="cell-head">
                        <div className="strong">{a.raw_value}</div>
                        <div className="mono text-xs subtle">{a.normalized_value}</div>
                        {a.pending && <span className="badge badge-warning">Pendiente</span>}
                      </td>
                      <td data-label="Departamento">
                        <div className={styles.assignControl}>
                          <select
                            className="select select-sm"
                            value={selected}
                            onChange={(e) =>
                              setChoice((prev) => ({ ...prev, [a.id]: e.target.value === '' ? '' : Number(e.target.value) }))
                            }
                            aria-label={`Departamento para «${a.raw_value}»`}
                          >
                            <option value="">Sin asignar</option>
                            {activeDepartments.map((d) => (
                              <option key={d.id} value={d.id}>
                                {d.name}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className={`btn btn-sm ${a.pending ? 'btn-primary' : ''}`}
                            disabled={mutation.busy || !changed}
                            onClick={() => void assign(a)}
                          >
                            Asignar
                          </button>
                        </div>
                      </td>
                      <td className="nowrap text-sm muted" data-label="Registrado">
                        {formatDateTime(a.created_at)}
                      </td>
                      <td className="text-right cell-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={mutation.busy}
                          onClick={() => void remove(a)}
                        >
                          Eliminar
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <form className="panel form" onSubmit={create}>
        <div>
          <h2 className="panel-title">Registrar un valor por adelantado</h2>
          <p className="panel-subtitle">
            Útil para preparar la equivalencia antes de que los usuarios inicien sesión. El valor se normaliza (mayúsculas,
            sin tildes ni prefijo numérico).
          </p>
        </div>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">
              Valor del atributo «department»<span className="required">*</span>
            </span>
            <input
              className="input"
              value={form.value}
              onChange={(e) => setForm({ ...form, value: e.target.value })}
              required
              maxLength={200}
              placeholder="07 Comercial"
            />
          </label>
          <label className="field">
            <span className="field-label">Departamento</span>
            <select
              className="select"
              value={form.department_id}
              onChange={(e) => setForm({ ...form, department_id: e.target.value === '' ? '' : Number(e.target.value) })}
            >
              <option value="">Dejar pendiente</option>
              {activeDepartments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={mutation.busy || !form.value.trim()}>
            Registrar valor
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------- Proveedores

interface ProviderForm {
  name: string;
  department_ids: number[];
}

export function ProvidersSection() {
  const { data, error, loading, reload } = useApiResource<Provider[]>('/admin/providers');
  const deps = useDepartments();
  const providers = useMemo(() => [...(data ?? [])].sort(byName), [data]);
  const mutation = useMutation();
  const [form, setForm] = useState<ProviderForm>({ name: '', department_ids: [] });
  const [editingId, setEditingId] = useState<number | null>(null);

  // Se ofrecen los departamentos de atención (y los ya elegidos aunque hayan cambiado).
  const options = useMemo(
    () =>
      deps.departments
        .filter((d) => (d.serves_tickets && d.active) || form.department_ids.includes(d.id))
        .map((d) => ({ value: d.id, label: d.name })),
    [deps.departments, form.department_ids],
  );
  const depName = (id: number) => deps.departments.find((d) => d.id === id)?.name ?? `#${id}`;

  function startEdit(provider: Provider) {
    mutation.clear();
    setEditingId(provider.id);
    setForm({ name: provider.name, department_ids: [...provider.department_ids] });
  }

  function cancelEdit() {
    setEditingId(null);
    setForm({ name: '', department_ids: [] });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = { name: form.name.trim(), department_ids: form.department_ids };
    const result = await mutation.run(
      () => (editingId ? catalogApi.updateProvider(editingId, body) : catalogApi.createProvider({ ...body, active: true })),
      editingId ? 'Proveedor actualizado.' : `Proveedor «${body.name}» creado.`,
    );
    if (result) {
      cancelEdit();
      void reload({ silent: true });
    }
  }

  async function toggleActive(provider: Provider) {
    const result = await mutation.run(
      () => catalogApi.updateProvider(provider.id, { active: !provider.active }),
      provider.active ? `Proveedor «${provider.name}» desactivado.` : `Proveedor «${provider.name}» activado.`,
    );
    if (result) void reload({ silent: true });
  }

  return (
    <div className="stack">
      <form className="panel form" onSubmit={submit}>
        <div>
          <h2 className="panel-title">{editingId ? `Editar proveedor «${form.name}»` : 'Nuevo proveedor'}</h2>
          <p className="panel-subtitle">
            Empresa externa que atiende tickets (p. ej. enlaces, mantenimiento). Los agentes lo indican en el ticket y se
            usa en los informes de proveedor.
          </p>
        </div>
        <label className="field" style={{ maxWidth: 480 }}>
          <span className="field-label">
            Nombre<span className="required">*</span>
          </span>
          <input
            className="input"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            minLength={2}
            maxLength={150}
          />
        </label>
        <CheckboxGroup
          legend="Departamentos donde aplica"
          hint="Sin selección = disponible en todos los departamentos."
          options={options}
          value={form.department_ids}
          onChange={(department_ids) => setForm({ ...form, department_ids })}
        />
        <div className="form-actions">
          {editingId && (
            <button type="button" className="btn btn-ghost" onClick={cancelEdit}>
              Cancelar
            </button>
          )}
          <button className="btn btn-primary" disabled={mutation.busy || form.name.trim().length < 2}>
            {editingId ? 'Guardar cambios' : 'Crear proveedor'}
          </button>
        </div>
      </form>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <ErrorAlert error={error ?? deps.error} onRetry={() => void (reload(), deps.reload())} />
        {loading && !data ? (
          <Loading />
        ) : providers.length === 0 ? (
          <EmptyState title="No hay proveedores registrados" />
        ) : (
          <div className="table-wrap">
            <table className="table table-stack">
              <thead>
                <tr>
                  <th scope="col">Proveedor</th>
                  <th scope="col">Aplica a</th>
                  <th scope="col">Estado</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {providers.map((p) => (
                  <tr key={p.id} style={p.active ? undefined : { opacity: 0.65 }}>
                    <td className="strong cell-head">{p.name}</td>
                    <td data-label="Aplica a">
                      <Tags items={p.department_ids.map(depName)} empty="Todos los departamentos" />
                    </td>
                    <td data-label="Estado">
                      <span className={`badge ${p.active ? 'badge-success' : 'badge-neutral'}`}>
                        {p.active ? 'Activo' : 'Inactivo'}
                      </span>
                    </td>
                    <td className="text-right cell-actions">
                      <span className="row-actions">
                        <button type="button" className="btn btn-sm" onClick={() => startEdit(p)}>
                          Editar
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          disabled={mutation.busy}
                          onClick={() => void toggleActive(p)}
                        >
                          {p.active ? 'Desactivar' : 'Activar'}
                        </button>
                      </span>
                    </td>
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

// ---------------------------------------------------------------- Usuarios (perfil: departamento y VIP)

export function UsersProfileSection() {
  const deps = useDepartments();
  const activeDepartments = useMemo(() => deps.departments.filter((d) => d.active), [deps.departments]);
  const [term, setTerm] = useState('');
  const debounced = useDebouncedValue(term.trim(), 350);
  const query = useMemo(() => ({ q: debounced || undefined, limit: 100, include_inactive: true }), [debounced]);
  const users = useApiResource<UserOut[]>('/users', query);
  const mutation = useMutation();
  const [editing, setEditing] = useState<{ id: number; department_id: number | ''; vip: boolean } | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const user = users.data?.find((u) => u.id === editing.id);
    const result = await mutation.run(
      () =>
        catalogApi.updateUserProfile(editing.id, {
          vip: editing.vip,
          department_id: editing.department_id === '' ? null : editing.department_id,
        }),
      `Perfil de ${user?.display_name ?? 'el usuario'} actualizado.`,
    );
    if (result) {
      users.setData((prev) => (prev ? prev.map((u) => (u.id === result.id ? result : u)) : prev));
      setEditing(null);
    }
  }

  const list = users.data ?? [];

  return (
    <div className="stack">
      <Alert tone="info">
        El departamento y la condición VIP se toman del Active Directory en cada inicio de sesión (atributo «department» y
        grupo VIP). Use el ajuste manual para usuarios sin esos datos; un inicio de sesión posterior con datos del AD lo
        vuelve a fijar.
      </Alert>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <div className="toolbar" style={{ padding: 'var(--space-3) var(--space-4)' }}>
          <label className="field field-search">
            <span className="field-label">Buscar usuario</span>
            <input
              className="input"
              type="search"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="Nombre, usuario o correo"
              maxLength={100}
            />
          </label>
        </div>
        <ErrorAlert error={users.error ?? deps.error} onRetry={() => void (users.reload(), deps.reload())} />
        {users.loading && !users.data ? (
          <Loading />
        ) : list.length === 0 ? (
          <EmptyState title="Sin resultados">Solo aparecen usuarios que ya iniciaron sesión al menos una vez.</EmptyState>
        ) : (
          <form onSubmit={save} className="table-wrap">
            <table className="table table-stack">
              <thead>
                <tr>
                  <th scope="col">Usuario</th>
                  <th scope="col">Roles</th>
                  <th scope="col">Departamento</th>
                  <th scope="col">VIP</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((u) =>
                  editing?.id === u.id ? (
                    <tr key={u.id}>
                      <td className="cell-head">
                        <div className="strong">{u.display_name}</div>
                        <div className="mono text-xs subtle">{u.username}</div>
                      </td>
                      <td data-label="Roles" className="text-sm">
                        {u.roles.map((r) => ROLE_LABELS[r] ?? r).join(', ') || '—'}
                      </td>
                      <td data-label="Departamento">
                        <select
                          className="select select-sm"
                          value={editing.department_id}
                          onChange={(e) =>
                            setEditing({ ...editing, department_id: e.target.value === '' ? '' : Number(e.target.value) })
                          }
                          aria-label={`Departamento de ${u.display_name}`}
                        >
                          <option value="">Sin departamento</option>
                          {activeDepartments.map((d) => (
                            <option key={d.id} value={d.id}>
                              {d.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td data-label="VIP">
                        <label className="checkbox">
                          <input
                            type="checkbox"
                            checked={editing.vip}
                            onChange={(e) => setEditing({ ...editing, vip: e.target.checked })}
                          />
                          VIP
                        </label>
                      </td>
                      <td className="text-right nowrap cell-actions">
                        <span className="row-actions">
                          <button type="submit" className="btn btn-primary btn-sm" disabled={mutation.busy}>
                            Guardar
                          </button>
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>
                            Cancelar
                          </button>
                        </span>
                      </td>
                    </tr>
                  ) : (
                    <tr key={u.id} style={u.active ? undefined : { opacity: 0.65 }}>
                      <td className="cell-head">
                        <div className="strong">{u.display_name}</div>
                        <div className="mono text-xs subtle">
                          {u.username}
                          {u.active ? '' : ' · inactivo'}
                        </div>
                      </td>
                      <td data-label="Roles" className="text-sm">
                        {u.roles.map((r) => ROLE_LABELS[r] ?? r).join(', ') || '—'}
                      </td>
                      <td data-label="Departamento">
                        {u.department?.name ?? <span className="subtle">Sin departamento</span>}
                      </td>
                      <td data-label="VIP">
                        {u.vip ? <span className="badge badge-warning">VIP</span> : <span className="subtle">No</span>}
                      </td>
                      <td className="text-right cell-actions">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            mutation.clear();
                            setEditing({ id: u.id, department_id: u.department?.id ?? '', vip: Boolean(u.vip) });
                          }}
                        >
                          Editar perfil
                        </button>
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </form>
        )}
      </section>
    </div>
  );
}
