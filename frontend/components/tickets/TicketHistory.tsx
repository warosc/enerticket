'use client';

import { EmptyState } from '@/components/ui';
import { formatDateTime } from '@/lib/format';
import type { Catalog, JsonObject, TicketHistoryEntry, UserRef } from '@/lib/types';

const EVENT_LABELS: Record<string, string> = {
  'ticket.created': 'Creó el ticket',
  'ticket.updated': 'Actualizó el ticket',
  'ticket.assigned': 'Cambió la asignación',
  'ticket.transitioned': 'Cambió el estado',
  'ticket.commented': 'Agregó un comentario',
  'sla.warning': 'Alerta de SLA',
  'sla.breached': 'SLA incumplido',
  'approval.requested': 'Solicitó aprobación',
  'approval.decided': 'Registró una decisión de aprobación',
  'approval.cancelled': 'Canceló una solicitud de aprobación',
  'attachment.added': 'Adjuntó un archivo',
  'attachment.deleted': 'Eliminó un adjunto',
  'notification.created': 'Generó una notificación',
};

const FIELD_LABELS: Record<string, string> = {
  title: 'Título',
  description: 'Descripción',
  priority: 'Prioridad',
  status: 'Estado',
  type: 'Tipo',
  department_id: 'Departamento',
  team_id: 'Equipo',
  assignee_id: 'Asignado',
  project_id: 'Proyecto',
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function describeValue(field: string, value: unknown, catalog: Catalog | null, users: UserRef[]): string {
  if (value === null || value === undefined || value === '') return 'vacío';
  if (catalog) {
    switch (field) {
      case 'priority':
        return catalog.priorities.find((p) => p.key === value)?.name ?? String(value);
      case 'status':
        return catalog.workflow.states.find((s) => s.key === value)?.name ?? String(value);
      case 'type':
        return catalog.ticket_types.find((t) => t.key === value)?.name ?? String(value);
      case 'department_id':
        return catalog.departments.find((d) => d.id === value)?.name ?? `#${String(value)}`;
      case 'team_id':
        return catalog.teams.find((t) => t.id === value)?.name ?? `#${String(value)}`;
      case 'project_id': {
        const project = catalog.projects.find((p) => p.id === value);
        return project ? `${project.code} — ${project.name}` : `#${String(value)}`;
      }
      default:
        break;
    }
  }
  if (field === 'assignee_id') return users.find((u) => u.id === value)?.display_name ?? `usuario #${String(value)}`;
  if (field === 'description') return '(texto actualizado)';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function describeChanges(entry: TicketHistoryEntry, catalog: Catalog | null, users: UserRef[]): string[] {
  const before = isObject(entry.before) ? entry.before : {};
  const after = isObject(entry.after) ? entry.after : {};

  if (entry.event_type === 'ticket.created') return [];
  if (entry.event_type === 'ticket.commented') {
    return after.visibility === 'internal' ? ['Comentario interno'] : ['Comentario público'];
  }
  const text = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : null);
  if (entry.event_type === 'approval.decided') {
    const decision = after.decision === 'approved' ? 'Aprobó' : after.decision === 'rejected' ? 'Rechazó' : null;
    const step = text(after.step_name);
    const lines = [[decision, step ? `etapa «${step}»` : null].filter(Boolean).join(' ')].filter(Boolean);
    if (after.request_status === 'approved') lines.push('Solicitud aprobada');
    if (after.request_status === 'rejected') lines.push('Solicitud rechazada');
    const comment = text(after.comment);
    if (comment) lines.push(`«${comment}»`);
    return lines;
  }
  if (entry.event_type === 'approval.requested') {
    const parts = [text(after.transition_name), text(after.step_name) ? `etapa «${text(after.step_name)}»` : null];
    return [parts.filter(Boolean).join(' · ')].filter(Boolean);
  }
  if (entry.event_type === 'approval.cancelled') {
    const reason = text(after.reason);
    return reason ? [`«${reason}»`] : [];
  }
  if (entry.event_type === 'attachment.added' || entry.event_type === 'attachment.deleted') {
    const filename = text(after.filename);
    return filename ? [filename] : [];
  }
  if (entry.event_type === 'sla.warning' || entry.event_type === 'sla.breached') {
    const metric = text(after.metric_label) ?? text(after.metric);
    return metric ? [metric] : [];
  }

  const lines: string[] = [];
  for (const field of Object.keys(FIELD_LABELS)) {
    // El cambio de asignado tiene su propio evento (ticket.assigned); no se repite en ticket.updated.
    if (entry.event_type === 'ticket.updated' && field === 'assignee_id') continue;
    if (!(field in after) && !(field in before)) continue;
    const from = before[field];
    const to = after[field];
    if (JSON.stringify(from) === JSON.stringify(to)) continue;
    if (field === 'description') {
      lines.push('Descripción actualizada');
      continue;
    }
    lines.push(
      `${FIELD_LABELS[field]}: ${describeValue(field, from, catalog, users)} → ${describeValue(field, to, catalog, users)}`,
    );
  }
  if (lines.length === 0 && typeof after.comment === 'string' && after.comment) lines.push(`«${after.comment}»`);
  return lines;
}

export default function TicketHistory({
  entries,
  catalog,
  users,
}: {
  entries: TicketHistoryEntry[];
  catalog: Catalog | null;
  users: UserRef[];
}) {
  if (entries.length === 0) return <EmptyState title="Sin movimientos registrados" />;
  const ordered = [...entries].sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : b.id - a.id));
  const rows = ordered
    .map((entry) => ({ entry, changes: describeChanges(entry, catalog, users) }))
    // Una actualización cuyo único cambio fue el asignado ya aparece como "Cambió la asignación".
    .filter(({ entry, changes }) => entry.event_type !== 'ticket.updated' || changes.length > 0);
  return (
    <ol className="timeline">
      {rows.map(({ entry, changes }) => {
        return (
          <li key={entry.id} className="timeline-item">
            <div>
              <span className="strong">{entry.actor?.display_name ?? 'Sistema'}</span>{' '}
              <span className="muted">{(EVENT_LABELS[entry.event_type] ?? entry.event_type).toLowerCase()}</span>
            </div>
            <div className="timeline-time">{formatDateTime(entry.timestamp)}</div>
            {changes.map((line) => (
              <div key={line} className="timeline-detail">
                {line}
              </div>
            ))}
          </li>
        );
      })}
    </ol>
  );
}
