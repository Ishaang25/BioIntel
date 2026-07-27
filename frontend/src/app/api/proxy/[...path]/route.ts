/**
 * Server-side proxy to the BioIntel API.
 *
 * Browser code never talks to the backend directly. This keeps the API key on
 * the server, avoids CORS entirely, and gives us one place to enforce limits
 * on what the browser is allowed to reach.
 */

import { NextRequest, NextResponse } from 'next/server';

const API_URL = process.env.BIOINTEL_API_URL ?? 'http://127.0.0.1:8000';
const API_PREFIX = '/api/v1';
const API_KEY = process.env.BIOINTEL_API_KEY;

/** Only these top-level API resources are reachable from the browser. */
const ALLOWED_ROOTS = new Set(['documents', 'runs', 'health', 'vocabularies']);

export const dynamic = 'force-dynamic';

function buildHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  const contentType = request.headers.get('content-type');
  if (contentType) headers.set('content-type', contentType);
  headers.set('accept', request.headers.get('accept') ?? 'application/json');
  if (API_KEY) headers.set('x-api-key', API_KEY);
  return headers;
}

async function forward(request: NextRequest, path: string[]): Promise<Response> {
  const root = path[0];
  if (!root || !ALLOWED_ROOTS.has(root)) {
    return NextResponse.json(
      { code: 'not_found', message: 'Unknown API resource.' },
      { status: 404 },
    );
  }

  const search = request.nextUrl.search;
  const target = `${API_URL}${API_PREFIX}/${path.join('/')}${search}`;

  const init: RequestInit = {
    method: request.method,
    headers: buildHeaders(request),
    // Streaming a request body requires duplex; safe for all methods here.
    ...(request.method !== 'GET' && request.method !== 'HEAD'
      ? { body: request.body, duplex: 'half' }
      : {}),
    cache: 'no-store',
  } as RequestInit;

  try {
    const upstream = await fetch(target, init);

    // Pass streaming responses (SSE, file downloads) straight through.
    const responseHeaders = new Headers();
    for (const key of ['content-type', 'content-disposition', 'cache-control', 'x-request-id']) {
      const value = upstream.headers.get(key);
      if (value) responseHeaders.set(key, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json(
      {
        code: 'upstream_unavailable',
        message: 'The BioIntel API is not reachable. Is the backend running?',
      },
      { status: 502 },
    );
  }
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}
