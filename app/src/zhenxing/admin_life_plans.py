"""Read-only, admin-scoped observations of the actual birthday lifecycle.

This module never prepares, authorizes, queues, pays or advances anything.
Dependencies mirror life_plan_worker; unused-fund release is a recorded
business stage inside fulfilment, not a manufactured LifePlanJob.
"""
from fastapi import Depends, Query
from sqlalchemy import select
from .accounts import require_account_role
from .authentication import as_utc
from .access_models import LoginAccount
from .credit_models import PaymentJournal
from .life_plan_models import LifePlan, LifePlanMandate, LifePlanOccurrence, LifePlanJob
from .life_plan_service import life_plan_view
from .models import User
from .services import Principal

def _time(value):return as_utc(value).isoformat() if value else None

def observe_birthday_plans(session, *, username=None, plan_id=None, limit=20):
    query=select(LifePlan).join(LoginAccount, (LoginAccount.user_id==LifePlan.user_id)&(LoginAccount.sandbox_id==LifePlan.sandbox_id))
    if username:query=query.where(LoginAccount.username==username)
    if plan_id:query=query.where(LifePlan.id==plan_id)
    plans=session.scalars(query.order_by(LifePlan.created_at.desc(),LifePlan.id).limit(limit+1)).all()
    output=[]
    for p in plans[:limit]:
        owner=session.scalar(select(LoginAccount).where(LoginAccount.user_id==p.user_id,LoginAccount.sandbox_id==p.sandbox_id))
        user=session.get(User,p.user_id)
        principal=Principal(p.user_id,p.sandbox_id)
        view=life_plan_view(session,principal,p.id)['plans'][0]
        mandates=session.scalars(select(LifePlanMandate).where(LifePlanMandate.plan_id==p.id).order_by(LifePlanMandate.authorized_at.desc())).all()
        mandate=next((m for m in mandates if m.plan_version==p.version),None)
        occurrences=[]
        for occurrence in session.scalars(select(LifePlanOccurrence).where(LifePlanOccurrence.plan_id==p.id).order_by(LifePlanOccurrence.occurrence_key)):
            jobs=session.scalars(select(LifePlanJob).where(LifePlanJob.occurrence_id==occurrence.id).order_by(LifePlanJob.next_run_at,LifePlanJob.item_index,LifePlanJob.id)).all()
            current={ (j.kind,j.item_index):j for j in jobs if j.plan_version==p.version}
            job_rows=[]
            for j in jobs:
                dependencies=[]
                if j.kind=='RESERVE':dependencies=['authorization']
                if j.kind=='ORDER':
                    previous=current.get(('ORDER',j.item_index-1)) if j.item_index else current.get(('RESERVE',None))
                    dependencies=[previous.id] if previous else []
                if j.kind=='FULFIL':
                    previous=current.get(('ORDER',j.item_index));dependencies=[previous.id] if previous else []
                result=j.result_json if isinstance(j.result_json,dict) else {}
                job_rows.append({'job_id':j.id,'kind':j.kind,'item_index':j.item_index,'plan_version':j.plan_version,
                    'status':j.status,'scheduled_at':_time(j.next_run_at),'completed_at':_time(j.completed_at),'attempts':j.attempts,
                    'depends_on':dependencies,'error_code':j.last_error,
                    'order_id':result.get('order_id'),'amount_minor':result.get('amount_minor'),
                    'reserved_minor':result.get('reserved_minor'),'cash_debit_minor':result.get('cash_debit_minor'),
                    'terminal_no_effect':result.get('terminal_no_effect') is True})
            release=session.scalar(select(PaymentJournal).where(PaymentJournal.user_id==p.user_id,PaymentJournal.sandbox_id==p.sandbox_id,
                PaymentJournal.kind=='RESERVATION_RELEASE',PaymentJournal.operation_key==f'life:{occurrence.id}:completed-release'))
            recorded=next((x for x in view['occurrences'] if x['occurrence_id']==occurrence.id),{})
            occurrences.append({'occurrence_id':occurrence.id,'birthday':occurrence.occurrence_key,'status':occurrence.status,
                'jobs':job_rows,'orders':recorded.get('orders',[]),
                'unused_fund_release':{'type':'BUSINESS_STAGE_NOT_JOB','recorded':release is not None,
                    'status':'SUCCEEDED' if release else 'NOT_RECORDED','journal_id':release.id if release else None,
                    'recorded_at':_time(release.created_at) if release else None,
                    'released_unused_minor':max(0,p.budget_minor-p.spent_minor) if release and p.status=='SUCCEEDED' and p.reserved_minor==0 else None,
                    'depends_on':[j.id for j in jobs if j.kind=='FULFIL' and j.plan_version==p.version],
                    'execution_boundary':'LAST_FULFIL_TRANSACTION'}})
        output.append({'plan_id':p.id,'name':p.name,'username':owner.username if owner else None,'display_name':user.display_name if user else None,
            'version':p.version,'status':p.status,'created_at':_time(p.created_at),'budget_minor':p.budget_minor,'reserved_minor':p.reserved_minor,
            'spent_minor':p.spent_minor,'refunded_minor':view['refunded_minor'],'net_cash_debit_minor':view['net_cash_debit_minor'],
            'scope':view['scope'],'authorization':{'recorded':mandate is not None,'status':mandate.status if mandate else 'NOT_AUTHORIZED',
                'task_id':mandate.task_id if mandate else None,'plan_version':mandate.plan_version if mandate else None,
                'authorized_at':_time(mandate.authorized_at) if mandate else None,'expires_at':_time(mandate.expires_at) if mandate else None},
            'occurrences':occurrences})
    return {'schema_version':'admin-birthday-lifecycle-v1','read_only':True,'bank_confirmation_permitted':False,
        'merchant_source':'VIRTUAL_MERCHANT_REGISTER_NOT_EXTERNAL_DELIVERY','plans':output,'truncated':len(plans)>limit,
        'dependency_contract':{'order_items':'SERIAL_BY_ITEM_INDEX','fulfil_items':'EACH_AFTER_OWN_ORDER','unused_release':'INSIDE_LAST_FULFIL_TRANSACTION'}}

def install_admin_life_plan_routes(app,get_session,get_principal):
    @app.get('/api/v2/admin/life-plans')
    def read_birthday_plans(username:str|None=Query(default=None,min_length=3,max_length=32),
            plan_id:str|None=Query(default=None,min_length=1,max_length=80),
            session=Depends(get_session),principal=Depends(get_principal)):
        require_account_role(session,principal,{'ADMIN'})
        with session.no_autoflush:
            return observe_birthday_plans(session,username=username,plan_id=plan_id)
