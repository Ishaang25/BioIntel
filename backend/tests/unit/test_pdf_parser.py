"""PDF ingestion tests."""

from __future__ import annotations

import fitz
import pytest

from app.core.enums import PageKind
from app.core.errors import DocumentTooLarge, PdfParseError, UnsupportedDocument
from app.ingestion.pdf_parser import (
    build_pdf,
    compute_hash,
    parse_pdf,
    probe_pdf,
    render_page_png,
    validate_pdf_bytes,
)
from tests.fixtures.sample_deck import NEUROGEN_PAGES, neurogen_pdf


class TestValidation:
    def test_rejects_empty_file(self):
        with pytest.raises(UnsupportedDocument):
            validate_pdf_bytes(b"")

    def test_rejects_non_pdf(self):
        with pytest.raises(UnsupportedDocument):
            validate_pdf_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 500)

    def test_rejects_html_disguised_as_pdf(self):
        with pytest.raises(UnsupportedDocument):
            validate_pdf_bytes(b"<html><body>not a pdf</body></html>", filename="deck.pdf")

    def test_rejects_oversized_file(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "max_upload_mb", 1)
        with pytest.raises(DocumentTooLarge):
            validate_pdf_bytes(b"%PDF-1.7" + b"0" * (2 * 1024 * 1024))

    def test_accepts_valid_pdf(self):
        validate_pdf_bytes(neurogen_pdf())

    def test_rejects_encrypted_pdf(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
        doc.close()
        with pytest.raises(UnsupportedDocument, match="password"):
            parse_pdf(data)

    def test_rejects_corrupt_pdf(self):
        with pytest.raises(PdfParseError):
            parse_pdf(b"%PDF-1.7\nthis is not actually a pdf structure\n%%EOF")

    def test_page_limit_enforced(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "max_pdf_pages", 3)
        with pytest.raises(DocumentTooLarge, match="pages"):
            parse_pdf(build_pdf(["a"] * 5))


class TestParsing:
    def test_extracts_all_pages(self):
        parsed = parse_pdf(neurogen_pdf())
        assert parsed.page_count == len(NEUROGEN_PAGES)
        assert len(parsed.pages) == len(NEUROGEN_PAGES)

    def test_page_text_is_recovered(self):
        parsed = parse_pdf(neurogen_pdf())
        page_three = parsed.pages[2]
        assert "LRRK2" in page_three.text
        assert "3.2 nM" in page_three.text

    def test_pages_are_classified_as_digital(self):
        parsed = parse_pdf(neurogen_pdf())
        assert all(p.kind is PageKind.DIGITAL_TEXT for p in parsed.pages)
        assert parsed.requires_ocr is False

    def test_blocks_carry_positions(self):
        parsed = parse_pdf(neurogen_pdf())
        blocks = parsed.pages[0].blocks
        assert blocks
        assert all(len(b.bbox) == 4 for b in blocks)
        assert all(b.block_index == i for i, b in enumerate(blocks))

    def test_blocks_are_in_reading_order(self):
        parsed = parse_pdf(neurogen_pdf())
        blocks = parsed.pages[1].blocks
        tops = [b.bbox[1] for b in blocks]
        assert tops == sorted(tops)

    def test_empty_page_is_classified_empty(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        parsed = parse_pdf(data)
        assert parsed.pages[0].kind is PageKind.EMPTY

    def test_image_only_page_is_scanned_and_needs_vision(self):
        doc = fitz.open()
        page = doc.new_page(width=400, height=400)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 380, 380))
        pixmap.set_rect(pixmap.irect, (200, 40, 40))
        page.insert_image(fitz.Rect(5, 5, 395, 395), pixmap=pixmap)
        data = doc.tobytes()
        doc.close()

        parsed = parse_pdf(data)
        assert parsed.pages[0].kind is PageKind.SCANNED_IMAGE
        assert parsed.pages[0].needs_vision is True
        assert parsed.requires_ocr is True

    def test_renders_are_written_for_vision_pages(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 280, 280))
        pixmap.set_rect(pixmap.irect, (10, 90, 200))
        page.insert_image(fitz.Rect(5, 5, 295, 295), pixmap=pixmap)
        data = doc.tobytes()
        doc.close()

        parsed = parse_pdf(data, render_dir=tmp_path)
        assert parsed.pages[0].render_path is not None
        written = tmp_path / parsed.pages[0].render_path
        assert written.exists()
        assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_no_render_for_plain_text_pages(self, tmp_path):
        parse_pdf(neurogen_pdf(), render_dir=tmp_path)
        assert not list(tmp_path.glob("*.png"))

    def test_metadata_is_captured(self):
        parsed = parse_pdf(neurogen_pdf())
        assert "NeuroGen" in (parsed.metadata.get("title") or "")

    def test_full_text_joins_pages(self):
        parsed = parse_pdf(neurogen_pdf())
        assert "MPTP" in parsed.full_text
        assert "Series A" in parsed.full_text


class TestTables:
    def test_ruled_table_is_extracted(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=300)
        rows = [
            ["Program", "Indication", "Stage"],
            ["NG-101", "Parkinson", "IND-enabling"],
            ["NG-205", "ALS", "Discovery"],
        ]
        x_positions = [40, 200, 340, 460]
        y_positions = [40, 80, 120, 160]
        for y in y_positions:
            page.draw_line(fitz.Point(40, y), fitz.Point(460, y))
        for x in x_positions:
            page.draw_line(fitz.Point(x, 40), fitz.Point(x, 160))
        for row_index, row in enumerate(rows):
            for col_index, cell in enumerate(row):
                page.insert_text(
                    fitz.Point(x_positions[col_index] + 6, y_positions[row_index] + 25),
                    cell,
                    fontsize=10,
                )
        data = doc.tobytes()
        doc.close()

        parsed = parse_pdf(data)
        tables = parsed.pages[0].tables
        assert tables, "expected the ruled table to be detected"
        markdown = tables[0].to_markdown()
        assert "NG-101" in markdown
        assert markdown.count("|") > 6


class TestHelpers:
    def test_hash_is_stable_and_content_addressed(self):
        data = neurogen_pdf()
        assert compute_hash(data) == compute_hash(data)
        assert compute_hash(data) != compute_hash(build_pdf(["different"]))

    def test_probe_is_cheap_and_accurate(self):
        probe = probe_pdf(neurogen_pdf())
        assert probe["page_count"] == len(NEUROGEN_PAGES)

    def test_render_png_respects_size_cap(self):
        doc = fitz.open(stream=neurogen_pdf(), filetype="pdf")
        png = render_page_png(doc.load_page(0), dpi=600)
        doc.close()
        image = fitz.Pixmap(png)
        assert max(image.width, image.height) <= 1600
