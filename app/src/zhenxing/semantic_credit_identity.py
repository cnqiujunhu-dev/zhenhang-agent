"""Real completed credit receipts may identify a card for a fresh READ only.

No historical amount, balance, limit, repayment source, status or confirmation
grant is offered.  The next CREDIT_VIEW/STATEMENT obtains its own current facts.
"""
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .credit_models import CreditInstallment, CreditRepayment
from .models import Confirmation, Task, ToolCall
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

KIND = "COMPLETED_CREDIT_RECEIPT_IDENTITY"
INSTALLMENT_KIND='COMPLETED_CREDIT_INSTALLMENT_IDENTITY'
CONTROL_KINDS = frozenset({'CARD_LOCK','CARD_UNLOCK','CARD_REPORT_LOSS'})
SOURCE_KINDS = frozenset({"CREDIT_REPAY", "CREDIT_INSTALLMENT", "CREDIT_SETTLE_INSTALLMENT"}) | CONTROL_KINDS
READ_CONSUMERS = frozenset({"CREDIT_VIEW", "CREDIT_STATEMENT"})
LITERAL_SELECTOR_VERSION = "completed-credit-current-literal-selector-v1"


def _closed_selector_prefix(value):
    """A bank nominal's closed prefix, not inferred ownership or an action."""
    from .card_identifiers import CARD_NUMBER_PATTERN
    value = re.sub(r'(?<![A-Za-z0-9_-])' + CARD_NUMBER_PATTERN + r'(?![A-Za-z0-9_-])', '\x00', value, flags=re.I)
    return re.fullmatch(r'(?:\s|\x00|[：:]|请|现在|立即|马上|帮我|用|使用|从|把|将|向|给|刚才|'
        r'这张|那张|我的|本人(?:的)?|我自己的|自己的|的|借记卡|储蓄卡|信用卡|银行卡|卡号|号码|卡|'
        r'结清|提前|全部|\b(?:please|now|use|using|from|my|own|debit|credit|bank|card|number|'
        r'the|this|that|with|settle|prepay)\b)*', value, re.I) is not None


def _current_literal_source(message, change, changes):
    """Reject clipped possessors/quotes/polarity; never create a SELF proof."""
    from .semantic_proposal import _trusted_span, _literal_field
    from .semantic_read_subject import _QUOTED, _SELF_DENIAL, _trusted_owner_message
    from .semantic_inert_write_policies import WRITE_POLICIES, affirmative_projection_source
    from .semantic_router import SemanticFieldChange
    from .card_identifiers import CARD_NUMBER_PATTERN
    from .semantic_source_conditions import has_source_condition
    if has_source_condition(message):
        return None
    span = change.get('source_span')
    raw = _trusted_span(message, span, 'card_reference')
    if change.get('source_text') is not None and change['source_text'] != raw:
        return None
    if any(span['start'] < row.end() and span['end'] > row.start() for row in _QUOTED.finditer(message)):
        return None
    literal = _literal_field('card_reference', raw, 'CREDIT_SETTLE_INSTALLMENT', {})
    if not isinstance(literal, str) or re.fullmatch(CARD_NUMBER_PATTERN, literal) is None:
        return None
    trusted = _trusted_owner_message(message)
    if _SELF_DENIAL.search(trusted) or re.search(r'属于|归属|持卡人|\b(?:belongs?\s+to|owned\s+by)\b', trusted, re.I):
        return None
    if re.search(r'(?:替|代|为|帮|给)(?!我(?:自己|本人)?|本人|借记卡|信用卡|银行卡|卡(?:号)?|zhenhang|xiaozhao)'
            r'[^，,。；;!?！？\n]{0,60}?(?:结清|偿还|还款)|'
            r'(?<![A-Za-z0-9_.-])(?:for|on\s+behalf\s+of)\s+(?!me\b|my\b)[A-Za-z]', trusted, re.I):
        return None
    left = max(trusted.rfind(mark, 0, span['start']) for mark in ('，', ',', '。', '；', ';', '\n')) + 1
    if not _closed_selector_prefix(trusted[left:span['start']]):
        return None
    # Inspect complete possessor prefixes, not just the selected card substring.
    # The closed vocabulary cannot swallow names, family roles or an unknown
    # actor before a bank/debt resource. No status word proves an owner.
    for found in re.finditer(r'(?P<owner>[^，,。；;!?！？\n]{1,160}?)的\s*'
            r'(?:(?:这|那)(?:张|笔)|当前|未还)*'
            r'(?:借记卡|信用卡|银行卡|账户|卡片|分期|欠款|债务|本金|费用)', trusted):
        if not _closed_selector_prefix(found['owner']):
            return None
    if re.search(r"(?<![A-Za-z0-9_.-])(?:his|her|their|someone\s+else['’]?s)\s+(?:debit\s+|credit\s+|bank\s+)?(?:card|account|debt)|"
            r"(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]*['’]s\s+(?:debit\s+|credit\s+|bank\s+)?(?:card|account|debt)", trusted, re.I):
        return None
    selected = [SemanticFieldChange.model_validate(row) for row in changes]
    anchor = next(row for row in selected if row.model_dump() == SemanticFieldChange.model_validate(change).model_dump())
    if not affirmative_projection_source(message, WRITE_POLICIES['CREDIT_SETTLE_INSTALLMENT'],
            anchor, selected, 'CREDIT_SETTLE_INSTALLMENT'):
        return None
    return literal


def project_current_literal_selection(session, principal, thread, message, decision, offered, changes, actions):
    """Current full literal only; the unchanged receipt remains READ-only."""
    selected = {ref for ref in decision.get('entity_refs', [])
        if (offered.get('entity_bindings', {}).get(ref) or {}).get('source_kind') == KIND}
    if not selected or decision.get('mode') != 'REQUEST' or len(actions) != 1 \
            or actions[0].get('kind') != 'CREDIT_SETTLE_INSTALLMENT' or len(selected) != 1:
        return changes, []
    candidates = [row for row in changes if row.get('entity_ref') in selected]
    if len(candidates) != 1:
        return changes, []
    change = candidates[0]
    if change.get('action_ref') != 'new:0' or change.get('operation') != 'SET' \
            or change.get('field') != 'card_reference' or change.get('selected_options'):
        return changes, []
    try:
        literal = _current_literal_source(message, change, changes)
    except (DomainError, TypeError, ValueError, KeyError, AttributeError, StopIteration):
        return changes, []
    if literal is None:
        return changes, []
    ref = change['entity_ref']; entry = offered['entity_bindings'][ref]
    if entry.get('read_recovery_only'):
        return changes, []  # An interrupted recheck never provides a write selector.
    # Reuse the entire original immutable receipt audit, not a value copied
    # from the model. READ_CONSUMERS/checked_identity deliberately stay intact.
    reference = checked_identity(session, principal, thread, entry, 'card_reference', 'CREDIT_VIEW')
    from .credit_billing import owned_credit_card
    card = owned_credit_card(session, principal, reference)
    if literal.lower() != card.card_number or entry.get('card_id') != card.id:
        return changes, []
    from copy import deepcopy
    projected = deepcopy(changes)
    projected[changes.index(change)]['entity_ref'] = None
    ledger = [{'action_ref': 'new:0', 'entity_ref': ref, 'field': 'card_reference',
        'source_span': deepcopy(change['source_span']), 'source_message_hash': canonical_hash({'message': message}),
        'source_span_hash': canonical_hash({'literal': message[change['source_span']['start']:change['source_span']['end']]}),
        'receipt_hash': entry['receipt_hash'], 'card_identity_hash': entry['card_identity_hash'],
        'compiled_literal_hash': canonical_hash({'card_reference': literal}),
        'bank_write_authorized': False, 'confirmation_inherited': False}]
    return projected, ledger


def _deny():
    raise DomainError("SEMANTIC_CREDIT_IDENTITY_SOURCE_CHANGED",
        "这份已完成信用业务的卡片身份无法核验，请明确卡片或重新查询；不会据旧回执办理业务。", 409)


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def card_identities(session, principal, thread, parent, before_sequence):
    """Audit the original native receipt and return immutable card identity."""
    try:
        return [*_card_identities(session, principal, thread, parent, before_sequence),
            *_merchant_card_identities(session, principal, thread, parent, before_sequence)]
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        _deny()


def _merchant_card_identities(session,principal,thread,parent,before_sequence):
    """The verified purchase identifies its credit card for fresh reads only.

    Reuse the original purchase/receipt/owner proof. No past balance, amount,
    repayment source or consumed confirmation becomes new banking authority.
    """
    from .semantic_merchant_payment_identity import payment_identities
    from .credit_models import PaymentRecord
    from .credit_billing import owned_credit_card
    parent=session.get(Task,parent) if isinstance(parent,str) else parent
    thread=session.get(ChatThread,thread) if isinstance(thread,str) else thread
    if not _owned(parent,principal) or parent.state!='SUCCEEDED':return []
    if session.scalar(select(RuntimeOperation.id).where(RuntimeOperation.parent_task_id==parent.id,
            RuntimeOperation.active==1,RuntimeOperation.action_kind=='MERCHANT_PURCHASE',RuntimeOperation.status=='SUCCEEDED')) is None:return []
    out=[]
    for proof in payment_identities(session,principal,thread,parent,before_sequence):
        payment=session.get(PaymentRecord,proof['payment_id'])
        if not _owned(payment,principal) or payment.instrument_type!='CREDIT' or payment.status!='POSTED':_deny()
        card=owned_credit_card(session,principal,payment.instrument_id)
        if payment.credit_account_id!=card.credit_account_id:_deny()
        identity={'card_id':card.id,'card_number':card.card_number,'credit_account_id':card.credit_account_id,
            'user_id':card.user_id,'sandbox_id':card.sandbox_id}
        out.append({**proof,'source_kind':KIND,'field':'card_reference','value':payment.instrument_id,
            'card_reference':payment.instrument_id,'card_id':card.id,'credit_account_id':card.credit_account_id,
            'receipt_hash':proof['payment_receipt_hash'],'card_identity_hash':canonical_hash(identity),
            'label':'信用卡尾号'+card.card_number[-4:]+' · 刚才消费使用的卡片身份'})
    return out


def _card_identities(session, principal, thread, parent, before_sequence, *, read_recovery=False):
    thread = session.get(ChatThread, thread) if isinstance(thread, str) else thread
    parent = session.get(Task, parent) if isinstance(parent, str) else parent
    if (not _owned(thread, principal) or thread.status != "ACTIVE"
            or not _owned(parent, principal) or parent.state not in ({'PARTIAL'} if read_recovery else {'SUCCEEDED'})
            or type(before_sequence) is not int or before_sequence <= 0):
        return []
    record = session.get(RuntimeRecord, parent.id)
    metadata = record.metadata_json if record else None
    native = metadata.get("cloud_semantic_intent") if isinstance(metadata, dict) else None
    if not record or record.kind != "PARENT" or not isinstance(native, dict):
        return []
    operations = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.parent_task_id == parent.id, RuntimeOperation.active == 1,
        RuntimeOperation.action_kind.in_(SOURCE_KINDS), RuntimeOperation.status == "SUCCEEDED")
        .order_by(RuntimeOperation.ordinal)))
    if not operations:
        return []  # Do not audit every unrelated completed write.
    origin = session.get(ChatTurn, native.get("source_turn_id"))
    if (not _owned(origin, principal) or origin.thread_id != thread.id or origin.task_id != parent.id
            or origin.status != "ANSWERED" or type(origin.sequence) is not int or origin.sequence >= before_sequence):
        return []
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version))
    if (snapshot is None or snapshot.plan_hash != parent.plan_hash
            or canonical_hash(parent.plan_json) != parent.plan_hash or snapshot.plan_json != parent.plan_json):
        _deny()
    from .semantic_proposal import validate_semantic_source_snapshot
    from .runtime import validate_runtime_family_context
    validate_runtime_family_context(session, parent)
    validate_semantic_source_snapshot(session, principal, parent, metadata, snapshot)

    from .conversation_provenance import current_plan_completed_write
    from .credit_billing import owned_credit_card
    from .credit_receipts import completed_credit_receipt_integrity
    from .credit_runtime import ACTION_TOOLS
    from .card_identifiers import CARD_NUMBER_PATTERN
    from .permissions import actor_role
    out = []
    for operation in operations:
        child = session.get(Task, operation.child_task_id) if operation.child_task_id else None
        child_record = session.get(RuntimeRecord, operation.child_task_id) if child else None
        bound = [node for node in snapshot.plan_json.get("nodes", [])
            if isinstance(node, dict) and node.get("operation_id") == operation.id]
        if (operation.plan_version != record.plan_version or len(bound) != 1
                or bound[0].get("node_id") != operation.operation_key or bound[0].get("kind") != operation.action_kind
                or bound[0].get("args") != operation.args_json or bound[0].get("depends_on") != operation.depends_on_json
                or not _owned(child, principal) or child.state != "SUCCEEDED"
                or not child_record or child_record.kind != "CHILD" or child_record.root_task_id != parent.id
                or child_record.metadata_json.get("operation_id") != operation.id
                or child_record.metadata_json.get("prepared_actor_role") != actor_role(session, principal.sandbox_id, principal.user_id)
                or not current_plan_completed_write(session, parent, operation)):
            _deny()
        tool = ACTION_TOOLS[operation.action_kind]
        calls = list(session.scalars(select(ToolCall).where(ToolCall.task_id == child.id,
            ToolCall.sandbox_id == principal.sandbox_id, ToolCall.tool_name == tool, ToolCall.status == "SUCCESS")))
        confirmations = list(session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
            Confirmation.sandbox_id == principal.sandbox_id, Confirmation.user_id == principal.user_id,
            Confirmation.action_type == tool, Confirmation.status == "CONSUMED")))
        if (len(calls) != 1 or len(confirmations) != 1
                or not completed_credit_receipt_integrity(session, parent, operation, child, calls[0], confirmations[0])):
            _deny()
        args = child.plan_json["nodes"][0]["args"]
        reference = args.get("card_reference")
        if not isinstance(reference, str) or not reference.startswith("credit:"):
            _deny()
        card = owned_credit_card(session, principal, reference)
        if (reference != "credit:" + card.id or confirmations[0].target_id != card.id
                or not re.fullmatch(CARD_NUMBER_PATTERN, card.card_number)):
            _deny()
        # The selected physical card must belong to the actual posted credit
        # facility, including installment receipts that do not repeat card_id.
        if operation.action_kind in CONTROL_KINDS:
            if child.result_json.get('card_id') != card.id or child.result_json.get('schema_version') != 'credit-card-control-v1':
                _deny()
            effect = card
        elif operation.action_kind == "CREDIT_INSTALLMENT":
            effect = session.get(CreditInstallment, child.result_json.get("installment_id"))
        else:
            effect = session.get(CreditRepayment, child.result_json.get("repayment_id"))
        if effect is None or effect.credit_account_id != card.credit_account_id:
            _deny()
        label = "信用卡尾号" + card.card_number[-4:] + " · 已完成业务中的卡片身份"
        from .semantic_router import _text
        _text(label, 160, private=True)
        out.append({"source_kind": KIND, "task_id": parent.id, "task_version": parent.version,
            "plan_version": record.plan_version, "snapshot_id": snapshot.id, "plan_hash": snapshot.plan_hash,
            "source_binding_hash": native["binding_hash"], "source_turn_id": origin.id,
            "conversation_id": thread.id, "before_sequence": before_sequence, "operation_id": operation.id,
            "child_task_id": child.id, "child_result_hash": canonical_hash(child.result_json),
            "confirmation_id": confirmations[0].id, "receipt_hash": canonical_hash({
                "confirmation_id": confirmations[0].id, "view_hash": confirmations[0].view_hash,
                "request_hash": calls[0].request_hash, "tool_call_id": calls[0].id, "result": calls[0].result_json}),
            "card_id": card.id, "credit_account_id": card.credit_account_id, "card_reference": reference,
            "card_identity_hash": canonical_hash({"card_id": card.id, "card_number": card.card_number,
                "credit_account_id": card.credit_account_id, "user_id": card.user_id, "sandbox_id": card.sandbox_id}),
            "label": label, "field": "card_reference", "value": reference})
    return out


def checked_identity(session, principal, thread, entry, field, action_kind):
    """The compiler also requires an explicit NEW READ/current-span selection."""
    if (not isinstance(entry, dict) or entry.get("source_kind") != KIND
            or field != "card_reference" or action_kind not in READ_CONSUMERS):
        _deny()
    parent = session.get(Task, entry.get("task_id"))
    provider = repayment_read_recovery_identities if entry.get('read_recovery_only') else card_identities
    if parent is None or entry not in provider(session, principal, thread, parent, entry.get("before_sequence")):
        _deny()
    return entry["card_reference"]


def repayment_read_recovery_identities(session, principal, thread, parent, before_sequence):
    """Only posted repayment + interrupted READs may expose a READ-only identity."""
    parent = session.get(Task, parent) if isinstance(parent, str) else parent
    if not _owned(parent, principal) or parent.state != 'PARTIAL':
        return []
    ops = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.parent_task_id == parent.id, RuntimeOperation.active == 1)))
    writes = [op for op in ops if op.action_kind == 'CREDIT_REPAY']
    if len(writes) != 1 or writes[0].status != 'SUCCEEDED' or not any(
            op.action_kind in READ_CONSUMERS and op.status == 'FAILED' for op in ops) \
            or any(op.action_kind not in READ_CONSUMERS | {'CREDIT_REPAY'} or
                op.status not in {'SUCCEEDED', 'FAILED', 'BLOCKED'} for op in ops):
        return []
    return [{**proof, 'read_recovery_only': True,
        'label': proof['label'].split(' · ')[0] + ' · 已还款，继续查询'}
        for proof in _card_identities(session, principal, thread, parent, before_sequence, read_recovery=True)]


def installment_identities(session,principal,thread,parent,before_sequence):
    values=[]
    for card in card_identities(session,principal,thread,parent,before_sequence):
        operation=session.get(RuntimeOperation,card['operation_id'])
        if operation.action_kind!='CREDIT_INSTALLMENT':continue
        child=session.get(Task,card['child_task_id']);item=session.get(CreditInstallment,child.result_json.get('installment_id'))
        if not item or item.credit_account_id!=card['credit_account_id']:_deny()
        values.append({**card,'source_kind':INSTALLMENT_KIND,'field':'installment_id','value':item.id,'installment_id':item.id,
            'label':f"刚才办理的{item.periods}期分期 · 信用卡尾号{card['label'].split(' · ')[0][-4:]}",
            'identity_only':True,'bank_write_authorized':False})
    return values


def checked_installment_identity(session,principal,thread,entry,field,action_kind):
    if field!='installment_id' or action_kind!='CREDIT_SETTLE_INSTALLMENT' or entry.get('source_kind')!=INSTALLMENT_KIND:_deny()
    parent=session.get(Task,entry.get('task_id'))
    if not parent or entry not in installment_identities(session,principal,thread,parent,entry['before_sequence']):_deny()
    return entry['installment_id']
