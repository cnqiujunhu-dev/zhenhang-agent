"""Authenticated AA invitation controls; there is no direct bank-pay endpoint."""
from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from .aa_participant_payments import (cancel_collection, decline_invitation, own_invitations,
                                     prepare_participant_payment)


class AAPaymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_card_reference: str | None = Field(default=None, min_length=1, max_length=80)


class AAVersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)


def install_aa_payment_routes(app, get_session, get_principal):
    @app.get("/api/v2/transfers/aa/invitations")
    def invitations(session=Depends(get_session), principal=Depends(get_principal)):
        return {"scope": "OWN_AA_PAYMENT_INVITATIONS", "invitations": own_invitations(session, principal)}

    @app.post("/api/v2/transfers/aa/{request_id}/participants/{participant_id}/payment-task", status_code=201)
    def payment(request_id: str, participant_id: str, payload: AAPaymentInput,
                idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=120),
                session=Depends(get_session), principal=Depends(get_principal)):
        parent = prepare_participant_payment(session, principal, request_id, participant_id,
                                             payload.source_card_reference, idempotency_key)
        session.commit()
        return {"task_id": parent.id, "state": parent.state, "version": parent.version,
                "trace_url": f"/api/v2/tasks/{parent.id}/trace",
                "message": "已返回本人AA付款任务，请以任务当前状态及银行回执为准；准备请求不会替代二次验证和确认，也不会重复付款。"}

    @app.post("/api/v2/transfers/aa/{request_id}/participants/{participant_id}/decline")
    def decline(request_id: str, participant_id: str, payload: AAVersionInput,
                session=Depends(get_session), principal=Depends(get_principal)):
        result = decline_invitation(session, principal, request_id, participant_id, payload.expected_version)
        session.commit()
        return result

    @app.post("/api/v2/transfers/aa/{request_id}/cancel")
    def cancel(request_id: str, payload: AAVersionInput,
               session=Depends(get_session), principal=Depends(get_principal)):
        result = cancel_collection(session, principal, request_id, payload.expected_version)
        session.commit()
        return result
