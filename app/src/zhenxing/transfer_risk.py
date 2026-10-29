"""Bank-owned transfer limits, independent of cards, models and confirmation.

The money caller holds the funding Account row lock before a commit check.
The usage record and monetary journal share the same outer transaction/savepoint.
No quota is consumed by preview, confirmation, a failed posting or a replay.
"""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select

from .banking_extension_models import TransferBlacklist, TransferDailyUsage, TransferRiskPosting, FxTransferRiskPosting
from .contracts import DomainError
from .extensions_models import FraudLabel
from .internal_banking_models import InternalPayeeBinding
from .models import Transfer, User, Account, Payee
from .planning import canonical_hash
from .operational_clock import posting_now


SINGLE_LIMIT_MINOR = 5_000_000
DAILY_LIMIT_MINOR = 10_000_000
HIGH_VALUE_MINOR = 1_000_000
LOCAL_TZ = timezone(timedelta(hours=8))
POLICY_VERSION = "transfer-limits-v1"
VELOCITY_VERSION = 'same-recipient-high-value-v1'
HIGH_VALUE_MAX_POSTED = 2


def _day(session, source):
    current = posting_now(session, source.sandbox_id, source.user_id).astimezone(LOCAL_TZ)
    start = datetime.combine(current.date(), time(), LOCAL_TZ)
    return current.date().isoformat(), start, start + timedelta(days=1)


def _target_ref(session, payee):
    binding = session.get(InternalPayeeBinding, payee.id)
    return "BANK:" + binding.recipient_login_account_id if binding else "PAYEE:" + payee.id


def _check_recipient(session, source, payee, *, lock):
    query = select(TransferBlacklist).where(TransferBlacklist.sandbox_id == source.sandbox_id,
        TransferBlacklist.user_id == source.user_id, TransferBlacklist.target_ref == _target_ref(session, payee))
    blocked = session.scalar(query.with_for_update() if lock else query)
    query = select(FraudLabel).where(FraudLabel.sandbox_id == source.sandbox_id,
        FraudLabel.user_id == source.user_id, FraudLabel.payee_id == payee.id)
    label = session.scalar(query.with_for_update() if lock else query)
    if blocked and blocked.status == "BLOCKED" or label and label.risk_level == "HIGH":
        raise DomainError("TRANSFER_RECIPIENT_BLOCKED", "收款对象在当前风险限制名单中，确认不能覆盖限制。", 403)
    if label and label.risk_level == "UNKNOWN":
        raise DomainError("TRANSFER_RECIPIENT_REVIEW_REQUIRED", "收款对象风险尚未核实，暂不执行转账。", 403)
    # A recipient's personal credit-card billing state is not a transfer-risk
    # signal. Do not read it here, including when replaying an old confirmation
    # or executing an AA/scheduled payment through the shared money entry point.

def _check_recipient_velocity(session,source,target_ref,amount_minor,day,start,end):
    if amount_minor<HIGH_VALUE_MINOR:return
    from sqlalchemy import or_,and_
    from .internal_banking_models import InternalPayeeBinding
    ids=select(InternalPayeeBinding.payee_id).where(InternalPayeeBinding.recipient_login_account_id==target_ref[5:]) \
        if target_ref.startswith('BANK:') else select(Payee.id).where(Payee.id==target_ref[6:])
    count=session.scalar(select(func.count()).select_from(Transfer).outerjoin(TransferRiskPosting,TransferRiskPosting.transfer_id==Transfer.id)
        .where(Transfer.user_id==source.user_id,Transfer.sandbox_id==source.sandbox_id,Transfer.status=='POSTED',
            Transfer.payee_id.in_(ids),Transfer.amount_minor>=HIGH_VALUE_MINOR,
            or_(TransferRiskPosting.business_date==day,and_(TransferRiskPosting.transfer_id.is_(None),Transfer.created_at>=start,Transfer.created_at<end)))) or 0
    if target_ref.startswith('BANK:'):
        from .fx_models import FxQuote,FxOperation
        for operation,quote in session.execute(select(FxOperation,FxQuote).join(FxQuote,FxQuote.id==FxOperation.quote_id)
                .join(FxTransferRiskPosting,FxTransferRiskPosting.fx_operation_id==FxOperation.id).where(
                    FxOperation.user_id==source.user_id,FxOperation.sandbox_id==source.sandbox_id,FxOperation.status=='POSTED',
                    FxOperation.kind=='TRANSFER',FxTransferRiskPosting.business_date==day,FxTransferRiskPosting.base_amount_minor>=HIGH_VALUE_MINOR)):
            if (quote.scope_json.get('target')or{}).get('login_id')==target_ref[5:]:count+=1
    if count>=HIGH_VALUE_MAX_POSTED:
        raise DomainError('TRANSFER_HIGH_VALUE_FREQUENCY_BLOCKED','今日已向同一收款账户完成两笔大额转账，本次转账触发异常交易风控，暂不执行。请通过银行服务渠道核实。',403)


def _historical_usage(session, source, day, start, end):
    recorded = select(TransferRiskPosting.transfer_id)
    total, count = session.execute(select(func.coalesce(func.sum(Transfer.amount_minor), 0), func.count()).where(
        Transfer.sandbox_id == source.sandbox_id, Transfer.user_id == source.user_id, Transfer.status == "POSTED",
        Transfer.created_at >= start, Transfer.created_at < end, Transfer.id.not_in(recorded))).one()
    tracked, tracked_count = session.execute(select(func.coalesce(func.sum(TransferRiskPosting.amount_minor), 0), func.count()).where(
        TransferRiskPosting.sandbox_id == source.sandbox_id, TransferRiskPosting.user_id == source.user_id,
        TransferRiskPosting.business_date == day,
        TransferRiskPosting.currency == "CNY")).one()
    fx_total, fx_count = session.execute(select(func.coalesce(func.sum(FxTransferRiskPosting.base_amount_minor), 0), func.count()).where(
        FxTransferRiskPosting.sandbox_id == source.sandbox_id, FxTransferRiskPosting.user_id == source.user_id,
        FxTransferRiskPosting.business_date == day)).one()
    from .fx_models import FxOperation, FxQuote
    untracked = session.execute(select(FxOperation, FxQuote).outerjoin(FxQuote, FxQuote.id == FxOperation.quote_id).where(
        FxOperation.sandbox_id == source.sandbox_id, FxOperation.user_id == source.user_id, FxOperation.kind == "TRANSFER",
        FxOperation.status == "POSTED", FxOperation.created_at >= start, FxOperation.created_at < end,
        FxOperation.id.not_in(select(FxTransferRiskPosting.fx_operation_id)))).all()
    for operation, quote in untracked:
        value = quote.scope_json.get("cny_reference_minor") if quote else None
        if quote is None or canonical_hash(quote.scope_json) != quote.scope_hash or type(value) is not int or value <= 0:
            raise DomainError("TRANSFER_DAILY_HISTORY_UNVERIFIED", "当日原外汇付款等值依据需核验，不能忽略后继续占用额度。", 409)
        fx_total += value
        fx_count += 1
    return int(total) + int(tracked) + int(fx_total), int(count) + int(tracked_count) + int(fx_count)


def check_transfer_limits(session, source, payee, amount_minor, *, for_commit=False):
    if type(amount_minor) is not int or not 1 <= amount_minor <= SINGLE_LIMIT_MINOR or source.currency != "CNY":
        raise DomainError("TRANSFER_SINGLE_LIMIT_EXCEEDED", "人民币转账金额须为正整数分，且单笔不超过5万元。", 422)
    if payee is not None:
        _check_recipient(session, source, payee, lock=for_commit)
    if for_commit:
        # Monetary entry points already hold all involved User rows before
        # cash/wallet locks. Rechecking this same owner keeps one quota bucket
        # across funding accounts without introducing a new lock after cash.
        session.scalar(select(User).where(User.id == source.user_id, User.sandbox_id == source.sandbox_id)
            .with_for_update().execution_options(populate_existing=True))
    day, start, end = _day(session, source)
    query = select(TransferDailyUsage).where(TransferDailyUsage.sandbox_id == source.sandbox_id,
        TransferDailyUsage.user_id == source.user_id,
        TransferDailyUsage.business_date == day, TransferDailyUsage.currency == "CNY")
    usage = session.scalar(query.with_for_update().execution_options(populate_existing=True) if for_commit else query)
    if usage is None:
        posted, count = _historical_usage(session, source, day, start, end)
        if for_commit:
            # All monetary paths hold the same owner lock, including cards
            # backed by different funding accounts.
            usage = TransferDailyUsage(sandbox_id=source.sandbox_id, user_id=source.user_id,
                account_id=source.id, business_date=day, currency="CNY", posted_minor=posted, posted_count=count)
            session.add(usage)
            session.flush()
    else:
        posted, count = usage.posted_minor, usage.posted_count
    if posted + amount_minor > DAILY_LIMIT_MINOR:
        raise DomainError("TRANSFER_DAILY_LIMIT_EXCEEDED", "本人当日累计转账将超过10万元，换卡、换账户或拆分不能绕过。", 403)
    if payee is not None:_check_recipient_velocity(session,source,_target_ref(session,payee),amount_minor,day,start,end)
    return {"policy_version": POLICY_VERSION, "currency": "CNY", "business_date": day,
        "time_zone": "Asia/Shanghai", "single_limit_minor": SINGLE_LIMIT_MINOR,
        "daily_limit_minor": DAILY_LIMIT_MINOR, "posted_minor": posted, "posted_count": count,
        "remaining_minor": DAILY_LIMIT_MINOR - posted, "high_value": amount_minor >= HIGH_VALUE_MINOR,
        "requires_step_up": True, "usage": usage if for_commit else None}


def record_transfer_limit_posting(session, source, transfer, checked):
    usage = checked["usage"]
    if usage is None or transfer.source_account_id != source.id or transfer.amount_minor + usage.posted_minor > DAILY_LIMIT_MINOR:
        raise DomainError("TRANSFER_QUOTA_SCOPE_CHANGED", "转账累计限额范围发生变化，整笔未提交。", 409)
    usage.posted_minor += transfer.amount_minor
    usage.posted_count += 1
    session.add(TransferRiskPosting(sandbox_id=source.sandbox_id, user_id=source.user_id, account_id=source.id,
        transfer_id=transfer.id, business_key=transfer.idempotency_key, business_date=checked["business_date"],
        currency="CNY", amount_minor=transfer.amount_minor))
    session.flush()


def check_fx_transfer_limits(session, quote):
    """Call only after lock_quote_resources and original-key replay lookup.

    EXCHANGE is not an outward payment. A TRANSFER consumes the same owner's
    base-CNY bucket as CNY/AA/schedules, using the signed bank quote's ceiling.
    """
    if quote.kind != "TRANSFER":
        return None
    scope = quote.scope_json
    if canonical_hash(scope) != quote.scope_hash or scope.get("kind") != "TRANSFER":
        raise DomainError("FX_QUOTE_INTEGRITY_FAILED", "外汇转账限额依据未通过核验。", 409)
    source = session.get(Account, scope["source"]["bank_account_id"])
    if source is None or (source.sandbox_id, source.user_id, source.currency) != (quote.sandbox_id, quote.user_id, "CNY"):
        raise DomainError("TRANSFER_QUOTA_SCOPE_CHANGED", "外汇转账累计主体发生变化。", 409)
    target_login_id = scope["target"]["login_id"]
    block = session.scalar(select(TransferBlacklist).where(TransferBlacklist.sandbox_id == quote.sandbox_id,
        TransferBlacklist.user_id == quote.user_id, TransferBlacklist.target_ref == "BANK:" + target_login_id).with_for_update())
    if block and block.status == "BLOCKED":
        raise DomainError("TRANSFER_RECIPIENT_BLOCKED", "收款对象受限制，不能换币转账绕过。", 403)
    payees = list(session.scalars(select(Payee).join(InternalPayeeBinding, InternalPayeeBinding.payee_id == Payee.id).where(
        Payee.sandbox_id == quote.sandbox_id, Payee.user_id == quote.user_id,
        InternalPayeeBinding.recipient_login_account_id == target_login_id)))
    for payee in payees:
        _check_recipient(session, source, payee, lock=True)
    checked = check_transfer_limits(session, source, None, scope.get("cny_reference_minor"), for_commit=True)
    day,start,end=_day(session,source)
    _check_recipient_velocity(session,source,'BANK:'+target_login_id,scope['cny_reference_minor'],day,start,end)
    checked["quote_scope_hash"] = quote.scope_hash
    checked["base_amount_minor"] = scope["cny_reference_minor"]
    return checked


def record_fx_transfer_limit_posting(session, operation, checked):
    if checked is None:
        return
    usage = checked["usage"]
    # The checked value is immutable within the locked quote transaction.
    from .fx_models import FxQuote
    quote = session.get(FxQuote, operation.quote_id)
    amount = quote.scope_json.get("cny_reference_minor") if quote else None
    if (quote is None or quote.scope_hash != checked["quote_scope_hash"] or canonical_hash(quote.scope_json) != quote.scope_hash
            or type(amount) is not int or amount != checked["base_amount_minor"]
            or (operation.sandbox_id, operation.user_id) != (usage.sandbox_id, usage.user_id)
            or usage.posted_minor + amount > DAILY_LIMIT_MINOR):
        raise DomainError("TRANSFER_QUOTA_SCOPE_CHANGED", "外汇等值累计范围发生变化。", 409)
    usage.posted_minor += amount
    usage.posted_count += 1
    session.add(FxTransferRiskPosting(sandbox_id=operation.sandbox_id, user_id=operation.user_id,
        fx_operation_id=operation.id, business_key=operation.original_key, business_date=checked["business_date"],
        base_currency="CNY", base_amount_minor=amount, quote_scope_hash=quote.scope_hash))
    session.flush()
