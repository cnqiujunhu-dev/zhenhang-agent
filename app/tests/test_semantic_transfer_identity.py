"""Posted sender identity selects a new READ; SQLite + MockTransport only."""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi, choice, send, source_change, drain, finance_snapshot
from test_transfer_statement_card_scope import prepare_payment, confirm, explicit_endpoints
from zhenxing.access_models import LoginAccount
from zhenxing.contracts import DomainError
from zhenxing.conversation_models import ChatThread, ChatTurn
from zhenxing.models import Account, Card, Confirmation, LedgerEntry, Task, ToolCall, Transaction, Transfer
from zhenxing.payment_instruments import resolve_source_instrument
from zhenxing.runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord
from zhenxing.semantic_offered_contracts import checked_consumer_contract
from zhenxing.semantic_transfer_identity import KIND, VERSION, card_identities, checked_identity
from zhenxing.services import Principal


@pytest.fixture
def posted_transfer(cloud_asgi):
    h = cloud_asgi
    pending, scope, source, target = prepare_payment(h, 'TRANSFER')
    final = confirm(h, pending)
    assert final['state'] == 'SUCCEEDED'
    h.state['allow_isolated_confirmations'] = False
    return h, final['task_id'], source, target


def _binding(session, h, task_id):
    parent = session.get(Task, task_id)
    actor = Principal(parent.user_id, parent.sandbox_id)
    thread = session.get(ChatThread, h.thread_id)
    identities = card_identities(session, actor, thread, parent, thread.next_sequence)
    assert len(identities) == 1
    return actor, thread, parent, identities[0]


def _select(prepared, text, *, mode='REQUEST', kind='CARD_STATUS', consume=True, field='card_number'):
    candidates = [row for row in prepared['entities'] if row.get('kind') == KIND]
    assert len(candidates) == 1, prepared['entities']
    entity = candidates[0]
    assert entity['allowed_fields'] == ['card_number'] and entity['consumer_kinds'] == ['CARD_STATUS']
    assert entity['consumer_contract_version'] == VERSION
    assert not {'balance_minor', 'status', 'amount_minor', 'value', 'card_id', 'recipient_card'} & entity.keys()
    change = {**source_change(text, field, '付款卡'), 'entity_ref': entity['entity_ref']}
    return choice(mode, action_kinds=[kind] if mode == 'REQUEST' else [],
        dependencies=[[]] if mode == 'REQUEST' else [], entity_refs=[entity['entity_ref']],
        changes=[change] if consume else [], question_aspects=['BALANCE'])


def test_posted_transfer_sender_card_identity_reads_fresh_balance_and_state_without_second_payment(posted_transfer):
    h, task_id, source, target = posted_transfer
    with h.factory.begin() as session:
        actor, thread, parent, binding = _binding(session, h, task_id)
        assert binding['card_id'] == source['card_id'] and binding['value'] == source['number']
        assert binding['identity_only'] is True and binding['bank_write_authorized'] is False
        assert binding['card_id'] != target['card_id'] and 'recipient_card' not in binding
        assert not {'balance_minor', 'cash_balance_minor', 'available_balance_minor', 'status',
            'card_version', 'account_version', 'permission', 'amount_minor'} & binding.keys()
        # Explicit isolated fixture precondition: later legitimate banking
        # state must be read anew, never copied from the old transfer receipt.
        account = session.get(Account, source['account_id'])
        account.balance_minor += 517
        account.version += 1
        card = session.get(Card, source['card_id'])
        card.status = 'LOCKED'
        card.version += 1
        expected = resolve_source_instrument(session, actor, 'debit:' + card.id, purpose='READ')
        assert checked_identity(session, actor, thread, binding, 'card_number', 'CARD_STATUS') == source['number']
    before = finance_snapshot(h.factory)
    with h.factory() as session:
        counts = {model: session.scalar(select(func.count()).select_from(model)) for model in (Transfer, Confirmation, ToolCall)}
    text = '刚才这张付款卡现在余额还有多少，卡片状态呢？'
    h.state['decision_factory'] = lambda prepared: _select(prepared, text)
    turn = send(h, text, 'fresh-post-transfer-sender-card')
    assert turn.task_id and turn.task_id != task_id, turn.diagnostics_json
    drain(h)
    trace = h.http.get('/api/v2/tasks/' + turn.task_id + '/trace', headers=h.headers).json()
    assert trace['state'] == 'SUCCEEDED' and trace['pending_confirmation'] is None, trace
    assert len(trace['nodes']) == 1 and trace['nodes'][0]['action'] == 'CARD_STATUS', trace
    result = trace['nodes'][0]['result']
    cards = result['cards']
    assert len(cards) == 1 and cards[0]['card_id'] == source['card_id'], result
    assert cards[0]['status'] == 'LOCKED'
    assert str(expected['cash_balance_minor']) in str(result), result
    assert finance_snapshot(h.factory) == before
    with h.factory() as session:
        assert all(session.scalar(select(func.count()).select_from(model)) == count for model, count in counts.items())
        assert session.scalar(select(func.count()).select_from(Transfer)) == 1
    assert all(event['purpose'] != 'UNEXPECTED_PLANNER' for event in h.state['events'])


def test_pending_transfer_no_identity_then_original_epoch_thread_and_receipt_tampering_denied(cloud_asgi):
    h = cloud_asgi
    pending, scope, source, target = prepare_payment(h, 'TRANSFER')
    with h.factory() as session:
        parent = session.get(Task, pending['task_id']); actor = Principal(parent.user_id, parent.sandbox_id)
        thread = session.get(ChatThread, h.thread_id)
        assert card_identities(session, actor, thread, parent, thread.next_sequence) == []
    final = confirm(h, pending)
    with h.factory.begin() as session:
        actor, thread, parent, entry = _binding(session, h, final['task_id'])
        other = session.scalar(select(LoginAccount).where(LoginAccount.username == 'quyuwei'))
        assert card_identities(session, Principal(other.user_id, other.sandbox_id), thread, parent, thread.next_sequence) == []
        wrong_thread = ChatThread(user_id=actor.user_id, sandbox_id=actor.sandbox_id, request_hash='different-transfer-thread')
        session.add(wrong_thread); session.flush()
        assert card_identities(session, actor, wrong_thread, parent, thread.next_sequence) == []
        origin = session.get(ChatTurn, entry['source_turn_id'])
        assert card_identities(session, actor, thread, parent, origin.sequence) == []
        for kind in ('TRANSFER', 'FX_TRANSFER', 'CARD_LOCK', 'CARD_LOSS', 'BILL_ANALYSIS', 'CREDIT_VIEW', 'CREDIT_REPAY', None):
            with pytest.raises(DomainError): checked_identity(session, actor, thread, entry, 'card_number', kind)
        for field in ('card_last4', 'source_card', 'recipient_card_reference', 'amount_minor', 'balance_minor'):
            with pytest.raises(DomainError): checked_identity(session, actor, thread, entry, field, 'CARD_STATUS')
        for key in ('card_id', 'value', 'sender_statement_id', 'receipt_hash', 'identity_version', 'confirmation_id'):
            changed = {**deepcopy(entry), key: 'forged'}
            with pytest.raises(DomainError): checked_identity(session, actor, thread, changed, 'card_number', 'CARD_STATUS')
        for target_name in ('parent_state', 'native', 'source_turn', 'snapshot', 'operation', 'child_link', 'child_hash',
                'call_hash', 'call_result', 'confirmation', 'confirmation_target', 'statement_card', 'statement_amount',
                'statement_source', 'statement_ref', 'ledger', 'actor_version'):
            savepoint = session.begin_nested()
            try:
                child = session.get(Task, entry['child_task_id'])
                call = session.scalar(select(ToolCall).where(ToolCall.task_id == child.id))
                confirmation = session.get(Confirmation, entry['confirmation_id'])
                statement = session.get(Transaction, entry['sender_statement_id'])
                if target_name == 'parent_state': session.get(Task, entry['task_id']).state = 'UNKNOWN'
                elif target_name == 'native': session.get(RuntimeRecord, entry['task_id']).metadata_json = {}
                elif target_name == 'source_turn': session.get(ChatTurn, entry['source_turn_id']).status = 'FAILED'
                elif target_name == 'snapshot': session.get(PlanSnapshot, entry['snapshot_id']).plan_hash = '0' * 64
                elif target_name == 'operation': session.get(RuntimeOperation, entry['operation_id']).active = 0
                elif target_name == 'child_link': session.get(RuntimeRecord, child.id).root_task_id = child.id
                elif target_name == 'child_hash': child.plan_hash = '0' * 64
                elif target_name == 'call_hash': call.request_hash = '0' * 64
                elif target_name == 'call_result': call.result_json = {}
                elif target_name == 'confirmation': confirmation.status = 'PENDING'
                elif target_name == 'confirmation_target': confirmation.target_id = 'foreign-payee'
                elif target_name == 'statement_card': statement.card_id = source['other_card_id']
                elif target_name == 'statement_amount': statement.amount_minor -= 1
                elif target_name == 'statement_source': statement.source_kind = 'HISTORY'
                elif target_name == 'statement_ref': statement.operation_ref = 'another-transfer'
                elif target_name == 'ledger': session.scalar(select(LedgerEntry).where(LedgerEntry.delta_minor < 0)).delta_minor -= 1
                else: session.scalar(select(LoginAccount).where(LoginAccount.user_id == actor.user_id)).version += 1
                session.flush()
                with pytest.raises(DomainError): checked_identity(session, actor, thread, entry, 'card_number', 'CARD_STATUS')
            finally:
                savepoint.rollback(); session.expire_all()
        assert checked_identity(session, actor, session.get(ChatThread, h.thread_id), entry, 'card_number', 'CARD_STATUS') == source['number']


def test_unconsumed_reference_consult_write_wrong_field_and_implicit_literal_are_not_new_bank_work(posted_transfer):
    h, task_id, source, target = posted_transfer
    before = finance_snapshot(h.factory)
    text = '刚才这张付款卡现在余额还有多少？'
    for form in ('unconsumed', 'consult', 'write', 'wrong_field', 'implicit_literal'):
        def decision(prepared):
            result = _select(prepared, text, mode='CONSULT' if form == 'consult' else 'REQUEST',
                kind='TRANSFER' if form == 'write' else 'CARD_STATUS', consume=form not in {'unconsumed','consult'},
                field='source_card' if form == 'wrong_field' else 'card_number')
            if form == 'implicit_literal':
                result['entity_refs'] = []
                result['changes'][0]['entity_ref'] = None
            return result
        h.state['decision_factory'] = decision
        turn = send(h, text, 'refused-transfer-identity-' + form)
        assert turn.task_id is None and turn.mode == 'NEED_CONTEXT', (form, turn.diagnostics_json)
    assert finance_snapshot(h.factory) == before


def test_transfer_identity_contract_has_independent_version_and_exact_read_field_only():
    row = {'kind':KIND, 'domain':'CARDS', 'field':'card_number', 'read_kind':'CARD_STATUS',
        'allowed_fields':['card_number'], 'consumer_contract_version':VERSION, 'consumer_kinds':['CARD_STATUS']}
    assert checked_consumer_contract(row) == {'consumer_contract_version':VERSION,'consumer_kinds':['CARD_STATUS']}
    for changed in ({'allowed_fields':['card_number','source_card']}, {'consumer_kinds':['TRANSFER']},
            {'consumer_contract_version':'offered-action-entity-contract-v1'}, {'read_kind':'TRANSFER'}):
        with pytest.raises(ValueError): checked_consumer_contract({**row, **changed})
    with pytest.raises(ValueError): checked_consumer_contract({key:value for key,value in row.items()
        if key not in {'consumer_contract_version','consumer_kinds'}})


def test_card_read_then_entity_funded_confirmed_transfer_then_receipt_fresh_read_survives_old_balance_change(cloud_asgi):
    h = cloud_asgi
    source, target = explicit_endpoints(h)
    with h.factory.begin() as session:
        account = session.get(Account, source['account_id'])
        account.balance_minor = 14891364
        account.version += 1
    text = f"查我的{source['number']}借记卡余额。"
    h.state['semantic'] = choice('REQUEST', action_kinds=['CARD_STATUS'], dependencies=[[]],
        changes=[source_change(text, 'card_number', source['number'])], question_aspects=['BALANCE'])
    first = send(h, text, 'original-funding-card-read')
    assert first.task_id, first.diagnostics_json
    drain(h)
    text = f"从这张付款卡给{target['number']}转12.34元。"
    def transfer_decision(prepared):
        entity = next(row for row in prepared['entities'] if row.get('read_kind') == 'CARD_STATUS'
            and row.get('field') == 'card_number' and source['number'][-4:] in row['label'])
        change = {**source_change(text, 'card_number', '这张付款卡'), 'entity_ref':entity['entity_ref']}
        return choice('REQUEST', action_kinds=['TRANSFER'], dependencies=[[]], entity_refs=[entity['entity_ref']],
            changes=[change, source_change(text,'payee_name',target['number']),source_change(text,'amount_minor','12.34元')])
    h.state['decision_factory'] = transfer_decision
    requested = send(h, text, 'entity-funded-transfer')
    assert requested.task_id, {key:(requested.diagnostics_json.get('semantic_diagnostics') or {}).get(key)
        for key in ('failure_reasons','semantic_validation_errors','semantic_failure_phase','semantic_source_error_code')}
    drain(h)
    pending = h.http.get('/api/v2/tasks/' + requested.task_id + '/trace', headers=h.headers).json()
    final = confirm(h, pending)
    assert final['state'] == 'SUCCEEDED', final
    h.state['allow_isolated_confirmations'] = False
    with h.factory() as session:
        actor, thread, parent, entry = _binding(session, h, final['task_id'])
        assert checked_identity(session, actor, thread, entry, 'card_number', 'CARD_STATUS') == source['number']
        assert session.get(Account, source['account_id']).balance_minor == 14890130
    before = finance_snapshot(h.factory)
    text = '刚才这张付款卡余额还有多少？'
    h.state['decision_factory'] = lambda prepared: _select(prepared, text)
    turn = send(h, text, 'entity-funded-transfer-fresh-balance')
    assert turn.task_id and turn.task_id != final['task_id'], turn.diagnostics_json
    drain(h)
    trace = h.http.get('/api/v2/tasks/' + turn.task_id + '/trace', headers=h.headers).json()
    assert trace['state'] == 'SUCCEEDED' and trace['pending_confirmation'] is None, trace
    assert trace['nodes'][0]['action'] == 'CARD_STATUS'
    result = trace['nodes'][0]['result']
    assert len(result['cards']) == 1 and result['cards'][0]['card_id'] == source['card_id'], result
    assert '14890130' in str(result) and '14891364' not in str(result), result
    assert finance_snapshot(h.factory) == before
    with h.factory() as session:
        assert session.scalar(select(func.count()).select_from(Transfer)) == 1
