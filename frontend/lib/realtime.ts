'use client';

/**
 * Eventos de tickets en tiempo real (WebSocket `/ws`).
 *
 * - Una sola conexión WebSocket compartida por toda la aplicación (singleton con contador
 *   de suscriptores). Se abre con el primer suscriptor y se cierra 10 s después del último.
 * - Credenciales frescas en cada conexión: token OIDC vigente (se refresca si está por
 *   expirar) o identidad de laboratorio en `NEXT_PUBLIC_AUTH_MODE=dev`.
 * - Reconexión con backoff exponencial y jitter; inmediata al volver la red o la pestaña.
 * - Antes de que expire el token se abre una conexión nueva y se cierra la anterior sin
 *   perder eventos. Tras una reconexión real (o si el servidor lo pide) los suscriptores
 *   reciben un evento sintético `realtime.resync` para que recarguen sus datos.
 *
 * Uso:
 *   useTicketEvents((event) => {...}, { ticketId: 12 });      // eventos de un ticket
 *   const { status } = useRealtimeStatus();                    // indicador de conexión
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { API_URL } from './api';
import { getRealtimeAuthParams, useAuth } from './auth';

/** Tipos de evento conocidos; los módulos pueden emitir otros (string libre). */
export type TicketEventType =
  | 'ticket.created'
  | 'ticket.updated'
  | 'ticket.assigned'
  | 'ticket.transitioned'
  | 'ticket.commented'
  /** El usuario dejó de ver el ticket (cambio de área/asignación). Sin datos del ticket. */
  | 'ticket.hidden'
  | 'sla.warning'
  | 'sla.breached'
  | 'approval.requested'
  | 'approval.decided'
  | 'attachment.added'
  | 'notification.created'
  /** Sintético (cliente): se pudieron perder eventos; recargar los datos mostrados. */
  | 'realtime.resync'
  | (string & {});

/** Payload publicado en Redis `servicedesk.events` y reenviado por `/ws`. */
export interface TicketEvent {
  type: TicketEventType;
  ticket_id: number;
  ticket_key: string;
  actor_id: number | null;
  data: Record<string, unknown>;
  /** Fecha ISO 8601 del evento. */
  ts: string;
  department_id: number | null;
  team_id: number | null;
  requester_id: number | null;
  assignee_id: number | null;
}

/** Filtro opcional aplicado del lado del cliente. */
export interface TicketEventFilter {
  /** Solo eventos de este ticket. */
  ticketId?: number;
  /** Solo estos tipos de evento (incluya 'realtime.resync' si desea ese aviso). */
  types?: TicketEventType[];
}

export type TicketEventHandler = (event: TicketEvent) => void;

export const RESYNC_EVENT: TicketEventType = 'realtime.resync';

// ---------- Estado de la conexión ----------

export type RealtimeStatus =
  /** Sin suscriptores o sin sesión. */
  | 'idle'
  | 'connecting'
  | 'open'
  /** Conexión perdida; reintentando con backoff. */
  | 'reconnecting'
  /** El navegador está sin red; se reconecta al volver. */
  | 'offline'
  /** Credenciales rechazadas varias veces; se sigue reintentando con calma. */
  | 'unauthorized'
  /** El servidor rechazó al usuario (p.ej. desactivado). No se reintenta. */
  | 'forbidden';

export interface RealtimeState {
  status: RealtimeStatus;
  /** Último motivo de error o cierre (en español), si lo hay. */
  lastError: string | null;
  /** Epoch ms de la última conexión establecida. */
  connectedAt: number | null;
  /** Epoch ms del próximo reintento programado. */
  retryAt: number | null;
  /** Reintentos consecutivos. */
  attempt: number;
}

const INITIAL_STATE: RealtimeState = { status: 'idle', lastError: null, connectedAt: null, retryAt: null, attempt: 0 };

// Códigos de cierre del servidor (ver backend/app/routers/ws.py).
const CLOSE_SESSION_EXPIRED = 4000;
const CLOSE_UNAUTHORIZED = 4401;
const CLOSE_FORBIDDEN = 4403;
const CLOSE_TRY_AGAIN_LATER = 1013;

const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
const UNAUTHORIZED_RETRY_MS = 60_000;
const IDLE_DISCONNECT_MS = 10_000;
/** Si no llega ningún mensaje (el servidor envía ping cada 25 s), la conexión se da por muerta. */
const WATCHDOG_MS = 70_000;
/** Margen para rotar la conexión antes de que expire el token. */
const ROTATE_BEFORE_EXPIRY_MS = 20_000;
const MIN_ROTATE_DELAY_MS = 5_000;
const HANDSHAKE_TIMEOUT_MS = 15_000;

interface Subscription {
  deliver: TicketEventHandler;
  ticketId?: number;
  types?: readonly string[];
}

interface ServerMessage {
  type?: unknown;
  expires_at?: unknown;
  [key: string]: unknown;
}

function closeReasonText(code: number, reason: string): string {
  if (reason) return reason;
  switch (code) {
    case CLOSE_UNAUTHORIZED:
      return 'Credenciales no válidas para la conexión en tiempo real.';
    case CLOSE_FORBIDDEN:
      return 'El servidor rechazó la conexión en tiempo real.';
    case CLOSE_TRY_AGAIN_LATER:
      return 'El servidor pidió reintentar más tarde.';
    case 1006:
      return 'La conexión en tiempo real se interrumpió.';
    default:
      return `Conexión cerrada (código ${code}).`;
  }
}

function backoffDelay(attempt: number): number {
  const ceiling = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.min(attempt, 10));
  // Jitter "equal": entre la mitad y el total del techo, evita reconexiones en manada.
  return Math.round(ceiling / 2 + (Math.random() * ceiling) / 2);
}

function buildSocketUrl(params: Record<string, string>): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  const url = new URL(explicit || `${API_URL}/ws`, window.location.href);
  if (url.protocol === 'http:') url.protocol = 'ws:';
  else if (url.protocol === 'https:') url.protocol = 'wss:';
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  return url.toString();
}

function matches(sub: Subscription, event: TicketEvent): boolean {
  if (sub.ticketId !== undefined && event.ticket_id !== sub.ticketId) return false;
  if (sub.types && !sub.types.includes(event.type)) return false;
  return true;
}

function isTicketEvent(value: ServerMessage): value is ServerMessage & TicketEvent {
  return typeof value.type === 'string' && typeof value.ticket_id === 'number';
}

class RealtimeClient {
  private subs = new Set<Subscription>();
  private listeners = new Set<() => void>();
  private state: RealtimeState = INITIAL_STATE;
  private authKey: string | null = null;

  /** Conexión activa (la que entrega eventos). */
  private socket: WebSocket | null = null;
  /** Conexión nueva abierta para reemplazar a la activa antes de que expire el token. */
  private successor: WebSocket | null = null;
  private connecting = false;
  private generation = 0;
  private attempt = 0;
  private hadConnection = false;
  private authFailures = 0;
  private recentExpiries: number[] = [];

  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setTimeout> | null = null;
  private rotateTimer: ReturnType<typeof setTimeout> | null = null;
  private windowListeners = false;

  // ---------- Estado observable ----------

  getState = (): RealtimeState => this.state;

  onStateChange = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private setState(patch: Partial<RealtimeState>): void {
    const next = { ...this.state, ...patch };
    const keys = Object.keys(next) as (keyof RealtimeState)[];
    if (keys.every((key) => next[key] === this.state[key])) return;
    this.state = next;
    for (const listener of Array.from(this.listeners)) listener();
  }

  // ---------- Suscripciones ----------

  subscribe(sub: Subscription, authKey: string): () => void {
    this.subs.add(sub);
    this.clearTimer('idle');
    this.setAuthKey(authKey);
    this.ensureConnected();
    return () => {
      this.subs.delete(sub);
      if (this.subs.size === 0) {
        this.clearTimer('idle');
        this.idleTimer = setTimeout(() => this.disconnect('idle'), IDLE_DISCONNECT_MS);
      }
    };
  }

  private setAuthKey(authKey: string): void {
    if (this.authKey === authKey) return;
    const changed = this.authKey !== null;
    this.authKey = authKey;
    if (changed) {
      // Otra identidad: nueva conexión con sus credenciales y sin "resync" heredado.
      this.disconnect('idle');
      this.hadConnection = false;
    }
  }

  /** Sin sesión: cierra la conexión (p.ej. tras cerrar sesión). */
  reset(): void {
    this.authKey = null;
    this.hadConnection = false;
    this.disconnect('idle');
  }

  // ---------- Entrega ----------

  private deliver(event: TicketEvent): void {
    for (const sub of Array.from(this.subs)) {
      if (!matches(sub, event)) continue;
      try {
        sub.deliver(event);
      } catch (error) {
        console.error('Error en un suscriptor de eventos en tiempo real', error);
      }
    }
  }

  private deliverResync(): void {
    const ts = new Date().toISOString();
    for (const sub of Array.from(this.subs)) {
      if (sub.types && !sub.types.includes(RESYNC_EVENT)) continue;
      const event: TicketEvent = {
        type: RESYNC_EVENT,
        ticket_id: sub.ticketId ?? 0,
        ticket_key: '',
        actor_id: null,
        data: {},
        ts,
        department_id: null,
        team_id: null,
        requester_id: null,
        assignee_id: null,
      };
      try {
        sub.deliver(event);
      } catch (error) {
        console.error('Error en un suscriptor de eventos en tiempo real', error);
      }
    }
  }

  // ---------- Conexión ----------

  private ensureConnected(): void {
    if (typeof window === 'undefined' || !this.authKey || this.subs.size === 0) return;
    if (this.socket || this.connecting || this.retryTimer) return;
    if (this.state.status === 'forbidden') return;
    this.installWindowListeners();
    void this.connect(false);
  }

  private async connect(rotation: boolean): Promise<void> {
    if (!rotation) {
      this.connecting = true;
      // Una rotación pendiente pierde sentido si se abre una conexión nueva desde cero.
      const orphan = this.successor;
      this.successor = null;
      if (orphan) {
        orphan.onclose = null;
        orphan.onmessage = null;
        orphan.close(1000, 'superseded');
      }
    }
    const generation = rotation ? this.generation : ++this.generation;
    const authKey = this.authKey;
    if (!rotation) {
      this.setState({ status: this.hadConnection || this.attempt > 0 ? 'reconnecting' : 'connecting', retryAt: null });
    }
    let params: Record<string, string>;
    try {
      params = await getRealtimeAuthParams();
    } catch {
      params = {};
    }
    // Cambió la identidad o se cerró todo mientras se obtenía el token.
    if (generation !== this.generation || authKey !== this.authKey || this.subs.size === 0) {
      if (!rotation) this.connecting = false;
      return;
    }
    if (Object.keys(params).length === 0) {
      if (rotation) return;
      this.connecting = false;
      this.authFailures += 1;
      this.scheduleRetry('Sin credenciales vigentes para la conexión en tiempo real.', this.authFailures >= 3);
      return;
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(buildSocketUrl(params));
    } catch (error) {
      if (rotation) return;
      this.connecting = false;
      this.scheduleRetry(error instanceof Error ? error.message : 'No se pudo abrir la conexión.', false);
      return;
    }
    if (rotation) this.successor = ws;
    else this.socket = ws;

    let ready = false;
    const handshakeTimer = setTimeout(() => {
      if (!ready) ws.close(4001, 'handshake timeout');
    }, HANDSHAKE_TIMEOUT_MS);

    ws.onmessage = (message: MessageEvent) => {
      if (generation !== this.generation) return;
      let data: ServerMessage;
      try {
        data = JSON.parse(typeof message.data === 'string' ? message.data : '') as ServerMessage;
      } catch {
        return;
      }
      if (ws === this.socket) this.armWatchdog();
      switch (data.type) {
        case 'ready':
          ready = true;
          clearTimeout(handshakeTimer);
          this.onReady(ws, data, rotation);
          return;
        case 'ping':
        case 'pong':
          return;
        case 'resync':
          if (ws === this.socket) this.deliverResync();
          return;
        default:
          // Durante una rotación, solo la conexión activa entrega eventos (evita duplicados).
          if (ws === this.socket && isTicketEvent(data)) this.deliver(data as TicketEvent);
      }
    };

    ws.onclose = (event: CloseEvent) => {
      clearTimeout(handshakeTimer);
      if (ws === this.successor) {
        this.successor = null;
        return;
      }
      if (ws !== this.socket || generation !== this.generation) return;
      this.socket = null;
      this.connecting = false;
      this.clearTimer('watchdog');
      this.clearTimer('rotate');
      this.onSocketClosed(event.code, event.reason);
    };

    ws.onerror = () => {
      // El detalle llega en onclose; los navegadores no exponen la causa del error.
    };
  }

  private onReady(ws: WebSocket, data: ServerMessage, rotation: boolean): void {
    if (rotation) {
      if (ws !== this.successor) return;
      const previous = this.socket;
      this.socket = ws;
      this.successor = null;
      if (previous) {
        previous.onclose = null;
        previous.onmessage = null;
        previous.close(1000, 'rotated');
      }
    } else {
      this.connecting = false;
      if (this.hadConnection) this.deliverResync();
    }
    this.hadConnection = true;
    this.attempt = 0;
    this.authFailures = 0;
    this.setState({ status: 'open', lastError: null, connectedAt: Date.now(), retryAt: null, attempt: 0 });
    this.armWatchdog();
    this.scheduleRotation(data.expires_at);
  }

  private onSocketClosed(code: number, reason: string): void {
    if (this.subs.size === 0 || !this.authKey) {
      this.setState({ status: 'idle', retryAt: null });
      return;
    }
    if (code === CLOSE_FORBIDDEN) {
      this.setState({ status: 'forbidden', lastError: closeReasonText(code, reason), retryAt: null });
      return;
    }
    if (code === CLOSE_SESSION_EXPIRED) {
      // Expiración planificada: reconectar enseguida con credenciales nuevas (salvo bucles).
      const now = Date.now();
      this.recentExpiries = this.recentExpiries.filter((t) => now - t < 10_000).concat(now);
      if (this.recentExpiries.length <= 3) {
        this.setState({ status: 'reconnecting', lastError: null });
        this.retryTimer = setTimeout(() => {
          this.retryTimer = null;
          this.ensureConnected();
        }, 250);
        return;
      }
    }
    if (code === CLOSE_UNAUTHORIZED) this.authFailures += 1;
    this.scheduleRetry(closeReasonText(code, reason), this.authFailures >= 3);
  }

  private scheduleRetry(message: string, unauthorized: boolean): void {
    this.clearTimer('retry');
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      this.setState({ status: 'offline', lastError: message, retryAt: null });
      return; // el evento "online" reconecta
    }
    const delay = unauthorized ? UNAUTHORIZED_RETRY_MS : backoffDelay(this.attempt);
    this.attempt += 1;
    this.setState({
      status: unauthorized ? 'unauthorized' : 'reconnecting',
      lastError: message,
      retryAt: Date.now() + delay,
      attempt: this.attempt,
    });
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.ensureConnected();
    }, delay);
  }

  private scheduleRotation(expiresAt: unknown): void {
    this.clearTimer('rotate');
    if (typeof expiresAt !== 'number' || !Number.isFinite(expiresAt)) return;
    const delay = expiresAt * 1000 - Date.now() - ROTATE_BEFORE_EXPIRY_MS;
    if (delay < MIN_ROTATE_DELAY_MS) return; // muy cerca: se reconecta al recibir 4000
    this.rotateTimer = setTimeout(() => {
      this.rotateTimer = null;
      if (this.socket && !this.successor && this.subs.size > 0) void this.connect(true);
    }, delay);
  }

  private armWatchdog(): void {
    this.clearTimer('watchdog');
    this.watchdogTimer = setTimeout(() => {
      // Sin mensajes ni pings: la conexión está muerta aunque el navegador no lo sepa.
      this.forceReconnect('No se recibieron mensajes del servidor.');
    }, WATCHDOG_MS);
  }

  /** Descarta la conexión actual y reconecta de inmediato. */
  private forceReconnect(message?: string): void {
    this.dropSockets('reconnect');
    this.clearTimer('retry');
    this.clearTimer('watchdog');
    this.clearTimer('rotate');
    if (message) this.setState({ lastError: message });
    this.ensureConnected();
  }

  /** Cierra las conexiones sin disparar su lógica de cierre e invalida las pendientes. */
  private dropSockets(reason: string): void {
    this.generation += 1;
    this.connecting = false;
    for (const ws of [this.socket, this.successor]) {
      if (!ws) continue;
      ws.onclose = null;
      ws.onmessage = null;
      try {
        ws.close(1000, reason);
      } catch {
        // ignorar: el socket ya estaba cerrado
      }
    }
    this.socket = null;
    this.successor = null;
  }

  private disconnect(nextStatus: RealtimeStatus): void {
    this.dropSockets('client closing');
    this.attempt = 0;
    this.authFailures = 0;
    for (const timer of ['retry', 'idle', 'watchdog', 'rotate'] as const) this.clearTimer(timer);
    this.setState({ status: nextStatus, retryAt: null, attempt: 0 });
  }

  private clearTimer(kind: 'retry' | 'idle' | 'watchdog' | 'rotate'): void {
    const key = `${kind}Timer` as const;
    const timer = this[key];
    if (timer) clearTimeout(timer);
    this[key] = null;
  }

  // ---------- Red y visibilidad ----------

  private installWindowListeners(): void {
    if (this.windowListeners || typeof window === 'undefined') return;
    this.windowListeners = true;
    window.addEventListener('online', () => {
      if (this.state.status === 'open' || this.subs.size === 0) return;
      this.attempt = 0;
      this.clearTimer('retry');
      this.ensureConnected();
    });
    window.addEventListener('offline', () => {
      if (this.socket) this.setState({ status: 'offline' });
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible' || this.subs.size === 0) return;
      if (this.state.status === 'reconnecting' || this.state.status === 'offline') {
        this.clearTimer('retry');
        this.ensureConnected();
      }
    });
  }
}

const client = new RealtimeClient();

/** Clave de identidad: si cambia (otro usuario o roles), se abre una conexión nueva. */
function useAuthKey(): string | null {
  const auth = useAuth();
  if (!auth.authenticated || !auth.user) return null;
  return `${auth.mode}:${auth.user.id}:${auth.user.sub}:${[...auth.user.roles].sort().join(',')}`;
}

/**
 * Se suscribe a los eventos de tickets visibles para el usuario actual.
 * El handler puede cambiar entre renders sin provocar una nueva suscripción.
 */
export function useTicketEvents(handler: TicketEventHandler, filter?: TicketEventFilter): void {
  const authKey = useAuthKey();
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });

  const ticketId = filter?.ticketId;
  const typesKey = filter?.types ? filter.types.join('|') : null;

  useEffect(() => {
    if (!authKey) return;
    const types = typesKey === null ? undefined : typesKey.split('|').filter(Boolean);
    return client.subscribe(
      { deliver: (event) => handlerRef.current(event), ticketId, types },
      authKey,
    );
  }, [authKey, ticketId, typesKey]);
}

/** Cierra la conexión compartida (p.ej. al cerrar sesión). */
export function resetRealtime(): void {
  client.reset();
}

/** Estado de la conexión en tiempo real (para indicadores de UI). */
export function useRealtimeStatus(): RealtimeState {
  return useSyncExternalStore(client.onStateChange, client.getState, () => INITIAL_STATE);
}

export const REALTIME_STATUS_LABELS: Record<RealtimeStatus, string> = {
  idle: 'Tiempo real inactivo',
  connecting: 'Conectando…',
  open: 'En vivo',
  reconnecting: 'Reconectando…',
  offline: 'Sin conexión',
  unauthorized: 'Sesión no válida para tiempo real',
  forbidden: 'Tiempo real no disponible',
};
