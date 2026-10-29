"""Owner/card-scoped purchase detail from verified credit postings, never UI data."""
from sqlalchemy import select, func

from .authentication import as_utc
from .contracts import DomainError
from .credit_models import CreditObligation, CreditStatement, PaymentRecord, PaymentRefund
from .credit_receipts import credit_payment_posting_integrity, credit_refund_posting_integrity
from .planning import canonical_hash

VERSION = 'credit-statement-consumption-v1'
MARKER = 'consumption_projection_version'
LIMIT = 100


def projection_version(result):
    if MARKER not in result:
        return None
    if type(result[MARKER]) is not str or result[MARKER] != VERSION:
        raise ValueError('CREDIT_CONSUMPTION_VERSION_INVALID')
    return VERSION


def _unverified():
    raise DomainError('CREDIT_CONSUMPTION_UNVERIFIED', '消费明细的记账来源暂未核验完整，请稍后重新查询。', 409)


def consumption_details(session, principal, card, facility, currency, *, limit=LIMIT):
    if (card.sandbox_id, card.user_id) != (principal.sandbox_id, principal.user_id) \
            or (facility.sandbox_id, facility.user_id) != (principal.sandbox_id, principal.user_id) \
            or card.credit_account_id != facility.id:
        raise DomainError('CREDIT_OWNER_REQUIRED', '当前身份不能读取这张信用卡。', 403)
    if currency not in {'CNY','USD'} or type(limit) is not int or not 1 <= limit <= LIMIT:
        _unverified()
    where = (PaymentRecord.sandbox_id == principal.sandbox_id, PaymentRecord.user_id == principal.user_id,
        PaymentRecord.credit_account_id == facility.id, PaymentRecord.instrument_id == 'credit:'+card.id,
        PaymentRecord.instrument_type == 'CREDIT', PaymentRecord.currency == currency,
        PaymentRecord.status.in_(['POSTED','REFUNDED']))
    total = session.scalar(select(func.count()).select_from(PaymentRecord).where(*where))
    payments = list(session.scalars(select(PaymentRecord).where(*where)
        .order_by(PaymentRecord.created_at.desc(), PaymentRecord.id.desc()).limit(limit)))
    details=[]
    for payment in payments:
        if not credit_payment_posting_integrity(session, principal, payment):
            _unverified()
        obligation = session.scalar(select(CreditObligation).where(CreditObligation.payment_id == payment.id,
            CreditObligation.credit_account_id == facility.id, CreditObligation.currency == currency))
        if obligation is None or not 0 <= obligation.remaining_minor <= obligation.amount_minor <= payment.amount_minor:
            _unverified()
        statement = session.get(CreditStatement, obligation.statement_id) if obligation.statement_id else None
        if obligation.statement_id and (statement is None or statement.credit_account_id != facility.id
                or statement.currency != currency or statement.snapshot_hash != canonical_hash(statement.snapshot_json)):
            _unverified()
        if statement and any(statement.snapshot_json.get(key) != value for key,value in {
                'currency':currency,'cycle_key':statement.cycle_key,
                'period_start':as_utc(statement.period_start).isoformat(),
                'period_end':as_utc(statement.period_end).isoformat(),
                'due_at':as_utc(statement.due_at).isoformat()}.items()):
            _unverified()
        refunds = list(session.scalars(select(PaymentRefund).where(PaymentRefund.payment_id == payment.id)))
        if any(not credit_refund_posting_integrity(session,principal,refund,payment) for refund in refunds):
            _unverified()
        refunded = sum(refund.receipt_json['amount_minor'] for refund in refunds)
        if not 0 <= refunded <= payment.amount_minor:
            _unverified()
        details.append({'payment_id':payment.id,'merchant':payment.merchant_ref,
            'amount_minor':payment.amount_minor,'currency':currency,
            'occurred_at':as_utc(payment.created_at).isoformat(),
            'billing_status':'BILLED' if statement else 'UNBILLED',
            'statement_cycle':statement.cycle_key if statement else None,
            'statement_period_start':as_utc(statement.period_start).isoformat() if statement else None,
            'statement_period_end':as_utc(statement.period_end).isoformat() if statement else None,
            'due_at':as_utc(statement.due_at).isoformat() if statement else None,
            'remaining_principal_minor':obligation.remaining_minor,
            'refunded_minor':refunded,'net_purchase_minor':payment.amount_minor-refunded,
            'has_installment':obligation.installment_id is not None})
    return {'schema_version':VERSION,'scope':'SELECTED_CARD_PURCHASES','currency':currency,
        'total_count':total,'returned_count':len(details),'limit':limit,'has_more':total>len(details),
        'displayed_purchase_minor':sum(row['amount_minor'] for row in details),
        'displayed_remaining_principal_minor':sum(row['remaining_principal_minor'] for row in details),
        'billed_count':sum(row['billing_status']=='BILLED' for row in details),
        'unbilled_count':sum(row['billing_status']=='UNBILLED' for row in details),'items':details}
