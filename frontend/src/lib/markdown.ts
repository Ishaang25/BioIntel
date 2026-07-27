/**
 * Minimal, safe Markdown renderer for report sections.
 *
 * Report bodies come from a language model, so they are untrusted input.
 * Rather than pulling in a full Markdown library and a sanitiser, we escape
 * *everything* first and then re-introduce only the small set of constructs
 * the report prompt is allowed to use: headings, paragraphs, lists, tables,
 * bold/italic, inline code and autolinks. Raw HTML in the source can never
 * survive, because `<` is escaped before any rule runs.
 */

const ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char);
}

/** Inline formatting, applied to already-escaped text. */
function inline(text: string): string {
  return (
    text
      // `code`
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // **bold**
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      // _italic_ (avoid mangling snake_case identifiers)
      .replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;:)])/g, '$1<em>$2</em>')
      // [text](url) — http(s) only
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>',
      )
      // bare URLs
      .replace(
        /(^|\s)(https?:\/\/[^\s<]+)/g,
        '$1<a href="$2" target="_blank" rel="noreferrer noopener">$2</a>',
      )
  );
}

function renderTable(rows: string[]): string {
  const cells = (row: string) =>
    row
      .replace(/^\s*\|/, '')
      .replace(/\|\s*$/, '')
      .split('|')
      .map((cell) => cell.trim());

  const [headerRow, , ...bodyRows] = rows;
  if (!headerRow) return '';

  const head = cells(headerRow)
    .map((cell) => `<th>${inline(cell)}</th>`)
    .join('');
  const body = bodyRows
    .map((row) => `<tr>${cells(row).map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`)
    .join('');

  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

const TABLE_DIVIDER = /^\s*\|?[\s:-]*-[-\s:|]*\|?\s*$/;

export function markdownToHtml(markdown: string): string {
  if (!markdown?.trim()) return '';

  const lines = escapeHtml(markdown).split('\n');
  const out: string[] = [];
  let paragraph: string[] = [];
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${inline(paragraph.join(' '))}</p>`);
      paragraph = [];
    }
  };

  const flushList = () => {
    if (list) {
      const items = list.items.map((item) => `<li>${inline(item)}</li>`).join('');
      out.push(`<${list.type}>${items}</${list.type}>`);
      list = null;
    }
  };

  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? '';
    const trimmed = line.trim();

    if (!trimmed) {
      flushAll();
      continue;
    }

    // Table: a header row followed by a divider row.
    if (trimmed.includes('|') && TABLE_DIVIDER.test(lines[index + 1] ?? '')) {
      flushAll();
      const rows: string[] = [];
      while (index < lines.length && (lines[index] ?? '').includes('|')) {
        rows.push(lines[index] ?? '');
        index += 1;
      }
      index -= 1;
      out.push(renderTable(rows));
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushAll();
      const level = Math.min(6, (heading[1] ?? '#').length + 1); // demote: page owns h1/h2
      out.push(`<h${level}>${inline(heading[2] ?? '')}</h${level}>`);
      continue;
    }

    if (/^(---|\*\*\*|___)$/.test(trimmed)) {
      flushAll();
      out.push('<hr />');
      continue;
    }

    if (trimmed.startsWith('&gt; ')) {
      flushAll();
      out.push(`<blockquote>${inline(trimmed.slice(5))}</blockquote>`);
      continue;
    }

    const bullet = /^[-*+]\s+(.*)$/.exec(trimmed);
    if (bullet) {
      flushParagraph();
      if (list?.type !== 'ul') {
        flushList();
        list = { type: 'ul', items: [] };
      }
      list.items.push(bullet[1] ?? '');
      continue;
    }

    const ordered = /^\d+[.)]\s+(.*)$/.exec(trimmed);
    if (ordered) {
      flushParagraph();
      if (list?.type !== 'ol') {
        flushList();
        list = { type: 'ol', items: [] };
      }
      list.items.push(ordered[1] ?? '');
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  flushAll();
  return out.join('\n');
}
