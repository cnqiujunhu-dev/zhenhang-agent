"""Explicit independent v3 form setup for disposable suitability-positive tests.

Never grant validity by editing RiskProfile or fabricating an assessment record.
The synthetic customer's eight choices produce R2, sufficient for the R1 cash
management product, without granting the highest risk level to every fixture.
"""
from fastapi.testclient import TestClient

from zhenxing.wealth import QUESTIONNAIRE_VERSION, risk_assessment_questions, submit_risk_assessment


ANSWERS = {
    "investment_horizon": 1,
    "liquidity": 2,
    "experience": 1,
    "loss_tolerance": 1,
    "income_stability": 2,
    "asset_buffer": 2,
    "liability_burden": 2,
    "repayment_pressure": 3,
}


def _assert_saved(result):
    assert result["valid"] is True and result["risk_level"] == 2, result
    assert result["questionnaire_version"] == QUESTIONNAIRE_VERSION
    assert result["assessment_source"] == "INDEPENDENT_USER_FORM"
    assert result["assessment_id"] and result["profile_id"]
    return result


def submit_risk_form(http, headers):
    """Use the actual owned form API, exclusively through an in-process client."""
    assert isinstance(http, TestClient), "Suitability fixture must not use a real service"
    questions = http.get("/api/v2/wealth/risk-assessment", headers=headers)
    assert questions.status_code == 200
    form = questions.json()
    assert {row["question_id"] for row in form["questions"]} == set(ANSWERS)
    result = http.post("/api/v2/wealth/risk-assessment", headers=headers, json={
        "questionnaire_version": form["questionnaire_version"],
        "answers": dict(ANSWERS), "independent_confirmation": True,
    })
    assert result.status_code == 200, result.text
    return _assert_saved(result.json())


def submit_risk_form_in_session(session, sandbox_id, user_id):
    """Same form boundary for domain-only SQLite fixtures; never commit here."""
    assert session.get_bind().dialect.name == "sqlite", "Isolated SQLite fixture required"
    form = risk_assessment_questions(session, sandbox_id, user_id)
    assert {row["question_id"] for row in form["questions"]} == set(ANSWERS)
    return _assert_saved(submit_risk_assessment(session, sandbox_id, user_id,
        answers=dict(ANSWERS), questionnaire_version=form["questionnaire_version"],
        independent_confirmation=True))
