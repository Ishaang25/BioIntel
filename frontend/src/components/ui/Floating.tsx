'use client';

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** `useLayoutEffect` that does not warn during server rendering. */
const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

export interface FloatingPosition {
  top: number;
  left: number;
  placement: 'top' | 'bottom';
}

const MARGIN = 8;

/**
 * Positions a layer against an anchor element in viewport coordinates.
 *
 * Deliberately small: prefers below, flips above when it would overflow, and
 * clamps horizontally. Recomputes on scroll and resize while open only — an
 * idle popover costs nothing.
 */
export function useFloatingPosition(
  anchor: HTMLElement | null,
  layer: HTMLElement | null,
  open: boolean,
  gap = 8,
): FloatingPosition | null {
  const [position, setPosition] = useState<FloatingPosition | null>(null);

  useIsomorphicLayoutEffect(() => {
    if (!open || !anchor) {
      setPosition(null);
      return;
    }

    const compute = () => {
      const rect = anchor.getBoundingClientRect();
      const width = layer?.offsetWidth ?? 280;
      const height = layer?.offsetHeight ?? 120;

      const below = rect.bottom + gap;
      const above = rect.top - gap - height;
      const placement: 'top' | 'bottom' =
        below + height > window.innerHeight - MARGIN && above > MARGIN ? 'top' : 'bottom';

      // Clamp on both axes: a layer taller than the space either side of the
      // anchor should sit inside the viewport rather than run off it.
      const top = Math.max(
        MARGIN,
        Math.min(placement === 'bottom' ? below : above, window.innerHeight - height - MARGIN),
      );
      const left = Math.min(
        Math.max(MARGIN, rect.left + rect.width / 2 - width / 2),
        window.innerWidth - width - MARGIN,
      );
      setPosition({ top, left, placement });
    };

    compute();
    const onChange = () => compute();
    window.addEventListener('scroll', onChange, true);
    window.addEventListener('resize', onChange);

    // The layer's height is only final once its content has laid out; without
    // this the first frame positions against an estimate and visibly jumps.
    const observer = layer ? new ResizeObserver(compute) : null;
    if (layer && observer) observer.observe(layer);

    return () => {
      window.removeEventListener('scroll', onChange, true);
      window.removeEventListener('resize', onChange);
      observer?.disconnect();
    };
  }, [anchor, layer, open, gap]);

  return position;
}

/** Renders into `document.body` once mounted; no-ops during SSR. */
export function Portal({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return createPortal(children, document.body);
}

/** Fires when a pointer press or Escape lands outside every supplied node. */
export function useDismiss(
  open: boolean,
  onDismiss: () => void,
  nodes: Array<HTMLElement | null>,
) {
  const latest = useRef(nodes);
  latest.current = nodes;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (latest.current.some((node) => node?.contains(target))) return;
      onDismiss();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDismiss();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onDismiss]);
}
