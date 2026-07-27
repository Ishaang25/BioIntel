"""Document ingestion service."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound, UnsupportedDocument
from app.core.logging import get_logger
from app.db.models import Document
from app.ingestion.pdf_parser import compute_hash, probe_pdf, validate_pdf_bytes

log = get_logger(__name__)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_LEN = 200


def sanitize_filename(name: str) -> str:
    """Reduce an uploaded filename to something safe to store and display.

    Defends against path traversal, control characters, and Windows reserved
    names.  The original is never used to build a filesystem path -- storage
    paths are derived from the document id -- but the sanitised name is shown
    in the UI and included in exports.
    """
    name = (name or "document.pdf").strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME_RE.sub("_", name).strip("._") or "document.pdf"
    stem, _, extension = name.rpartition(".")
    if not stem:
        stem, extension = name, "pdf"
    if stem.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        stem = f"_{stem}"
    if extension.lower() != "pdf":
        extension = "pdf"
    return f"{stem[: MAX_FILENAME_LEN - 4]}.{extension}"


def store_document(
    session: Session,
    *,
    data: bytes,
    filename: str,
    notes: str | None = None,
    uploaded_by: str | None = None,
    deduplicate: bool = True,
) -> tuple[Document, bool]:
    """Validate, persist and register an uploaded PDF.

    Returns ``(document, created)``.  Identical content re-uploaded returns
    the existing document so an analyst who uploads the same deck twice does
    not pay to parse it twice.
    """
    safe_name = sanitize_filename(filename)
    validate_pdf_bytes(data, filename=safe_name)

    digest = compute_hash(data)
    if deduplicate:
        existing = (
            session.execute(select(Document).where(Document.content_hash == digest))
            .scalars()
            .first()
        )
        if existing is not None and Path(existing.storage_path).exists():
            log.info("document.deduplicated", document_id=existing.id, hash=digest[:12])
            return existing, False

    probe = probe_pdf(data)

    document = Document(
        filename=safe_name,
        content_hash=digest,
        size_bytes=len(data),
        mime_type="application/pdf",
        storage_path="",  # set below, once the id exists
        page_count=int(probe["page_count"]),
        pdf_metadata=probe["metadata"],
        notes=notes,
        uploaded_by=uploaded_by,
    )
    session.add(document)
    session.flush()

    settings.ensure_directories()
    target = settings.uploads_dir / f"{document.id}.pdf"
    target.write_bytes(data)
    document.storage_path = str(target)

    log.info(
        "document.stored",
        document_id=document.id,
        pages=document.page_count,
        size_bytes=document.size_bytes,
    )
    return document, True


def get_document(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise NotFound(f"Document {document_id} was not found.")
    return document


def list_documents(session: Session, *, limit: int = 50, offset: int = 0) -> list[Document]:
    return list(
        session.execute(
            select(Document)
            .order_by(Document.created_at.desc())
            .limit(min(limit, 200))
            .offset(max(0, offset))
        ).scalars()
    )


def count_documents(session: Session) -> int:
    from sqlalchemy import func

    return int(session.execute(select(func.count(Document.id))).scalar_one())


def delete_document(session: Session, document_id: str) -> None:
    """Remove a document, its derived files and every analysis of it."""
    document = get_document(session, document_id)
    path = Path(document.storage_path)
    render_dir = settings.renders_dir / document.id
    session.delete(document)
    session.flush()

    try:
        if path.exists():
            path.unlink()
        if render_dir.exists():
            for file in render_dir.iterdir():
                file.unlink()
            render_dir.rmdir()
    except OSError:
        log.warning("document.file_cleanup_failed", document_id=document_id, exc_info=True)


def read_document_bytes(document: Document) -> bytes:
    path = Path(document.storage_path)
    if not path.exists():
        raise NotFound("The stored file for this document is missing.")
    return path.read_bytes()


def page_render_path(document_id: str, page_number: int) -> Path:
    """Path of a rasterised page, validated to stay inside the render root."""
    root = settings.renders_dir.resolve()
    candidate = (root / document_id / f"page-{page_number:04d}.png").resolve()
    if not candidate.is_relative_to(root):
        raise UnsupportedDocument("Invalid render path.")
    return candidate
