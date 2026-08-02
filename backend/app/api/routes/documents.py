"""Document upload and inspection endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    PaginationDep,
    UploadPrincipal,
    rate_limit,
    upload_rate_limit,
)
from app.core.config import settings
from app.core.errors import DocumentTooLarge, NotFound
from app.core.logging import get_logger
from app.core.upload_tickets import issue_ticket
from app.db.models import AnalysisRun, DocumentPage
from app.schemas.api import (
    DocumentDetailOut,
    DocumentOut,
    Page,
    PageOut,
    RunOut,
    UploadResponse,
    UploadTicketOut,
)
from app.services import documents as document_service
from app.services import runs as run_service

log = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(upload_rate_limit)],
    summary="Upload a pitch deck",
)
async def upload_document(
    request: Request,
    session: DbSession,
    principal: UploadPrincipal,
    file: Annotated[UploadFile, File(description="The pitch deck as a PDF.")],
    notes: Annotated[str | None, Form()] = None,
    analyze: Annotated[bool, Form()] = True,
) -> UploadResponse:
    """Store a PDF and, unless told otherwise, immediately queue its analysis.

    Accepts either an API key or a single-use ``X-Upload-Ticket`` (see
    :func:`app.api.deps.require_upload_principal`), which is how the browser
    reaches this endpoint without routing a large body through a serverless
    proxy that would reject it.
    """
    _reject_oversized_body(request)
    data = await _read_upload(file)

    document, created = document_service.store_document(
        session,
        data=data,
        filename=file.filename or "document.pdf",
        notes=notes,
        uploaded_by=principal.label,
    )
    session.commit()

    run = None
    if analyze:
        run = run_service.create_run(session, document_id=document.id, requested_by=principal.label)

    return UploadResponse(
        document=DocumentOut.model_validate(document),
        created=created,
        run=RunOut.model_validate(run) if run else None,
    )


@router.post(
    "/upload-ticket",
    response_model=UploadTicketOut,
    dependencies=[Depends(rate_limit)],
    summary="Mint a single-use ticket for a direct browser upload",
)
def create_upload_ticket(principal: CurrentPrincipal) -> UploadTicketOut:
    """Issue a short-lived credential authorising one direct upload.

    Called server-to-server by a frontend that holds the API key, on behalf of
    a browser that must not.  The browser then posts the file straight here
    with ``X-Upload-Ticket``, bypassing a serverless proxy whose request-body
    ceiling is lower than this API's own upload limit.
    """
    ticket = issue_ticket()
    return UploadTicketOut(
        token=ticket.token,
        expires_in_seconds=ticket.ttl_seconds,
        max_bytes=ticket.max_bytes,
    )


#: A multipart body carries the file plus boundaries, field names and the
#: notes field. Comparing the whole body against the file limit would reject a
#: file that is legitimately just under it, so allow a little slack here and
#: let the per-file check in `_read_upload` be the exact one.
_MULTIPART_OVERHEAD_ALLOWANCE = 64 * 1024


def _reject_oversized_body(request: Request) -> None:
    """Fail an over-limit upload on its declared length, before reading it.

    Streaming already enforces the ceiling, but only after the client has sent
    everything up to it.  A declared ``Content-Length`` is enough to answer
    immediately, which turns a minute of wasted upload into a fast, specific
    error the UI can act on.
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > settings.max_upload_bytes + _MULTIPART_OVERHEAD_ALLOWANCE:
        raise DocumentTooLarge(
            f"The upload is {declared / 1_048_576:.1f} MB; the limit is "
            f"{settings.max_upload_mb} MB.",
            detail={"limit_bytes": settings.max_upload_bytes, "declared_bytes": declared},
        )


async def _read_upload(file: UploadFile) -> bytes:
    """Read the upload with a hard size ceiling enforced during streaming.

    Reading first and checking afterwards would let a malicious client push an
    arbitrarily large body into memory before we reject it.
    """
    limit = settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise DocumentTooLarge(
                f"The upload exceeds the {settings.max_upload_mb} MB limit.",
                detail={"limit_bytes": limit},
            )
        chunks.append(chunk)
    await file.close()
    return b"".join(chunks)


@router.get(
    "",
    response_model=Page[DocumentOut],
    dependencies=[Depends(rate_limit)],
    summary="List uploaded documents",
)
def list_documents(session: DbSession, page: PaginationDep) -> Page[DocumentOut]:
    items = document_service.list_documents(session, limit=page.limit, offset=page.offset)
    return Page[DocumentOut](
        items=[DocumentOut.model_validate(d) for d in items],
        total=document_service.count_documents(session),
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetailOut,
    dependencies=[Depends(rate_limit)],
    summary="Get one document",
)
def get_document(session: DbSession, document_id: str) -> DocumentDetailOut:
    document = document_service.get_document(session, document_id)
    latest = run_service.latest_run_for_document(session, document_id)
    run_count = int(
        session.execute(
            select(func.count(AnalysisRun.id)).where(AnalysisRun.document_id == document_id)
        ).scalar_one()
    )
    detail = DocumentDetailOut.model_validate(document)
    detail.latest_run_id = latest.id if latest else None
    detail.run_count = run_count
    return detail


@router.get(
    "/{document_id}/pages",
    response_model=list[PageOut],
    dependencies=[Depends(rate_limit)],
    summary="List parsed pages",
)
def list_pages(
    session: DbSession,
    document_id: str,
    include_text: Annotated[bool, Query()] = False,
) -> list[PageOut]:
    document_service.get_document(session, document_id)
    rows = session.execute(
        select(DocumentPage)
        .where(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number)
    ).scalars()
    out: list[PageOut] = []
    for row in rows:
        page = PageOut.model_validate(row)
        page.has_render = bool(row.render_path)
        page.text = row.text if include_text else None
        out.append(page)
    return out


@router.get(
    "/{document_id}/pages/{page_number}/render",
    dependencies=[Depends(rate_limit)],
    summary="Get a rendered page image",
    response_class=FileResponse,
)
def get_page_render(session: DbSession, document_id: str, page_number: int) -> FileResponse:
    document_service.get_document(session, document_id)
    path = document_service.page_render_path(document_id, page_number)
    if not path.exists():
        raise NotFound("No rendered image exists for this page.")
    return FileResponse(path, media_type="image/png", filename=f"page-{page_number}.png")


@router.get(
    "/{document_id}/file",
    dependencies=[Depends(rate_limit)],
    summary="Download the original PDF",
    response_class=FileResponse,
)
def download_document(session: DbSession, document_id: str) -> FileResponse:
    document = document_service.get_document(session, document_id)
    from pathlib import Path

    path = Path(document.storage_path)
    if not path.exists():
        raise NotFound("The stored file for this document is missing.")
    return FileResponse(path, media_type="application/pdf", filename=document.filename)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit)],
    summary="Delete a document and all its analyses",
)
def delete_document(session: DbSession, document_id: str) -> Response:
    document_service.delete_document(session, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
