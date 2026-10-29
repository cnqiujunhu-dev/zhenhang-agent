from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import DomainError, StrictModel
from .family_account_context import family_account_contexts, issue_family_account_context
from .extensions import (
    carbon_view, create_family_helper, evaluate_transfer_risk, family_view,
    goals_view, owner_review_family_proposal,
)
from .models import Payee


class HelperRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=40)


class OwnerReviewRequest(StrictModel):
    decision: Literal["APPROVE", "REJECT"]
    expected_proposal_version: int = Field(ge=1)


class FamilyContextRequest(StrictModel):
    expected_link_version: int = Field(ge=1)


def install_extension_routes(app, get_session, get_principal):
    @app.get("/api/v2/extensions/fraud")
    def fraud(payee_name: str | None = Query(default=None, max_length=80),
              message: str = Query(default="", max_length=1000),
              session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        result = evaluate_transfer_risk(session, context={"sandbox_id": principal.sandbox_id,
            "user_id": principal.user_id, "payee_name": payee_name, "message": message})
        contacts = [{"payee_name": row.display_name, "account_last4": row.account_last4,
                     "status": row.status} for row in session.scalars(select(Payee).where(
            Payee.sandbox_id == principal.sandbox_id, Payee.user_id == principal.user_id))]
        return {"risk": result, "contacts": contacts, "synthetic": True}

    @app.get("/api/v2/extensions/family")
    def family(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return family_view(session, principal.sandbox_id, principal.user_id)

    @app.get("/api/v2/extensions/family/contexts")
    def family_contexts(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return family_account_contexts(session, principal)

    @app.post("/api/v2/extensions/family/contexts/{link_id}/session")
    def family_context_session(link_id: str, payload: FamilyContextRequest,
                               session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        try:
            result = issue_family_account_context(session, principal, link_id=link_id,
                                                  expected_link_version=payload.expected_link_version)
        except DomainError as exc:
            # A revoked parent/link is not a revoked child's main bearer session.
            if exc.code == "FAMILY_CONTEXT_INVALID":
                raise DomainError(exc.code, exc.message, 403) from exc
            raise
        session.commit()
        return result

    @app.post("/api/v2/extensions/family/helpers")
    def helper(payload: HelperRequest, session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        result = create_family_helper(session, principal, payload.display_name)
        session.commit()
        return result

    @app.post("/api/v2/extensions/family/proposals/{proposal_id}/owner-review", status_code=202)
    def owner_review(proposal_id: str, payload: OwnerReviewRequest,
                     session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        result = owner_review_family_proposal(session, principal, proposal_id, payload.decision, payload.expected_proposal_version)
        session.commit()
        return result

    @app.get("/api/v2/extensions/goals")
    def goals(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return goals_view(session, principal.sandbox_id, principal.user_id)

    @app.get("/api/v2/extensions/carbon")
    def carbon(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return carbon_view(session, principal.sandbox_id, principal.user_id)
