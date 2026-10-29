"""Read-only customer API. All customer writes use existing task confirmation."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, StrictBool, Field

from .credit_billing import statement_view
from .credit_service import credit_view
from .customer_presentation import customer_business_value


class CreditReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    adult_eligible: StrictBool = False
    operation_key: str = Field(min_length=1, max_length=120)


def install_credit_routes(application, factory, principal_dependency):
    @application.get("/api/v2/credit/cards")
    def read_credit_cards(card_reference: str | None = None, principal=Depends(principal_dependency)):
        with factory() as session:
            return customer_business_value(credit_view(session, principal, card_reference))

    @application.get("/api/v2/credit/statements")
    def read_credit_statements(card_reference: str | None = None, currency: str | None = None,
            principal=Depends(principal_dependency)):
        with factory() as session:
            return customer_business_value(statement_view(session, principal, card_reference, currency=currency))

    @application.get("/api/v2/admin/credit/applications")
    def list_credit_applications(principal=Depends(principal_dependency)):
        from .credit_lifecycle import list_credit_applications
        with factory() as session:
            return list_credit_applications(session, principal)

    @application.post("/api/v2/admin/credit/applications/{application_id}/decision")
    def decide_application(application_id: str, request: CreditReviewRequest, principal=Depends(principal_dependency)):
        from .credit_lifecycle import decide_credit_application
        with factory.begin() as session:
            return decide_credit_application(session, principal, application_id, **request.model_dump())
