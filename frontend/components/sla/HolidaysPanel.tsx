'use client';

import { useMemo, useState, type FormEvent } from 'react';
import { EmptyState, ErrorAlert, Loading } from '@/components/ui';
import { formatDate } from '@/lib/format';
import { useApiResource } from '@/lib/hooks';
import { slaApi, type SlaCalendar, type SlaHoliday } from '@/lib/sla';
import { Feedback, useMutation } from './shared';

const WEEKDAY_FMT = new Intl.DateTimeFormat('es-GT', { weekday: 'long', timeZone: 'UTC' });

function weekdayOf(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? '' : WEEKDAY_FMT.format(date);
}

function displayDate(holiday: SlaHoliday, year: number): string {
  // Recurrentes: se muestran en el año consultado.
  const iso = holiday.recurring ? `${year}${holiday.date.slice(4)}` : holiday.date;
  return formatDate(`${iso}T12:00:00`);
}

/** Feriados de un calendario: consulta por año, alta manual, importación oficial y baja. */
export default function HolidaysPanel({
  calendar,
  canEdit,
  onChanged,
}: {
  calendar: SlaCalendar;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const currentYear = new Date().getFullYear();
  const years = useMemo(() => Array.from({ length: 6 }, (_, i) => currentYear - 1 + i), [currentYear]);
  const [year, setYear] = useState(currentYear);
  const query = useMemo(() => ({ year }), [year]);
  const { data, error, loading, reload } = useApiResource<SlaHoliday[]>(
    `/sla/calendars/${calendar.id}/holidays`,
    query,
  );
  const mutation = useMutation();
  const [form, setForm] = useState({ date: '', name: '', recurring: false });

  const holidays = useMemo(() => {
    const list = [...(data ?? [])];
    const key = (h: SlaHoliday) => (h.recurring ? `${year}${h.date.slice(4)}` : h.date);
    return list.sort((a, b) => key(a).localeCompare(key(b)));
  }, [data, year]);

  async function refresh() {
    await reload({ silent: true });
    onChanged();
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const name = form.name.trim();
    if (!form.date || name.length < 2) return;
    const created = await mutation.run(
      () => slaApi.createHoliday(calendar.id, { date: form.date, name, recurring: form.recurring }),
      `Feriado «${name}» agregado.`,
    );
    if (created) {
      setForm({ date: '', name: '', recurring: false });
      void refresh();
    }
  }

  async function importYear() {
    const result = await mutation.run(
      () => slaApi.importHolidays(calendar.id, [year]),
      (r) =>
        r.created === 0
          ? `Los feriados oficiales de ${year} ya estaban registrados.`
          : `Se agregaron ${r.created} feriados oficiales de ${year}.`,
    );
    if (result) void refresh();
  }

  async function toggleRecurring(holiday: SlaHoliday) {
    const updated = await mutation.run(
      () => slaApi.updateHoliday(holiday.id, { recurring: !holiday.recurring }),
      holiday.recurring ? 'El feriado ya no se repite cada año.' : 'El feriado se repetirá cada año.',
    );
    if (updated) void refresh();
  }

  async function remove(holiday: SlaHoliday) {
    if (!window.confirm(`¿Eliminar el feriado «${holiday.name}»?`)) return;
    const ok = await mutation.run(() => slaApi.deleteHoliday(holiday.id).then(() => true), 'Feriado eliminado.');
    if (ok) void refresh();
  }

  return (
    <section className="panel stack">
      <div className="row-between">
        <div>
          <h3 className="panel-title">Feriados</h3>
          <p className="panel-subtitle">
            Días completos sin servicio en «{calendar.name}». Los recurrentes se repiten cada año.
          </p>
        </div>
        <div className="row-sm">
          <label className="row-sm">
            <span className="text-sm muted">Año</span>
            <select
              className="select select-sm"
              style={{ width: 'auto' }}
              value={year}
              onChange={(e) => setYear(Number(e.target.value))}
            >
              {years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
          {canEdit && (
            <button type="button" className="btn btn-sm btn-secondary" onClick={() => void importYear()} disabled={mutation.busy}>
              Importar oficiales de Guatemala {year}
            </button>
          )}
        </div>
      </div>

      {calendar.is_24x7 && (
        <p className="text-sm muted">
          Este calendario es 24x7: los feriados que agregue sí interrumpirán el servicio ese día.
        </p>
      )}

      <Feedback mutation={mutation} />
      <ErrorAlert error={error} onRetry={() => void reload()} />

      {loading && !data ? (
        <Loading label="Cargando feriados…" />
      ) : holidays.length === 0 ? (
        !error && <EmptyState title={`Sin feriados en ${year}`}>Agregue feriados o importe los oficiales.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th scope="col">Fecha</th>
                <th scope="col">Nombre</th>
                <th scope="col">Tipo</th>
                {canEdit && (
                  <th scope="col" className="text-right">
                    Acciones
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {holidays.map((h) => {
                const iso = h.recurring ? `${year}${h.date.slice(4)}` : h.date;
                return (
                  <tr key={h.id}>
                    <td className="nowrap">
                      {displayDate(h, year)}
                      <div className="text-xs subtle">{weekdayOf(iso)}</div>
                    </td>
                    <td>{h.name}</td>
                    <td>
                      <span className={`badge ${h.recurring ? 'badge-primary' : 'badge-neutral'}`}>
                        {h.recurring ? 'Cada año' : 'Fecha fija'}
                      </span>
                    </td>
                    {canEdit && (
                      <td className="text-right nowrap">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => void toggleRecurring(h)}
                          disabled={mutation.busy}
                        >
                          {h.recurring ? 'Solo esta fecha' : 'Repetir cada año'}
                        </button>{' '}
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => void remove(h)}
                          disabled={mutation.busy}
                        >
                          Eliminar
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {canEdit && (
        <form className="toolbar" onSubmit={add} aria-label="Agregar feriado">
          <label className="field">
            <span className="field-label">Fecha</span>
            <input
              className="input"
              type="date"
              value={form.date}
              required
              onChange={(e) => setForm({ ...form, date: e.target.value })}
            />
          </label>
          <label className="field field-search">
            <span className="field-label">Nombre</span>
            <input
              className="input"
              value={form.name}
              required
              minLength={2}
              maxLength={150}
              placeholder="p. ej. Feria patronal"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label className="checkbox" style={{ alignSelf: 'flex-end', minHeight: 38 }}>
            <input
              type="checkbox"
              checked={form.recurring}
              onChange={(e) => setForm({ ...form, recurring: e.target.checked })}
            />
            Cada año
          </label>
          <button
            type="submit"
            className="btn btn-primary"
            style={{ alignSelf: 'flex-end' }}
            disabled={mutation.busy || !form.date || form.name.trim().length < 2}
          >
            Agregar feriado
          </button>
        </form>
      )}
    </section>
  );
}
