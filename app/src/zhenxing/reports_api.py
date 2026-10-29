from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from .reports import analyze_recurring_subscriptions, generate_bill_report


def install_report_routes(app, get_session, get_principal):
    @app.get("/api/v2/bills/report")
    def bills_report(
        period: Literal["month", "year"] = "month",
        year: int | None = Query(default=None, ge=2000, le=2100),
        month: int | None = Query(default=None, ge=1, le=12),
        card_reference: str | None = Query(default=None, min_length=4, max_length=100),
        currency: Literal['CNY', 'USD'] | None = None,
        session: Session = Depends(get_session),
        principal: Any = Depends(get_principal),
    ):
        return generate_bill_report(session, principal.sandbox_id, principal.user_id,
                                    period=period, year=year, month=month,
                                    card_reference=card_reference, currency=currency)

    @app.get("/api/v2/subscriptions/analysis")
    def subscriptions_analysis(
        session: Session = Depends(get_session),
        principal: Any = Depends(get_principal),
    ):
        return analyze_recurring_subscriptions(session, principal.sandbox_id, principal.user_id)
