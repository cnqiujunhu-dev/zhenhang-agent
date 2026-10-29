"""Fresh DB, no cloud/network or running account data. Intended for app/tests/."""
import pytest
pytestmark=pytest.mark.release_credentials
from sqlalchemy import select,func
from zhenxing.database import create_database
from zhenxing.schema import ensure_schema
from zhenxing.release_seed import initialize_release_data
from zhenxing.models import Account,Card,Sandbox,Subscription,Transaction
from zhenxing.access_models import LoginAccount
from zhenxing.credit_models import CreditCard,CreditStatement,CreditObligation
from zhenxing.credit_service import credit_account_invariants
from zhenxing.credit_billing import statement_view
from zhenxing.services import Principal
from zhenxing.contracts import DomainError
from zhenxing.occasion_models import FamilyOccasion
from zhenxing.contact_models import ContactAlias

@pytest.fixture
def store(tmp_path):
    engine,factory=create_database('sqlite:///'+(tmp_path/'release.sqlite').as_posix());ensure_schema(engine)
    yield factory
    engine.dispose()

def initialize(db):
    return initialize_release_data(db,business_time='2026-10-30',customer_password_suffix='FixtureSuffixOnly2026',admin_password='FixtureAdminOnly2026')

def test_new_data_has_independent_cash_credit_debt_and_twelve_month_history(store):
    with store.begin()as db:initialize(db)
    with store()as db:
        actors=list(db.scalars(select(LoginAccount).where(LoginAccount.role!='ADMIN')))
        assert len(actors)==14
        for actor in actors:
            owner=Principal(actor.user_id,actor.sandbox_id)
            cards=list(db.scalars(select(Card).where(Card.user_id==actor.user_id)))
            assert len(cards)==2 and len({c.account_id for c in cards})==2
            assert db.scalar(select(func.count()).select_from(CreditCard).where(CreditCard.user_id==actor.user_id))==1
            assert db.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id==actor.user_id))==2
            transactions=list(db.scalars(select(Transaction).where(Transaction.sandbox_id==actor.sandbox_id)))
            assert len({t.occurred_at.strftime('%Y-%m')for t in transactions})==12
            bill=statement_view(db,owner,currency='CNY')['statements_by_currency'][0]
            assert (bill['outstanding_minor'],bill['current_due_minor'],bill['unbilled_principal_minor'])==(103600,44800,58800)
            assert len(bill['statements'])==2 and credit_account_invariants(db,owner)['ok']
            assert db.scalar(select(func.count()).select_from(FamilyOccasion).where(FamilyOccasion.owner_user_id==actor.user_id))==2
            assert db.scalar(select(func.count()).select_from(ContactAlias).where(ContactAlias.user_id==actor.user_id))==2

def test_repeated_initialization_preserves_user_changes_and_first_date(store):
    with store.begin()as db:result=initialize(db)
    with store.begin()as db:
        actor=db.scalar(select(LoginAccount).where(LoginAccount.username=='quyuwei'))
        from zhenxing.payment_instruments import resolve_source_instrument,post_merchant_payment
        owner=Principal(actor.user_id,actor.sandbox_id)
        snapshot=resolve_source_instrument(db,owner,None,'CNY',purpose='MERCHANT')
        account=db.get(Account,snapshot['account_id']);bank_id=account.id;expected=account.balance_minor-123
        post_merchant_payment(db,owner,instrument_snapshot=snapshot,amount_minor=123,merchant_ref='社区超市',operation_key='post-initialization-purchase')
        sub=db.scalar(select(Subscription).where(Subscription.user_id==actor.user_id));sub.status='CANCELLED';sub.auto_renew=False;sub_id=sub.id
    with store.begin()as db:
        replay=initialize_release_data(db,business_time='2027-01-01')
        assert replay['replayed'] and replay['business_time']==result['business_time']
        assert db.get(Account,bank_id).balance_minor==expected
        assert db.get(Subscription,sub_id).status=='CANCELLED' and not db.get(Subscription,sub_id).auto_renew
        assert db.scalar(select(func.count()).select_from(LoginAccount))==15

def test_unmarked_nonempty_database_is_refused(store):
    with store.begin()as db:db.add(Sandbox())
    with store.begin()as db:
        with pytest.raises(DomainError)as exc:initialize(db)
        assert exc.value.code=='RELEASE_SEED_EMPTY_DATABASE_REQUIRED'
        assert db.scalar(select(func.count()).select_from(LoginAccount))==0

def test_mid_initialization_failure_rolls_back_all_accounts_and_money(store,monkeypatch):
    import zhenxing.release_seed as module
    def fail(*_):raise RuntimeError('isolated seed interruption')
    monkeypatch.setattr(module,'_credit_history',fail)
    with pytest.raises(RuntimeError):
        with store.begin()as db:initialize(db)
    with store()as db:
        assert db.scalar(select(func.count()).select_from(LoginAccount))==0
        assert db.scalar(select(func.count()).select_from(Account))==0
        assert db.scalar(select(func.count()).select_from(Sandbox))==0
