"""Time and settlement facts for existing scheduled payments, without predictions."""
import re
from .authentication import as_utc
from .models import Transfer


def timing_policy(source):
    explicit = isinstance(source, str) and bool(re.search(
        r'\d{1,2}:\d{2}|(?:\d{1,2}|[零一二三四五六七八九十]{1,3})点|\d{1,2}\s*(?:AM|PM)\b', source, re.I))
    return {'version':'schedule-time-policy-v1', 'time_zone':'Asia/Shanghai',
        'execution_precision':'NOT_BEFORE_TIMESTAMP', 'time_origin':'USER_TIME' if explicit else 'DEFAULT_09_00',
        'arrival_time_guaranteed':False, 'holiday_adjustment':'NONE'}


def posting_facts(session, transfer_id):
    transfer = session.get(Transfer, transfer_id) if transfer_id else None
    if transfer is None or transfer.status != 'POSTED':
        return {'debit_status':'NOT_POSTED', 'settlement_status':'NOT_SUBMITTED', 'debit_posted_at':None}
    from .internal_banking_models import InternalTransferSettlement
    settlement = session.get(InternalTransferSettlement, transfer.id)
    return {'debit_status':'POSTED', 'settlement_status':'SETTLED' if settlement else 'UNVERIFIED',
        'debit_posted_at':as_utc(transfer.created_at).isoformat()}


def schedule_projection(session, row, series=None):
    from .transfer_recurrence import series_view
    scope = row.scope_json or {}
    instrument = scope.get('transfer_payment_scope') or {}
    details = series_view(session,series) if series else None
    settlement = posting_facts(session,row.transfer_id)
    if details:
        posted=[part for part in details['occurrences'] if part['debit_status']=='POSTED']
        settlement={'debit_status':('POSTED' if len(posted)==details['total_occurrences'] else 'PARTIALLY_POSTED') if posted else 'NOT_POSTED',
            'settlement_status':('UNVERIFIED' if any(p['settlement_status']=='UNVERIFIED' for p in posted) else 'SETTLED') if posted else 'NOT_SUBMITTED',
            'posted_occurrences':len(posted), 'debit_posted_at':max((p['debit_posted_at'] for p in posted),default=None)}
    return {'schedule_id':row.id, 'task_id':row.task_id, 'payee_name':scope.get('payee_name',''),
        'amount_minor':row.amount_minor, 'currency':'CNY', 'memo':row.memo,
        'due_at':as_utc(row.due_at).isoformat(), 'status':series.status if series else row.status,
        'source_last4':(instrument.get('source') or {}).get('display_last4'),
        'recipient_last4':(instrument.get('recipient') or {}).get('display_last4'),
        'next_due_at':as_utc(series.next_due_at).isoformat() if series and series.next_due_at else None,
        'transfer_id':row.transfer_id, 'error':row.last_error,
        'series':details, 'timing_policy':scope.get('timing_policy'), **settlement}
