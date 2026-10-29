"""Completed order identities may select a fresh holding READ, never a write.

The old holding quantity is deliberately absent: current quantity, price and
cost must come from a new ASSET_POSITIONS_READ tool result.
"""
from sqlalchemy import select

from .contracts import DomainError
from .models import Task,InvestmentOrder,InvestmentProduct
from .runtime_models import RuntimeOperation,RuntimeRecord,PlanSnapshot
from .conversation_models import ChatTurn
from .planning import canonical_hash

KIND='COMPLETED_ASSET_RECEIPT_IDENTITY'
CASH_KIND='COMPLETED_WEALTH_CARD_IDENTITY'


def project_selected_identity_changes(decision,offered,actions,message):
    """Compile a selected receipt identity into a NEW exact product READ.

    Selection already comes from the current cloud decision. This only closes
    the optional field representation; _entity/checked_identity must still
    prove the posted order, actor and immutable source before using the ID.
    No old quantity, value, action or confirmation is copied.
    """
    from copy import deepcopy
    selected={ref for ref in decision.get('entity_refs',[]) if
        (offered.get('entity_bindings',{}).get(ref) or {}).get('source_kind')==KIND}
    changes=decision.get('changes',[])
    explicit={row.get('entity_ref') for row in changes if row.get('operation')=='SET' and row.get('field')=='product_name'}
    if not selected-explicit:return changes
    if decision.get('mode')!='REQUEST' or len(actions)!=1 or actions[0].get('kind')!='ASSET_POSITIONS_READ' \
            or len(selected)!=1 or any(row.get('field')=='product_name' for row in changes):
        raise DomainError('SEMANTIC_ASSET_IDENTITY_REQUIRES_NEW_READ','成交身份只能用于独立的明确产品持仓查询。',409)
    reference=next(iter(selected))
    visible=next((row for row in offered['model'].get('entities',[]) if row.get('entity_ref')==reference),None)
    if not visible or visible.get('domain')!='ASSETS' or visible.get('read_kind')!='ASSET_POSITIONS_READ' \
            or visible.get('field')!='product_name' or 'product_name' not in visible.get('allowed_fields',[]):_deny()
    return [*deepcopy(changes),{'action_ref':'new:0','field':'product_name','operation':'SET','entity_ref':reference,
        'source_span':{'start':0,'end':len(message)},'source_text':message}]


def _deny():
    raise DomainError('SEMANTIC_ASSET_IDENTITY_SOURCE_CHANGED','这项成交回执的产品身份无法核验，请明确要查询的持仓产品。',409)


def order_identities(session,principal,thread,task,before_sequence):
    if task.state!='SUCCEEDED' or (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id):return []
    origin=session.scalar(select(ChatTurn).where(ChatTurn.thread_id==thread.id,ChatTurn.task_id==task.id,
        ChatTurn.user_id==principal.user_id,ChatTurn.sandbox_id==principal.sandbox_id,
        ChatTurn.status=='ANSWERED',ChatTurn.sequence<before_sequence).order_by(ChatTurn.sequence.desc()).limit(1))
    if not origin:return []
    from .runtime import task_trace
    from .conversation_provenance import validate_current_continuation
    record=session.get(RuntimeRecord,task.id)
    if record and record.metadata_json.get('cloud_semantic_intent'):
        from .semantic_proposal import validate_semantic_source_snapshot
        snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,PlanSnapshot.version==record.plan_version))
        if not snapshot:_deny()
        validate_semantic_source_snapshot(session,principal,task,record.metadata_json,snapshot)
    else:validate_current_continuation(session,task)
    trace=task_trace(session,principal,task.id)
    if not (trace.get('outcome') or {}).get('verified'):return []
    nodes={node['operation_id']:node for node in trace.get('nodes',[])}
    out=[]
    for operation in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id,
            RuntimeOperation.active==1,RuntimeOperation.status=='SUCCEEDED',
            RuntimeOperation.action_kind.in_(['WEALTH_BUY','WEALTH_REDEEM'])).order_by(RuntimeOperation.ordinal)):
        child=session.get(Task,operation.child_task_id)
        node=nodes.get(operation.id)
        if not child or child.state!='SUCCEEDED' or (child.user_id,child.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
                or not node or node.get('result')!=child.result_json:_deny()
        order=session.get(InvestmentOrder,child.result_json.get('order_id'))
        if not order or (order.user_id,order.sandbox_id)!=(principal.user_id,principal.sandbox_id) or order.status!='POSTED':_deny()
        product=session.get(InvestmentProduct,order.product_id)
        if not product or product.sandbox_id!=principal.sandbox_id:_deny()
        binding={'source_kind':KIND,'task_id':task.id,'task_version':task.version,'operation_id':operation.id,
            'child_task_id':child.id,'child_result_hash':canonical_hash(child.result_json),'order_id':order.id,
            'order_hash':canonical_hash({'product_id':order.product_id,'units':order.units,'amount_minor':order.amount_minor,
                'type':order.order_type,'status':order.status}),
            'product_id':product.id,'product_name':product.name,
            'conversation_id':thread.id,'before_sequence':before_sequence,'source_turn_id':origin.id,
            'field':'product_name','value':product.id}
        out.append(binding)
    return out


def checked_identity(session,principal,thread,entry,field,action_kind):
    if field!='product_name' or action_kind!='ASSET_POSITIONS_READ':_deny()
    task=session.get(Task,entry.get('task_id'))
    if not task:_deny()
    if entry not in order_identities(session,principal,thread,task,entry['before_sequence']):_deny()
    return entry['product_id']


def cash_card_identities(session,principal,thread,task,before_sequence):
    """A proved order supplies only its own cash-card identity for fresh reads."""
    from .models import Card
    from .card_identifiers import card_identifier_view
    values=[]
    for identity in order_identities(session,principal,thread,task,before_sequence):
        child=session.get(Task,identity['child_task_id'])
        nodes=(child.plan_json or {}).get('nodes',[])
        if len(nodes)!=1:continue
        instrument=nodes[0].get('payment_instrument') or {}
        card=session.get(Card,instrument.get('card_id'))
        if (instrument.get('instrument_type')!='DEBIT' or not card
                or (card.user_id,card.sandbox_id,card.account_id)!=(principal.user_id,principal.sandbox_id,instrument.get('account_id'))):continue
        number=(card_identifier_view(session,card) or {}).get('card_number')
        if not number or number!=instrument.get('card_number'):continue
        role='收款卡' if nodes[0].get('order_type')=='REDEEM' else '付款卡'
        values.append({**identity,'source_kind':CASH_KIND,'field':'card_number','value':number,
            'card_id':card.id,'account_id':card.account_id,'label':f"上次理财{role}，本人借记卡尾号{number[-4:]}"})
    return values


def checked_cash_identity(session,principal,thread,entry,field,action_kind):
    if field!='card_number' or action_kind!='CARD_STATUS':_deny()
    task=session.get(Task,entry.get('task_id'))
    if not task or entry not in cash_card_identities(session,principal,thread,task,entry['before_sequence']):_deny()
    return entry['value']
