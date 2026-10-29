"""Explicit fresh-environment sample data, built from application domain services.

No runtime database export, reset, or automatic reseeding of existing accounts.
Install this file as zhenxing/release_seed.py in the reviewed distribution.
"""
from __future__ import annotations
import os
from contextlib import contextmanager
from datetime import datetime,date,timedelta,timezone
from sqlalchemy import select,func,text
from .contracts import DomainError
from .models import Account,Card,Transaction,InvestmentProduct,Sandbox,User
from .access_models import LoginAccount,AccessAudit

REVISION='release-samples-v1'
MARKER='RELEASE_SAMPLES_INITIALIZED'
TZ=timezone(timedelta(hours=8))

def anchor_time(value=None):
    if value is None:return datetime.now(TZ).replace(microsecond=0).astimezone(timezone.utc)
    if isinstance(value,str):
        if len(value)==10:value=datetime.combine(date.fromisoformat(value),datetime.min.time(),TZ).replace(hour=12)
        else:value=datetime.fromisoformat(value)
    if not isinstance(value,datetime) or value.tzinfo is None or not 2000<=value.year<=2100:
        raise DomainError('RELEASE_SEED_DATE_INVALID','初始化日期需要带时区，或使用YYYY-MM-DD。',422)
    return value.astimezone(timezone.utc)

@contextmanager
def _initial_posting_time(session,when):
    """Set dates before INSERT, on this new-environment Session only.

    Existing immutable rows are never updated and global clocks are unchanged.
    """
    from sqlalchemy import event
    from .credit_models import PaymentRecord
    def before_flush(current,_context,_instances):
        for obj in current.new:
            if type(obj).__module__=='zhenxing.credit_models' and hasattr(obj,'created_at'):
                obj.created_at=when
            if isinstance(obj,PaymentRecord) and isinstance(obj.receipt_json,dict):
                obj.receipt_json['posted_at']=when.isoformat()
            if isinstance(obj,Transaction):obj.occurred_at=when
    event.listen(session,'before_flush',before_flush)
    try:yield
    finally:event.remove(session,'before_flush',before_flush)

def _credit_history(session,login,clock):
    from .services import Principal
    from .credit_models import CreditCard,CreditRepayment,CreditOperation,PaymentJournal
    from .payment_instruments import resolve_source_instrument
    from .credit_service import post_credit_purchase,credit_account_invariants
    from .credit_billing import close_credit_statement,repay_credit
    who=Principal(login.user_id,login.sandbox_id)
    card=session.scalar(select(CreditCard).where(CreditCard.user_id==login.user_id))
    def charge(amount,merchant,days,key):
        snapshot=resolve_source_instrument(session,who,card.card_number,'CNY',purpose='MERCHANT')
        with _initial_posting_time(session,clock-timedelta(days=days)):
            post_credit_purchase(session,who,instrument_snapshot=snapshot,amount_minor=amount,
                merchant_ref=merchant,operation_key=f'{REVISION}:{login.username}:{key}')
    for i,(amount,merchant,days) in enumerate([(8800,'社区餐饮',65),(22000,'生活超市',60),(3600,'城市交通',55)]):
        charge(amount,merchant,days,'paid-purchase-'+str(i))
    with _initial_posting_time(session,clock-timedelta(days=45)):
        close_credit_statement(session,who,card.card_number,currency='CNY',cycle_key='release-previous',
            start=clock-timedelta(days=75),end=clock-timedelta(days=45),due_at=clock-timedelta(days=25),now=clock-timedelta(days=45))
    source=resolve_source_instrument(session,who,None,'CNY',purpose='REPAYMENT')
    with _initial_posting_time(session,clock-timedelta(days=30)):
        repay_credit(session,who,card_reference=card.card_number,source_snapshot=source,currency='CNY',
            amount_minor=34400,operation_key=f'{REVISION}:{login.username}:previous-repayment')
    charge(32800,'生活超市',20,'current-groceries');charge(12000,'周末餐饮',5,'current-dining')
    with _initial_posting_time(session,clock-timedelta(days=2)):
        close_credit_statement(session,who,card.card_number,currency='CNY',cycle_key='release-current',
            start=clock-timedelta(days=45),end=clock-timedelta(days=2),due_at=clock+timedelta(days=10),now=clock-timedelta(days=2))
    charge(58800,'数码家电',1,'unbilled-purchase')
    session.flush()
    assert credit_account_invariants(session,who)['ok']

def _family_profiles(session,login,clock):
    from .services import Principal
    from .extensions_models import FamilyMember
    from .family_profile_service import put_member_profile
    from .occasion_service import put_family_occasion
    who=Principal(login.user_id,login.sandbox_id)
    existing=list(session.scalars(select(FamilyMember).where(FamilyMember.owner_user_id==login.user_id).order_by(FamilyMember.display_name)))
    if existing:
        subjects=[(m.id,'陈浩' if m.display_name=='儿子' else '陈悦',m.display_name)for m in existing[:2]]
    else:subjects=[(None,'林悦','配偶'),(None,'陈悦','女儿')]
    for index,(member_id,name,relation) in enumerate(subjects):
        result=put_member_profile(session,who,family_member_id=member_id,display_name=name,
            relationship_label=relation,expected_version=0,source_reference=f'{REVISION}:family:{login.username}:{index}')
        member=result['members'][0]['family_member_id']
        day=(clock.astimezone(TZ)+timedelta(days=14+index*16)).date()
        put_family_occasion(session,who,family_member_id=member,month=day.month,day=day.day,
            leap_day_policy='FEB28' if (day.month,day.day)==(2,29)else None,adult_subject_confirmed=True,
            source_reference=f'{REVISION}:birthday:{login.username}:{index}',expected_version=0)

def _contacts(session,logins):
    from .contact_models import ContactAlias
    from .payment_instruments import resolve_recipient_instrument
    for i,login in enumerate(logins):
        for alias,target in [('房东',logins[(i+1)%len(logins)]),('朋友小林',logins[(i+2)%len(logins)])]:
            snapshot=resolve_recipient_instrument(session,target.username)
            session.add(ContactAlias(sandbox_id=login.sandbox_id,user_id=login.user_id,alias_name=alias,
                recipient_login_account_id=target.id,recipient_bank_account_id=snapshot['account_id'],
                recipient_card_id=snapshot['card_id']))

def initialize_release_data(session,*,business_time=None,customer_password_suffix=None,admin_password=None):
    """Caller owns transaction. Existing unmarked business data is a hard stop."""
    from .accounts import SEED_USERNAMES,PROFILE_CHOICES,create_customer_account,password_hash
    from .demo_family_accounts import (_ACCOUNT_DEFAULTS,DEMO_FAMILY_ACCOUNTS_REVISION,bootstrap_demo_family_accounts)
    from .asset_samples import bootstrap_asset_samples
    from .customer_profile_migration import seed_named_customer_profiles
    from .demo_carbon_funding import bootstrap_demo_carbon_points
    from .fx_banking import bootstrap_dual_currency_accounts
    from .credit_bootstrap import seed_payment_upgrades_for_usernames
    from .life_plan_migration import initialize_life_plan_services
    from .asset_models import AssetSnapshotPrice
    if session.bind.dialect.name=='postgresql':session.execute(text('SELECT pg_advisory_xact_lock(9360490)'))
    markers=list(session.scalars(select(AccessAudit).where(AccessAudit.action==MARKER)))
    if markers:
        if len(markers)!=1 or markers[0].payload_json.get('revision')!=REVISION:
            raise DomainError('RELEASE_SEED_MARKER_CHANGED','初始化标识不一致，未重新填充。',409)
        saved=markers[0].payload_json
        for username,user_id in saved['identities'].items():
            found=session.scalar(select(LoginAccount).where(LoginAccount.username==username))
            if found is None or found.user_id!=user_id:raise DomainError('RELEASE_SEED_IDENTITIES_CHANGED','初始化身份已变化，未补回或覆盖。',409)
        return {**saved,'replayed':True}
    if session.scalar(select(func.count()).select_from(LoginAccount)) or session.scalar(select(func.count()).select_from(Sandbox)):
        raise DomainError('RELEASE_SEED_EMPTY_DATABASE_REQUIRED','只在全新空环境创建发布数据，现有账户不会重置。',409)
    suffix=customer_password_suffix or os.getenv('ZX_INITIAL_CUSTOMER_SUFFIX','')
    admin_secret=admin_password or os.getenv('ZX_INITIAL_ADMIN_PASSWORD','')
    if len(suffix)<12 or len(admin_secret)<12:
        raise DomainError('RELEASE_SEED_CREDENTIALS_REQUIRED','请先生成独立初始凭据；客户后缀和管理员口令均至少12位。',422)
    clock=anchor_time(business_time);logins=[]
    display={'wangwei':'王伟','qiujunhu':'邱俊虎','quyuwei':'曲宇玮','wangmingzhe':'王明哲','liyanliang':'李彦良',
        'panzhinuo':'潘芷诺','test':'陈博','chenyue':'陈悦','zhanghao':'张昊','linxinyi':'林欣怡'}
    for i,name in enumerate(SEED_USERNAMES):
        logins.append(create_customer_account(session,name,name+suffix,PROFILE_CHOICES[i%4],seed=20261001+i*101,
            role='DEV_TEST'if name=='test'else'CUSTOMER',display_name=display.get(name,name),business_time=clock))
    for name,label,profile,seed in _ACCOUNT_DEFAULTS:
        login=create_customer_account(session,name,name+suffix,profile,seed=seed,display_name=label,business_time=clock)
        logins.append(login)
        session.add(AccessAudit(target_account_id=login.id,action='DEMO_FAMILY_ACCOUNT_CREATED',payload_json={
            'revision':DEMO_FAMILY_ACCOUNTS_REVISION,'operation_key':f'{DEMO_FAMILY_ACCOUNTS_REVISION}:account:{name}',
            'username':name,'user_id':login.user_id,'sandbox_id':login.sandbox_id,'authority':'RELEASE_FIXTURE_INITIALIZATION',
            'credentials_reset_on_restart':False}))
    sid=Sandbox();session.add(sid);session.flush();user=User(sandbox_id=sid.id,display_name='系统管理员');session.add(user);session.flush()
    admin=LoginAccount(username='admin',password_hash=password_hash(admin_secret),user_id=user.id,sandbox_id=sid.id,role='ADMIN')
    session.add(admin);session.flush()
    bootstrap_dual_currency_accounts(session);bootstrap_demo_family_accounts(session)
    bootstrap_asset_samples(session);seed_named_customer_profiles(session);bootstrap_demo_carbon_points(session)
    names=[x.username for x in logins]
    seed_payment_upgrades_for_usernames(session,names,adult_usernames=names,eligibility_source='EXPLICIT_ADULT_RELEASE_FIXTURES_V1')
    for login in logins:
        initialize_life_plan_services(session,login.sandbox_id)
        for account in session.scalars(select(Account).where(Account.user_id==login.user_id)):
            if '主要' in account.name:account.name='日常收支账户'
        for product in session.scalars(select(InvestmentProduct).where(InvestmentProduct.sandbox_id==login.sandbox_id)):
            product.name=product.name.replace('（仿真）','')
        for tx in session.scalars(select(Transaction).where(Transaction.sandbox_id==login.sandbox_id,Transaction.category=='测试攻击文本')):
            tx.merchant='未知扣款商户';tx.category='待核验';tx.flag='ANOMALY'
        _credit_history(session,login,clock);_family_profiles(session,login,clock)
    _contacts(session,logins)
    for price in session.scalars(select(AssetSnapshotPrice)):price.observed_at=clock
    session.flush()
    manifest={'revision':REVISION,'business_time':clock.isoformat(),'customer_count':len(logins),
        'identities':{x.username:x.user_id for x in logins},'generated_from_runtime_export':False,
        'sample_credit':{'closed_due_cny_minor':44800,'unbilled_cny_minor':58800,'settled_previous_cny_minor':34400},
        'cards_per_customer':{'debit':2,'credit':1},'subscriptions_per_customer':2,'replayed':False}
    session.add(AccessAudit(target_account_id=admin.id,action=MARKER,payload_json=manifest));session.flush()
    return manifest

def main():
    import argparse,json
    from .database import create_database
    from .schema import ensure_schema
    parser=argparse.ArgumentParser(description='Initialize a new environment with reproducible sample data; never reset existing accounts.')
    parser.add_argument('--as-of',help='YYYY-MM-DD or timezone-aware ISO datetime; overrides ZX_SEED_AS_OF')
    args=parser.parse_args()
    if len(os.getenv('ZX_INITIAL_CUSTOMER_SUFFIX',''))<12 or len(os.getenv('ZX_INITIAL_ADMIN_PASSWORD',''))<12:
        parser.error('Generate local initial credentials first; suffix and admin password must each have at least 12 characters.')
    engine,factory=create_database();ensure_schema(engine)
    requested=args.as_of or os.getenv('ZX_SEED_AS_OF') or None
    with factory.begin()as session:result=initialize_release_data(session,business_time=requested)
    if requested and result.get('replayed'):
        result={**result,'requested_as_of':requested,'existing_initialization_date_preserved':True}
    print(json.dumps({k:v for k,v in result.items()if k!='identities'},ensure_ascii=False))

if __name__=='__main__':main()
