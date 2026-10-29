"""Private source-read permission for the next step of an unchanged plan.

Not execution authorization, a replan permission, a bank grant or a JSON token.
Every earlier write must still have a real matched receipt and posting proof.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib,hmac,secrets

from sqlalchemy import select

from .contracts import DomainError
from .intelligence import READ_KINDS,action_signature
from .models import Task,ToolCall,Confirmation,Transaction
from .planning import canonical_hash
from .runtime_models import RuntimeRecord,RuntimeOperation,PlanSnapshot

_AUTHORITY=object();_KEY=secrets.token_bytes(32)
SUPPORTED_COMPLETED_WRITE_KINDS=frozenset({'TRANSFER','SUBSCRIPTION_START','SUBSCRIPTION_RENEW',
    'WEALTH_BUY','WEALTH_REDEEM','FX_TRANSFER','FX_EXCHANGE',
    'CREDIT_REPAY','CREDIT_INSTALLMENT','CREDIT_SETTLE_INSTALLMENT','CREDIT_APPLY','CREDIT_LIMIT_REQUEST','CREDIT_ACTIVATE','AA_PAY',
    'OCCASION_MEMBER_SET','OCCASION_SET','LIFE_PLAN_AUTHORIZE','LIFE_PLAN_CANCEL','MERCHANT_PURCHASE','OWN_ACCOUNT_TRANSFER'})


def _deny():raise DomainError('SEMANTIC_PHASE_SOURCE_CHANGED','原计划的已完成步骤或当前确认范围已变化，请核对原回执。',409)


def validate_semantic_phase_capabilities(proposal):
    """Reject unsupported native combinations before any first-step grant.

    This is a registered domain proof capability, not natural-language routing.
    All single operations remain governed by their existing domain contracts.
    """
    if any(action.kind not in READ_KINDS and action.kind not in SUPPORTED_COMPLETED_WRITE_KINDS
            for action in proposal.actions[:-1]):
        raise DomainError('SEMANTIC_COMBINATION_NOT_SUPPORTED',
            '这些服务暂不支持放在同一次办理中。请分别提出需求，我会逐项处理并分别请你确认。',409)


def completed_write_receipt_integrity(session,parent,op,child,call,confirmation):
    """Match real financial/state evidence, not merely existence of a call."""
    if call.sandbox_id!=parent.sandbox_id or call.result_json!=child.result_json or call.idempotency_key!=op.idempotency_key:return False
    node=next((row for row in child.plan_json.get('nodes',[]) if row.get('node_id')==confirmation.node_key),None)
    if not node or node.get('tool_name')!=confirmation.action_type or call.tool_name!=confirmation.action_type:return False
    if str(node.get('tool_name','')).startswith('credit.'):
        from .credit_receipts import completed_credit_receipt_integrity
        return completed_credit_receipt_integrity(session,parent,op,child,call,confirmation)
    if op.action_kind=='AA_PAY':
        from .aa_participant_payments import completed_aa_payment_receipt
        return completed_aa_payment_receipt(session,parent,op,child,call,confirmation) is not None
    if op.action_kind=='OWN_ACCOUNT_TRANSFER':
        from .own_account_transfers import completed_own_account_transfer_receipt
        return completed_own_account_transfer_receipt(session,parent,op,child,call,confirmation) is not None
    if op.action_kind in {'OCCASION_MEMBER_SET','OCCASION_SET','LIFE_PLAN_AUTHORIZE','LIFE_PLAN_CANCEL','MERCHANT_PURCHASE'}:
        from .life_write_receipts import completed_life_write_receipt_integrity
        from .services import Principal
        return completed_life_write_receipt_integrity(session,Principal(parent.user_id,parent.sandbox_id),child,kind=op.action_kind)
    result=child.result_json;slots=(op.args_json or {}).get('slots') or {}
    if op.action_kind=='SUBSCRIPTION_CANCEL' or op.action_kind=='SUBSCRIPTION_START' and node.get('effect_kind')=='RESTORE_AUTORENEW':
        from .subscription_write_receipts import completed_subscription_setting_receipt
        return completed_subscription_setting_receipt(session,parent,op,child,call,confirmation) is not None
    if op.action_kind=='TRANSFER':
        from .models import Transfer
        from .internal_banking import settlement_integrity
        transfer=session.get(Transfer,result.get('transfer_id'))
        return bool(transfer and (transfer.user_id,transfer.sandbox_id)==(parent.user_id,parent.sandbox_id)
            and transfer.status=='POSTED' and transfer.idempotency_key==op.idempotency_key
            and transfer.amount_minor==node.get('amount_minor')==slots.get('amount_minor')==result.get('amount_minor')
            and transfer.source_account_id==node.get('source_account_id') and transfer.payee_id==node.get('payee_id')==confirmation.target_id
            and settlement_integrity(session,transfer).get('balanced') is True)
    if op.action_kind in {'SUBSCRIPTION_START','SUBSCRIPTION_RENEW'}:
        from .models import SubscriptionPayment,SubscriptionLedgerEntry
        paid=session.get(SubscriptionPayment,result.get('payment_id'))
        if not paid or (paid.user_id,paid.sandbox_id,paid.task_id)!=(parent.user_id,parent.sandbox_id,child.id) \
                or paid.status!='POSTED' or paid.idempotency_key!=op.idempotency_key \
                or paid.amount_minor!=node.get('total_amount_minor') or result.get('charged_minor')!=paid.amount_minor \
                or paid.months!=node.get('months') or paid.auto_renew is not node.get('auto_renew') \
                or paid.product_version!=node.get('expected_product_version') or paid.product_id!=node.get('product_id'):return False
        ledger=list(session.scalars(select(SubscriptionLedgerEntry).where(SubscriptionLedgerEntry.payment_id==paid.id)))
        expected=sorted([(paid.account_id,-paid.amount_minor,'CNY'),('subscription-merchant:'+paid.product_id,paid.amount_minor,'CNY')])
        if sorted((row.account_ref,row.delta_minor,row.currency) for row in ledger)!=expected or any(row.sandbox_id!=parent.sandbox_id for row in ledger):return False
        tx=session.get(Transaction,result.get('transaction_id'))
        return bool(tx and tx.account_id==paid.account_id and tx.amount_minor==-paid.amount_minor and tx.source_kind=='SUBSCRIPTION')
    if op.action_kind in {'WEALTH_BUY','WEALTH_REDEEM'}:
        from .models import Account,InvestmentOrder,InvestmentLedgerEntry
        order=session.get(InvestmentOrder,result.get('order_id'))
        expected_type='BUY' if op.action_kind=='WEALTH_BUY' else 'REDEEM'
        if not order or (order.user_id,order.sandbox_id)!=(parent.user_id,parent.sandbox_id) \
                or order.status!='POSTED' or order.idempotency_key!=op.idempotency_key \
                or order.order_type!=expected_type or node.get('order_type')!=expected_type or result.get('order_type')!=expected_type \
                or order.product_id!=node.get('product_id') or confirmation.target_id!=order.product_id \
                or order.amount_minor!=node.get('amount_minor') or result.get('amount_minor')!=order.amount_minor \
                or order.units!=node.get('units') or order.amount_minor!=order.units*node.get('unit_price_minor',0) \
                or slots.get('amount_minor') is not None and slots['amount_minor']!=order.amount_minor \
                or slots.get('units') is not None and slots['units']!=order.units:return False
        account=session.get(Account,order.account_id)
        if not account or (account.user_id,account.sandbox_id)!=(parent.user_id,parent.sandbox_id):return False
        delta=-order.amount_minor if expected_type=='BUY' else order.amount_minor
        ledger=list(session.scalars(select(InvestmentLedgerEntry).where(InvestmentLedgerEntry.order_id==order.id)))
        expected=sorted([(order.account_id,delta,'CNY'),('INVESTMENT:'+order.product_id,-delta,'CNY')])
        if sorted((row.account_ref,row.delta_minor,row.currency) for row in ledger)!=expected \
                or any(row.sandbox_id!=parent.sandbox_id for row in ledger):return False
        txs=list(session.scalars(select(Transaction).where(Transaction.operation_ref=='investment:'+order.id)))
        return len(txs)==1 and (txs[0].sandbox_id,txs[0].account_id,txs[0].source_kind,txs[0].amount_minor)==(parent.sandbox_id,order.account_id,'INVESTMENT',delta)
    if op.action_kind in {'FX_TRANSFER','FX_EXCHANGE'}:
        from .fx_models import FxOperation,FxQuote
        from .fx_banking import fx_operation_integrity
        transaction=session.get(FxOperation,result.get('fx_operation_id') or result.get('operation_id'))
        quote=session.get(FxQuote,node.get('quote_id'))
        return bool(transaction and (transaction.user_id,transaction.sandbox_id)==(parent.user_id,parent.sandbox_id)
            and transaction.status=='POSTED' and transaction.task_id==child.id and transaction.original_key==op.idempotency_key
            and quote and (quote.user_id,quote.sandbox_id)==(parent.user_id,parent.sandbox_id)
            and transaction.quote_id==quote.id==confirmation.target_id
            and quote.scope_hash==node.get('quote_scope_hash')==canonical_hash(quote.scope_json)
            and transaction.kind==quote.kind==('TRANSFER' if op.action_kind=='FX_TRANSFER' else 'EXCHANGE')
            and transaction.requested_amount_minor==quote.requested_amount_minor==slots.get('amount_minor')
            and transaction.requested_currency==quote.requested_currency==slots.get('currency')
            and transaction.debit_minor==quote.debit_minor==result.get('debit_minor')
            and transaction.credit_minor==quote.credit_minor==result.get('credit_minor')
            and transaction.source_currency==quote.source_currency==result.get('source_currency')
            and transaction.target_currency==quote.target_currency==result.get('target_currency')
            and transaction.source_ref==quote.scope_json['source']['account_ref']
            and transaction.target_ref==quote.scope_json['target']['account_ref']
            and fx_operation_integrity(session,transaction).get('balanced') is True)
    # Other registered domains retain their own single-operation execution
    # gates. Do not pretend a generic audit event proves a financial effect;
    # native multi-write continuation fails closed until a typed proof exists.
    return False


def _payload(session,parent,target,phase):
    from .conversation_provenance import current_plan_completed_write,validate_dialogue_actor_epoch
    from .permissions import require_banking_actor
    from .services import Principal
    record=session.get(RuntimeRecord,parent.id)
    require_banking_actor(session,Principal(parent.user_id,parent.sandbox_id,family_context=record.metadata_json.get('family_context_origin') if record else None))
    if phase not in {'PREPARE_NEXT_STEP','CONFIRM_NEXT_STEP'} or not record or record.kind!='PARENT' \
            or parent.state not in {'EXECUTING','AWAITING_CONFIRMATION','PLANNING','NEED_CLARIFICATION'}:_deny()
    validate_dialogue_actor_epoch(session,parent,record.metadata_json)
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==record.plan_version))
    if not snapshot or snapshot.plan_hash!=parent.plan_hash or canonical_hash(snapshot.plan_json)!=parent.plan_hash \
            or canonical_hash(parent.plan_json)!=parent.plan_hash:_deny()
    ops=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,RuntimeOperation.active==1).order_by(RuntimeOperation.ordinal)))
    if target not in ops or target.plan_version!=record.plan_version or target.status not in {'BLOCKED','PENDING','WAITING_CONFIRMATION'}:_deny()
    nodes={row.get('node_id'):row for row in snapshot.plan_json.get('nodes',[])}
    if len(nodes)!=len(snapshot.plan_json.get('nodes',[])) or set(nodes)!={op.operation_key for op in ops}:_deny()
    completed=[];identities=[]
    for op in ops:
        node=nodes.get(op.operation_key)
        if op.sandbox_id!=parent.sandbox_id or op.plan_version!=record.plan_version or not node \
                or node.get('kind')!=op.action_kind or canonical_hash(node.get('args'))!=canonical_hash(op.args_json) \
                or canonical_hash(action_signature(op.args_json))!=op.fingerprint \
                or node.get('depends_on')!=op.depends_on_json:_deny()
        child=session.get(Task,op.child_task_id) if op.child_task_id else None
        if op.status in {'UNKNOWN','RUNNING','AUTHORIZED'} or child and child.state in {'UNKNOWN','EXECUTING','AUTHORIZED'}:_deny()
        if op.action_kind not in READ_KINDS and op.status=='SUCCEEDED':
            if op.ordinal>=target.ordinal or not current_plan_completed_write(session,parent,op):_deny()
            completed.append(op.id)
        identities.append({'id':op.id,'status':op.status,'args_hash':canonical_hash(op.args_json),'child_id':op.child_task_id,
            'child_version':child.version if child else None,'child_plan_hash':child.plan_hash if child else None,
            'result_hash':canonical_hash(child.result_json) if child else None})
    if not completed:_deny()
    by_key={op.operation_key:op for op in ops}
    if any(key not in by_key or by_key[key].status!='SUCCEEDED' for key in target.depends_on_json):_deny()
    if phase=='PREPARE_NEXT_STEP':
        if target.child_task_id is not None:_deny()
        confirmation=None
    else:
        child=session.get(Task,target.child_task_id) if target.child_task_id else None
        if target.status!='WAITING_CONFIRMATION' or not child or child.state!='AWAITING_CONFIRMATION' \
                or (child.user_id,child.sandbox_id)!=(parent.user_id,parent.sandbox_id) or canonical_hash(child.plan_json)!=child.plan_hash:_deny()
        from .authentication import as_utc
        from .models import utcnow
        confirmations=list(session.scalars(select(Confirmation).where(Confirmation.task_id==child.id,Confirmation.status=='PENDING',
            Confirmation.user_id==parent.user_id,Confirmation.sandbox_id==parent.sandbox_id)))
        if len(confirmations)!=1 or confirmations[0].consumed_at is not None or confirmations[0].plan_hash!=child.plan_hash \
                or as_utc(confirmations[0].expires_at)<=utcnow():_deny()
        confirmation={'id':confirmations[0].id,'plan_hash':confirmations[0].plan_hash,'view_hash':confirmations[0].view_hash}
    return {'phase':phase,'parent_id':parent.id,'owner':[parent.user_id,parent.sandbox_id],'parent_version':parent.version,
        'snapshot_id':snapshot.id,'plan_version':record.plan_version,'plan_hash':parent.plan_hash,
        'proposal_hash':canonical_hash(snapshot.proposal_json),'semantic_binding_hash':canonical_hash(record.metadata_json.get('cloud_semantic_intent')),
        'input_revision':record.metadata_json.get('input_revision',0),'target_id':target.id,'completed_ids':completed,
        'operations':identities,'confirmation':confirmation}


def _seal(payload):return hmac.new(_KEY,canonical_hash(payload).encode(),hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class VerifiedSemanticPhaseContext:
    authority:object
    payload:dict
    seal:str


def build_semantic_phase_context(session,parent,target,phase):
    payload=_payload(session,parent,target,phase)
    return VerifiedSemanticPhaseContext(_AUTHORITY,deepcopy(payload),_seal(payload))


def verified_semantic_phase(session,parent,context):
    if not isinstance(context,VerifiedSemanticPhaseContext) or context.authority is not _AUTHORITY:_deny()
    if not hmac.compare_digest(_seal(context.payload),context.seal):_deny()
    target=session.get(RuntimeOperation,context.payload.get('target_id'))
    if not target or _payload(session,parent,target,context.payload.get('phase'))!=context.payload:_deny()
    return context.payload
