'use client';

import { useCallback, useEffect, useRef, type ReactNode } from 'react';

import { Portal } from './Floating';
import { cx } from './primitives';

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/**
 * Right-hand detail drawer.
 *
 * Modal in the accessibility sense — focus is trapped, the page beneath is
 * inert to the keyboard and Escape closes — but visually restrained: a plain
 * panel with a hairline edge, no scrim blur, no motion beyond a short slide.
 */
export function Drawer({
  open,
  onClose,
  title,
  eyebrow,
  footer,
  children,
  width = 'lg',
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  eyebrow?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  width?: 'md' | 'lg';
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const close = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    if (!open) return;

    restoreTo.current = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';

    const panel = panelRef.current;
    const initial = panel?.querySelector<HTMLElement>('[data-autofocus]');
    if (initial) initial.focus();
    else panel?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        close();
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const targets = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (node) => node.offsetParent !== null,
      );
      if (targets.length === 0) return;
      const first = targets[0]!;
      const last = targets[targets.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.style.overflow = overflow;
      restoreTo.current?.focus?.();
    };
  }, [open, close]);

  if (!open) return null;

  return (
    <Portal>
      <div className="fixed inset-0 z-40">
        <div
          className="absolute inset-0 bg-fg/20 motion-safe:animate-[fadeIn_120ms_ease-out]"
          onClick={close}
          aria-hidden
        />
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={typeof title === 'string' ? title : 'Details'}
          tabIndex={-1}
          className={cx(
            'absolute inset-y-0 right-0 flex w-full flex-col border-l border-line bg-panel shadow-drawer outline-none',
            'motion-safe:animate-[slideIn_140ms_ease-out]',
            width === 'lg' ? 'sm:w-[620px]' : 'sm:w-[460px]',
          )}
        >
          <header className="flex items-start gap-3 border-b border-line px-6 py-4">
            <div className="min-w-0 flex-1">
              {eyebrow && <div className="mb-1.5 flex flex-wrap items-center gap-1.5">{eyebrow}</div>}
              <h2 className="text-[15px] font-semibold leading-snug tracking-[-0.01em] text-fg">
                {title}
              </h2>
            </div>
            <button
              type="button"
              onClick={close}
              data-autofocus
              aria-label="Close details"
              className="-mr-2 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-fg-3 transition-colors hover:bg-subtle hover:text-fg"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
                <path
                  d="M1.5 1.5l11 11M12.5 1.5l-11 11"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 py-5">
            {children}
          </div>

          {footer && <div className="border-t border-line px-6 py-3">{footer}</div>}
        </div>
      </div>
    </Portal>
  );
}

/** A titled block inside a drawer. Keeps the sectioning consistent. */
export function DrawerSection({
  title,
  children,
  aside,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="border-t border-line py-4 first:border-t-0 first:pt-0">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="label">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}
