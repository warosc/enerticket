'use client';

/**
 * Tarjeta de una solicitud de aprobación: transición, motivo, etapas con sus decisiones y,
 * si el usuario puede decidir, el formulario Aprobar / Rechazar (el rechazo exige motivo).
 * La usan la bandeja (/approvals) y el panel del ticket.
 */
import Link from 'next/link';
import { useState } from 'react';
import { FlowIcon } from '@/components/notifications/FlowIcon';
import { Alert, PriorityBadge, StatusBadge } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import {
  APPROVAL_STATUS_BADGE,
  APPROVAL_STATUS_LABELS,
  cancelApproval,
  decideApproval,
  DECISION_LABELS,
  RULE_LABELS,
  STEP_STATUS_LABELS,
  type ApprovalRequest,
  type ApprovalStep,
} from '@/lib/flows';
import { formatDateTime, formatRelative } from '@/lib/format';
import styles from './approvals.module.css';

const MARKER_CLASS: Record<ApprovalStep['status'], string> = {
  waiting: '',
  pending: styles.markerPending,
  approved: styles.markerApproved,
  rejected: styles.markerRejected,
  cancelled: styles.markerCancelled,
};

function StepItem({ step }: { step: ApprovalStep }) {
  const decided = step.decisions.filter((d) => d.decision === 'approved' || d.decision === 'rejected');
  const pending = step.decisions.filter((d) => d.decision === 'pending');
  return (
    <li className={styles.step}>
      <span className={`${styles.stepMarker} ${MARKER_CLASS[step.status]}`} aria-hidden="true">
        {step.status === 'approved' ? (
          <FlowIcon name="check" width={14} height={14} />
        ) : step.status === 'rejected' ? (
          <FlowIcon name="x" width={14} height={14} />
        ) : (
          step.order
        )}
      </span>
      <div style={{ minWidth: 0 }}>
        <div className={styles.stepHead}>
          <span className={styles.stepName}>{step.name}</span>
          <span className={`badge ${step.status === 'pending' ? 'badge-warning' : step.status === 'approved' ? 'badge-success' : step.status === 'rejected' ? 'badge-danger' : 'badge-neutral'}`}>
            {STEP_STATUS_LABELS[step.status]}
          </span>
        </div>
        <div className={styles.stepMeta}>
          {step.approver_label} · {RULE_LABELS[step.rule]}
        </div>
        {(decided.length > 0 || pending.length > 0 || step.decisions.length === 0) && (
          <ul className={styles.decisions}>
            {decided.map((d) => (
              <li key={d.id} className={styles.decision}>
                <span className={styles.decisionName}>{d.approver.display_name}</span>
                <span className={d.decision === 'approved' ? styles.decisionApproved : styles.decisionRejected}>
                  {DECISION_LABELS[d.decision]}
                  {d.is_override ? ' (como administrador)' : ''}
                </span>
                <time dateTime={d.decided_at ?? undefined} title={formatDateTime(d.decided_at)}>
                  {formatRelative(d.decided_at)}
                </time>
                {d.comment && <span className={styles.decisionComment}>“{d.comment}”</span>}
              </li>
            ))}
            {step.status === 'pending' && pending.length > 0 && (
              <li className={styles.decision}>
                Esperando a: {pending.map((d) => d.approver.display_name).join(', ')}
              </li>
            )}
            {step.status === 'pending' && step.decisions.length === 0 && (
              <li className={styles.decision}>Sin aprobadores designados: puede decidir un administrador.</li>
            )}
          </ul>
        )}
      </div>
    </li>
  );
}

export default function ApprovalCard({
  request,
  onChanged,
  showTicket = true,
}: {
  request: ApprovalRequest;
  /** Se invoca con la solicitud actualizada tras decidir o cancelar. */
  onChanged: (updated: ApprovalRequest) => void;
  showTicket?: boolean;
}) {
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState<'approve' | 'reject' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);

  async function decide(decision: 'approve' | 'reject') {
    if (decision === 'reject' && !comment.trim()) {
      setError('Indique el motivo del rechazo en el comentario.');
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      const updated = await decideApproval(request.id, decision, comment);
      setComment('');
      onChanged(updated);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    setBusy('cancel');
    setError(null);
    try {
      const updated = await cancelApproval(request.id, comment);
      setConfirmCancel(false);
      onChanged(updated);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const steps = [...request.steps].sort((a, b) => a.order - b.order);
  const actionable = request.can_decide;

  return (
    <article className={`${styles.card} ${actionable ? styles.cardActionable : ''}`} aria-label={`Solicitud de aprobación ${request.id}`}>
      <div className={styles.cardHeader}>
        <div style={{ minWidth: 0 }}>
          {showTicket ? (
            <div className={styles.cardTitle}>
              <Link href={`/tickets/${request.ticket.id}`} className="mono">
                {request.ticket.key}
              </Link>{' '}
              {request.ticket.title}
            </div>
          ) : (
            <div className={styles.cardTitle}>Solicitud #{request.id}</div>
          )}
          <div className={styles.cardSub}>
            {request.requested_by ? `Solicitada por ${request.requested_by.display_name}` : 'Solicitada por el sistema'} ·{' '}
            <time dateTime={request.created_at} title={formatDateTime(request.created_at)}>
              {formatRelative(request.created_at)}
            </time>
            {request.policy ? ` · ${request.policy.name}` : ''}
          </div>
        </div>
        <div className="row-sm">
          {showTicket && (
            <>
              <StatusBadge status={request.ticket.status} />
              <PriorityBadge priority={request.ticket.priority} />
            </>
          )}
          <span className={`badge ${APPROVAL_STATUS_BADGE[request.status]}`}>{APPROVAL_STATUS_LABELS[request.status]}</span>
        </div>
      </div>

      <div className={styles.transition}>
        <span className="muted">{request.transition.name}:</span>
        <span className={styles.stateChip}>{request.transition.from_state?.name ?? '—'}</span>
        <FlowIcon name="arrowRight" width={14} height={14} />
        <span className={styles.stateChip}>{request.transition.to_state?.name ?? '—'}</span>
        {showTicket && <span className="text-xs subtle">· {request.ticket.type.name} · {request.ticket.department.name}</span>}
      </div>

      {request.reason && <div className={styles.reason}>{request.reason}</div>}

      <ol className={styles.steps} aria-label="Etapas de aprobación">
        {steps.map((step) => (
          <StepItem key={step.id} step={step} />
        ))}
      </ol>

      {request.status === 'approved' && request.executed_at && (
        <Alert tone="success">Transición aplicada {formatRelative(request.executed_at)}.</Alert>
      )}
      {request.status === 'approved' && !request.executed_at && (
        <Alert tone="warning" title="Aprobada, pendiente de aplicar">
          {request.execution_error ??
            'La transición no se aplicó automáticamente. Un agente puede aplicarla desde el ticket sin volver a solicitar aprobación.'}
        </Alert>
      )}

      {error && <Alert tone="error">{error}</Alert>}

      {(request.can_decide || request.can_cancel) && (
        <div className={styles.decide}>
          <label className="field">
            <span className="field-label">
              Comentario {request.can_decide ? '(obligatorio para rechazar)' : '(opcional)'}
            </span>
            <textarea
              className="textarea"
              style={{ minHeight: 64 }}
              value={comment}
              maxLength={4000}
              onChange={(e) => setComment(e.target.value)}
              placeholder={request.can_decide ? 'Condiciones, observaciones o motivo del rechazo' : 'Motivo de la cancelación'}
            />
          </label>
          <div className={styles.decideActions}>
            {request.can_cancel &&
              (confirmCancel ? (
                <>
                  <span className="text-sm muted" style={{ alignSelf: 'center' }}>
                    ¿Cancelar la solicitud?
                  </span>
                  <button type="button" className="btn btn-sm" onClick={() => setConfirmCancel(false)} disabled={busy !== null}>
                    No
                  </button>
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => void cancel()} disabled={busy !== null}>
                    {busy === 'cancel' ? 'Cancelando…' : 'Sí, cancelar'}
                  </button>
                </>
              ) : (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmCancel(true)} disabled={busy !== null}>
                  Cancelar solicitud
                </button>
              ))}
            {request.can_decide && (
              <>
                <button type="button" className="btn btn-danger" onClick={() => void decide('reject')} disabled={busy !== null}>
                  <FlowIcon name="x" width={16} height={16} />
                  {busy === 'reject' ? 'Rechazando…' : 'Rechazar'}
                </button>
                <button type="button" className="btn btn-success" onClick={() => void decide('approve')} disabled={busy !== null}>
                  <FlowIcon name="check" width={16} height={16} />
                  {busy === 'approve' ? 'Aprobando…' : 'Aprobar'}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
