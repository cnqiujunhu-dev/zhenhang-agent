from __future__ import annotations

from typing import Any

from fastapi import Depends
from pydantic import Field, StrictBool, StrictInt
from sqlalchemy.orm import Session

from .contracts import StrictModel
from .wealth import (
    list_wealth_products, risk_assessment_questions,
    risk_profile_view, submit_risk_assessment,
)


class RiskAssessmentRequest(StrictModel):
    questionnaire_version: str = Field(min_length=1, max_length=40)
    answers: dict[str, StrictInt]
    independent_confirmation: StrictBool


def install_wealth_routes(app, get_session, get_principal):
    @app.get("/api/v2/wealth/products")
    def products(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return list_wealth_products(session, principal.sandbox_id, principal.user_id)

    @app.get("/api/v2/wealth/risk-profile")
    def profile(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return risk_profile_view(session, principal.sandbox_id, principal.user_id)

    @app.get("/api/v2/wealth/risk-assessment")
    def questionnaire(session: Session = Depends(get_session), principal: Any = Depends(get_principal)):
        return risk_assessment_questions(session, principal.sandbox_id, principal.user_id)

    @app.post("/api/v2/wealth/risk-assessment")
    def assess(payload: RiskAssessmentRequest, session: Session = Depends(get_session),
               principal: Any = Depends(get_principal)):
        result = submit_risk_assessment(
            session, principal.sandbox_id, principal.user_id, answers=payload.answers,
            questionnaire_version=payload.questionnaire_version,
            independent_confirmation=payload.independent_confirmation,
        )
        session.commit()
        return result
