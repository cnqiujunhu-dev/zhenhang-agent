"""Private credit billing status, never a recipient transfer-risk decision.

The legacy module name is retained for historical evidence scripts. Current
payment paths must not import this projection: even a generic warning would
reveal another customer's private billing state. Only authorized credit
servicing views may read these records.
"""
from datetime import datetime,timezone,timedelta
from sqlalchemy import select
from .models import utcnow
from .authentication import as_utc
from .credit_models import CreditAccount,CreditStatement
from .planning import canonical_hash

VERSION='private-credit-billing-status-v2'
TZ=timezone(timedelta(hours=8))

def credit_billing_status(session,account,*,now=None):
    """Caller must authorize private credit servicing access before reading."""
    now=as_utc(now or utcnow());local=now.astimezone(TZ)
    start=local.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    from .credit_billing import _obligations,_fees,_due_obligation,_due_fee
    rows=session.execute(select(CreditStatement,CreditAccount).join(CreditAccount,CreditAccount.id==CreditStatement.credit_account_id)
        .where(CreditAccount.user_id==account.user_id,CreditAccount.sandbox_id==account.sandbox_id)
        .order_by(CreditStatement.period_end.desc(),CreditStatement.id)).all()
    seen=set();sources=[];unavailable=False
    for statement,facility in rows:
        if canonical_hash(statement.snapshot_json)!=statement.snapshot_hash:unavailable=True;continue
        closed=statement.snapshot_json.get('closed_at')
        try:issued=as_utc(datetime.fromisoformat(closed)) if closed else as_utc(statement.created_at)
        except (ValueError,TypeError):unavailable=True;continue
        if issued>now:continue
        key=(facility.id,statement.currency)
        if key in seen:continue
        seen.add(key)
        cutoff=as_utc(statement.period_end)
        remaining=sum(_due_obligation(session,x,cutoff,include_unbilled=True)for x in _obligations(session,facility,statement.currency)if as_utc(x.created_at)<cutoff)
        remaining+=sum(_due_fee(session,x,cutoff)for x in _fees(session,facility,statement.currency)if as_utc(x.created_at)<cutoff)
        if remaining>0:sources.append({'statement_id':statement.id,'currency':statement.currency,'remaining_due_minor':remaining,
            'closed_at':issued.isoformat(),'credit_account_id':facility.id})
    outstanding=bool(sources)
    return {'username':account.username,
        'label':'已出账待还'if outstanding else '账单待核验'if unavailable else '无已出账待还款项',
        'billing_status':'BILLED_OUTSTANDING'if outstanding else 'SOURCE_UNAVAILABLE'if unavailable else 'NO_BILLED_OUTSTANDING',
        'has_billed_outstanding':outstanding,'source_review_required':unavailable,
        'month':start.strftime('%Y-%m'),'policy_version':VERSION,
        'private_reason':'CLOSED_STATEMENT_UNSETTLED'if outstanding else 'SOURCE_UNAVAILABLE'if unavailable else None,
        'private_sources':sources,'checked_at':now.isoformat(),'overdue_asserted':False}


def credit_risk_label(session,account,*,now=None):
    """Historical read-only adapter; no payment gate or risk label is produced.

    New code uses credit_billing_status and its explicitly private fields.
    """
    result=credit_billing_status(session,account,now=now)
    return {**result,'active':result['has_billed_outstanding'],
        'review_required':result['source_review_required']}
