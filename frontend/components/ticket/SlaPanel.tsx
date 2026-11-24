'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import styles from '@/components/sla/sla.module.css';
import { SlaProgress } from '@/components/sla/shared';
import { ErrorAlert, Spinner } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { formatDateTime, formatRelative } from '@/lib/format';
import { useTicketEvents } from '@/lib/realtime';
import {
  SLA_EVENT_LABELS,
  SLA_METRIC_LABELS,
  SLA_METRICS,
  SLA_REASON_LABELS,
  SLA_STATUS_LABELS,
  TONE_BADGE_CLASS,
  consumedPct,
  formatBusinessDuration,
  formatTargetMinutes,
  remainingLabel,
  slaApi,
  slaTone,
  type SlaEvent,
  type SlaMetricInfo,
  type SlaTone,
  type TicketSla,
  type TicketSlaDetail,
} from '@/lib/sla';
import type { Ticket } from '@/lib/types';

const REFRESH_MS = 60_000;

const EVENT_TONES: Record<string, SlaTone> = {
  met: 'ok',
  started: 'ok',
  resumed: 'ok',
  reopened: 'ok',
  warning: 'warning',
  breached: 'breached',
  paused: 'paused',
};

function eventDetail(event: SlaEvent): string | null {
  const data = event.data ?? {};
  const parts: string[] = [];
  const reason = typeof data.reason === 'string' ? data.reason : null;
  if (reason) {
    if (reason.startsWith('state:')) parts.push(`estado «${reason.slice(6)}»`);
    else parts.push(SLA_REASON_LABELS[reason] ?? reason);
  }
  if (typeof data.policy_name === 'string' && event.type === 'started') parts.push(`política «${data.policy_name}»`);
  if (typeof data.new_policy_name === 'string') parts.push(`nueva política «${data.new_policy_name}»`);
  if (Array.isArray(data.pause) && data.pause.length === 2) {
    const [from, to] = data.pause as [string, string];
    const ms = new Date(to).getTime() - new Date(from).getTime();
    if (Number.isFinite(ms) && ms > 0) parts.push(`pausa de ${formatBusinessDuration(ms / 1000)} (tiempo real)`);
  }
  if (typeof data.target_at === 'string' && ['started', 'resumed', 'reopened'].includes(event.type)) {
    parts.push(`vence ${formatDateTime(data.target_at)}`);
  }
  return parts.length ? parts.join(' · ') : null;
}

/**
 * Relleno de la barra: % de tiempo hábil consumido mientras el reloj corre (verde/ámbar/rojo);
 * llena en verde si la métrica se cumplió y llena en rojo si venció.
 */
function progressFor(info: SlaMetricInfo, tone: SlaTone): { pct: number; tone: SlaTone; caption: string } {
  const consumed = consumedPct(info);
  const rounded = Math.round(consumed);
  if (info.status === 'cancelled') return { pct: 0, tone: 'cancelled', caption: 'Métrica cancelada' };
  if (!info.open) {
    if (info.breached || tone === 'breached') {
      return { pct: 100, tone: 'breached', caption: `Finalizada fuera de objetivo · ${rounded} % del tiempo hábil` };
    }
    return { pct: 100, tone: 'ok', caption: `Cumplida · ${rounded} % del tiempo hábil usado` };
  }
  if (tone === 'breached') return { pct: 100, tone, caption: `Objetivo excedido · ${rounded} % del tiempo hábil` };
  const base = `${rounded} % del tiempo hábil consumido`;
  if (info.paused) return { pct: consumed, tone, caption: `${base} · reloj en pausa` };
  if (consumed === 0) return { pct: 0, tone, caption: `${base} · el reloj avanza solo en horario hábil` };
  return { pct: consumed, tone, caption: base };
}

function MetricBlock({ info }: { info: SlaMetricInfo }) {
  const tone = slaTone(info);
  const progress = progressFor(info, tone);
  const label = SLA_METRIC_LABELS[info.metric];
  return (
    <div className={styles.metric}>
      <div className={styles.metricHead}>
        <span className={styles.metricTitle}>{label}</span>
        <span className={`badge ${TONE_BADGE_CLASS[info.status === 'met' ? 'ok' : tone]}`}>
          {tone === 'warning' ? 'En riesgo' : SLA_STATUS_LABELS[info.status]}
        </span>
      </div>
      <div className={`${styles.metricMain} ${styles[`tone-${tone}`] ?? ''}`}>{remainingLabel(info)}</div>
      <SlaProgress
        pct={progress.pct}
        tone={progress.tone}
        markerPct={info.open ? info.warning_threshold_pct : undefined}
        label={`${label}: ${progress.caption}`}
      />
      <div className={styles.progressCaption}>{progress.caption}</div>
      <dl className={styles.metricMeta}>
        <dt>Objetivo</dt>
        <dd>{formatTargetMinutes(info.target_minutes)} hábiles</dd>
        {info.open && info.target_at && (
          <>
            <dt>Vence</dt>
            <dd title={formatRelative(info.target_at)}>{formatDateTime(info.target_at)}</dd>
          </>
        )}
        {info.paused && info.paused_at && (
          <>
            <dt>En pausa desde</dt>
            <dd>{formatDateTime(info.paused_at)}</dd>
          </>
        )}
        {info.paused_business_seconds > 0 && (
          <>
            <dt>Tiempo en pausa</dt>
            <dd>{formatBusinessDuration(info.paused_business_seconds)} hábiles</dd>
          </>
        )}
        {info.breached_at && (
          <>
            <dt>Venció</dt>
            <dd>{formatDateTime(info.breached_at)}</dd>
          </>
        )}
        {!info.open && info.stopped_at && info.status !== 'cancelled' && (
          <>
            <dt>{info.metric === 'first_response' ? 'Respondido' : 'Resuelto'}</dt>
            <dd>{formatDateTime(info.stopped_at)}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

/**
 * Panel de SLA en el detalle del ticket: progreso de primera respuesta y resolución,
 * objetivos, pausas e historial del reloj. Se actualiza cada minuto y con eventos en tiempo real.
 */
export default function SlaPanel({ ticket }: { ticket: Ticket }) {
  const [detail, setDetail] = useState<TicketSlaDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controller.current?.abort();
    const ctrl = new AbortController();
    controller.current = ctrl;
    setLoading(true);
    try {
      const result = await slaApi.ticketDetail(ticket.id, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setDetail(result);
      setError(null);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setError(errorMessage(e));
    } finally {
      if (!ctrl.signal.aborted) setLoading(false);
    }
  }, [ticket.id]);

  // Recarga al cambiar el ticket (transiciones, comentarios, prioridad) y cada minuto.
  useEffect(() => {
    void load();
    const id = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') void load();
    }, REFRESH_MS);
    return () => {
      clearInterval(id);
      controller.current?.abort();
    };
  }, [load, ticket.updated_at, ticket.status.key, ticket.priority.key]);

  useTicketEvents(
    () => {
      void load();
    },
    { ticketId: ticket.id, types: ['sla.warning', 'sla.breached'] },
  );

  const sla: TicketSla | null = detail ?? ticket.sla ?? null;
  const metrics = sla ? SLA_METRICS.map((m) => sla[m]).filter((m): m is SlaMetricInfo => !!m) : [];
  const events = detail?.events ?? [];

  return (
    <section className="panel" aria-labelledby={`sla-panel-${ticket.id}`}>
      <div className="panel-header">
        <div>
          <h2 className="panel-title" id={`sla-panel-${ticket.id}`}>
            Nivel de servicio (SLA)
          </h2>
          <div className="panel-subtitle">
            {sla?.policy ? sla.policy.name : 'Sin política aplicable'}
            {metrics.length > 0 && ' · tiempos en horario hábil'}
          </div>
        </div>
        {loading && <Spinner label="Actualizando SLA" />}
      </div>

      <ErrorAlert error={error} onRetry={() => void load()} />

      {metrics.length === 0 ? (
        !loading && (
          <p className="text-sm muted">
            Ninguna política de SLA aplica a este ticket (prioridad, tipo o departamento sin objetivo configurado).
          </p>
        )
      ) : (
        <div>
          {metrics.map((info) => (
            <MetricBlock key={info.instance_id} info={info} />
          ))}
        </div>
      )}

      {events.length > 0 && (
        <details className={styles.history}>
          <summary>Historial del reloj ({events.length})</summary>
          <ol className={styles.eventList}>
            {[...events].reverse().map((event) => (
              <li key={event.id} className={styles.eventItem}>
                <span className={`${styles.eventDot} ${styles[`tone-${EVENT_TONES[event.type] ?? 'done'}`] ?? ''}`} aria-hidden="true" />
                <div>
                  <div>
                    <span className={styles.eventTitle}>{SLA_EVENT_LABELS[event.type] ?? event.type}</span>{' '}
                    <span className="subtle">· {SLA_METRIC_LABELS[event.metric]}</span>
                  </div>
                  <div className="subtle">{formatDateTime(event.at)}</div>
                  {eventDetail(event) && <div className="muted">{eventDetail(event)}</div>}
                </div>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
