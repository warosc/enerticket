'use client';

/**
 * Secciones de Administración: departamentos, equipos (con grupo AD/Keycloak), miembros y proyectos.
 * Todas usan los endpoints /admin/* (rol admin) e invalidan el catálogo compartido tras cada cambio.
 */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import UserPicker from '@/components/users/UserPicker';
import { Alert, EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { invalidateCatalog, useApiResource } from '@/lib/hooks';
import type {
  Department,
  DepartmentInput,
  Project,
  ProjectInput,
  Team,
  TeamInput,
  TeamMember,
  TeamMemberRole,
  UserOut,
} from '@/lib/types';

const MEMBER_ROLE_LABELS: Record<TeamMemberRole, string> = { member: 'Miembro', lead: 'Líder' };

function useMutation() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run<T>(action: () => Promise<T>, success: string): Promise<T | null> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      invalidateCatalog();
      setNotice(success);
      return result;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, notice, run, clear: () => (setError(null), setNotice(null)) };
}

function Feedback({ error, notice }: { error: string | null; notice: string | null }) {
  if (error) return <Alert tone="error">{error}</Alert>;
  if (notice) return <Alert tone="success">{notice}</Alert>;
  return null;
}

// ---------------------------------------------------------------- Departamentos

export function DepartmentsSection() {
  const { data, error, loading, reload } = useApiResource<Department[]>('/admin/departments');
  const departments = useMemo(() => [...(data ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'es')), [data]);
  const mutation = useMutation();
  const [name, setName] = useState('');
  const [parentId, setParentId] = useState<number | ''>('');
  const [editing, setEditing] = useState<Department | null>(null);

  const nameOf = (id: number | null) => departments.find((d) => d.id === id)?.name ?? '—';

  async function create(event: FormEvent) {
    event.preventDefault();
    const payload: DepartmentInput = { name: name.trim(), parent_id: parentId === '' ? null : parentId };
    const created = await mutation.run(
      () => apiFetch<Department>('/admin/departments', { method: 'POST', body: payload }),
      `Departamento «${payload.name}» creado.`,
    );
    if (created) {
      setName('');
      setParentId('');
      void reload({ silent: true });
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const payload: DepartmentInput = { name: editing.name.trim(), parent_id: editing.parent_id };
    const updated = await mutation.run(
      () => apiFetch<Department>(`/admin/departments/${editing.id}`, { method: 'PATCH', body: payload }),
      'Departamento actualizado.',
    );
    if (updated) {
      setEditing(null);
      void reload({ silent: true });
    }
  }

  return (
    <div className="stack">
      <form className="panel form" onSubmit={create}>
        <h2 className="panel-title">Nuevo departamento</h2>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">
              Nombre<span className="required">*</span>
            </span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={150} />
          </label>
          <label className="field">
            <span className="field-label">Departamento padre</span>
            <select
              className="select"
              value={parentId}
              onChange={(e) => setParentId(e.target.value === '' ? '' : Number(e.target.value))}
            >
              <option value="">Ninguno</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={mutation.busy || name.trim().length < 2}>
            Crear departamento
          </button>
        </div>
      </form>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <ErrorAlert error={error} onRetry={() => void reload()} />
        {loading && !data ? (
          <Loading />
        ) : departments.length === 0 ? (
          <EmptyState title="No hay departamentos" />
        ) : (
          <form onSubmit={saveEdit} className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Nombre</th>
                  <th scope="col">Padre</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {departments.map((d) =>
                  editing?.id === d.id ? (
                    <tr key={d.id}>
                      <td>
                        <input
                          className="input input-sm"
                          value={editing.name}
                          onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                          required
                          minLength={2}
                          maxLength={150}
                          aria-label="Nombre del departamento"
                        />
                      </td>
                      <td>
                        <select
                          className="select select-sm"
                          value={editing.parent_id ?? ''}
                          onChange={(e) =>
                            setEditing({ ...editing, parent_id: e.target.value === '' ? null : Number(e.target.value) })
                          }
                          aria-label="Departamento padre"
                        >
                          <option value="">Ninguno</option>
                          {departments
                            .filter((p) => p.id !== d.id)
                            .map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name}
                              </option>
                            ))}
                        </select>
                      </td>
                      <td className="text-right nowrap">
                        <button type="submit" className="btn btn-primary btn-sm" disabled={mutation.busy}>
                          Guardar
                        </button>{' '}
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>
                          Cancelar
                        </button>
                      </td>
                    </tr>
                  ) : (
                    <tr key={d.id}>
                      <td className="strong">{d.name}</td>
                      <td className="muted">{nameOf(d.parent_id)}</td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            mutation.clear();
                            setEditing({ ...d });
                          }}
                        >
                          Editar
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

// ---------------------------------------------------------------- Equipos

export function TeamsSection() {
  const teamsRes = useApiResource<Team[]>('/admin/teams');
  const depsRes = useApiResource<Department[]>('/admin/departments');
  const departments = useMemo(() => [...(depsRes.data ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'es')), [depsRes.data]);
  const teams = useMemo(() => {
    const depName = new Map(departments.map((d) => [d.id, d.name]));
    return [...(teamsRes.data ?? [])].sort(
      (a, b) =>
        (depName.get(a.department_id) ?? '').localeCompare(depName.get(b.department_id) ?? '', 'es') ||
        a.name.localeCompare(b.name, 'es'),
    );
  }, [teamsRes.data, departments]);
  const mutation = useMutation();
  const [form, setForm] = useState<{ name: string; department_id: number | ''; external_group: string }>({
    name: '',
    department_id: '',
    external_group: '',
  });
  const [editing, setEditing] = useState<Team | null>(null);

  const depName = (id: number) => departments.find((d) => d.id === id)?.name ?? `#${id}`;

  async function create(event: FormEvent) {
    event.preventDefault();
    if (form.department_id === '') return;
    const payload: TeamInput = {
      name: form.name.trim(),
      department_id: form.department_id,
      external_group: form.external_group.trim() || null,
    };
    const created = await mutation.run(
      () => apiFetch<Team>('/admin/teams', { method: 'POST', body: payload }),
      `Equipo «${payload.name}» creado.`,
    );
    if (created) {
      setForm({ name: '', department_id: form.department_id, external_group: '' });
      void teamsRes.reload({ silent: true });
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const payload: TeamInput = {
      name: editing.name.trim(),
      department_id: editing.department_id,
      external_group: editing.external_group?.trim() || null,
    };
    const updated = await mutation.run(
      () => apiFetch<Team>(`/admin/teams/${editing.id}`, { method: 'PATCH', body: payload }),
      'Equipo actualizado.',
    );
    if (updated) {
      setEditing(null);
      void teamsRes.reload({ silent: true });
    }
  }

  return (
    <div className="stack">
      <form className="panel form" onSubmit={create}>
        <div>
          <h2 className="panel-title">Nuevo equipo</h2>
          <p className="panel-subtitle">
            El grupo externo es el nombre del grupo de Active Directory / Keycloak cuyos miembros se asignan
            automáticamente al equipo al iniciar sesión.
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
            <span className="field-label">
              Departamento<span className="required">*</span>
            </span>
            <select
              className="select"
              value={form.department_id}
              onChange={(e) => setForm({ ...form, department_id: e.target.value === '' ? '' : Number(e.target.value) })}
              required
            >
              <option value="">Seleccione…</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field field-full">
            <span className="field-label">Grupo externo (AD/Keycloak)</span>
            <input
              className="input mono"
              value={form.external_group}
              onChange={(e) => setForm({ ...form, external_group: e.target.value })}
              maxLength={255}
              placeholder="GG-SD-Telecomunicaciones"
            />
            <span className="field-hint">Opcional. Sin grupo, los miembros se administran manualmente.</span>
          </label>
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={mutation.busy || form.department_id === '' || form.name.trim().length < 2}>
            Crear equipo
          </button>
        </div>
      </form>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <ErrorAlert error={teamsRes.error ?? depsRes.error} onRetry={() => void (teamsRes.reload(), depsRes.reload())} />
        {teamsRes.loading && !teamsRes.data ? (
          <Loading />
        ) : teams.length === 0 ? (
          <EmptyState title="No hay equipos" />
        ) : (
          <form onSubmit={saveEdit} className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Equipo</th>
                  <th scope="col">Departamento</th>
                  <th scope="col">Grupo externo</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {teams.map((t) =>
                  editing?.id === t.id ? (
                    <tr key={t.id}>
                      <td>
                        <input
                          className="input input-sm"
                          value={editing.name}
                          onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                          required
                          minLength={2}
                          maxLength={150}
                          aria-label="Nombre del equipo"
                        />
                      </td>
                      <td>
                        <select
                          className="select select-sm"
                          value={editing.department_id}
                          onChange={(e) => setEditing({ ...editing, department_id: Number(e.target.value) })}
                          aria-label="Departamento"
                        >
                          {departments.map((d) => (
                            <option key={d.id} value={d.id}>
                              {d.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          className="input input-sm mono"
                          value={editing.external_group ?? ''}
                          onChange={(e) => setEditing({ ...editing, external_group: e.target.value })}
                          maxLength={255}
                          aria-label="Grupo externo"
                        />
                      </td>
                      <td className="text-right nowrap">
                        <button type="submit" className="btn btn-primary btn-sm" disabled={mutation.busy}>
                          Guardar
                        </button>{' '}
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>
                          Cancelar
                        </button>
                      </td>
                    </tr>
                  ) : (
                    <tr key={t.id}>
                      <td className="strong">{t.name}</td>
                      <td>{depName(t.department_id)}</td>
                      <td>
                        {t.external_group ? (
                          <span className="mono text-xs">{t.external_group}</span>
                        ) : (
                          <span className="subtle">Manual</span>
                        )}
                      </td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            mutation.clear();
                            setEditing({ ...t });
                          }}
                        >
                          Editar
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

// ---------------------------------------------------------------- Miembros

export function MembersSection() {
  const teamsRes = useApiResource<Team[]>('/admin/teams');
  const teams = useMemo(() => [...(teamsRes.data ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'es')), [teamsRes.data]);
  const [teamId, setTeamId] = useState<number | null>(null);
  const membersRes = useApiResource<TeamMember[]>(teamId ? `/admin/teams/${teamId}/members` : null);
  const mutation = useMutation();
  const [newUser, setNewUser] = useState<UserOut | null>(null);
  const [newRole, setNewRole] = useState<TeamMemberRole>('member');

  useEffect(() => {
    if (teamId === null && teams.length > 0) setTeamId(teams[0].id);
  }, [teamId, teams]);

  const team = teams.find((t) => t.id === teamId) ?? null;

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!teamId || !newUser) return;
    const created = await mutation.run(
      () =>
        apiFetch<TeamMember>(`/admin/teams/${teamId}/members`, {
          method: 'POST',
          body: { user_id: newUser.id, role: newRole },
        }),
      `${newUser.display_name} agregado al equipo.`,
    );
    if (created) {
      setNewUser(null);
      setNewRole('member');
      void membersRes.reload({ silent: true });
    }
  }

  async function changeRole(member: TeamMember, role: TeamMemberRole) {
    const updated = await mutation.run(
      () => apiFetch<TeamMember>(`/admin/teams/${member.team_id}/members/${member.user_id}`, { method: 'PATCH', body: { role } }),
      `Rol de ${member.display_name} actualizado.`,
    );
    if (updated) void membersRes.reload({ silent: true });
  }

  async function remove(member: TeamMember) {
    if (!window.confirm(`¿Quitar a ${member.display_name} del equipo?`)) return;
    const done = await mutation.run(
      () => apiFetch<void>(`/admin/teams/${member.team_id}/members/${member.user_id}`, { method: 'DELETE' }),
      `${member.display_name} fue retirado del equipo.`,
    );
    if (done !== null) void membersRes.reload({ silent: true });
  }

  return (
    <div className="stack">
      <section className="panel stack">
        <ErrorAlert error={teamsRes.error} onRetry={() => void teamsRes.reload()} />
        <label className="field" style={{ maxWidth: 420 }}>
          <span className="field-label">Equipo</span>
          <select
            className="select"
            value={teamId ?? ''}
            onChange={(e) => {
              mutation.clear();
              setTeamId(e.target.value === '' ? null : Number(e.target.value));
            }}
          >
            {teams.length === 0 && <option value="">No hay equipos</option>}
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        {team?.external_group && (
          <Alert tone="info">
            Este equipo está vinculado al grupo <span className="mono">{team.external_group}</span>. Los miembros se
            sincronizan al iniciar sesión según los grupos del directorio; los cambios manuales pueden revertirse en el
            próximo inicio de sesión del usuario.
          </Alert>
        )}
      </section>

      {team && (
        <form className="panel form" onSubmit={add}>
          <h2 className="panel-title">Agregar miembro a {team.name}</h2>
          <div className="form-grid">
            <UserPicker value={newUser} onChange={setNewUser} required />
            <label className="field">
              <span className="field-label">Rol en el equipo</span>
              <select className="select" value={newRole} onChange={(e) => setNewRole(e.target.value as TeamMemberRole)}>
                <option value="member">Miembro</option>
                <option value="lead">Líder</option>
              </select>
            </label>
          </div>
          <div className="form-actions">
            <button className="btn btn-primary" disabled={mutation.busy || !newUser}>
              Agregar
            </button>
          </div>
        </form>
      )}

      <Feedback error={mutation.error} notice={mutation.notice} />

      {team && (
        <section className="panel panel-flush">
          <ErrorAlert error={membersRes.error} onRetry={() => void membersRes.reload()} />
          {membersRes.loading && !membersRes.data ? (
            <Loading />
          ) : (membersRes.data ?? []).length === 0 ? (
            <EmptyState title="El equipo no tiene miembros" />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Nombre</th>
                    <th scope="col">Usuario</th>
                    <th scope="col">Correo</th>
                    <th scope="col">Rol</th>
                    <th scope="col" className="text-right">
                      Acciones
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(membersRes.data ?? []).map((m) => (
                    <tr key={m.user_id}>
                      <td className="strong">{m.display_name}</td>
                      <td className="mono text-xs">{m.username}</td>
                      <td className="muted">{m.email ?? '—'}</td>
                      <td>
                        <select
                          className="select select-sm"
                          style={{ width: 'auto' }}
                          value={m.role}
                          disabled={mutation.busy}
                          onChange={(e) => void changeRole(m, e.target.value as TeamMemberRole)}
                          aria-label={`Rol de ${m.display_name}`}
                        >
                          {(Object.keys(MEMBER_ROLE_LABELS) as TeamMemberRole[]).map((r) => (
                            <option key={r} value={r}>
                              {MEMBER_ROLE_LABELS[r]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          disabled={mutation.busy}
                          onClick={() => void remove(m)}
                        >
                          Quitar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Proyectos

interface ProjectForm {
  code: string;
  name: string;
  team_id: number | '';
  owner: UserOut | null;
}

const EMPTY_PROJECT: ProjectForm = { code: '', name: '', team_id: '', owner: null };
const PROJECT_CODE_RE = /^[A-Za-z0-9_-]{2,30}$/;

export function ProjectsSection() {
  const projectsRes = useApiResource<Project[]>('/admin/projects');
  const teamsRes = useApiResource<Team[]>('/admin/teams');
  const teams = useMemo(() => [...(teamsRes.data ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'es')), [teamsRes.data]);
  const projects = useMemo(() => [...(projectsRes.data ?? [])].sort((a, b) => a.code.localeCompare(b.code, 'es')), [projectsRes.data]);
  const mutation = useMutation();
  const [form, setForm] = useState<ProjectForm>(EMPTY_PROJECT);
  const [editingId, setEditingId] = useState<number | null>(null);

  const teamName = (id: number | null) => (id === null ? '—' : teams.find((t) => t.id === id)?.name ?? `#${id}`);

  function startEdit(project: Project) {
    mutation.clear();
    setEditingId(project.id);
    setForm({
      code: project.code,
      name: project.name,
      team_id: project.team_id ?? '',
      owner: project.owner_id
        ? { id: project.owner_id, display_name: `Usuario #${project.owner_id}`, username: '', email: null, roles: [], active: true }
        : null,
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(EMPTY_PROJECT);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!PROJECT_CODE_RE.test(form.code.trim())) return;
    const payload: ProjectInput = {
      code: form.code.trim(),
      name: form.name.trim(),
      team_id: form.team_id === '' ? null : form.team_id,
      owner_id: form.owner?.id ?? null,
    };
    const result = await mutation.run(
      () =>
        editingId
          ? apiFetch<Project>(`/admin/projects/${editingId}`, { method: 'PATCH', body: payload })
          : apiFetch<Project>('/admin/projects', { method: 'POST', body: payload }),
      editingId ? 'Proyecto actualizado.' : `Proyecto ${payload.code} creado.`,
    );
    if (result) {
      cancelEdit();
      void projectsRes.reload({ silent: true });
    }
  }

  const codeValid = PROJECT_CODE_RE.test(form.code.trim());

  return (
    <div className="stack">
      <form className="panel form" onSubmit={submit}>
        <h2 className="panel-title">{editingId ? `Editar proyecto ${form.code}` : 'Nuevo proyecto'}</h2>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">
              Código<span className="required">*</span>
            </span>
            <input
              className="input mono"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              required
              maxLength={30}
              placeholder="TI-2026-01"
              aria-invalid={form.code !== '' && !codeValid}
            />
            <span className={form.code !== '' && !codeValid ? 'field-error' : 'field-hint'}>
              2 a 30 caracteres: letras, números, guion o guion bajo.
            </span>
          </label>
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
              maxLength={200}
            />
          </label>
          <label className="field">
            <span className="field-label">Equipo responsable</span>
            <select
              className="select"
              value={form.team_id}
              onChange={(e) => setForm({ ...form, team_id: e.target.value === '' ? '' : Number(e.target.value) })}
            >
              <option value="">Sin equipo</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <UserPicker label="Responsable" value={form.owner} onChange={(owner) => setForm({ ...form, owner })} />
        </div>
        <div className="form-actions">
          {editingId && (
            <button type="button" className="btn btn-ghost" onClick={cancelEdit}>
              Cancelar
            </button>
          )}
          <button className="btn btn-primary" disabled={mutation.busy || !codeValid || form.name.trim().length < 2}>
            {editingId ? 'Guardar cambios' : 'Crear proyecto'}
          </button>
        </div>
      </form>

      <Feedback error={mutation.error} notice={mutation.notice} />

      <section className="panel panel-flush">
        <ErrorAlert error={projectsRes.error} onRetry={() => void projectsRes.reload()} />
        {projectsRes.loading && !projectsRes.data ? (
          <Loading />
        ) : projects.length === 0 ? (
          <EmptyState title="No hay proyectos registrados" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Código</th>
                  <th scope="col">Nombre</th>
                  <th scope="col">Equipo</th>
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {projects.map((p) => (
                  <tr key={p.id}>
                    <td className="col-key">{p.code}</td>
                    <td className="strong">{p.name}</td>
                    <td>{teamName(p.team_id)}</td>
                    <td className="text-right">
                      <button type="button" className="btn btn-sm" onClick={() => startEdit(p)}>
                        Editar
                      </button>
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
