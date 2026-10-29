"""Explicit, confirmed future-renewal consent without buying paid rights again."""
from datetime import timedelta

from sqlalchemy import select, update

from .audit import append_audit
from .authentication import as_utc
from .contracts import DomainError, FinancialIntentContract
from .models import (Account, Subscription, SubscriptionProduct, SubscriptionPayment, Task, Transaction,
    Confirmation, ToolCall, OutboxMessage, utcnow)
from .planning import canonical_hash
from .platform import add_task_nodes, append_message, record_policy
from .workflow import finish_node, transition_task

EFFECT = "RESTORE_AUTORENEW"
POLICY = "RESTORE_AT_EXISTING_PAID_EXPIRY"
TOOL = "subscription.start.v1"
NODE = "start-subscription"


def _source_required():
    return DomainError('SUBSCRIPTION_SOURCE_REQUIRED','原代扣账户或卡片需要核对，不能改用其它付款账户。',409)


def _recorded_source(session, *, subscription, product, source_card_reference=None):
    """Resolve the actual previous debit, never today's default or a first account."""
    from .payment_instruments import resolve_source_instrument
    from .services import Principal
    uid,sid=subscription.user_id,subscription.sandbox_id
    principal=Principal(uid,sid)
    if subscription.last_payment_id:
        paid=session.scalar(select(SubscriptionPayment).where(SubscriptionPayment.id==subscription.last_payment_id,
            SubscriptionPayment.user_id==uid,SubscriptionPayment.sandbox_id==sid,
            SubscriptionPayment.subscription_id==subscription.id,SubscriptionPayment.product_id==product.id))
        original=session.scalar(select(Task).where(Task.id==paid.task_id,Task.user_id==uid,
            Task.sandbox_id==sid)) if paid else None
        if not paid or paid.status!='POSTED' or type(paid.amount_minor) is not int or paid.amount_minor<=0 \
                or paid.entitlement_end_at is None or subscription.entitlement_end_at is None \
                or as_utc(paid.entitlement_end_at)!=as_utc(subscription.entitlement_end_at) \
                or not original or original.state!='SUCCEEDED' or not isinstance(original.plan_json,dict) \
                or canonical_hash(original.plan_json)!=original.plan_hash \
                or (original.result_json or {}).get('payment_id')!=paid.id:
            raise _source_required()
        candidates=[node for node in original.plan_json.get('nodes',[]) if isinstance(node,dict)
            and node.get('tool_name') in {'subscription.start.v1','subscription.renew.v1'}
            and node.get('action')==paid.action and node.get('product_id')==paid.product_id
            and node.get('account_id')==paid.account_id and node.get('total_amount_minor')==paid.amount_minor]
        if len(candidates)!=1:
            raise _source_required()
        source=candidates[0].get('payment_instrument')
        if not isinstance(source,dict) or source.get('schema_version')!='payment-instrument-v1' \
                or source.get('instrument_type')!='DEBIT' or source.get('account_id')!=paid.account_id \
                or (source.get('user_id'),source.get('sandbox_id'))!=(uid,sid) \
                or source.get('instrument_id')!='debit:'+str(source.get('card_id')) \
                or source.get('scope_hash')!=canonical_hash({key:value for key,value in source.items() if key!='scope_hash'}):
            raise _source_required()
        account_id,card_id=paid.account_id,source['card_id']
        proof={'kind':'SUBSCRIPTION_PAYMENT','payment_id':paid.id,'task_id':paid.task_id,'plan_hash':original.plan_hash,
            'account_id':account_id,'card_id':card_id,'amount_minor':paid.amount_minor,'months':paid.months,
            'action':paid.action,'product_id':paid.product_id,'product_version':paid.product_version,
            'effective_from':as_utc(paid.effective_from).isoformat(),'entitlement_end_at':as_utc(paid.entitlement_end_at).isoformat()}
    else:
        # Opening/recurring statement rows have no interactive payment receipt.
        # Exact last-paid timestamp, service, amount, owner and card must agree.
        if subscription.last_paid_at is None or type(subscription.amount_minor) is not int or subscription.amount_minor<=0:
            raise _source_required()
        rows=list(session.scalars(select(Transaction).join(Account,Transaction.account_id==Account.id).where(
            Transaction.sandbox_id==sid,Account.user_id==uid,Account.sandbox_id==sid,Account.currency=='CNY',
            Transaction.occurred_at==subscription.last_paid_at,Transaction.merchant==subscription.merchant,
            Transaction.amount_minor==-subscription.amount_minor,Transaction.category.in_({'订阅','订阅服务'}))))
        if len(rows)!=1 or rows[0].card_id is None:
            raise _source_required()
        row=rows[0];account_id,card_id=row.account_id,row.card_id
        proof={'kind':'PAID_STATEMENT','transaction_id':row.id,'account_id':account_id,'card_id':card_id,
            'merchant':row.merchant,'amount_minor':row.amount_minor,'occurred_at':as_utc(row.occurred_at).isoformat(),
            'category':row.category,'source_kind':row.source_kind,'operation_ref':row.operation_ref}
    source=resolve_source_instrument(session,principal,'debit:'+card_id,'CNY',purpose='MERCHANT')
    if source['instrument_type']!='DEBIT' or source['account_id']!=account_id:
        raise _source_required()
    if source_card_reference is not None:
        requested=resolve_source_instrument(session,principal,source_card_reference,'CNY',purpose='READ')
        if requested['instrument_id']!=source['instrument_id']:
            raise DomainError('SUBSCRIPTION_SOURCE_CHANGED','恢复自动续费须沿用原代扣卡；更换付款卡需要另行核对授权。',409)
    return source,proof


def prepare_autorenew_restore(session, *, task, conversation_id, subscription, product, months, source_card_reference=None):
    """Caller has a grounded explicit auto_renew=True; confirmation grants it."""
    from .subscription_commerce import CONNECTED_SOURCES, commerce_view
    now=utcnow()
    if subscription.user_id!=task.user_id or subscription.sandbox_id!=task.sandbox_id \
            or subscription.status not in {'ACTIVE','CANCELLED'} or subscription.source_type not in CONNECTED_SOURCES \
            or subscription.entitlement_end_at is None or as_utc(subscription.entitlement_end_at)<=now \
            or type(months) is not int or not 1<=months<=12:
        raise DomainError('SUBSCRIPTION_CHANGED','已有权益或账户已变化，请重新核对自动续费设置。',409)
    source,proof=_recorded_source(session,subscription=subscription,product=product,source_card_reference=source_card_reference)
    account=session.get(Account,source['account_id']);end=as_utc(subscription.entitlement_end_at).isoformat()
    node={'node_id':NODE,'tool_name':TOOL,'read_only':False,'requires_confirmation':True,'action':'START',
        'effect_kind':EFFECT,'merchant':product.merchant,'product_id':product.id,'expected_product_version':product.version,
        'source_type':product.source_type,'subscription_id':subscription.id,'expected_subscription_version':subscription.version,
        'expected_subscription_source_type':subscription.source_type,'previous_status':subscription.status,
        'previous_auto_renew':bool(subscription.auto_renew),'previous_entitlement_end_at':end,
        'account_id':account.id,'expected_account_version':account.version,'payment_instrument':source,
        'source_card_last4':source['display_last4'],'autorenew_source':proof,'monthly_price_minor':product.monthly_price_minor,
        'total_amount_minor':0,'currency':'CNY','months':months,'auto_renew':True,'auto_renew_setting_source':'CURRENT_USER',
        'mandate_amount_minor':product.monthly_price_minor*months,'mandate_billing_months':months,
        'effective_from':end,'entitlement_end_at':end,'expires_at':end,'effective_policy':POLICY}
    task.plan_json={'plan_version':1,'policy_version':'subscription-autorenew-v1','task_id':task.id,
        'sandbox_id':task.sandbox_id,'nodes':[node]};task.plan_hash=canonical_hash(task.plan_json)
    add_task_nodes(session,task_id=task.id,sandbox_id=task.sandbox_id,plan=task.plan_json)
    record_policy(session,task_id=task.id,sandbox_id=task.sandbox_id,node_key=NODE,decision='REQUIRE_CONFIRMATION',
        reason_codes=['EXPLICIT_RECURRING_AUTHORIZATION','KEEP_EXISTING_PAID_RIGHTS','NO_CURRENT_PAYMENT'],policy_version='subscription-autorenew-v1')
    fic=FinancialIntentContract.model_validate(task.fic_json);fic.requested_capabilities=[TOOL];fic.status='READY'
    fic.slots={key:{'value':node[key],'source':'AUTHORITATIVE'} for key in ('merchant','total_amount_minor','currency','months','auto_renew')}
    task.fic_json=fic.model_dump();transition_task(task,'AWAITING_CONFIRMATION')
    task.result_json={key:node[key] for key in ('action','effect_kind','merchant','subscription_id','currency','monthly_price_minor',
        'total_amount_minor','months','auto_renew','auto_renew_setting_source','mandate_amount_minor','mandate_billing_months',
        'effective_from','entitlement_end_at','expires_at','effective_policy','source_card_last4')}
    task.result_json.update(message=f"准备恢复{product.merchant}自动续费：本次不扣费，已付权益到期时间不变；到期后每{months}个月扣款{node['mandate_amount_minor']/100:.2f}元，请确认。",charged_minor=0)
    session.add(Confirmation(sandbox_id=task.sandbox_id,task_id=task.id,user_id=task.user_id,node_key=NODE,action_type=TOOL,
        target_id=subscription.id,plan_hash=task.plan_hash,view_hash=canonical_hash(commerce_view(task.plan_hash,node)),expires_at=now+timedelta(minutes=5)))
    append_audit(session,sandbox_id=task.sandbox_id,task_id=task.id,event_type='SUBSCRIPTION_AUTORENEW_PROPOSED',payload=commerce_view(task.plan_hash,node))
    append_message(session,conversation_id=conversation_id,sandbox_id=task.sandbox_id,task_id=task.id,role='ASSISTANT',content=task.result_json['message'])
    return task


def dispatch_autorenew_restore(session, *, task, node, task_node, confirmation, request_hash, idempotency_key, user_id, sandbox_id):
    """Called only after ToolGateway's owner/policy/step-up/idempotency checks."""
    from .subscription_commerce import CONNECTED_SOURCES, commerce_view
    account=session.scalar(select(Account).where(Account.id==node.get('account_id'),Account.sandbox_id==sandbox_id,
        Account.user_id==user_id).with_for_update())
    product=session.scalar(select(SubscriptionProduct).where(SubscriptionProduct.id==node.get('product_id')).with_for_update())
    item=session.scalar(select(Subscription).where(Subscription.id==node.get('subscription_id'),Subscription.sandbox_id==sandbox_id,
        Subscription.user_id==user_id).with_for_update())
    months=node.get('months')
    if not account or not product or not item or account.currency!='CNY' or product.currency!='CNY' \
            or account.version!=node.get('expected_account_version') or product.version!=node.get('expected_product_version') \
            or product.status!='AVAILABLE' or product.source_type not in CONNECTED_SOURCES or product.source_type!=node.get('source_type') \
            or product.merchant!=node.get('merchant') or product.monthly_price_minor!=node.get('monthly_price_minor') \
            or item.version!=node.get('expected_subscription_version') or item.status!=node.get('previous_status') \
            or item.source_type not in CONNECTED_SOURCES or item.source_type!=node.get('expected_subscription_source_type') \
            or bool(item.auto_renew)!=node.get('previous_auto_renew') or item.status not in {'ACTIVE','CANCELLED'} \
            or item.entitlement_end_at is None or as_utc(item.entitlement_end_at)<=utcnow():
        raise DomainError('SUBSCRIPTION_CHANGED','原权益、账户或续费方案已变化，请重新核对。',409)
    end=as_utc(item.entitlement_end_at).isoformat()
    if node.get('action')!='START' or node.get('effect_kind')!=EFFECT or type(months) is not int or not 1<=months<=12 \
            or node.get('auto_renew') is not True or node.get('auto_renew_setting_source')!='CURRENT_USER' \
            or type(node.get('total_amount_minor')) is not int or node['total_amount_minor']!=0 or node.get('currency')!='CNY' \
            or node.get('mandate_amount_minor')!=product.monthly_price_minor*months or node.get('mandate_billing_months')!=months \
            or any(node.get(key)!=end for key in ('previous_entitlement_end_at','effective_from','entitlement_end_at','expires_at')) \
            or node.get('effective_policy')!=POLICY or confirmation.target_id!=item.id \
            or confirmation.view_hash!=canonical_hash(commerce_view(task.plan_hash,node)):
        raise DomainError('SUBSCRIPTION_TERMS_INVALID','本次自动续费确认内容无效，没有扣款或改变权益。',409)
    from .payment_instruments import validate_instrument_snapshot
    from .services import Principal
    current,proof=_recorded_source(session,subscription=item,product=product)
    if proof!=node.get('autorenew_source') or current['account_id']!=account.id \
            or current['instrument_id']!=(node.get('payment_instrument') or {}).get('instrument_id') \
            or current['display_last4']!=node.get('source_card_last4'):
        raise DomainError('SUBSCRIPTION_SOURCE_CHANGED','原代扣凭据或账户已变化，请重新核对。',409)
    validate_instrument_snapshot(session,Principal(user_id,sandbox_id),node.get('payment_instrument'),purpose='MERCHANT')
    affected=session.execute(update(Subscription).where(Subscription.id==item.id,Subscription.version==item.version).values(
        status='ACTIVE',auto_renew=True,amount_minor=node['mandate_amount_minor'],billing_months=months,
        next_due_at=item.entitlement_end_at,version=Subscription.version+1).execution_options(synchronize_session=False)).rowcount
    if affected!=1:
        raise DomainError('SUBSCRIPTION_CHANGED','订阅设置已变化，请重新核对。',409)
    session.refresh(item)
    confirmation.status='CONSUMED';confirmation.consumed_at=utcnow()
    task.result_json={key:node[key] for key in ('action','effect_kind','merchant','subscription_id','currency','monthly_price_minor',
        'total_amount_minor','months','auto_renew','auto_renew_setting_source','mandate_amount_minor','mandate_billing_months',
        'effective_from','entitlement_end_at','expires_at','effective_policy','source_card_last4')}
    task.result_json.update(message=f"已恢复{product.merchant}自动续费。本次没有扣款，已付权益到期时间不变；到期后每{months}个月扣款{node['mandate_amount_minor']/100:.2f}元。",
        charged_minor=0,mandate_restored=True,entitled=True,status=item.status,version=item.version)
    finish_node(session,task,task_node,task.result_json)
    session.add(ToolCall(sandbox_id=sandbox_id,task_id=task.id,tool_name=TOOL,idempotency_key=idempotency_key,
        request_hash=request_hash,status='SUCCESS',result_json=task.result_json))
    append_audit(session,sandbox_id=sandbox_id,task_id=task.id,event_type='SUBSCRIPTION_AUTORENEW_RESTORED',payload=task.result_json)
    session.add(OutboxMessage(sandbox_id=sandbox_id,task_id=task.id,event_type='TASK_COMPLETED',
        payload_json={'task_id':task.id,'subscription_id':item.id,'effect_kind':EFFECT}))
    return task


def restored_mandate_instrument(session, subscription):
    """A consumed restoration grant is durable; today's default is irrelevant.

    None means there is no governing restoration grant (legacy behavior).
    An invalid grant raises instead of silently selecting another card.
    """
    from .subscription_commerce import commerce_view
    from .payment_instruments import validate_instrument_snapshot
    from .services import Principal
    tasks=session.scalars(select(Task).where(Task.user_id==subscription.user_id,
        Task.sandbox_id==subscription.sandbox_id,Task.state=='SUCCEEDED',Task.intent_type=='SUBSCRIPTION_MANAGEMENT')
        .order_by(Task.updated_at.desc(),Task.id.desc()))
    for task in tasks:
        result=task.result_json if isinstance(task.result_json,dict) else {}
        nodes=task.plan_json.get('nodes',[]) if isinstance(task.plan_json,dict) else []
        matching=[node for node in nodes if isinstance(node,dict) and node.get('effect_kind')==EFFECT
            and node.get('subscription_id')==subscription.id]
        if not matching and not (result.get('effect_kind')==EFFECT and result.get('subscription_id')==subscription.id):
            continue
        confirmation=session.scalar(select(Confirmation).where(Confirmation.task_id==task.id,
            Confirmation.node_key==NODE,Confirmation.user_id==subscription.user_id,
            Confirmation.sandbox_id==subscription.sandbox_id))
        if not confirmation or confirmation.status!='CONSUMED' or confirmation.consumed_at is None:
            raise _source_required()
        # A later explicit paid subscription transaction is a newer setting.
        # Recurring statement postings do not supersede a reviewed mandate.
        newer=session.scalar(select(SubscriptionPayment.id).where(SubscriptionPayment.subscription_id==subscription.id,
            SubscriptionPayment.user_id==subscription.user_id,SubscriptionPayment.sandbox_id==subscription.sandbox_id,
            SubscriptionPayment.status=='POSTED',SubscriptionPayment.created_at>confirmation.consumed_at).limit(1))
        if newer:
            return None
        if len(matching)!=1 or canonical_hash(task.plan_json)!=task.plan_hash:
            raise _source_required()
        node=matching[0];source=node.get('payment_instrument');proof=node.get('autorenew_source')
        call=session.scalar(select(ToolCall).where(ToolCall.task_id==task.id,ToolCall.sandbox_id==subscription.sandbox_id,
            ToolCall.tool_name==TOOL,ToolCall.status=='SUCCESS'))
        if not isinstance(source,dict) or not isinstance(proof,dict) \
                or proof.get('kind') not in {'SUBSCRIPTION_PAYMENT','PAID_STATEMENT'} \
                or (proof.get('account_id'),proof.get('card_id'))!=(source.get('account_id'),source.get('card_id')) \
                or source.get('instrument_type')!='DEBIT' or source.get('currency')!='CNY' \
                or node.get('account_id')!=source.get('account_id') or node.get('source_card_last4')!=source.get('display_last4') \
                or node.get('tool_name')!=TOOL or node.get('action')!='START' or node.get('auto_renew') is not True \
                or node.get('currency')!='CNY' or node.get('merchant')!=subscription.merchant \
                or node.get('expected_subscription_source_type')!=subscription.source_type \
                or node.get('mandate_amount_minor')!=subscription.amount_minor \
                or node.get('mandate_billing_months')!=subscription.billing_months \
                or confirmation.action_type!=TOOL or confirmation.target_id!=subscription.id \
                or confirmation.plan_hash!=task.plan_hash \
                or confirmation.view_hash!=canonical_hash(commerce_view(task.plan_hash,node)) \
                or result.get('mandate_restored') is not True or result.get('auto_renew') is not True \
                or type(result.get('charged_minor')) is not int or result['charged_minor']!=0 \
                or type(result.get('total_amount_minor')) is not int or result['total_amount_minor']!=0 \
                or result.get('source_card_last4')!=source.get('display_last4') \
                or not call or call.result_json!=result:
            raise _source_required()
        return validate_instrument_snapshot(session,Principal(subscription.user_id,subscription.sandbox_id),source,
            purpose='MERCHANT',amount_minor=subscription.amount_minor,version_policy='CARD_BINDING')
    return None
