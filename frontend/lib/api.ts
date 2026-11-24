/**
 * Cliente HTTP de la API del Service Desk.
 *
 * - `apiFetch<T>(path, init)` añade las cabeceras de autenticación (Bearer en OIDC,
 *   X-Dev-* en modo laboratorio), serializa JSON, interpreta la respuesta y lanza
 *   `ApiError` con un mensaje legible en español.
 * - `apiFetchRaw(path, init)` devuelve el `Response` sin interpretar (descargas, CSV).
 *
 * La autenticación se inyecta desde lib/auth.tsx mediante `configureApiAuth`, de modo que
 * este módulo no depende de React ni de keycloak-js.
 */
import type { ApiErrorBody } from './types';

export const API_URL: string = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/+$/, '');

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue | QueryValue[]>;

export interface ApiFetchInit extends Omit<RequestInit, 'body'> {
  /** Objeto a serializar como JSON, o un BodyInit nativo (FormData, Blob, string...). */
  body?: unknown;
  /** Parámetros de query; se omiten los valores null/undefined/''. */
  query?: QueryParams;
  /** false para no enviar credenciales (por defecto true). */
  auth?: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  readonly detail: unknown;

  constructor(message: string, status: number, path: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.path = path;
    this.detail = detail;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

// ---------- Inyección de autenticación ----------

export type AuthHeadersProvider = () => Promise<Record<string, string>>;

interface ApiAuthConfig {
  getHeaders: AuthHeadersProvider;
  onUnauthorized?: () => void;
}

let authConfig: ApiAuthConfig | null = null;

/** Lo invoca lib/auth.tsx; no es necesario llamarlo desde los módulos. */
export function configureApiAuth(config: ApiAuthConfig | null): void {
  authConfig = config;
}

// ---------- Utilidades ----------

export function buildQuery(query?: QueryParams): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, raw] of Object.entries(query)) {
    const values = Array.isArray(raw) ? raw : [raw];
    for (const value of values) {
      if (value === null || value === undefined || value === '') continue;
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export function apiUrl(path: string, query?: QueryParams): string {
  if (/^https?:\/\//i.test(path)) return path + buildQuery(query);
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${API_URL}${normalized}${buildQuery(query)}`;
}

function isNativeBody(body: unknown): body is BodyInit {
  return (
    typeof body === 'string' ||
    (typeof FormData !== 'undefined' && body instanceof FormData) ||
    (typeof Blob !== 'undefined' && body instanceof Blob) ||
    (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) ||
    (typeof ArrayBuffer !== 'undefined' && body instanceof ArrayBuffer) ||
    (typeof ReadableStream !== 'undefined' && body instanceof ReadableStream)
  );
}

const STATUS_MESSAGES: Record<number, string> = {
  400: 'La solicitud no es válida.',
  401: 'Su sesión expiró o no es válida. Inicie sesión nuevamente.',
  403: 'No tiene permisos para realizar esta acción.',
  404: 'El recurso solicitado no existe o no tiene acceso a él.',
  409: 'La operación entra en conflicto con el estado actual del recurso.',
  413: 'El archivo o contenido enviado excede el tamaño permitido.',
  422: 'Los datos enviados no son válidos.',
  429: 'Demasiadas solicitudes. Intente de nuevo en unos segundos.',
};

function describeValidationErrors(detail: Array<{ loc?: Array<string | number>; msg?: string }>): string {
  const parts = detail
    .map((item) => {
      const field = (item.loc ?? []).filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.');
      const msg = item.msg ?? 'valor inválido';
      return field ? `${field}: ${msg}` : msg;
    })
    .filter(Boolean);
  return parts.length ? `Datos inválidos — ${parts.join('; ')}` : STATUS_MESSAGES[422];
}

function messageFor(status: number, body: unknown): string {
  const detail = (body as ApiErrorBody | null)?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) return describeValidationErrors(detail);
  if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message;
  }
  if (STATUS_MESSAGES[status]) return STATUS_MESSAGES[status];
  if (status >= 500) return `Error interno del servidor (${status}). Intente de nuevo más tarde.`;
  return `Error inesperado de la API (${status}).`;
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined;
  const contentType = response.headers.get('content-type') ?? '';
  const text = await response.text();
  if (!text) return undefined;
  if (contentType.includes('json')) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return text;
}

/** Devuelve el Response sin interpretar el cuerpo, pero lanza ApiError si no es 2xx. */
export async function apiFetchRaw(path: string, init: ApiFetchInit = {}): Promise<Response> {
  const { body, query, auth = true, headers: initHeaders, ...rest } = init;
  const headers = new Headers(initHeaders);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');

  let requestBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (isNativeBody(body)) {
      requestBody = body;
    } else {
      requestBody = JSON.stringify(body);
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    }
  }

  if (auth && authConfig) {
    const authHeaders = await authConfig.getHeaders();
    for (const [key, value] of Object.entries(authHeaders)) {
      if (!headers.has(key)) headers.set(key, value);
    }
  }

  const url = apiUrl(path, query);
  let response: Response;
  try {
    response = await fetch(url, { ...rest, headers, body: requestBody });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(
      `No se pudo conectar con la API (${API_URL}). Verifique su conexión o que el servicio esté disponible.`,
      0,
      path,
      error,
    );
  }

  if (!response.ok) {
    const errorBody = await readBody(response).catch(() => undefined);
    if (response.status === 401 && auth) authConfig?.onUnauthorized?.();
    throw new ApiError(messageFor(response.status, errorBody), response.status, path, errorBody);
  }
  return response;
}

/** Llama a la API y devuelve el cuerpo JSON tipado (undefined en 204). */
export async function apiFetch<T = unknown>(path: string, init: ApiFetchInit = {}): Promise<T> {
  const response = await apiFetchRaw(path, init);
  return (await readBody(response)) as T;
}

/** Mensaje legible para cualquier error capturado. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === 'AbortError') return 'La solicitud fue cancelada.';
  if (error instanceof Error && error.message) return error.message;
  return 'Ocurrió un error inesperado.';
}

/** Normaliza respuestas de lista que pueden venir como arreglo o como `{items}`. */
export function asList<T>(data: T[] | { items?: T[] } | null | undefined): T[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}
