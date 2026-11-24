'use client';

/**
 * Selector de identidad de laboratorio (NEXT_PUBLIC_AUTH_MODE=dev).
 * No hay contraseña: la API debe ejecutarse con AUTH_MODE=dev solo en un entorno aislado.
 */
import { useState, type FormEvent } from 'react';
import { DEV_GROUPS, DEV_PRESETS, useAuth, validateDevIdentity, type DevIdentity } from '@/lib/auth';
import { ROLE_LABELS } from '@/lib/format';
import type { Role } from '@/lib/types';
import { Alert } from './ui';

const SELECTABLE_ROLES: Role[] = ['admin', 'agent', 'approver'];

export default function DevLogin() {
  const { setDevIdentity, devIdentity, error } = useAuth();
  const initial = devIdentity ?? DEV_PRESETS[1];
  const [user, setUser] = useState(initial.user);
  const [name, setName] = useState(initial.name ?? '');
  const [roles, setRoles] = useState<Role[]>(initial.roles.filter((r) => r !== 'requester'));
  const [groups, setGroups] = useState<string[]>(initial.groups);
  const [extraGroups, setExtraGroups] = useState('');
  const [department, setDepartment] = useState(initial.department ?? '');
  const [formError, setFormError] = useState<string | null>(null);

  function applyPreset(preset: DevIdentity) {
    setUser(preset.user);
    setName(preset.name ?? '');
    setRoles(preset.roles.filter((r) => r !== 'requester'));
    setGroups(preset.groups);
    setExtraGroups('');
    setDepartment(preset.department ?? '');
    setFormError(null);
  }

  function toggle<T>(list: T[], value: T): T[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const extra = extraGroups
      .split(/[\n;]+/)
      .map((g) => g.trim())
      .filter(Boolean);
    const identity: DevIdentity = {
      user: user.trim(),
      name: name.trim() || undefined,
      roles: roles.includes('requester') ? roles : [...roles, 'requester'],
      groups: Array.from(new Set([...groups, ...extra])),
      department: department.trim() || undefined,
    };
    const problem = validateDevIdentity(identity);
    if (problem) {
      setFormError(problem);
      return;
    }
    setFormError(null);
    setDevIdentity(identity);
  }

  // Compara sin el rol implícito "requester" y sin importar el orden.
  const roleKey = (list: Role[]) => list.filter((r) => r !== 'requester').sort().join();
  const groupKey = (list: string[]) => [...list].sort().join();
  const selectedPreset = DEV_PRESETS.find(
    (p) => p.user === user && roleKey(p.roles) === roleKey(roles) && groupKey(p.groups) === groupKey(groups),
  );

  return (
    <div className="screen-center">
      <form className="panel screen-card stack" style={{ width: 'min(760px, 100%)' }} onSubmit={submit}>
        <div className="row">
          <span className="app-brand-mark" aria-hidden="true">
            ESD
          </span>
          <div>
            <h1 className="page-title" style={{ fontSize: 'var(--text-xl)' }}>
              Energuate Service Desk
            </h1>
            <p className="muted text-sm">Acceso de laboratorio</p>
          </div>
        </div>

        <Alert tone="warning" title="Modo de desarrollo">
          La identidad se elige sin contraseña y se envía como cabeceras X-Dev-*. Use este modo solo en un entorno
          aislado; nunca en producción.
        </Alert>

        {error && <Alert tone="error">{error}</Alert>}
        {formError && <Alert tone="error">{formError}</Alert>}

        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field-label" style={{ marginBottom: 8 }}>
            Usuarios de demostración
          </legend>
          <p className="field-hint">
            Coinciden con los datos de ejemplo del backend (<span className="mono">SEED_DEMO=true</span>). También
            puede definir una identidad propia más abajo.
          </p>
          <div className="choice-grid">
            {DEV_PRESETS.map((preset) => (
              <button
                key={preset.user}
                type="button"
                className={`choice${selectedPreset?.user === preset.user ? ' is-selected' : ''}`}
                onClick={() => applyPreset(preset)}
                aria-pressed={selectedPreset?.user === preset.user}
              >
                <div className="choice-title">{preset.label}</div>
                <div className="choice-sub">{preset.description}</div>
                <div className="choice-sub mono">{preset.user}</div>
              </button>
            ))}
          </div>
        </fieldset>

        <div className="form-grid">
          <label className="field">
            <span className="field-label">
              Usuario<span className="required">*</span>
            </span>
            <input
              className="input"
              value={user}
              onChange={(e) => setUser(e.target.value)}
              required
              maxLength={150}
              pattern="[A-Za-z0-9._@\-]+"
              autoComplete="off"
            />
            <span className="field-hint">Letras, números y . _ @ -</span>
          </label>
          <label className="field">
            <span className="field-label">Nombre a mostrar</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
          </label>
        </div>

        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field-label" style={{ marginBottom: 8 }}>
            Roles
          </legend>
          <div className="row">
            {SELECTABLE_ROLES.map((role) => (
              <label key={role} className="checkbox">
                <input type="checkbox" checked={roles.includes(role)} onChange={() => setRoles(toggle(roles, role))} />
                {ROLE_LABELS[role]}
              </label>
            ))}
            <span className="field-hint">Todo usuario es también Solicitante.</span>
          </div>
        </fieldset>

        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field-label" style={{ marginBottom: 8 }}>
            Grupos (equipos por área)
          </legend>
          <div className="grid-auto" style={{ gap: 8 }}>
            {DEV_GROUPS.map((group) => (
              <label key={group} className="checkbox">
                <input
                  type="checkbox"
                  checked={groups.includes(group)}
                  onChange={() => setGroups(toggle(groups, group))}
                />
                <span className="mono text-xs">{group}</span>
              </label>
            ))}
          </div>
          <label className="field">
            <span className="field-label">Otros grupos</span>
            <input
              className="input"
              value={extraGroups}
              onChange={(e) => setExtraGroups(e.target.value)}
              placeholder="Separados por punto y coma"
            />
          </label>
          <label className="field">
            <span className="field-label">Departamento AD (opcional)</span>
            <input
              className="input"
              value={department}
              maxLength={200}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder="p.ej. 07 COMERCIAL"
            />
            <span className="field-hint">Simula el atributo «department» del AD (cabecera X-Dev-Department).</span>
          </label>
        </fieldset>

        <div className="form-actions">
          <button type="submit" className="btn btn-primary btn-lg">
            Ingresar
          </button>
        </div>
      </form>
    </div>
  );
}
