'use client';

/**
 * Editor de una regla de automatización con simulación (dry-run) contra un ticket real.
 */
import { useState, type FormEvent } from 'react';
import { ActionBuilder, ConditionBuilder, describeCondition } from '@/components/automations/RuleBuilders';
import { FlowIcon } from '@/components/notifications/FlowIcon';
import { Alert } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import {
  createRule,
  dryRunRule,
  updateRule,
  type AutomationMeta,
  type AutomationRule,
  type AutomationRuleInput,
  type ConditionOperator,
  type DryRunResult,
  type RuleAction,
} from '@/lib/flows';
import type { Catalog, TicketPage } from '@/lib/types';
import styles from './automations.module.css';

export function emptyRule(meta: AutomationMeta): AutomationRuleInput {
  return {
    name: '',
    description: '',
    active: true,
    trigger: meta.triggers[0]?.key ?? 'ticket.created',
    conditions: { match: 'all', rules: [] },
    actions: [{ type: 'set_priority', priority_key: 'high' }],
    order: 100,
    stop_processing: false,
  };
}

function validate(rule: AutomationRuleInput): string | null {
  if (!rule.name.trim()) return 'El nombre es obligatorio.';
  if (rule.actions.length === 0) return 'Agregue al menos una acción.';
  for (const [i, c] of rule.conditions.rules.entries()) {
    const needsValue = c.op !== 'is_empty' && c.op !== 'is_not_empty';
    const empty = c.value === undefined || c.value === null || c.value === '' || (Array.isArray(c.value) && c.value.length === 0);
    if (needsValue && empty) return `La condición ${i + 1} necesita un valor.`;
  }
  const required: Record<RuleAction['type'], (a: RuleAction) => boolean> = {
    assign_user: (a) => Boolean(a.user_id),
    assign_team: (a) => Boolean(a.team_id),
    set_priority: (a) => Boolean(a.priority_key),
    transition: (a) => Boolean(a.to_state),
    request_approval: (a) => Boolean(a.to_state),
    add_comment: (a) => Boolean(a.body?.trim()),
    notify: (a) => Boolean(a.title?.trim()) && (a.targets?.length ?? 0) > 0,
  };
  for (const [i, a] of rule.actions.entries()) {
    if (!required[a.type](a)) return `Complete los datos de la acción ${i + 1}.`;
  }
  return null;
}

function DryRunPanel({ rule, meta, catalog }: { rule: AutomationRuleInput; meta: AutomationMeta; catalog: Catalog }) {
  const [ticketRef, setTicketRef] = useState('');
  const [eventJson, setEventJson] = useState('');
  const [result, setResult] = useState<DryRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    setError(null);
    setResult(null);
    const invalid = validate(rule);
    if (invalid) {
      setError(invalid);
      return;
    }
    let eventData: Record<string, unknown> = {};
    if (eventJson.trim()) {
      try {
        const parsed: unknown = JSON.parse(eventJson);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not object');
        eventData = parsed as Record<string, unknown>;
      } catch {
        setError('Los datos del evento deben ser un objeto JSON, p. ej. {"to_state": "resolved"}.');
        return;
      }
    }
    setRunning(true);
    try {
      const term = ticketRef.trim();
      const digits = term.toUpperCase().replace(/^ESD-/, '');
      let ticketId = /^\d+$/.test(digits) ? Number(digits) : NaN;
      if (Number.isNaN(ticketId)) {
        const page = await apiFetch<TicketPage>('/tickets', { query: { q: term, page_size: 1 } });
        if (!page.items[0]) throw new Error(`No se encontró un ticket que coincida con «${term}».`);
        ticketId = page.items[0].id;
      }
      setResult(await dryRunRule(ticketId, { ...rule, name: rule.name.trim() || 'Simulación' }, eventData));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="panel stack" aria-labelledby="dry-run-title">
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h3 className="panel-title" id="dry-run-title">
          Simular contra un ticket
        </h3>
        <span className="panel-subtitle">No modifica nada: muestra qué condiciones se cumplen y qué haría la regla.</span>
      </div>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">Ticket (ESD-123, número o texto)</span>
          <input className="input" value={ticketRef} onChange={(e) => setTicketRef(e.target.value)} placeholder="ESD-1" />
        </label>
        <label className="field">
          <span className="field-label">Datos del evento (JSON opcional)</span>
          <input
            className="input mono"
            value={eventJson}
            onChange={(e) => setEventJson(e.target.value)}
            placeholder='{"to_state": "resolved"}'
          />
        </label>
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-secondary" onClick={() => void run()} disabled={running || !ticketRef.trim()}>
          <FlowIcon name="play" width={14} height={14} />
          {running ? 'Simulando…' : 'Simular'}
        </button>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {result && (
        <div className={`${styles.result} ${result.matched ? styles.resultMatched : styles.resultNotMatched}`} aria-live="polite">
          <div className="strong">
            {result.ticket.key} — {result.matched ? 'La regla se ejecutaría' : 'La regla NO se ejecutaría'}
          </div>
          {result.conditions.length > 0 && (
            <ul className={styles.checkList}>
              {result.conditions.map((c, i) => (
                <li key={i} className={styles.checkItem}>
                  <span className={c.result ? styles.ok : styles.fail}>{c.result ? '✓' : '✗'}</span>
                  <span>
                    {describeCondition({ field: c.field, op: c.op as ConditionOperator, value: c.value }, meta, catalog)}
                    <span className="subtle"> · valor actual: {c.actual === null || c.actual === undefined ? '—' : String(c.actual)}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {result.matched && (
            <>
              <div className="text-sm strong">Acciones:</div>
              <ol style={{ margin: 0, paddingLeft: 20 }}>
                {result.actions.map((a, i) => (
                  <li key={i}>
                    {a.description}
                    {a.rendered_title && <div className="text-xs muted">Título: {a.rendered_title}</div>}
                    {a.rendered_body && <div className="text-xs muted pre-wrap">Texto: {a.rendered_body}</div>}
                    {a.recipient_ids && <div className="text-xs muted">Destinatarios: {a.recipient_ids.length}</div>}
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      )}
    </section>
  );
}

export default function RuleEditor({
  rule,
  meta,
  catalog,
  onCancel,
  onSaved,
}: {
  rule: AutomationRule | null;
  meta: AutomationMeta;
  catalog: Catalog;
  onCancel: () => void;
  onSaved: (rule: AutomationRule) => void;
}) {
  const [form, setForm] = useState<AutomationRuleInput>(() =>
    rule
      ? {
          name: rule.name,
          description: rule.description,
          active: rule.active,
          trigger: rule.trigger,
          conditions: { match: rule.conditions.match ?? 'all', rules: rule.conditions.rules ?? [] },
          actions: rule.actions,
          order: rule.order,
          stop_processing: rule.stop_processing,
        }
      : emptyRule(meta),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const invalid = validate(form);
    if (invalid) {
      setError(invalid);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body = { ...form, name: form.name.trim(), description: form.description.trim() };
      const saved = rule ? await updateRule(rule.id, body) : await createRule(body);
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack-lg">
      <form className="panel form" onSubmit={(e) => void submit(e)} aria-label={rule ? 'Editar regla' : 'Nueva regla'}>
        <div className="panel-header" style={{ marginBottom: 0 }}>
          <h2 className="panel-title">{rule ? `Editar «${rule.name}»` : 'Nueva regla'}</h2>
        </div>
        {error && <Alert tone="error">{error}</Alert>}

        <div className="form-grid">
          <label className="field">
            <span className="field-label">
              Nombre<span className="required">*</span>
            </span>
            <input className="input" value={form.name} maxLength={150} required onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Cuando ocurra</span>
            <select className="select" value={form.trigger} onChange={(e) => setForm({ ...form, trigger: e.target.value })}>
              {meta.triggers.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field field-full">
            <span className="field-label">Descripción</span>
            <input className="input" value={form.description} maxLength={2000} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Orden</span>
            <input
              className="input"
              type="number"
              min={0}
              max={100000}
              value={form.order}
              onChange={(e) => setForm({ ...form, order: Number(e.target.value) || 0 })}
            />
            <span className="field-hint">Las reglas se evalúan de menor a mayor.</span>
          </label>
          <div className="stack-sm" style={{ justifyContent: 'center' }}>
            <label className="checkbox">
              <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
              Regla activa
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.stop_processing}
                onChange={(e) => setForm({ ...form, stop_processing: e.target.checked })}
              />
              Detener las reglas siguientes si esta se ejecuta
            </label>
          </div>
        </div>

        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="panel-title" style={{ fontSize: 'var(--text-base)', marginBottom: 'var(--space-2)' }}>
            Condiciones
          </legend>
          <ConditionBuilder value={form.conditions} onChange={(conditions) => setForm({ ...form, conditions })} meta={meta} catalog={catalog} />
        </fieldset>

        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="panel-title" style={{ fontSize: 'var(--text-base)', marginBottom: 'var(--space-2)' }}>
            Acciones
          </legend>
          <ActionBuilder value={form.actions} onChange={(actions) => setForm({ ...form, actions })} meta={meta} catalog={catalog} />
        </fieldset>

        <Alert tone="info">
          Las acciones se ejecutan como el usuario «Automatización». Para evitar bucles, los eventos generados por automatizaciones
          solo se reevalúan hasta {meta.max_depth} nivel(es) y una regla no se repite sobre el mismo ticket en la misma cadena.
        </Alert>

        <div className="form-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={saving}>
            Cancelar
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Guardando…' : 'Guardar regla'}
          </button>
        </div>
      </form>

      <DryRunPanel rule={form} meta={meta} catalog={catalog} />
    </div>
  );
}
