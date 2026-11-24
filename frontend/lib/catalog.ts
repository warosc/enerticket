/**
 * Catálogo de servicio (Fase 3): llamadas a la API de administración del catálogo y utilidades
 * para clasificar tickets (departamento de atención → categoría → subcategoría, proveedor y canal).
 *
 * Los tipos viven en lib/types.ts (los de políticas de SLA, con categorías y «solo VIP», en lib/sla.ts).
 */
import { apiFetch } from './api';
import type {
  AdminCategory,
  Catalog,
  CatalogCategory,
  CatalogImportResult,
  CatalogProvider,
  CategoryInput,
  CategoryPatch,
  Department,
  DepartmentAlias,
  Gerencia,
  GerenciaInput,
  GerenciaViewer,
  Provider,
  ProviderInput,
  SubcategoryInput,
  SubcategoryPatch,
  TicketChannel,
  TicketOut,
  UserOut,
  UserProfilePatch,
} from './types';

// ---------- Etiquetas ----------

/** Etiquetas por defecto (la API también las envía en `catalog.channels`). */
export const CHANNEL_LABELS: Record<TicketChannel, string> = {
  portal: 'Portal',
  email: 'Correo',
  phone: 'Teléfono',
  other: 'Otro',
};

/** Claves de tipo que acepta la importación CSV (columna `tipos`, separadas por `|`). */
export const IMPORT_TYPE_KEYS = ['incident', 'service_request', 'work_order'] as const;

export const WORK_ORDER_TYPE = 'work_order';

export function channelLabel(key: string | null | undefined, catalog?: Catalog | null): string {
  if (!key) return '—';
  return catalog?.channels.find((c) => c.key === key)?.label ?? CHANNEL_LABELS[key as TicketChannel] ?? key;
}

/** «12 h 30 min» hábiles a partir de minutos; `null` → «—». */
export function formatBusinessMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** Horas con dos decimales (formato del informe de proveedor). */
export function formatBusinessHours(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  return (minutes / 60).toLocaleString('es-GT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name, 'es');

// ---------- Reglas de clasificación (mismas que la API; la API vuelve a validarlas) ----------

/** Departamentos que reciben tickets (activos y con «atiende tickets»), por nombre. */
export function ticketDepartments(catalog: Catalog, keepId?: number | null): Department[] {
  return catalog.departments
    .filter((d) => (d.active && d.serves_tickets) || d.id === keepId)
    .sort(byName);
}

/**
 * ¿El departamento tiene categorías activas (que admitan `typeKey`, si se indica)? Entonces resolver
 * exige clasificar; si ninguna admite el tipo del ticket, no (como la API).
 */
export function departmentHasCategories(
  catalog: Catalog | null,
  departmentId: number | null | undefined,
  typeKey?: string | null,
): boolean {
  if (!catalog || !departmentId) return false;
  return catalog.categories.some((c) => c.department_id === departmentId && categoryAllowsType(c, typeKey));
}

/** ¿La categoría admite el tipo? (`type_keys` vacía = todos los tipos). */
export function categoryAllowsType(category: Pick<CatalogCategory, 'type_keys'>, typeKey: string | null | undefined): boolean {
  return !typeKey || category.type_keys.length === 0 || category.type_keys.includes(typeKey);
}

/** Categorías activas del departamento que admiten el tipo, en el orden de la API. */
export function categoriesFor(
  catalog: Catalog,
  departmentId: number | null | undefined,
  typeKey?: string | null,
): CatalogCategory[] {
  if (!departmentId) return [];
  return catalog.categories.filter((c) => c.department_id === departmentId && categoryAllowsType(c, typeKey));
}

/** Proveedores activos aplicables al departamento (`department_ids` vacía = todos). */
export function providersFor(catalog: Catalog, departmentId: number | null | undefined): CatalogProvider[] {
  return catalog.providers.filter(
    (p) => p.department_ids.length === 0 || (departmentId ? p.department_ids.includes(departmentId) : false),
  );
}

export function findCategory(catalog: Catalog | null, id: number | null | undefined): CatalogCategory | undefined {
  if (!catalog || !id) return undefined;
  return catalog.categories.find((c) => c.id === id);
}

/** Mapa id → «Categoría / Subcategoría» de todas las subcategorías activas. */
export function subcategoryLabels(catalog: Catalog | null): Map<number, string> {
  const map = new Map<number, string>();
  for (const c of catalog?.categories ?? []) {
    for (const s of c.subcategories) map.set(s.id, `${c.name} / ${s.name}`);
  }
  return map;
}

/** «Categoría › Subcategoría» del ticket, o null si no está clasificado. */
export function classificationLabel(ticket: Pick<TicketOut, 'category' | 'subcategory'>): string | null {
  if (!ticket.category) return null;
  return ticket.subcategory ? `${ticket.category.name} › ${ticket.subcategory.name}` : ticket.category.name;
}

// ---------- Plantilla CSV de importación ----------

export const IMPORT_TEMPLATE_HEADER = 'departamento,categoria,subcategoria,tipos,ans_horas';

/** Plantilla de ejemplo (datos ficticios). La columna `ans_horas` es opcional. */
export const IMPORT_TEMPLATE_CSV = [
  IMPORT_TEMPLATE_HEADER,
  'Soporte Técnico,Equipo de cómputo,Falla de hardware,incident,8',
  'Soporte Técnico,Equipo de cómputo,Instalación de software,service_request,16',
  'Telecomunicaciones,Telefonía,Línea nueva,service_request|work_order,24',
  'Sistemas Comerciales,Facturación,,incident|service_request,',
].join('\r\n');

/** Descarga la plantilla CSV (UTF-8 con BOM para que Excel respete las tildes). */
export function downloadImportTemplate(filename = 'plantilla-catalogo.csv'): void {
  const blob = new Blob(['﻿', IMPORT_TEMPLATE_CSV, '\r\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- API ----------

export const catalogApi = {
  // Gerencias
  gerencias: () => apiFetch<Gerencia[]>('/admin/gerencias'),
  createGerencia: (body: GerenciaInput) => apiFetch<Gerencia>('/admin/gerencias', { method: 'POST', body }),
  updateGerencia: (id: number, body: Partial<GerenciaInput>) =>
    apiFetch<Gerencia>(`/admin/gerencias/${id}`, { method: 'PATCH', body }),
  gerenciaViewers: (id: number) => apiFetch<GerenciaViewer[]>(`/admin/gerencias/${id}/viewers`),
  setGerenciaViewers: (id: number, userIds: number[]) =>
    apiFetch<GerenciaViewer[]>(`/admin/gerencias/${id}/viewers`, { method: 'PUT', body: { user_ids: userIds } }),

  // Valores del AD
  aliases: (pending?: boolean) =>
    apiFetch<DepartmentAlias[]>('/admin/department-aliases', { query: { pending } }),
  createAlias: (value: string, departmentId: number | null) =>
    apiFetch<DepartmentAlias>('/admin/department-aliases', {
      method: 'POST',
      body: { value, department_id: departmentId },
    }),
  assignAlias: (id: number, departmentId: number | null) =>
    apiFetch<DepartmentAlias>(`/admin/department-aliases/${id}`, {
      method: 'PATCH',
      body: { department_id: departmentId },
    }),
  deleteAlias: (id: number) => apiFetch<void>(`/admin/department-aliases/${id}`, { method: 'DELETE' }),

  // Categorías
  categories: (departmentId?: number | null) =>
    apiFetch<AdminCategory[]>('/admin/categories', { query: { department_id: departmentId ?? undefined } }),
  createCategory: (body: CategoryInput) => apiFetch<AdminCategory>('/admin/categories', { method: 'POST', body }),
  updateCategory: (id: number, body: CategoryPatch) =>
    apiFetch<AdminCategory>(`/admin/categories/${id}`, { method: 'PATCH', body }),
  createSubcategory: (categoryId: number, body: SubcategoryInput) =>
    apiFetch<AdminCategory>(`/admin/categories/${categoryId}/subcategories`, { method: 'POST', body }),
  updateSubcategory: (id: number, body: SubcategoryPatch) =>
    apiFetch<AdminCategory>(`/admin/subcategories/${id}`, { method: 'PATCH', body }),

  // Proveedores
  providers: () => apiFetch<Provider[]>('/admin/providers'),
  createProvider: (body: ProviderInput) => apiFetch<Provider>('/admin/providers', { method: 'POST', body }),
  updateProvider: (id: number, body: Partial<ProviderInput>) =>
    apiFetch<Provider>(`/admin/providers/${id}`, { method: 'PATCH', body }),

  // Importación
  importCatalog: (file: File, dryRun: boolean) => {
    const form = new FormData();
    form.append('file', file);
    return apiFetch<CatalogImportResult>('/admin/catalog/import', {
      method: 'POST',
      body: form,
      query: { dry_run: dryRun },
    });
  },

  // Perfil de usuarios
  updateUserProfile: (userId: number, body: UserProfilePatch) =>
    apiFetch<UserOut>(`/admin/users/${userId}/profile`, { method: 'PATCH', body }),
};
