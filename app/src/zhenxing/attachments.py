"""Private, owner-scoped image evidence lifecycle, with no bank write capability."""
import hashlib
import hmac
import io
import os
import re
import secrets
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session

from .attachment_models import Attachment, AttachmentEvent, AttachmentJob, AttachmentReview
from .contracts import DomainError
from .models import Account, Subscription, Transaction, new_id, utcnow
from .permissions import require_sandbox_owner
from .planning import canonical_hash
from .services import Principal
from .vision_gateway import DeepSeekVisionGateway, ObservedFields, redact_document_text, safe_vision_model_observation
from .resource_defaults import ARCHIVED_RESOURCE_STATUS


MAX_BYTES = 5*1024*1024
MAX_PIXELS = 24_000_000
MAX_EDGE = 8192
ALLOWED_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
FIELDS = set(ObservedFields.model_fields)
_PREVIEW_KEY = secrets.token_bytes(32)


@event.listens_for(Session, "after_commit")
def _commit_attachment_files(session):
    if session.in_nested_transaction():
        return
    session.info.pop("private_attachment_new_files", None)
    for path in session.info.pop("private_attachment_delete_files", []):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The row is already inaccessible. Do not undo its committed revoke
            # or expose filesystem details; stale private blobs can be purged.
            pass


@event.listens_for(Session, "after_soft_rollback")
def _rollback_attachment_files(session, previous_transaction):
    if previous_transaction.parent is not None:
        return
    session.info.pop("private_attachment_delete_files", None)
    for path in session.info.pop("private_attachment_new_files", []):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def storage_root(value=None):
    return Path(value or os.getenv("ZX_ATTACHMENTS_DIR") or Path(__file__).resolve().parents[2]/"var"/"attachments").resolve()


def _blob_path(row, root=None):
    base = storage_root(root)
    expected = f"{row.sandbox_id}/{row.user_id}/{row.id}." + {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[row.mime_type]
    try:
        for part in (row.sandbox_id, row.user_id, row.id):
            UUID(part)
    except (ValueError, TypeError):
        raise DomainError("ATTACHMENT_STORAGE_INVALID", "附件存储身份无效", 409) from None
    target = (base/row.blob_key).resolve()
    if row.blob_key != expected or not target.is_relative_to(base):
        raise DomainError("ATTACHMENT_STORAGE_INVALID", "附件路径不属于受控私有存储", 409)
    return target


def sanitize_image(data, declared_mime):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise DomainError("ATTACHMENT_TOO_LARGE", "图片须非空且不超过5MiB", 413)
    if declared_mime not in ALLOWED_MIME.values():
        raise DomainError("ATTACHMENT_MEDIA_TYPE", "仅接受JPEG、PNG、WebP静态图片，不接受SVG或远程URL", 415)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                kind, size = image.format, image.size
                if kind not in ALLOWED_MIME or ALLOWED_MIME[kind] != declared_mime:
                    raise DomainError("ATTACHMENT_MEDIA_MISMATCH", "声明的类型与真实图片内容不一致", 415)
                if min(size) < 1 or max(size) > MAX_EDGE or size[0]*size[1] > MAX_PIXELS:
                    raise DomainError("ATTACHMENT_PIXEL_LIMIT", "图片不得超过24MP或任一边8192像素", 413)
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise DomainError("ATTACHMENT_ANIMATION", "不接受动画或多帧图片", 415)
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                image = ImageOps.exif_transpose(image)
                image = image.convert("RGBA" if kind == "PNG" and "A" in image.getbands() else "RGB")
                output = io.BytesIO()
                options = {"JPEG": {"quality": 92, "optimize": True}, "PNG": {"optimize": True}, "WEBP": {"quality": 92, "method": 4}}[kind]
                image.save(output, format=kind, **options)
                normalized, width, height = output.getvalue(), image.width, image.height
    except DomainError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DomainError("ATTACHMENT_INVALID_IMAGE", "图片解码或完整性校验未通过", 415) from None
    if len(normalized) > MAX_BYTES:
        raise DomainError("ATTACHMENT_NORMALIZED_TOO_LARGE", "安全重编码后的图片超过5MiB，请选择较小图片", 413)
    return normalized, ALLOWED_MIME[kind], width, height


def _event(session, row, kind, payload):
    previous = session.scalar(select(AttachmentEvent).where(AttachmentEvent.attachment_id == row.id).order_by(AttachmentEvent.seq.desc()).limit(1))
    seq, previous_hash = (previous.seq+1, previous.event_hash) if previous else (1, "0"*64)
    event_hash = canonical_hash({"attachment_id": row.id, "seq": seq, "event_type": kind, "payload": payload, "previous_hash": previous_hash})
    session.add(AttachmentEvent(attachment_id=row.id, sandbox_id=row.sandbox_id, user_id=row.user_id,
        seq=seq, event_type=kind, payload_json=payload, previous_hash=previous_hash, event_hash=event_hash))
    session.flush()


def owned_attachment(session, principal, identifier, *, lock=False, allow_deleted=False):
    require_sandbox_owner(session, principal)
    query = select(Attachment).where(Attachment.id == identifier, Attachment.sandbox_id == principal.sandbox_id,
                                    Attachment.user_id == principal.user_id)
    row = session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None or (row.state == "DELETED" and not allow_deleted):
        raise DomainError("ATTACHMENT_NOT_FOUND", "附件不存在", 404)
    return row


def upload_attachment(session, principal, data, mime_type, purpose, *, cloud_processing_consent, filename="image", root=None, idempotency_key=None):
    require_sandbox_owner(session, principal)
    if purpose not in {"BILLING", "FRAUD"} or cloud_processing_consent is not True:
        raise DomainError("ATTACHMENT_CONSENT_REQUIRED", "请明确核对扣款或诈骗截图用途，并认可向DeepSeek云API发送本图进行提取", 422)
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 120):
        raise DomainError("ATTACHMENT_INVALID_KEY", "附件幂等键无效", 422)
    normalized, actual_mime, width, height = sanitize_image(data, mime_type)
    original_sha = hashlib.sha256(data).hexdigest()
    request_hash = canonical_hash({"original_sha256": original_sha, "purpose": purpose, "consent": True})
    if idempotency_key:
        old = session.scalar(select(Attachment).where(Attachment.sandbox_id == principal.sandbox_id,
            Attachment.user_id == principal.user_id, Attachment.idempotency_key == idempotency_key))
        if old:
            if old.request_hash != request_hash or old.state == "DELETED":
                raise DomainError("ATTACHMENT_IDEMPOTENCY_CONFLICT", "同键不能改换图片、用途或复活已删除附件", 409)
            return old
    now, identifier = utcnow(), new_id()
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[actual_mime]
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(filename).replace("\\", "/").split("/")[-1])[:128] or "image"
    row = Attachment(id=identifier, sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        purpose=purpose, idempotency_key=idempotency_key, request_hash=request_hash, original_sha256=original_sha,
        sha256=hashlib.sha256(normalized).hexdigest(), mime_type=actual_mime, byte_size=len(normalized), width=width, height=height,
        display_name=name, blob_key=f"{principal.sandbox_id}/{principal.user_id}/{identifier}.{extension}",
        cloud_consented_at=now, expires_at=now+timedelta(days=7))
    path = _blob_path(row, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive new blob: no caller filename or previous user object is overwritten.
    with path.open("xb") as saved:
        saved.write(normalized)
    session.info.setdefault("private_attachment_new_files", []).append(path)
    session.add(row)
    session.flush()
    session.add(AttachmentJob(attachment_id=row.id))
    _event(session, row, "UPLOADED", {"sha256": row.sha256, "purpose": purpose, "byte_size": row.byte_size, "consent": True})
    return row


def reprocess_attachment(session,principal,identifier,*,expected_version,cloud_processing_consent,root=None):
    from .authentication import as_utc
    from .vision_gateway import VISION_POLICY_VERSION
    require_sandbox_owner(session,principal)
    row=owned_attachment(session,principal,identifier,lock=True)
    if cloud_processing_consent is not True:raise DomainError('ATTACHMENT_CONSENT_REQUIRED','请先同意将这张图片发送至云端重新识别。',422)
    if row.version!=expected_version or row.state not in {'EXTRACTED','REVIEWED','FAILED'} or as_utc(row.expires_at)<=utcnow():
        raise DomainError('ATTACHMENT_VERSION_CHANGED','材料状态已变化，请先重新读取。',409)
    try:data=_blob_path(row,root).read_bytes()
    except OSError:raise DomainError('ATTACHMENT_BLOB_MISSING','原图暂不可读取，请重新上传。',404) from None
    if hashlib.sha256(data).hexdigest()!=row.sha256:raise DomainError('ATTACHMENT_HASH_CHANGED','原图校验失败。',409)
    key='reprocess:'+canonical_hash({'source':row.id,'version':row.version,'policy':VISION_POLICY_VERSION})
    return upload_attachment(session,principal,data,row.mime_type,row.purpose,cloud_processing_consent=True,
        filename=row.display_name,root=root,idempotency_key=key)


def _preview_url(row):
    expires = min(int((utcnow()+timedelta(seconds=120)).timestamp()), int(as_utc(row.expires_at).timestamp()))
    signature = hmac.new(_PREVIEW_KEY, f"{row.id}|{row.sandbox_id}|{row.user_id}|{row.version}|{row.sha256}|{expires}".encode(), hashlib.sha256).hexdigest()
    return f"/api/v3/attachments/{row.id}/preview?expires={expires}&signature={signature}", datetime.fromtimestamp(expires, timezone.utc).isoformat()


def preview_bytes(session, principal, identifier, expires, signature, *, root=None):
    row = owned_attachment(session, principal, identifier)
    if as_utc(row.expires_at) <= utcnow() or type(expires) is not int or not 0 < expires-utcnow().timestamp() <= 121:
        raise DomainError("ATTACHMENT_PREVIEW_EXPIRED", "图片预览链接已过期，请重新获取", 410)
    expected = hmac.new(_PREVIEW_KEY, f"{row.id}|{row.sandbox_id}|{row.user_id}|{row.version}|{row.sha256}|{expires}".encode(), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise DomainError("ATTACHMENT_PREVIEW_INVALID", "预览签名或附件版本已变化", 403)
    try:
        data = _blob_path(row, root).read_bytes()
    except OSError:
        raise DomainError("ATTACHMENT_BLOB_MISSING", "私有图片文件不可用", 410) from None
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise DomainError("ATTACHMENT_HASH_CHANGED", "附件内容与已登记指纹不一致", 409)
    return data, row.mime_type


def review_public(row):
    return {"review_id": row.id, "source_kind": row.source_kind, "attachment_version": row.attachment_version,
        "sha256": row.sha256, "selected_fields": row.selected_fields_json, "fields": row.fields_json,
        "user_confirmed": True, "created_at": as_utc(row.created_at).isoformat()}


def attachment_audit_valid(session, row):
    previous = "0"*64
    for seq, item in enumerate(session.scalars(select(AttachmentEvent).where(
            AttachmentEvent.attachment_id == row.id).order_by(AttachmentEvent.seq)), 1):
        if (item.sandbox_id, item.user_id) != (row.sandbox_id, row.user_id) or item.seq != seq \
                or item.previous_hash != previous or item.event_hash != canonical_hash({"attachment_id": row.id,
                    "seq": seq, "event_type": item.event_type, "payload": item.payload_json, "previous_hash": previous}):
            return False
        previous = item.event_hash
    return previous != "0"*64


def billing_comparison(session, principal, fields):
    from datetime import date as calendar_date, time as day_time
    from sqlalchemy import func
    account = require_sandbox_owner(session, principal)
    result = {"status": "INSUFFICIENT_FIELDS", "transactions": [], "subscriptions": [], "conflicts": [],
              "bank_data_changed": False, "screenshot_proves_posting": False}
    amount, currency, merchant, observed_date = (fields.get(key) for key in ("amount_minor", "currency", "merchant", "date"))
    if type(amount) is not int or not currency or not merchant:
        return result
    merchant_key = merchant.strip().lower()
    status_text = fields.get("status") or ""
    refund = bool(re.search(r"(?i)退款|退回|返还|\bREFUND(?:ED)?\b|\bREVERSED\b", status_text))
    pending = bool(re.search(r"(?i)待处理|处理中|退款中|申请退款|失败|未支付|\bPENDING\b|\bFAILED\b", status_text))
    signed_amount = amount if refund else -amount
    start = end = None
    if observed_date:
        try:
            start = datetime.combine(calendar_date.fromisoformat(observed_date), day_time.min,
                                     timezone(timedelta(hours=8))).astimezone(timezone.utc)
            end = start + timedelta(days=1)
        except (TypeError, ValueError):
            result["conflicts"].append("DATE_INVALID: 请核对图片日期")
            return result
    if currency == "USD":
        from .fx_models import FxStatement
        query = select(FxStatement).where(FxStatement.sandbox_id == principal.sandbox_id,
            FxStatement.user_id == principal.user_id, FxStatement.currency == "USD", FxStatement.amount_minor == signed_amount,
            func.lower(func.trim(FxStatement.merchant)) == merchant_key)
        within_date = query.where(FxStatement.occurred_at >= start, FxStatement.occurred_at < end) if start else query
        matches = list(session.scalars(within_date.order_by(FxStatement.occurred_at.desc()).limit(101)))
        result["transactions"] = [{"statement_id": item.id, "operation_id": item.operation_id, "amount_minor": item.amount_minor,
            "currency": "USD", "merchant": item.merchant, "date": as_utc(item.occurred_at).astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            "source_kind": "FX", "operation_ref": item.operation_ref, "candidate_only": True} for item in matches[:20]]
        if not observed_date:
            result["conflicts"].append("DATE_MISSING: 不能仅按金额和商户认定同一笔")
        elif refund and not matches and session.scalar(select(FxStatement.id).where(
                FxStatement.sandbox_id == principal.sandbox_id, FxStatement.user_id == principal.user_id,
                FxStatement.currency == "USD", FxStatement.amount_minor == -amount,
                func.lower(func.trim(FxStatement.merchant)) == merchant_key).limit(1)) is not None:
            result.update(status="CONFLICT", conflicts=["REFUND_DIRECTION_DIFFERS: 截图退款方向不能匹配支出流水"])
        elif not matches and session.scalar(query.limit(1)) is not None:
            result.update(status="CONFLICT", conflicts=["DATE_DIFFERS: 同金额同商户的美元流水日期与截图不一致"])
        else:
            result["status"] = "NO_MATCH" if not matches else "ONE_CANDIDATE" if len(matches) == 1 else "MULTIPLE_CANDIDATES"
        if len(matches) > 100:
            result["conflicts"].append("CANDIDATE_LIST_TRUNCATED: 同日确有大量候选，展示前20条，不自动选择")
        if pending and matches:
            result["status"] = "CONFLICT"
            result["conflicts"].append("DOCUMENT_STATUS_UNSETTLED: 截图处理/失败状态不能证明银行已入账，需核对原回执")
        return result
    if currency != account.currency:
        result.update(status="CONFLICT", conflicts=["CURRENCY_DIFFERS_FROM_CNY_ACCOUNT: 不将美元金额当人民币扣款核对"])
        return result
    query = select(Transaction).where(Transaction.sandbox_id == principal.sandbox_id,
        Transaction.account_id == account.id, Transaction.amount_minor == signed_amount,
        func.lower(func.trim(Transaction.merchant)) == merchant_key)
    within_date = query.where(Transaction.occurred_at >= start, Transaction.occurred_at < end) if start else query
    matched = list(session.scalars(within_date.order_by(Transaction.occurred_at.desc()).limit(101)))
    result["transactions"] = [{"transaction_id": item.id, "amount_minor": item.amount_minor, "currency": account.currency,
        "merchant": item.merchant, "date": as_utc(item.occurred_at).astimezone(timezone(timedelta(hours=8))).date().isoformat(),
        "source_kind": item.source_kind, "operation_ref": item.operation_ref, "candidate_only": True} for item in matched[:20]]
    result["subscriptions"] = [{"subscription_id": item.id, "merchant": item.merchant, "amount_minor": item.amount_minor,
        "source_type": item.source_type, "status": item.status, "version": item.version, "candidate_only": True}
        for item in session.scalars(select(Subscription).where(Subscription.sandbox_id == principal.sandbox_id,
            Subscription.user_id == principal.user_id, Subscription.amount_minor == amount,
            Subscription.status != ARCHIVED_RESOURCE_STATUS,
            func.lower(func.trim(Subscription.merchant)) == merchant_key).limit(20))]
    if refund and not matched and session.scalar(select(Transaction.id).where(
            Transaction.sandbox_id == principal.sandbox_id, Transaction.account_id == account.id,
            Transaction.amount_minor == -amount, func.lower(func.trim(Transaction.merchant)) == merchant_key).limit(1)) is not None:
        result.update(status="CONFLICT", conflicts=["REFUND_DIRECTION_DIFFERS: 截图退款方向不能匹配支出流水"])
    elif observed_date and not matched and session.scalar(query.limit(1)) is not None:
        result.update(status="CONFLICT", conflicts=["DATE_DIFFERS: 金额和商户候选的银行日期与截图日期不一致"])
    elif not observed_date:
        result["conflicts"].append("DATE_MISSING: 不能因金额与商户相同便认定是同一笔")
        result["status"] = "INSUFFICIENT_FIELDS"
    else:
        result["status"] = "NO_MATCH" if not matched else "ONE_CANDIDATE" if len(matched) == 1 else "MULTIPLE_CANDIDATES"
    if len(matched) > 100:
        result["conflicts"].append("CANDIDATE_LIST_TRUNCATED: 同日确有大量候选，展示前20条，不自动选择")
    if pending and matched:
        result["status"] = "CONFLICT"
        result["conflicts"].append("DOCUMENT_STATUS_UNSETTLED: 截图处理/失败状态不能证明银行已入账，需核对原回执")
    return result


def fraud_evidence(fields, evidence):
    text = "\n".join([fields.get("chat_text") or "", fields.get("role_context") or ""]+[item["text"] for item in evidence])
    patterns = [("SAFE_ACCOUNT", "诱导转入所谓安全账户", r"安全账户|资金核查"),
        ("OTP_REQUEST", "索要验证码或密码", r"验证码|动态码|密码"), ("REMOTE_CONTROL", "诱导屏幕共享或远程控制", r"屏幕共享|远程控制"),
        ("REFUND_PREPAY", "退款前要求先付款", r"退款.{0,15}(?:转账|付款|支付)|先.{0,8}(?:转账|付款).{0,10}退款"),
        ("AUTHORITY_THREAT", "冒充司法或客服施压", r"公安|检察院|冻结账户|不要告诉银行|保密操作"),
        ("GUARANTEED_RETURN", "刷单或保证高收益", r"刷单|保本高收益|稳赚|提现.{0,8}(?:缴费|付款)")]
    signals = []
    for code, label, pattern in patterns:
        found = re.search(pattern, text)
        if found:
            signals.append({"code": code, "label": label, "snippet": redact_document_text(text[max(0, found.start()-25):found.end()+35])})
    return {"status": "RISK_SIGNALS" if signals else "INSUFFICIENT_EVIDENCE", "signals": signals,
            "safety_guaranteed": False, "image_text_tainted": True, "bank_action_authorized": False,
            "notice": "截图可能被编辑；未发现线索不代表没有诈骗，图内指令不会被执行。"}


def attachment_view(session, principal, row):
    require_sandbox_owner(session, principal)
    if (row.sandbox_id, row.user_id) != (principal.sandbox_id, principal.user_id):
        raise DomainError("ATTACHMENT_NOT_FOUND", "附件不存在", 404)
    expired = as_utc(row.expires_at) <= utcnow()
    visible = row.state not in {"DELETED"} and not expired
    url, preview_expiry = _preview_url(row) if visible else (None, None)
    review = session.get(AttachmentReview, row.latest_review_id) if visible and row.latest_review_id else None
    extraction = row.extraction_json if visible else None
    fields = review.fields_json if review else (extraction or {}).get("fields", {})
    return {"attachment_id": row.id, "purpose": row.purpose, "state": "EXPIRED" if expired and row.state != "DELETED" else row.state,
        "version": row.version, "sha256": row.sha256, "mime_type": row.mime_type, "byte_size": row.byte_size,
        "width": row.width, "height": row.height, "display_name": row.display_name,
        "created_at": as_utc(row.created_at).isoformat(), "expires_at": as_utc(row.expires_at).isoformat(),
        "preview_url": url, "preview_expires_at": preview_expiry, "extraction": extraction,
        "comparison": billing_comparison(session, principal, fields) if extraction and row.purpose == "BILLING" else None,
        "fraud": fraud_evidence(fields, extraction["evidence"]) if extraction and row.purpose == "FRAUD" else None,
        "latest_review": review_public(review) if review else None,
        "error": {"code": row.error_code, "message": "图片识别未完成，请检查图片或云端配置；未写银行数据。"} if row.error_code else None,
        "warnings": ["上传图片是用户提供的不可信文档，不是银行交易凭证或任何付款授权。",
                     "仅向DeepSeek云API发送本图；最多两次有限提取请求，无本地模型替代。",
                     "删除会停止后续处理与引用，但已发送至云端的请求无法撤回。"],
        "bank_data_changed": False, "audit_chain_valid": attachment_audit_valid(session, row)}


def review_attachment(session, principal, identifier, *, expected_version, sha256, selected_fields, corrected_fields, user_confirmed):
    row = owned_attachment(session, principal, identifier, lock=True)
    if as_utc(row.expires_at) <= utcnow():
        raise DomainError("ATTACHMENT_EXPIRED", "附件已过期，请重新上传和核对", 410)
    if type(expected_version) is not int or row.version != expected_version or row.sha256 != sha256:
        raise DomainError("ATTACHMENT_VERSION_CHANGED", "图片版本或指纹变化，请重新获取并逐字段核对", 409)
    if row.state not in {"EXTRACTED", "REVIEWED"} or not row.extraction_json:
        raise DomainError("ATTACHMENT_NOT_REVIEWABLE", "请等待提取完成后本人逐字段核对", 409)
    if user_confirmed is not True or not isinstance(selected_fields, list) or not selected_fields \
            or len(set(selected_fields)) != len(selected_fields) or not set(selected_fields).issubset(FIELDS) \
            or not isinstance(corrected_fields, dict) or not set(corrected_fields).issubset(selected_fields):
        raise DomainError("ATTACHMENT_REVIEW_INVALID", "请显式认可选中的白名单字段；修正只允许选中字段，不接受操作指令", 422)
    values = {**row.extraction_json["fields"], **corrected_fields}
    try:
        values = ObservedFields.model_validate(values).model_dump()
    except (ValueError, TypeError):
        raise DomainError("ATTACHMENT_REVIEW_INVALID", "核对字段的金额、日期或长度不合法", 422) from None
    chosen = {key: redact_document_text(values[key]) if isinstance(values[key], str) else values[key] for key in selected_fields}
    if any(value is None or value == "" for value in chosen.values()):
        raise DomainError("ATTACHMENT_REVIEW_MISSING", "未读清的字段不能认可为已核对事实，请修正或取消勾选", 422)
    row.version += 1
    row.state, row.updated_at = "REVIEWED", utcnow()
    review = AttachmentReview(attachment_id=row.id, sandbox_id=row.sandbox_id, user_id=row.user_id,
        attachment_version=row.version, sha256=row.sha256, selected_fields_json=selected_fields,
        fields_json=chosen, corrections_json={k: chosen[k] for k in corrected_fields},
        evidence_hash=canonical_hash({"attachment_id": row.id, "version": row.version, "sha256": row.sha256, "fields": chosen}))
    session.add(review); session.flush()
    row.latest_review_id = review.id
    _event(session, row, "USER_REVIEWED", {"review_id": review.id, "version": row.version, "sha256": row.sha256,
        "fields": selected_fields, "evidence_hash": review.evidence_hash, "source_kind": review.source_kind})
    return row, review


def delete_attachment(session, principal, identifier, expected_version, *, root=None):
    row = owned_attachment(session, principal, identifier, lock=True, allow_deleted=True)
    if row.version != expected_version:
        raise DomainError("ATTACHMENT_VERSION_CHANGED", "附件版本变化，请刷新后再删除", 409)
    if row.state == "DELETED":
        return row
    row.version += 1
    row.state, row.deleted_at, row.updated_at = "DELETED", utcnow(), utcnow()
    row.extraction_json = None
    job = session.scalar(select(AttachmentJob).where(AttachmentJob.attachment_id == row.id).with_for_update())
    if job:
        job.status, job.completed_at = "CANCELLED", utcnow()
    _event(session, row, "DELETED", {"version": row.version, "sha256": row.sha256})
    path = _blob_path(row, root)
    session.info.setdefault("private_attachment_delete_files", []).append(path)
    return row


def attachment_evidence_for_conversation(session, principal, review_id, expected_version, sha256):
    require_sandbox_owner(session, principal)
    review = session.scalar(select(AttachmentReview).where(AttachmentReview.id == review_id,
        AttachmentReview.sandbox_id == principal.sandbox_id, AttachmentReview.user_id == principal.user_id))
    if review is None:
        raise DomainError("ATTACHMENT_REVIEW_NOT_FOUND", "本人核对证据不存在", 404)
    row = owned_attachment(session, principal, review.attachment_id)
    if row.state != "REVIEWED" or as_utc(row.expires_at) <= utcnow() or row.latest_review_id != review.id \
            or row.version != expected_version or review.attachment_version != expected_version \
            or row.sha256 != sha256 or review.sha256 != sha256 \
            or review.evidence_hash != canonical_hash({"attachment_id": row.id, "version": row.version, "sha256": row.sha256, "fields": review.fields_json}):
        raise DomainError("ATTACHMENT_REVIEW_CHANGED", "证据已删除、过期或被新的本人核对替代，不能使用旧引用", 409)
    if not attachment_audit_valid(session, row):
        raise DomainError("ATTACHMENT_AUDIT_CHANGED", "文档证据的审计链未通过校验", 409)
    try:
        current_bytes = _blob_path(row).read_bytes()
    except OSError:
        raise DomainError("ATTACHMENT_HASH_CHANGED", "原私有图片已不可用，请重新核对", 409) from None
    if hashlib.sha256(current_bytes).hexdigest() != row.sha256:
        raise DomainError("ATTACHMENT_HASH_CHANGED", "原私有图片指纹变化，不能沿用旧核对证据", 409)
    return {**review_public(review), "attachment_id": row.id, "purpose": row.purpose, "evidence_hash": review.evidence_hash,
        "tainted": True, "bank_authoritative": False, "can_authorize_financial_action": False,
        "notice": "本人核对的是文档字段；任何银行操作仍需明确请求和原工作流确认。"}


def process_attachment_job(factory, gateway=None, storage_root=None, worker_id=None):
    """Caller schedules this bounded job; no live DB transaction spans cloud I/O."""
    gateway = gateway or DeepSeekVisionGateway()
    identity, now = worker_id or "vision-"+new_id(), utcnow()
    with factory.begin() as session:
        candidate = session.scalar(select(AttachmentJob).where(or_(
            (AttachmentJob.status == "QUEUED") & (AttachmentJob.next_run_at <= now),
            (AttachmentJob.status == "LEASED") & (AttachmentJob.lease_until < now)))
            .order_by(AttachmentJob.created_at, AttachmentJob.id).limit(1))
        if candidate is None:
            return False
        row = session.scalar(select(Attachment).where(Attachment.id == candidate.attachment_id)
                             .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if row is None:
            return False
        job = session.scalar(select(AttachmentJob).where(AttachmentJob.id == candidate.id)
                             .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if job is None or not (job.status == "QUEUED" and as_utc(job.next_run_at) <= now
                or job.status == "LEASED" and job.lease_until and as_utc(job.lease_until) < now):
            return False
        if row.state == "DELETED" or as_utc(row.expires_at) <= now or job.attempts >= 2:
            job.status, job.completed_at = "CANCELLED" if row.state == "DELETED" else "FAILED", now
            if row.state != "DELETED":
                row.state, row.error_code, row.version = "EXPIRED" if as_utc(row.expires_at) <= now else "FAILED", "VISION_BUDGET_EXHAUSTED", row.version+1
            return True
        try:
            require_sandbox_owner(session, Principal(row.user_id, row.sandbox_id))
        except DomainError:
            row.state, row.error_code, row.version = "FAILED", "ATTACHMENT_ACTOR_UNAVAILABLE", row.version+1
            job.status, job.completed_at = "FAILED", now
            return True
        job.status, job.lease_owner, job.lease_until = "LEASED", identity, now+timedelta(seconds=180)
        job.attempts += 1
        row.state, row.updated_at, row.version = "PROCESSING", now, row.version+1
        aid, jid, version, sha, mime, purpose = row.id, job.id, row.version, row.sha256, row.mime_type, row.purpose
        path = _blob_path(row, storage_root)
        _event(session, row, "PROCESSING_STARTED", {"version": version, "attempt": job.attempts, "provider": "deepseek"})
    result, error, model_observation = None, None, None
    try:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise DomainError("ATTACHMENT_HASH_CHANGED", "附件指纹不一致，未向云端发送", 409)
        with factory() as session:
            live = session.get(Attachment, aid)
            if live.state != "PROCESSING" or live.version != version or live.sha256 != sha or as_utc(live.expires_at) <= utcnow():
                return True
        result = gateway.extract(data, mime, purpose)  # Outside every DB transaction.
        if isinstance(result, dict):
            result = dict(result)
            model_observation = safe_vision_model_observation(result.pop("_model_observation", None))
    except OSError:
        error = "ATTACHMENT_BLOB_MISSING"
    except DomainError as failure:
        error = failure.code
        model_observation = safe_vision_model_observation(getattr(failure, "_model_observation", None))
    except Exception:
        error = "VISION_UNEXPECTED_ERROR"  # No exception/payload text is retained.
    with factory.begin() as session:
        row = session.scalar(select(Attachment).where(Attachment.id == aid).with_for_update().execution_options(populate_existing=True))
        job = session.scalar(select(AttachmentJob).where(AttachmentJob.id == jid).with_for_update().execution_options(populate_existing=True))
        if job.status != "LEASED" or job.lease_owner != identity or row.state != "PROCESSING" or row.version != version or row.sha256 != sha:
            return True
        prior_result = job.result_json if isinstance(job.result_json, dict) else {}
        previous = prior_result.get("model_observations") if isinstance(prior_result.get("model_observations"), list) else []
        observations = [safe for value in previous[-2:] if (safe := safe_vision_model_observation(value)) is not None]
        if model_observation is None and error in {"ATTACHMENT_BLOB_MISSING", "ATTACHMENT_HASH_CHANGED"}:
            model_observation = safe_vision_model_observation({"schema_version": "vision-model-observation-v1",
                "purpose": "VISION_EXTRACTION", "source": "WORKER_PRE_HTTP_V1", "provider": "deepseek",
                "http_issued": False, "model_api_call_count": 0, "error_code": error})
        row.version += 1; row.updated_at = utcnow()
        if as_utc(row.expires_at) <= utcnow():
            row.state, row.error_code, job.status = "EXPIRED", "ATTACHMENT_EXPIRED", "FAILED"
        elif error:
            job.last_error, row.error_code = error, error
            retry = error == "VISION_CLOUD_TRANSIENT" and job.attempts < 2
            row.state, job.status = ("QUEUED", "QUEUED") if retry else ("FAILED", "FAILED")
            job.next_run_at = utcnow()+timedelta(seconds=2)
        else:
            row.state, row.error_code, row.extraction_json = "EXTRACTED", None, result
            job.status = "SUCCEEDED"
            job.result_json = {**prior_result, "attachment_id": aid, "version": row.version, "sha256": sha,
                "provider": result["provider"], "model": result["model"], "usage": result.get("usage"),
                "http_attempts": job.attempts if result["provider"] == "deepseek" else 0}
            row.extraction_json = {**result, "model_http_attempts": job.result_json["http_attempts"]}
        if model_observation is not None:
            observations.append({**model_observation, "result_adopted": job.status == "SUCCEEDED"})
        job.result_json = {**(job.result_json if isinstance(job.result_json, dict) else {}),
            "model_observations": observations[-2:],
            "historical_attempts_without_http_evidence": max(0, job.attempts - len(observations))}
        if job.status in {"SUCCEEDED", "FAILED"}:
            job.completed_at = utcnow()
        _event(session, row, "EXTRACTION_FINISHED", {"version": row.version, "status": row.state,
            "sha256": sha, "error_code": row.error_code, "provider": "deepseek", "attempts": job.attempts})
    return True
