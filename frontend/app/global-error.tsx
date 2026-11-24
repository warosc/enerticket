'use client';

/**
 * Error en el layout raíz (fuera de AppShell). Reemplaza todo el documento, por eso define <html>/<body>.
 */
import { useEffect } from 'react';
import './globals.css';

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error('Error no controlado en la aplicación', error);
  }, [error]);

  return (
    <html lang="es">
      <body>
        <main className="screen-center">
          <section className="panel screen-card status-page" role="alert" aria-labelledby="global-error-title">
            <h1 className="page-title" id="global-error-title">
              El portal no pudo cargarse
            </h1>
            <p className="muted">
              Ocurrió un error inesperado. Intente de nuevo en unos segundos; si persiste, comuníquelo a la Mesa de
              Servicio.
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
              <a href="/" className="btn">
                Ir al inicio
              </a>
            </div>
          </section>
        </main>
      </body>
    </html>
  );
}
