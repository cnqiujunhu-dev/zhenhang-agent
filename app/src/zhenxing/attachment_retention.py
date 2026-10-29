"""Bounded private-blob retention; no cloud calls or bank data mutations."""
from sqlalchemy import case, or_, select

from .attachment_models import Attachment, AttachmentJob
from .attachments import _blob_path, _event, as_utc
from .contracts import DomainError
from .models import utcnow


def cleanup_attachment_retention(session, *, root=None, limit=20):
    """Caller commits. Locks Attachment -> Job, then deletes blobs post-commit.

    Each tick scans at most twenty rows. Missing blobs are harmless. A failed
    post-commit unlink leaves its inaccessible DELETED/EXPIRED row available for
    a later bounded retry. No private filesystem path is returned or logged.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise DomainError("ATTACHMENT_RETENTION_LIMIT", "附件维护每次最多扫描20条", 422)
    now = utcnow()
    rows = list(session.scalars(select(Attachment).where(or_(Attachment.state == "DELETED",
        Attachment.expires_at <= now)).order_by(
            case((Attachment.state.not_in(["DELETED", "EXPIRED"]), 0), else_=1),
            Attachment.updated_at, Attachment.id).limit(limit).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)))
    changed = 0
    for row in rows:
        if row.state not in {"DELETED", "EXPIRED"} and as_utc(row.expires_at) <= now:
            row.state = "EXPIRED"
            row.version += 1
            row.error_code = "ATTACHMENT_EXPIRED"
            row.extraction_json = None
            job = session.scalar(select(AttachmentJob).where(AttachmentJob.attachment_id == row.id)
                                 .with_for_update().execution_options(populate_existing=True))
            if job:
                job.status, job.completed_at = "CANCELLED", now
                job.lease_owner, job.lease_until = None, None
            _event(session, row, "RETENTION_EXPIRED", {"version": row.version, "sha256": row.sha256,
                                                       "reason": "SEVEN_DAY_DOCUMENT_TTL"})
            changed += 1
        path = _blob_path(row, root)
        if path.is_file():
            pending = session.info.setdefault("private_attachment_delete_files", [])
            if path not in pending:
                pending.append(path)
            changed += 1
        # Rotate old already-cleared rows: the first twenty tombstones must not
        # permanently starve expired live documents or later unlink retries.
        row.updated_at = now
    session.flush()
    return changed
