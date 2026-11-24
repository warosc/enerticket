'use client';

/**
 * Automatizaciones (solo admin): reglas (lista, alta/edición, activar, eliminar) e historial
 * de ejecuciones con su resultado por acción.
 */
import Link from 'next/link';
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { describeCondition } from '@/components/automations/RuleBuilders';
import RuleEditor from '@/components/automations/RuleEditor';
import { FlowIcon } from '@/components/notifications/FlowIcon';
import { Alert, EmptyState, ErrorAlert, Loading, PageHeader, Pagination } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  deleteRule,
  EXECUTION_STATUS_BADGE,
  EXECUTION_STATUS_LABELS,
  getAutomationMeta,
  listExecutions,
  listRules,
  updateRule,
  type AutomationMeta,
  type AutomationRule,
  type ExecutionPage,
} from '@/lib/flows';
import { formatDateTime, formatRelative } from '@/lib/format';
import { useCatalog } from '@/lib/hooks';
import styles from './automations.module.css';

type Tab = 'rules' | 'executions';

function RulesTab({ meta, onOpenExecutions }: { meta: AutomationMeta; onOpenExecutions: (ruleId: number) => void }) {
  const { catalog, error: catalogError, reload: reloadCatalog } = useCatalog();
  const [rules, setRules] = useState<AutomationRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<AutomationRule | 'new' | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRules(await listRules());
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

  const triggerLabel = useMemo(() => new Map(meta.triggers.map((t) => [t.key, t.label])), [meta]);
  const actionLabel = useMemo(() => new Map(meta.actions.map((a) => [a.key, a.label])), [meta]);

  async function toggle(rule: AutomationRule) {
    setBusy(rule.id);
    setNotice(null);
    try {
      const updated = await updateRule(rule.id, { active: !rule.active });
      setRules((prev) => prev?.map((r) => (r.id === rule.id ? updated : r)) ?? null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove(rule: AutomationRule) {
    if (!window.confirm(`¿Eliminar la regla «${rule.name}»? También se borrará su historial de ejecuciones.`)) return;
    setBusy(rule.id);
    try {
      await deleteRule(rule.id);
      setNotice(`Regla «${rule.name}» eliminada.`);
      setRules((prev) => prev?.filter((r) => r.id !== rule.id) ?? null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (editing) {
    if (!catalog) {
      return catalogError ? <ErrorAlert error={catalogError} onRetry={() => void reloadCatalog()} /> : <Loading label="Cargando catálogo…" />;
    }
    return (
      <RuleEditor
        key={editing === 'new' ? 'new' : editing.id}
        rule={editing === 'new' ? null : editing}
        meta={meta}
        catalog={catalog}
        onCancel={() => setEditing(null)}
        onSaved={(saved) => {
          setNotice(`Regla «${saved.name}» guardada.`);
          setEditing(null);
          void load();
        }}
      />
    );
  }

  return (
    <div className="stack">
      <ErrorAlert error={error} onRetry={() => void load()} />
      {notice && <Alert tone="success">{notice}</Alert>}
      <section className="panel panel-flush">
        <div className="panel-header">
          <div>
            <h2 className="panel-title">Reglas</h2>
            <p className="panel-subtitle">Se evalúan en orden ante cada evento de su disparador.</p>
          </div>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing('new')}>
            Nueva regla
          </button>
        </div>
        {loading && !rules ? (
          <Loading label="Cargando reglas…" />
        ) : rules && rules.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="col-num">Orden</th>
                  <th>Regla</th>
                  <th>Disparador</th>
                  <th>Acciones</th>
                  <th className="col-num">Ejecuciones</th>
                  <th>Activa</th>
                  <th aria-label="Opciones" />
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td className="col-num">{rule.order}</td>
                    <td className="col-title">
                      <div className="strong">{rule.name}</div>
                      <div className={styles.ruleSummary}>
                        {rule.conditions.rules.length === 0
                          ? 'Siempre'
                          : rule.conditions.rules
                              .map((c) => describeCondition(c, meta, catalog))
                              .join(rule.conditions.match === 'any' ? ' o ' : ' y ')}
                        {rule.stop_processing ? ' · detiene las siguientes' : ''}
                      </div>
                    </td>
                    <td className="text-sm">{triggerLabel.get(rule.trigger) ?? rule.trigger}</td>
                    <td className="text-sm">{rule.actions.map((a) => actionLabel.get(a.type) ?? a.type).join(', ')}</td>
                    <td className="col-num">
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpenExecutions(rule.id)} title="Ver ejecuciones">
                        {rule.execution_count}
                      </button>
                      {rule.last_execution_at && (
                        <div className="text-xs subtle" title={formatDateTime(rule.last_execution_at)}>
                          {formatRelative(rule.last_execution_at)}
                        </div>
                      )}
                    </td>
                    <td>
                      <label className={styles.switch}>
                        <input
                          type="checkbox"
                          checked={rule.active}
                          disabled={busy === rule.id}
                          onChange={() => void toggle(rule)}
                          aria-label={`${rule.active ? 'Desactivar' : 'Activar'} ${rule.name}`}
                        />
                        {rule.active ? 'Sí' : 'No'}
                      </label>
                    </td>
                    <td className="nowrap">
                      <div className="btn-group">
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(rule)}>
                          Editar
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => void remove(rule)}
                          disabled={busy === rule.id}
                          aria-label={`Eliminar ${rule.name}`}
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
          <EmptyState title="Sin reglas">Cree una regla para automatizar asignaciones, prioridades o avisos.</EmptyState>
        )}
      </section>
    </div>
  );
}

function ExecutionsTab({
  rules,
  ruleId,
  onRuleChange,
}: {
  rules: AutomationRule[];
  ruleId: number | null;
  onRuleChange: (ruleId: number | null) => void;
}) {
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [data, setData] = useState<ExecutionPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await listExecutions({ rule_id: ruleId ?? undefined, status: status || undefined, page, page_size: pageSize }));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [ruleId, status, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="stack">
      <div className="toolbar">
        <label className="field">
          <span className="field-label">Regla</span>
          <select
            className="select"
            value={ruleId ?? ''}
            onChange={(e) => {
              onRuleChange(e.target.value ? Number(e.target.value) : null);
              setPage(1);
            }}
          >
            <option value="">Todas</option>
            {rules.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Resultado</span>
          <select
            className="select"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Todos</option>
            {(Object.keys(EXECUTION_STATUS_LABELS) as Array<keyof typeof EXECUTION_STATUS_LABELS>).map((key) => (
              <option key={key} value={key}>
                {EXECUTION_STATUS_LABELS[key]}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn" onClick={() => void load()} disabled={loading}>
          Actualizar
        </button>
      </div>
      <ErrorAlert error={error} onRetry={() => void load()} />
      <section className="panel panel-flush">
        {loading && !data ? (
          <Loading label="Cargando ejecuciones…" />
        ) : data && data.items.length > 0 ? (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Regla</th>
                    <th>Ticket</th>
                    <th>Evento</th>
                    <th>Resultado</th>
                    <th aria-label="Detalle" />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((execution) => (
                    <Fragment key={execution.id}>
                      <tr>
                        <td className="nowrap text-sm" title={formatDateTime(execution.created_at)}>
                          {formatRelative(execution.created_at)}
                        </td>
                        <td>{execution.rule.name}</td>
                        <td className="col-key">
                          {execution.ticket ? <Link href={`/tickets/${execution.ticket.id}`}>{execution.ticket.key}</Link> : '—'}
                        </td>
                        <td className="text-sm">
                          <code>{execution.event_type}</code>
                          {execution.depth > 0 && <span className="badge badge-outline" style={{ marginLeft: 6 }}>en cadena</span>}
                        </td>
                        <td>
                          <span className={`badge ${EXECUTION_STATUS_BADGE[execution.status]}`}>{EXECUTION_STATUS_LABELS[execution.status]}</span>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            aria-expanded={expanded === execution.id}
                            onClick={() => setExpanded((id) => (id === execution.id ? null : execution.id))}
                          >
                            {expanded === execution.id ? 'Ocultar' : 'Detalle'}
                          </button>
                        </td>
                      </tr>
                      {expanded === execution.id && (
                        <tr className={styles.detailRow}>
                          <td colSpan={6}>
                            <ul className={styles.resultLines}>
                              {execution.result.map((r, i) => (
                                <li key={i}>
                                  <span className={r.ok ? styles.ok : styles.fail}>{r.ok ? '✓' : '✗'}</span> {r.detail}
                                </li>
                              ))}
                            </ul>
                            {execution.error && <div className="text-xs" style={{ color: 'var(--color-danger)', marginTop: 4 }}>{execution.error}</div>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={data.page}
              pageSize={pageSize}
              total={data.total}
              onPageChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                setPage(1);
              }}
            />
          </>
        ) : (
          !error && <EmptyState title="Sin ejecuciones">Aún no hay reglas que se hayan ejecutado con estos filtros.</EmptyState>
        )}
      </section>
    </div>
  );
}

export default function AutomationsView() {
  const { isAdmin } = useAuth();
  const [tab, setTab] = useState<Tab>('rules');
  const [meta, setMeta] = useState<AutomationMeta | null>(null);
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [executionsRule, setExecutionsRule] = useState<number | null>(null);

  const loadMeta = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([getAutomationMeta(), listRules()]);
      setMeta(m);
      setRules(r);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void loadMeta();
  }, [isAdmin, loadMeta]);

  if (!isAdmin) {
    return (
      <div className="page">
        <PageHeader title="Automatizaciones" />
        <section className="panel">
          <EmptyState title="Acceso restringido">Esta sección requiere el rol de administrador.</EmptyState>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Automatizaciones"
        subtitle="Reglas que reaccionan a eventos de tickets: asignan, cambian prioridad o estado, comentan, notifican o solicitan aprobación."
      />
      <div className="tabs" role="tablist" aria-label="Secciones de automatizaciones">
        {(
          [
            ['rules', 'Reglas'],
            ['executions', 'Historial de ejecuciones'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`tab${tab === key ? ' is-active' : ''}`}
            onClick={() => {
              setTab(key);
              if (key === 'executions') void loadMeta();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <ErrorAlert error={error} onRetry={() => void loadMeta()} />
      {!meta ? (
        !error && <Loading label="Cargando…" />
      ) : tab === 'rules' ? (
        <RulesTab
          meta={meta}
          onOpenExecutions={(ruleId) => {
            setExecutionsRule(ruleId);
            setTab('executions');
            void loadMeta();
          }}
        />
      ) : (
        <ExecutionsTab rules={rules} ruleId={executionsRule} onRuleChange={setExecutionsRule} />
      )}
    </div>
  );
}
