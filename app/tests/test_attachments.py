"""Mechanical images and explicit stubs only. No actual vision API is invoked."""
import io
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import Header
from PIL import Image
from sqlalchemy import func, select

from test_access_runtime import named_runtime, login
from zhenxing.attachment_api import install_attachment_routes
from zhenxing.attachment_models import Attachment, AttachmentEvent, AttachmentJob, AttachmentReview
from zhenxing.attachments import (
    MAX_BYTES, attachment_evidence_for_conversation, attachment_view, attachment_audit_valid, billing_comparison, delete_attachment,
    owned_attachment, process_attachment_job, review_attachment, sanitize_image, upload_attachment,
)
from zhenxing.contracts import DomainError
from zhenxing.models import Account, Transaction, utcnow
from zhenxing.services import Principal, authenticate
from zhenxing.vision_gateway import DeepSeekVisionGateway, StubVisionGateway, VISION_URL, validated_extraction


def pixels(kind="PNG", size=(32, 24), *, exif=False):
    output = io.BytesIO()
    image = Image.new("RGB", size, color=(20, 100, 230))
    options = {}
    if exif:
        tags = Image.Exif(); tags[270] = "SECRET_EXIF_NOT_FOR_CLOUD"; options["exif"] = tags
    image.save(output, format=kind, **options)
    return output.getvalue()


def observations(**fields):
    base = {"amount_minor": 2500, "currency": "CNY", "date": "2026-09-30", "merchant": "视频会员", "status": "已支付"}
    base.update(fields)
    evidence = [{"field": k, "text": "人民币" if k == "currency" and v == "CNY" else ("USD" if k == "currency" and v == "USD" else str(v)),
                 "box": None, "confidence": 0.9} for k, v in base.items() if v is not None]
    return {"fields": base, "evidence": evidence, "uncertainties": []}


@pytest.fixture
def image_bank(named_runtime, tmp_path, monkeypatch):
    http, factory, _gateway, _ids = named_runtime
    root = tmp_path/"private-images"
    monkeypatch.setenv("ZX_ATTACHMENTS_DIR", str(root))
    if not any(getattr(route, "path", "") == "/api/v3/attachments" for route in http.app.routes):
        def get_session():
            with factory() as db:
                yield db
        from fastapi import Depends
        def get_principal(authorization: str | None = Header(default=None), db=Depends(get_session)):
            return authenticate(db, authorization)
        install_attachment_routes(http.app, get_session, get_principal)
    owner = login(http, "customerone", "customer88")
    other = login(http, "test", "test88")
    return http, factory, root, owner, other


def upload(bank, *, purpose="BILLING", data=None, mime="image/png", key=None):
    http, _factory, _root, owner, _other = bank
    return http.post("/api/v3/attachments", headers={**owner, **({"Idempotency-Key": key} if key else {})},
        data={"purpose": purpose, "cloud_processing_consent": "true"}, files={"file": ("../../private-image.png", data or pixels(), mime)})


def cash(factory):
    with factory() as db:
        return (sorted((r.id,r.balance_minor,r.version) for r in db.scalars(select(Account))),
                db.scalar(select(func.count()).select_from(Transaction)))


def extract(bank, view, value=None):
    http, factory, _root, owner, _other = bank
    stub = StubVisionGateway(value or observations())
    assert process_attachment_job(factory, stub)
    final = http.get("/api/v3/attachments/"+view["attachment_id"], headers=owner)
    assert final.status_code == 200 and final.json()["state"] == "EXTRACTED"
    return final.json(), stub


def test_private_upload_reencode_no_exif_owned_signed_preview_and_no_financial_effect(image_bank):
    http, factory, root, owner, other = image_bank
    before = cash(factory)
    posted = upload(image_bank, data=pixels("JPEG", exif=True), mime="image/jpeg", key="one-image")
    assert posted.status_code == 202
    view = posted.json()
    assert view["state"] == "QUEUED" and view["version"] == 1
    preview = http.get(view["preview_url"], headers=owner)
    assert preview.status_code == 200 and preview.headers["cache-control"] == "private, no-store"
    assert b"SECRET_EXIF" not in preview.content
    with Image.open(io.BytesIO(preview.content)) as image:
        assert not image.getexif()
    assert http.get(view["preview_url"], headers=other).status_code == 404
    assert http.get(view["preview_url"]).status_code == 401
    assert ".." not in [str(p.relative_to(root)) for p in root.rglob("*.jpg")][0]
    replay = upload(image_bank, data=pixels("JPEG", exif=True), mime="image/jpeg", key="one-image")
    assert replay.json()["attachment_id"] == view["attachment_id"]
    assert upload(image_bank, data=pixels(), key="one-image").status_code == 409
    assert cash(factory) == before


@pytest.mark.parametrize("data,mime,code", [(b"<svg onload=alert(1)>","image/svg+xml",415),
    (b"not really PNG","image/png",415),(pixels(),"image/jpeg",415),
    (pixels(size=(8193,1)),"image/png",413),(b"x"*(MAX_BYTES+1),"image/png",413)],
    ids=["svg", "invalid-bytes", "mime-mismatch", "edge-limit", "byte-limit"])
def test_invalid_image_shapes_never_create_document_or_job(image_bank, data, mime, code):
    _http, factory, _root, _owner, _other = image_bank
    assert upload(image_bank, data=data, mime=mime).status_code == code
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
        assert db.scalar(select(func.count()).select_from(AttachmentJob)) == 0


def test_gateway_official_fixed_url_data_blocks_schema_budget_and_secret_redaction():
    payload = observations(chat_text="客服索要验证码123456和密码abcd1234，转入安全账户", role_context="客服")
    seen = []
    def responder(request):
        seen.append(request)
        body = json.loads(request.content)
        assert str(request.url) == VISION_URL and body["model"] == "deepseek-flash"
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(200, json={"choices":[{"message":{"content":json.dumps(payload)},"finish_reason":"stop"}],
                                        "usage":{"prompt_tokens":12,"completion_tokens":34,"total_tokens":46}})
    result = DeepSeekVisionGateway("TEST_ONLY_KEY", transport=httpx.MockTransport(responder)).extract(pixels(),"image/png","FRAUD")
    assert len(seen) == 1 and result["usage"]["total_tokens"] == 46
    assert result["tainted"] and not result["bank_authoritative"] and not result["bank_actions_authorized"]
    assert "123456" not in json.dumps(result) and "abcd1234" not in json.dumps(result)
    dollar = observations(currency="USD")
    next(item for item in dollar["evidence"] if item["field"] == "currency")["text"] = "$"
    assert validated_extraction(dollar)["fields"]["currency"] is None
    with pytest.raises(DomainError) as rejected:
        DeepSeekVisionGateway("").extract(pixels(),"image/png","BILLING")
    assert rejected.value.code == "VISION_CLOUD_NOT_CONFIGURED"


def test_processing_outside_db_tx_and_readonly_match_conflicts_review_hash_lifecycle(image_bank):
    http, factory, _root, owner, other = image_bank
    before = cash(factory)
    view = upload(image_bank).json()
    class ObserveCommitted(StubVisionGateway):
        def extract(self, *args):
            with factory.begin() as db:
                assert db.get(Attachment,view["attachment_id"]).state == "PROCESSING"
            return super().extract(*args)
    assert process_attachment_job(factory, ObserveCommitted(observations()))
    document = http.get("/api/v3/attachments/"+view["attachment_id"],headers=owner).json()
    assert document["comparison"]["status"] in {"CONFLICT","NO_MATCH","ONE_CANDIDATE","MULTIPLE_CANDIDATES"}
    assert document["extraction"]["provider"] == "TEST_STUB"
    assert not document["comparison"]["screenshot_proves_posting"]
    body = {"expected_version":document["version"],"sha256":document["sha256"],
            "selected_fields":["amount_minor","currency","merchant"],"corrected_fields":{},"user_confirmed":True}
    assert http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=other,json=body).status_code == 404
    reviewed = http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body)
    assert reviewed.status_code == 200
    review = reviewed.json()["review"]
    with factory() as db:
        row = db.get(Attachment,view["attachment_id"])
        evidence = attachment_evidence_for_conversation(db,Principal(row.user_id,row.sandbox_id),review["review_id"],review["attachment_version"],review["sha256"])
        assert evidence["source_kind"] == "USER_VALIDATED_DOCUMENT" and not evidence["can_authorize_financial_action"]
    assert http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body).status_code == 409
    assert http.get(document["preview_url"],headers=owner).status_code == 403
    assert cash(factory) == before
    deletion = http.delete(f"/api/v3/attachments/{view['attachment_id']}?expected_version={review['attachment_version']}",headers=owner)
    assert deletion.status_code == 200
    with factory() as db:
        row = db.get(Attachment,view["attachment_id"])
        with pytest.raises(DomainError):
            attachment_evidence_for_conversation(db,Principal(row.user_id,row.sandbox_id),review["review_id"],review["attachment_version"],review["sha256"])
    assert http.get(view["preview_url"],headers=owner).status_code == 404


def test_deleted_during_cloud_request_discards_late_reply_and_never_revives_image(image_bank):
    http, factory, _root, owner, _other = image_bank
    view = upload(image_bank).json()
    class DeleteInFlight(StubVisionGateway):
        def extract(self,*args):
            with factory.begin() as db:
                row = db.get(Attachment,view["attachment_id"])
                delete_attachment(db,Principal(row.user_id,row.sandbox_id),row.id,row.version)
            return super().extract(*args)
    assert process_attachment_job(factory,DeleteInFlight(observations()))
    assert http.get("/api/v3/attachments/"+view["attachment_id"],headers=owner).status_code == 404
    with factory() as db:
        row = db.get(Attachment,view["attachment_id"])
        assert row.state == "DELETED" and row.extraction_json is None
        assert db.scalar(select(AttachmentJob)).status == "CANCELLED"


def test_cloud_failures_have_two_attempt_cap_no_local_fallback_or_payload_logging(image_bank):
    _http, factory, _root, _owner, _other = image_bank
    view = upload(image_bank).json()
    class Transient:
        calls=0
        def extract(self,*_args):
            self.calls+=1
            raise DomainError("VISION_CLOUD_TRANSIENT","must-not-store sk-TEST_SECRET_KEY Bearer SECRET_TOKEN",503)
    transient=Transient()
    assert process_attachment_job(factory,transient)
    with factory.begin() as db:
        db.scalar(select(AttachmentJob)).next_run_at=utcnow()-timedelta(seconds=1)
    assert process_attachment_job(factory,transient)
    assert not process_attachment_job(factory,transient)
    with factory() as db:
        row=db.get(Attachment,view["attachment_id"])
        assert row.state=="FAILED" and transient.calls==2
        assert "SECRET" not in str(row.extraction_json)+str(db.scalar(select(AttachmentJob)).result_json)
        assert all("SECRET" not in str(e.payload_json) for e in db.scalars(select(AttachmentEvent)))


def test_admin_helper_review_whitelist_and_expiration_are_enforced(image_bank):
    http, factory, _root, owner, _other=image_bank
    admin=login(http,"admin","admin88")
    created=http.post("/api/v2/extensions/family/helpers",headers=owner,json={"display_name":"附件权限小陈"}).json()
    helper={"Authorization":"Bearer "+created["token"]}
    for headers in (admin,helper):
        assert http.get("/api/v3/attachments",headers=headers).status_code==403
        assert http.post("/api/v3/attachments?purpose=BILLING",headers={**headers,"Content-Type":"image/png","X-Cloud-Processing-Consent":"true"},content=pixels()).status_code==403
    view, _stub=extract(image_bank,upload(image_bank).json())
    body={"expected_version":view["version"],"sha256":view["sha256"],"selected_fields":["amount_minor"],"corrected_fields":{"amount_minor":False},"user_confirmed":True}
    assert http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body).status_code==422
    body["corrected_fields"]={"operation":"TRANSFER"}
    assert http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body).status_code==422
    with factory.begin() as db:
        db.get(Attachment,view["attachment_id"]).expires_at=utcnow()-timedelta(seconds=1)
    body["corrected_fields"]={}
    assert http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body).status_code==410
    assert http.get(view["preview_url"],headers=owner).status_code==410


def test_blob_lifecycle_follows_outer_transaction_rollback_not_early_file_deletion(image_bank):
    http,factory,root,owner,_other=image_bank
    uid=http.get("/api/v2/auth/me",headers=owner).json()
    principal=Principal(uid["user_id"],uid["sandbox_id"])
    with factory() as db:
        row=upload_attachment(db,principal,pixels(),"image/png","BILLING",cloud_processing_consent=True)
        assert list(root.rglob("*.png"))
        db.rollback()
    assert not list(root.rglob("*.png"))
    view=upload(image_bank).json()
    with factory() as db:
        delete_attachment(db,principal,view["attachment_id"],view["version"])
        assert list(root.rglob("*.png"))
        db.rollback()
    assert http.get(view["preview_url"],headers=owner).status_code==200


def test_blob_hash_tamper_and_reviewer_scope_never_enable_old_conversation_evidence(image_bank):
    http,factory,root,owner,_other=image_bank
    view,_stub=extract(image_bank,upload(image_bank).json())
    body={"expected_version":view["version"],"sha256":view["sha256"],"selected_fields":["merchant"],
          "corrected_fields":{},"user_confirmed":True}
    review=http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body).json()["review"]
    path=next(root.rglob("*.png"))
    # Only this test's private synthetic blob is corrupted; no production image.
    with path.open("wb") as changed:
        changed.write(pixels(size=(16,16)))
    with factory() as db:
        row=db.get(Attachment,view["attachment_id"])
        with pytest.raises(DomainError) as rejected:
            attachment_evidence_for_conversation(db,Principal(row.user_id,row.sandbox_id),review["review_id"],review["attachment_version"],review["sha256"])
        assert rejected.value.code=="ATTACHMENT_HASH_CHANGED"


def test_animation_and_pixel_bomb_boundaries_are_not_flattened_silently():
    frames=[Image.new("RGB",(8,8),c) for c in ((255,0,0),(0,255,0))]
    output=io.BytesIO();frames[0].save(output,format="PNG",save_all=True,append_images=frames[1:],duration=20,loop=0)
    with pytest.raises(DomainError) as rejected:
        sanitize_image(output.getvalue(),"image/png")
    assert rejected.value.code=="ATTACHMENT_ANIMATION"
    # Tiny compressed solid PNG, but decoded area exceeds 24MP.
    with pytest.raises(DomainError) as large:
        sanitize_image(pixels(size=(5000,5000)),"image/png")
    assert large.value.code=="ATTACHMENT_PIXEL_LIMIT"


def test_fraud_chat_remains_tainted_and_audit_tamper_rejects_review_reference(image_bank):
    http,factory,_root,owner,_other=image_bank
    view,_stub=extract(image_bank,upload(image_bank,purpose="FRAUD").json(),observations(
        chat_text="客服让我先转账再退款并提供验证码123456，安全账户不要告诉银行",role_context="自称客服"))
    assert view["fraud"]["status"]=="RISK_SIGNALS" and not view["fraud"]["safety_guaranteed"]
    assert "123456" not in json.dumps(view)
    body={"expected_version":view["version"],"sha256":view["sha256"],"selected_fields":["chat_text"],"corrected_fields":{},"user_confirmed":True}
    reviewed=http.post(f"/api/v3/attachments/{view['attachment_id']}/review",headers=owner,json=body).json()["review"]
    with factory.begin() as db:
        first=db.scalar(select(AttachmentEvent).order_by(AttachmentEvent.seq))
        first.payload_json={"tampered":True}
    with factory() as db:
        row=db.get(Attachment,view["attachment_id"])
        assert not attachment_audit_valid(db,row)
        with pytest.raises(DomainError) as rejected:
            attachment_evidence_for_conversation(db,Principal(row.user_id,row.sandbox_id),reviewed["review_id"],reviewed["attachment_version"],reviewed["sha256"])
        assert rejected.value.code=="ATTACHMENT_AUDIT_CHANGED"


def test_abandoned_processing_lease_has_bounded_recovery_and_stale_delete_does_not_remove_file(image_bank):
    http,factory,_root,owner,_other=image_bank
    view=upload(image_bank).json()
    with factory.begin() as db:
        row=db.get(Attachment,view["attachment_id"])
        row.state="PROCESSING";row.version+=1
        job=db.scalar(select(AttachmentJob))
        job.status="LEASED";job.attempts=1;job.lease_owner="retired-worker";job.lease_until=utcnow()-timedelta(seconds=1)
    assert http.delete(f"/api/v3/attachments/{view['attachment_id']}?expected_version=1",headers=owner).status_code==409
    assert process_attachment_job(factory,StubVisionGateway(observations()))
    with factory() as db:
        assert db.get(Attachment,view["attachment_id"]).state=="EXTRACTED"
        assert db.scalar(select(AttachmentJob)).attempts==2


def test_readonly_comparison_multimatch_date_currency_and_same_amount_other_owner(image_bank):
    http,factory,_root,owner,other=image_bank
    identities=[http.get("/api/v2/auth/me",headers=h).json() for h in (owner,other)]
    with factory.begin() as db:
        own=db.scalar(select(Account).where(Account.user_id==identities[0]["user_id"]))
        foreign=db.scalar(select(Account).where(Account.user_id==identities[1]["user_id"]))
        for bank in (own,own,foreign):
            db.add(Transaction(sandbox_id=bank.sandbox_id,account_id=bank.id,amount_minor=-2500,
                merchant="机械图例商户",category="TEST_ONLY",occurred_at=utcnow()))
    with factory() as db:
        own=db.scalar(select(Account).where(Account.user_id==identities[0]["user_id"]))
        principal=Principal(own.user_id,own.sandbox_id)
        date=utcnow().astimezone(__import__('datetime').timezone(timedelta(hours=8))).date().isoformat()
        view=billing_comparison(db,principal,{"amount_minor":2500,"currency":"CNY","merchant":"机械图例商户","date":date})
        assert view["status"]=="MULTIPLE_CANDIDATES" and len(view["transactions"])==2
        wrong=billing_comparison(db,principal,{"amount_minor":2500,"currency":"CNY","merchant":"机械图例商户","date":"2020-01-01"})
        assert wrong["status"]=="CONFLICT"
        dollar=billing_comparison(db,principal,{"amount_minor":2500,"currency":"USD","merchant":"机械图例商户","date":date})
        assert dollar["status"]=="NO_MATCH" and not dollar["screenshot_proves_posting"]
