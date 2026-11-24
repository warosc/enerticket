'use client';

import { useId, useMemo } from 'react';
import { categoriesFor } from '@/lib/catalog';
import type { Catalog, CatalogCategory, IdNameRef } from '@/lib/types';

export interface CategoryValue {
  categoryId: number | null;
  subcategoryId: number | null;
}

/**
 * Selectores encadenados categoría → subcategoría, filtrados por departamento y tipo de ticket.
 * Devuelve dos `.field` para usarse dentro de un `.form-grid` o de un panel apilado.
 * `current` permite conservar la clasificación actual aunque ya no esté activa en el catálogo.
 */
export default function CategoryFields({
  catalog,
  departmentId,
  typeKey,
  value,
  onChange,
  required = false,
  disabled = false,
  current,
  categoryHint,
}: {
  catalog: Catalog;
  departmentId: number | null | undefined;
  typeKey?: string | null;
  value: CategoryValue;
  onChange: (value: CategoryValue) => void;
  required?: boolean;
  disabled?: boolean;
  current?: { category: IdNameRef | null; subcategory: IdNameRef | null };
  categoryHint?: string;
}) {
  const id = useId();
  const categories = useMemo<CatalogCategory[]>(() => {
    const list = categoriesFor(catalog, departmentId, typeKey);
    const cur = current?.category;
    if (cur && !list.some((c) => c.id === cur.id)) {
      const known = catalog.categories.find((c) => c.id === cur.id);
      list.unshift(known ?? { id: cur.id, department_id: departmentId ?? 0, name: cur.name, type_keys: [], subcategories: [] });
    }
    return list;
  }, [catalog, departmentId, typeKey, current?.category]);

  const selected = categories.find((c) => c.id === value.categoryId) ?? null;
  const subcategories = useMemo<IdNameRef[]>(() => {
    const list = [...(selected?.subcategories ?? [])];
    const cur = current?.subcategory;
    if (cur && selected && selected.id === current?.category?.id && !list.some((s) => s.id === cur.id)) {
      list.unshift(cur);
    }
    return list;
  }, [selected, current?.subcategory, current?.category?.id]);

  const noCategories = categories.length === 0;

  return (
    <>
      <div className="field">
        <label className="field-label" htmlFor={`${id}-category`}>
          Categoría
          {required && !noCategories && <span className="required">*</span>}
        </label>
        <select
          id={`${id}-category`}
          className="select"
          value={value.categoryId ?? ''}
          disabled={disabled || noCategories}
          required={required && !noCategories}
          onChange={(e) => {
            const next = e.target.value === '' ? null : Number(e.target.value);
            onChange({ categoryId: next, subcategoryId: null });
          }}
        >
          <option value="">{noCategories ? 'Sin categorías para este departamento' : 'Seleccione…'}</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        {categoryHint && <span className="field-hint">{categoryHint}</span>}
      </div>
      <div className="field">
        <label className="field-label" htmlFor={`${id}-subcategory`}>
          Subcategoría
        </label>
        <select
          id={`${id}-subcategory`}
          className="select"
          value={value.subcategoryId ?? ''}
          disabled={disabled || !selected || subcategories.length === 0}
          onChange={(e) =>
            onChange({
              categoryId: value.categoryId,
              subcategoryId: e.target.value === '' ? null : Number(e.target.value),
            })
          }
        >
          <option value="">
            {!selected ? 'Elija primero la categoría' : subcategories.length === 0 ? 'Sin subcategorías' : 'Sin subcategoría'}
          </option>
          {subcategories.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
    </>
  );
}
