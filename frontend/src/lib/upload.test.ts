/**
 * Upload routing and failure reporting.
 *
 * The defect these cover: a 6.1 MB deck was rejected with a bare
 * `413 Payload Too Large` by Vercel's edge — before the proxy function ran, so
 * with a non-JSON body — and the UI showed "Request failed with status 413".
 * Two things must hold now. A file over the proxy's ceiling must not be sent
 * through the proxy at all, and any 413 that still occurs must produce a
 * message that names the limit and says what to do.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  API_UPLOAD_LIMIT_BYTES,
  PROXY_BODY_LIMIT_BYTES,
  UploadError,
  errorForStatus,
  formatMegabytes,
  tooLargeMessage,
  uploadDocument,
} from './upload';

/* ------------------------------------------------------------- fake XHR --- */

interface Sent {
  url: string;
  headers: Record<string, string>;
}

const sent: Sent[] = [];

/** How the next `send()` should resolve. */
let outcome: { status: number; body: string } | 'network-error' = {
  status: 201,
  body: JSON.stringify({ document: { id: 'doc_1' }, created: true, run: { id: 'run_1' } }),
};

class FakeXhr {
  status = 0;
  responseText = '';
  responseType = '';
  timeout = 0;
  upload = { onprogress: null as ((event: unknown) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;

  private url = '';
  private headers: Record<string, string> = {};

  open(_method: string, url: string) {
    this.url = url;
  }

  setRequestHeader(key: string, value: string) {
    this.headers[key.toLowerCase()] = value;
  }

  send() {
    sent.push({ url: this.url, headers: this.headers });
    this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    queueMicrotask(() => {
      if (outcome === 'network-error') {
        this.onerror?.();
        return;
      }
      this.status = outcome.status;
      this.responseText = outcome.body;
      this.onload?.();
    });
  }

  abort() {
    this.onabort?.();
  }
}

function fileOfSize(bytes: number): File {
  return new File([new Uint8Array(bytes)], 'deck.pdf', { type: 'application/pdf' });
}

beforeEach(() => {
  sent.length = 0;
  outcome = {
    status: 201,
    body: JSON.stringify({ document: { id: 'doc_1' }, created: true, run: { id: 'run_1' } }),
  };
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ tests --- */

describe('routing', () => {
  it('sends a small file through the proxy, which needs no ticket', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        throw new Error('a small upload must not ask for a ticket');
      }),
    );

    await uploadDocument(fileOfSize(1024));

    expect(sent).toHaveLength(1);
    expect(sent[0]!.url).toBe('/api/proxy/documents');
    expect(sent[0]!.headers['x-upload-ticket']).toBeUndefined();
  });

  it('sends a file over the proxy ceiling straight to the API with a ticket', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          uploadUrl: 'https://api.example.com/api/v1/documents',
          token: 'ticket-abc',
          expiresInSeconds: 900,
          maxBytes: API_UPLOAD_LIMIT_BYTES,
        }),
      ),
    );

    await uploadDocument(fileOfSize(PROXY_BODY_LIMIT_BYTES + 1));

    expect(sent).toHaveLength(1);
    expect(sent[0]!.url).toBe('https://api.example.com/api/v1/documents');
    expect(sent[0]!.headers['x-upload-ticket']).toBe('ticket-abc');
  });

  it('explains the limit instead of sending a doomed request when no ticket is available', async () => {
    // 501: this deployment has no publicly reachable API for the browser.
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 501 })));

    const error = await uploadDocument(fileOfSize(PROXY_BODY_LIMIT_BYTES + 1)).catch((e) => e);

    expect(error).toBeInstanceOf(UploadError);
    expect((error as UploadError).kind).toBe('too_large');
    expect((error as UploadError).limitBytes).toBe(PROXY_BODY_LIMIT_BYTES);
    expect(sent).toHaveLength(0);
  });

  it('refuses a file above the ceiling the ticket itself reports', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          uploadUrl: 'https://api.example.com/api/v1/documents',
          token: 'ticket-abc',
          expiresInSeconds: 900,
          maxBytes: 8 * 1024 * 1024,
        }),
      ),
    );

    const error = await uploadDocument(fileOfSize(9 * 1024 * 1024)).catch((e) => e);

    expect((error as UploadError).kind).toBe('too_large');
    expect((error as UploadError).message).toContain('8.0 MB');
    expect(sent).toHaveLength(0);
  });

  it('names CORS when the direct upload cannot reach the API at all', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          uploadUrl: 'https://api.example.com/api/v1/documents',
          token: 'ticket-abc',
          expiresInSeconds: 900,
          maxBytes: API_UPLOAD_LIMIT_BYTES,
        }),
      ),
    );
    outcome = 'network-error';

    const error = await uploadDocument(fileOfSize(PROXY_BODY_LIMIT_BYTES + 1)).catch((e) => e);

    expect((error as UploadError).kind).toBe('network');
    expect((error as UploadError).message).toContain('CORS_ORIGINS');
  });

  it('does not send a request that was cancelled while the ticket was in flight', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        controller.abort();
        return jsonResponse({
          uploadUrl: 'https://api.example.com/api/v1/documents',
          token: 'ticket-abc',
          expiresInSeconds: 900,
          maxBytes: API_UPLOAD_LIMIT_BYTES,
        });
      }),
    );

    const error = await uploadDocument(fileOfSize(PROXY_BODY_LIMIT_BYTES + 1), {
      signal: controller.signal,
    }).catch((e) => e);

    expect((error as UploadError).kind).toBe('cancelled');
    expect(sent).toHaveLength(0);
  });

  it('reports upload progress as bytes leave the browser', async () => {
    vi.stubGlobal('fetch', vi.fn());
    const seen: Array<number | null> = [];

    await uploadDocument(fileOfSize(1024), { onProgress: (p) => seen.push(p.fraction) });

    expect(seen).toEqual([0.5]);
  });
});

describe('errorForStatus', () => {
  it('turns a platform 413 with no JSON body into a usable instruction', () => {
    // This is exactly what Vercel's edge returns: HTML, not our error shape.
    const error = errorForStatus(413, '<html>Payload Too Large</html>', PROXY_BODY_LIMIT_BYTES);

    expect(error.kind).toBe('too_large');
    expect(error.message).toBe(
      'This PDF exceeds the upload size limit. Maximum supported size: 4.0 MB. ' +
        'Please compress the PDF and try again.',
    );
  });

  it('prefers the limit the API reports over the one assumed', () => {
    const body = JSON.stringify({
      code: 'document_too_large',
      message: 'The upload exceeds the 50 MB limit.',
      detail: { limit_bytes: 50 * 1024 * 1024 },
    });

    expect(errorForStatus(413, body, PROXY_BODY_LIMIT_BYTES).limitBytes).toBe(50 * 1024 * 1024);
  });

  it.each([
    [415, 'unsupported_file'],
    [429, 'rate_limited'],
    [401, 'server'],
    [500, 'server'],
    [0, 'network'],
  ] as const)('maps %i to %s', (status, kind) => {
    expect(errorForStatus(status, '', PROXY_BODY_LIMIT_BYTES).kind).toBe(kind);
  });

  it('never leaves the reader with only a status code', () => {
    expect(errorForStatus(418, '', PROXY_BODY_LIMIT_BYTES).message).toMatch(/rejected/);
  });
});

describe('formatting', () => {
  it.each([
    [4 * 1024 * 1024, '4.0'],
    [6.1 * 1024 * 1024, '6.1'],
    [50 * 1024 * 1024, '50'],
  ])('formats %i bytes as %s MB', (bytes, expected) => {
    expect(formatMegabytes(bytes)).toBe(expected);
  });

  it('states the limit and the remedy', () => {
    expect(tooLargeMessage(50 * 1024 * 1024)).toContain('Maximum supported size: 50 MB');
    expect(tooLargeMessage(50 * 1024 * 1024)).toContain('compress');
  });
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}
