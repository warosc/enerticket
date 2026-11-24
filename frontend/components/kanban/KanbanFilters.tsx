'use client';

import { useEffect, useId, useState } from 'react';
import { useDebouncedValue } from '@/lib/hooks';
import type { Catalog, UserOut } from '@/lib/types';
import { activeFilterCount, EMPTY_FILTERS, type AssigneeFilter, type KanbanFilters as Filters } from './boardLogic';
import styles from './kanban.module.css';

export default function KanbanFilters({
  filters,
  catalog,
  agents,
  agentsError,
  canFilterByAssignee,
  onChange,
}: {
  filters: Filters;
  catalog: Catalog;
  agents: UserOut[];
  agentsError: string | null;
  canFilterByAssignee: boolean;
  onChange: (patch: Partial<Filters>) => void;
}) {
  const id = useId();
  const [search, setSearch] = useState(filters.q);
  const debounced = useDebouncedValue(search, 350);

  // Sincroniza el texto si el filtro cambia desde fuera (p.ej. "Limpiar filtros").
  useEffect(() => {
    setSearch(filters.q);
  }, [filters.q]);

  useEffect(() => {
    if (debounced.trim() !== filters.q.trim()) onChange({ q: debounced });
    // Solo reacciona al texto con retardo (onChange/filters.q cambian en cada render del padre).
  }, [debounced]);

  const departmentId = Number(filters.department_id) || null;
  const teams = catalog.teams.filter((t) => !departmentId || t.department_id === departmentId);
  const count = activeFilterCount(filters);
  const selectedAgentMissing =
    filters.assignee !== '' &&
    filters.assignee !== 'me' &&
    filters.assignee !== 'none' &&
    !agents.some((a) => String(a.id) === filters.assignee);

  return (
    <div className="stack-sm">
      <div className={styles.filters} role="search" aria-label="Filtros del tablero">
        <div className={`field ${styles.filterSearch}`}>
          <label className="field-label" htmlFor={`${id}-q`}>
            Buscar
          </label>
          <input
            id={`${id}-q`}
            className="input"
            type="search"
            placeholder="Clave, título o descripción"
            value={search}
            maxLength={200}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="field">
          <label className="field-label" htmlFor={`${id}-dep`}>
            Departamento
          </label>
          <select
            id={`${id}-dep`}
            className="select"
            value={filters.department_id}
            onChange={(e) => onChange({ department_id: e.target.value, team_id: '' })}
          >
            <option value="">Todos</option>
            {catalog.departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="field-label" htmlFor={`${id}-team`}>
            Equipo
          </label>
          <select
            id={`${id}-team`}
            className="select"
            value={filters.team_id}
            onChange={(e) => onChange({ team_id: e.target.value })}
          >
            <option value="">Todos</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="field-label" htmlFor={`${id}-prio`}>
            Prioridad
          </label>
          <select
            id={`${id}-prio`}
            className="select"
            value={filters.priority}
            onChange={(e) => onChange({ priority: e.target.value })}
          >
            <option value="">Todas</option>
            {catalog.priorities.map((p) => (
              <option key={p.id} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="field-label" htmlFor={`${id}-assignee`}>
            Asignado
          </label>
          <select
            id={`${id}-assignee`}
            className="select"
            value={filters.assignee}
            onChange={(e) => onChange({ assignee: e.target.value as AssigneeFilter })}
          >
            <option value="">Cualquiera</option>
            <option value="me">Asignados a mí</option>
            <option value="none">Sin asignar</option>
            {canFilterByAssignee && agents.length > 0 && (
              <optgroup label="Agentes">
                {agents.map((a) => (
                  <option key={a.id} value={String(a.id)}>
                    {a.display_name}
                  </option>
                ))}
              </optgroup>
            )}
            {selectedAgentMissing && <option value={filters.assignee}>Usuario #{filters.assignee}</option>}
          </select>
          {agentsError && <span className="field-error">No se pudo cargar la lista de agentes: {agentsError}</span>}
        </div>
      </div>

      <div className="row-between">
        <div className={styles.filterChecks}>
          <label className="checkbox">
            <input type="checkbox" checked={filters.mine} onChange={(e) => onChange({ mine: e.target.checked })} />
            Mis tickets (creados por mí o asignados a mí)
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={filters.hideFinal}
              onChange={(e) => onChange({ hideFinal: e.target.checked })}
            />
            Ocultar estados finales
          </label>
        </div>
        {count > 0 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(EMPTY_FILTERS)}>
            Limpiar filtros ({count})
          </button>
        )}
      </div>
    </div>
  );
}
