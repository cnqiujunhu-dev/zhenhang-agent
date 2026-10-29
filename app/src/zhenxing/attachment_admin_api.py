"""Admin-only image diagnostics: metadata inspection, never image authority.

No preview, image text, original field values, prompt or execution capability is
returned. Successful inspection appends an access audit and changes no material.
"""
import math
import re

from fastapi import Depends, Query, Response
from sqlalchemy import func, select

from .access_models import AccessAudit, LoginAccount
from .accounts import require_account_role
from .admin_api import redact_observation
from .attachment_models import Attachment, AttachmentEvent, AttachmentJob
from .authentication import as_utc
from .contracts import DomainError
from .models import utcnow

FIELDS = {'amount_minor', 'currency', 'date', 'merchant', 'counterparty', 'recipient_card', 'status', 'chat_text', 'role_context'}


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', value) else None


def _label(value):
    return redact_observation(value[:120]) if isinstance(value, str) else None


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _time(value):
    return as_utc(value).isoformat() if value else None


def _row(row):
    return {'attachment_id': row.id, 'purpose': _code(row.purpose), 'state': _code(row.state),
        'version': row.version, 'created_at': _time(row.created_at), 'updated_at': _time(row.updated_at),
        'expires_at': _time(row.expires_at), 'error_code': _code(row.error_code)}


def _diagnostic(session, row):
    result = _row(row)
    job = session.scalar(select(AttachmentJob).where(AttachmentJob.attachment_id == row.id))
    active = row.state not in {'DELETED', 'EXPIRED'} and as_utc(row.expires_at) > utcnow()
    extraction = row.extraction_json if active and isinstance(row.extraction_json, dict) else {}
    evidence = extraction.get('evidence')
    rows = []
    for item in evidence[:64] if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or item.get('field') not in FIELDS:
            continue
        score, box = item.get('confidence'), item.get('box')
        score = score if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1 else None
        valid_box = isinstance(box, list) and len(box) == 4 and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in box) and box[0] < box[2] and box[1] < box[3]
        rows.append({'field': item['field'], 'raw_score': score, 'position': box if valid_box else None})
    usage = extraction.get('usage') if isinstance(extraction.get('usage'), dict) else {}
    result.update({'read_only': True, 'business_write_performed': False, 'image_content_included': False,
        'extraction_available': bool(extraction), 'provider': _label(extraction.get('provider')),
        'model': _label(extraction.get('model')), 'http_attempts': _count(extraction.get('model_http_attempts')),
        'usage': {key: _count(usage.get(key)) for key in ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens')},
        'field_observations': rows, 'scores_are_calibrated_safety_confidence': False,
        'job': {'job_id': job.id, 'status': _code(job.status), 'attempts': job.attempts,
            'last_error': _code(job.last_error), 'completed_at': _time(job.completed_at)} if job else None,
        'events': [{'sequence': event.seq, 'type': _code(event.event_type), 'observed_at': _time(event.created_at)}
            for event in session.scalars(select(AttachmentEvent).where(AttachmentEvent.attachment_id == row.id)
                .order_by(AttachmentEvent.seq.desc()).limit(30))]})
    return result


def install_attachment_admin_routes(app, get_session, get_principal):
    @app.get('/api/v2/admin/image-diagnostics')
    def list_diagnostics(response: Response, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50),
                         session=Depends(get_session), principal=Depends(get_principal)):
        admin = require_account_role(session, principal, {'ADMIN'})
        response.headers['Cache-Control'] = 'private, no-store'
        total = session.scalar(select(func.count()).select_from(Attachment))
        rows = list(session.scalars(select(Attachment).order_by(Attachment.created_at.desc(), Attachment.id)
            .offset((page - 1) * page_size).limit(page_size)))
        projection = {'schema_version': 'admin-image-diagnostics-v1', 'read_only': True,
            'business_write_performed': False, 'items': [_row(row) for row in rows],
            'page': page, 'page_size': page_size, 'total': total}
        audit = AccessAudit(actor_account_id=admin.id, action='ADMIN_IMAGE_DIAGNOSTIC_LIST_READ',
            payload_json={'reason': '只读查看图片处理诊断目录', 'page': page, 'page_size': page_size, 'returned_count': len(rows)})
        session.add(audit)
        session.commit()
        return {**projection, 'access_audit_id': audit.id}

    @app.get('/api/v2/admin/image-diagnostics/{attachment_id}')
    def detail(attachment_id: str, response: Response, session=Depends(get_session), principal=Depends(get_principal)):
        admin = require_account_role(session, principal, {'ADMIN'})
        response.headers['Cache-Control'] = 'private, no-store'
        row = session.get(Attachment, attachment_id)
        if row is None:
            raise DomainError('ATTACHMENT_NOT_FOUND', '未找到这份图片处理记录', 404)
        projection = _diagnostic(session, row)
        owner = session.scalar(select(LoginAccount).where(LoginAccount.user_id == row.user_id,
            LoginAccount.sandbox_id == row.sandbox_id))
        audit = AccessAudit(actor_account_id=admin.id, target_account_id=owner.id if owner else None,
            action='ADMIN_IMAGE_DIAGNOSTIC_READ', payload_json={'reason': '只读核查图片识别与字段诊断',
                'attachment_id': row.id, 'attachment_version': row.version})
        session.add(audit)
        session.commit()
        return {**projection, 'schema_version': 'admin-image-diagnostic-v1', 'access_audit_id': audit.id}
