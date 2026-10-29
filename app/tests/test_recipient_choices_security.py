"""Independent privacy/lifecycle review of the recipient choice integration."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi, finance_snapshot, send, drain, choice, source_change
from test_banking_workflow_confirmation_asgi import banking_h
from test_conversation_recipient_choices import prepare, model_choice
from zhenxing.access_models import LoginAccount
from zhenxing.contact_models import ContactAlias
from zhenxing.conversation_models import ChatThread, ChatTurn
from zhenxing.conversation_service import submit_turn
from zhenxing.credit_models import CustomerPaymentDefaults
from zhenxing.models import Card, Task
from zhenxing.recipient_choices import KEY, KIND, SELECTION, _candidates, attach_choices
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.services import Principal


def owner(db):
    login = db.scalar(select(LoginAccount).where(LoginAccount.username == 'customerone'))
    return Principal(login.user_id, login.sandbox_id)


def test_exact_username_does_not_inventory_unshared_cards(banking_h):
    h = banking_h
    with h.factory.begin() as db:
        principal = owner(db)
        recipient = db.scalar(select(LoginAccount).where(LoginAccount.username == 'quyuwei'))
        defaults = db.scalar(select(CustomerPaymentDefaults).where(CustomerPaymentDefaults.user_id == recipient.user_id))
        designated = defaults.recipient_instrument_id.split(':', 1)[1]
        all_cards = list(db.scalars(select(Card).where(Card.user_id == recipient.user_id)))
        assert len(all_cards) >= 2
        offered = _candidates(db, principal, 'quyuwei')
        assert {row['card_id'] for row in offered} == {designated}
        assert _candidates(db, principal, 'quyu') == []
        alternate = next(card for card in all_cards if card.id != designated)
        db.add(ContactAlias(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
            alias_name='亲友备用收款卡', recipient_login_account_id=recipient.id,
            recipient_bank_account_id=alternate.account_id))
        db.flush()
        assert {row['card_id'] for row in _candidates(db, principal, 'quyuwei')} == {designated, alternate.id}


@pytest.mark.parametrize('field', [KEY, SELECTION, 'nested_choice'])
def test_original_field_audit_cannot_export_server_choice_evidence(banking_h, field):
    h = banking_h
    turn, data, _ = prepare(h)
    # Exercise both the issued-set row and the selected-turn marker independently
    # of model behavior; original source bytes must remain unchanged on denial.
    with h.factory.begin() as db:
        row = db.get(ChatTurn, turn.id)
        diagnostics = deepcopy(row.diagnostics_json)
        if field == SELECTION:
            diagnostics.pop(KEY)
            diagnostics[SELECTION] = {'choice_set_id': data['choice_set_id']}
            row.diagnostics_json = diagnostics
        elif field == 'nested_choice':
            stored = diagnostics.pop(KEY)
            diagnostics['semantic_source_bundle_v2'] = {'entity_bindings': {
                'e0': {'source_kind': KIND, 'candidate': stored['candidates'][0]}}}
            row.diagnostics_json = diagnostics
    url = f'/api/v3/conversations/{h.thread_id}/turns/{turn.id}/audit'
    response = h.http.get(url, headers=h.headers,
        params={'version': 'stored-turn-field-v1', 'field': 'diagnostics_json'})
    assert response.status_code == 403, response.text
    assert response.json()['error']['code'] == 'CHAT_AUDIT_PRIVATE_CHOICES'
    assert data['choices'][0]['choice_token'] not in response.text
    with h.factory() as db:
        assert db.get(ChatTurn, turn.id).diagnostics_json == diagnostics
    # Nonsecret original fields retain their byte-audit contract.
    other = h.http.get(url, headers=h.headers,
        params={'version': 'stored-turn-field-v1', 'field': 'suggestions_json'})
    assert other.status_code == 200, other.text


@pytest.mark.parametrize('existing_count', [38, 39, 40])
def test_context_never_injects_half_a_recipient_pair(banking_h, existing_count):
    h = banking_h; origin, data, _ = prepare(h)
    with h.factory.begin() as db:
        principal = owner(db)
        thread = db.get(ChatThread, h.thread_id)
        turn = submit_turn(db, principal, thread.id, message='尾号'+data['choices'][0]['card_last4']+'那张',
            client_request_id='choice-budget-review')
        stored = db.get(ChatTurn, origin.id).diagnostics_json[KEY]
        offered = {'actor_scope_hash': stored['actor_hash'],
            'frame_bindings': {'f0': {'task_id': data['task_id'], 'task_version': data['task_version']}},
            'model': {'frames': [{'frame_ref': 'f0', 'continuation_allowed': True, 'paused': False}],
                'entities': [{'entity_ref': 'e'+str(i)} for i in range(existing_count)]}, 'entity_bindings': {}}
        attach_choices(db, principal, thread, turn, offered)
        candidates = [row for row in offered['model']['entities'] if row.get('kind') == KIND]
        assert len(candidates) in {0, 2}
        if existing_count == 38:
            assert {row['field'] for row in candidates} == {'payee_name', 'recipient_card_reference'}
        assert len(offered['model']['entities']) <= 40


def test_natural_choice_cannot_reuse_a_set_after_task_revision(banking_h):
    h = banking_h; origin, data, _ = prepare(h)
    with h.factory.begin() as db:
        principal = owner(db)
        thread = db.get(ChatThread, h.thread_id)
        turn = submit_turn(db, principal, thread.id, message='尾号'+data['choices'][0]['card_last4']+'那张',
            client_request_id='choice-stale-review')
        stored = db.get(ChatTurn, origin.id).diagnostics_json[KEY]
        task = db.get(Task, data['task_id']); task.version += 1
        offered = {'actor_scope_hash': stored['actor_hash'],
            'frame_bindings': {'f0': {'task_id': task.id, 'task_version': task.version}},
            'model': {'frames': [{'frame_ref': 'f0', 'continuation_allowed': True, 'paused': False}],
                'entities': []}, 'entity_bindings': {}}
        attach_choices(db, principal, thread, turn, offered)
        assert offered['model']['entities'] == []
        assert offered['entity_bindings'] == {}


def test_task_changed_while_model_reads_natural_choice_cannot_apply(banking_h):
    h = banking_h; origin, data, _ = prepare(h)
    before = finance_snapshot(h.factory)
    h.state['decision_factory'] = model_choice

    def concurrent_revision():
        with h.factory.begin() as db:
            db.get(Task, data['task_id']).version += 1
        # One competing change, regardless of whether semantic recovery is enabled.
        h.state['semantic_side_effect'] = None

    h.state['semantic_side_effect'] = concurrent_revision
    result = send(h, '尾号'+data['choices'][0]['card_last4']+'那张', 'choice-model-race')
    assert result.mode == 'NEED_CONTEXT'
    assert result.diagnostics_json.get('semantic_processing_error') in {
        'CHAT_SELECTED_TASK_CHANGED', 'SEMANTIC_EXPLANATION_SOURCE_CHANGED'}, result.diagnostics_json
    assert result.diagnostics_json['bank_task_created'] is False
    assert finance_snapshot(h.factory) == before


def save_two_recipient_cards(h):
    """Both relations belong to the payer, even though the cards belong to one peer."""
    with h.factory.begin() as db:
        principal = owner(db)
        peer = db.scalar(select(LoginAccount).where(LoginAccount.username == 'quyuwei'))
        defaults = db.scalar(select(CustomerPaymentDefaults).where(CustomerPaymentDefaults.user_id == peer.user_id))
        designated = defaults.recipient_instrument_id.split(':', 1)[1]
        cards = list(db.scalars(select(Card).where(Card.user_id == peer.user_id).order_by(Card.id)))
        assert len(cards) == 2 and len({card.account_id for card in cards}) == 2
        for index, card in enumerate(cards):
            db.add(ContactAlias(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
                alias_name='亲友收款卡'+str(index+1), recipient_login_account_id=peer.id,
                recipient_bank_account_id=card.account_id))
        db.flush()
        from zhenxing.card_identifiers import card_identifier_view
        alternative = next(card for card in cards if card.id != designated)
        return card_identifier_view(db, alternative)['card_number']


def natural_card_decision(card_tail, *, mismatched=False):
    def decide(data):
        entities = [row for row in data['entities'] if row.get('kind') == KIND]
        assert len(entities) == 4
        matches = [row for row in entities if '尾号'+card_tail in row['label']]
        assert len(matches) == 2 and {row['field'] for row in matches} == {
            'payee_name', 'recipient_card_reference'}
        if mismatched:
            # Even two candidates naming the same person cannot donate their
            # payee/card halves to a different candidate's paired evidence.
            other_payee = next(row for row in entities if row not in matches and row['field'] == 'payee_name')
            matches = [other_payee, next(row for row in matches if row['field'] == 'recipient_card_reference')]
        frame = next(row for row in data['frames'] if row['frame_ref'] == matches[0]['frame_ref'])
        action = next(row for row in frame['actions'] if row['kind'] == 'TRANSFER')
        return choice('CONTINUE', focus_ref=frame['frame_ref'], entity_refs=[row['entity_ref'] for row in matches],
            changes=[{**source_change(data['message'], row['field'], data['message'], action['action_ref']),
                'entity_ref': row['entity_ref']} for row in matches])
    return decide


def test_natural_tail_selects_saved_alternate_card_in_same_task(banking_h):
    h = banking_h; alternate_number = save_two_recipient_cards(h)
    origin, candidates, _ = prepare(h)
    assert len(candidates['choices']) == 2
    before = finance_snapshot(h.factory)
    original_trace = h.http.get(f'/api/v2/tasks/{origin.task_id}/trace', headers=h.headers).json()
    with h.factory() as db:
        operation = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == origin.task_id,
            RuntimeOperation.active == 1))
        prior_slots = deepcopy(operation.args_json['slots'])
    message = '就尾号'+alternate_number[-4:]+'那张'
    h.state['decision_factory'] = natural_card_decision(alternate_number[-4:])
    result = send(h, message, 'natural-saved-alternate-card')
    assert result.task_id == origin.task_id and result.mode == 'REVISE', result.diagnostics_json
    assert SELECTION not in result.diagnostics_json  # No click token or structured selection was submitted.
    drain(h)
    with h.factory() as db:
        operation = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == origin.task_id,
            RuntimeOperation.active == 1))
        slots = operation.args_json['slots']
        assert slots['recipient_card_reference'] == alternate_number
        assert slots['amount_minor'] == 103 and slots['memo'] == '午餐' and slots['payee_name'] == 'quyuwei'
        assert {key: value for key, value in slots.items() if key != 'recipient_card_reference'} == {
            key: value for key, value in prior_slots.items() if key != 'recipient_card_reference'}
    final_trace = h.http.get(f'/api/v2/tasks/{origin.task_id}/trace', headers=h.headers).json()
    assert final_trace['state'] == 'AWAITING_CONFIRMATION'
    assert final_trace['pending_confirmation'] != original_trace['pending_confirmation']
    assert finance_snapshot(h.factory) == before
    semantic = [event for event in h.state['events'] if event['purpose'] == 'SEMANTIC'][-1]['offered']
    assert semantic['message'] == message
    assert len([row for row in semantic['entities'] if row.get('kind') == KIND]) == 4


def test_natural_tail_rejects_payee_and_card_from_different_candidates(banking_h):
    h = banking_h; alternate_number = save_two_recipient_cards(h)
    origin, candidates, _ = prepare(h)
    assert len(candidates['choices']) == 2
    before = finance_snapshot(h.factory)
    original_trace = h.http.get(f'/api/v2/tasks/{origin.task_id}/trace', headers=h.headers).json()
    h.state['decision_factory'] = natural_card_decision(alternate_number[-4:], mismatched=True)
    result = send(h, '就尾号'+alternate_number[-4:]+'那张', 'natural-mismatched-card-pair')
    assert result.mode == 'NEED_CONTEXT'
    assert result.diagnostics_json['semantic_processing_error'] == 'RECIPIENT_CHOICE_PAIR_REQUIRED', result.diagnostics_json
    assert result.diagnostics_json['bank_task_created'] is False
    final_trace = h.http.get(f'/api/v2/tasks/{origin.task_id}/trace', headers=h.headers).json()
    assert final_trace['version'] == original_trace['version']
    assert final_trace['pending_confirmation'] == original_trace['pending_confirmation']
    assert finance_snapshot(h.factory) == before
