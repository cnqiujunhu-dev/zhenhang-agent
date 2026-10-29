"""A posted transfer's sender instrument may select one fresh card READ.

Only the actual debit statement supplies the card identity. No old amount,
balance, card state, receiver instrument or confirmation is inherited.
"""
from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .models import Confirmation, Task, ToolCall, Transaction, Transfer
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

KIND = 'COMPLETED_TRANSFER_RECEIPT_IDENTITY'
VERSION = 'completed-transfer-sender-card-read-v1'
RECEIPT_KIND = 'COMPLETED_TRANSFER_RECEIPT_FACTS'
RECEIPT_VERSION = 'completed-transfer-facts-consultation-v1'


def consumer_contract():
    return {'consumer_contract_version': VERSION, 'consumer_kinds': ['CARD_STATUS']}


def _deny():
    raise DomainError('SEMANTIC_TRANSFER_IDENTITY_SOURCE_CHANGED',
        '之前转账回执中的付款卡身份无法核验，请明确要查询的本人卡片。', 409)


def _owned(row, principal):
    return row is not None and (row.user_id, row.sandbox_id) == (principal.user_id, principal.sandbox_id)


def card_identities(session, principal, thread, parent, before_sequence):
    """Rebuild the original owned receipt, actor epoch and posted sender row."""
    try:
        return _card_identities(session, principal, thread, parent, before_sequence)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        _deny()


def _card_identities(session, principal, thread, parent, before_sequence):
    thread = session.get(ChatThread, thread) if isinstance(thread, str) else thread
    parent = session.get(Task, parent) if isinstance(parent, str) else parent
    if (not _owned(thread, principal) or thread.status != 'ACTIVE' or not _owned(parent, principal)
            or parent.state != 'SUCCEEDED' or type(before_sequence) is not int or before_sequence <= 0
            or getattr(principal, 'family_context', None)):
        return []
    record = session.get(RuntimeRecord, parent.id)
    metadata = record.metadata_json if record else None
    native = metadata.get('cloud_semantic_intent') if isinstance(metadata, dict) else None
    if not record or record.kind != 'PARENT' or not isinstance(native, dict):
        return []
    operations = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.parent_task_id == parent.id, RuntimeOperation.active == 1,
        RuntimeOperation.action_kind == 'TRANSFER', RuntimeOperation.status == 'SUCCEEDED')
        .order_by(RuntimeOperation.ordinal)))
    if not operations:
        return []
    origin = session.get(ChatTurn, native.get('source_turn_id'))
    if (not _owned(origin, principal) or origin.thread_id != thread.id or origin.task_id != parent.id
            or origin.status != 'ANSWERED' or type(origin.sequence) is not int or origin.sequence >= before_sequence):
        return []
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version))
    if (not snapshot or snapshot.plan_hash != parent.plan_hash
            or canonical_hash(parent.plan_json) != parent.plan_hash or snapshot.plan_json != parent.plan_json):
        _deny()
    from .conversation_provenance import current_plan_completed_write, validate_dialogue_actor_epoch
    from .semantic_proposal import validate_semantic_source_snapshot
    from .runtime import validate_runtime_family_context
    validate_dialogue_actor_epoch(session, parent, metadata)
    validate_runtime_family_context(session, parent)
    validate_semantic_source_snapshot(session, principal, parent, metadata, snapshot)
    from .semantic_status_facts import completed_transfer_facts
    verified = completed_transfer_facts(session, principal, parent)
    if not verified:
        _deny()
    from .payment_instruments import resolve_source_instrument
    out = []
    for operation in operations:
        child = session.get(Task, operation.child_task_id) if operation.child_task_id else None
        child_record = session.get(RuntimeRecord, child.id) if child else None
        if (not _owned(child, principal) or child.state != 'SUCCEEDED' or not child_record
                or child_record.kind != 'CHILD' or child_record.root_task_id != parent.id
                or child_record.metadata_json.get('operation_id') != operation.id
                or not current_plan_completed_write(session, parent, operation)):
            _deny()
        transfer = session.get(Transfer, child.result_json.get('transfer_id'))
        if not _owned(transfer, principal) or transfer.status != 'POSTED':
            _deny()
        receipts = [fact for fact in verified if fact.get('kind') == 'TRANSFER'
            and fact.get('source_id') == 'bank-receipt:' + transfer.id]
        statements = list(session.scalars(select(Transaction).where(
            Transaction.sandbox_id == principal.sandbox_id, Transaction.account_id == transfer.source_account_id,
            Transaction.source_kind == 'TRANSFER', Transaction.operation_ref == 'transfer:' + transfer.id)))
        if len(receipts) != 1 or len(statements) != 1:
            _deny()
        statement = statements[0]
        # Legacy card-less receipts remain usable for their original STATUS,
        # but never acquire today's default card or a guessed matching tail.
        if statement.card_id is None:
            continue
        if statement.amount_minor != -transfer.amount_minor or not isinstance(receipts[0].get('source_card'), dict):
            _deny()
        current = resolve_source_instrument(session, principal, 'debit:' + statement.card_id, 'CNY', purpose='READ')
        if current['instrument_type'] != 'DEBIT' or current['card_id'] != statement.card_id \
                or current['display_last4'] != receipts[0]['source_card'].get('display_last4'):
            _deny()
        calls = list(session.scalars(select(ToolCall).where(ToolCall.task_id == child.id,
            ToolCall.sandbox_id == principal.sandbox_id, ToolCall.status == 'SUCCESS',
            ToolCall.idempotency_key == operation.idempotency_key)))
        confirmations = list(session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
            Confirmation.sandbox_id == principal.sandbox_id, Confirmation.user_id == principal.user_id,
            Confirmation.plan_hash == child.plan_hash, Confirmation.status == 'CONSUMED',
            Confirmation.consumed_at.is_not(None))))
        if len(calls) != 1 or len(confirmations) != 1:
            _deny()
        card_identity = {key: current[key] for key in ('instrument_id', 'instrument_type', 'card_id', 'card_number', 'display_last4')}
        out.append({'source_kind': KIND, 'identity_version': VERSION,
            'task_id': parent.id, 'task_version': parent.version, 'plan_version': record.plan_version,
            'snapshot_id': snapshot.id, 'plan_hash': snapshot.plan_hash, 'source_binding_hash': native['binding_hash'],
            'source_turn_id': origin.id, 'conversation_id': thread.id, 'before_sequence': before_sequence,
            'operation_id': operation.id, 'child_task_id': child.id,
            'child_result_hash': canonical_hash(child.result_json), 'transfer_id': transfer.id,
            'sender_statement_id': statement.id, 'sender_statement_hash': canonical_hash({
                key: getattr(statement, key) for key in ('id', 'sandbox_id', 'account_id', 'card_id', 'amount_minor', 'source_kind', 'operation_ref')}),
            'confirmation_id': confirmations[0].id, 'receipt_hash': receipts[0]['receipt_hash'],
            'tool_receipt_hash': canonical_hash({'confirmation_id': confirmations[0].id,
                'view_hash': confirmations[0].view_hash, 'request_hash': calls[0].request_hash,
                'tool_call_id': calls[0].id, 'result': calls[0].result_json}),
            'card_id': current['card_id'], 'instrument_id': current['instrument_id'],
            'card_identity_hash': canonical_hash(card_identity),
            'label': '付款借记卡尾号' + current['display_last4'] + ' · 已完成转账中的本人卡片身份',
            'field': 'card_number', 'value': current['card_number'],
            'identity_only': True, 'bank_write_authorized': False})
    return out


def checked_identity(session, principal, thread, entry, field, action_kind):
    if (not isinstance(entry, dict) or entry.get('source_kind') != KIND
            or field != 'card_number' or action_kind != 'CARD_STATUS'):
        _deny()
    parent = session.get(Task, entry.get('task_id'))
    if parent is None or entry not in card_identities(session, principal, thread, parent, entry.get('before_sequence')):
        _deny()
    return entry['value']


def receipt_identities(session, principal, thread, parent, before_sequence, *, card_sources=None):
    """Offer verified posted receipts separately from current-card READ identity.

    The receipt has no writable fields. Its amount and receiver describe this
    posting only, never a present balance or authority for a second payment.
    """
    from copy import deepcopy
    from .semantic_status_facts import completed_transfer_facts
    sources = card_identities(session, principal, thread, parent, before_sequence) if card_sources is None else card_sources
    if not sources:
        return []
    facts = completed_transfer_facts(session, principal, parent)
    result = []
    for source in sources:
        matches = [fact for fact in facts if fact.get('source_id') == 'bank-receipt:' + source['transfer_id']
            and fact.get('receipt_hash') == source['receipt_hash']]
        if len(matches) != 1:
            _deny()
        fact = matches[0]
        result.append({'source_kind': RECEIPT_KIND, 'receipt_version': RECEIPT_VERSION,
            'task_id': source['task_id'], 'task_version': source['task_version'],
            'conversation_id': source['conversation_id'], 'before_sequence': before_sequence,
            'operation_id': source['operation_id'], 'transfer_id': source['transfer_id'],
            'receipt_hash': fact['receipt_hash'], 'receipt': deepcopy(fact),
            'label': ('已完成转账回执 · 收款人' + fact['recipient'] + ' · 转账金额、付款卡、收款卡与执行状态')[:160],
            'bank_write_authorized': False})
    return result


def checked_receipt(session, principal, thread, entry):
    """Legacy sender-card refs may explain their bound receipt, not card state."""
    if not isinstance(entry, dict) or entry.get('source_kind') not in {KIND, RECEIPT_KIND}:
        _deny()
    parent = session.get(Task, entry.get('task_id'))
    if parent is None:
        _deny()
    if entry['source_kind'] == KIND:
        if entry not in card_identities(session, principal, thread, parent, entry.get('before_sequence')):
            _deny()
        choices = receipt_identities(session, principal, thread, parent, entry.get('before_sequence'))
        matches = [row for row in choices if row['transfer_id'] == entry['transfer_id']
            and row['receipt_hash'] == entry['receipt_hash']]
        if len(matches) != 1:
            _deny()
        return matches[0]['receipt']
    choices = receipt_identities(session, principal, thread, parent, entry.get('before_sequence'))
    if entry not in choices:
        _deny()
    return entry['receipt']
