'use client';

/**
 * Administración de políticas de aprobación (solo admin).
 *
 * - Transiciones del flujo con su indicador "requiere aprobación".
 * - Políticas: para una transición, filtros por tipo de ticket / departamento y etapas
 *   secuenciales de aprobadores (usuario, rol, equipo o líder del equipo; regla todos/cualquiera).
 */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { FlowIcon } from '@/components/notifications/FlowIcon';
import { Alert, EmptyState, ErrorAlert, Loading } from '@/components/ui';
import UserPicker from '@/components/users/UserPicker';
import { errorMessage } from '@/lib/api';
import {
  APPROVER_TYPE_LABELS,
  createPolicy,
  deletePolicy,
  listApprovalTransitions,
  listPolicies,
  RULE_LABELS,
  setTransitionApproval,
  updatePolicy,
  type ApprovalPolicy,
  type ApprovalPolicyInput,
  type ApproverType,
  type StepSpec,
  type TransitionApprovalInfo,
} from '@/lib/flows';
import { invalidateCatalog, useCatalog } from '@/lib/hooks';
import type { Catalog, UserOut } from '@/lib/types';
import styles from './approvals.module.css';

const ROLE_OPTIONS: { key: NonNullable<StepSpec['role']>; label: string }[] = [
  { key: 'approver', label: 'Aprobadores' },
  { key: 'agent', label: 'Agentes' },
  { key: 'admin', label: 'Administradores' },
];

const EMPTY_STEP: StepSpec = { name: '', approver_type: 'team_lead', scope: 'department', rule: 'any' };

function emptyPolicy(transitionId: number | null): ApprovalPolicyInput {
  return {
    name: '',
    active: true,
    transition_id: transitionId ?? 0,
    ticket_type_keys: [],
    department_ids: [],
    steps: [{ ...EMPTY_STEP, name: 'Líder del equipo' }],
    order: 100,
  };
}

function transitionLabel(t: { name: string; from_name: string | null; to_name: string | null }): string {
  return `${t.from_name ?? '?'} → ${t.to_name ?? '?'} (${t.name})`;
}

function placeholderUser(id: number): UserOut {
  return { id, username: '', display_name: `Usuario #${id}`, email: null, roles: [], active: true };
}

function StepEditor({
  step,
  index,
  total,
  catalog,
  onChange,
  onRemove,
  onMove,
}: {
  step: StepSpec;
  index: number;
  total: number;
  catalog: Catalog;
  onChange: (step: StepSpec) => void;
  onRemove: () => void;
  onMove: (delta: number) => void;
}) {
  const [user, setUser] = useState<UserOut | null>(step.user_id ? placeholderUser(step.user_id) : null);
  const set = (patch: Partial<StepSpec>) => onChange({ ...step, ...patch });

  return (
    <div className={styles.stepEditor}>
      <div className={styles.stepEditorHead}>
        <span className="strong text-sm">Etapa {index + 1}</span>
        <div className="row-sm">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onMove(-1)} disabled={index === 0} aria-label="Subir etapa">
            ↑
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onMove(1)} disabled={index === total - 1} aria-label="Bajar etapa">
            ↓
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onRemove} disabled={total === 1} aria-label="Quitar etapa">
            <FlowIcon name="trash" width={14} height={14} />
          </button>
        </div>
      </div>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">
            Nombre<span className="required">*</span>
          </span>
          <input className="input" value={step.name} maxLength={150} required onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">Aprobador</span>
          <select
            className="select"
            value={step.approver_type}
            onChange={(e) => {
              const type = e.target.value as ApproverType;
              set({
                approver_type: type,
                user_id: type === 'user' ? step.user_id ?? null : null,
                role: type === 'role' ? step.role ?? 'approver' : null,
                team_id: type === 'team' || type === 'team_lead' ? step.team_id ?? null : null,
              });
            }}
          >
            {(Object.keys(APPROVER_TYPE_LABELS) as ApproverType[]).map((key) => (
              <option key={key} value={key}>
                {APPROVER_TYPE_LABELS[key]}
              </option>
            ))}
          </select>
        </label>
        {step.approver_type === 'user' && (
          <div className="field-full">
            <UserPicker
              label="Usuario aprobador"
              required
              value={user}
              onChange={(u) => {
                setUser(u);
                set({ user_id: u?.id ?? null });
              }}
            />
          </div>
        )}
        {step.approver_type === 'role' && (
          <>
            <label className="field">
              <span className="field-label">Rol</span>
              <select className="select" value={step.role ?? 'approver'} onChange={(e) => set({ role: e.target.value as StepSpec['role'] })}>
                {ROLE_OPTIONS.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Alcance</span>
              <select className="select" value={step.scope ?? 'department'} onChange={(e) => set({ scope: e.target.value as 'department' | 'global' })}>
                <option value="department">Del departamento del ticket (si no hay, todos)</option>
                <option value="global">Todos los usuarios con el rol</option>
              </select>
            </label>
          </>
        )}
        {(step.approver_type === 'team' || step.approver_type === 'team_lead') && (
          <label className="field">
            <span className="field-label">Equipo</span>
            <select
              className="select"
              value={step.team_id ?? ''}
              required={step.approver_type === 'team'}
              onChange={(e) => set({ team_id: e.target.value ? Number(e.target.value) : null })}
            >
              {step.approver_type === 'team_lead' && <option value="">Equipo del ticket</option>}
              {step.approver_type === 'team' && <option value="">Seleccione…</option>}
              {catalog.teams.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span className="field-label">Regla</span>
          <select className="select" value={step.rule} onChange={(e) => set({ rule: e.target.value as StepSpec['rule'] })}>
            {(Object.keys(RULE_LABELS) as StepSpec['rule'][]).map((key) => (
              <option key={key} value={key}>
                {RULE_LABELS[key]}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}

function ChipSelect<T extends string | number>({
  options,
  selected,
  onChange,
  emptyLabel,
}: {
  options: { value: T; label: string }[];
  selected: T[];
  onChange: (values: T[]) => void;
  emptyLabel: string;
}) {
  return (
    <div className="stack-sm">
      <div className={styles.chips}>
        {options.map((option) => {
          const on = selected.includes(option.value);
          return (
            <label key={String(option.value)} className={`${styles.chip} ${on ? styles.chipOn : ''}`}>
              <input
                type="checkbox"
                checked={on}
                onChange={() => onChange(on ? selected.filter((v) => v !== option.value) : [...selected, option.value])}
              />
              {option.label}
            </label>
          );
        })}
      </div>
      <span className="field-hint">{selected.length === 0 ? emptyLabel : `${selected.length} seleccionado(s)`}</span>
    </div>
  );
}

function PolicyForm({
  initial,
  transitions,
  catalog,
  onCancel,
  onSaved,
}: {
  initial: ApprovalPolicy | null;
  transitions: TransitionApprovalInfo[];
  catalog: Catalog;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<ApprovalPolicyInput>(() =>
    initial
      ? {
          name: initial.name,
          active: initial.active,
          transition_id: initial.transition_id,
          ticket_type_keys: initial.ticket_type_keys,
          department_ids: initial.department_ids,
          steps: initial.steps,
          order: initial.order,
        }
      : emptyPolicy(transitions[0]?.id ?? null),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Claves estables para los editores de etapa (al reordenar conservan su estado).
  const [stepKeys, setStepKeys] = useState<number[]>(() => form.steps.map((_, i) => i));
  const [nextKey, setNextKey] = useState(form.steps.length);

  function updateStep(index: number, step: StepSpec) {
    setForm((f) => ({ ...f, steps: f.steps.map((s, i) => (i === index ? step : s)) }));
  }

  function moveStep(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= form.steps.length) return;
    const swap = <T,>(list: T[]) => {
      const copy = [...list];
      [copy[index], copy[target]] = [copy[target], copy[index]];
      return copy;
    };
    setForm((f) => ({ ...f, steps: swap(f.steps) }));
    setStepKeys((keys) => swap(keys));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.transition_id) {
      setError('Seleccione la transición.');
      return;
    }
    const invalid = form.steps.findIndex(
      (s) => !s.name.trim() || (s.approver_type === 'user' && !s.user_id) || (s.approver_type === 'team' && !s.team_id),
    );
    if (invalid >= 0) {
      setError(`Complete la etapa ${invalid + 1}: nombre y aprobador son obligatorios.`);
      return;
    }
    setSaving(true);
    try {
      const body = { ...form, name: form.name.trim(), steps: form.steps.map((s) => ({ ...s, name: s.name.trim() })) };
      if (initial) await updatePolicy(initial.id, body);
      else await createPolicy(body);
      invalidateCatalog();
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="panel form" onSubmit={(e) => void submit(e)} aria-label={initial ? 'Editar política' : 'Nueva política'}>
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h3 className="panel-title">{initial ? `Editar «${initial.name}»` : 'Nueva política de aprobación'}</h3>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="form-grid">
        <label className="field">
          <span className="field-label">
            Nombre<span className="required">*</span>
          </span>
          <input className="input" value={form.name} required maxLength={150} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">
            Transición<span className="required">*</span>
          </span>
          <select className="select" value={form.transition_id || ''} required onChange={(e) => setForm({ ...form, transition_id: Number(e.target.value) })}>
            <option value="">Seleccione…</option>
            {transitions.map((t) => (
              <option key={t.id} value={t.id}>
                {transitionLabel(t)}
              </option>
            ))}
          </select>
        </label>
        <div className="field field-full">
          <span className="field-label">Tipos de ticket</span>
          <ChipSelect
            options={catalog.ticket_types.map((t) => ({ value: t.key, label: t.name }))}
            selected={form.ticket_type_keys}
            onChange={(values) => setForm({ ...form, ticket_type_keys: values })}
            emptyLabel="Aplica a todos los tipos"
          />
        </div>
        <div className="field field-full">
          <span className="field-label">Departamentos</span>
          <ChipSelect
            options={catalog.departments.map((d) => ({ value: d.id, label: d.name }))}
            selected={form.department_ids}
            onChange={(values) => setForm({ ...form, department_ids: values })}
            emptyLabel="Aplica a todos los departamentos"
          />
        </div>
        <label className="field">
          <span className="field-label">Prioridad de evaluación</span>
          <input
            className="input"
            type="number"
            min={0}
            max={100000}
            value={form.order}
            onChange={(e) => setForm({ ...form, order: Number(e.target.value) || 0 })}
          />
          <span className="field-hint">Menor número = se evalúa primero. Se usa la primera política que coincide.</span>
        </label>
        <label className="checkbox" style={{ alignSelf: 'center' }}>
          <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
          Política activa
        </label>
      </div>

      <div className="stack-sm">
        <div className="row-between">
          <span className="field-label">Etapas (se aprueban en orden)</span>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={form.steps.length >= 10}
            onClick={() => {
              setForm((f) => ({ ...f, steps: [...f.steps, { ...EMPTY_STEP, name: `Etapa ${f.steps.length + 1}` }] }));
              setStepKeys((keys) => [...keys, nextKey]);
              setNextKey((k) => k + 1);
            }}
          >
            Agregar etapa
          </button>
        </div>
        {form.steps.map((step, index) => (
          <StepEditor
            key={stepKeys[index]}
            step={step}
            index={index}
            total={form.steps.length}
            catalog={catalog}
            onChange={(s) => updateStep(index, s)}
            onMove={(delta) => moveStep(index, delta)}
            onRemove={() => {
              setForm((f) => ({ ...f, steps: f.steps.filter((_, i) => i !== index) }));
              setStepKeys((keys) => keys.filter((_, i) => i !== index));
            }}
          />
        ))}
      </div>

      <div className="form-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={saving}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Guardando…' : 'Guardar política'}
        </button>
      </div>
    </form>
  );
}

export default function PoliciesSection() {
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const [policies, setPolicies] = useState<ApprovalPolicy[] | null>(null);
  const [transitions, setTransitions] = useState<TransitionApprovalInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ApprovalPolicy | 'new' | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, t] = await Promise.all([listPolicies(), listApprovalTransitions()]);
      setPolicies(p);
      setTransitions(t);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const typeNames = useMemo(() => new Map(catalog?.ticket_types.map((t) => [t.key, t.name]) ?? []), [catalog]);
  const departmentNames = useMemo(() => new Map(catalog?.departments.map((d) => [d.id, d.name]) ?? []), [catalog]);

  async function toggleTransition(t: TransitionApprovalInfo) {
    setBusyId(`t${t.id}`);
    setNotice(null);
    try {
      await setTransitionApproval(t.id, !t.requires_approval);
      invalidateCatalog();
      setNotice(`«${transitionLabel(t)}» ${t.requires_approval ? 'ya no requiere' : 'ahora requiere'} aprobación.`);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(policy: ApprovalPolicy) {
    if (!window.confirm(`¿Eliminar la política «${policy.name}»? Las solicitudes existentes no se modifican.`)) return;
    setBusyId(`p${policy.id}`);
    try {
      await deletePolicy(policy.id);
      setNotice(`Política «${policy.name}» eliminada.`);
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  }

  if (loading && !policies) return <Loading label="Cargando políticas…" />;

  return (
    <div className="stack-lg">
      <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} />
      <ErrorAlert error={error} onRetry={() => void load()} />
      {notice && <Alert tone="success">{notice}</Alert>}

      <Alert tone="info" title="Cómo se decide si un cambio requiere aprobación">
        Una transición exige aprobación solo si está marcada como «requiere aprobación». Si tiene políticas activas se usa la
        primera que coincide con el tipo y departamento del ticket (si ninguna coincide, no se pide aprobación); si no tiene
        políticas, la aprueba cualquier aprobador del departamento del ticket.
      </Alert>

      {editing && catalog ? (
        <PolicyForm
          initial={editing === 'new' ? null : editing}
          transitions={transitions}
          catalog={catalog}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setNotice('Política guardada.');
            setEditing(null);
            void load();
          }}
        />
      ) : (
        <section className="panel panel-flush">
          <div className="panel-header">
            <h3 className="panel-title">Políticas de aprobación</h3>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing('new')} disabled={!catalog}>
              Nueva política
            </button>
          </div>
          {policies && policies.length > 0 ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Política</th>
                    <th>Transición</th>
                    <th>Aplica a</th>
                    <th>Etapas</th>
                    <th>Estado</th>
                    <th aria-label="Acciones" />
                  </tr>
                </thead>
                <tbody>
                  {policies.map((policy) => (
                    <tr key={policy.id}>
                      <td className="strong">{policy.name}</td>
                      <td className="text-sm">{transitionLabel(policy.transition)}</td>
                      <td className="text-sm">
                        {policy.ticket_type_keys.length ? policy.ticket_type_keys.map((k) => typeNames.get(k) ?? k).join(', ') : 'Todos los tipos'}
                        <br />
                        <span className="subtle text-xs">
                          {policy.department_ids.length
                            ? policy.department_ids.map((id) => departmentNames.get(id) ?? `#${id}`).join(', ')
                            : 'Todos los departamentos'}
                        </span>
                      </td>
                      <td className="text-sm">
                        <ol style={{ margin: 0, paddingLeft: 18 }}>
                          {policy.steps.map((s, i) => (
                            <li key={i}>
                              {s.name} <span className="subtle">({APPROVER_TYPE_LABELS[s.approver_type]}, {RULE_LABELS[s.rule].toLowerCase()})</span>
                            </li>
                          ))}
                        </ol>
                      </td>
                      <td>
                        <span className={`badge ${policy.active ? 'badge-success' : 'badge-neutral'}`}>{policy.active ? 'Activa' : 'Inactiva'}</span>
                      </td>
                      <td className="nowrap">
                        <div className="btn-group">
                          <button type="button" className="btn btn-sm" onClick={() => setEditing(policy)} disabled={!catalog}>
                            Editar
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            onClick={() => void remove(policy)}
                            disabled={busyId === `p${policy.id}`}
                            aria-label={`Eliminar ${policy.name}`}
                          >
                            <FlowIcon name="trash" width={14} height={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="Sin políticas">Las transiciones marcadas usan la aprobación por defecto del departamento.</EmptyState>
          )}
        </section>
      )}

      <section className="panel panel-flush">
        <div className="panel-header">
          <h3 className="panel-title">Transiciones del flujo</h3>
          <span className="panel-subtitle">Active «requiere aprobación» para exigir aprobación en una transición.</span>
        </div>
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th>Transición</th>
                <th>Políticas activas</th>
                <th>Requiere aprobación</th>
              </tr>
            </thead>
            <tbody>
              {transitions.map((t) => (
                <tr key={t.id}>
                  <td>{transitionLabel(t)}</td>
                  <td className="col-num">{t.active_policies}</td>
                  <td>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={t.requires_approval}
                        disabled={busyId === `t${t.id}`}
                        onChange={() => void toggleTransition(t)}
                      />
                      {t.requires_approval ? 'Sí' : 'No'}
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
