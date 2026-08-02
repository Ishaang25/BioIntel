/**
 * Splitting the report into views made every cross-section jump depend on this
 * map: a citation opening a claim, a chart segment filtering the claim table, a
 * risk tracing to its source. If a section id here resolves to the wrong view —
 * or to none — the click silently does nothing.
 */

import { describe, expect, it } from 'vitest';

import { VIEWS, viewDef, viewForSection, viewFromHash } from './navigation';

/** Every id a section actually renders with, and the callers that target them. */
const SECTION_IDS = [
  'verdict',
  'overview',
  'summary',
  'explore',
  'score',
  'analysis',
  'risks',
  'claims',
  'evidence',
  'questions',
  'appendix',
];

describe('views', () => {
  it('starts with the summary, which is the default landing view', () => {
    expect(VIEWS[0]!.id).toBe('summary');
  });

  it('gives every view a label and a blurb the reader can act on', () => {
    for (const view of VIEWS) {
      expect(view.label.length, view.id).toBeGreaterThan(0);
      expect(view.blurb.length, view.id).toBeGreaterThan(10);
    }
  });

  it('has no duplicate ids', () => {
    expect(new Set(VIEWS.map((v) => v.id)).size).toBe(VIEWS.length);
  });

  it('groups the tabs in reading order: answer, then working, then reference', () => {
    const order = ['answer', 'working', 'reference'];
    const seen = VIEWS.map((view) => order.indexOf(view.group));
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
  });
});

describe('viewForSection', () => {
  it.each(SECTION_IDS)('resolves "%s" to a real view', (id) => {
    const view = viewForSection(id);
    expect(VIEWS.some((candidate) => candidate.id === view)).toBe(true);
  });

  it('keeps the three summary sections together', () => {
    expect(viewForSection('verdict')).toBe('summary');
    expect(viewForSection('summary')).toBe('summary');
    expect(viewForSection('explore')).toBe('summary');
  });

  it('still understands the pre-split "overview" anchor', () => {
    expect(viewForSection('overview')).toBe('summary');
  });

  it.each(['claims', 'evidence', 'questions', 'risks', 'appendix', 'analysis', 'score'])(
    'sends "%s" to its own view',
    (id) => {
      expect(viewForSection(id)).toBe(id);
    },
  );

  it('falls back to the summary rather than nowhere', () => {
    expect(viewForSection('a-section-that-does-not-exist')).toBe('summary');
  });
});

describe('viewFromHash', () => {
  it.each([
    ['#claims', 'claims'],
    ['claims', 'claims'],
    ['#overview', 'summary'],
    ['#verdict', 'summary'],
  ])('reads %s as the %s view', (hash, expected) => {
    expect(viewFromHash(hash)).toBe(expected);
  });

  it.each(['', '#', '#not-a-view'])('ignores %o rather than guessing', (hash) => {
    expect(viewFromHash(hash)).toBeNull();
  });
});

describe('viewDef', () => {
  it('returns the definition for a known view', () => {
    expect(viewDef('claims').label).toBe('Claims');
  });
});
