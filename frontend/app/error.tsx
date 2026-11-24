'use client';

/** Límite de error de las páginas del portal: muestra un mensaje en español y permite reintentar. */
import Link from 'next/link';
import { useEffect } from 'react';

export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Queda en la consola del navegador para soporte; el detalle técnico no se muestra al usuario.
    console.error('Error no controlado en la página', error);
  }, [error]);

  return (
    <div className="page">
      <section className="panel status-page" role="alert" aria-labelledby="page-error-title">
        <h1 className="page-title" id="page-error-title">
          Algo salió mal
        </h1>
        <p className="muted">
          Ocurrió un error inesperado al mostrar esta sección. Puede reintentar o volver al inicio; si el problema
          persiste, comuníquelo a la Mesa de Servicio.
        </p>
        {error.digest && (
          <p className="text-xs subtle">
            Referencia del error: <span className="mono">{error.digest}</span>
          </p>
        )}
        <div className="row" style={{ justifyContent: 'center' }}>
          <button type="button" className="btn btn-primary" onClick={() => reset()}>
            Reintentar
          </button>
          <Link href="/" className="btn">
            Ir al dashboard
          </Link>
        </div>
      </section>
    </div>
  );
}
