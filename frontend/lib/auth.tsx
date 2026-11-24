'use client';

/**
 * Autenticación del portal.
 *
 * - `NEXT_PUBLIC_AUTH_MODE=oidc` (por defecto): Keycloak con keycloak-js, PKCE S256,
 *   `check-sso` y, si no hay sesión, redirección a login (todas las rutas del portal
 *   están protegidas). El token se refresca automáticamente.
 * - `NEXT_PUBLIC_AUTH_MODE=dev` (solo laboratorio): identidad elegida en un selector y
 *   persistida en localStorage; se envía como cabeceras X-Dev-User / X-Dev-Roles / X-Dev-Groups.
 *
 * Tras autenticar se carga `/me`. keycloak-js se importa dinámicamente solo en el cliente.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type Keycloak from 'keycloak-js';
import { apiFetch, configureApiAuth, errorMessage } from './api';
import type { Me, Role } from './types';

export type AuthMode = 'oidc' | 'dev';

export const AUTH_MODE: AuthMode = process.env.NEXT_PUBLIC_AUTH_MODE === 'dev' ? 'dev' : 'oidc';

const KEYCLOAK_URL = (process.env.NEXT_PUBLIC_KEYCLOAK_URL || 'http://localhost:8081').replace(/\/+$/, '');
const KEYCLOAK_REALM = process.env.NEXT_PUBLIC_KEYCLOAK_REALM || 'energuate';
const KEYCLOAK_CLIENT_ID = process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID || 'servicedesk-web';

/** Segundos mínimos de validez restante antes de refrescar el token. */
const TOKEN_MIN_VALIDITY = 30;
const TOKEN_REFRESH_INTERVAL_MS = 20_000;
const LOGIN_LOOP_GUARD_KEY = 'esd.lastLoginRedirect';
const LOGIN_LOOP_WINDOW_MS = 30_000;

// ---------- Identidad de laboratorio ----------

export interface DevIdentity {
  /** Nombre de usuario (X-Dev-User). Caracteres permitidos: letras, dígitos, . _ @ - */
  user: string;
  roles: Role[];
  /** Grupos AD/Keycloak simulados (X-Dev-Groups), p.ej. GG-SD-Telecomunicaciones. */
  groups: string[];
  /** Nombre a mostrar opcional (X-Dev-Name). */
  name?: string;
  /** Correo opcional (X-Dev-Email). */
  email?: string;
}

export interface DevPreset extends DevIdentity {
  label: string;
  description: string;
}

/** Grupos de laboratorio; coinciden con el `external_group` sembrado por defecto. */
export const DEV_GROUPS: readonly string[] = [
  'GG-SD-Telecomunicaciones',
  'GG-SD-SeguridadRedes',
  'GG-SD-SOC',
  'GG-SD-Riesgo',
  'GG-SD-ProyectosTI',
  'GG-SD-General',
];

export const DEV_PRESETS: readonly DevPreset[] = [
  {
    label: 'Administrador',
    description: 'Acceso total y administración',
    user: 'lab.admin',
    name: 'Administrador de laboratorio',
    roles: ['admin', 'agent'],
    groups: [],
  },
  {
    label: 'Agente Telecomunicaciones',
    description: 'Atiende tickets del área de Telecomunicaciones',
    user: 'lab.agente.telecom',
    name: 'Agente Telecomunicaciones',
    roles: ['agent'],
    groups: ['GG-SD-Telecomunicaciones'],
  },
  {
    label: 'Agente SOC',
    description: 'Atiende tickets de Ciberseguridad / SOC',
    user: 'lab.agente.soc',
    name: 'Agente SOC',
    roles: ['agent'],
    groups: ['GG-SD-SOC'],
  },
  {
    label: 'Aprobador',
    description: 'Revisa aprobaciones dirigidas a él',
    user: 'lab.aprobador',
    name: 'Aprobador de laboratorio',
    roles: ['approver'],
    groups: ['GG-SD-Riesgo'],
  },
  {
    label: 'Solicitante',
    description: 'Crea y consulta sus propios tickets',
    user: 'lab.solicitante',
    name: 'Solicitante de laboratorio',
    roles: ['requester'],
    groups: [],
  },
];

const DEV_STORAGE_KEY = 'esd.devIdentity';
const DEV_USER_RE = /^[A-Za-z0-9._@-]{1,150}$/;
const KNOWN_ROLES: readonly Role[] = ['admin', 'agent', 'approver', 'requester'];

function sanitizeDevIdentity(value: unknown): DevIdentity | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Partial<Record<keyof DevIdentity, unknown>>;
  const user = typeof raw.user === 'string' ? raw.user.trim() : '';
  if (!DEV_USER_RE.test(user)) return null;
  const roles = Array.isArray(raw.roles)
    ? (raw.roles.filter((r): r is Role => typeof r === 'string' && (KNOWN_ROLES as string[]).includes(r)))
    : [];
  const groups = Array.isArray(raw.groups)
    ? raw.groups.filter((g): g is string => typeof g === 'string' && g.trim() !== '' && !g.includes(',')).map((g) => g.trim())
    : [];
  const name = typeof raw.name === 'string' && raw.name.trim() ? raw.name.trim().slice(0, 200) : undefined;
  const email = typeof raw.email === 'string' && raw.email.trim() ? raw.email.trim() : undefined;
  return { user, roles: Array.from(new Set(roles)), groups: Array.from(new Set(groups)), name, email };
}

function readStoredDevIdentity(): DevIdentity | null {
  try {
    const stored = window.localStorage.getItem(DEV_STORAGE_KEY);
    return stored ? sanitizeDevIdentity(JSON.parse(stored)) : null;
  } catch {
    return null;
  }
}

function writeStoredDevIdentity(identity: DevIdentity | null): void {
  try {
    if (identity) window.localStorage.setItem(DEV_STORAGE_KEY, JSON.stringify(identity));
    else window.localStorage.removeItem(DEV_STORAGE_KEY);
  } catch {
    // Almacenamiento no disponible (modo privado): la identidad dura solo esta pestaña.
  }
}

/** Valida una identidad de laboratorio; devuelve el mensaje de error o null. */
export function validateDevIdentity(identity: DevIdentity): string | null {
  if (!DEV_USER_RE.test(identity.user.trim())) {
    return 'El usuario solo admite letras, números y los caracteres . _ @ - (máximo 150).';
  }
  if (identity.groups.some((g) => g.includes(','))) return 'Los grupos no pueden contener comas.';
  return null;
}

// ---------- Estado compartido fuera de React (lo usan apiFetch y realtime) ----------

let keycloakInstance: Keycloak | null = null;
let keycloakInitPromise: Promise<Keycloak> | null = null;
let currentDevIdentity: DevIdentity | null = null;

function devHeaders(identity: DevIdentity): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Dev-User': identity.user,
    'X-Dev-Roles': identity.roles.join(','),
    'X-Dev-Groups': identity.groups.join(','),
  };
  // Las cabeceras HTTP solo admiten ISO-8859-1; se omite el nombre si trae otros caracteres.
  if (identity.name && /^[\x20-\x7e\xa0-\xff]*$/.test(identity.name)) headers['X-Dev-Name'] = identity.name;
  if (identity.email) headers['X-Dev-Email'] = identity.email;
  return headers;
}

/** Token de acceso vigente (lo refresca si está por expirar). null si no hay sesión OIDC. */
export async function getAccessToken(): Promise<string | null> {
  const kc = keycloakInstance;
  if (!kc || !kc.authenticated) return null;
  try {
    await kc.updateToken(TOKEN_MIN_VALIDITY);
  } catch {
    return null;
  }
  return kc.token ?? null;
}

/** Identidad de laboratorio activa (solo en modo dev). */
export function getDevIdentity(): DevIdentity | null {
  return AUTH_MODE === 'dev' ? currentDevIdentity : null;
}

/** Cabeceras de autenticación para peticiones HTTP a la API. */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  if (AUTH_MODE === 'dev') return currentDevIdentity ? devHeaders(currentDevIdentity) : {};
  const token = await getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Parámetros de query para autenticar el WebSocket `/ws`:
 * OIDC → `{token}`; dev → `{dev_user, dev_roles, dev_groups}`. Vacío si no hay sesión.
 */
export async function getRealtimeAuthParams(): Promise<Record<string, string>> {
  if (AUTH_MODE === 'dev') {
    const id = currentDevIdentity;
    return id ? { dev_user: id.user, dev_roles: id.roles.join(','), dev_groups: id.groups.join(',') } : {};
  }
  const token = await getAccessToken();
  return token ? { token } : {};
}

function recentlyRedirectedToLogin(): boolean {
  try {
    const last = Number(window.sessionStorage.getItem(LOGIN_LOOP_GUARD_KEY) || 0);
    return Date.now() - last < LOGIN_LOOP_WINDOW_MS;
  } catch {
    return false;
  }
}

function markLoginRedirect(): void {
  try {
    window.sessionStorage.setItem(LOGIN_LOOP_GUARD_KEY, String(Date.now()));
  } catch {
    // Sin sessionStorage no hay protección contra bucles, pero el login sigue funcionando.
  }
}

async function redirectToLogin(kc: Keycloak): Promise<void> {
  markLoginRedirect();
  await kc.login({ redirectUri: window.location.href });
}

function initKeycloak(): Promise<Keycloak> {
  if (!keycloakInitPromise) {
    keycloakInitPromise = (async () => {
      const { default: KeycloakCtor } = await import('keycloak-js');
      const kc = new KeycloakCtor({ url: KEYCLOAK_URL, realm: KEYCLOAK_REALM, clientId: KEYCLOAK_CLIENT_ID });
      await kc.init({
        onLoad: 'check-sso',
        pkceMethod: 'S256',
        checkLoginIframe: false,
        silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
        silentCheckSsoFallback: true,
      });
      keycloakInstance = kc;
      return kc;
    })().catch((error: unknown) => {
      keycloakInitPromise = null;
      throw error;
    });
  }
  return keycloakInitPromise;
}

configureApiAuth({
  getHeaders: getAuthHeaders,
  onUnauthorized: () => {
    const kc = keycloakInstance;
    if (AUTH_MODE !== 'oidc' || !kc) return;
    // Intenta un refresco forzado; si falla, vuelve al login (con protección contra bucles).
    kc.updateToken(-1).catch(() => {
      if (!recentlyRedirectedToLogin()) void redirectToLogin(kc);
    });
  },
});

// ---------- Contexto React ----------

export type AuthStatus = 'loading' | 'unauthenticated' | 'redirecting' | 'authenticated' | 'error';

export interface AuthContextValue {
  mode: AuthMode;
  /** true cuando la inicialización terminó y `/me` se resolvió (o falló). */
  ready: boolean;
  status: AuthStatus;
  authenticated: boolean;
  /** Usuario de `/me`; null hasta cargarlo. */
  user: Me | null;
  roles: Role[];
  /** Mensaje de error de autenticación o de carga de `/me`. */
  error: string | null;
  /** Token de acceso vigente (OIDC) o null (modo dev / sin sesión). */
  token: () => Promise<string | null>;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  /** true si el usuario tiene alguno de los roles indicados. */
  hasRole: (...roles: Role[]) => boolean;
  isAdmin: boolean;
  /** agent o admin. */
  isAgent: boolean;
  /** Vuelve a cargar `/me` (p.ej. tras cambios de equipos). */
  refreshUser: () => Promise<void>;
  /** Solo modo dev. */
  devIdentity: DevIdentity | null;
  /** Solo modo dev: establece (o borra con null) la identidad de laboratorio. */
  setDevIdentity: (identity: DevIdentity | null) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [devIdentity, setDevIdentityState] = useState<DevIdentity | null>(null);
  const meRequest = useRef(0);

  const loadMe = useCallback(async () => {
    const requestId = ++meRequest.current;
    setError(null);
    try {
      const me = await apiFetch<Me>('/me');
      if (requestId !== meRequest.current) return;
      setUser(me);
      setStatus('authenticated');
    } catch (e) {
      if (requestId !== meRequest.current) return;
      setUser(null);
      setError(`No se pudo cargar su perfil: ${errorMessage(e)}`);
      setStatus('error');
    }
  }, []);

  // Inicialización (solo en cliente).
  useEffect(() => {
    let cancelled = false;

    if (AUTH_MODE === 'dev') {
      const stored = readStoredDevIdentity();
      currentDevIdentity = stored;
      setDevIdentityState(stored);
      if (stored) void loadMe();
      else setStatus('unauthenticated');
      return () => {
        cancelled = true;
      };
    }

    let refreshTimer: ReturnType<typeof setInterval> | undefined;
    initKeycloak()
      .then(async (kc) => {
        if (cancelled) return;
        if (!kc.authenticated) {
          if (recentlyRedirectedToLogin()) {
            setError('No fue posible iniciar sesión en Keycloak. Verifique la configuración del cliente y vuelva a intentarlo.');
            setStatus('error');
            return;
          }
          setStatus('redirecting');
          await redirectToLogin(kc);
          return;
        }
        const refresh = () => {
          kc.updateToken(TOKEN_MIN_VALIDITY * 2).catch(() => {
            setStatus('redirecting');
            void redirectToLogin(kc);
          });
        };
        kc.onTokenExpired = refresh;
        kc.onAuthRefreshError = () => {
          setStatus('redirecting');
          void redirectToLogin(kc);
        };
        kc.onAuthLogout = () => {
          setUser(null);
          setStatus('redirecting');
          void redirectToLogin(kc);
        };
        refreshTimer = setInterval(refresh, TOKEN_REFRESH_INTERVAL_MS);
        await loadMe();
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(`No se pudo contactar al servidor de identidad (${KEYCLOAK_URL}): ${errorMessage(e)}`);
        setStatus('error');
      });

    return () => {
      cancelled = true;
      if (refreshTimer) clearInterval(refreshTimer);
    };
  }, [loadMe]);

  const login = useCallback(async () => {
    if (AUTH_MODE === 'dev') {
      setStatus('unauthenticated');
      return;
    }
    setError(null);
    try {
      const kc = await initKeycloak();
      setStatus('redirecting');
      await redirectToLogin(kc);
    } catch (e) {
      setError(`No se pudo iniciar sesión: ${errorMessage(e)}`);
      setStatus('error');
    }
  }, []);

  const logout = useCallback(async () => {
    meRequest.current++;
    setUser(null);
    if (AUTH_MODE === 'dev') {
      currentDevIdentity = null;
      writeStoredDevIdentity(null);
      setDevIdentityState(null);
      setError(null);
      setStatus('unauthenticated');
      return;
    }
    const kc = keycloakInstance;
    setStatus('redirecting');
    if (kc) {
      try {
        await kc.logout({ redirectUri: `${window.location.origin}/` });
        return;
      } catch (e) {
        setError(`No se pudo cerrar la sesión: ${errorMessage(e)}`);
        setStatus('error');
        return;
      }
    }
    window.location.assign('/');
  }, []);

  const setDevIdentity = useCallback(
    (identity: DevIdentity | null) => {
      if (AUTH_MODE !== 'dev') return;
      const clean = identity ? sanitizeDevIdentity(identity) : null;
      currentDevIdentity = clean;
      writeStoredDevIdentity(clean);
      setDevIdentityState(clean);
      setUser(null);
      if (clean) {
        setStatus('loading');
        void loadMe();
      } else {
        meRequest.current++;
        setStatus('unauthenticated');
      }
    },
    [loadMe],
  );

  const refreshUser = useCallback(async () => {
    if (status === 'authenticated' || status === 'error') await loadMe();
  }, [loadMe, status]);

  const token = useCallback(() => getAccessToken(), []);

  const value = useMemo<AuthContextValue>(() => {
    const roles = (user?.roles ?? []) as Role[];
    const hasRole = (...wanted: Role[]) => wanted.some((r) => roles.includes(r));
    return {
      mode: AUTH_MODE,
      ready: status !== 'loading',
      status,
      authenticated: status === 'authenticated' && user !== null,
      user,
      roles,
      error,
      token,
      login,
      logout,
      hasRole,
      isAdmin: roles.includes('admin'),
      isAgent: roles.includes('agent') || roles.includes('admin'),
      refreshUser,
      devIdentity,
      setDevIdentity,
    };
  }, [status, user, error, token, login, logout, refreshUser, devIdentity, setDevIdentity]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth debe usarse dentro de <AuthProvider>.');
  return ctx;
}
