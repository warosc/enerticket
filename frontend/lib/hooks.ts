'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch, errorMessage, type QueryParams } from './api';
import type { Catalog } from './types';

export interface ApiResource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Vuelve a pedir el recurso; `silent` no muestra el estado de carga. */
  reload: (options?: { silent?: boolean }) => Promise<void>;
  setData: (updater: T | null | ((prev: T | null) => T | null)) => void;
}

/**
 * Carga un recurso GET de la API. `path = null` desactiva la carga.
 * Cancela peticiones obsoletas cuando cambian `path` o `query`.
 */
export function useApiResource<T>(path: string | null, query?: QueryParams): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const queryKey = JSON.stringify(query ?? {});
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (silent: boolean) => {
      if (path === null) return;
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      if (!silent) setLoading(true);
      try {
        const result = await apiFetch<T>(path, {
          query: JSON.parse(queryKey) as QueryParams,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setData(result);
        setError(null);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(errorMessage(e));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [path, queryKey],
  );

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    void load(false);
    return () => controllerRef.current?.abort();
  }, [load, path]);

  const reload = useCallback((options?: { silent?: boolean }) => load(options?.silent ?? false), [load]);

  return { data, error, loading, reload, setData };
}

// ---------- Catálogo compartido (caché en memoria) ----------

let catalogCache: Catalog | null = null;
let catalogPromise: Promise<Catalog> | null = null;

function fetchCatalog(force = false): Promise<Catalog> {
  if (force) {
    catalogCache = null;
    catalogPromise = null;
  }
  if (catalogCache) return Promise.resolve(catalogCache);
  if (!catalogPromise) {
    catalogPromise = apiFetch<Catalog>('/catalog')
      .then((catalog) => {
        catalogCache = catalog;
        return catalog;
      })
      .finally(() => {
        catalogPromise = null;
      });
  }
  return catalogPromise;
}

/** Invalida el catálogo en caché (p.ej. tras cambios en Administración). */
export function invalidateCatalog(): void {
  catalogCache = null;
}

export interface CatalogState {
  catalog: Catalog | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Catálogo de /catalog compartido entre páginas (se pide una sola vez). */
export function useCatalog(): CatalogState {
  const [catalog, setCatalog] = useState<Catalog | null>(catalogCache);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(catalogCache === null);
  const mounted = useRef(true);

  const load = useCallback(async (force: boolean) => {
    setLoading(true);
    try {
      const result = await fetchCatalog(force);
      if (!mounted.current) return;
      setCatalog(result);
      setError(null);
    } catch (e) {
      if (mounted.current) setError(`No se pudo cargar el catálogo: ${errorMessage(e)}`);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load(false);
    return () => {
      mounted.current = false;
    };
  }, [load]);

  const reload = useCallback(() => load(true), [load]);
  return { catalog, error, loading, reload };
}

/** Valor con retardo (búsquedas mientras se escribe). */
export function useDebouncedValue<T>(value: T, delayMs = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
