'use client';

import { useState } from 'react';
import { DepartmentsSection, MembersSection, ProjectsSection, TeamsSection } from '@/components/admin/AdminSections';
import { EmptyState, PageHeader } from '@/components/ui';
import { useAuth } from '@/lib/auth';

type AdminTab = 'departments' | 'teams' | 'members' | 'projects';

const TABS: { key: AdminTab; label: string }[] = [
  { key: 'departments', label: 'Departamentos' },
  { key: 'teams', label: 'Equipos' },
  { key: 'members', label: 'Miembros' },
  { key: 'projects', label: 'Proyectos' },
];

export default function AdminPage() {
  const { isAdmin } = useAuth();
  const [tab, setTab] = useState<AdminTab>('departments');

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
    <div className="page">
      <PageHeader
        title="Administración"
        subtitle="Estructura organizativa: departamentos, equipos vinculados a grupos del directorio, miembros y proyectos."
      />
      <div className="tabs" role="tablist" aria-label="Secciones de administración">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`tab${tab === t.key ? ' is-active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === 'departments' && <DepartmentsSection />}
        {tab === 'teams' && <TeamsSection />}
        {tab === 'members' && <MembersSection />}
        {tab === 'projects' && <ProjectsSection />}
      </div>
    </div>
  );
}
