'use client';

/**
 * Constructores del editor de reglas: condiciones (campo / operador / valor) y acciones.
 */
import { useState } from 'react';
import { FlowIcon } from '@/components/notifications/FlowIcon';
import UserPicker from '@/components/users/UserPicker';
import { CATEGORY_LABELS } from '@/lib/format';
import type {
  ActionType,
  AutomationMeta,
  ConditionOperator,
  FieldKind,
  RuleAction,
  RuleCondition,
  RuleConditions,
} from '@/lib/flows';
import type { Catalog, StateCategory, UserOut } from '@/lib/types';
import styles from './automations.module.css';

const NO_VALUE_OPS: ConditionOperator[] = ['is_empty', 'is_not_empty'];
const LIST_OPS: ConditionOperator[] = ['in', 'not_in'];
const TEXT_OPS: ConditionOperator[] = ['contains', 'not_contains', 'starts_with'];
const NUMBER_OPS: ConditionOperator[] = ['gt', 'gte', 'lt', 'lte'];

/** Operadores que tienen sentido para cada tipo de campo. */
function operatorsFor(kind: FieldKind, all: AutomationMeta['operators']) {
  const allowed = new Set<ConditionOperator>(['eq', 'neq', ...LIST_OPS, ...NO_VALUE_OPS]);
  if (kind === 'text') TEXT_OPS.forEach((op) => allowed.add(op));
  if (kind === 'number' || kind === 'user') NUMBER_OPS.forEach((op) => allowed.add(op));
  return all.filter((op) => allowed.has(op.key));
}

function optionsFor(kind: FieldKind, catalog: Catalog): { value: string; label: string }[] | null {
  switch (kind) {
    case 'priority':
      return catalog.priorities.map((p) => ({ value: p.key, label: p.name }));
    case 'state':
      return catalog.workflow.states.map((s) => ({ value: s.key, label: s.name }));
    case 'category':
      return (Object.keys(CATEGORY_LABELS) as StateCategory[]).map((c) => ({ value: c, label: CATEGORY_LABELS[c] }));
    case 'ticket_type':
      return catalog.ticket_types.map((t) => ({ value: t.key, label: t.name }));
    case 'department':
      return catalog.departments.map((d) => ({ value: String(d.id), label: d.name }));
    case 'team':
      return catalog.teams.map((t) => ({ value: String(t.id), label: t.name }));
    default:
      return null;
  }
}

function valueToText(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ');
  if (value === null || value === undefined) return '';
  return String(value);
}

function ConditionValue({
  condition,
  kind,
  catalog,
  onChange,
}: {
  condition: RuleCondition;
  kind: FieldKind;
  catalog: Catalog;
  onChange: (value: unknown) => void;
}) {
  if (NO_VALUE_OPS.includes(condition.op)) {
    return <span className="text-xs subtle" style={{ alignSelf: 'center' }}>Sin valor</span>;
  }
  const options = optionsFor(kind, catalog);
  const numeric = kind === 'number' || kind === 'department' || kind === 'team' || kind === 'user';
  if (LIST_OPS.includes(condition.op)) {
    if (options) {
      const selected = (Array.isArray(condition.value) ? condition.value : []).map(String);
      return (
        <select
          className="select"
          multiple
          size={Math.min(5, options.length)}
          aria-label="Valores"
          value={selected}
          onChange={(e) => {
            const values = Array.from(e.target.selectedOptions).map((o) => o.value);
            onChange(numeric ? values.map(Number) : values);
          }}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        className="input"
        aria-label="Valores separados por coma"
        placeholder="valor1, valor2"
        value={valueToText(condition.value)}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(',')
              .map((v) => v.trim())
              .filter(Boolean),
          )
        }
      />
    );
  }
  if (options) {
    return (
      <select
        className="select"
        aria-label="Valor"
        value={valueToText(condition.value)}
        onChange={(e) => onChange(numeric ? Number(e.target.value) : e.target.value)}
      >
        <option value="">Seleccione…</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      className="input"
      aria-label="Valor"
      type={numeric ? 'number' : 'text'}
      maxLength={500}
      value={valueToText(condition.value)}
      onChange={(e) => onChange(numeric ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value)}
    />
  );
}

export function ConditionBuilder({
  value,
  onChange,
  meta,
  catalog,
}: {
  value: RuleConditions;
  onChange: (value: RuleConditions) => void;
  meta: AutomationMeta;
  catalog: Catalog;
}) {
  const fieldKind = (field: string): FieldKind => meta.fields.find((f) => f.key === field)?.kind ?? 'text';
  const update = (index: number, patch: Partial<RuleCondition>) =>
    onChange({ ...value, rules: value.rules.map((r, i) => (i === index ? { ...r, ...patch } : r)) });

  return (
    <div className={styles.builder}>
      <div className="row-between">
        <div className="row-sm">
          <span className="text-sm">Se cumple si coinciden</span>
          <div className={styles.matchToggle} role="radiogroup" aria-label="Modo de coincidencia">
            {(['all', 'any'] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={value.match === m}
                className={`${styles.matchOption} ${value.match === m ? styles.matchOptionActive : ''}`}
                onClick={() => onChange({ ...value, match: m })}
              >
                {m === 'all' ? 'todas' : 'alguna'}
              </button>
            ))}
          </div>
        </div>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={value.rules.length >= 20}
          onClick={() => onChange({ ...value, rules: [...value.rules, { field: 'ticket.priority', op: 'eq', value: '' }] })}
        >
          Agregar condición
        </button>
      </div>
      {value.rules.length === 0 && (
        <p className="text-sm muted">Sin condiciones: la regla se ejecuta en todos los eventos del disparador.</p>
      )}
      {value.rules.map((condition, index) => {
        const kind = fieldKind(condition.field);
        const ops = operatorsFor(kind, meta.operators);
        return (
          <div className={styles.row} key={index}>
            <select
              className="select"
              aria-label="Campo"
              value={condition.field}
              onChange={(e) => {
                const nextKind = fieldKind(e.target.value);
                const nextOps = operatorsFor(nextKind, meta.operators).map((o) => o.key);
                update(index, { field: e.target.value, op: nextOps.includes(condition.op) ? condition.op : 'eq', value: '' });
              }}
            >
              {meta.fields.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}
                </option>
              ))}
              {!meta.fields.some((f) => f.key === condition.field) && <option value={condition.field}>{condition.field}</option>}
            </select>
            <select
              className="select"
              aria-label="Operador"
              value={condition.op}
              onChange={(e) => {
                const op = e.target.value as ConditionOperator;
                const wasList = LIST_OPS.includes(condition.op);
                const isList = LIST_OPS.includes(op);
                update(index, { op, value: wasList === isList ? condition.value : isList ? [] : '' });
              }}
            >
              {ops.map((op) => (
                <option key={op.key} value={op.key}>
                  {op.label}
                </option>
              ))}
            </select>
            <ConditionValue condition={condition} kind={kind} catalog={catalog} onChange={(v) => update(index, { value: v })} />
            <button
              type="button"
              className="btn btn-ghost btn-icon"
              aria-label={`Quitar condición ${index + 1}`}
              onClick={() => onChange({ ...value, rules: value.rules.filter((_, i) => i !== index) })}
            >
              <FlowIcon name="trash" width={16} height={16} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function defaultAction(type: ActionType): RuleAction {
  switch (type) {
    case 'add_comment':
      return { type, body: '', visibility: 'internal' };
    case 'notify':
      return { type, targets: ['assignee'], title: '{{ticket.key}}: {{ticket.title}}', body: '' };
    default:
      return { type };
  }
}

function ActionEditor({
  action,
  index,
  meta,
  catalog,
  onChange,
  onRemove,
}: {
  action: RuleAction;
  index: number;
  meta: AutomationMeta;
  catalog: Catalog;
  onChange: (action: RuleAction) => void;
  onRemove: () => void;
}) {
  const [user, setUser] = useState<UserOut | null>(
    action.user_id
      ? { id: action.user_id, username: '', display_name: `Usuario #${action.user_id}`, email: null, roles: [], active: true }
      : null,
  );
  const set = (patch: Partial<RuleAction>) => onChange({ ...action, ...patch });
  const targets = action.targets ?? [];

  return (
    <div className={styles.action}>
      <div className={styles.actionHead}>
        <span className={styles.actionIndex}>{index + 1}</span>
        <select
          className="select"
          style={{ width: 'auto', minWidth: 200 }}
          aria-label="Tipo de acción"
          value={action.type}
          onChange={(e) => onChange(defaultAction(e.target.value as ActionType))}
        >
          {meta.actions.map((a) => (
            <option key={a.key} value={a.key}>
              {a.label}
            </option>
          ))}
        </select>
        <span className="spacer" />
        <button type="button" className="btn btn-ghost btn-icon" aria-label={`Quitar acción ${index + 1}`} onClick={onRemove}>
          <FlowIcon name="trash" width={16} height={16} />
        </button>
      </div>

      {action.type === 'assign_user' && (
        <UserPicker
          label="Agente"
          role="agent"
          required
          value={user}
          onChange={(u) => {
            setUser(u);
            set({ user_id: u?.id });
          }}
        />
      )}
      {action.type === 'assign_team' && (
        <label className="field">
          <span className="field-label">Equipo</span>
          <select className="select" value={action.team_id ?? ''} required onChange={(e) => set({ team_id: Number(e.target.value) || undefined })}>
            <option value="">Seleccione…</option>
            {catalog.teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <span className="field-hint">Si el equipo es de otro departamento, el ticket se mueve a ese departamento.</span>
        </label>
      )}
      {action.type === 'set_priority' && (
        <label className="field">
          <span className="field-label">Prioridad</span>
          <select className="select" value={action.priority_key ?? ''} required onChange={(e) => set({ priority_key: e.target.value })}>
            <option value="">Seleccione…</option>
            {catalog.priorities.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {(action.type === 'transition' || action.type === 'request_approval') && (
        <label className="field">
          <span className="field-label">Estado destino</span>
          <select className="select" value={action.to_state ?? ''} required onChange={(e) => set({ to_state: e.target.value })}>
            <option value="">Seleccione…</option>
            {catalog.workflow.states.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
          <span className="field-hint">
            {action.type === 'transition'
              ? 'Si la transición requiere aprobación, se crea la solicitud en lugar de cambiar el estado.'
              : 'Crea una solicitud de aprobación; al aprobarse se aplica el cambio de estado.'}
          </span>
        </label>
      )}
      {action.type === 'add_comment' && (
        <>
          <label className="field">
            <span className="field-label">Comentario</span>
            <textarea className="textarea" value={action.body ?? ''} maxLength={4000} required onChange={(e) => set({ body: e.target.value })} />
          </label>
          <label className="field" style={{ maxWidth: 260 }}>
            <span className="field-label">Visibilidad</span>
            <select className="select" value={action.visibility ?? 'internal'} onChange={(e) => set({ visibility: e.target.value as 'public' | 'internal' })}>
              <option value="internal">Interno (solo agentes)</option>
              <option value="public">Público (visible al solicitante)</option>
            </select>
          </label>
        </>
      )}
      {action.type === 'notify' && (
        <>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="field-label">Destinatarios</legend>
            <div className="row-sm">
              {meta.notify_targets.map((t) => (
                <label key={t.key} className="checkbox">
                  <input
                    type="checkbox"
                    checked={targets.includes(t.key)}
                    onChange={() => set({ targets: targets.includes(t.key) ? targets.filter((x) => x !== t.key) : [...targets, t.key] })}
                  />
                  {t.label}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="field">
            <span className="field-label">Título</span>
            <input className="input" value={action.title ?? ''} maxLength={250} required onChange={(e) => set({ title: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Mensaje</span>
            <textarea className="textarea" style={{ minHeight: 64 }} value={action.body ?? ''} maxLength={4000} onChange={(e) => set({ body: e.target.value })} />
          </label>
        </>
      )}
    </div>
  );
}

export function ActionBuilder({
  value,
  onChange,
  meta,
  catalog,
}: {
  value: RuleAction[];
  onChange: (value: RuleAction[]) => void;
  meta: AutomationMeta;
  catalog: Catalog;
}) {
  const [keys, setKeys] = useState<number[]>(() => value.map((_, i) => i));
  const [nextKey, setNextKey] = useState(value.length);
  return (
    <div className={styles.builder}>
      {value.map((action, index) => (
        <ActionEditor
          key={keys[index] ?? index}
          action={action}
          index={index}
          meta={meta}
          catalog={catalog}
          onChange={(a) => onChange(value.map((x, i) => (i === index ? a : x)))}
          onRemove={() => {
            onChange(value.filter((_, i) => i !== index));
            setKeys((k) => k.filter((_, i) => i !== index));
          }}
        />
      ))}
      <div>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={value.length >= 10}
          onClick={() => {
            onChange([...value, defaultAction('set_priority')]);
            setKeys((k) => [...k, nextKey]);
            setNextKey((n) => n + 1);
          }}
        >
          Agregar acción
        </button>
      </div>
      <p className="field-hint">Plantillas disponibles en textos: {meta.template_help}</p>
    </div>
  );
}

/** Resumen legible de una condición (lista de reglas). */
export function describeCondition(condition: RuleCondition, meta: AutomationMeta | null, catalog: Catalog | null): string {
  const field = meta?.fields.find((f) => f.key === condition.field);
  const op = meta?.operators.find((o) => o.key === condition.op)?.label ?? condition.op;
  if (NO_VALUE_OPS.includes(condition.op)) return `${field?.label ?? condition.field} ${op}`;
  const options = field && catalog ? optionsFor(field.kind, catalog) : null;
  const render = (v: unknown) => options?.find((o) => o.value === String(v))?.label ?? String(v);
  const value = Array.isArray(condition.value) ? condition.value.map(render).join(', ') : render(condition.value);
  return `${field?.label ?? condition.field} ${op} «${value}»`;
}
