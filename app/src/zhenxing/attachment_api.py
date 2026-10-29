"""Authenticated private image upload and explicit field review, never execution."""
from typing import Any, Literal

from fastapi import Depends, Header, Query, Request, Response
from pydantic import Field, StrictBool
from sqlalchemy import select
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.datastructures import UploadFile

from .attachment_models import Attachment
from .attachment_limits import MAX_FILES_PER_UPLOAD_REQUEST, MAX_IMAGES_PER_BATCH
from .attachments import (
    ALLOWED_MIME, MAX_BYTES, MAX_EDGE, MAX_PIXELS, attachment_view, delete_attachment,
    owned_attachment, preview_bytes, review_attachment, review_public, upload_attachment,
)
from .contracts import DomainError, StrictModel
from .permissions import require_sandbox_owner
from .vision_gateway import DeepSeekVisionGateway


class AttachmentReviewRequest(StrictModel):
    expected_version: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_fields: list[str] = Field(min_length=1, max_length=9)
    corrected_fields: dict[str, Any] = Field(default_factory=dict)
    user_confirmed: StrictBool

class AttachmentReprocessRequest(StrictModel):
    expected_version: int = Field(ge=1,strict=True)
    cloud_processing_consent: StrictBool


async def _bounded_body(request):
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError:
            raise DomainError("ATTACHMENT_BODY_INVALID", "上传长度无效", 400) from None
        if length > MAX_BYTES+16384:
            raise DomainError("ATTACHMENT_TOO_LARGE", "图片上传不得超过5MiB", 413)
    chunks, count = [], 0
    async for chunk in request.stream():
        count += len(chunk)
        if count > MAX_BYTES+16384:
            raise DomainError("ATTACHMENT_TOO_LARGE", "图片上传不得超过5MiB", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def install_attachment_routes(app, get_session, get_principal, *, storage_root=None):
    @app.post("/api/v3/attachments", status_code=202)
    async def upload(request: Request, purpose: Literal["BILLING", "FRAUD"] | None = None,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                     session=Depends(get_session), principal=Depends(get_principal)):
        require_sandbox_owner(session, principal)  # Reject unauthorized bodies before parsing.
        body = await _bounded_body(request)
        content_type = request.headers.get("content-type", "")
        if content_type.lower().startswith("multipart/form-data"):
            async def stream():
                yield body
            try:
                form = await MultiPartParser(request.headers, stream(), max_files=MAX_FILES_PER_UPLOAD_REQUEST, max_fields=2, max_part_size=8192).parse()
            except (MultiPartException, ValueError):
                raise DomainError("ATTACHMENT_FORM_INVALID", "仅接受一个file及purpose/cloud_processing_consent字段", 422) from None
            try:
                if set(form.keys()) != {"file", "purpose", "cloud_processing_consent"} or len(form.multi_items()) != 3:
                    raise DomainError("ATTACHMENT_FORM_INVALID", "上传字段缺失、重复或含未允许字段", 422)
                file = form["file"]
                if not isinstance(file, UploadFile):
                    raise DomainError("ATTACHMENT_FORM_INVALID", "file必须是图片文件", 422)
                if purpose is not None and purpose != form["purpose"]:
                    raise DomainError("ATTACHMENT_PURPOSE_CONFLICT", "图片用途字段冲突", 422)
                row = upload_attachment(session, principal, await file.read(MAX_BYTES+1), file.content_type,
                    form["purpose"], cloud_processing_consent=form["cloud_processing_consent"] == "true",
                    filename=file.filename or "image", root=storage_root, idempotency_key=idempotency_key)
            finally:
                await form.close()
        else:
            row = upload_attachment(session, principal, body, content_type.split(";")[0].strip(), purpose,
                cloud_processing_consent=request.headers.get("x-cloud-processing-consent") == "true",
                root=storage_root, idempotency_key=idempotency_key)
        session.commit()
        return attachment_view(session, principal, row)

    @app.get("/api/v3/attachments")
    @app.get("/api/v3/attachments/list", include_in_schema=False)
    def list_attachments(limit: int = Query(default=30, ge=1, le=100), session=Depends(get_session), principal=Depends(get_principal)):
        require_sandbox_owner(session, principal)
        rows = session.scalars(select(Attachment).where(Attachment.sandbox_id == principal.sandbox_id,
            Attachment.user_id == principal.user_id, Attachment.state != "DELETED").order_by(Attachment.created_at.desc(), Attachment.id).limit(limit))
        return {"attachments": [attachment_view(session, principal, row) for row in rows],
            "limits": {"max_bytes": MAX_BYTES, "max_pixels": MAX_PIXELS, "max_edge": MAX_EDGE,
                       "allowed_mime_types": list(ALLOWED_MIME.values()),
                       "max_images_per_batch": MAX_IMAGES_PER_BATCH,
                       "max_files_per_request": MAX_FILES_PER_UPLOAD_REQUEST},
            "cloud_processing": {"provider": "deepseek", "model": "deepseek-flash", "local_fallback": False,
                                 "configured": DeepSeekVisionGateway().configured}}

    @app.get("/api/v3/attachments/{attachment_id}")
    def get_attachment(attachment_id: str, session=Depends(get_session), principal=Depends(get_principal)):
        return attachment_view(session, principal, owned_attachment(session, principal, attachment_id))

    @app.get("/api/v3/attachments/{attachment_id}/preview")
    def preview(attachment_id: str, expires: int = Query(ge=1), signature: str = Query(pattern=r"^[0-9a-f]{64}$"),
                session=Depends(get_session), principal=Depends(get_principal)):
        data, mime = preview_bytes(session, principal, attachment_id, expires, signature, root=storage_root)
        return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": "inline"})

    @app.post("/api/v3/attachments/{attachment_id}/review")
    def review(attachment_id: str, payload: AttachmentReviewRequest, session=Depends(get_session), principal=Depends(get_principal)):
        row, reviewed = review_attachment(session, principal, attachment_id, **payload.model_dump())
        session.commit()
        return {"attachment": attachment_view(session, principal, row), "review": review_public(reviewed)}

    @app.post('/api/v3/attachments/{attachment_id}/reprocess',status_code=202)
    def reprocess(attachment_id:str,payload:AttachmentReprocessRequest,session=Depends(get_session),principal=Depends(get_principal)):
        from .attachments import reprocess_attachment
        row=reprocess_attachment(session,principal,attachment_id,root=storage_root,**payload.model_dump())
        session.commit()
        return attachment_view(session,principal,row)

    @app.delete("/api/v3/attachments/{attachment_id}")
    def delete(attachment_id: str, expected_version: int = Query(ge=1), session=Depends(get_session), principal=Depends(get_principal)):
        row = delete_attachment(session, principal, attachment_id, expected_version, root=storage_root)
        session.commit()
        return {"attachment_id": row.id, "state": row.state, "version": row.version, "deleted": True}
