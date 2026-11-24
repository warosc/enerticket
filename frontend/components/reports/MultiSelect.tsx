'use client';

/**
 * Selector múltiple con búsqueda (filtros del generador de reportes).
 * En escritorio se despliega bajo el botón; en móvil (< 640px) como hoja inferior fija para no desbordar
 * la página.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ReportOption } from '@/lib/reports';
import styles from './builder.module.css';

const SEARCH_THRESHOLD = 8;

function normalize(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

export default function MultiSelect<T extends string | number>({
  label,
  options,
  value,
  onChange,
  placeholder = 'Todos',
  disabled,
  emptyText = 'No hay opciones disponibles.',
}: {
  label: string;
  options: ReportOption<T>[];
  value: T[];
  onChange: (next: T[]) => void;
  placeholder?: string;
  disabled?: boolean;
  emptyText?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const labelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent | TouchEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('touchstart', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('touchstart', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (open) searchRef.current?.focus();
    else setQuery('');
  }, [open]);

  const selected = useMemo(() => new Set<T>(value), [value]);
  const filtered = useMemo(() => {
    const q = normalize(query.trim());
    if (!q) return options;
    return options.filter((o) => normalize(o.label).includes(q) || (o.group && normalize(o.group).includes(q)));
  }, [options, query]);

  const summary = useMemo(() => {
    if (!value.length) return placeholder;
    const labels = options.filter((o) => selected.has(o.value)).map((o) => o.label);
    // Valores guardados que ya no están en el catálogo (p.ej. desactivados) se cuentan igual.
    const missing = value.length - labels.length;
    if (labels.length <= 2 && missing <= 0) return labels.join(', ');
    return `${value.length} seleccionado${value.length === 1 ? '' : 's'}`;
  }, [options, placeholder, selected, value.length]);

  const toggle = (option: T) => {
    if (selected.has(option)) onChange(value.filter((v) => v !== option));
    else onChange([...value, option]);
  };

  let lastGroup: string | undefined;

  return (
    <div className={`field ${styles.multi}`} ref={rootRef}>
      <span className="field-label" id={labelId}>
        {label}
      </span>
      <button
        type="button"
        className={`select ${styles.multiTrigger}${value.length ? ` ${styles.multiTriggerActive}` : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${label}: ${summary}`}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title={summary}
      >
        <span className="truncate">{summary}</span>
      </button>
      {open && (
        <div className={styles.multiPopover} role="dialog" aria-label={label}>
          <div className={styles.multiHeader}>
            <span className="strong text-sm">{label}</span>
            <div className="row-sm">
              {value.length > 0 && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange([])}>
                  Limpiar
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setOpen(false)}
                aria-label="Cerrar"
              >
                Listo
              </button>
            </div>
          </div>
          {options.length > SEARCH_THRESHOLD && (
            <input
              ref={searchRef}
              type="search"
              className="input input-sm"
              placeholder="Buscar…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label={`Buscar en ${label}`}
            />
          )}
          <div className={styles.multiList} id={listId} role="listbox" aria-multiselectable="true" aria-label={label}>
            {filtered.length === 0 && <div className="text-sm subtle">{options.length ? 'Sin coincidencias.' : emptyText}</div>}
            {filtered.map((option) => {
              const showGroup = option.group !== undefined && option.group !== lastGroup;
              lastGroup = option.group;
              return (
                <div key={String(option.value)}>
                  {showGroup && <div className={styles.multiGroup}>{option.group}</div>}
                  <label className={`checkbox ${styles.multiOption}`} role="option" aria-selected={selected.has(option.value)}>
                    <input
                      type="checkbox"
                      checked={selected.has(option.value)}
                      onChange={() => toggle(option.value)}
                    />
                    <span>{option.label}</span>
                  </label>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
