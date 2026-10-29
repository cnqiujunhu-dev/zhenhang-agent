"""Own currency-separated data and public reference rates; no money patch route."""
from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy import select

from .fx_banking import _login, fx_operation_integrity, inspect_fx, quote_public
from .fx_models import DualCurrencyProfile, FxOperation, FxQuote, FxStatement, FxWallet
from .fx_rates import get_current_market_rate, public_rate
from .models import Account, Card
from .simulation import as_utc


def _owned_statements(session, principal, limit):
    _login(session, principal.sandbox_id, principal.user_id)
    return list(session.scalars(select(FxStatement).where(FxStatement.sandbox_id == principal.sandbox_id,
        FxStatement.user_id == principal.user_id).order_by(FxStatement.occurred_at.desc(), FxStatement.id).limit(limit)))


def _public_quote(quote):
    data = quote_public(quote)
    result = {key: data[key] for key in ("quote_id", "rate", "base_currency", "quote_currency", "market_date", "provider", "source_url",
        "fetched_at", "expires_at", "requested_currency", "requested_amount_minor", "source_currency", "target_currency",
        "debit_minor", "credit_minor", "fee_minor", "fee_source", "reference_only", "simulated_execution_price", "settlement_notice")}
    # Only newly explicit selectors gain this receipt identity. Do not rewrite
    # the public shape of historical quotes, or expose the sender's private
    # alias/declared context, account IDs, or either party's balances.
    if quote.scope_json.get("declared_recipient_context"):
        result.update({key: data[key] for key in ("recipient_username", "recipient_card_number", "recipient_card_display_last4")})
    return result


def own_fx_transfer_receipts(session, principal, limit=50):
    rows = _owned_statements(session, principal, limit)
    result = []
    for row in rows:
        op = session.get(FxOperation, row.operation_id)
        if op.kind != "TRANSFER":
            continue
        quote = session.get(FxQuote, op.quote_id)
        incoming = row.amount_minor > 0
        counterpart = quote.scope_json["source"]["username"] if incoming else quote.scope_json["target"]["username"]
        result.append({"transfer_id": "fx-" + op.id, "direction": row.direction, "amount_minor": abs(row.amount_minor),
            "currency": row.currency, "requested_amount_minor": op.requested_amount_minor, "requested_currency": op.requested_currency,
            "transfer_channel": "INTERNAL_BANK_FX", "status": op.status, "counterparty": counterpart,
            "created_at": as_utc(op.created_at).isoformat(), "settlement_verified": fx_operation_integrity(session, op)["balanced"],
            "fx_quote": _public_quote(quote), "memo": quote.scope_json.get("memo", "")})
    return result


def own_fx_history(session, principal, limit=100):
    rows = _owned_statements(session, principal, limit)
    operations = []
    for op_id in dict.fromkeys(row.operation_id for row in rows):
        op = session.get(FxOperation, op_id)
        quote = session.get(FxQuote, op.quote_id) if op.quote_id else None
        operations.append({"operation_id": op.id, "kind": op.kind, "requested_currency": op.requested_currency,
            "requested_amount_minor": op.requested_amount_minor, "source_currency": op.source_currency, "target_currency": op.target_currency,
            "debit_minor": op.debit_minor, "credit_minor": op.credit_minor, "quote_id": op.quote_id,
            "recipient_username": op.recipient_username, "fee_minor": 0, "fee_source": "SIM_POLICY", "status": op.status,
            "created_at": as_utc(op.created_at).isoformat(), "fx_quote": _public_quote(quote) if quote else None,
            "settlement_verified": fx_operation_integrity(session, op)["balanced"],
            "funding_source": op.receipt_json.get("funding_source") if op.kind == "INITIAL_FUNDING" else None})
    return {"scope": "OWN_FX_STATEMENTS", "synthetic": True, "operations": operations,
            "statements": [{"statement_id": row.id, "operation_id": row.operation_id, "kind": session.get(FxOperation, row.operation_id).kind,
                "account_ref": row.account_ref, "currency": row.currency, "amount_minor": row.amount_minor, "direction": row.direction,
                "merchant": row.merchant, "occurred_at": as_utc(row.occurred_at).isoformat(), "operation_ref": row.operation_ref} for row in rows],
            "counterparty_balances_disclosed": False, "counterparty_tasks_disclosed": False}


def own_fx_wallets(session, principal):
    _login(session, principal.sandbox_id, principal.user_id)
    inspection = inspect_fx(session, principal.sandbox_id, principal.user_id)
    totals = {row["wallet_id"]: row for row in inspection["balances"]}
    wallets = []
    for row in session.scalars(select(FxWallet).where(FxWallet.sandbox_id == principal.sandbox_id, FxWallet.user_id == principal.user_id)):
        profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.usd_wallet_id == row.id, DualCurrencyProfile.user_id == principal.user_id))
        card = session.get(Card, profile.card_id) if profile else None
        wallets.append({"wallet_id": row.id, "currency": "USD", "balance_minor": row.balance_minor, "version": row.version,
            "status": row.status, "card_id": card.id if card else None, "card_last4": card.last4 if card else None,
            "card_status": card.status if card else None, "profile_version": profile.version if profile else None,
            "ledger_balance_minor": totals[row.id]["ledger_minor"], "balanced": totals[row.id]["balanced"]})
    return {"synthetic": True, "wallets": wallets,
            "cny_accounts": [{"account_id": row.id, "currency": "CNY", "balance_minor": row.balance_minor, "version": row.version}
                for row in session.scalars(select(Account).where(Account.sandbox_id == principal.sandbox_id, Account.user_id == principal.user_id))],
            "fees": {"fee_minor": 0, "source": "SIM_POLICY"}, "currency_balances_are_not_summed": True,
            "reconciliation": {key: inspection[key] for key in ("balanced", "balances", "per_currency_net_minor", "mismatches")}}


def install_fx_routes(app, get_session, get_banking_principal):
    @app.get("/api/v2/fx/wallets")
    def wallets(session=Depends(get_session), principal=Depends(get_banking_principal)):
        return own_fx_wallets(session, principal)

    @app.get("/api/v2/fx/rate")
    def rate(session=Depends(get_session), principal=Depends(get_banking_principal)):
        _login(session, principal.sandbox_id, principal.user_id)
        # Authentication read dependency has opened a SQLAlchemy transaction.
        # Explicitly finish that read before sending the currency-only HTTPS GET.
        session.commit()
        return public_rate(get_current_market_rate())

    @app.get("/api/v2/fx/history")
    def history(limit: int = Query(default=100, ge=1, le=200), session=Depends(get_session), principal=Depends(get_banking_principal)):
        return own_fx_history(session, principal, limit)
