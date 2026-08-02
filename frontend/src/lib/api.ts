/**
 * Typed API client.
 *
 * Server components call the backend directly (server-to-server, no CORS, the
 * API key never reaches the browser).  Client components go through the Next
 * route handlers under `/api/proxy`, which forward with the key attached.
 */

import type {
  ClaimDetail,
  Claim,
  DocumentDetail,
  DocumentSummary,
  Entity,
  Evidence,
  Health,
  Paginated,
  Question,
  Report,
  Risk,
  Run,
  RunDetail,
} from './types';

const SERVER_BASE = process.env.BIOINTEL_API_URL ?? 'http://127.0.0.1:8000';
const API_PREFIX = '/api/v1';

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

/**
 * Default ceiling on a single API call.
 *
 * Every caller runs inside something with a hard limit of its own — a Vercel
 * function, or a browser tab a person is staring at. An unbounded `fetch`
 * against a saturated backend does not fail, it hangs, and the surrounding
 * function is killed with no useful error. Bounding the request turns that
 * into an ordinary handled failure.
 */
const DEFAULT_TIMEOUT_MS = 12_000;


export interface RequestOptions extends RequestInit {
  /** Seconds; omit for the Next.js default. */
  revalidate?: number;
  /** Abort after this many milliseconds. Pass 0 to wait indefinitely. */
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { revalidate, timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = options;
  const isServer = typeof window === 'undefined';
  const base = isServer ? `${SERVER_BASE}${API_PREFIX}` : '/api/proxy';

  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (isServer && process.env.BIOINTEL_API_KEY) {
    headers.set('X-API-Key', process.env.BIOINTEL_API_KEY);
  }

  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      ...init,
      headers,
      signal: init.signal ?? (timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : null),
      // Analysis state changes constantly; never serve it from a stale cache.
      cache: revalidate === undefined ? 'no-store' : undefined,
      ...(revalidate !== undefined ? { next: { revalidate } } : {}),
    });
  } catch (cause) {
    const name = cause instanceof Error ? cause.name : '';
    if (name === 'TimeoutError' || name === 'AbortError') {
      throw new ApiRequestError(
        504,
        'timeout',
        `The BioIntel API did not respond within ${Math.round(timeoutMs / 1000)}s.`,
      );
    }
    throw new ApiRequestError(503, 'network_error', 'The BioIntel API is not reachable.');
  }

  if (!response.ok) {
    let code = 'http_error';
    let message = `Request failed with status ${response.status}`;
    let detail: Record<string, unknown> | undefined;
    try {
      const body = await response.json();
      code = body.code ?? code;
      message = body.message ?? message;
      detail = body.detail;
    } catch {
      /* non-JSON error body; keep the defaults */
    }
    throw new ApiRequestError(response.status, code, message, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: (options?: RequestOptions) => request<Health>('/health', options),

  listDocuments: (limit = 50, offset = 0) =>
    request<Paginated<DocumentSummary>>(`/documents?limit=${limit}&offset=${offset}`),

  getDocument: (id: string) => request<DocumentDetail>(`/documents/${id}`),

  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: 'DELETE' }),

  listRuns: (limit = 50, offset = 0) =>
    request<Paginated<Run>>(`/runs?limit=${limit}&offset=${offset}`),

  getRun: (id: string, options?: RequestOptions) =>
    request<RunDetail>(`/runs/${id}`, options),

  createRun: (documentId: string, force = false) =>
    request<Run>('/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_id: documentId, force }),
    }),

  cancelRun: (id: string) => request<Run>(`/runs/${id}/cancel`, { method: 'POST' }),

  listClaims: (
    runId: string,
    params: { limit?: number; thesisCritical?: boolean } = {},
    options?: RequestOptions,
  ) => {
    const search = new URLSearchParams({ limit: String(params.limit ?? 200) });
    if (params.thesisCritical) search.set('thesis_critical', 'true');
    return request<Paginated<Claim>>(`/runs/${runId}/claims?${search}`, options);
  },

  getClaim: (runId: string, claimId: string, options?: RequestOptions) =>
    request<ClaimDetail>(`/runs/${runId}/claims/${claimId}`, options),

  listEntities: (runId: string, options?: RequestOptions) =>
    request<Entity[]>(`/runs/${runId}/entities`, options),
  listEvidence: (runId: string, options?: RequestOptions) =>
    request<Evidence[]>(`/runs/${runId}/evidence`, options),
  listQuestions: (runId: string, options?: RequestOptions) =>
    request<Question[]>(`/runs/${runId}/questions`, options),
  listRisks: (runId: string, options?: RequestOptions) =>
    request<Risk[]>(`/runs/${runId}/risks`, options),
  getReport: (runId: string, options?: RequestOptions) =>
    request<Report>(`/runs/${runId}/report`, options),

  reportExportUrl: (runId: string, format: 'markdown' | 'html') =>
    `/api/proxy/runs/${runId}/report/export?format=${format}`,
};

/** Fetch that tolerates a 404 (an artefact the run has not produced yet). */
export async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) return null;
    throw error;
  }
}
