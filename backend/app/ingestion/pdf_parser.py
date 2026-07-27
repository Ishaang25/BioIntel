"""PDF parsing.

Responsibilities
----------------
1. Validate that the bytes really are a PDF and are safely processable.
2. Extract a text layer, positioned text blocks, and machine-readable tables.
3. Classify each page as digital / scanned / mixed / empty so downstream
   stages know when a vision pass is required.
4. Rasterise pages that need visual interpretation.

Everything here is pure and synchronous; persistence lives in the service
layer.  This keeps the parser trivially unit-testable against synthetic PDFs.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from app.core.config import settings
from app.core.enums import PageKind
from app.core.errors import DocumentTooLarge, PdfParseError, UnsupportedDocument
from app.core.logging import get_logger
from app.utils.text import clean_extracted_text, collapse_whitespace

log = get_logger(__name__)

PDF_MAGIC = b"%PDF-"

#: A page with fewer characters than this has no usable text layer.
MIN_CHARS_FOR_TEXT_PAGE = 40
#: A page whose raster images cover more than this share of its area and which
#: has little text is treated as a scan.
SCANNED_IMAGE_AREA_RATIO = 0.55
#: Pages with some text but heavy imagery get both text and vision treatment.
MIXED_MIN_IMAGE_RATIO = 0.18
#: DPI used when rasterising pages for the vision model.
RENDER_DPI = 150
#: Cap the rendered pixel dimension so vision payloads stay bounded.
MAX_RENDER_PIXELS = 1600

#: Glyphs PDF producers use for bullet list markers. Written as code points
#: because several are visually indistinguishable from ASCII in source.
_BULLET = chr(0x2022)  # BULLET
_EN_DASH = chr(0x2013)  # EN DASH
_LIST_MARKERS = (_BULLET, "-", _EN_DASH, "*")


@dataclass(slots=True)
class ParsedBlock:
    block_index: int
    block_type: str
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float = 0.0
    is_bold: bool = False


@dataclass(slots=True)
class ParsedTable:
    index: int
    bbox: tuple[float, float, float, float]
    header: list[str]
    rows: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "bbox": list(self.bbox),
            "header": self.header,
            "rows": self.rows,
            "markdown": self.to_markdown(),
        }

    def to_markdown(self) -> str:
        if not self.header and not self.rows:
            return ""
        header = self.header or [f"col{i + 1}" for i in range(len(self.rows[0]))]
        width = len(header)
        lines = [
            "| " + " | ".join(_md_cell(c) for c in header) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        for row in self.rows:
            padded = (list(row) + [""] * width)[:width]
            lines.append("| " + " | ".join(_md_cell(c) for c in padded) + " |")
        return "\n".join(lines)


def _md_cell(value: Any) -> str:
    return collapse_whitespace(str(value or "")).replace("|", "\\|")


@dataclass(slots=True)
class ParsedPage:
    page_number: int
    kind: PageKind
    width: float
    height: float
    rotation: int
    text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    image_count: int = 0
    vector_drawing_count: int = 0
    image_area_ratio: float = 0.0
    render_path: str | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def needs_vision(self) -> bool:
        """Whether a multimodal pass would add information for this page."""
        if self.kind in (PageKind.SCANNED_IMAGE, PageKind.MIXED):
            return True
        # A slide that is mostly a chart still has axis labels in the text
        # layer but the *shape* of the data only exists visually.
        return self.vector_drawing_count >= 12 or self.image_count > 0


@dataclass(slots=True)
class ParsedDocument:
    page_count: int
    pages: list[ParsedPage]
    metadata: dict[str, Any]
    requires_ocr: bool
    is_encrypted: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)


# ---------------------------------------------------------------- checks ---
def compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_pdf_bytes(data: bytes, *, filename: str | None = None) -> None:
    """Cheap structural validation before we hand bytes to the PDF engine."""
    if not data:
        raise UnsupportedDocument("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise DocumentTooLarge(
            f"File is {len(data) / 1_048_576:.1f} MB; the limit is {settings.max_upload_mb} MB.",
            detail={"size_bytes": len(data), "limit_bytes": settings.max_upload_bytes},
        )
    if not data.lstrip()[:1024].startswith(PDF_MAGIC):
        raise UnsupportedDocument(
            "Only PDF files are supported.",
            detail={"filename": filename} if filename else None,
        )


def open_document(data: bytes) -> fitz.Document:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # pragma: no cover - depends on corrupt input
        raise PdfParseError(f"Could not open the PDF: {exc}", cause=exc) from exc

    if doc.needs_pass:
        doc.close()
        raise UnsupportedDocument("The PDF is password protected.")
    if doc.page_count == 0:
        doc.close()
        raise PdfParseError("The PDF contains no pages.")
    if doc.page_count > settings.max_pdf_pages:
        count = doc.page_count
        doc.close()
        raise DocumentTooLarge(
            f"The PDF has {count} pages; the limit is {settings.max_pdf_pages}.",
            detail={"page_count": count},
        )
    return doc


# --------------------------------------------------------------- parsing ---
def _extract_blocks(page: fitz.Page) -> tuple[list[ParsedBlock], str]:
    """Extract positioned text blocks plus a reading-order text layer."""
    blocks: list[ParsedBlock] = []
    try:
        raw = page.get_text("dict")
    except Exception as exc:  # pragma: no cover
        raise PdfParseError(f"Text extraction failed on page {page.number + 1}: {exc}") from exc

    items: list[tuple[tuple[float, float], ParsedBlock]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # 0 == text
            continue
        lines_text: list[str] = []
        sizes: list[float] = []
        bold_flags: list[bool] = []
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            line_text = "".join(span.get("text", "") for span in spans)
            if line_text.strip():
                lines_text.append(line_text)
            for span in spans:
                if span.get("text", "").strip():
                    sizes.append(float(span.get("size", 0.0)))
                    flags = int(span.get("flags", 0))
                    # bit 4 (value 16) marks a bold font in PyMuPDF span flags
                    bold_flags.append(bool(flags & 16))
        text = clean_extracted_text("\n".join(lines_text))
        if not text:
            continue
        bbox = tuple(float(v) for v in block.get("bbox", (0, 0, 0, 0)))  # type: ignore[assignment]
        avg_size = sum(sizes) / len(sizes) if sizes else 0.0
        is_bold = bool(bold_flags) and sum(bold_flags) / len(bold_flags) > 0.6
        parsed = ParsedBlock(
            block_index=0,
            block_type=_classify_block(text, avg_size, is_bold),
            text=text,
            bbox=bbox,  # type: ignore[arg-type]
            font_size=round(avg_size, 2),
            is_bold=is_bold,
        )
        items.append(((bbox[1], bbox[0]), parsed))

    # Reading order: top-to-bottom, then left-to-right.
    items.sort(key=lambda pair: (round(pair[0][0], 1), round(pair[0][1], 1)))
    for i, (_, block) in enumerate(items):
        block.block_index = i
        blocks.append(block)

    page_text = clean_extracted_text("\n\n".join(b.text for b in blocks))
    return blocks, page_text


def _classify_block(text: str, font_size: float, is_bold: bool) -> str:
    stripped = text.strip()
    words = stripped.split()
    if len(words) <= 12 and (is_bold or font_size >= 16):
        return "heading"
    if stripped.startswith(_LIST_MARKERS) or ("\n" + _BULLET) in stripped:
        return "list"
    if len(words) <= 6 and stripped.lower().startswith(("figure", "fig.", "table", "exhibit")):
        return "caption"
    return "text"


def _extract_tables(page: fitz.Page) -> list[ParsedTable]:
    """Extract tables using PyMuPDF's ruling/whitespace table finder."""
    tables: list[ParsedTable] = []
    try:
        finder = page.find_tables()
    except Exception:  # pragma: no cover - engine-specific edge cases
        return tables

    for i, table in enumerate(getattr(finder, "tables", []) or []):
        try:
            data = table.extract()
        except Exception:  # pragma: no cover
            continue
        rows = [
            [collapse_whitespace(str(cell)) if cell is not None else "" for cell in row]
            for row in data
            if any(cell not in (None, "") for cell in row)
        ]
        if len(rows) < 2:
            continue
        header, body = rows[0], rows[1:]
        # A "table" with a single column is almost always a mis-detected list.
        if len(header) < 2:
            continue
        bbox = tuple(float(v) for v in (table.bbox or (0, 0, 0, 0)))
        tables.append(ParsedTable(index=i, bbox=bbox, header=header, rows=body))  # type: ignore[arg-type]
    return tables


def _image_area_ratio(page: fitz.Page) -> tuple[int, float]:
    """Return (image_count, fraction of page area covered by raster images)."""
    page_area = float(page.rect.width * page.rect.height) or 1.0
    covered = 0.0
    count = 0
    try:
        infos = page.get_image_info()
    except Exception:  # pragma: no cover
        return 0, 0.0
    for info in infos:
        bbox = info.get("bbox")
        if not bbox:
            continue
        rect = fitz.Rect(bbox) & page.rect
        if rect.is_empty:
            continue
        count += 1
        covered += float(rect.width * rect.height)
    return count, min(1.0, covered / page_area)


def _classify_page(
    char_count: int, image_area_ratio: float, image_count: int, drawing_count: int
) -> PageKind:
    has_text = char_count >= MIN_CHARS_FOR_TEXT_PAGE
    if not has_text:
        if image_area_ratio >= SCANNED_IMAGE_AREA_RATIO or image_count > 0:
            return PageKind.SCANNED_IMAGE
        if drawing_count > 0:
            return PageKind.MIXED
        return PageKind.EMPTY
    if image_area_ratio >= MIXED_MIN_IMAGE_RATIO:
        return PageKind.MIXED
    return PageKind.DIGITAL_TEXT


def render_page_png(page: fitz.Page, dpi: int = RENDER_DPI) -> bytes:
    """Rasterise a page to PNG, bounded by :data:`MAX_RENDER_PIXELS`."""
    zoom = dpi / 72.0
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > MAX_RENDER_PIXELS:
        zoom *= MAX_RENDER_PIXELS / longest
    matrix = fitz.Matrix(zoom, zoom)
    try:
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png")
    except Exception as exc:  # pragma: no cover
        raise PdfParseError(f"Could not render page {page.number + 1}: {exc}", cause=exc) from exc


def parse_pdf(
    data: bytes,
    *,
    render_dir: Path | None = None,
    render_prefix: str = "page",
    filename: str | None = None,
) -> ParsedDocument:
    """Parse ``data`` into a :class:`ParsedDocument`.

    When ``render_dir`` is provided, pages that need visual interpretation are
    rasterised to ``<render_dir>/<render_prefix>-<n>.png``.
    """
    validate_pdf_bytes(data, filename=filename)
    doc = open_document(data)
    try:
        metadata = {
            k: v for k, v in (doc.metadata or {}).items() if isinstance(v, str) and v.strip()
        }
        metadata["pdf_version"] = getattr(doc, "pdf_version", lambda: None)() or ""
        try:
            metadata["has_toc"] = bool(doc.get_toc())
        except Exception:  # pragma: no cover
            metadata["has_toc"] = False

        pages: list[ParsedPage] = []
        scanned_pages = 0

        for index in range(doc.page_count):
            page = doc.load_page(index)
            blocks, text = _extract_blocks(page)
            tables = _extract_tables(page)
            image_count, area_ratio = _image_area_ratio(page)
            try:
                drawing_count = len(page.get_drawings())
            except Exception:  # pragma: no cover
                drawing_count = 0

            kind = _classify_page(len(text), area_ratio, image_count, drawing_count)
            if kind == PageKind.SCANNED_IMAGE:
                scanned_pages += 1

            parsed = ParsedPage(
                page_number=index + 1,
                kind=kind,
                width=float(page.rect.width),
                height=float(page.rect.height),
                rotation=int(page.rotation),
                text=text,
                blocks=blocks,
                tables=tables,
                image_count=image_count,
                vector_drawing_count=drawing_count,
                image_area_ratio=round(area_ratio, 4),
            )

            if render_dir is not None and parsed.needs_vision:
                render_dir.mkdir(parents=True, exist_ok=True)
                out = render_dir / f"{render_prefix}-{index + 1:04d}.png"
                out.write_bytes(render_page_png(page))
                parsed.render_path = out.name

            pages.append(parsed)

        requires_ocr = bool(pages) and scanned_pages / len(pages) >= 0.25
        log.info(
            "pdf.parsed",
            pages=len(pages),
            scanned_pages=scanned_pages,
            requires_ocr=requires_ocr,
            chars=sum(p.char_count for p in pages),
        )
        return ParsedDocument(
            page_count=doc.page_count,
            pages=pages,
            metadata=metadata,
            requires_ocr=requires_ocr,
        )
    finally:
        doc.close()


def probe_pdf(data: bytes) -> dict[str, Any]:
    """Lightweight metadata probe used at upload time (no full parse)."""
    validate_pdf_bytes(data)
    doc = open_document(data)
    try:
        return {
            "page_count": doc.page_count,
            "metadata": {
                k: v for k, v in (doc.metadata or {}).items() if isinstance(v, str) and v.strip()
            },
        }
    finally:
        doc.close()


def build_pdf(pages: list[str], *, title: str = "Test Document") -> bytes:
    """Create a simple text PDF. Used by tests and the sample-deck generator.

    ``insert_textbox`` writes nothing at all when the text overflows the box,
    which turns a dense fixture page into a silently blank one. Shrink the type
    until the content fits so what a caller passes in is what a parser reads
    back out.
    """
    doc = fitz.open()
    box = fitz.Rect(48, 48, 744, 564)
    for content in pages:
        page = doc.new_page(width=792, height=612)  # 4:3 slide-ish landscape
        for fontsize in (13, 11, 9, 8, 7, 6, 5, 4):
            # A negative return means the text did not fit and nothing was drawn.
            if page.insert_textbox(box, content, fontsize=fontsize, fontname="helv") >= 0:
                break
    doc.set_metadata({"title": title})
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()
