import { describe, expect, it } from 'vitest';

import { markdownToBlocks, pdfText } from './pdf';

describe('pdfText — WinAnsi folding', () => {
  it('folds the non-breaking hyphen the pipeline emits', () => {
    // U+2011 is what breaks jsPDF's built-in Helvetica: it silently switches
    // the whole string to a two-byte encoding and mismeasures the line.
    expect(pdfText('COVID‑19')).toBe('COVID-19');
    expect(pdfText('near‑term')).toBe('near-term');
  });

  it('keeps the curly punctuation WinAnsi can represent', () => {
    expect(pdfText('Moderna’s “claim” — verified…')).toBe(
      'Moderna’s “claim” — verified…',
    );
  });

  it('keeps Latin-1 accents and symbols', () => {
    expect(pdfText('Genentech · ±5 µg × 3')).toBe('Genentech · ±5 µg × 3');
  });

  it('drops zero-width and control characters', () => {
    expect(pdfText('a​b­c﻿d')).toBe('abcd');
  });

  it('preserves newlines so table cells keep their line breaks', () => {
    expect(pdfText('Title\nDetail')).toBe('Title\nDetail');
  });

  it('maps comparison operators to ASCII rather than dropping them', () => {
    expect(pdfText('n ≥ 12, p ≤ 0.05')).toBe('n >= 12, p <= 0.05');
  });

  it('emits nothing for absent values', () => {
    expect(pdfText(null)).toBe('');
    expect(pdfText(undefined)).toBe('');
    expect(pdfText(42)).toBe('42');
  });

  it('leaves no character outside the representable set', () => {
    const folded = pdfText('α β γ 中文 🙂 COVID‑19');
    for (const char of folded) {
      const code = char.codePointAt(0) ?? 0;
      expect(code === 0x0a || code <= 0xff).toBe(true);
    }
  });
});

describe('markdownToBlocks', () => {
  it('promotes a fully bold line to a heading', () => {
    expect(markdownToBlocks('**What is established**')).toEqual([
      { kind: 'h', text: 'What is established' },
    ]);
  });

  it('strips emphasis markers from body text', () => {
    const [block] = markdownToBlocks('The **lead** asset is `mRNA-1647`.');
    expect(block?.text).toBe('The lead asset is mRNA-1647.');
  });

  it('keeps link text and discards the URL', () => {
    const [block] = markdownToBlocks('See [the trial](https://example.com/x).');
    expect(block?.text).toBe('See the trial.');
  });

  it('collects bullets and numbered items as list blocks', () => {
    const blocks = markdownToBlocks('- first\n- second\n1. third');
    expect(blocks.map((b) => b.kind)).toEqual(['li', 'li', 'li']);
    expect(blocks.map((b) => b.text)).toEqual(['first', 'second', 'third']);
  });

  it('drops markdown tables, which are printed as real tables elsewhere', () => {
    const blocks = markdownToBlocks('| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter.');
    expect(blocks).toEqual([{ kind: 'p', text: 'After.' }]);
  });

  it('joins wrapped lines into one paragraph', () => {
    const blocks = markdownToBlocks('one\ntwo\n\nthree');
    expect(blocks).toEqual([
      { kind: 'p', text: 'one two' },
      { kind: 'p', text: 'three' },
    ]);
  });
});
