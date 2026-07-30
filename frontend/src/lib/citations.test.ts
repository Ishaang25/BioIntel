import { describe, expect, it } from 'vitest';

import { linkCitations, markdownToHtml } from './markdown';

const refs = (...values: string[]) => new Set(values);

describe('linkCitations', () => {
  it('turns a declared reference into a control carrying its ref', () => {
    const html = linkCitations(markdownToHtml('Approved in 2024 [C1].'), refs('C1'));
    expect(html).toContain('data-cite="C1"');
    expect(html).toContain('class="cite"');
  });

  it('marks a reference the report never declared as dangling, not clickable', () => {
    const html = linkCitations(markdownToHtml('Claimed [C99].'), refs('C1'));
    expect(html).not.toContain('data-cite="C99"');
    expect(html).toContain('cite-dangling');
    expect(html).toContain('>C99<');
  });

  it('handles runs of adjacent references', () => {
    const html = linkCitations(markdownToHtml('Filed [C1][C2][C3].'), refs('C1', 'C2', 'C3'));
    expect(html.match(/data-cite="/g)).toHaveLength(3);
  });

  it('never rewrites inside a tag, so attributes cannot be corrupted', () => {
    const html = linkCitations(
      markdownToHtml('See [the trial](https://example.com/a[C1]b).'),
      refs('C1'),
    );
    // The URL keeps its literal text; only body text is eligible for rewriting.
    expect(html).toContain('href="https://example.com/a[C1]b"');
    expect(html).not.toContain('data-cite="C1"');
  });

  it('leaves ordinary bracketed text alone', () => {
    const html = linkCitations(markdownToHtml('A note [see below] here.'), refs('C1'));
    expect(html).not.toContain('cite');
  });

  it('escaping still runs first — markup in a citation body cannot survive', () => {
    const html = linkCitations(markdownToHtml('<b>bold</b> [C1]'), refs('C1'));
    expect(html).not.toContain('<b>');
    expect(html).toContain('&lt;b&gt;');
  });
});
