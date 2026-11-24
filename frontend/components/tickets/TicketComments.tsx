'use client';

import { useState, type FormEvent } from 'react';
import { Alert, EmptyState } from '@/components/ui';
import { apiFetch, errorMessage } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import type { CommentCreate, CommentVisibility, TicketComment } from '@/lib/types';

export default function TicketComments({
  ticketId,
  comments,
  canComment,
  canUseInternal,
  onAdded,
}: {
  ticketId: number;
  comments: TicketComment[];
  canComment: boolean;
  canUseInternal: boolean;
  onAdded: (comment: TicketComment) => void;
}) {
  const [body, setBody] = useState('');
  const [visibility, setVisibility] = useState<CommentVisibility>('public');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = body.trim();
    if (!text) {
      setError('Escriba el comentario antes de enviarlo.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const payload: CommentCreate = { body: text, visibility: canUseInternal ? visibility : 'public' };
      const comment = await apiFetch<TicketComment>(`/tickets/${ticketId}/comments`, { method: 'POST', body: payload });
      setBody('');
      onAdded(comment);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="stack">
      {comments.length === 0 ? (
        <EmptyState title="Sin comentarios">Sea el primero en comentar este ticket.</EmptyState>
      ) : (
        <div className="comment-list">
          {comments.map((c) => (
            <article key={c.id} className={`comment${c.visibility === 'internal' ? ' comment-internal' : ''}`}>
              <div className="comment-meta">
                <span className="comment-author">{c.author?.display_name ?? 'Usuario'}</span>
                <span>{formatDateTime(c.created_at)}</span>
                {c.visibility === 'internal' && <span className="badge badge-warning">Interno</span>}
              </div>
              <div className="comment-body">{c.body}</div>
            </article>
          ))}
        </div>
      )}

      {canComment && (
        <form className="form" onSubmit={submit}>
          {error && <Alert tone="error">{error}</Alert>}
          <label className="field">
            <span className="field-label">Nuevo comentario</span>
            <textarea
              className="textarea"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              maxLength={20000}
              placeholder={visibility === 'internal' ? 'Nota interna, solo visible para agentes' : 'Escriba su comentario'}
            />
          </label>
          <div className="row-between">
            {canUseInternal ? (
              <div className="row" role="radiogroup" aria-label="Visibilidad del comentario">
                <label className="checkbox">
                  <input
                    type="radio"
                    name="visibility"
                    checked={visibility === 'public'}
                    onChange={() => setVisibility('public')}
                  />
                  Público
                </label>
                <label className="checkbox">
                  <input
                    type="radio"
                    name="visibility"
                    checked={visibility === 'internal'}
                    onChange={() => setVisibility('internal')}
                  />
                  Interno (solo agentes)
                </label>
              </div>
            ) : (
              <span className="field-hint">El comentario será visible para el equipo que atiende el ticket.</span>
            )}
            <button type="submit" className="btn btn-primary" disabled={submitting || !body.trim()}>
              {submitting ? 'Enviando…' : 'Comentar'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
