"""Actual posted recipient credits for a STATUS answer, never requested slots."""
from decimal import Decimal

from sqlalchemy import select

from .models import Task,Transfer,Payee,Card,Transaction
from .runtime_models import RuntimeOperation
from .planning import canonical_hash
from .redaction import redact_credentials


def _receipt_card(session,card_id,account_id,user_id,sandbox_id,currency,expected_tail=None,*,posted_at=None):
    """Historical instrument identity, not today's availability or default card.

    A locked/lost card can still be the instrument on an old posted receipt.
    Never resolve a missing instrument from a current default or a card count.
    """
    from .card_identifiers import card_identifier_view
    card=session.get(Card,card_id) if isinstance(card_id,str) else None
    if card is None or (card.user_id,card.sandbox_id)!=(user_id,sandbox_id):return None
    if card.account_id!=account_id:
        # The documented one-time split moved the old secondary card, not its
        # historical postings. Only that same-owner migration can prove an old
        # binding; today's default card or a timestamp-free alias cannot.
        from .credit_models import CardAccountBindingHistory,PaymentUpgradeRecord
        if posted_at is None:return None
        history=list(session.scalars(select(CardAccountBindingHistory).where(
            CardAccountBindingHistory.card_id==card.id,CardAccountBindingHistory.user_id==user_id,
            CardAccountBindingHistory.sandbox_id==sandbox_id,CardAccountBindingHistory.previous_account_id==account_id,
            CardAccountBindingHistory.current_account_id==card.account_id,CardAccountBindingHistory.effective_at>=posted_at)))
        if len(history)!=1:return None
        migration=session.get(PaymentUpgradeRecord,history[0].migration_id)
        if migration is None or (migration.user_id,migration.sandbox_id)!=(user_id,sandbox_id) \
                or (migration.result_json or {}).get('primary_account_id')!=account_id \
                or (migration.result_json or {}).get('secondary_account_id')!=card.account_id \
                or card.id not in (migration.result_json or {}).get('debit_card_ids',[]):return None
    identity=card_identifier_view(session,card) or {}
    tail=identity.get('display_last4')
    if not isinstance(tail,str) or len(tail)!=4 or any(c not in '0123456789abcdefABCDEF' for c in tail):return None
    if expected_tail is not None and tail!=expected_tail:return None
    return {'instrument_type':'DEBIT','display_last4':tail,'currency':currency}


def posted_transfer_card_facts(session,principal,transfer,result,settlement):
    """Project masked cards from the actual posting, not unexecuted plan slots.

    Legacy postings without a card-linked statement retain their original
    amount/status answer. They are not retrospectively assigned today's card.
    A present but contradictory card context invalidates the richer receipt.
    """
    context=result.get('source_card_context')
    if context is None:return {}
    if not isinstance(context,dict):return None
    rows=list(session.scalars(select(Transaction).where(Transaction.sandbox_id==principal.sandbox_id,
        Transaction.account_id==transfer.source_account_id,Transaction.source_kind=='TRANSFER',
        Transaction.operation_ref=='transfer:'+transfer.id)))
    if len(rows)!=1 or rows[0].amount_minor!=-transfer.amount_minor:return None
    if rows[0].card_id is None:return {}  # Existing pre-card posting contract.
    if rows[0].card_id!=context.get('card_id') or context.get('account_id')!=transfer.source_account_id:return None
    source=_receipt_card(session,rows[0].card_id,transfer.source_account_id,principal.user_id,
        principal.sandbox_id,'CNY',context.get('display_last4'),posted_at=transfer.created_at)
    if source is None:return None
    facts={'source_card':source}
    if settlement is not None:
        credit=list(session.scalars(select(Transaction).where(Transaction.sandbox_id==settlement.receiver_sandbox_id,
            Transaction.account_id==settlement.receiver_account_id,Transaction.source_kind=='TRANSFER',
            Transaction.operation_ref=='transfer:'+transfer.id)))
        if len(credit)!=1 or credit[0].amount_minor!=settlement.amount_minor:return None
        if credit[0].card_id is not None:
            target=_receipt_card(session,credit[0].card_id,settlement.receiver_account_id,
                settlement.receiver_user_id,settlement.receiver_sandbox_id,settlement.currency,posted_at=transfer.created_at)
            if target is None:return None
            facts['recipient_card']=target
    return facts


def posted_fx_card_facts(session,principal,posted):
    """FX debit and credit keep distinct currencies and original quote scope."""
    from .fx_models import FxQuote
    from .fx_banking import quote_public
    quote=session.get(FxQuote,posted.quote_id) if posted.quote_id else None
    if quote is None:return {}
    original=(posted.receipt_json or {}).get('fx_quote')
    if original is None:return {}  # Legacy receipt without original card scope.
    scope=quote.scope_json
    if not isinstance(scope,dict) or canonical_hash(scope)!=quote.scope_hash:return None
    if not isinstance(original,dict) or quote.kind!=posted.kind:return None
    try:public=quote_public(quote)
    except (KeyError,TypeError,AttributeError,ValueError):return None
    if canonical_hash(original)!=canonical_hash(public):return None
    if (quote.user_id,quote.sandbox_id,quote.source_currency,quote.target_currency,quote.debit_minor,quote.credit_minor)!= \
            (principal.user_id,principal.sandbox_id,posted.source_currency,posted.target_currency,posted.debit_minor,posted.credit_minor):return None
    facts={}
    for side,key,account_ref,user_id,sandbox_id,currency in (
            ('source','source_card',posted.source_ref,principal.user_id,principal.sandbox_id,posted.source_currency),
            ('target','recipient_card',posted.target_ref,posted.receiver_user_id,posted.receiver_sandbox_id,posted.target_currency)):
        entry=scope.get(side)
        if not isinstance(entry,dict) or (entry.get('account_ref'),entry.get('currency'))!=(account_ref,currency):return None
        if entry.get('card_id') is None:continue
        if (entry.get('user_id'),entry.get('sandbox_id'))!=(user_id,sandbox_id):return None
        card=_receipt_card(session,entry['card_id'],entry.get('bank_account_id'),user_id,sandbox_id,currency,
            entry.get('display_last4'),posted_at=posted.created_at)
        if card is None:return None
        facts[key]=card
    return facts


def completed_transfer_facts(session,principal,parent):
    from .runtime import task_trace
    from .conversation_provenance import current_plan_completed_write
    if (parent.user_id,parent.sandbox_id)!=(principal.user_id,principal.sandbox_id):return []
    trace=task_trace(session,principal,parent.id)
    if not (trace.get('outcome') or {}).get('verified') or (trace.get('outcome') or {}).get('kind')!='BUSINESS_RECEIPT':return []
    facts=[]
    for operation in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,
            RuntimeOperation.active==1,RuntimeOperation.status=='SUCCEEDED',
            RuntimeOperation.action_kind.in_(['TRANSFER','FX_TRANSFER','FX_EXCHANGE'])).order_by(RuntimeOperation.ordinal)):
        if not current_plan_completed_write(session,parent,operation):return []
        child=session.get(Task,operation.child_task_id)
        if not child or child.state!='SUCCEEDED':return []
        result=child.result_json
        if operation.action_kind=='TRANSFER':
            from .internal_banking import settlement_integrity
            from .internal_banking_models import InternalTransferSettlement
            transfer=session.get(Transfer,result.get('transfer_id'))
            if not transfer or (transfer.user_id,transfer.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                    or transfer.status!='POSTED' or transfer.amount_minor!=result.get('amount_minor') \
                    or not settlement_integrity(session,transfer)['balanced']:return []
            settlement=session.get(InternalTransferSettlement,transfer.id)
            payee=session.get(Payee,transfer.payee_id)
            recipient=settlement.receiver_username if settlement else payee.display_name if payee else '收款对象'
            fact={'kind':'TRANSFER','recipient':redact_credentials(recipient),'currency':settlement.currency if settlement else 'CNY',
                'debit_minor':transfer.amount_minor,'credit_minor':settlement.amount_minor if settlement else None,
                'recipient_credit_verified':settlement is not None,'source_id':'bank-receipt:'+transfer.id}
            cards=posted_transfer_card_facts(session,principal,transfer,result,settlement)
        else:
            from .fx_models import FxOperation
            from .fx_banking import fx_operation_integrity
            posted=session.get(FxOperation,result.get('fx_operation_id') or result.get('operation_id'))
            expected_kind='EXCHANGE' if operation.action_kind=='FX_EXCHANGE' else 'TRANSFER'
            if not posted or posted.kind!=expected_kind or posted.status!='POSTED' \
                    or (posted.user_id,posted.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                    or posted.task_id!=child.id or not fx_operation_integrity(session,posted)['balanced'] \
                    or not posted.receiver_user_id or not posted.recipient_username:return []
            if expected_kind=='EXCHANGE' and (posted.receiver_user_id,posted.receiver_sandbox_id)!=(principal.user_id,principal.sandbox_id):return []
            fact={'kind':operation.action_kind,'recipient':redact_credentials(posted.recipient_username),
                'currency':posted.target_currency,'source_currency':posted.source_currency,
                'debit_minor':posted.debit_minor,'credit_minor':posted.credit_minor,
                'recipient_credit_verified':True,'source_id':'bank-receipt:'+posted.id}
            cards=posted_fx_card_facts(session,principal,posted)
        if cards is None:return []
        fact.update(cards)
        fact['receipt_hash']=canonical_hash(fact)
        facts.append(fact)
    return facts


def render_transfer_status(facts,language=None):
    lines=[];english=language=='en'
    for fact in facts:
        if not isinstance(fact,dict) or fact.get('receipt_hash')!=canonical_hash({key:value for key,value in fact.items() if key!='receipt_hash'}):continue
        if fact.get('currency') not in {'CNY','USD'} or type(fact.get('debit_minor')) is not int:continue
        name=fact['recipient']
        source=fact.get('source_card')
        if source is not None:
            tail=source.get('display_last4') if isinstance(source,dict) else None
            currency=fact.get('source_currency',fact['currency'])
            if not isinstance(source,dict) or set(source)!={'instrument_type','display_last4','currency'} \
                    or source.get('instrument_type')!='DEBIT' or source.get('currency')!=currency \
                    or not isinstance(tail,str) or len(tail)!=4 or any(c not in '0123456789abcdefABCDEF' for c in tail):continue
            debit=f'{Decimal(fact["debit_minor"])/100:.2f}'
            lines.append(f'Your debit card ending {tail} was debited {debit} {currency}.' if english else
                f'已从本人尾号{tail}借记卡转出{debit}{"元人民币" if currency=="CNY" else "美元"}。')
        if fact['recipient_credit_verified'] and type(fact.get('credit_minor')) is int:
            amount=f'{Decimal(fact["credit_minor"])/100:.2f}'
            lines.append(f'The posted receipt confirms {name} received {amount} {fact["currency"]}.' if english else
                f'已入账，平台账号{name}实际收到{amount}{"元人民币" if fact["currency"]=="CNY" else "美元"}。')
        else:
            amount=f'{Decimal(fact["debit_minor"])/100:.2f}'
            lines.append(f'{amount} {fact["currency"]} was posted to external clearing for {name}; external receipt is not verified.' if english else
                f'已向{name}外部转出{amount}元人民币，外部清算已记账；对方外部到账尚未核验。')
    if lines:lines.append('This reply checks the receipt and does not send another transfer.' if english else '这里只核对已有回执，没有再次转账。')
    return '\n'.join(lines) if lines else None
