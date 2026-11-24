'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback } from 'react';
import { EmptyState, PageHeader } from '@/components/ui';
import { useAuth } from '@/lib/auth';
import AtRiskSection from './AtRiskSection';
import CalendarsSection from './CalendarsSection';
import ComplianceSection from './ComplianceSection';
import PoliciesSection from './PoliciesSection';

type SlaTab = 'risk' | 'compliance' | 'policies' | 'calendars';

const TABS: Array<{ key: SlaTab; label: string }> = [
  { key: 'risk', label: 'En riesgo y vencidos' },
  { key: 'compliance', label: 'Cumplimiento' },
  { key: 'policies', label: 'Políticas' },
  { key: 'calendars', label: 'Calendarios y feriados' },
];

function isTab(value: string | null): value is SlaTab {
  return TABS.some((t) => t.key === value);
}

/** Página /sla: agentes ven riesgo y cumplimiento (y el catálogo en lectura); administradores lo editan. */
export default function SlaPageView() {
  const { isAgent, isAdmin } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const requested = params.get('tab');
  const tab: SlaTab = isTab(requested) ? requested : 'risk';

  const selectTab = useCallback(
    (next: SlaTab) => {
      const query = new URLSearchParams(params.toString());
      if (next === 'risk') query.delete('tab');
      else query.set('tab', next);
      const qs = query.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  if (!isAgent) {
    return (
      <div className="page">
        <PageHeader title="Niveles de servicio (SLA)" />
        <section className="panel">
          <EmptyState title="Acceso restringido">
            Esta sección es para agentes y administradores. El estado de SLA de sus tickets se muestra en el detalle de
            cada ticket.
          </EmptyState>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Niveles de servicio (SLA)"
        subtitle={
          isAdmin
            ? 'Seguimiento de vencimientos, cumplimiento y configuración de políticas, calendarios hábiles y feriados.'
            : 'Tickets de su área en riesgo o vencidos y cumplimiento de los objetivos de servicio.'
        }
      />
      <div className="tabs" role="tablist" aria-label="Secciones de SLA">
        {TABS.map((t) => (
          <button
            key={t.key}
            id={`sla-tab-${t.key}`}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            aria-controls={`sla-panel-${t.key}`}
            className={`tab${tab === t.key ? ' is-active' : ''}`}
            onClick={() => selectTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`sla-panel-${tab}`} aria-labelledby={`sla-tab-${tab}`}>
        {tab === 'risk' && <AtRiskSection />}
        {tab === 'compliance' && <ComplianceSection />}
        {tab === 'policies' && <PoliciesSection canEdit={isAdmin} />}
        {tab === 'calendars' && <CalendarsSection canEdit={isAdmin} />}
      </div>
    </div>
  );
}
