"""Finite completed renewal-setting receipts; never grants or payment substitutes."""
from sqlalchemy import select
from .audit import verify_audit_chain
from .authentication import as_utc
from .models import AuditEvent,Subscription,SubscriptionPayment,SubscriptionProduct,Transaction,Account,Card
from .planning import canonical_hash


def completed_subscription_setting_receipt(session,parent,op,child,call,confirmation):
    """Exact consumed plan + tool + domain event; later settings are not old grants."""
    try:
        owner=(parent.user_id,parent.sandbox_id)
        if op.action_kind not in {'SUBSCRIPTION_CANCEL','SUBSCRIPTION_START'} or op.status!='SUCCEEDED' \
                or not op.active or op.parent_task_id!=parent.id or op.sandbox_id!=parent.sandbox_id \
                or child.id!=op.child_task_id or child.state!='SUCCEEDED' or (child.user_id,child.sandbox_id)!=owner \
                or canonical_hash(child.plan_json)!=child.plan_hash or call.task_id!=child.id \
                or call.sandbox_id!=parent.sandbox_id or call.status!='SUCCESS' or call.idempotency_key!=op.idempotency_key \
                or call.result_json!=child.result_json or confirmation.task_id!=child.id \
                or (confirmation.user_id,confirmation.sandbox_id)!=owner or confirmation.status!='CONSUMED' \
                or confirmation.consumed_at is None or confirmation.plan_hash!=child.plan_hash:return None
        nodes=child.plan_json.get('nodes',[])
        if len(nodes)!=1:return None
        node=nodes[0];result=child.result_json;identifier=node.get('subscription_id')
        sub=session.get(Subscription,identifier)
        if sub is None or (sub.user_id,sub.sandbox_id)!=owner or sub.source_type not in {'BANK_MANDATE','MERCHANT_SIM'} \
                or sub.merchant!=node.get('merchant') or confirmation.target_id!=identifier \
                or result.get('subscription_id')!=identifier or node.get('node_id')!=confirmation.node_key \
                or node.get('read_only') is not False or node.get('requires_confirmation') is not True \
                or node.get('tool_name')!=call.tool_name or confirmation.action_type!=call.tool_name:return None
        events=list(session.scalars(select(AuditEvent).where(AuditEvent.task_id==child.id,
            AuditEvent.sandbox_id==parent.sandbox_id).order_by(AuditEvent.seq)))
        if not events or not verify_audit_chain(events):return None
        if session.scalar(select(SubscriptionPayment.id).where(SubscriptionPayment.task_id==child.id).limit(1)) is not None:return None
        if op.action_kind=='SUBSCRIPTION_CANCEL':
            if call.tool_name!='subscription.cancel.v1' or type(node.get('expected_version')) is not int \
                    or result.get('version')!=node['expected_version']+1 or sub.version<result['version'] \
                    or result.get('status')!='CANCELLED' or result.get('auto_renew') is not False \
                    or result.get('entitlement_preserved') is not True or type(result.get('refunded_minor')) is not int \
                    or result['refunded_minor']!=0 or result.get('source_type')!=node.get('source_type') \
                    or sub.source_type!=node['source_type'] \
                    or confirmation.view_hash!=canonical_hash({'plan_hash':child.plan_hash,'merchant':node['merchant'],
                        'amount_minor':node['amount_minor'],'source_type':node['source_type']}):return None
            payload={'subscription_id':identifier,'merchant':node['merchant'],'source_type':node['source_type'],'version':result['version']}
            event_type='SUBSCRIPTION_CANCELLED';effect='CANCEL_AUTORENEW'
            if sub.version==result['version'] and (sub.status!='CANCELLED' or sub.auto_renew is not False \
                    or sub.amount_minor!=node['amount_minor'] or (as_utc(sub.entitlement_end_at).isoformat() if sub.entitlement_end_at else None)!=result.get('entitlement_end_at')):return None
        else:
            from .subscription_autorenew import EFFECT,POLICY
            from .subscription_commerce import commerce_view
            if call.tool_name!='subscription.start.v1' or node.get('effect_kind')!=EFFECT \
                    or node.get('effective_policy')!=POLICY or node.get('action')!='START' \
                    or node.get('auto_renew_setting_source')!='CURRENT_USER' or node.get('auto_renew') is not True \
                    or type(node.get('total_amount_minor')) is not int or node['total_amount_minor']!=0 \
                    or type(result.get('charged_minor')) is not int or result['charged_minor']!=0 \
                    or result.get('mandate_restored') is not True or result.get('status')!='ACTIVE' \
                    or type(node.get('expected_subscription_version')) is not int \
                    or result.get('version')!=node['expected_subscription_version']+1 or sub.version<result['version'] \
                    or confirmation.view_hash!=canonical_hash(commerce_view(child.plan_hash,node)):return None
            keys=('action','effect_kind','merchant','subscription_id','currency','monthly_price_minor','total_amount_minor',
                'months','auto_renew','auto_renew_setting_source','mandate_amount_minor','mandate_billing_months',
                'effective_from','entitlement_end_at','expires_at','effective_policy','source_card_last4')
            if any(result.get(key)!=node.get(key) for key in keys) or node.get('currency')!='CNY' \
                    or any(node.get(key)!=node.get('previous_entitlement_end_at') for key in ('effective_from','entitlement_end_at','expires_at')):return None
            source=node.get('payment_instrument');proof=node.get('autorenew_source')
            if not isinstance(source,dict) or not isinstance(proof,dict) or source.get('instrument_type')!='DEBIT' \
                    or (source.get('user_id'),source.get('sandbox_id'))!=owner or source.get('currency')!='CNY' \
                    or source.get('scope_hash')!=canonical_hash({k:v for k,v in source.items() if k!='scope_hash'}) \
                    or source.get('instrument_id')!='debit:'+str(source.get('card_id')) \
                    or source.get('account_id')!=node.get('account_id') \
                    or (proof.get('account_id'),proof.get('card_id'))!=(source.get('account_id'),source.get('card_id')):return None
            card=session.get(Card,source['card_id']);account=session.get(Account,source['account_id']);product=session.get(SubscriptionProduct,node.get('product_id'))
            if card is None or account is None or (card.user_id,card.sandbox_id)!=owner or (account.user_id,account.sandbox_id)!=owner \
                    or card.account_id!=account.id or product is None or product.merchant!=sub.merchant:return None
            if proof.get('kind')=='PAID_STATEMENT':
                row=session.get(Transaction,proof.get('transaction_id'))
                if row is None or row.sandbox_id!=parent.sandbox_id or row.account_id!=account.id or row.card_id!=card.id \
                        or row.merchant!=sub.merchant or row.amount_minor!=proof.get('amount_minor') or row.amount_minor>=0 \
                        or row.category not in {'订阅','订阅服务'} or as_utc(row.occurred_at).isoformat()!=proof.get('occurred_at'):return None
            elif proof.get('kind')=='SUBSCRIPTION_PAYMENT':
                row=session.get(SubscriptionPayment,proof.get('payment_id'))
                if row is None or (row.user_id,row.sandbox_id)!=owner or row.subscription_id!=sub.id or row.status!='POSTED' \
                        or row.account_id!=account.id or row.product_id!=product.id or row.amount_minor!=proof.get('amount_minor') \
                        or row.months!=proof.get('months') or row.task_id!=proof.get('task_id'):return None
            else:return None
            if sub.version==result['version'] and (sub.status!='ACTIVE' or sub.auto_renew is not True \
                    or sub.amount_minor!=node.get('mandate_amount_minor') or sub.billing_months!=node.get('mandate_billing_months') \
                    or (as_utc(sub.entitlement_end_at).isoformat() if sub.entitlement_end_at else None)!=node['entitlement_end_at']):return None
            payload=result;event_type='SUBSCRIPTION_AUTORENEW_RESTORED';effect=EFFECT
        matched=[row for row in events if row.event_type==event_type and row.payload_json==payload]
        if len(matched)!=1:return None
        return {'version':'verified-subscription-setting-receipt-v1','subscription_id':sub.id,'operation_id':op.id,
            'effect_kind':effect,'source_event_id':matched[0].id,'posted_subscription_version':result['version'],
            'receipt_hash':canonical_hash({'plan_hash':child.plan_hash,'result':result,'event_hash':matched[0].event_hash})}
    except (KeyError,TypeError,ValueError,AttributeError):return None
