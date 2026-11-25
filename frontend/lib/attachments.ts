/**
 * Adjuntos de ticket: tipos, límites y subida con progreso. Lo usan el panel de adjuntos del
 * detalle del ticket y el formulario de alta (que sube los archivos justo después de crear el ticket).
 */
import { apiFetch, apiUrl } from '@/lib/api';
import { getAuthHeaders } from '@/lib/auth';
import type { UserRef } from '@/lib/types';

/** GET /tickets/{id}/attachments */
export interface Attachment {
  id: number;
  ticket_id: number;
  filename: string;
  size: number;
  mime: string;
  checksum: string;
  uploaded_by: UserRef;
  created_at: string;
  can_delete: boolean;
}

/** GET /attachments/settings */
export interface AttachmentSettings {
  max_mb: number;
  max_bytes: number;
  max_per_ticket: number;
  allowed_extensions: string[];
}

export const FALLBACK_ATTACHMENT_SETTINGS: AttachmentSettings = {
  max_mb: 20,
  max_bytes: 20 * 1024 * 1024,
  max_per_ticket: 50,
  allowed_extensions: [],
};

/** Límites del servidor; si fallan se usan los valores por defecto (el backend valida igualmente). */
export async function fetchAttachmentSettings(): Promise<AttachmentSettings> {
  try {
    return await apiFetch<AttachmentSettings>('/attachments/settings');
  } catch {
    return FALLBACK_ATTACHMENT_SETTINGS;
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString('es-GT', { maximumFractionDigits: value < 10 ? 1 : 0 })} ${units[unit]}`;
}

export function extensionOf(name: string): string {
  const index = name.lastIndexOf('.');
  return index > 0 ? name.slice(index + 1).toLowerCase() : '';
}

/** Validación previa en el navegador (tipo, vacío, tamaño). `null` si el archivo es aceptable. */
export function precheckAttachment(file: File, settings: AttachmentSettings): string | null {
  const ext = extensionOf(file.name);
  if (settings.allowed_extensions.length > 0 && !settings.allowed_extensions.includes(ext)) {
    return `Tipo no permitido (.${ext || 'sin extensión'}).`;
  }
  if (file.size === 0) return 'El archivo está vacío.';
  if (file.size > settings.max_bytes) return `Supera el máximo de ${settings.max_mb} MB.`;
  return null;
}

/** Valor del atributo `accept` del input de archivos. */
export function acceptAttribute(settings: AttachmentSettings): string | undefined {
  return settings.allowed_extensions.map((ext) => `.${ext}`).join(',') || undefined;
}

/** Mensaje legible de una respuesta de error de la API (JSON `{detail}`). */
function xhrErrorMessage(xhr: XMLHttpRequest): string {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail) return body.detail;
  } catch {
    /* respuesta no JSON */
  }
  switch (xhr.status) {
    case 0:
      return 'No se pudo conectar con la API.';
    case 401:
      return 'Su sesión expiró. Inicie sesión nuevamente.';
    case 403:
      return 'No tiene permisos para adjuntar archivos a este ticket.';
    case 413:
      return 'El archivo excede el tamaño permitido.';
    case 415:
      return 'Tipo de archivo no permitido.';
    default:
      return `Error al subir el archivo (${xhr.status}).`;
  }
}

/** Sube un archivo con XMLHttpRequest para poder reportar el progreso. */
export async function uploadAttachment(
  ticketId: number,
  file: File,
  onProgress: (fraction: number) => void = () => {},
  register: (xhr: XMLHttpRequest) => void = () => {},
): Promise<Attachment> {
  const headers = await getAuthHeaders();
  return new Promise<Attachment>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    register(xhr);
    xhr.open('POST', apiUrl(`/tickets/${ticketId}/attachments`));
    xhr.setRequestHeader('Accept', 'application/json');
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Attachment);
        } catch {
          reject(new Error('Respuesta inválida del servidor.'));
        }
      } else {
        reject(new Error(xhrErrorMessage(xhr)));
      }
    };
    xhr.onerror = () => reject(new Error(xhrErrorMessage(xhr)));
    xhr.onabort = () => reject(new Error('Subida cancelada.'));
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}
