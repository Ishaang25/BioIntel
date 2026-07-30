/**
 * Charts, hand-drawn in SVG.
 *
 * The report needs three shapes — a proportion ring, a ranked bar list and a
 * stacked bar. A charting library would add ~100 kB to a page that renders
 * three static figures, so these are built from primitives and inherit the
 * theme tokens directly.
 */

import type { ReactNode } from 'react';

import type { Slice, Tone } from '@/lib/report-model';
import { cx, toneClasses } from './primitives';

const STROKE: Record<Tone, string> = {
  pos: 'stroke-pos',
  info: 'stroke-info',
  warn: 'stroke-warn',
  alert: 'stroke-alert',
  crit: 'stroke-crit',
  neutral: 'stroke-fg-3',
};

function total(slices: Slice[]): number {
  return slices.reduce((sum, slice) => sum + slice.value, 0);
}

/* ------------------------------------------------------------------- donut --- */

export function DonutChart({
  slices,
  size = 132,
  thickness = 14,
  centreValue,
  centreLabel,
}: {
  slices: Slice[];
  size?: number;
  thickness?: number;
  centreValue?: ReactNode;
  centreLabel?: string;
}) {
  const sum = total(slices);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const gap = slices.length > 1 ? 1.5 : 0;

  let offset = 0;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="presentation">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={thickness}
          className="stroke-subtle"
        />
        {sum > 0 &&
          slices.map((slice) => {
            const length = (slice.value / sum) * circumference;
            const dash = Math.max(0, length - gap);
            const element = (
              <circle
                key={slice.key}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                strokeWidth={thickness}
                strokeDasharray={`${dash} ${circumference - dash}`}
                strokeDashoffset={-offset}
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
                className={STROKE[slice.tone]}
              >
                <title>{`${slice.label}: ${slice.value}`}</title>
              </circle>
            );
            offset += length;
            return element;
          })}
      </svg>
      {(centreValue !== undefined || centreLabel) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="num text-2xl font-semibold leading-none text-fg">{centreValue}</span>
          {centreLabel && <span className="mt-1 text-2xs text-fg-3">{centreLabel}</span>}
        </div>
      )}
    </div>
  );
}

export function ChartLegend({ slices, sum }: { slices: Slice[]; sum?: number }) {
  const denominator = sum ?? total(slices);
  return (
    <ul className="min-w-0 flex-1 space-y-1.5">
      {slices.map((slice) => (
        <li key={slice.key} className="flex items-baseline gap-2 text-[13px]" title={slice.hint}>
          <span
            aria-hidden
            className={cx('mt-1.5 h-2 w-2 shrink-0 rounded-sm', toneClasses.fill[slice.tone])}
          />
          <span className="min-w-0 flex-1 truncate text-fg-2">{slice.label}</span>
          <span className="num font-medium text-fg">{slice.value}</span>
          {denominator > 0 && (
            <span className="num w-9 shrink-0 text-right text-2xs text-fg-3">
              {Math.round((slice.value / denominator) * 100)}%
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------------- bar list --- */

export function BarList({
  slices,
  onSelect,
  activeKey,
}: {
  slices: Slice[];
  onSelect?: (key: string) => void;
  activeKey?: string | null;
}) {
  const max = Math.max(1, ...slices.map((slice) => slice.value));
  const sum = total(slices);

  return (
    <ul className="space-y-1">
      {slices.map((slice) => {
        const width = (slice.value / max) * 100;
        const share = sum > 0 ? Math.round((slice.value / sum) * 100) : 0;
        const content = (
          <>
            <span
              aria-hidden
              className={cx(
                'absolute inset-y-0 left-0 rounded-[3px]',
                toneClasses.fill[slice.tone],
                slice.tone === 'neutral' ? 'opacity-[0.16]' : 'opacity-[0.18]',
              )}
              style={{ width: `${width}%` }}
            />
            <span className="relative z-10 min-w-0 flex-1 truncate text-fg">{slice.label}</span>
            <span className="relative z-10 num font-medium text-fg">{slice.value}</span>
            <span className="relative z-10 num w-9 shrink-0 text-right text-2xs text-fg-3">
              {share}%
            </span>
          </>
        );

        const base =
          'relative flex items-center gap-2 overflow-hidden rounded-[5px] px-2 py-1.5 text-[13px]';

        return (
          <li key={slice.key} title={slice.hint}>
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(slice.key)}
                className={cx(
                  base,
                  'w-full text-left transition-colors hover:bg-subtle',
                  activeKey === slice.key && 'ring-1 ring-inset ring-line-strong',
                )}
              >
                {content}
              </button>
            ) : (
              <div className={base}>{content}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* -------------------------------------------------------------- stacked bar --- */

export function StackedBar({ slices, height = 8 }: { slices: Slice[]; height?: number }) {
  const sum = total(slices);
  if (sum === 0) return <div className="h-2 w-full rounded-full bg-subtle" />;

  return (
    <div className="flex w-full gap-px overflow-hidden rounded-full" style={{ height }}>
      {slices.map((slice) => (
        <div
          key={slice.key}
          className={toneClasses.fill[slice.tone]}
          style={{ width: `${(slice.value / sum) * 100}%` }}
          title={`${slice.label}: ${slice.value}`}
        />
      ))}
    </div>
  );
}
