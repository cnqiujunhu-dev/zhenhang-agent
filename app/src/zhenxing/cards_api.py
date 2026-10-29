"""Additive customer union endpoint; the v1 debit-only API stays compatible."""
from fastapi import Depends

from .cards import owned_card_status
from .customer_presentation import customer_business_value


def install_card_routes(application, factory, principal_dependency):
    @application.get("/api/v2/cards")
    def read_owned_cards(card_reference: str | None = None, principal=Depends(principal_dependency)):
        with factory() as session:
            return customer_business_value(owned_card_status(session, principal, card_reference))
