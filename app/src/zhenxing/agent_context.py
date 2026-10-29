"""Minimal, bounded authoritative facts; none are permissions or user intent."""
from sqlalchemy import select

from .capability_catalog import catalog
from .models import Account, BudgetEnvelope, Card, Payee, RiskProfile, Subscription
from .permissions import actor_role
from .resource_defaults import ARCHIVED_RESOURCE_STATUS

CONTEXT_VERSION = "bounded-context-20261001-v3"


def planning_facts(session, principal, *, message=None, preserve_context=False) -> dict:
    from .planning_fact_projection import project_planning_facts
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    capabilities = catalog(role)
    facts = {"version": CONTEXT_VERSION, "authority": "READ_ONLY_NOT_PERMISSION", "actor_role": role,
             "available_actions": [entry["kind"] for entry in capabilities["capabilities"]],
             "capability_catalog_version": capabilities["version"]}
    if role == "FAMILY_HELPER":
        # Even a granted helper's projection must be produced by the delegation
        # API; planning never loads the owner's full transaction history.
        return project_planning_facts(facts, message, preserve_context=preserve_context)
    # Account rows are deposit cash; CreditAccount is a separate liability
    # domain. Never choose an arbitrary first debit account or add USD cash to
    # CNY. The existing foreign-wallet projection remains separate below.
    accounts = list(session.scalars(select(Account).where(Account.sandbox_id == principal.sandbox_id,
        Account.user_id == principal.user_id, Account.currency == 'CNY').order_by(Account.id)))
    if accounts:
        from .extensions import reserved_goal_cash_minor
        from .payment_instruments import cash_reserved_minor
        goal_reserve = sum(reserved_goal_cash_minor(session, principal.sandbox_id, principal.user_id, account.id) for account in accounts)
        reserve = sum(cash_reserved_minor(session, principal, account.id) for account in accounts)
        balance = sum(account.balance_minor for account in accounts)
        facts["cash"] = {"balance_minor": balance, "goal_reserved_minor": goal_reserve,
                         "spendable_minor": balance - reserve, "currency": "CNY",
                         "source_version": max(account.version for account in accounts)}
        if len(accounts)>1:
            facts['cash']['account_count']=len(accounts)
        if reserve!=goal_reserve:
            facts['cash']['reserved_cash_minor']=reserve
    from .fx_models import FxWallet, DualCurrencyProfile
    usd_wallet = session.scalar(select(FxWallet).where(FxWallet.sandbox_id == principal.sandbox_id,
        FxWallet.user_id == principal.user_id))
    profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id == principal.user_id,
        DualCurrencyProfile.sandbox_id == principal.sandbox_id))
    facts["currencies"] = {"supported": ["CNY", "USD"], "rate_source": "SERVER_TOOL_NOT_MODEL",
        "usd_wallet": {"balance_minor": usd_wallet.balance_minor, "version": usd_wallet.version,
            "status": usd_wallet.status, "currency": "USD"} if usd_wallet else None,
        "dual_card": bool(profile), "goals_currency": "CNY", "foreign_schedules_aa_family_supported": False}
    from .card_identifiers import card_identifier_view
    facts["cards"] = [{"last4": row.last4, "status": row.status, "source_version": row.version,
                        **(card_identifier_view(session, row) or {})}
                      for row in session.scalars(select(Card).where(Card.sandbox_id == principal.sandbox_id,
                            Card.user_id == principal.user_id, Card.status != ARCHIVED_RESOURCE_STATUS).limit(6))]
    facts["known_payees"] = [{"name": row.display_name, "last4": row.account_last4}
                            for row in session.scalars(select(Payee).where(Payee.sandbox_id == principal.sandbox_id,
                                Payee.user_id == principal.user_id, Payee.status == "ACTIVE").limit(20))]
    if role in {"CUSTOMER", "DEV_TEST"}:
        from .contact_api import own_aliases
        facts["personal_recipient_aliases"] = own_aliases(session, principal)["aliases"][:20]
    facts["subscriptions"] = [{"merchant": row.merchant, "source": row.source_type, "status": row.status,
                                "source_version": row.version} for row in session.scalars(select(Subscription).where(
                                    Subscription.sandbox_id == principal.sandbox_id, Subscription.user_id == principal.user_id,
                                    Subscription.status != ARCHIVED_RESOURCE_STATUS).limit(10))]
    profile = session.scalar(select(RiskProfile).where(RiskProfile.sandbox_id == principal.sandbox_id,
                                                        RiskProfile.user_id == principal.user_id))
    if profile:
        from .wealth import risk_profile_view
        risk=risk_profile_view(session,principal.sandbox_id,principal.user_id)
        facts['risk_profile']={key:risk.get(key) for key in ('risk_level','expires_at','valid','status','questionnaire_version')}
    else:facts['risk_profile']=None
    facts["protected_budgets"] = [{"label": row.label, "reserved_minor": row.reserved_minor}
        for row in session.scalars(select(BudgetEnvelope).where(BudgetEnvelope.sandbox_id == principal.sandbox_id,
                                                               BudgetEnvelope.user_id == principal.user_id).limit(6))]
    from .extensions_models import CashflowGoal, FamilyMember, CarbonBehavior, CarbonReward
    facts["goals"] = [{"goal_name": row.name, "target_minor": row.target_minor,
                       "reserved_minor": row.reserved_minor, "source_version": row.version}
                      for row in session.scalars(select(CashflowGoal).where(CashflowGoal.sandbox_id == principal.sandbox_id,
                          CashflowGoal.user_id == principal.user_id, CashflowGoal.status == "ACTIVE").limit(10))]
    facts["family_helpers"] = [row.display_name for row in session.scalars(select(FamilyMember).where(
        FamilyMember.sandbox_id == principal.sandbox_id, FamilyMember.owner_user_id == principal.user_id,
        FamilyMember.helper_user_id.is_not(None)).limit(10))]
    facts["carbon_behaviors"] = [row.name for row in session.scalars(select(CarbonBehavior).where(
        CarbonBehavior.sandbox_id == principal.sandbox_id, CarbonBehavior.user_id == principal.user_id).limit(8))]
    facts["carbon_rewards"] = [{"reward_name": row.name, "cost_points": row.cost_points}
                             for row in session.scalars(select(CarbonReward).where(
                                 CarbonReward.sandbox_id == principal.sandbox_id).limit(6))]
    # The collector remains bounded and owner-scoped. Only the outbound model
    # facts are projected; this does not claim fewer database reads.
    return project_planning_facts(facts, message, preserve_context=preserve_context)
