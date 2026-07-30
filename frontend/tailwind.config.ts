import type { Config } from 'tailwindcss';

/**
 * Colour is expressed as semantic tokens, not a raw palette.
 *
 * Every token resolves to a CSS custom property holding an `R G B` triplet, so
 * Tailwind's opacity modifiers (`bg-panel/60`) keep working while light and
 * dark themes are swapped in one place — `globals.css`. Components never name a
 * concrete colour, which is what keeps the surface consistent as it grows.
 */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: token('c-canvas'),
        panel: token('c-panel'),
        subtle: token('c-subtle'),
        line: token('c-line'),
        'line-strong': token('c-line-strong'),
        fg: token('c-fg'),
        'fg-2': token('c-fg-2'),
        'fg-3': token('c-fg-3'),
        accent: token('c-accent'),
        pos: token('c-pos'),
        warn: token('c-warn'),
        alert: token('c-alert'),
        crit: token('c-crit'),
        info: token('c-info'),
        band: {
          strong: token('c-band-strong'),
          moderate: token('c-band-moderate'),
          limited: token('c-band-limited'),
          weak: token('c-band-weak'),
          unsupported: token('c-band-unsupported'),
        },
      },
      fontFamily: {
        sans: [
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI Variable Text',
          'Segoe UI',
          'Inter',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        serif: ['ui-serif', 'Iowan Old Style', 'Georgia', 'serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Cascadia Mono', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: {
        card: '10px',
      },
      boxShadow: {
        // Minimal by design: enterprise surfaces read as paper, not as glass.
        card: '0 1px 2px 0 rgb(var(--c-shadow) / 0.05)',
        pop: '0 4px 16px -2px rgb(var(--c-shadow) / 0.14), 0 1px 3px rgb(var(--c-shadow) / 0.08)',
        drawer: '-8px 0 32px -8px rgb(var(--c-shadow) / 0.18)',
      },
      maxWidth: {
        prose: '68ch',
      },
      transitionDuration: {
        DEFAULT: '120ms',
      },
    },
  },
  plugins: [],
};

export default config;
