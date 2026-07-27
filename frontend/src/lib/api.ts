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
  UploadResponse,
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

interface RequestOptions extends RequestInit {
  /** Seconds; omit for the Next.js default. */
  revalidate?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { revalidate, ...init } = options;
  const isServer = typeof window === 'undefined';
  const base = isServer ? `${SERVER_BASE}${API_PREFIX}` : '/api/proxy';

  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (isServer && process.env.BIOINTEL_API_KEY) {
    headers.set('X-API-Key', process.env.BIOINTEL_API_KEY);
  }

  const response = await fetch(`${base}${path}`, {
    ...init,
    headers,
    // Analysis state changes constantly; never serve it from a stale cache.
    cache: revalidate === undefined ? 'no-store' : undefined,
    ...(revalidate !== undefined ? { next: { revalidate } } : {}),
  });

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
  health: () => request<Health>('/health'),

  listDocuments: (limit = 50, offset = 0) =>
    request<Paginated<DocumentSummary>>(`/documents?limit=${limit}&offset=${offset}`),

  getDocument: (id: string) => request<DocumentDetail>(`/documents/${id}`),

  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: 'DELETE' }),

  uploadDocument: async (file: File, notes: string, analyze: boolean): Promise<UploadResponse> => {
    const form = new FormData();
    form.append('file', file);
    if (notes) form.append('notes', notes);
    form.append('analyze', String(analyze));
    // Let the browser set the multipart boundary.
    return request<UploadResponse>('/documents', { method: 'POST', body: form });
  },

  listRuns: (limit = 50, offset = 0) =>
    request<Paginated<Run>>(`/runs?limit=${limit}&offset=${offset}`),

  getRun: (id: string) => request<RunDetail>(`/runs/${id}`),

  createRun: (documentId: string, force = false) =>
    request<Run>('/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_id: documentId, force }),
    }),

  cancelRun: (id: string) => request<Run>(`/runs/${id}/cancel`, { method: 'POST' }),

  listClaims: (runId: string, params: { limit?: number; thesisCritical?: boolean } = {}) => {
    const search = new URLSearchParams({ limit: String(params.limit ?? 200) });
    if (params.thesisCritical) search.set('thesis_critical', 'true');
    return request<Paginated<Claim>>(`/runs/${runId}/claims?${search}`);
  },

  getClaim: (runId: string, claimId: string) =>
    request<ClaimDetail>(`/runs/${runId}/claims/${claimId}`),

  listEntities: (runId: string) => request<Entity[]>(`/runs/${runId}/entities`),
  listEvidence: (runId: string) => request<Evidence[]>(`/runs/${runId}/evidence`),
  listQuestions: (runId: string) => request<Question[]>(`/runs/${runId}/questions`),
  listRisks: (runId: string) => request<Risk[]>(`/runs/${runId}/risks`),
  getReport: (runId: string) => request<Report>(`/runs/${runId}/report`),

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
