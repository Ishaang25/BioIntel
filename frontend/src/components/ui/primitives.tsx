/**
 * Presentational primitives.
 *
 * Every surface in the app is composed from these. They carry no state and no
 * data-fetching, so they render identically on the server and the client.
 */

import Link from 'next/link';
import type { ReactNode } from 'react';

import { BAND_LABEL, BAND_TONE, SEVERITY_TONE, type Tone } from '@/lib/report-model';
import type { CredibilityBand, QuestionPriority, RiskSeverity, Stance } from '@/lib/types';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

/* -------------------------------------------------------------------- tone --- */

const TONE_CHIP: Record<Tone, string> = {
  pos: 'border-pos/25 bg-pos/10 text-pos',
  info: 'border-info/25 bg-info/10 text-info',
  warn: 'border-warn/25 bg-warn/10 text-warn',
  alert: 'border-alert/25 bg-alert/10 text-alert',
  crit: 'border-crit/25 bg-crit/10 text-crit',
  neutral: 'border-line bg-subtle text-fg-2',
};

const TONE_TEXT: Record<Tone, string> = {
  pos: 'text-pos',
  info: 'text-info',
  warn: 'text-warn',
  alert: 'text-alert',
  crit: 'text-crit',
  neutral: 'text-fg-2',
};

const TONE_FILL: Record<Tone, string> = {
  pos: 'bg-pos',
  info: 'bg-info',
  warn: 'bg-warn',
  alert: 'bg-alert',
  crit: 'bg-crit',
  neutral: 'bg-fg-3',
};

const TONE_EDGE: Record<Tone, string> = {
  pos: 'border-l-pos',
  info: 'border-l-info',
  warn: 'border-l-warn',
  alert: 'border-l-alert',
  crit: 'border-l-crit',
  neutral: 'border-l-line-strong',
};

export const toneClasses = { chip: TONE_CHIP, text: TONE_TEXT, fill: TONE_FILL, edge: TONE_EDGE };

/* ------------------------------------------------------------------ badges --- */

export function Badge({
  tone = 'neutral',
  children,
  title,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span className={cx('chip', TONE_CHIP[tone], className)} title={title}>
      {children}
    </span>
  );
}

export function Dot({ tone = 'neutral' }: { tone?: Tone }) {
  return <span aria-hidden className={cx('h-1.5 w-1.5 shrink-0 rounded-full', TONE_FILL[tone])} />;
}

export function BandBadge({ band, score }: { band: CredibilityBand; score?: number | null }) {
  return (
    <Badge tone={BAND_TONE[band]}>
      {score !== undefined && score !== null && (
        <span className="num font-semibold">{score.toFixed(0)}</span>
      )}
      {BAND_LABEL[band]}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: RiskSeverity }) {
  return (
    <Badge tone={SEVERITY_TONE[severity]}>
      <Dot tone={SEVERITY_TONE[severity]} />
      {severity.charAt(0).toUpperCase() + severity.slice(1)}
    </Badge>
  );
}

const PRIORITY_TONE: Record<QuestionPriority, Tone> = {
  critical: 'crit',
  high: 'alert',
  medium: 'warn',
  low: 'info',
};

export function PriorityBadge({ priority }: { priority: QuestionPriority }) {
  return (
    <Badge tone={PRIORITY_TONE[priority]}>
      <Dot tone={PRIORITY_TONE[priority]} />
      {priority.charAt(0).toUpperCase() + priority.slice(1)}
    </Badge>
  );
}

const STANCE_TONE: Record<Stance, Tone> = {
  supports: 'pos',
  contradicts: 'crit',
  mixed: 'warn',
  neutral: 'neutral',
  unrelated: 'neutral',
};

export function StanceBadge({ stance }: { stance: Stance }) {
  return (
    <Badge tone={STANCE_TONE[stance]}>{stance.charAt(0).toUpperCase() + stance.slice(1)}</Badge>
  );
}

/* ------------------------------------------------------------------- cards --- */

export function Card({
  children,
  className,
  id,
  as: Component = 'div',
}: {
  children: ReactNode;
  className?: string;
  id?: string;
  as?: 'div' | 'section' | 'article' | 'li';
}) {
  return (
    <Component id={id} className={cx('card', className)}>
      {children}
    </Component>
  );
}

export function SectionHeading({
  title,
  description,
  actions,
  id,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  id?: string;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 id={id} className="text-[17px] font-semibold tracking-[-0.01em] text-fg">
          {title}
        </h2>
        {description && <p className="mt-1 max-w-prose text-[13px] text-fg-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ metric --- */

export function MetricCard({
  label,
  value,
  hint,
  tone = 'neutral',
  onClick,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  onClick?: () => void;
}) {
  const body = (
    <>
      {/* Labels wrap rather than truncate — "Not independently verified" is a
          distinction the reader must be able to read in full — and reserve two
          lines so the figures stay on one baseline across the row. */}
      <div className="label min-h-[1.75rem] leading-[1.35]">{label}</div>
      <div className={cx('num text-[26px] font-semibold leading-none', TONE_TEXT[tone])}>
        {value}
      </div>
      {hint && <div className="mt-2 text-2xs leading-4 text-fg-3">{hint}</div>}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="card flex h-full flex-col p-4 text-left transition-colors hover:border-line-strong hover:bg-subtle"
      >
        {body}
      </button>
    );
  }
  return <div className="card flex h-full flex-col p-4">{body}</div>;
}

/* ---------------------------------------------------------------- key/value --- */

export function KeyValue({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="label">{label}</dt>
      <dd className="mt-1 text-[13.5px] leading-relaxed text-fg">{children}</dd>
    </div>
  );
}

/* ------------------------------------------------------------------- meter --- */

export function Meter({
  value,
  tone = 'neutral',
  className,
  label,
}: {
  /** 0–100. */
  value: number;
  tone?: Tone;
  className?: string;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className={cx('h-1.5 w-full overflow-hidden rounded-full bg-subtle', className)}
      role="meter"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div className={cx('h-full rounded-full', TONE_FILL[tone])} style={{ width: `${clamped}%` }} />
    </div>
  );
}

/* ------------------------------------------------------------------ states --- */

export function EmptyState({
  title,
  description,
  action,
  compact,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={cx(
        'card flex flex-col items-center justify-center gap-2 text-center',
        compact ? 'py-8' : 'py-16',
      )}
    >
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      {description && <p className="max-w-md text-[13px] text-fg-2">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Callout({
  tone = 'info',
  title,
  children,
}: {
  tone?: Tone;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className={cx('rounded-card border border-l-2 px-4 py-3', TONE_CHIP[tone], TONE_EDGE[tone])}>
      <div className="text-[13px] font-semibold">{title}</div>
      {children && <div className="mt-1 text-[13px] leading-relaxed text-fg-2">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ header --- */

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
    <header className="mb-7">
      {back && (
        <Link
          href={back.href}
          className="mb-2 inline-flex items-center gap-1.5 text-[13px] text-fg-3 transition-colors hover:text-fg"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
            <path
              d="M10 3.5L5.5 8l4.5 4.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-fg">{title}</h1>
          {subtitle && (
            <div className="mt-1.5 max-w-prose text-[13.5px] leading-relaxed text-fg-2">
              {subtitle}
            </div>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

/* ------------------------------------------------------------------- score --- */

/**
 * The headline credibility score.
 *
 * A single arc rather than a full ring: it reads as a measurement instrument
 * instead of a progress indicator, and leaves room for the band beneath it.
 */
export function ScoreArc({
  score,
  band,
  size = 148,
}: {
  score: number;
  band: CredibilityBand;
  size?: number;
}) {
  const stroke = 9;
  const radius = (size - stroke) / 2 - 2;
  const cx0 = size / 2;
  const cy0 = size / 2;
  // 240° sweep, opening downwards.
  const sweep = 240;
  const start = 150;
  const toPoint = (angle: number) => {
    const rad = (angle * Math.PI) / 180;
    return `${cx0 + radius * Math.cos(rad)} ${cy0 + radius * Math.sin(rad)}`;
  };
  const arc = (from: number, to: number) =>
    `M ${toPoint(from)} A ${radius} ${radius} 0 ${to - from > 180 ? 1 : 0} 1 ${toPoint(to)}`;

  const clamped = Math.max(0, Math.min(100, score));
  const end = start + (sweep * clamped) / 100;

  return (
    <div className="relative inline-flex flex-col items-center" style={{ width: size }}>
      <svg
        width={size}
        height={size * 0.82}
        viewBox={`0 0 ${size} ${size * 0.82}`}
        role="img"
        aria-label={`Scientific credibility score ${clamped.toFixed(0)} out of 100, ${BAND_LABEL[band]}`}
      >
        <path
          d={arc(start, start + sweep)}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          className="stroke-line"
        />
        <path
          d={arc(start, Math.max(start + 0.1, end))}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          className={cx(
            band === 'strong' && 'stroke-band-strong',
            band === 'moderate' && 'stroke-band-moderate',
            band === 'limited' && 'stroke-band-limited',
            band === 'weak' && 'stroke-band-weak',
            band === 'unsupported' && 'stroke-band-unsupported',
          )}
        />
      </svg>
      <div className="pointer-events-none absolute inset-x-0 top-[30%] text-center">
        <div className="num text-[38px] font-semibold leading-none tracking-[-0.02em] text-fg">
          {clamped.toFixed(0)}
        </div>
        <div className="mt-1 text-2xs text-fg-3">out of 100</div>
      </div>
    </div>
  );
}
