'use client';

import { useEffect, useId, useState } from 'react';
import { apiFetch, errorMessage } from '@/lib/api';
import { useDebouncedValue } from '@/lib/hooks';
import type { Role, UserOut } from '@/lib/types';

/**
 * Selector de usuario con búsqueda (GET /users?q=). Requiere rol agent o admin.
 * Solo aparecen usuarios que ya iniciaron sesión al menos una vez (provisión JIT).
 */
export default function UserPicker({
  value,
  onChange,
  role,
  label = 'Usuario',
  placeholder = 'Buscar por nombre, usuario o correo',
  required = false,
}: {
  value: UserOut | null;
  onChange: (user: UserOut | null) => void;
  role?: Role;
  label?: string;
  placeholder?: string;
  required?: boolean;
}) {
  const id = useId();
  const [term, setTerm] = useState('');
  const debounced = useDebouncedValue(term.trim(), 300);
  const [results, setResults] = useState<UserOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    apiFetch<UserOut[]>('/users', {
      query: { q: debounced || undefined, role, limit: 25 },
      signal: controller.signal,
    })
      .then((list) => {
        setResults(list);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [debounced, role]);

  const options = value && !results.some((u) => u.id === value.id) ? [value, ...results] : results;

  return (
    <div className="field">
      <label className="field-label" htmlFor={`${id}-search`}>
        {label}
        {required && <span className="required">*</span>}
      </label>
      <input
        id={`${id}-search`}
        className="input"
        type="search"
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder={placeholder}
        maxLength={100}
      />
      <select
        className="select"
        aria-label={`${label}: resultados`}
        value={value?.id ?? ''}
        required={required}
        onChange={(e) => {
          const selected = options.find((u) => u.id === Number(e.target.value)) ?? null;
          onChange(selected);
        }}
      >
        <option value="">{loading ? 'Buscando…' : options.length ? 'Seleccione un usuario' : 'Sin resultados'}</option>
        {options.map((u) => (
          <option key={u.id} value={u.id}>
            {u.username ? `${u.display_name} (${u.username})` : u.display_name}
          </option>
        ))}
      </select>
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}
