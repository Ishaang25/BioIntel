/**
 * Uploading a deck.
 *
 * ## Why this is not one `fetch`
 *
 * The browser talks to the API through `/api/proxy`, a Next route handler that
 * holds the API key. On Vercel that handler is a Serverless Function, and a
 * Serverless Function may receive at most **4.5 MB** of request body. The cap
 * is imposed at the edge, is identical on every plan, and cannot be raised —
 * the request is rejected with `413` before the function is invoked, so no
 * amount of configuration in Next.js or FastAPI changes the outcome. The API
 * itself accepts 50 MB.
 *
 * That is the whole of the reported bug: 1–4 MB decks are under the cap and
 * work, a 6.1 MB deck is over it and does not, and the same file compressed
 * drops back under and works again. Nothing about the document mattered.
 *
 * So there are two paths:
 *
 * - **Small file → proxy.** Unchanged, works everywhere including a
 *   self-hosted `next start` where no such cap exists.
 * - **Large file → direct.** The server mints a single-use ticket
 *   (`/api/upload-ticket`) and the browser posts the file straight to the API,
 *   never touching the function that would reject it.
 *
 * If the direct path is not available — no publicly reachable API, or CORS not
 * configured for this origin — the caller is told exactly what the limit is and
 * what to do, which is the one outcome that must never regress into an
 * unexplained `413`.
 *
 * `XMLHttpRequest` rather than `fetch` because it is still the only way to
 * observe upload progress in a browser; a multi-megabyte upload with no
 * feedback reads as a hang.
 */

import type { UploadResponse } from './types';

/**
 * Largest body we will send through the Next proxy.
 *
 * Vercel's ceiling is 4.5 MB. Sitting a little under it leaves room for the
 * multipart envelope — boundaries, field names, the notes field — so the
 * decision made here is the decision the edge makes.
 */
export const PROXY_BODY_LIMIT_BYTES = 4 * 1024 * 1024;

/** Ceiling the API enforces; overridden by whatever a ticket reports. */
export const API_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024;

/** Abort an upload that has made no progress for this long. */
const UPLOAD_TIMEOUT_MS = 180_000;

export type UploadFailureKind =
  | 'too_large'
  | 'unsupported_file'
  | 'rate_limited'
  | 'network'
  | 'timeout'
  | 'cancelled'
  | 'server';

export class UploadError extends Error {
  constructor(
    readonly kind: UploadFailureKind,
    message: string,
    /** Effective ceiling in bytes, when the failure was a size failure. */
    readonly limitBytes?: number,
  ) {
    super(message);
    this.name = 'UploadError';
  }
}

export interface UploadProgress {
  /** 0–1, or null while the total is unknown. */
  fraction: number | null;
  loaded: number;
  total: number | null;
}

export interface UploadOptions {
  notes?: string;
  analyze?: boolean;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

export function formatMegabytes(bytes: number): string {
  const mb = bytes / 1_048_576;
  return mb >= 10 ? mb.toFixed(0) : mb.toFixed(1);
}

/** The message the product shows whenever a file is simply too big. */
export function tooLargeMessage(limitBytes: number): string {
  return (
    `This PDF exceeds the upload size limit. Maximum supported size: ` +
    `${formatMegabytes(limitBytes)} MB. Please compress the PDF and try again.`
  );
}

interface DirectUploadTicket {
  uploadUrl: string;
  token: string;
  expiresInSeconds: number;
  maxBytes: number;
}

/**
 * Asks the server for a direct-upload ticket.
 *
 * Returns `null` when direct upload is not available in this deployment, which
 * is a supported configuration rather than an error: the caller falls back to
 * explaining the proxy's limit.
 */
async function requestTicket(signal?: AbortSignal): Promise<DirectUploadTicket | null> {
  let response: Response;
  try {
    response = await fetch('/api/upload-ticket', {
      method: 'POST',
      cache: 'no-store',
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    if (isAbort(cause)) throw new UploadError('cancelled', 'Upload cancelled.');
    return null;
  }
  if (!response.ok) return null;
  try {
    return (await response.json()) as DirectUploadTicket;
  } catch {
    return null;
  }
}

/**
 * Sends a deck for analysis, choosing the path that can actually carry it.
 *
 * Throws {@link UploadError} — never a bare status code — so every caller has
 * something specific to say to the reader.
 */
export async function uploadDocument(
  file: File,
  options: UploadOptions = {},
): Promise<UploadResponse> {
  const { notes = '', analyze = true, onProgress, signal } = options;

  if (file.size <= PROXY_BODY_LIMIT_BYTES) {
    return sendMultipart('/api/proxy/documents', file, {
      notes,
      analyze,
      onProgress,
      signal,
      limitBytes: API_UPLOAD_LIMIT_BYTES,
    });
  }

  const ticket = await requestTicket(signal);

  if (!ticket) {
    // No route exists that can carry this file. Say so precisely: the ceiling
    // quoted is the one that actually applied to this attempt.
    throw new UploadError(
      'too_large',
      tooLargeMessage(PROXY_BODY_LIMIT_BYTES),
      PROXY_BODY_LIMIT_BYTES,
    );
  }

  if (file.size > ticket.maxBytes) {
    throw new UploadError('too_large', tooLargeMessage(ticket.maxBytes), ticket.maxBytes);
  }

  return sendMultipart(ticket.uploadUrl, file, {
    notes,
    analyze,
    onProgress,
    signal,
    ticket: ticket.token,
    limitBytes: ticket.maxBytes,
    // A direct POST crosses an origin. If the API has not been told to allow
    // this one, the browser reports an opaque network error and never reveals
    // why — so that specific failure gets its own explanation.
    crossOrigin: true,
  });
}

interface SendOptions {
  notes: string;
  analyze: boolean;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
  ticket?: string;
  limitBytes: number;
  crossOrigin?: boolean;
}

function sendMultipart(url: string, file: File, options: SendOptions): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  if (options.notes) form.append('notes', options.notes);
  form.append('analyze', String(options.analyze));

  return new Promise<UploadResponse>((resolve, reject) => {
    // An already-aborted signal never fires `abort` again, so a request
    // cancelled while the ticket was being fetched would otherwise be sent.
    if (options.signal?.aborted) {
      reject(new UploadError('cancelled', 'Upload cancelled.'));
      return;
    }

    const request = new XMLHttpRequest();
    request.open('POST', url, true);
    request.responseType = 'text';
    request.timeout = UPLOAD_TIMEOUT_MS;
    request.setRequestHeader('Accept', 'application/json');
    if (options.ticket) request.setRequestHeader('X-Upload-Ticket', options.ticket);

    const abort = () => request.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    const cleanup = () => options.signal?.removeEventListener('abort', abort);

    request.upload.onprogress = (event) => {
      options.onProgress?.({
        fraction: event.lengthComputable ? event.loaded / event.total : null,
        loaded: event.loaded,
        total: event.lengthComputable ? event.total : null,
      });
    };

    request.onload = () => {
      cleanup();
      if (request.status >= 200 && request.status < 300) {
        try {
          resolve(JSON.parse(request.responseText) as UploadResponse);
        } catch {
          reject(new UploadError('server', 'The API returned a response we could not read.'));
        }
        return;
      }
      reject(errorForStatus(request.status, request.responseText, options.limitBytes));
    };

    request.onerror = () => {
      cleanup();
      reject(
        new UploadError(
          'network',
          options.crossOrigin
            ? 'The connection to the BioIntel API was refused. Large uploads go straight to the API, ' +
              "which must allow this site's origin. Check the CORS_ORIGINS setting on the API."
            : 'The connection dropped during the upload. Check your network and try again.',
        ),
      );
    };

    request.ontimeout = () => {
      cleanup();
      reject(
        new UploadError(
          'timeout',
          'The upload timed out. This usually means a slow connection rather than a problem with the file.',
        ),
      );
    };

    request.onabort = () => {
      cleanup();
      reject(new UploadError('cancelled', 'Upload cancelled.'));
    };

    request.send(form);
  });
}

/**
 * Turns a status code into something a person can act on.
 *
 * A `413` here is the one that matters. It arrives with a non-JSON body when
 * the platform produced it rather than the API, which is precisely the case
 * that used to surface as "Request failed with status 413".
 */
export function errorForStatus(
  status: number,
  body: string,
  limitBytes: number,
): UploadError {
  const parsed = parseJson(body);
  const apiMessage = typeof parsed?.message === 'string' ? parsed.message : null;
  const apiLimit =
    typeof parsed?.detail === 'object' && parsed.detail !== null
      ? (parsed.detail as Record<string, unknown>).limit_bytes
      : undefined;

  if (status === 413) {
    const effective = typeof apiLimit === 'number' ? apiLimit : limitBytes;
    return new UploadError('too_large', tooLargeMessage(effective), effective);
  }
  if (status === 415) {
    return new UploadError(
      'unsupported_file',
      apiMessage ?? 'That file is not a PDF BioIntel can read. Export it as a standard PDF and retry.',
    );
  }
  if (status === 429) {
    return new UploadError(
      'rate_limited',
      apiMessage ?? 'Too many uploads in a short window. Wait a minute and try again.',
    );
  }
  if (status === 401 || status === 403) {
    return new UploadError(
      'server',
      'This upload was not authorised. The link may have expired — reload the page and try again.',
    );
  }
  if (status === 0) {
    return new UploadError('network', 'The upload could not reach the BioIntel API.');
  }
  if (status >= 500) {
    return new UploadError(
      'server',
      apiMessage ?? 'The BioIntel API failed while accepting the upload. Try again in a moment.',
    );
  }
  return new UploadError('server', apiMessage ?? `The upload was rejected (HTTP ${status}).`);
}

function parseJson(body: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(body);
    return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function isAbort(cause: unknown): boolean {
  return cause instanceof Error && (cause.name === 'AbortError' || cause.name === 'TimeoutError');
}
