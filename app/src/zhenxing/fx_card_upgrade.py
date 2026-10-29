"""Explicit administrator configuration of one owned debit card; zero funding."""
from sqlalchemy import select

from .access_models import LoginAccount, ScenarioEditEvent
from .contracts import DomainError
from .credit_atomic import atomic_domain
from .fx_banking import _profile, fxcardprojection
from .fx_models import DualCurrencyProfile, FxWallet
from .models import Account, Card, User


@atomic_domain
def upgrade_debit_currency_support(session, *, username, card_id, actor_account_id, user_approved=False):
    if user_approved is not True:
        raise DomainError("EXPLICIT_CURRENCY_UPGRADE_APPROVAL_REQUIRED", "需明确确认双币配置变更。", 403)
    actor = session.scalar(select(LoginAccount).where(LoginAccount.id==actor_account_id).with_for_update().execution_options(populate_existing=True))
    if not actor or actor.role!="ADMIN" or actor.status!="ACTIVE":
        raise DomainError("ADMIN_CONFIGURATION_REQUIRED", "此配置需有效管理员处理。", 403)
    login = session.scalar(select(LoginAccount).where(LoginAccount.username==username).with_for_update().execution_options(populate_existing=True))
    if not login or login.status!="ACTIVE" or login.role!="CUSTOMER":
        raise DomainError("ACTIVE_CUSTOMER_REQUIRED", "需核对本人有效客户资料。", 409)
    owner = session.scalar(select(User).where(User.id==login.user_id, User.sandbox_id==login.sandbox_id).with_for_update().execution_options(populate_existing=True))
    card = session.scalar(select(Card).where(Card.id==card_id, Card.user_id==login.user_id,
        Card.sandbox_id==login.sandbox_id).with_for_update().execution_options(populate_existing=True))
    if not owner or not card or card.status!="ACTIVE":
        raise DomainError("OWN_ACTIVE_DEBIT_CARD_REQUIRED", "只能升级本人有效借记卡。", 409)
    account = session.scalar(select(Account).where(Account.id==card.account_id,
        Account.user_id==login.user_id, Account.sandbox_id==login.sandbox_id).with_for_update().execution_options(populate_existing=True))
    if not account or account.currency!="CNY":
        raise DomainError("OWN_CNY_ACCOUNT_REQUIRED", "需核对本人对应人民币账户。", 409)
    profile = session.scalar(select(DualCurrencyProfile).where(DualCurrencyProfile.user_id==login.user_id)
        .with_for_update().execution_options(populate_existing=True))
    if profile:
        session.scalar(select(FxWallet).where(FxWallet.id==profile.usd_wallet_id)
            .with_for_update().execution_options(populate_existing=True))
        profile, wallet, bound = _profile(session, login)
        if bound.id!=card.id or profile.status!="ACTIVE" or wallet.status!="ACTIVE":
            raise DomainError("EXISTING_DUAL_CURRENCY_BINDING_PRESERVED", "已有双币绑定不自动迁移或重新启用。", 409)
        return {"status":"ALREADY_CONFIGURED", "card_id":card.id,"account_id":account.id,
            "profile_id":profile.id,"wallet_id":wallet.id,"wallet_balance_minor":wallet.balance_minor,
            "funding_created":False,"card":fxcardprojection(session,card)}
    if session.scalar(select(FxWallet.id).where(FxWallet.user_id==login.user_id)):
        raise DomainError("EXISTING_USD_WALLET_PRESERVED", "已有美元钱包需独立核对，不重新绑定或填入余额。", 409)
    balance_before = account.balance_minor
    wallet = FxWallet(sandbox_id=login.sandbox_id,user_id=login.user_id,balance_minor=0)
    session.add(wallet); session.flush()
    profile = DualCurrencyProfile(sandbox_id=login.sandbox_id,user_id=login.user_id,card_id=card.id,usd_wallet_id=wallet.id)
    session.add(profile)
    session.flush()
    audit = ScenarioEditEvent(sandbox_id=login.sandbox_id,user_id=login.user_id,actor_account_id=actor.id,
        entity="dual_currency_profile",row_id=profile.id,previous_version=0,resulting_version=profile.version,
        reason="Explicit user-approved dual-currency configuration; zero initial USD funding",
        changes_json={"from":"CNY_ONLY","to":"CNY_USD","profile_id":profile.id,"usd_wallet_id":wallet.id,
            "usd_initial_balance_minor":0,"cny_balance_before_minor":balance_before,"cny_balance_after_minor":account.balance_minor,
            "prior_card_id_preserved":True,"prior_account_id_preserved":True,"card_controls_version_preserved":card.version,
            "financial_postings_created":False})
    session.add(audit); session.flush()
    return {"status":"UPGRADED_WITH_ZERO_USD_FUNDING","card_id":card.id,"account_id":account.id,
        "profile_id":profile.id,"wallet_id":wallet.id,"wallet_balance_minor":0,"edit_event_id":audit.id,
        "funding_created":False,"card":fxcardprojection(session,card)}
