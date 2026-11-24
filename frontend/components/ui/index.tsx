'use client';

/**
 * Componentes de interfaz reutilizables (fundación). Usan las clases de app/globals.css.
 */
import { useId, useState, type ReactNode } from 'react';
import type { PriorityRef, StatusRef } from '@/lib/types';
import { Icon } from '../icons';

const PRIORITY_KEYS = new Set(['low', 'medium', 'high', 'critical']);
const CATEGORY_KEYS = new Set(['todo', 'in_progress', 'waiting', 'done']);

export function PriorityBadge({ priority }: { priority: Pick<PriorityRef, 'key' | 'name'> | null | undefined }) {
  if (!priority) return <span className="badge badge-outline">Sin prioridad</span>;
  const variant = PRIORITY_KEYS.has(priority.key) ? `badge-priority-${priority.key}` : 'badge-neutral';
  return <span className={`badge ${variant}`}>{priority.name}</span>;
}

export function StatusBadge({ status }: { status: Pick<StatusRef, 'name' | 'category'> | null | undefined }) {
  if (!status) return <span className="badge badge-outline">Sin estado</span>;
  const variant = CATEGORY_KEYS.has(status.category) ? `badge-status-${status.category}` : 'badge-neutral';
  return <span className={`badge badge-dot ${variant}`}>{status.name}</span>;
}

export function Spinner({ large = false, label }: { large?: boolean; label?: string }) {
  return <span className={large ? 'spinner spinner-lg' : 'spinner'} role="status" aria-label={label ?? 'Cargando'} />;
}

export function Loading({ label = 'Cargando…' }: { label?: string }) {
  return (
    <div className="loading" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

type AlertTone = 'error' | 'warning' | 'success' | 'info';

export function Alert({
  tone = 'info',
  title,
  children,
  action,
}: {
  tone?: AlertTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`alert alert-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <div className="alert-body">
        {title && <div className="alert-title">{title}</div>}
        {children}
      </div>
      {action}
    </div>
  );
}

/** Alerta de error con botón de reintento opcional. No renderiza nada si `error` es vacío. */
export function ErrorAlert({ error, onRetry }: { error: string | null | undefined; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <Alert
      tone="error"
      action={
        onRetry ? (
          <button type="button" className="btn btn-sm" onClick={onRetry}>
            Reintentar
          </button>
        ) : undefined
      }
    >
      {error}
    </Alert>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-state-title">{title}</div>
      {children && <div className="text-sm">{children}</div>}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  breadcrumb,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {breadcrumb && <div className="breadcrumb">{breadcrumb}</div>}
        <h1 className="page-title">{title}</h1>
        {subtitle && <div className="page-subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizes = [10, 25, 50, 100],
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizes?: number[];
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav className="pagination" aria-label="Paginación">
      <span>
        {from}–{to} de {total}
      </span>
      <div className="row-sm">
        {onPageSizeChange && (
          <label className="row-sm">
            <span className="text-xs">Por página</span>
            <select
              className="select select-sm"
              style={{ width: 'auto' }}
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
            >
              {pageSizes.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          Anterior
        </button>
        <span className="text-xs">
          Página {page} de {totalPages}
        </span>
        <button
          type="button"
          className="btn btn-sm"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Siguiente
        </button>
      </div>
    </nav>
  );
}

/**
 * Panel de filtros: en escritorio se muestra siempre; en móvil (< 640px) queda plegado tras un
 * botón «Filtros (N activos)» para no ocupar toda la pantalla.
 */
export function CollapsibleFilters({
  activeCount,
  children,
  label = 'Filtros',
}: {
  activeCount: number;
  children: ReactNode;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const countText = activeCount > 0 ? ` (${activeCount} activo${activeCount === 1 ? '' : 's'})` : '';
  return (
    <section className={`panel filter-panel${open ? ' is-open' : ''}`} aria-label={label}>
      <button
        type="button"
        className="btn filter-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="row-sm">
          <Icon name="filter" width={15} height={15} />
          {label}
          {countText}
        </span>
        <Icon name="chevronDown" width={16} height={16} className="filter-toggle-chevron" />
      </button>
      <div id={bodyId} className="filter-panel-body">
        {children}
      </div>
    </section>
  );
}

/** Marcador para páginas de módulos aún no integrados. */
export function ModulePlaceholder({ title, description }: { title: string; description: string }) {
  return (
    <div className="page">
      <PageHeader title={title} subtitle={description} />
      <section className="panel">
        <EmptyState title="Módulo en construcción">
          Esta sección estará disponible cuando el módulo correspondiente se integre al portal.
        </EmptyState>
      </section>
    </div>
  );
}
