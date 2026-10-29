"""Personal confirmed aliases only; model/chat has no direct mutation endpoint."""
from fastapi import Depends
from sqlalchemy import select

from .contacts import _alias_target, _owner
from .contact_models import ContactAlias


def own_aliases(session, principal):
    _owner(session, principal.sandbox_id, principal.user_id)
    rows = []
    for alias in session.scalars(select(ContactAlias).where(ContactAlias.sandbox_id == principal.sandbox_id,
            ContactAlias.user_id == principal.user_id).order_by(ContactAlias.alias_name)):
        from .contracts import DomainError
        try:
            login, _bank, card = _alias_target(session, alias)
            from .card_identifiers import card_identifier_view
            identifier = card_identifier_view(session, card) if card else None
            rows.append({"alias_id": alias.id, "alias_name": alias.alias_name, "recipient_username": login.username,
                "recipient_reference": identifier["card_number"] if identifier else login.username,
                "status": alias.status, "version": alias.version, "available": True})
        except DomainError:
            rows.append({"alias_id": alias.id, "alias_name": alias.alias_name, "status": alias.status,
                "version": alias.version, "available": False, "recipient_reference": None, "recipient_username": None})
    return {"scope": "CURRENT_USER_CONTACT_ALIASES", "aliases": rows, "synthetic": True,
        "counterparty_balances_disclosed": False, "mutations_require_confirmed_task": True}


def install_contact_routes(app, get_session, get_banking_principal):
    @app.get("/api/v2/contacts/aliases")
    def aliases(session=Depends(get_session), principal=Depends(get_banking_principal)):
        return own_aliases(session, principal)
