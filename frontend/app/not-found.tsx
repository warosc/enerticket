import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Página no encontrada' };

export default function NotFound() {
  return (
    <div className="page">
      <section className="panel status-page" aria-labelledby="not-found-title">
        <p className="status-page-code" aria-hidden="true">
          404
        </p>
        <h1 className="page-title" id="not-found-title">
          Página no encontrada
        </h1>
        <p className="muted">La dirección que intentó abrir no existe o fue movida.</p>
        <div className="row" style={{ justifyContent: 'center' }}>
          <Link href="/" className="btn btn-primary">
            Ir al dashboard
          </Link>
          <Link href="/tickets" className="btn">
            Ver tickets
          </Link>
        </div>
      </section>
    </div>
  );
}
