"""Credit receipt proof: actual domain records and exact balanced posting legs."""
from sqlalchemy import select

from .credit_models import (CreditApplication, CreditCard, CreditFeeCharge, CreditInstallment, CreditObligation,
    CreditOperation, CreditRepayment, CreditRepaymentAllocation, CreditFeeAllocation, PaymentJournal, PaymentLedgerEntry, PaymentRecord)
from .models import Account, Transaction
from .planning import canonical_hash


CREDIT_WRITE_KINDS = frozenset({"CREDIT_REPAY", "CREDIT_INSTALLMENT", "CREDIT_SETTLE_INSTALLMENT",
    "CREDIT_APPLY", "CREDIT_LIMIT_REQUEST", "CREDIT_ACTIVATE"})


def credit_payment_posting_integrity(session,principal,payment):
    """Read proof for direct merchant purchases and authorization captures."""
    if not _owned(payment,principal) or payment.instrument_type!="CREDIT" or payment.currency not in {"CNY","USD"} \
            or type(payment.amount_minor) is not int or payment.amount_minor<=0 or payment.status not in {"POSTED","REFUNDED"}:return False
    receipt=payment.receipt_json or {};card=session.get(CreditCard,payment.instrument_id[7:]) if payment.instrument_id.startswith("credit:") else None
    if not _owned(card,principal) or card.credit_account_id!=payment.credit_account_id:return False
    if receipt.get("schema_version")!="payment-receipt-v1" or receipt.get("status")!="POSTED" or receipt.get("ledger_balanced") is not True \
            or receipt.get("cash_debit_minor")!=0 or any(type(receipt.get(key)) is not int or receipt[key]<0 for key in
                ("amount_minor","credit_debit_minor","credit_balance_used_minor","base_exposure_minor","quoted_base_exposure_minor")):return False
    if any(receipt.get(key)!=getattr(payment,key) for key in ("instrument_id","instrument_type","account_id","credit_account_id","amount_minor","currency","merchant_ref","authorization_ref","operation_key")) \
            or receipt.get("payment_id")!=payment.id or receipt["credit_debit_minor"]+receipt["credit_balance_used_minor"]!=payment.amount_minor:return False
    journal=session.get(PaymentJournal,payment.journal_id)
    if not _owned(journal,principal) or journal.kind!="CREDIT_PURCHASE" or journal.operation_key!=payment.operation_key \
            or journal.request_hash!=payment.request_hash or journal.result_json!=receipt:return False
    obligation=session.scalar(select(CreditObligation).where(CreditObligation.payment_id==payment.id))
    return bool(obligation and obligation.credit_account_id==payment.credit_account_id and obligation.currency==payment.currency
        and obligation.amount_minor==receipt["credit_debit_minor"] and _journal_matches(session,principal,journal.id,
            [("CREDIT:"+payment.credit_account_id,payment.currency,-payment.amount_minor),("MERCHANT:"+payment.merchant_ref,payment.currency,payment.amount_minor)]))


def credit_refund_posting_integrity(session,principal,refund,payment):
    if not _owned(refund,principal) or refund.payment_id!=payment.id or payment.status!="REFUNDED" \
            or not credit_payment_posting_integrity(session,principal,payment):return False
    receipt=refund.receipt_json or {}
    if receipt.get("schema_version")!="payment-refund-receipt-v1" or receipt.get("status")!="POSTED" \
            or receipt.get("ledger_balanced") is not True or receipt.get("cash_credit_minor")!=0 \
            or any(type(receipt.get(key)) is not int or receipt[key]<0 for key in ("amount_minor","credit_reduction_minor","credit_balance_added_minor")) \
            or receipt["credit_reduction_minor"]+receipt["credit_balance_added_minor"]!=payment.amount_minor:return False
    if (receipt.get("refund_id"),receipt.get("payment_id"),receipt.get("currency"),receipt.get("amount_minor"),receipt.get("instrument_id"),receipt.get("credit_account_id")) != \
            (refund.id,payment.id,payment.currency,payment.amount_minor,payment.instrument_id,payment.credit_account_id):return False
    journal=session.scalar(select(PaymentJournal).where(PaymentJournal.sandbox_id==principal.sandbox_id,PaymentJournal.user_id==principal.user_id,
        PaymentJournal.operation_key==refund.operation_key))
    return bool(journal and journal.kind=="CREDIT_REFUND" and journal.request_hash==refund.request_hash and journal.result_json==receipt
        and _journal_matches(session,principal,journal.id,[("CREDIT:"+payment.credit_account_id,payment.currency,payment.amount_minor),
            ("MERCHANT:"+payment.merchant_ref,payment.currency,-payment.amount_minor)]))


def credit_fee_posting_integrity(session,principal,fee):
    item=session.get(CreditInstallment,fee.installment_id)
    debt=session.get(CreditObligation,item.obligation_id) if item else None
    payment=session.get(PaymentRecord,debt.payment_id) if debt else None
    if not payment or not credit_payment_posting_integrity(session,principal,payment) or fee.credit_account_id!=payment.credit_account_id \
            or fee.currency!=payment.currency or fee.amount_minor!=item.fee_minor or type(fee.amount_minor) is not int or fee.amount_minor<=0:return False
    operation=session.scalar(select(CreditOperation).where(CreditOperation.sandbox_id==principal.sandbox_id,CreditOperation.user_id==principal.user_id,
        CreditOperation.operation_key==item.operation_key,CreditOperation.kind=="INSTALLMENT"))
    if not operation or operation.request_hash!=item.request_hash or operation.result_json.get("installment_id")!=item.id \
            or operation.result_json.get("pricing")!=fee.policy_json or operation.result_json.get("fee_minor")!=fee.amount_minor:return False
    journal=session.get(PaymentJournal,fee.journal_id)
    return bool(journal and journal.kind=="CREDIT_INSTALLMENT_FEE" and journal.request_hash==item.request_hash
        and journal.operation_key=="credit-fee:"+canonical_hash({"key":item.operation_key})
        and _journal_matches(session,principal,journal.id,[("CREDIT:"+fee.credit_account_id,fee.currency,-fee.amount_minor),
            ("FEE_REVENUE:INSTALLMENT",fee.currency,fee.amount_minor)]))


def _owned(row, parent):
    return row is not None and (row.sandbox_id, row.user_id) == (parent.sandbox_id, parent.user_id)


def _journal_matches(session, parent, journal_id, expected):
    journal = session.get(PaymentJournal, journal_id)
    if not _owned(journal, parent): return False
    rows = list(session.scalars(select(PaymentLedgerEntry).where(PaymentLedgerEntry.journal_id == journal.id)))
    return all(_owned(row, parent) for row in rows) and sorted((row.account_ref,row.currency,row.delta_minor) for row in rows) == sorted(expected)


def _repayment_matches(session, parent, receipt, args, expected_operation_key):
    repaid = session.get(CreditRepayment, receipt.get("repayment_id"))
    if not _owned(repaid, parent) or repaid.operation_key!=expected_operation_key: return False
    recorded = repaid.receipt_json
    if any(receipt.get(key) != recorded.get(key) for key in ("repayment_id", "credit_account_id", "amount_minor", "currency",
            "source_instrument_id", "account_id", "cash_currency", "cash_debit_minor", "fx_quote_id", "journal_id", "allocations")): return False
    if recorded.get("schema_version")!="credit-repayment-v1" or recorded.get("status")!="POSTED" \
            or args.get("card_reference")!="credit:"+str(recorded.get("card_id")):return False
    source = args.get("source_snapshot") or {}
    if (source.get("sandbox_id"), source.get("user_id"), source.get("instrument_type")) != (parent.sandbox_id,parent.user_id,"DEBIT") \
            or recorded.get("source_instrument_id") != source.get("instrument_id") or recorded.get("currency") != args.get("currency") \
            or recorded.get("amount_minor") != args.get("amount_minor") or recorded.get("ledger_balanced") is not True: return False
    amount, cash = recorded["amount_minor"], recorded["cash_debit_minor"]
    entries = [(source.get("balance_ref"),recorded["cash_currency"],-cash),("CREDIT:"+repaid.credit_account_id,recorded["currency"],amount)]
    if recorded["cash_currency"] != recorded["currency"]:
        from .credit_models import CreditFxQuote
        quote = session.get(CreditFxQuote, recorded.get("fx_quote_id"))
        if not _owned(quote,parent) or quote.amount_minor != amount or quote.base_exposure_minor != cash \
                or quote.scope_hash != canonical_hash(quote.source_json) or (recorded["cash_currency"],recorded["currency"]) != ("CNY","USD"): return False
        entries += [("FX_CLEARING:CNY","CNY",cash),("FX_CLEARING:USD","USD",-amount)]
    elif amount != cash: return False
    if not _journal_matches(session,parent,recorded["journal_id"],entries): return False
    allocations = list(session.scalars(select(CreditRepaymentAllocation).where(CreditRepaymentAllocation.repayment_id == repaid.id)))
    fees = list(session.scalars(select(CreditFeeAllocation).where(CreditFeeAllocation.repayment_id == repaid.id)))
    if sum(row.amount_minor for row in allocations+fees) != amount: return False
    for row in allocations:
        debt = session.get(CreditObligation,row.obligation_id)
        if not debt or debt.credit_account_id != repaid.credit_account_id or debt.currency != recorded["currency"] or row.amount_minor<=0:return False
    for row in fees:
        fee = session.get(CreditFeeCharge,row.fee_charge_id)
        if not fee or fee.credit_account_id != repaid.credit_account_id or fee.currency != recorded["currency"] or row.amount_minor<=0:return False
    if recorded["cash_currency"] == "CNY":
        account = session.get(Account,source.get("account_id"))
        tx = list(session.scalars(select(Transaction).where(Transaction.sandbox_id == parent.sandbox_id,
            Transaction.operation_ref == "credit-repayment:"+repaid.id)))
        if not _owned(account,parent) or len(tx)!=1 or (tx[0].account_id,tx[0].amount_minor,tx[0].source_kind)!=(account.id,-cash,"CREDIT_REPAYMENT"):return False
    else:
        from .fx_models import FxWallet
        from .fx_banking import usd_ref
        wallet=session.get(FxWallet,source.get("wallet_id"))
        if not _owned(wallet,parent) or source.get("balance_ref")!=usd_ref(wallet.id):return False
    return True


def completed_credit_receipt_integrity(session,parent,op,child,call,confirmation):
    """Historical proof tolerates later legitimate debt/status changes, not forgery."""
    from .credit_runtime import ACTION_TOOLS, CONTROL_ACTIONS
    from .models import Confirmation, Task, ToolCall
    if op.action_kind not in CREDIT_WRITE_KINDS|set(CONTROL_ACTIONS):return False
    if not all(getattr(row,"id",None) for row in (child,call,confirmation)) or session.get(Task,child.id) is not child \
            or session.get(ToolCall,call.id) is not call or session.get(Confirmation,confirmation.id) is not confirmation:return False
    if not all((child,call,confirmation)) or (child.user_id,child.sandbox_id)!=(parent.user_id,parent.sandbox_id) \
            or child.state!="SUCCEEDED" or call.status!="SUCCESS" or call.task_id!=child.id or call.sandbox_id!=parent.sandbox_id \
            or call.idempotency_key!=op.idempotency_key or call.result_json!=child.result_json \
            or confirmation.status!="CONSUMED" or confirmation.consumed_at is None \
            or (confirmation.user_id,confirmation.sandbox_id,confirmation.task_id)!=(parent.user_id,parent.sandbox_id,child.id) \
            or confirmation.plan_hash!=child.plan_hash or canonical_hash(child.plan_json)!=child.plan_hash:return False
    nodes=child.plan_json.get("nodes") or []
    if len(nodes)!=1:return False
    node=nodes[0];kind=op.action_kind;args=node.get("args") or {};tool=ACTION_TOOLS[kind]
    if node.get("action_kind")!=kind or node.get("tool_name")!=tool or call.tool_name!=tool \
            or confirmation.action_type!=tool or confirmation.node_key!=node.get("node_id") \
            or confirmation.view_hash!=canonical_hash({"plan_hash":child.plan_hash,"action":kind,"args":args}):return False
    if call.request_hash!=canonical_hash({"task_id":child.id,"confirmation_id":confirmation.id,"plan_hash":child.plan_hash}):return False
    proof=session.scalar(select(CreditOperation).where(CreditOperation.sandbox_id==parent.sandbox_id,
        CreditOperation.user_id==parent.user_id,CreditOperation.operation_key==op.idempotency_key))
    if not proof or {**proof.result_json,"action":kind,"bank_state_changed":True}!=child.result_json:return False
    result=child.result_json
    if kind in {"CREDIT_REPAY","CREDIT_SETTLE_INSTALLMENT"}:
        if proof.kind != ("REPAY" if kind=="CREDIT_REPAY" else "SETTLE_INSTALLMENT"):return False
        if kind=="CREDIT_SETTLE_INSTALLMENT" and (result.get("installment_id")!=args.get("installment_id") or result.get("status")!="SETTLED"):return False
        key=op.idempotency_key if kind=="CREDIT_REPAY" else "settle-repay:"+canonical_hash({"key":op.idempotency_key})
        return _repayment_matches(session,parent,result,args,key)
    if kind=="CREDIT_INSTALLMENT":
        item=session.get(CreditInstallment,result.get("installment_id"));payment=session.get(PaymentRecord,args.get("payment_id"))
        debt=session.get(CreditObligation,item.obligation_id) if item else None
        if proof.kind!="INSTALLMENT" or not item or item.operation_key!=op.idempotency_key or item.request_hash!=proof.request_hash \
                or not _owned(payment,parent) or not debt or debt.payment_id!=payment.id \
                or item.credit_account_id!=payment.credit_account_id or item.currency!=result.get("currency") \
                or (item.principal_minor,item.periods,item.fee_minor,item.schedule_json)!=(result.get("principal_minor"),args.get("periods"),result.get("fee_minor"),result.get("schedule")) \
                or sum(row["principal_minor"] for row in item.schedule_json)!=item.principal_minor \
                or sum(row["fee_minor"] for row in item.schedule_json)!=item.fee_minor:return False
        fees=list(session.scalars(select(CreditFeeCharge).where(CreditFeeCharge.installment_id==item.id)))
        if item.fee_minor==0:return not fees
        return len(fees)==1 and fees[0].amount_minor==item.fee_minor and fees[0].policy_json==result.get("pricing") and _journal_matches(session,parent,fees[0].journal_id,
            [("CREDIT:"+item.credit_account_id,item.currency,-item.fee_minor),("FEE_REVENUE:INSTALLMENT",item.currency,item.fee_minor)])
    if kind in {"CREDIT_APPLY","CREDIT_LIMIT_REQUEST"}:
        application=session.get(CreditApplication,result.get("application_id"))
        if args.get('application_credit_account_id') is not None and (
                kind != 'CREDIT_APPLY' or not application
                or application.credit_account_id != args['application_credit_account_id']
                or proof.request_hash != canonical_hash({'kind': 'CARD', 'card_reference': None,
                    'requested_limit_minor': None, 'credit_account_id': args['application_credit_account_id']})):
            return False
        return bool(proof.kind=="APPLICATION" and _owned(application,parent) and application.operation_key==op.idempotency_key
            and application.request_hash==proof.request_hash and application.kind==("CARD" if kind=="CREDIT_APPLY" else "LIMIT")
            and application.requested_limit_minor==args.get("requested_limit_minor") and result.get("status")=="PENDING_REVIEW")
    card=session.get(CreditCard,result.get("card_id"))
    expected_action="ACTIVATE" if kind=="CREDIT_ACTIVATE" else CONTROL_ACTIONS[kind]
    return bool(proof.kind=="CARD_CONTROL" and _owned(card,parent) and args.get("card_reference")=="credit:"+card.id
        and result.get("schema_version")=="credit-card-control-v1"
        and result.get("card_number")==card.card_number and result.get("instrument_type")=="CREDIT"
        and card.version>=result.get("version",2**53) and result.get("version")==args.get("expected_card_version",-1)+1
        and confirmation.target_id==card.id and proof.request_hash==canonical_hash({"kind":"CARD_CONTROL","card_id":card.id,
            "action":expected_action,"limit_minor":args.get("limit_minor")}))
