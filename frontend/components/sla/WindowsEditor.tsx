'use client';

import { WEEKDAY_LABELS, WEEKDAY_SHORT, type SlaWindow } from '@/lib/sla';
import styles from './sla.module.css';

function toMinutes(value: string): number {
  const [h, m] = value.split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Valida ventanas: fin posterior al inicio y sin solapamientos en el mismo día. */
export function validateWindows(windows: SlaWindow[]): string | null {
  if (windows.length === 0) return 'Defina al menos una ventana de servicio.';
  for (let day = 0; day < 7; day += 1) {
    const ranges = windows
      .filter((w) => w.weekday === day)
      .map((w) => ({ start: toMinutes(w.start), end: toMinutes(w.end) }))
      .sort((a, b) => a.start - b.start);
    for (let i = 0; i < ranges.length; i += 1) {
      if (ranges[i].end <= ranges[i].start) {
        return `${WEEKDAY_LABELS[day]}: la hora de fin debe ser posterior a la de inicio.`;
      }
      if (i > 0 && ranges[i].start < ranges[i - 1].end) {
        return `${WEEKDAY_LABELS[day]}: las ventanas se solapan.`;
      }
    }
  }
  return null;
}

/** Vista compacta de la semana: barras con las horas hábiles de cada día. */
export function WeekStrip({ windows }: { windows: SlaWindow[] }) {
  return (
    <div className={styles.weekStrip} aria-hidden="true">
      {WEEKDAY_SHORT.map((label, day) => (
        <div key={label} className={styles.weekDay}>
          <div className={styles.weekDayBar}>
            {windows
              .filter((w) => w.weekday === day)
              .map((w) => {
                const start = toMinutes(w.start);
                const end = toMinutes(w.end);
                return (
                  <span
                    key={`${w.start}-${w.end}`}
                    className={styles.weekSpan}
                    style={{ top: `${(start / 1440) * 100}%`, height: `${(Math.max(end - start, 0) / 1440) * 100}%` }}
                  />
                );
              })}
          </div>
          <span className={styles.weekDayLabel}>{label}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Editor de ventanas de servicio por día. Un fin "00:00" se interpreta como medianoche (24:00).
 */
export default function WindowsEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: SlaWindow[];
  onChange: (next: SlaWindow[]) => void;
  disabled?: boolean;
}) {
  function sorted(next: SlaWindow[]): SlaWindow[] {
    return [...next].sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start));
  }

  function toggleDay(day: number, enabled: boolean) {
    const others = value.filter((w) => w.weekday !== day);
    onChange(sorted(enabled ? [...others, { weekday: day, start: '08:00', end: '17:00' }] : others));
  }

  function updateRange(day: number, index: number, patch: Partial<SlaWindow>) {
    const dayRanges = value.filter((w) => w.weekday === day);
    const others = value.filter((w) => w.weekday !== day);
    const updated = dayRanges.map((w, i) => (i === index ? { ...w, ...patch } : w));
    onChange(sorted([...others, ...updated]));
  }

  function removeRange(day: number, index: number) {
    const dayRanges = value.filter((w) => w.weekday === day);
    const others = value.filter((w) => w.weekday !== day);
    onChange(sorted([...others, ...dayRanges.filter((_, i) => i !== index)]));
  }

  function addRange(day: number) {
    const dayRanges = value.filter((w) => w.weekday === day);
    const lastEnd = dayRanges.length ? toMinutes(dayRanges[dayRanges.length - 1].end) : 8 * 60;
    const start = Math.min(lastEnd + 60, 22 * 60);
    const fmt = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
    onChange(sorted([...value, { weekday: day, start: fmt(start), end: fmt(Math.min(start + 120, 24 * 60)) }]));
  }

  function setFullDay(day: number) {
    const others = value.filter((w) => w.weekday !== day);
    onChange(sorted([...others, { weekday: day, start: '00:00', end: '24:00' }]));
  }

  return (
    <div className={styles.weekGrid}>
      {WEEKDAY_LABELS.map((label, day) => {
        const ranges = value.filter((w) => w.weekday === day);
        const enabled = ranges.length > 0;
        return (
          <div key={label} className={styles.dayRow}>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={enabled}
                disabled={disabled}
                onChange={(e) => toggleDay(day, e.target.checked)}
              />
              <span className="strong">{label}</span>
            </label>
            {enabled ? (
              <div className={styles.ranges}>
                {ranges.map((range, index) => (
                  <div key={`${day}-${index}`} className={styles.range}>
                    <input
                      className="input input-sm"
                      type="time"
                      value={range.start}
                      disabled={disabled}
                      aria-label={`${label}: inicio de la ventana ${index + 1}`}
                      onChange={(e) => updateRange(day, index, { start: e.target.value || '00:00' })}
                    />
                    <span className="muted">a</span>
                    <input
                      className="input input-sm"
                      type="time"
                      value={range.end === '24:00' ? '00:00' : range.end}
                      disabled={disabled}
                      aria-label={`${label}: fin de la ventana ${index + 1}`}
                      onChange={(e) => {
                        const v = e.target.value || '00:00';
                        updateRange(day, index, { end: v === '00:00' ? '24:00' : v });
                      }}
                    />
                    {range.end === '24:00' && <span className="text-xs subtle">medianoche</span>}
                    {!disabled && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => removeRange(day, index)}
                        aria-label={`Quitar ventana ${index + 1} del ${label.toLowerCase()}`}
                      >
                        Quitar
                      </button>
                    )}
                  </div>
                ))}
                {!disabled && (
                  <div className="row-sm">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => addRange(day)}>
                      + Ventana
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setFullDay(day)}>
                      Todo el día
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <span className="text-sm subtle" style={{ paddingTop: 6 }}>
                No laborable
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
