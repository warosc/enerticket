'use client';

/**
 * Estructura principal del portal: barra lateral con navegación por rol, barra superior
 * con la campana de notificaciones y estados de autenticación (carga, login dev, errores).
 */
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';
import { useAuth } from '@/lib/auth';
import { resetRealtime, useRealtimeStatus } from '@/lib/realtime';
import { initials, ROLE_LABELS } from '@/lib/format';
import type { Role } from '@/lib/types';
import DevLogin from './DevLogin';
import { Icon, type IconName } from './icons';
import NotificationBell from './NotificationBell';
import RealtimeIndicator from './kanban/RealtimeIndicator';
import { Alert, Spinner } from './ui';

export interface NavItem {
  href: string;
  label: string;
  icon: IconName;
  /** Roles que ven el enlace; vacío = todos los usuarios autenticados. */
  roles: Role[];
}

export const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'Dashboard', icon: 'dashboard', roles: [] },
  { href: '/tickets', label: 'Tickets', icon: 'tickets', roles: [] },
  { href: '/kanban', label: 'Kanban', icon: 'kanban', roles: ['agent', 'admin'] },
  { href: '/approvals', label: 'Aprobaciones', icon: 'approvals', roles: ['approver', 'agent', 'admin'] },
  { href: '/sla', label: 'SLA', icon: 'sla', roles: ['agent', 'admin'] },
  { href: '/automations', label: 'Automatizaciones', icon: 'automations', roles: ['admin'] },
  { href: '/reports', label: 'Reportes', icon: 'reports', roles: [] },
  { href: '/admin', label: 'Administración', icon: 'admin', roles: ['admin'] },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

function FullScreenMessage({ children }: { children: ReactNode }) {
  return (
    <div className="screen-center">
      <div className="panel screen-card stack" style={{ alignItems: 'center', textAlign: 'center' }}>
        {children}
      </div>
    </div>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname() || '/';
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNavOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navOpen]);

  if (auth.status === 'loading') {
    return (
      <FullScreenMessage>
        <Spinner large />
        <p className="muted">Verificando sesión…</p>
      </FullScreenMessage>
    );
  }

  if (auth.status === 'redirecting') {
    return (
      <FullScreenMessage>
        <Spinner large />
        <p className="muted">Redirigiendo al inicio de sesión corporativo…</p>
      </FullScreenMessage>
    );
  }

  if (auth.mode === 'dev' && auth.status === 'unauthenticated') {
    return <DevLogin />;
  }

  if (auth.status === 'error' || !auth.user) {
    return (
      <FullScreenMessage>
        <h1 className="page-title" style={{ fontSize: 'var(--text-xl)' }}>
          No fue posible iniciar la sesión
        </h1>
        <Alert tone="error">{auth.error ?? 'No se pudo cargar su perfil.'}</Alert>
        <div className="row" style={{ justifyContent: 'center' }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => (auth.mode === 'oidc' ? window.location.reload() : void auth.refreshUser())}
          >
            Reintentar
          </button>
          {auth.mode === 'oidc' ? (
            <button type="button" className="btn" onClick={() => void auth.login()}>
              Iniciar sesión de nuevo
            </button>
          ) : null}
          <button type="button" className="btn btn-ghost" onClick={() => {
            // Cierra el WebSocket de inmediato (no esperar a que expire la identidad).
            resetRealtime();
            void auth.logout();
          }}>
            {auth.mode === 'dev' ? 'Cambiar identidad' : 'Cerrar sesión'}
          </button>
        </div>
      </FullScreenMessage>
    );
  }

  const user = auth.user;
  const visibleItems = NAV_ITEMS.filter((item) => item.roles.length === 0 || auth.hasRole(...item.roles));
  const current = NAV_ITEMS.filter((item) => isActive(pathname, item.href)).sort((a, b) => b.href.length - a.href.length)[0];
  const roleText = user.roles
    .filter((r) => r !== 'requester' || user.roles.length === 1)
    .map((r) => ROLE_LABELS[r] ?? r)
    .join(' · ');

  return (
    <div className={`app-shell${navOpen ? ' is-nav-open' : ''}`}>
      <a href="#contenido" className="skip-link">
        Saltar al contenido
      </a>
      <aside className="app-sidebar" id="app-sidebar" aria-label="Navegación principal">
        <Link href="/" className="app-brand">
          <span className="app-brand-mark" aria-hidden="true">
            ESD
          </span>
          <span>
            <span className="app-brand-name">Energuate</span>
            <br />
            <span className="app-brand-sub">Service Desk</span>
          </span>
        </Link>

        <nav className="app-nav" aria-label="Secciones">
          {visibleItems.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`app-nav-link${active ? ' is-active' : ''}`}
                aria-current={active ? 'page' : undefined}
              >
                <Icon name={item.icon} className="app-nav-icon" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="app-sidebar-footer">
          <div className="app-user">
            <span className="app-user-avatar" aria-hidden="true">
              {initials(user.display_name || user.username)}
            </span>
            <span style={{ minWidth: 0 }}>
              <span className="app-user-name truncate" style={{ display: 'block' }} title={user.display_name}>
                {user.display_name || user.username}
              </span>
              <span className="app-user-roles truncate" style={{ display: 'block' }}>
                {roleText}
              </span>
              {(user.department || user.vip) && (
                <span
                  className="app-user-roles truncate"
                  style={{ display: 'block' }}
                  title={[user.department?.name, user.gerencia?.name].filter(Boolean).join(' · ')}
                >
                  {user.vip ? 'VIP' : ''}
                  {user.vip && user.department ? ' · ' : ''}
                  {user.department?.name ?? ''}
                </span>
              )}
            </span>
          </div>
          <button type="button" className="btn btn-sm btn-logout" onClick={() => {
            // Cierra el WebSocket de inmediato (no esperar a que expire la identidad).
            resetRealtime();
            void auth.logout();
          }}>
            <Icon name="logout" width={14} height={14} />
            {auth.mode === 'dev' ? 'Cambiar identidad' : 'Cerrar sesión'}
          </button>
        </div>
      </aside>

      <button
        type="button"
        className="app-backdrop"
        aria-label="Cerrar menú"
        tabIndex={-1}
        onClick={() => setNavOpen(false)}
      />

      <div className="app-main">
        <header className="app-topbar">
          <button
            type="button"
            className="btn btn-ghost btn-icon app-menu-toggle"
            aria-label="Abrir menú"
            aria-controls="app-sidebar"
            aria-expanded={navOpen}
            onClick={() => setNavOpen((open) => !open)}
          >
            <Icon name="menu" />
          </button>
          <span className="app-topbar-title">{current?.label ?? 'Service Desk'}</span>
          <span className="spacer" />
          {auth.mode === 'dev' && <span className="app-env-tag">Laboratorio</span>}
          <TopbarRealtime />
          <NotificationBell />
        </header>
        <main className="app-content" id="contenido" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}

/** Indicador de tiempo real en la barra superior (oculto mientras no hay conexión en uso). */
function TopbarRealtime() {
  const realtime = useRealtimeStatus();
  if (realtime.status === 'idle') return null;
  return <RealtimeIndicator className="app-topbar-live" />;
}
