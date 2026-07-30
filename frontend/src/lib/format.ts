/** Presentation-layer formatters. Pure, dependency-free, and unit-testable. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

/**
 * The API serialises naive UTC timestamps; append the zone so browsers in
 * other locales do not read them as local time.
 */
function parseDate(value: string): Date {
  const normalised = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const parsed = new Date(normalised);
  return Number.isNaN(parsed.getTime()) ? new Date(value) : parsed;
}

export function formatDate(value: string): string {
  return parseDate(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(value: string): string {
  return parseDate(value).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatRelative(value: string): string {
  const then = parseDate(value).getTime();
  const diff = Date.now() - then;
  const minutes = Math.round(diff / 60_000);
  if (Math.abs(minutes) < 1) return 'just now';
  if (Math.abs(minutes) < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return `${days}d ago`;
  return formatDate(value);
}

/** `regulatory_approval` → `Regulatory approval`. Sentence case, not Title Case. */
export function humanise(value: string | null | undefined): string {
  if (!value) return '';
  const spaced = value.replace(/[_-]+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(0);
}

export function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1).trimEnd()}…`;
}

/**
 * First sentence of a body of prose, used for the collapsed state of risk
 * cards. Falls back to a character truncation when no terminator is found.
 */
export function firstSentence(value: string, max = 220): string {
  const text = value.trim().replace(/\s+/g, ' ');
  const match = /^(.+?[.!?])(?:\s|$)/.exec(text);
  const candidate = match?.[1] ?? text;
  return truncate(candidate, max);
}

export function pluralise(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

/** Collapses the newlines that PDF text extraction leaves inside quotes. */
export function cleanQuote(value: string): string {
  return value.replace(/\s*\n\s*/g, ' ').replace(/\s{2,}/g, ' ').trim();
}
