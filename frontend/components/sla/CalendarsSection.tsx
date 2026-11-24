'use client';

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { formatDateTime } from '@/lib/format';
import { useApiResource } from '@/lib/hooks';
import {
  BUSINESS_HOURS_PRESET,
  FULL_TIME_PRESET,
  describeWindows,
  formatBusinessDuration,
  slaApi,
  type SlaCalendar,
  type SlaCalendarInput,
  type SlaPreview,
} from '@/lib/sla';
import HolidaysPanel from './HolidaysPanel';
import { Feedback, useMutation } from './shared';
import WindowsEditor, { WeekStrip, validateWindows } from './WindowsEditor';
import styles from './sla.module.css';

const COMMON_TIMEZONES = [
  'America/Guatemala',
  'America/El_Salvador',
  'America/Tegucigalpa',
  'America/Costa_Rica',
  'America/Mexico_City',
  'America/Bogota',
  'America/New_York',
  'UTC',
];

function emptyCalendar(): SlaCalendarInput {
  return { name: '', description: '', timezone: 'America/Guatemala', windows: BUSINESS_HOURS_PRESET };
}

function CalendarForm({
  initial,
  calendarId,
  busy,
  onSubmit,
  onCancel,
  onDelete,
}: {
  initial: SlaCalendarInput;
  calendarId: number | null;
  busy: boolean;
  onSubmit: (value: SlaCalendarInput) => void;
  onCancel?: () => void;
  onDelete?: () => void;
}) {
  const [form, setForm] = useState<SlaCalendarInput>(initial);
  const windowsError = validateWindows(form.windows);
  const valid = form.name.trim().length >= 2 && !windowsError && (form.timezone ?? '').trim().length > 0;
  const prefix = calendarId ? `calendar-${calendarId}` : 'calendar-new';

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    onSubmit({
      name: form.name.trim(),
      description: (form.description ?? '').trim(),
      timezone: (form.timezone ?? '').trim(),
      windows: form.windows,
    });
  }

  return (
    <form className="panel form" onSubmit={submit} aria-label={calendarId ? 'Editar calendario' : 'Nuevo calendario'}>
      <h3 className="panel-title">{calendarId ? 'Configuración del calendario' : 'Nuevo calendario hábil'}</h3>
      <div className="form-grid">
        <label className="field">
          <span className="field-label">
            Nombre<span className="required">*</span>
          </span>
          <input
            className="input"
            value={form.name}
            required
            minLength={2}
            maxLength={150}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Zona horaria<span className="required">*</span>
          </span>
          <input
            className="input"
            list={`${prefix}-zones`}
            value={form.timezone ?? ''}
            required
            onChange={(e) => setForm({ ...form, timezone: e.target.value })}
          />
          <datalist id={`${prefix}-zones`}>
            {COMMON_TIMEZONES.map((z) => (
              <option key={z} value={z} />
            ))}
          </datalist>
          <span className="field-hint">Identificador IANA. Guatemala: UTC-6 sin horario de verano.</span>
        </label>
        <label className="field field-full">
          <span className="field-label">Descripción</span>
          <input
            className="input"
            value={form.description ?? ''}
            maxLength={2000}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </label>
      </div>

      <div className="stack-sm">
        <div className="row-between">
          <span className="field-label">Horario de servicio</span>
          <div className="row-sm">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setForm({ ...form, windows: BUSINESS_HOURS_PRESET })}
            >
              L-V 08:00-17:00
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setForm({ ...form, windows: FULL_TIME_PRESET })}>
              24x7
            </button>
          </div>
        </div>
        <WeekStrip windows={form.windows} />
        <p className="field-hint">{describeWindows(form.windows)}</p>
        <WindowsEditor value={form.windows} onChange={(windows) => setForm({ ...form, windows })} />
        {windowsError && <p className="field-error">{windowsError}</p>}
      </div>

      <div className="form-actions">
        {onDelete && (
          <button type="button" className="btn btn-ghost" onClick={onDelete} disabled={busy} style={{ marginRight: 'auto' }}>
            Eliminar calendario
          </button>
        )}
        {onCancel && (
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
        )}
        <button type="submit" className="btn btn-primary" disabled={busy || !valid}>
          {calendarId ? 'Guardar cambios' : 'Crear calendario'}
        </button>
      </div>
      {calendarId && (
        <p className="field-hint">
          Al guardar se recalculan los vencimientos de los tickets abiertos que usan este calendario.
        </p>
      )}
    </form>
  );
}

function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

/** Simulador: cuándo vence un objetivo de N minutos hábiles iniciado en una fecha dada. */
function PreviewTool({ calendar }: { calendar: SlaCalendar }) {
  const [start, setStart] = useState(() => toLocalInput(new Date()));
  const [minutes, setMinutes] = useState(120);
  const [result, setResult] = useState<SlaPreview | null>(null);
  const mutation = useMutation();

  useEffect(() => setResult(null), [calendar.id]);

  async function run(event: FormEvent) {
    event.preventDefault();
    const date = new Date(start);
    if (Number.isNaN(date.getTime()) || minutes < 1) return;
    const preview = await mutation.run(() => slaApi.preview(calendar.id, minutes, date.toISOString()));
    if (preview) setResult(preview);
  }

  return (
    <section className="panel stack-sm">
      <h3 className="panel-title">Simular vencimiento</h3>
      <form className="toolbar" onSubmit={run}>
        <label className="field">
          <span className="field-label">Inicio (hora de su equipo)</span>
          <input className="input" type="datetime-local" value={start} required onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Minutos hábiles</span>
          <input
            className="input"
            type="number"
            min={1}
            max={525600}
            value={minutes}
            onChange={(e) => setMinutes(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <button type="submit" className="btn btn-secondary" style={{ alignSelf: 'flex-end' }} disabled={mutation.busy}>
          Calcular
        </button>
      </form>
      <Feedback mutation={mutation} />
      {result && (
        <p className="text-sm">
          Un objetivo de <strong>{formatBusinessDuration(result.minutes * 60)}</strong> hábiles iniciado el{' '}
          {formatDateTime(result.start)} vence el <strong>{formatDateTime(result.target_at)}</strong> (hora de Guatemala).
        </p>
      )}
    </section>
  );
}

/** Calendarios hábiles: lista, edición de ventanas, simulador y feriados. */
export default function CalendarsSection({ canEdit }: { canEdit: boolean }) {
  const { data, error, loading, reload } = useApiResource<SlaCalendar[]>('/sla/calendars');
  const calendars = useMemo(() => data ?? [], [data]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const mutation = useMutation();

  useEffect(() => {
    if (selectedId === null && calendars.length > 0) setSelectedId(calendars[0].id);
    if (selectedId !== null && calendars.length > 0 && !calendars.some((c) => c.id === selectedId)) {
      setSelectedId(calendars[0].id);
    }
  }, [calendars, selectedId]);

  const selected = calendars.find((c) => c.id === selectedId) ?? null;

  async function create(value: SlaCalendarInput) {
    const created = await mutation.run(() => slaApi.createCalendar(value), `Calendario «${value.name}» creado.`);
    if (created) {
      setCreating(false);
      setSelectedId(created.id);
      void reload({ silent: true });
    }
  }

  async function update(value: SlaCalendarInput) {
    if (!selected) return;
    const updated = await mutation.run(() => slaApi.updateCalendar(selected.id, value), 'Calendario actualizado.');
    if (updated) void reload({ silent: true });
  }

  async function remove() {
    if (!selected) return;
    if (!window.confirm(`¿Eliminar el calendario «${selected.name}» y sus feriados?`)) return;
    const ok = await mutation.run(() => slaApi.deleteCalendar(selected.id).then(() => true), 'Calendario eliminado.');
    if (ok) {
      setSelectedId(null);
      void reload({ silent: true });
    }
  }

  if (loading && !data) return <Loading label="Cargando calendarios…" />;

  return (
    <div className="stack">
      <ErrorAlert error={error} onRetry={() => void reload()} />
      <Feedback mutation={mutation} />
      <div className={styles.calendarLayout}>
        <div className="stack-sm">
          <div className={styles.calendarList} role="list" aria-label="Calendarios">
            {calendars.map((c) => (
              <button
                key={c.id}
                type="button"
                role="listitem"
                aria-current={c.id === selectedId && !creating ? 'true' : undefined}
                className={`${styles.calendarItem} ${c.id === selectedId && !creating ? styles.calendarItemActive : ''}`}
                onClick={() => {
                  setCreating(false);
                  mutation.clear();
                  setSelectedId(c.id);
                }}
              >
                <span className="row-between">
                  <span className="strong">{c.name}</span>
                  {c.is_24x7 && <span className="badge badge-primary">24x7</span>}
                </span>
                <span className="text-xs muted">{describeWindows(c.windows)}</span>
                <span className="text-xs subtle">
                  {c.timezone} · {formatBusinessDuration(c.weekly_business_minutes * 60)}/semana · {c.holiday_count}{' '}
                  feriados · {c.policy_count} políticas
                </span>
              </button>
            ))}
            {calendars.length === 0 && !error && <EmptyState title="No hay calendarios" />}
          </div>
          {canEdit && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                mutation.clear();
                setCreating(true);
              }}
            >
              Nuevo calendario
            </button>
          )}
        </div>

        <div className="stack">
          {canEdit && creating ? (
            <CalendarForm
              key="new"
              initial={emptyCalendar()}
              calendarId={null}
              busy={mutation.busy}
              onSubmit={(v) => void create(v)}
              onCancel={() => setCreating(false)}
            />
          ) : selected ? (
            <>
              {canEdit ? (
                <CalendarForm
                  key={`${selected.id}-${selected.updated_at}`}
                  initial={{
                    name: selected.name,
                    description: selected.description,
                    timezone: selected.timezone,
                    windows: selected.windows,
                  }}
                  calendarId={selected.id}
                  busy={mutation.busy}
                  onSubmit={(v) => void update(v)}
                  onDelete={selected.policy_count === 0 ? () => void remove() : undefined}
                />
              ) : (
                <section className="panel stack-sm">
                  <h3 className="panel-title">{selected.name}</h3>
                  {selected.description && <p className="text-sm muted">{selected.description}</p>}
                  <WeekStrip windows={selected.windows} />
                  <p className="text-sm">
                    {describeWindows(selected.windows)} · {selected.timezone}
                  </p>
                </section>
              )}
              <PreviewTool calendar={selected} />
              <HolidaysPanel
                key={selected.id}
                calendar={selected}
                canEdit={canEdit}
                onChanged={() => void reload({ silent: true })}
              />
            </>
          ) : (
            !error && <EmptyState title="Seleccione un calendario" />
          )}
        </div>
      </div>
    </div>
  );
}
