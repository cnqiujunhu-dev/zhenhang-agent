"""Minimal recipient directory and own settlement receipts; no other user's task or balance."""
from fastapi import Depends, Query
from sqlalchemy import or_, select

from .contracts import DomainError
from .internal_banking import list_internal_recipients, settlement_integrity
from .internal_banking_models import InternalTransferSettlement
from .models import Payee, Transfer
from .permissions import require_sandbox_owner


def install_internal_banking_routes(app, get_session, get_principal):
    @app.get("/api/v2/transfers/recipients")
    def recipients(q: str | None = Query(default=None, max_length=32), limit: int = Query(default=30, ge=1, le=100),
                   session=Depends(get_session), principal=Depends(get_principal)):
        require_sandbox_owner(session, principal)
        rows = list_internal_recipients(session, principal.sandbox_id, principal.user_id, q, limit)
        from .access_models import LoginAccount
        from .fx_banking import recipientcurrencies
        for row in rows:
            login = session.scalar(select(LoginAccount).where(LoginAccount.username == row["username"]))
            row["accepted_currencies"] = recipientcurrencies(session, login)
            from .payment_instruments import resolve_recipient_instrument, recipient_instrument_view
            # The payer sees only the registered default receiving instrument,
            # never another customer's balances/limits or private card catalog.
            target = resolve_recipient_instrument(session, login.username, caller_principal=principal)
            public = recipient_instrument_view(target)
            references = [{**public, "card_number": target.get("card_number"), "accepted_currencies": ["CNY"]}]
            row["available_receiving_cards"] = references
            row["primary_card_number"] = references[0]["card_number"] if references else None
            row["receiving_card_scope"] = "REGISTERED_DEFAULT_ONLY"
        return {"registered_accounts": [{**row, "display_name": row["username"], "account_last4": row["account_reference"][-4:]} for row in rows],
                "scope": "MINIMAL_REGISTERED_ACCOUNT_DIRECTORY", "synthetic": True}

    @app.get("/api/v2/transfers/history")
    def history(limit: int = Query(default=50, ge=1, le=200), session=Depends(get_session), principal=Depends(get_principal)):
        require_sandbox_owner(session, principal)
        own_sender = (Transfer.sandbox_id == principal.sandbox_id) & (Transfer.user_id == principal.user_id)
        own_receiver = (InternalTransferSettlement.receiver_sandbox_id == principal.sandbox_id) & (InternalTransferSettlement.receiver_user_id == principal.user_id)
        rows = session.execute(select(Transfer, InternalTransferSettlement).outerjoin(InternalTransferSettlement,
            InternalTransferSettlement.transfer_id == Transfer.id).where(or_(own_sender, own_receiver))
            .order_by(Transfer.created_at.desc()).limit(limit))
        receipts = []
        for transfer, settlement in rows:
            incoming = bool(settlement and settlement.receiver_user_id == principal.user_id and settlement.receiver_sandbox_id == principal.sandbox_id)
            payee = session.get(Payee, transfer.payee_id)
            receipts.append({"transfer_id": transfer.id, "direction": "INCOMING" if incoming else "OUTGOING",
                "transfer_channel": "INTERNAL_BANK" if settlement else "EXTERNAL_SIM", "status": transfer.status,
                "amount_minor": transfer.amount_minor, "currency": "CNY", "memo": transfer.memo,
                "counterparty": settlement.sender_username if incoming else (settlement.receiver_username if settlement else payee.display_name),
                "created_at": transfer.created_at.isoformat(), "settlement_verified": settlement_integrity(session, transfer)["balanced"]})
        from .fx_api import own_fx_transfer_receipts
        from .permissions import actor_role
        if actor_role(session, principal.sandbox_id, principal.user_id) in {"CUSTOMER", "DEV_TEST"}:
            receipts.extend(own_fx_transfer_receipts(session, principal, limit=limit))
        receipts.sort(key=lambda row: row["created_at"], reverse=True)
        return {"scope": "OWN_TRANSFER_RECEIPTS", "receipts": receipts[:limit], "synthetic": True,
                "counterparty_balances_disclosed": False, "counterparty_tasks_disclosed": False}
