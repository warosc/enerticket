'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { DepartmentsSection, MembersSection, ProjectsSection, TeamsSection } from '@/components/admin/AdminSections';
import adminStyles from '@/components/admin/admin.module.css';
import {
  AliasesSection,
  GerenciasSection,
  ProvidersSection,
  UsersProfileSection,
} from '@/components/admin/CatalogAdminSections';
import CatalogImportSection from '@/components/admin/CatalogImportSection';
import CategoriesSection from '@/components/admin/CategoriesSection';
import { EmptyState, Loading, PageHeader } from '@/components/ui';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import type { DepartmentAlias } from '@/lib/types';

type AdminTab =
  | 'departments'
  | 'gerencias'
  | 'aliases'
  | 'categories'
  | 'providers'
  | 'import'
  | 'users'
  | 'teams'
  | 'members'
  | 'projects';

const TABS: { key: AdminTab; label: string }[] = [
  { key: 'departments', label: 'Departamentos' },
  { key: 'gerencias', label: 'Gerencias' },
  { key: 'aliases', label: 'Valores del AD' },
  { key: 'categories', label: 'Categorías' },
  { key: 'providers', label: 'Proveedores' },
  { key: 'import', label: 'Importar catálogo' },
  { key: 'users', label: 'Usuarios' },
  { key: 'teams', label: 'Equipos' },
  { key: 'members', label: 'Miembros' },
  { key: 'projects', label: 'Proyectos' },
];

function isTab(value: string | null): value is AdminTab {
  return TABS.some((t) => t.key === value);
}

function AdminView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const requested = searchParams.get('tab');
  const tab: AdminTab = isTab(requested) ? requested : 'departments';
  const [pending, setPending] = useState<number | null>(null);

  // Conteo de valores del AD pendientes de asignar (se resalta en la pestaña).
  useEffect(() => {
    let active = true;
    apiFetch<DepartmentAlias[]>('/admin/department-aliases', { query: { pending: true } })
      .then((list) => {
        if (active) setPending(list.length);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  const onPendingChange = useCallback((count: number) => setPending(count), []);

  function selectTab(next: AdminTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === 'departments') params.delete('tab');
    else params.set('tab', next);
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  return (
    <div className="page">
      <PageHeader
        title="Administración"
        subtitle="Estructura organizativa, catálogo de servicio (gerencias, categorías, proveedores) y equipos vinculados a grupos del directorio."
      />
      <div className="tabs" role="tablist" aria-label="Secciones de administración">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`admin-tab-${t.key}`}
            aria-controls="admin-tabpanel"
            aria-selected={tab === t.key}
            className={`tab${tab === t.key ? ' is-active' : ''}`}
            onClick={() => selectTab(t.key)}
          >
            {t.label}
            {t.key === 'aliases' && pending !== null && pending > 0 && (
              <>
                <span className={adminStyles.countBadge} aria-hidden="true">
                  {pending}
                </span>
                <span className="visually-hidden">{` (${pending} pendiente${pending === 1 ? '' : 's'})`}</span>
              </>
            )}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="admin-tabpanel" aria-labelledby={`admin-tab-${tab}`}>
        {tab === 'departments' && <DepartmentsSection />}
        {tab === 'gerencias' && <GerenciasSection />}
        {tab === 'aliases' && <AliasesSection onPendingChange={onPendingChange} />}
        {tab === 'categories' && <CategoriesSection />}
        {tab === 'providers' && <ProvidersSection />}
        {tab === 'import' && <CatalogImportSection />}
        {tab === 'users' && <UsersProfileSection />}
        {tab === 'teams' && <TeamsSection />}
        {tab === 'members' && <MembersSection />}
        {tab === 'projects' && <ProjectsSection />}
      </div>
    </div>
  );
}

export default function AdminPage() {
  const { isAdmin } = useAuth();

  if (!isAdmin) {
    return (
      <div className="page">
        <PageHeader title="Administración" />
        <section className="panel">
          <EmptyState title="Acceso restringido">Esta sección requiere el rol de administrador.</EmptyState>
        </section>
      </div>
    );
  }

  return (
    <Suspense fallback={<Loading />}>
      <AdminView />
    </Suspense>
  );
}
