'use client';

import { useCallback, useId, useRef, useState, type ReactNode } from 'react';

import { Portal, useFloatingPosition } from './Floating';
import { cx } from './primitives';

/**
 * Explanatory tooltip for dense numeric UI.
 *
 * Opens on hover *and* on keyboard focus, and is described by `aria-describedby`
 * so the content is not sighted-mouse-only. Content is rich (a node, not a
 * string) because the score dimensions need a heading plus a sentence.
 */
export function Tooltip({
  content,
  children,
  className,
  width = 300,
}: {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const [layer, setLayer] = useState<HTMLDivElement | null>(null);
  const id = useId();
  const position = useFloatingPosition(anchorRef.current, layer, open, 6);

  const show = useCallback(() => setOpen(true), []);
  const hide = useCallback(() => setOpen(false), []);

  return (
    <>
      <span
        ref={anchorRef}
        className={cx('inline-flex', className)}
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        {children}
      </span>
      {open && (
        <Portal>
          <div
            ref={setLayer}
            id={id}
            role="tooltip"
            style={{
              position: 'fixed',
              top: position?.top ?? -9999,
              left: position?.left ?? -9999,
              width,
              visibility: position ? 'visible' : 'hidden',
            }}
            className="z-50 rounded-lg border border-line bg-panel p-3 text-[12.5px] leading-relaxed text-fg-2 shadow-pop"
          >
            {content}
          </div>
        </Portal>
      )}
    </>
  );
}

/** A small `?` affordance that carries a tooltip. Use beside a label. */
export function InfoHint({ content }: { content: ReactNode }) {
  return (
    <Tooltip content={content}>
      <span
        aria-hidden
        className="flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-line text-[9px] font-semibold text-fg-3 transition-colors hover:border-line-strong hover:text-fg-2"
      >
        ?
      </span>
    </Tooltip>
  );
}
