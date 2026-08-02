/**
 * Mints a direct-upload ticket on behalf of the browser.
 *
 * The browser normally reaches the API through `/api/proxy`, which attaches
 * the API key. That path cannot carry a large file: a Vercel Serverless
 * Function may receive at most 4.5 MB of request body, the limit is fixed on
 * every plan, and the request is rejected at the edge with `413` before any of
 * our code runs. The API's own ceiling is 50 MB.
 *
 * So for anything above the proxy's ceiling the browser posts the file straight
 * to the API instead. It still must not hold the API key, so this route — which
 * runs on the server and does hold it — exchanges the key for a short-lived,
 * single-use ticket and hands back only that.
 *
 * Requires the API to be reachable from the browser and to allow the app's
 * origin via CORS (`CORS_ORIGINS` on the backend). Without that, direct upload
 * is unavailable and the caller falls back to explaining the size limit.
 */

import { NextResponse } from 'next/server';

const API_URL = process.env.BIOINTEL_API_URL ?? 'http://127.0.0.1:8000';
const API_PREFIX = '/api/v1';
const API_KEY = process.env.BIOINTEL_API_KEY;

/**
 * Origin the browser should post to. Defaults to the URL the server uses,
 * which is already the public one in the standard deployment (Vercel reaches
 * the API over the internet). Set this only when the two differ — a private
 * network address internally and a public hostname externally.
 */
const PUBLIC_API_URL = process.env.BIOINTEL_PUBLIC_API_URL ?? API_URL;

export const dynamic = 'force-dynamic';
export const maxDuration = 20;

interface TicketResponse {
  token: string;
  expires_in_seconds: number;
  max_bytes: number;
}

export async function POST() {
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}${API_PREFIX}/documents/upload-ticket`, {
      method: 'POST',
      headers: {
        accept: 'application/json',
        ...(API_KEY ? { 'x-api-key': API_KEY } : {}),
      },
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return NextResponse.json(
      {
        code: 'upstream_unavailable',
        message: 'The BioIntel API is not reachable, so a large upload cannot be started.',
      },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    // An older API without this endpoint answers 404. That is not an error the
    // user can act on — it means direct upload is unavailable and the caller
    // should explain the proxy's size limit instead.
    const code = upstream.status === 404 ? 'direct_upload_unsupported' : 'ticket_rejected';
    return NextResponse.json(
      { code, message: 'The API did not issue an upload ticket.' },
      { status: upstream.status === 404 ? 501 : 502 },
    );
  }

  const ticket = (await upstream.json()) as TicketResponse;

  return NextResponse.json(
    {
      uploadUrl: `${PUBLIC_API_URL.replace(/\/+$/, '')}${API_PREFIX}/documents`,
      token: ticket.token,
      expiresInSeconds: ticket.expires_in_seconds,
      maxBytes: ticket.max_bytes,
    },
    { headers: { 'cache-control': 'no-store' } },
  );
}
