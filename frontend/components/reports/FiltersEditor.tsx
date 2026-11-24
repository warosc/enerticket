'use client';

/**
 * Filtros de una definición de reporte (globales o de sección), alimentados por /catalog.
 * Las listas dependientes se acotan por la selección superior (departamento → equipos/categorías/
 * proveedores, categoría → subcategorías, gerencia → departamentos solicitantes) y al cambiar la
 * selección superior se retiran los valores que ya no aplican.
 */
import { useMemo } from 'react';
import type { ReportCatalogOptions, ReportDefinitionFilters, ReportOption } from '@/lib/reports';
import MultiSelect from './MultiSelect';
import styles from './builder.module.css';

type Filters = ReportDefinitionFilters;

function keepValid<T>(values: T[], valid: Set<T>): T[] {
  return values.filter((v) => valid.has(v));
}

function teamsFor(options: ReportCatalogOptions, departmentIds: number[]) {
  return options.teams.filter((t) => !departmentIds.length || departmentIds.includes(t.department_id));
}

function categoriesFor(options: ReportCatalogOptions, departmentIds: number[], typeKeys: string[]) {
  return options.categories.filter(
    (c) =>
      (!departmentIds.length || departmentIds.includes(c.department_id)) &&
      (!typeKeys.length || !c.type_keys.length || c.type_keys.some((k) => typeKeys.includes(k))),
  );
}

function providersFor(options: ReportCatalogOptions, departmentIds: number[]) {
  return options.providers.filter(
    (p) => !departmentIds.length || !p.department_ids.length || p.department_ids.some((id) => departmentIds.includes(id)),
  );
}

function requesterDepartmentsFor(options: ReportCatalogOptions, gerenciaIds: number[]) {
  return options.departments.filter(
    (d) => !gerenciaIds.length || (d.gerencia_id !== null && gerenciaIds.includes(d.gerencia_id)),
  );
}

export default function FiltersEditor({
  value,
  onChange,
  options,
  disabled,
}: {
  value: Filters;
  onChange: (next: Filters) => void;
  options: ReportCatalogOptions;
  disabled?: boolean;
}) {
  const departmentName = useMemo(() => new Map(options.departments.map((d) => [d.id, d.name])), [options.departments]);

  const serviceDepartments = useMemo<ReportOption[]>(
    () =>
      options.departments
        .filter((d) => d.serves_tickets)
        .map((d) => ({ value: d.id, label: d.active ? d.name : `${d.name} (inactivo)` })),
    [options.departments],
  );

  const teams = useMemo<ReportOption[]>(
    () =>
      teamsFor(options, value.department_ids).map((t) => ({
        value: t.id,
        label: t.name,
        group: departmentName.get(t.department_id),
      })),
    [options, value.department_ids, departmentName],
  );

  const visibleCategories = useMemo(
    () => categoriesFor(options, value.department_ids, value.type_keys),
    [options, value.department_ids, value.type_keys],
  );

  const categories = useMemo<ReportOption[]>(
    () =>
      visibleCategories.map((c) => ({ value: c.id, label: c.name, group: departmentName.get(c.department_id) ?? '' })),
    [visibleCategories, departmentName],
  );

  const subcategories = useMemo<ReportOption[]>(() => {
    const source = value.category_ids.length
      ? visibleCategories.filter((c) => value.category_ids.includes(c.id))
      : visibleCategories;
    return source.flatMap((c) => c.subcategories.map((s) => ({ value: s.id, label: s.name, group: c.name })));
  }, [visibleCategories, value.category_ids]);

  const providers = useMemo<ReportOption[]>(
    () => providersFor(options, value.department_ids).map((p) => ({ value: p.id, label: p.name })),
    [options, value.department_ids],
  );

  const gerencias = useMemo<ReportOption[]>(
    () => options.gerencias.map((g) => ({ value: g.id, label: g.name })),
    [options.gerencias],
  );

  const requesterDepartments = useMemo<ReportOption[]>(
    () => requesterDepartmentsFor(options, value.gerencia_ids).map((d) => ({ value: d.id, label: d.name })),
    [options, value.gerencia_ids],
  );

  /** Aplica un cambio y retira los valores dependientes que dejaron de aplicar. */
  const update = (patch: Partial<Filters>) => {
    const next: Filters = { ...value, ...patch };
    if ('department_ids' in patch || 'type_keys' in patch) {
      next.team_ids = keepValid(next.team_ids, new Set(teamsFor(options, next.department_ids).map((t) => t.id)));
      const cats = categoriesFor(options, next.department_ids, next.type_keys);
      next.category_ids = keepValid(next.category_ids, new Set(cats.map((c) => c.id)));
      next.provider_ids = keepValid(next.provider_ids, new Set(providersFor(options, next.department_ids).map((p) => p.id)));
    }
    if ('department_ids' in patch || 'type_keys' in patch || 'category_ids' in patch) {
      const cats = categoriesFor(options, next.department_ids, next.type_keys).filter(
        (c) => !next.category_ids.length || next.category_ids.includes(c.id),
      );
      next.subcategory_ids = keepValid(
        next.subcategory_ids,
        new Set(cats.flatMap((c) => c.subcategories.map((s) => s.id))),
      );
    }
    if ('gerencia_ids' in patch) {
      next.requester_department_ids = keepValid(
        next.requester_department_ids,
        new Set(requesterDepartmentsFor(options, next.gerencia_ids).map((d) => d.id)),
      );
    }
    onChange(next);
  };

  return (
    <div className={styles.filtersGrid}>
      <MultiSelect<string>
        label="Tipo"
        options={options.types}
        value={value.type_keys}
        onChange={(type_keys) => update({ type_keys })}
        disabled={disabled}
      />
      <MultiSelect
        label="Departamento de atención"
        options={serviceDepartments}
        value={value.department_ids}
        onChange={(department_ids) => update({ department_ids })}
        disabled={disabled}
      />
      <MultiSelect
        label="Equipo"
        options={teams}
        value={value.team_ids}
        onChange={(team_ids) => update({ team_ids })}
        disabled={disabled}
      />
      <MultiSelect
        label="Categoría"
        options={categories}
        value={value.category_ids}
        onChange={(category_ids) => update({ category_ids })}
        placeholder="Todas"
        emptyText="No hay categorías para los departamentos y tipos elegidos."
        disabled={disabled}
      />
      <MultiSelect
        label="Subcategoría"
        options={subcategories}
        value={value.subcategory_ids}
        onChange={(subcategory_ids) => update({ subcategory_ids })}
        placeholder="Todas"
        emptyText="No hay subcategorías para las categorías elegidas."
        disabled={disabled}
      />
      <MultiSelect
        label="Proveedor"
        options={providers}
        value={value.provider_ids}
        onChange={(provider_ids) => update({ provider_ids })}
        emptyText="No hay proveedores registrados."
        disabled={disabled}
      />
      <MultiSelect
        label="Gerencia solicitante"
        options={gerencias}
        value={value.gerencia_ids}
        onChange={(gerencia_ids) => update({ gerencia_ids })}
        placeholder="Todas"
        emptyText="No hay gerencias registradas."
        disabled={disabled}
      />
      <MultiSelect
        label="Departamento solicitante"
        options={requesterDepartments}
        value={value.requester_department_ids}
        onChange={(requester_department_ids) => update({ requester_department_ids })}
        disabled={disabled}
      />
      <MultiSelect<string>
        label="Prioridad"
        options={options.priorities}
        value={value.priority_keys}
        onChange={(priority_keys) => update({ priority_keys })}
        placeholder="Todas"
        disabled={disabled}
      />
      <MultiSelect<string>
        label="Canal"
        options={options.channels}
        value={value.channels}
        onChange={(channels) => update({ channels })}
        disabled={disabled}
      />
      <label className="field">
        <span className="field-label">VIP</span>
        <select
          className="select"
          value={value.vip === null ? '' : value.vip ? 'yes' : 'no'}
          disabled={disabled}
          onChange={(e) => update({ vip: e.target.value === '' ? null : e.target.value === 'yes' })}
        >
          <option value="">Todos</option>
          <option value="yes">Solo VIP</option>
          <option value="no">Sin VIP</option>
        </select>
      </label>
      {value.assignee_ids.length > 0 && (
        <div className="field">
          <span className="field-label">Técnico asignado</span>
          <div className="row-sm">
            <span className="badge badge-primary">
              {value.assignee_ids.length} técnico{value.assignee_ids.length === 1 ? '' : 's'}
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={disabled}
              onClick={() => update({ assignee_ids: [] })}
            >
              Quitar
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
