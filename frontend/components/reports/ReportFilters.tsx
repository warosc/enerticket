'use client';

import { useMemo } from 'react';
import type { Catalog } from '@/lib/types';
import {
  addDaysIso,
  daysBetweenInclusive,
  monthStartIso,
  todayIso,
  type ReportFilterValues,
} from '@/lib/reports';
import styles from './reports.module.css';

export const MAX_RANGE_DAYS = 731;

interface Preset {
  key: string;
  label: string;
  range: () => { from: string; to: string };
}

const PRESETS: Preset[] = [
  { key: '7d', label: '7 días', range: () => ({ from: addDaysIso(todayIso(), -6), to: todayIso() }) },
  { key: '30d', label: '30 días', range: () => ({ from: addDaysIso(todayIso(), -29), to: todayIso() }) },
  { key: '90d', label: '90 días', range: () => ({ from: addDaysIso(todayIso(), -89), to: todayIso() }) },
  { key: 'month', label: 'Mes actual', range: () => ({ from: monthStartIso(todayIso()), to: todayIso() }) },
  { key: '12m', label: '12 meses', range: () => ({ from: addDaysIso(todayIso(), -364), to: todayIso() }) },
];

/** Valida el rango; devuelve un mensaje en español o null. */
export function validateRange(filters: ReportFilterValues): string | null {
  if (!filters.from || !filters.to) return 'Indique la fecha inicial y la final.';
  if (filters.from > filters.to) return 'La fecha inicial no puede ser posterior a la final.';
  if (daysBetweenInclusive(filters.from, filters.to) > MAX_RANGE_DAYS) {
    return `El rango máximo es de ${MAX_RANGE_DAYS} días.`;
  }
  return null;
}

export default function ReportFilters({
  value,
  onChange,
  catalog,
  disabled,
}: {
  value: ReportFilterValues;
  onChange: (next: ReportFilterValues) => void;
  catalog: Catalog | null;
  disabled?: boolean;
}) {
  const teams = useMemo(
    () =>
      (catalog?.teams ?? []).filter((t) => value.department_id === null || t.department_id === value.department_id),
    [catalog, value.department_id],
  );
  const activePreset = PRESETS.find((p) => {
    const r = p.range();
    return r.from === value.from && r.to === value.to;
  })?.key;
  const today = todayIso();

  return (
    <section className="panel" aria-label="Filtros del reporte">
      <div className={styles.filters}>
        <label className="field">
          <span className="field-label">Desde</span>
          <input
            type="date"
            className="input"
            value={value.from}
            max={value.to || today}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, from: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">Hasta</span>
          <input
            type="date"
            className="input"
            value={value.to}
            min={value.from}
            max={today}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, to: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">Departamento</span>
          <select
            className="select"
            value={value.department_id ?? ''}
            disabled={disabled || !catalog}
            onChange={(e) => {
              const department_id = e.target.value ? Number(e.target.value) : null;
              const teamStillValid =
                value.team_id !== null &&
                catalog?.teams.some((t) => t.id === value.team_id && (department_id === null || t.department_id === department_id));
              onChange({ ...value, department_id, team_id: teamStillValid ? value.team_id : null });
            }}
          >
            <option value="">Todos</option>
            {(catalog?.departments ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Equipo</span>
          <select
            className="select"
            value={value.team_id ?? ''}
            disabled={disabled || !catalog}
            onChange={(e) => onChange({ ...value, team_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Todos</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={styles.presets} style={{ marginTop: 'var(--space-3)' }} role="group" aria-label="Rangos rápidos">
        {PRESETS.map((preset) => (
          <button
            key={preset.key}
            type="button"
            className={`btn btn-sm ${activePreset === preset.key ? styles.presetActive : ''}`}
            aria-pressed={activePreset === preset.key}
            disabled={disabled}
            onClick={() => onChange({ ...value, ...preset.range() })}
          >
            {preset.label}
          </button>
        ))}
      </div>
    </section>
  );
}
