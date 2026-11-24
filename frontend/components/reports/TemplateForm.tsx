'use client';

/**
 * Formulario para guardar la definición actual como plantilla (nueva o existente): nombre, descripción,
 * compartir por rol/departamento y, solo para administradores o líderes de equipo, programación de envío.
 */
import { useId, useMemo, useState, type FormEvent } from 'react';
import { ErrorAlert } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import {
  REPORT_LIMITS,
  ROLE_LABELS,
  WEEKDAY_LABELS,
  type ReportCatalogOptions,
  type ReportOption,
  type ReportSchedule,
  type ReportScheduleFrequency,
  type ReportTemplate,
  type ReportTemplateInput,
} from '@/lib/reports';
import MultiSelect from './MultiSelect';
import styles from './builder.module.css';

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;
const SHAREABLE_ROLES = ['agent', 'approver', 'requester', 'admin'];

function defaultSchedule(): ReportSchedule {
  return { enabled: false, frequency: 'monthly', day: 1, hour: 7, recipients: [] };
}

function parseRecipients(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)
    .filter((s, i, all) => all.indexOf(s) === i);
}

export default function TemplateForm({
  template,
  defaultName,
  canSchedule,
  options,
  onSubmit,
  onCancel,
}: {
  /** Plantilla existente a actualizar; null para crear una nueva. */
  template: ReportTemplate | null;
  defaultName: string;
  canSchedule: boolean;
  options: ReportCatalogOptions;
  onSubmit: (input: Omit<ReportTemplateInput, 'definition'>) => Promise<void>;
  onCancel: () => void;
}) {
  const baseId = useId();
  const [name, setName] = useState(template?.name ?? defaultName);
  const [description, setDescription] = useState(template?.description ?? '');
  const [roles, setRoles] = useState<string[]>(template?.shared_roles ?? []);
  const [departmentIds, setDepartmentIds] = useState<number[]>(template?.shared_department_ids ?? []);
  const [schedule, setSchedule] = useState<ReportSchedule>(() => ({
    ...defaultSchedule(),
    ...(template?.schedule ?? {}),
  }));
  const [recipientsText, setRecipientsText] = useState((template?.schedule?.recipients ?? []).join(', '));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const departmentOptions = useMemo<ReportOption[]>(
    () => options.departments.map((d) => ({ value: d.id, label: d.name })),
    [options.departments],
  );

  const recipients = parseRecipients(recipientsText);
  const invalidRecipients = recipients.filter((r) => !EMAIL_RE.test(r));

  const validate = (): string | null => {
    if (!name.trim()) return 'Indique un nombre para la plantilla.';
    if (canSchedule && schedule.enabled) {
      if (!recipients.length) return 'Indique al menos un destinatario para el envío programado.';
      if (invalidRecipients.length) return `Correos no válidos: ${invalidRecipients.join(', ')}.`;
      if (recipients.length > REPORT_LIMITS.maxRecipients) {
        return `Máximo ${REPORT_LIMITS.maxRecipients} destinatarios.`;
      }
    }
    return null;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const input: Omit<ReportTemplateInput, 'definition'> = {
        name: name.trim(),
        description: description.trim(),
        shared_roles: roles,
        shared_department_ids: departmentIds,
      };
      // Sin permiso se omite (la API conserva la programación existente); desactivada se quita.
      if (canSchedule) input.schedule = schedule.enabled ? { ...schedule, recipients } : null;
      await onSubmit(input);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const setFrequency = (frequency: ReportScheduleFrequency) =>
    setSchedule((s) => ({ ...s, frequency, day: frequency === 'weekly' ? 0 : 1 }));

  return (
    <form className={`panel form ${styles.templateForm}`} onSubmit={(e) => void submit(e)} aria-labelledby={`${baseId}-t`}>
      <div className="panel-header" style={{ marginBottom: 0 }}>
        <h2 className="panel-title" id={`${baseId}-t`}>
          {template ? `Guardar cambios en «${template.name}»` : 'Guardar como plantilla'}
        </h2>
      </div>
      <ErrorAlert error={error} />
      <div className="form-grid">
        <label className="field">
          <span className="field-label">
            Nombre<span className="required">*</span>
          </span>
          <input className="input" value={name} maxLength={150} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="field">
          <span className="field-label">Descripción</span>
          <input
            className="input"
            value={description}
            maxLength={2000}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
      </div>

      <fieldset className={styles.fieldset}>
        <legend className="field-label">Compartir</legend>
        <p className="field-hint">
          Quien la reciba la ejecuta con su propio alcance: solo verá los tickets que ya puede ver. Sin selección, la
          plantilla es solo suya.
        </p>
        <div className="form-grid">
          <div className="field">
            <span className="field-label">Por rol</span>
            <div className="row">
              {SHAREABLE_ROLES.map((role) => (
                <label key={role} className="checkbox">
                  <input
                    type="checkbox"
                    checked={roles.includes(role)}
                    onChange={(e) =>
                      setRoles((prev) => (e.target.checked ? [...prev, role] : prev.filter((r) => r !== role)))
                    }
                  />
                  {ROLE_LABELS[role] ?? role}
                </label>
              ))}
            </div>
          </div>
          <MultiSelect
            label="Por departamento"
            options={departmentOptions}
            value={departmentIds}
            onChange={setDepartmentIds}
            placeholder="Ninguno"
          />
        </div>
      </fieldset>

      {canSchedule ? (
        <fieldset className={styles.fieldset}>
          <legend className="field-label">Envío programado por correo</legend>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={schedule.enabled}
              onChange={(e) => setSchedule((s) => ({ ...s, enabled: e.target.checked }))}
            />
            Enviar este reporte en Excel automáticamente
          </label>
          {schedule.enabled && (
            <>
              <div className={styles.scheduleGrid}>
                <label className="field">
                  <span className="field-label">Frecuencia</span>
                  <select
                    className="select"
                    value={schedule.frequency}
                    onChange={(e) => setFrequency(e.target.value as ReportScheduleFrequency)}
                  >
                    <option value="monthly">Mensual</option>
                    <option value="weekly">Semanal</option>
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">{schedule.frequency === 'weekly' ? 'Día de la semana' : 'Día del mes'}</span>
                  <select
                    className="select"
                    value={schedule.day}
                    onChange={(e) => setSchedule((s) => ({ ...s, day: Number(e.target.value) }))}
                  >
                    {schedule.frequency === 'weekly'
                      ? WEEKDAY_LABELS.map((label, i) => (
                          <option key={label} value={i}>
                            {label}
                          </option>
                        ))
                      : Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
                          <option key={d} value={d}>
                            {d}
                          </option>
                        ))}
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">Hora (Guatemala)</span>
                  <select
                    className="select"
                    value={schedule.hour}
                    onChange={(e) => setSchedule((s) => ({ ...s, hour: Number(e.target.value) }))}
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>
                        {String(h).padStart(2, '0')}:00
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="field">
                <span className="field-label">
                  Destinatarios<span className="required">*</span>
                </span>
                <textarea
                  className="textarea"
                  rows={2}
                  style={{ minHeight: 64 }}
                  value={recipientsText}
                  onChange={(e) => setRecipientsText(e.target.value)}
                  placeholder="correo@empresa.com, otro@empresa.com"
                />
                <span className={invalidRecipients.length ? 'field-error' : 'field-hint'}>
                  {invalidRecipients.length
                    ? `Correos no válidos: ${invalidRecipients.join(', ')}`
                    : `${recipients.length} de máx. ${REPORT_LIMITS.maxRecipients}. Separe con comas o saltos de línea. El reporte se calcula con su alcance.`}
                </span>
              </label>
            </>
          )}
        </fieldset>
      ) : (
        <p className="field-hint">El envío programado por correo está disponible para administradores y líderes de equipo.</p>
      )}

      <div className="form-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={saving}>
          Cancelar
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? 'Guardando…' : template ? 'Guardar cambios' : 'Guardar plantilla'}
        </button>
      </div>
    </form>
  );
}
