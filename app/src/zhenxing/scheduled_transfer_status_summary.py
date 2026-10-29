"""Status of a selected, confirmed single appointment; never a payment trigger."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .intelligence import READ_KINDS, action_signature
from .planning import canonical_hash

VERSION = 'verified-scheduled-transfer-status-v1'


def scheduled_transfer_scope_summary(session, principal, tasks, contexts, language='zh-CN'):
    """Recheck the selected owned runtime/confirmation/registration chain.

    Waiting registration is not a posted debit. A posted answer additionally
    requires the original Transfer and balanced bank ledger. Recurring/mixed,
    stale, unconfirmed and uncertain scopes keep their existing safe answer.
    No clock advance, posting, confirmation, default-card lookup or write occurs.
    """
    from .banking_extension_models import ScopedScheduledTransfer, ScheduledTransferSeries
    from .models import Confirmation, Task, ToolCall, Transfer
    from .runtime_models import RuntimeOperation, RuntimeRecord, PlanSnapshot
    from .task_dialogue_context import VERSION as CONTEXT_VERSION
    try:
        if not isinstance(tasks,list) or not isinstance(contexts,list) or len(tasks)!=1 or len(contexts)!=1:
            return None
        parent, context = tasks[0], contexts[0]
        if (parent.user_id,parent.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                or not isinstance(context,dict) or context.get('version')!=CONTEXT_VERSION \
                or context.get('authority')!='OWNED_RUNTIME_FACTS_NOT_USER_AUTHORIZATION' \
                or context.get('task_id')!=parent.id or type(context.get('task_version')) is not int \
                or context['task_version']!=parent.version or context.get('state')!=parent.state \
                or context.get('possible_write_effect') is not False or parent.state not in {'WAITING','SUCCEEDED'}:
            return None
        kinds=context.get('action_kinds')
        if not isinstance(kinds,list) or any(not isinstance(kind,str) for kind in kinds) \
                or [kind for kind in kinds if kind not in READ_KINDS]!=['TRANSFER_SCHEDULE']:
            return None
        record=session.get(RuntimeRecord,parent.id)
        writes=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==parent.id,
            RuntimeOperation.action_kind.not_in(READ_KINDS))))
        if record is None or record.kind!='PARENT' or len(writes)!=1:
            return None
        op=writes[0]
        if op.action_kind!='TRANSFER_SCHEDULE' or op.sandbox_id!=principal.sandbox_id or op.active!=1 \
                or op.plan_version!=record.plan_version or op.status not in {'WAITING_TIME','SUCCEEDED'}:
            return None
        snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,PlanSnapshot.version==record.plan_version))
        bound=next((row for row in (snapshot.plan_json.get('nodes',[]) if snapshot else []) if row.get('operation_id')==op.id),None)
        if snapshot is None or snapshot.plan_hash!=parent.plan_hash or snapshot.plan_json!=parent.plan_json \
                or canonical_hash(parent.plan_json)!=parent.plan_hash or not bound \
                or bound.get('node_id')!=op.operation_key or bound.get('kind')!=op.action_kind \
                or bound.get('args')!=op.args_json or bound.get('depends_on')!=op.depends_on_json \
                or canonical_hash(action_signature(op.args_json))!=op.fingerprint:
            return None
        child=session.get(Task,op.child_task_id)
        child_record=session.get(RuntimeRecord,op.child_task_id)
        if child is None or (child.user_id,child.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                or child_record is None or child_record.kind!='CHILD' or child_record.root_task_id!=parent.id \
                or child_record.metadata_json.get('operation_id')!=op.id or child.intent_type!='TRANSFER_SCHEDULE':
            return None
        scheduled=session.scalar(select(ScopedScheduledTransfer).where(ScopedScheduledTransfer.task_id==child.id,
            ScopedScheduledTransfer.user_id==principal.user_id,ScopedScheduledTransfer.sandbox_id==principal.sandbox_id))
        if scheduled is None or session.scalar(select(ScheduledTransferSeries.id).where(ScheduledTransferSeries.schedule_id==scheduled.id)):
            return None
        nodes=(child.plan_json or {}).get('nodes',[])
        node=nodes[0] if len(nodes)==1 else {}
        scope={key:value for key,value in node.items() if key not in {'node_id','tool_name','read_only','requires_confirmation'}}
        confirmation=session.get(Confirmation,scheduled.confirmation_id)
        calls=list(session.scalars(select(ToolCall).where(ToolCall.task_id==child.id,
            ToolCall.sandbox_id==principal.sandbox_id,ToolCall.tool_name=='transfer.schedule.v1',
            ToolCall.idempotency_key==op.idempotency_key,ToolCall.status=='SUCCESS')))
        if canonical_hash(child.plan_json)!=child.plan_hash or child.plan_hash!=scheduled.plan_hash \
                or node.get('node_id')!='schedule-transfer' or node.get('tool_name')!='transfer.schedule.v1' \
                or node.get('read_only') is not False or node.get('requires_confirmation') is not True \
                or scope!=scheduled.scope_json or confirmation is None or confirmation.status!='CONSUMED' \
                or (confirmation.task_id,confirmation.user_id,confirmation.sandbox_id)!=(child.id,principal.user_id,principal.sandbox_id) \
                or confirmation.action_type!='transfer.schedule.v1' or confirmation.node_key!='schedule-transfer' \
                or confirmation.plan_hash!=scheduled.plan_hash or confirmation.target_id!=scheduled.payee_id \
                or confirmation.view_hash!=canonical_hash({'plan_hash':child.plan_hash,'scope':scope}) \
                or confirmation.consumed_at is None or as_utc(confirmation.consumed_at)>as_utc(confirmation.expires_at) \
                or len(calls)!=1 or calls[0].result_json.get('schedule_id')!=scheduled.id \
                or calls[0].result_json.get('status')!='WAITING':
            return None
        due=datetime.fromisoformat(scope['due_at'])
        if due.tzinfo is None or as_utc(due)!=as_utc(scheduled.due_at) \
                or (scope.get('source_account_id'),scope.get('payee_id'),scope.get('amount_minor'))!=(
                    scheduled.source_account_id,scheduled.payee_id,scheduled.amount_minor) \
                or type(scheduled.amount_minor) is not int or scheduled.amount_minor<=0:
            return None
        existing=list(session.scalars(select(Transfer).where(Transfer.sandbox_id==principal.sandbox_id,
            Transfer.idempotency_key==scheduled.business_key)))
        english=language=='en'
        if scheduled.status=='WAITING':
            if parent.state!='WAITING' or child.state!='WAITING' or op.status!='WAITING_TIME' \
                    or context.get('completed_write_present') is not False or scheduled.transfer_id is not None \
                    or scheduled.completed_at is not None or existing \
                    or child.result_json.get('schedule_id')!=scheduled.id or child.result_json.get('status')!='WAITING':
                return None
            date=due.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
            amount=f'{Decimal(scheduled.amount_minor)/100:.2f}'
            answer=(f'The appointment is registered, but this transfer has not been debited or sent. It is scheduled for {date} (Shanghai time), CNY {amount}. Execution will recheck the recipient, card and funds; registration does not guarantee a successful payment.' if english else
                f'这笔预约已登记成功，但还没有扣款或转出。约定在{date}（北京时间）转账{amount}元人民币；到时会重新核对收款人、卡片和资金，预约登记不代表付款已完成或保证一定成功。')
        elif scheduled.status=='POSTED':
            if parent.state!='SUCCEEDED' or child.state!='SUCCEEDED' or op.status!='SUCCEEDED' \
                    or context.get('verified') is not True or context.get('completed_write_present') is not True \
                    or context.get('outcome_kind')!='BUSINESS_RECEIPT' or len(existing)!=1 \
                    or scheduled.transfer_id!=existing[0].id or child.result_json.get('transfer_id')!=existing[0].id:
                return None
            transfer=existing[0]
            if (transfer.user_id,transfer.source_account_id,transfer.payee_id,transfer.amount_minor)!=(
                    principal.user_id,scheduled.source_account_id,scheduled.payee_id,scheduled.amount_minor):
                return None
            from .internal_banking import settlement_integrity
            if not settlement_integrity(session,transfer)['balanced']:
                return None
            from .internal_banking_models import InternalTransferSettlement
            from .semantic_status_facts import posted_transfer_card_facts
            from .completed_transfer_status_summary import render_completed_transfer_summary
            settlement=session.get(InternalTransferSettlement,transfer.id)
            recipient=settlement.receiver_username if settlement else scope.get('payee_name')
            fact={'kind':'TRANSFER','recipient':recipient,'currency':'CNY','debit_minor':transfer.amount_minor,
                'credit_minor':settlement.amount_minor if settlement else None,'recipient_credit_verified':settlement is not None,
                'source_id':'bank-receipt:'+transfer.id}
            payment=scope.get('transfer_payment_scope')
            cards=posted_transfer_card_facts(session,principal,transfer,
                {'source_card_context':payment['source']} if isinstance(payment,dict) else {},settlement)
            if cards is None:return None
            fact.update(cards);fact['receipt_hash']=canonical_hash(fact)
            answer=render_completed_transfer_summary([fact],language)
            if not answer:return None
        else:
            return None
        return {'version':VERSION,'verified':True,'schedule_id':scheduled.id,'state':scheduled.status,
            'answer':answer,'registration_plan_hash':scheduled.plan_hash}
    except (DomainError,ValueError,TypeError,KeyError,AttributeError):
        return None
