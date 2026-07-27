import { describe, expect, it } from 'vitest';

import { markdownToHtml } from './markdown';

describe('markdownToHtml — safety', () => {
  it('escapes raw HTML so model output cannot inject markup', () => {
    const html = markdownToHtml('<script>alert(1)</script>');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('escapes img tags so event handlers never become attributes', () => {
    const html = markdownToHtml('<img src=x onerror="alert(1)">');
    // The payload survives as inert escaped text, never as markup.
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
    expect(html).toContain('&quot;');
  });

  it('does not linkify javascript: URLs', () => {
    const html = markdownToHtml('[click](javascript:alert(1))');
    expect(html).not.toContain('<a ');
    expect(html).toBe('<p>[click](javascript:alert(1))</p>');
  });

  it('escapes quotes so attributes cannot be broken out of', () => {
    const html = markdownToHtml('text with " and \' characters');
    expect(html).toContain('&quot;');
    expect(html).toContain('&#39;');
  });

  it('only linkifies http(s) URLs', () => {
    const html = markdownToHtml('[x](ftp://example.com/file)');
    expect(html).not.toContain('<a href="ftp:');
  });
});

describe('markdownToHtml — formatting', () => {
  it('renders paragraphs', () => {
    expect(markdownToHtml('First line.\n\nSecond line.')).toBe(
      '<p>First line.</p>\n<p>Second line.</p>',
    );
  });

  it('joins wrapped lines into one paragraph', () => {
    expect(markdownToHtml('One\ntwo')).toBe('<p>One two</p>');
  });

  it('demotes headings so the page keeps h1/h2', () => {
    expect(markdownToHtml('# Title')).toBe('<h2>Title</h2>');
    expect(markdownToHtml('## Section')).toBe('<h3>Section</h3>');
  });

  it('renders bullet lists', () => {
    expect(markdownToHtml('- one\n- two')).toBe('<ul><li>one</li><li>two</li></ul>');
  });

  it('renders ordered lists', () => {
    expect(markdownToHtml('1. one\n2. two')).toBe('<ol><li>one</li><li>two</li></ol>');
  });

  it('renders bold and inline code', () => {
    expect(markdownToHtml('**bold** and `code`')).toBe(
      '<p><strong>bold</strong> and <code>code</code></p>',
    );
  });

  it('leaves snake_case identifiers alone', () => {
    expect(markdownToHtml('field_name_here')).toBe('<p>field_name_here</p>');
  });

  it('renders tables', () => {
    const html = markdownToHtml('| A | B |\n|---|---|\n| 1 | 2 |');
    expect(html).toContain('<table>');
    expect(html).toContain('<th>A</th>');
    expect(html).toContain('<td>1</td>');
  });

  it('renders blockquotes', () => {
    expect(markdownToHtml('> quoted')).toBe('<blockquote>quoted</blockquote>');
  });

  it('renders horizontal rules', () => {
    expect(markdownToHtml('---')).toBe('<hr />');
  });

  it('preserves citation markers used by the report', () => {
    const html = markdownToHtml('The claim holds [C3], but see [E7].');
    expect(html).toContain('[C3]');
    expect(html).toContain('[E7]');
  });

  it('opens external links safely', () => {
    const html = markdownToHtml('[PubMed](https://pubmed.ncbi.nlm.nih.gov/123/)');
    expect(html).toContain('rel="noreferrer noopener"');
    expect(html).toContain('target="_blank"');
  });

  it('returns an empty string for empty input', () => {
    expect(markdownToHtml('')).toBe('');
    expect(markdownToHtml('   ')).toBe('');
  });
});
