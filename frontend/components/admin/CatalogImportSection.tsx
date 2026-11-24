'use client';

/**
 * Importación del catálogo desde CSV: plantilla descargable, vista previa (`dry_run`) con errores por
 * línea y aplicación. La importación es idempotente: crea o reactiva lo que falte.
 */
import Link from 'next/link';
import { useId, useRef, useState, type FormEvent } from 'react';
import { Alert } from '@/components/ui';
import { errorMessage } from '@/lib/api';
import { catalogApi, downloadImportTemplate } from '@/lib/catalog';
import { invalidateCatalog } from '@/lib/hooks';
import type { CatalogImportResult } from '@/lib/types';
import styles from './admin.module.css';

const MAX_BYTES = 2 * 1024 * 1024;

export default function CatalogImportSection() {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CatalogImportResult | null>(null);
  const [applied, setApplied] = useState<CatalogImportResult | null>(null);
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null);
  const [error, setError] = useState<string | null>(null);

  function chooseFile(next: File | null) {
    setPreview(null);
    setApplied(null);
    setError(null);
    if (next && next.size > MAX_BYTES) {
      setFile(null);
      setError('El archivo supera el máximo de 2 MB.');
      if (inputRef.current) inputRef.current.value = '';
      return;
    }
    setFile(next);
  }

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(dryRun ? 'preview' : 'apply');
    setError(null);
    try {
      const result = await catalogApi.importCatalog(file, dryRun);
      if (dryRun) {
        setPreview(result);
        setApplied(null);
      } else {
        invalidateCatalog();
        setApplied(result);
        setPreview(null);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void run(true);
  }

  function reset() {
    setFile(null);
    setPreview(null);
    setApplied(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = '';
  }

  const result = applied ?? preview;
  const nothingToDo =
    preview !== null &&
    preview.created.categories + preview.created.subcategories + preview.created.policies + preview.updated.policies === 0;

  return (
    <div className="stack">
      <section className="panel stack" aria-labelledby="import-format-title">
        <div className="row-between">
          <div>
            <h2 className="panel-title" id="import-format-title">
              Importar catálogo desde CSV
            </h2>
            <p className="panel-subtitle">
              Una fila por categoría o subcategoría. Los departamentos deben existir; lo que falte se crea y lo inactivo se
              reactiva. Puede importar el mismo archivo varias veces.
            </p>
          </div>
          <button type="button" className="btn" onClick={() => downloadImportTemplate()}>
            Descargar plantilla
          </button>
        </div>
        <div className="table-wrap">
          <table className={`table table-compact ${styles.formatTable}`}>
            <caption className="visually-hidden">Columnas del archivo CSV</caption>
            <thead>
              <tr>
                <th scope="col">Columna</th>
                <th scope="col">Contenido</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <code>departamento</code>
                </td>
                <td>Nombre del departamento de atención (obligatorio).</td>
              </tr>
              <tr>
                <td>
                  <code>categoria</code>
                </td>
                <td>Nombre de la categoría (obligatorio).</td>
              </tr>
              <tr>
                <td>
                  <code>subcategoria</code>
                </td>
                <td>Opcional. Vacío = la fila solo define la categoría.</td>
              </tr>
              <tr>
                <td>
                  <code>tipos</code>
                </td>
                <td>
                  Opcional. Claves separadas por «|»: <code>incident</code> (Incidencia), <code>service_request</code>{' '}
                  (Petición), <code>work_order</code> (Orden de trabajo). Vacío = todos los tipos.
                </td>
              </tr>
              <tr>
                <td>
                  <code>ans_horas</code>
                </td>
                <td>
                  Opcional. Horas hábiles de resolución: crea o actualiza la política de SLA «ANS departamento / categoría[ /
                  subcategoría]».
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="field-hint">
          Archivo CSV en UTF-8, separado por comas (también se acepta «;», como lo guarda Excel en español). Máximo 2 MB y
          5 000 filas.
        </p>
      </section>

      <Alert tone="info" title="ANS del catálogo">
        Las políticas «ANS …» creadas por la importación se aplican antes que las políticas generales (por ejemplo,
        las de prioridad): gana siempre la más específica. Puede revisarlas en{' '}
        <Link href="/sla?tab=policies">SLA › Políticas</Link>.
      </Alert>

      <form className="panel form" onSubmit={submit} aria-labelledby="import-file-title">
        <h2 className="panel-title" id="import-file-title">
          Archivo
        </h2>
        {error && <Alert tone="error">{error}</Alert>}
        <div className={styles.fileRow}>
          <div className="field">
            <label className="field-label" htmlFor={inputId}>
              Archivo CSV<span className="required">*</span>
            </label>
            <input
              ref={inputRef}
              id={inputId}
              className={styles.fileInput}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
            />
            {file && (
              <span className="field-hint">
                {file.name} · {(file.size / 1024).toLocaleString('es-GT', { maximumFractionDigits: 1 })} KB
              </span>
            )}
          </div>
          <div className="btn-group">
            {(file || result) && (
              <button type="button" className="btn btn-ghost" onClick={reset} disabled={busy !== null}>
                Limpiar
              </button>
            )}
            <button type="submit" className="btn btn-secondary" disabled={!file || busy !== null}>
              {busy === 'preview' ? 'Analizando…' : 'Vista previa'}
            </button>
          </div>
        </div>
      </form>

      {result && (
        <section className="panel stack" aria-live="polite" aria-labelledby="import-result-title">
          <div className="row-between">
            <h2 className="panel-title" id="import-result-title">
              {result.dry_run ? 'Vista previa (aún no se guardó nada)' : 'Importación aplicada'}
            </h2>
            {result.dry_run && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy !== null || nothingToDo}
                onClick={() => void run(false)}
              >
                {busy === 'apply' ? 'Aplicando…' : 'Aplicar importación'}
              </button>
            )}
          </div>
          {!result.dry_run && (
            <Alert tone="success">
              El catálogo se actualizó. Las categorías nuevas ya están disponibles al crear y clasificar tickets.
            </Alert>
          )}
          {nothingToDo && result.errors.length === 0 && (
            <Alert tone="info">No hay cambios: todo lo del archivo ya existe en el catálogo.</Alert>
          )}
          <div className={styles.summary}>
            <SummaryItem value={result.created.categories} label={result.dry_run ? 'Categorías a crear' : 'Categorías creadas'} />
            <SummaryItem
              value={result.created.subcategories}
              label={result.dry_run ? 'Subcategorías a crear' : 'Subcategorías creadas'}
            />
            <SummaryItem value={result.created.policies} label={result.dry_run ? 'Políticas ANS a crear' : 'Políticas ANS creadas'} />
            <SummaryItem
              value={result.updated.policies}
              label={result.dry_run ? 'Políticas ANS a actualizar' : 'Políticas ANS actualizadas'}
            />
            <SummaryItem value={result.errors.length} label="Filas con error (se omiten)" />
          </div>
          {result.errors.length > 0 && (
            <div className="table-wrap">
              <table className="table table-compact">
                <caption className="visually-hidden">Errores por línea</caption>
                <thead>
                  <tr>
                    <th scope="col" className="col-num">
                      Línea
                    </th>
                    <th scope="col">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {result.errors.map((err, index) => (
                    <tr key={`${err.line}-${index}`}>
                      <td className="col-num">{err.line}</td>
                      <td style={{ overflowWrap: 'anywhere' }}>{err.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function SummaryItem({ value, label }: { value: number; label: string }) {
  return (
    <div className={styles.summaryItem}>
      <div className={styles.summaryValue}>{value.toLocaleString('es-GT')}</div>
      <div className={styles.summaryLabel}>{label}</div>
    </div>
  );
}
