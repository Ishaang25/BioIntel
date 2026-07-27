/** Presentational primitives shared across the app. */

import Link from 'next/link';
import type { ReactNode } from 'react';

import type { CredibilityBand, QuestionPriority, RiskSeverity, Stance } from '@/lib/types';

/* ------------------------------------------------------------------ score --- */

const BAND_STYLES: Record<CredibilityBand, string> = {
  strong: 'border-band-strong/30 bg-band-strong/10 text-band-strong',
  moderate: 'border-band-moderate/30 bg-band-moderate/10 text-band-moderate',
  limited: 'border-band-limited/30 bg-band-limited/10 text-band-limited',
  weak: 'border-band-weak/30 bg-band-weak/10 text-band-weak',
  unsupported: 'border-band-unsupported/30 bg-band-unsupported/10 text-band-unsupported',
};

const BAND_LABEL: Record<CredibilityBand, string> = {
  strong: 'Strong',
  moderate: 'Moderate',
  limited: 'Limited',
  weak: 'Weak',
  unsupported: 'Unsupported',
};

export function BandChip({ band, score }: { band: CredibilityBand; score?: number }) {
  return (
    <span className={`chip ${BAND_STYLES[band]}`}>
      {score !== undefined && <span className="font-mono">{score.toFixed(0)}</span>}
      {BAND_LABEL[band]}
    </span>
  );
}

export function ScoreDial({ score, band }: { score: number; band: CredibilityBand }) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.max(0, Math.min(100, score)) / 100) * circumference;
  const colour = {
    strong: '#15803d',
    moderate: '#0369a1',
    limited: '#a16207',
    weak: '#c2410c',
    unsupported: '#b91c1c',
  }[band];

  return (
    <div className="flex items-center gap-4">
      <svg width="128" height="128" viewBox="0 0 128 128" role="img" aria-label={`Score ${score.toFixed(0)} of 100`}>
        <circle cx="64" cy="64" r={radius} fill="none" stroke="currentColor" strokeWidth="10" className="text-ink-200 dark:text-ink-800" />
        <circle
          cx="64"
          cy="64"
          r={radius}
          fill="none"
          stroke={colour}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference}`}
          transform="rotate(-90 64 64)"
        />
        <text x="64" y="60" textAnchor="middle" className="fill-current text-2xl font-semibold" style={{ fontSize: 26 }}>
          {score.toFixed(0)}
        </text>
        <text x="64" y="82" textAnchor="middle" className="fill-current text-ink-500" style={{ fontSize: 11 }}>
          / 100
        </text>
      </svg>
      <div>
        <div className="label">Scientific credibility</div>
        <div className="mt-1 text-lg font-semibold">{BAND_LABEL[band]}</div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- stance --- */

const STANCE_STYLES: Record<Stance, string> = {
  supports: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  contradicts: 'border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400',
  mixed: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  neutral: 'border-ink-400/30 bg-ink-400/10 text-ink-600 dark:text-ink-400',
  unrelated: 'border-ink-300/30 bg-ink-300/10 text-ink-500',
};

export function StanceChip({ stance }: { stance: Stance }) {
  return <span className={`chip ${STANCE_STYLES[stance]}`}>{stance}</span>;
}

/* --------------------------------------------------------------- severity --- */

const SEVERITY_STYLES: Record<RiskSeverity, string> = {
  critical: 'border-red-600/40 bg-red-600/10 text-red-700 dark:text-red-400',
  high: 'border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-400',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  low: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-400',
  info: 'border-ink-400/40 bg-ink-400/10 text-ink-600 dark:text-ink-400',
};

export function SeverityChip({ severity }: { severity: RiskSeverity }) {
  return <span className={`chip ${SEVERITY_STYLES[severity]}`}>{severity}</span>;
}

const PRIORITY_STYLES: Record<QuestionPriority, string> = {
  critical: SEVERITY_STYLES.critical,
  high: SEVERITY_STYLES.high,
  medium: SEVERITY_STYLES.medium,
  low: SEVERITY_STYLES.low,
};

export function PriorityChip({ priority }: { priority: QuestionPriority }) {
  return <span className={`chip ${PRIORITY_STYLES[priority]}`}>{priority}</span>;
}

/* ---------------------------------------------------------------- generic --- */

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'accent' }) {
  const styles =
    tone === 'accent'
      ? 'border-ink-900/20 bg-ink-900/5 text-ink-800 dark:border-ink-100/20 dark:bg-ink-100/10 dark:text-ink-100'
      : 'border-ink-200 bg-ink-100 text-ink-600 dark:border-ink-800 dark:bg-ink-800 dark:text-ink-300';
  return <span className={`chip ${styles}`}>{children}</span>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-500">{hint}</div>}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center gap-3 py-14 text-center">
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="max-w-md text-sm text-ink-500">{description}</p>
      {action}
    </div>
  );
}

export function Callout({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warning' | 'danger';
  title: string;
  children: ReactNode;
}) {
  const styles = {
    info: 'border-sky-500/30 bg-sky-500/5',
    warning: 'border-amber-500/40 bg-amber-500/5',
    danger: 'border-red-500/40 bg-red-500/5',
  }[tone];
  return (
    <div className={`rounded-lg border px-4 py-3 ${styles}`}>
      <div className="text-sm font-semibold">{title}</div>
      <div className="mt-1 text-sm text-ink-600 dark:text-ink-400">{children}</div>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  back,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  back?: { href: string; label: string };
}) {
  return (
    <header className="mb-6">
      {back && (
        <Link href={back.href} className="mb-2 inline-block text-sm text-ink-500 hover:text-ink-900 dark:hover:text-ink-100">
          ← {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <div className="mt-1 text-sm text-ink-500">{subtitle}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

/* ----------------------------------------------------------------- format --- */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function humanise(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
