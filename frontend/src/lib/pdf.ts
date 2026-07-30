/**
 * PDF export.
 *
 * Produces a paginated consulting deliverable — cover page, running header and
 * footer, sectioned body, real tables — rather than a print of the web page or
 * a conversion of the memo markdown. jsPDF and its table plugin are loaded on
 * demand so neither reaches the initial bundle.
 */

import { formatDateTime, formatDuration, humanise, truncate } from './format';
import {
  BAND_LABEL,
  EVIDENCE_STATE,
  recommendationLabel,
  sourceLabel,
  type ReportModel,
} from './report-model';

type Doc = import('jspdf').jsPDF;

/* ------------------------------------------------------------------- theme --- */

const INK = [24, 28, 35] as const;
const MUTED = [110, 119, 130] as const;
const HAIRLINE = [214, 219, 225] as const;
const ACCENT = [24, 28, 35] as const;

const BAND_RGB: Record<string, readonly [number, number, number]> = {
  strong: [13, 116, 76],
  moderate: [29, 90, 158],
  limited: [154, 102, 0],
  weak: [183, 74, 21],
  unsupported: [176, 42, 34],
};

const PAGE = { width: 595.28, height: 841.89 }; // A4 portrait, points
const MARGIN = { top: 74, bottom: 62, left: 56, right: 56 };
const CONTENT_WIDTH = PAGE.width - MARGIN.left - MARGIN.right;

/* ---------------------------------------------------------------- encoding --- */

/**
 * jsPDF's built-in Helvetica can only encode WinAnsi. Hand it anything else —
 * the pipeline emits U+2011 non-breaking hyphens throughout ("COVID‑19",
 * "near‑term") — and it silently switches that string to a two-byte encoding,
 * which renders as garbage *and* measures at the wrong width, so the line
 * overruns the margin.
 *
 * Embedding a Unicode face would cost a few hundred kilobytes for one glyph, so
 * instead every string is folded to characters the standard encoding can carry.
 */
const TRANSLATIONS: Record<string, string> = {
  '‐': '-', // hyphen
  '‑': '-', // non-breaking hyphen — the common offender here
  '‒': '-', // figure dash
  '⁃': '-', // hyphen bullet
  '−': '-', // minus sign
  ' ': ' ', // no-break space
  ' ': ' ',
  ' ': ' ',
  ' ': ' ',
  ' ': ' ',
  '­': '', // soft hyphen
  '​': '',
  '‌': '',
  '‍': '',
  '﻿': '',
  '′': "'",
  '″': '"',
  '≤': '<=',
  '≥': '>=',
  '≠': '!=',
  '≈': '~',
  '→': '->',
  '←': '<-',
};

/** The CP1252-only characters, on top of printable ASCII and Latin-1. */
const WINANSI_EXTRA = new Set(
  '€‚ƒ„…†‡ˆ‰Š‹ŒŽ' +
    '‘’“”•–—˜™š›œžŸ',
);

function representable(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  // Newlines survive: table cells use them to separate a finding's title from
  // its detail, and stripping them runs the two together.
  if (code === 0x0a) return true;
  if (code >= 0x20 && code <= 0x7e) return true;
  if (code >= 0xa0 && code <= 0xff) return true;
  return WINANSI_EXTRA.has(char);
}

export function pdfText(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '';
  let out = '';
  for (const char of String(value).normalize('NFC')) {
    const mapped = TRANSLATIONS[char] ?? char;
    for (const piece of mapped) if (representable(piece)) out += piece;
  }
  return out;
}

/* ------------------------------------------------------- markdown → blocks --- */

interface Block {
  kind: 'h' | 'p' | 'li' | 'quote';
  text: string;
}

/**
 * Flattens report markdown into printable blocks.
 *
 * Tables and inline emphasis are dropped rather than approximated: a PDF that
 * shows `**` is worse than one that shows clean prose, and the structured data
 * those tables carried is printed as real tables elsewhere in the document.
 */
export function markdownToBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length > 0) {
      blocks.push({ kind: 'p', text: paragraph.join(' ') });
      paragraph = [];
    }
  };

  const inline = (value: string) =>
    value
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/(^|\s)_([^_]+)_/g, '$1$2')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '$1')
      .trim();

  for (const raw of markdown.split('\n')) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    if (/^\|/.test(line) || /^[-:|\s]+$/.test(line)) continue;
    if (/^(---|\*\*\*|___)$/.test(line)) {
      flush();
      continue;
    }

    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: 'h', text: inline(heading[1] ?? '') });
      continue;
    }
    if (line.startsWith('> ')) {
      flush();
      blocks.push({ kind: 'quote', text: inline(line.slice(2)) });
      continue;
    }
    const bullet = /^[-*+]\s+(.*)$/.exec(line) ?? /^\d+[.)]\s+(.*)$/.exec(line);
    if (bullet) {
      flush();
      blocks.push({ kind: 'li', text: inline(bullet[1] ?? '') });
      continue;
    }
    // A line that is entirely bold is a heading in everything but syntax —
    // the report prompt uses `**What is established**` as a sub-heading.
    const wholeLineBold = /^\*\*([^*]+)\*\*:?$/.exec(line);
    if (wholeLineBold) {
      flush();
      blocks.push({ kind: 'h', text: inline(wholeLineBold[1] ?? '') });
      continue;
    }
    paragraph.push(inline(line));
  }

  flush();
  return blocks.filter((block) => block.text.length > 0);
}

/* ------------------------------------------------------------------ writer --- */

/** Sequential page layout with automatic breaks. */
class Writer {
  y = MARGIN.top;

  constructor(readonly doc: Doc) {}

  private ensure(height: number) {
    if (this.y + height <= PAGE.height - MARGIN.bottom) return;
    this.doc.addPage();
    this.y = MARGIN.top;
  }

  space(amount: number) {
    this.y += amount;
  }

  /** Starts a new page unless the cursor is already at the top of one. */
  pageBreak() {
    if (this.y > MARGIN.top + 1) {
      this.doc.addPage();
      this.y = MARGIN.top;
    }
  }

  rule() {
    this.ensure(10);
    this.doc.setDrawColor(...HAIRLINE);
    this.doc.setLineWidth(0.5);
    this.doc.line(MARGIN.left, this.y, PAGE.width - MARGIN.right, this.y);
    this.y += 12;
  }

  sectionTitle(text: string, number?: number) {
    this.ensure(52);
    this.doc.setFont('helvetica', 'bold');
    this.doc.setFontSize(13);
    this.doc.setTextColor(...ACCENT);
    const label = number === undefined ? text : `${number}.  ${text}`;
    this.doc.text(pdfText(label), MARGIN.left, this.y);
    this.y += 8;
    this.doc.setDrawColor(...ACCENT);
    this.doc.setLineWidth(1);
    this.doc.line(MARGIN.left, this.y, MARGIN.left + 26, this.y);
    this.y += 16;
  }

  subTitle(text: string) {
    if (this.y > MARGIN.top + 1) this.y += 6;
    this.ensure(30);
    this.doc.setFont('helvetica', 'bold');
    this.doc.setFontSize(9);
    this.doc.setTextColor(...MUTED);
    this.doc.text(pdfText(text).toUpperCase(), MARGIN.left, this.y);
    this.y += 13;
  }

  paragraph(
    text: string,
    options: { size?: number; colour?: readonly [number, number, number]; indent?: number; bold?: boolean } = {},
  ) {
    const size = options.size ?? 9.5;
    const indent = options.indent ?? 0;
    const leading = size * 1.5;
    this.doc.setFont('helvetica', options.bold ? 'bold' : 'normal');
    this.doc.setFontSize(size);
    this.doc.setTextColor(...(options.colour ?? INK));

    const lines = this.doc.splitTextToSize(pdfText(text), CONTENT_WIDTH - indent) as string[];
    for (const line of lines) {
      this.ensure(leading);
      this.doc.text(line, MARGIN.left + indent, this.y);
      this.y += leading;
    }
    this.y += 4;
  }

  bullet(text: string, marker = '•') {
    const size = 9.5;
    const leading = size * 1.5;
    this.doc.setFont('helvetica', 'normal');
    this.doc.setFontSize(size);
    this.doc.setTextColor(...INK);

    const lines = this.doc.splitTextToSize(pdfText(text), CONTENT_WIDTH - 16) as string[];
    lines.forEach((line, index) => {
      this.ensure(leading);
      if (index === 0) {
        this.doc.setTextColor(...MUTED);
        this.doc.text(marker, MARGIN.left + 3, this.y);
        this.doc.setTextColor(...INK);
      }
      this.doc.text(line, MARGIN.left + 16, this.y);
      this.y += leading;
    });
    this.y += 2;
  }

  quote(text: string) {
    const size = 9;
    const leading = size * 1.5;
    this.doc.setFont('helvetica', 'italic');
    this.doc.setFontSize(size);
    const lines = this.doc.splitTextToSize(pdfText(text), CONTENT_WIDTH - 20) as string[];
    const height = lines.length * leading;
    this.ensure(height + 8);

    this.doc.setDrawColor(...HAIRLINE);
    this.doc.setLineWidth(2);
    this.doc.line(MARGIN.left, this.y - 8, MARGIN.left, this.y + height - 8);

    this.doc.setTextColor(...MUTED);
    for (const line of lines) {
      this.doc.text(line, MARGIN.left + 12, this.y);
      this.y += leading;
    }
    this.y += 6;
  }

  blocks(list: Block[]) {
    for (const block of list) {
      if (block.kind === 'h') this.subTitle(block.text);
      else if (block.kind === 'li') this.bullet(block.text);
      else if (block.kind === 'quote') this.quote(block.text);
      else this.paragraph(block.text);
    }
  }

  /** A boxed pull-out for a section's bottom line. */
  calloutBox(label: string, text: string) {
    const size = 9.5;
    const leading = size * 1.45;
    this.doc.setFont('helvetica', 'normal');
    this.doc.setFontSize(size);
    const lines = this.doc.splitTextToSize(pdfText(text), CONTENT_WIDTH - 24) as string[];
    const height = lines.length * leading + 26;
    this.ensure(height + 6);

    this.doc.setFillColor(246, 247, 249);
    this.doc.setDrawColor(...HAIRLINE);
    this.doc.roundedRect(MARGIN.left, this.y - 4, CONTENT_WIDTH, height, 3, 3, 'FD');
    this.doc.setFillColor(...ACCENT);
    this.doc.rect(MARGIN.left, this.y - 4, 2, height, 'F');

    this.doc.setFont('helvetica', 'bold');
    this.doc.setFontSize(7.5);
    this.doc.setTextColor(...MUTED);
    this.doc.text(pdfText(label).toUpperCase(), MARGIN.left + 12, this.y + 10);

    this.doc.setFont('helvetica', 'normal');
    this.doc.setFontSize(size);
    this.doc.setTextColor(...INK);
    let cursor = this.y + 24;
    for (const line of lines) {
      this.doc.text(line, MARGIN.left + 12, cursor);
      cursor += leading;
    }
    this.y += height + 8;
  }
}

/* ------------------------------------------------------------ cover & chrome --- */

function drawCover(doc: Doc, model: ReportModel) {
  const bandColour = BAND_RGB[model.band] ?? ACCENT;

  // Wordmark
  doc.setFillColor(...ACCENT);
  doc.roundedRect(MARGIN.left, 92, 22, 22, 3, 3, 'F');
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  doc.setTextColor(255, 255, 255);
  doc.text('Bi', MARGIN.left + 5.5, 107);

  doc.setTextColor(...ACCENT);
  doc.setFontSize(12);
  doc.text('BioIntel', MARGIN.left + 32, 107);
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8.5);
  doc.setTextColor(...MUTED);
  doc.text('Scientific due diligence', MARGIN.left + 32, 118);

  // Title block
  doc.setDrawColor(...HAIRLINE);
  doc.setLineWidth(0.5);
  doc.line(MARGIN.left, 168, PAGE.width - MARGIN.right, 168);

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(8);
  doc.setTextColor(...MUTED);
  doc.text('SCIENTIFIC DILIGENCE MEMORANDUM', MARGIN.left, 196);

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(30);
  doc.setTextColor(...ACCENT);
  const company = doc.splitTextToSize(pdfText(model.company), CONTENT_WIDTH) as string[];
  let y = 232;
  for (const line of company.slice(0, 3)) {
    doc.text(line, MARGIN.left, y);
    y += 34;
  }

  if (model.subtitle) {
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(11);
    doc.setTextColor(...MUTED);
    const subtitle = doc.splitTextToSize(pdfText(model.subtitle), CONTENT_WIDTH) as string[];
    for (const line of subtitle.slice(0, 3)) {
      doc.text(line, MARGIN.left, y);
      y += 16;
    }
  }

  // Headline figures
  const boxTop = 430;
  const boxWidth = CONTENT_WIDTH / 3;
  const figures: Array<[string, string, readonly [number, number, number]]> = [
    ['Credibility score', `${model.score.toFixed(0)} / 100`, bandColour],
    ['Band', BAND_LABEL[model.band], INK],
    ['Confidence', `${(model.confidence * 100).toFixed(0)}%`, INK],
  ];

  doc.setDrawColor(...HAIRLINE);
  doc.line(MARGIN.left, boxTop - 22, PAGE.width - MARGIN.right, boxTop - 22);

  figures.forEach(([label, value, colour], index) => {
    const x = MARGIN.left + index * boxWidth;
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7.5);
    doc.setTextColor(...MUTED);
    doc.text(pdfText(label).toUpperCase(), x, boxTop);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(20);
    doc.setTextColor(...colour);
    doc.text(pdfText(value), x, boxTop + 24);
  });

  doc.setDrawColor(...HAIRLINE);
  doc.line(MARGIN.left, boxTop + 44, PAGE.width - MARGIN.right, boxTop + 44);

  // Recommendation
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(7.5);
  doc.setTextColor(...MUTED);
  doc.text('RECOMMENDATION', MARGIN.left, boxTop + 74);
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(15);
  doc.setTextColor(...ACCENT);
  doc.text(pdfText(recommendationLabel(model.recommendation)), MARGIN.left, boxTop + 96);

  // Metadata footer block
  const metaTop = PAGE.height - 168;
  doc.setDrawColor(...HAIRLINE);
  doc.line(MARGIN.left, metaTop - 20, PAGE.width - MARGIN.right, metaTop - 20);

  const meta: Array<[string, string]> = [
    ['Source document', truncate(model.filename, 44)],
    ['Pages analysed', String(model.pageCount)],
    ['Generated', formatDateTime(model.createdAt)],
    ['Analysis duration', formatDuration(model.durationMs)],
    ['Claims extracted', String(model.claims.length)],
    ['Pipeline', `v${model.pipelineVersion}`],
  ];

  meta.forEach(([label, value], index) => {
    const column = index % 2;
    const row = Math.floor(index / 2);
    const x = MARGIN.left + column * (CONTENT_WIDTH / 2);
    const yy = metaTop + row * 30;
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7);
    doc.setTextColor(...MUTED);
    doc.text(pdfText(label).toUpperCase(), x, yy);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    doc.setTextColor(...INK);
    doc.text(pdfText(value), x, yy + 13);
  });

  doc.setFont('helvetica', 'italic');
  doc.setFontSize(7.5);
  doc.setTextColor(...MUTED);
  const disclaimer = doc.splitTextToSize(
    'Confidential. First-draft scientific analysis produced by automated claim extraction and literature retrieval. Requires review by a qualified scientific advisor before it informs an investment decision.',
    CONTENT_WIDTH,
  ) as string[];
  let disclaimerY = PAGE.height - 56;
  for (const line of disclaimer) {
    doc.text(line, MARGIN.left, disclaimerY);
    disclaimerY += 10;
  }
}

/** Running header and footer, stamped once the page count is known. */
function drawChrome(doc: Doc, model: ReportModel) {
  const total = doc.getNumberOfPages();
  const generated = formatDateTime(model.createdAt);

  for (let page = 2; page <= total; page += 1) {
    doc.setPage(page);

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7.5);
    doc.setTextColor(...MUTED);
    doc.text(pdfText(truncate(model.company, 52)).toUpperCase(), MARGIN.left, 44);
    doc.setFont('helvetica', 'normal');
    doc.text('Scientific diligence memorandum', PAGE.width - MARGIN.right, 44, { align: 'right' });

    doc.setDrawColor(...HAIRLINE);
    doc.setLineWidth(0.5);
    doc.line(MARGIN.left, 52, PAGE.width - MARGIN.right, 52);
    doc.line(MARGIN.left, PAGE.height - 44, PAGE.width - MARGIN.right, PAGE.height - 44);

    doc.setFontSize(7.5);
    doc.setTextColor(...MUTED);
    doc.text(pdfText(`BioIntel · ${generated}`), MARGIN.left, PAGE.height - 30);
    doc.text(`${page} of ${total}`, PAGE.width - MARGIN.right, PAGE.height - 30, {
      align: 'right',
    });
  }
}

/* ------------------------------------------------------------------- tables --- */

type AutoTable = typeof import('jspdf-autotable').default;

function table(
  autoTable: AutoTable,
  writer: Writer,
  head: string[],
  body: Array<Array<string | number>>,
  columnStyles?: Record<number, { cellWidth?: number; halign?: 'left' | 'right' | 'center' }>,
) {
  autoTable(writer.doc, {
    startY: writer.y,
    head: [head.map(pdfText)],
    body: body.map((row) => row.map(pdfText)),
    margin: { left: MARGIN.left, right: MARGIN.right, top: MARGIN.top, bottom: MARGIN.bottom },
    tableWidth: CONTENT_WIDTH,
    styles: {
      font: 'helvetica',
      fontSize: 8,
      cellPadding: { top: 5, bottom: 5, left: 6, right: 6 },
      textColor: [...INK] as [number, number, number],
      lineColor: [...HAIRLINE] as [number, number, number],
      lineWidth: { bottom: 0.5, top: 0, left: 0, right: 0 },
      overflow: 'linebreak',
      valign: 'top',
    },
    headStyles: {
      fontStyle: 'bold',
      fontSize: 7,
      textColor: [...MUTED] as [number, number, number],
      fillColor: false,
      lineWidth: { bottom: 0.8, top: 0, left: 0, right: 0 },
      lineColor: [...HAIRLINE] as [number, number, number],
      cellPadding: { top: 4, bottom: 6, left: 6, right: 6 },
    },
    bodyStyles: { fillColor: false },
    alternateRowStyles: { fillColor: [250, 251, 252] },
    columnStyles,
  });

  const finalY = (writer.doc as Doc & { lastAutoTable?: { finalY: number } }).lastAutoTable?.finalY;
  writer.y = (finalY ?? writer.y) + 20;
}

/* ------------------------------------------------------------------ export --- */

export async function exportReportPdf(model: ReportModel): Promise<void> {
  const [{ jsPDF }, autoTableModule] = await Promise.all([
    import('jspdf'),
    import('jspdf-autotable'),
  ]);
  const autoTable = autoTableModule.default;

  const doc = new jsPDF({ unit: 'pt', format: 'a4', compress: true });
  doc.setProperties({
    title: `${model.company} — Scientific diligence memorandum`,
    subject: 'BioIntel scientific due diligence',
    creator: 'BioIntel',
    author: 'BioIntel',
  });

  drawCover(doc, model);
  // The cover owns page 1 outright; the body starts on a page of its own.
  doc.addPage();

  const writer = new Writer(doc);
  let sectionNumber = 0;

  /* 1 — Executive summary */
  writer.sectionTitle('Executive summary', (sectionNumber += 1));
  writer.blocks(markdownToBlocks(model.executiveSummary));

  if (model.keyStrengths.length > 0) {
    writer.space(4);
    writer.subTitle('Key strengths');
    for (const item of model.keyStrengths) writer.bullet(item.text);
  }
  if (model.keyRisks.length > 0) {
    writer.space(4);
    writer.subTitle('Key risks');
    for (const item of model.keyRisks) {
      writer.bullet(`${humanise(item.severity ?? '')}: ${item.text}`.replace(/^: /, ''));
    }
  }

  /* 2 — Recommendation */
  writer.pageBreak();
  writer.sectionTitle('Investment recommendation', (sectionNumber += 1));
  writer.calloutBox(recommendationLabel(model.recommendation), model.recommendationRationale ||
    'No scorecard rationale was recorded for this run.');
  writer.blocks(markdownToBlocks(model.recommendationMarkdown));
  if (model.confidenceNote) {
    writer.space(4);
    writer.subTitle('Confidence');
    writer.paragraph(model.confidenceNote, { colour: MUTED });
  }

  /* 3 — Score breakdown */
  writer.pageBreak();
  writer.sectionTitle('Score breakdown', (sectionNumber += 1));
  writer.paragraph(
    `Overall scientific credibility ${model.score.toFixed(0)} of 100 (${BAND_LABEL[model.band]}), at ${(model.confidence * 100).toFixed(0)}% confidence. Dimension weighting is set by company archetype.`,
  );
  const assessed = model.dimensions.filter((dimension) => dimension.assessed);
  if (assessed.length > 0) {
    table(
      autoTable,
      writer,
      ['Dimension', 'Score', 'Band', 'Confidence', 'Rationale'],
      assessed.map((dimension) => [
        dimension.label,
        dimension.score === null ? '—' : dimension.score.toFixed(0),
        BAND_LABEL[dimension.band ?? 'unsupported'],
        dimension.confidence_band,
        truncate(dimension.rationale, 260),
      ]),
      {
        0: { cellWidth: 96 },
        1: { cellWidth: 34, halign: 'right' },
        2: { cellWidth: 54 },
        3: { cellWidth: 50 },
      },
    );
  }
  const unassessed = model.dimensions.filter((dimension) => !dimension.assessed);
  if (unassessed.length > 0) {
    writer.subTitle(`Not assessed — ${unassessed.length}`);
    writer.paragraph(
      `No claim in the deck bore on: ${unassessed.map((dimension) => dimension.label).join(', ')}. This is a disclosure gap, not a negative finding.`,
      { colour: MUTED },
    );
  }

  /* 4 — Analysis */
  for (const section of model.sections) {
    writer.pageBreak();
    writer.sectionTitle(section.heading, (sectionNumber += 1));
    writer.blocks(markdownToBlocks(section.body_markdown));
    if (section.so_what) writer.calloutBox('So what', section.so_what);
    if (section.confidence) {
      writer.paragraph(
        `Confidence: ${humanise(section.confidence)}${section.confidence_reason ? ` — ${section.confidence_reason}` : ''}`,
        { size: 8, colour: MUTED },
      );
    }
  }

  /* 5 — Risk summary */
  writer.pageBreak();
  writer.sectionTitle('Risk summary', (sectionNumber += 1));
  if (model.risks.length === 0) {
    writer.paragraph('No scientific risks were identified.', { colour: MUTED });
  } else {
    table(
      autoTable,
      writer,
      ['Severity', 'Finding', 'Category', 'Pages'],
      model.risks.map((card) => [
        humanise(card.severity),
        `${card.risk.title}\n${truncate(card.risk.description, 300)}`,
        humanise(card.risk.category),
        card.risk.source_pages.join(', ') || '—',
      ]),
      {
        0: { cellWidth: 52 },
        2: { cellWidth: 86 },
        3: { cellWidth: 44, halign: 'right' },
      },
    );
  }

  /* 6 — Evidence */
  writer.pageBreak();
  writer.sectionTitle('Evidence', (sectionNumber += 1));
  writer.subTitle('Claims by evidence state');
  table(
    autoTable,
    writer,
    ['Evidence state', 'Claims', 'Meaning'],
    model.evidenceStateSlices.map((slice) => [
      slice.label,
      slice.value,
      truncate(slice.hint ?? '', 200),
    ]),
    { 0: { cellWidth: 116 }, 1: { cellWidth: 44, halign: 'right' } },
  );

  if (model.sourceSlices.length > 0) {
    writer.subTitle('Sources searched');
    table(
      autoTable,
      writer,
      ['Source', 'Records screened'],
      model.sourceSlices.map((slice) => [slice.label, slice.value]),
      { 1: { cellWidth: 110, halign: 'right' } },
    );
  }

  if (model.evidence.length > 0) {
    writer.subTitle('Retrieved records');
    table(
      autoTable,
      writer,
      ['Record', 'Source', 'Identifier', 'Year'],
      model.evidence.map((record) => [
        truncate(record.title, 180),
        sourceLabel(record.source),
        record.pmid
          ? `PMID ${record.pmid}`
          : (record.nct_id ?? record.doi ?? record.external_id ?? '—'),
        record.publication_year ?? '—',
      ]),
      {
        1: { cellWidth: 84 },
        2: { cellWidth: 96 },
        3: { cellWidth: 38, halign: 'right' },
      },
    );
  }

  /* 7 — Claims */
  writer.pageBreak();
  writer.sectionTitle('Extracted claims', (sectionNumber += 1));
  writer.paragraph(
    `${model.claims.length} claims were extracted from ${model.pageCount} pages. Each carries the verbatim quote and page number it came from.`,
    { colour: MUTED },
  );
  table(
    autoTable,
    writer,
    ['Pg', 'Claim', 'Type', 'Evidence state', 'Score'],
    model.claims.map((row) => [
      row.page,
      row.claim.is_thesis_critical ? `${row.statement}  [thesis-critical]` : row.statement,
      row.typeLabel,
      EVIDENCE_STATE[row.state].label,
      row.score === null ? '—' : row.score.toFixed(0),
    ]),
    {
      0: { cellWidth: 24, halign: 'right' },
      2: { cellWidth: 78 },
      3: { cellWidth: 92 },
      4: { cellWidth: 34, halign: 'right' },
    },
  );

  /* 8 — Management questions */
  writer.pageBreak();
  writer.sectionTitle('Management questions', (sectionNumber += 1));
  if (model.questions.length === 0) {
    writer.paragraph('No diligence questions were generated.', { colour: MUTED });
  } else {
    table(
      autoTable,
      writer,
      ['#', 'Priority', 'Question', 'Why it matters'],
      model.questions.map((question, index) => [
        index + 1,
        humanise(question.priority),
        question.question,
        truncate(question.rationale, 300),
      ]),
      {
        0: { cellWidth: 20, halign: 'right' },
        1: { cellWidth: 52 },
      },
    );
  }

  /* 9 — Appendix */
  writer.pageBreak();
  writer.sectionTitle('Appendix', (sectionNumber += 1));

  if (model.limitations.length > 0) {
    writer.subTitle('Limitations of this analysis');
    for (const limitation of model.limitations) writer.bullet(limitation);
    writer.space(8);
  }

  if (model.scientificAssessment.length > 0) {
    writer.subTitle('Scientific assessment');
    for (const entry of model.scientificAssessment) {
      writer.paragraph(entry.label, { size: 8.5, bold: true });
      writer.paragraph(entry.value, { colour: MUTED });
    }
    writer.space(4);
  }

  if (model.citations.length > 0) {
    writer.subTitle('References');
    table(
      autoTable,
      writer,
      ['Ref', 'Reference', 'Locator'],
      model.citations.map((citation) => [
        citation.ref,
        truncate(citation.title, 220),
        citation.pageNumber !== null
          ? `Deck p.${citation.pageNumber}`
          : (citation.pmid ?? citation.nctId ?? citation.doi ?? '—'),
      ]),
      { 0: { cellWidth: 34 }, 2: { cellWidth: 84 } },
    );
  }

  writer.subTitle('Provenance');
  table(
    autoTable,
    writer,
    ['Field', 'Value'],
    [
      ['Run identifier', model.runId],
      ['Source document', model.filename],
      ['Pipeline version', `v${model.pipelineVersion}`],
      ['Report generated', formatDateTime(model.createdAt)],
      ['Analysis duration', formatDuration(model.durationMs)],
      ['Claims extracted', String(model.claims.length)],
      ['Evidence records retained', String(model.evidence.length)],
    ],
    { 0: { cellWidth: 150 } },
  );

  drawChrome(doc, model);

  const stamp = new Date().toISOString().slice(0, 10);
  const safeCompany = model.company.replace(/[^\w\s-]/g, '').replace(/\s+/g, '-').slice(0, 48);
  doc.save(`BioIntel-${safeCompany || 'diligence'}-${stamp}.pdf`);
}
