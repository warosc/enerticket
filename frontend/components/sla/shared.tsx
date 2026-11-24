'use client';

/**
 * Piezas reutilizables del módulo SLA: barra de progreso, retroalimentación de mutaciones,
 * campo de duración y grupo de casillas.
 */
import { useCallback, useState, type ReactNode } from 'react';
import { Alert } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import type { SlaTone } from '@/lib/sla';
import styles from './sla.module.css';

export function SlaProgress({
  pct,
  tone,
  markerPct,
  label,
}: {
  pct: number;
  tone: SlaTone;
  /** Posición del umbral de advertencia (0-100). */
  markerPct?: number;
  label: string;
}) {
  const width = Math.min(100, Math.max(0, pct));
  // Un consumo mínimo (> 0 %) sigue siendo visible como una franja.
  const fill = width > 0 && width < 2 ? 2 : width;
  return (
    <div
      className={`${styles.progress} ${styles[`tone-${tone}`] ?? ''}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(width)}
    >
      <div className={styles.progressFill} style={{ width: `${fill}%` }} />
      {markerPct !== undefined && markerPct > 0 && markerPct < 100 && (
        <span className={styles.progressMarker} style={{ left: `${markerPct}%` }} aria-hidden="true" />
      )}
    </div>
  );
}

export interface Mutation {
  busy: boolean;
  error: string | null;
  notice: string | null;
  run: <T>(action: () => Promise<T>, success?: string | ((result: T) => string)) => Promise<T | null>;
  clear: () => void;
}

/** Ejecuta una acción con estado de carga, error y aviso de éxito. */
export function useMutation(): Mutation {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async <T,>(action: () => Promise<T>, success?: string | ((result: T) => string)): Promise<T | null> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const result = await action();
        if (success) setNotice(typeof success === 'function' ? success(result) : success);
        return result;
      } catch (e) {
        setError(errorMessage(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const clear = useCallback(() => {
    setError(null);
    setNotice(null);
  }, []);

  return { busy, error, notice, run, clear };
}

export function Feedback({ mutation }: { mutation: Pick<Mutation, 'error' | 'notice'> }) {
  if (mutation.error) return <Alert tone="error">{mutation.error}</Alert>;
  if (mutation.notice) return <Alert tone="success">{mutation.notice}</Alert>;
  return null;
}

type DurationUnit = 'min' | 'h';

/** Entrada de duración en minutos con selector de unidad (minutos/horas). Vacío = null. */
export function DurationInput({
  id,
  value,
  onChange,
  allowEmpty = true,
  label,
}: {
  id: string;
  value: number | null;
  onChange: (minutes: number | null) => void;
  allowEmpty?: boolean;
  label: string;
}) {
  const [unit, setUnit] = useState<DurationUnit>(() => (value && value % 60 === 0 ? 'h' : 'min'));
  const display = value === null ? '' : unit === 'h' ? String(value / 60) : String(value);

  function update(raw: string, nextUnit: DurationUnit) {
    if (raw.trim() === '') {
      onChange(allowEmpty ? null : value);
      return;
    }
    const num = Number(raw.replace(',', '.'));
    if (!Number.isFinite(num) || num <= 0) return;
    onChange(Math.max(1, Math.round(nextUnit === 'h' ? num * 60 : num)));
  }

  return (
    <div className={styles.durationField}>
      <input
        id={id}
        className="input"
        type="number"
        inputMode="decimal"
        min={unit === 'h' ? 0.25 : 1}
        step={unit === 'h' ? 0.25 : 1}
        value={display}
        placeholder={allowEmpty ? 'Sin objetivo' : ''}
        aria-label={label}
        onChange={(e) => update(e.target.value, unit)}
      />
      <select
        className="select"
        value={unit}
        aria-label={`Unidad de ${label.toLowerCase()}`}
        onChange={(e) => setUnit(e.target.value as DurationUnit)}
      >
        <option value="min">minutos</option>
        <option value="h">horas</option>
      </select>
    </div>
  );
}

/** Grupo de casillas para seleccionar varios valores. */
export function CheckboxGroup<T extends string | number>({
  legend,
  hint,
  options,
  value,
  onChange,
  disabled = false,
}: {
  legend: string;
  hint?: ReactNode;
  options: Array<{ value: T; label: string }>;
  value: T[];
  onChange: (next: T[]) => void;
  disabled?: boolean;
}) {
  function toggle(option: T, checked: boolean) {
    const next = checked ? [...value, option] : value.filter((v) => v !== option);
    onChange(options.map((o) => o.value).filter((v) => next.includes(v)));
  }
  return (
    <fieldset className={styles.fieldset} disabled={disabled}>
      <legend>{legend}</legend>
      {hint && <div className="field-hint">{hint}</div>}
      <div className={styles.checkGrid}>
        {options.map((option) => (
          <label key={String(option.value)} className="checkbox">
            <input
              type="checkbox"
              checked={value.includes(option.value)}
              onChange={(e) => toggle(option.value, e.target.checked)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function Chips({ items, empty = 'Cualquiera' }: { items: string[]; empty?: string }) {
  if (!items.length) return <span className="text-xs subtle">{empty}</span>;
  return (
    <span className={styles.chips}>
      {items.map((item) => (
        <span key={item} className={styles.chip}>
          {item}
        </span>
      ))}
    </span>
  );
}
