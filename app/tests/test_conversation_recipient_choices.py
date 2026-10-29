"""Owned recipient choices through actual ASGI, cloud-semantic wire and confirmation."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, func

from test_access_runtime import named_runtime, authorization, login
from test_semantic_pro_asgi_entry import cloud_asgi, choice, source_change, send, drain, finance_snapshot
from test_banking_workflow_confirmation_asgi import banking_h
from zhenxing.conversation_models import ChatTurn
from zhenxing.conversation_service import process_chat_job
from zhenxing.models import Transfer, Card, utcnow
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.recipient_choices import KEY, KIND


def prepare(h):
    text = '给quyuwei转一块零三分人民币，备注午餐。'
    h.state['semantic'] = choice('REQUEST', action_kinds=['TRANSFER'], dependencies=[[]], changes=[
        source_change(text, field, literal) for field, literal in (
            ('payee_name', 'quyuwei'), ('amount_minor', '一块零三分人民币'), ('memo', '午餐'))])
    turn = send(h, text, 'recipient-initial')
    assert turn.task_id, turn.diagnostics_json
    drain(h)
    route = f'/api/v3/conversations/{h.thread_id}/turns/{turn.id}/recipient-choices'
    response = h.http.post(route, headers=h.headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['choices'], data
    return turn, data, route


def select_payload(data, index=0):
    return {'message': data['choices'][index]['label'], 'client_request_id': 'recipient-select',
        'current_task_id': data['task_id'], 'expected_task_version': data['task_version'],
        'explicit_task_selection': True, 'context_policy': 'AUTO',
        'recipient_choice': {'origin_turn_id': data['origin_turn_id'], 'choice_set_id': data['choice_set_id'],
            'choice_token': data['choices'][index]['choice_token']}}


def model_choice(data):
    entities = [row for row in data['entities'] if row.get('kind') == KIND]
    assert len(entities) == 2, data
    frame = next(row for row in data['frames'] if row['frame_ref'] == entities[0]['frame_ref'])
    action = next(row for row in frame['actions'] if row['kind'] == 'TRANSFER')
    return choice('CONTINUE', focus_ref=frame['frame_ref'], entity_refs=[row['entity_ref'] for row in entities],
        changes=[{**source_change(data['message'], row['field'], data['message'], action['action_ref']),
            'entity_ref': row['entity_ref']} for row in entities])


def test_choice_updates_same_task_keeps_amount_memo_requires_new_confirmation(banking_h):
    h = banking_h; turn, data, route = prepare(h)
    before = finance_snapshot(h.factory)
    assert h.http.post(route, headers=h.headers).json() == data  # stable token/expiry on re-read
    old_trace = h.http.get(f'/api/v2/tasks/{turn.task_id}/trace', headers=h.headers).json()
    h.state['decision_factory'] = model_choice
    body = select_payload(data)
    accepted = h.http.post(f'/api/v3/customer/conversations/{h.thread_id}/turns', headers=h.headers, json=body)
    assert accepted.status_code == 202, accepted.text
    assert process_chat_job(h.factory, h.gateway)
    drain(h)
    with h.factory() as db:
        new = db.get(ChatTurn, accepted.json()['turn_id'])
        assert new.task_id == turn.task_id and new.mode == 'REVISE', new.diagnostics_json
        active = db.scalar(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == turn.task_id,
            RuntimeOperation.active == 1))
        assert active.args_json['slots']['amount_minor'] == 103
        assert active.args_json['slots']['memo'] == '午餐'
        assert active.args_json['slots']['recipient_card_reference'].endswith(data['choices'][0]['card_last4'])
    trace = h.http.get(f'/api/v2/tasks/{turn.task_id}/trace', headers=h.headers).json()
    assert trace['state'] == 'AWAITING_CONFIRMATION', trace
    assert trace['pending_confirmation'] != old_trace['pending_confirmation']
    assert finance_snapshot(h.factory) == before
    duplicate = h.http.post(f'/api/v3/customer/conversations/{h.thread_id}/turns', headers=h.headers, json=body)
    assert duplicate.status_code == 202 and duplicate.json()['turn_id'] == accepted.json()['turn_id']
    h.state['allow_isolated_confirmations'] = True
    auth = authorization(h.http, h.headers, trace)
    assert h.http.post(f'/api/v2/tasks/{turn.task_id}/confirm', headers=h.headers, json=auth).status_code == 202
    drain(h)
    with h.factory() as db:
        transfers = list(db.scalars(select(Transfer)))
        assert len(transfers) == 1 and transfers[0].amount_minor == 103 and transfers[0].status == 'POSTED'


@pytest.mark.parametrize('fault', ['token', 'expired', 'card_changed', 'task_version', 'message', 'other_actor', 'other_thread'])
def test_stale_or_cross_scope_choice_never_submits_turn_or_money(banking_h, fault):
    h = banking_h; turn, data, _ = prepare(h); body = select_payload(data); headers = h.headers; thread = h.thread_id
    if fault == 'token': body['recipient_choice']['choice_token'] = 'x' * 32
    elif fault == 'expired':
        with h.factory.begin() as db:
            origin = db.get(ChatTurn, turn.id); stored = deepcopy(origin.diagnostics_json)
            stored[KEY]['expires_at'] = (utcnow() - timedelta(seconds=1)).isoformat(); origin.diagnostics_json = stored
    elif fault == 'card_changed':
        with h.factory.begin() as db:
            origin = db.get(ChatTurn, turn.id)
            db.get(Card, origin.diagnostics_json[KEY]['candidates'][0]['card_id']).version += 1
    elif fault == 'task_version': body['expected_task_version'] -= 1
    elif fault == 'message': body['message'] = '改为转3000元'
    elif fault == 'other_actor': headers = login(h.http, 'quyuwei', 'quyuwei88')
    elif fault == 'other_thread':
        thread = h.http.post('/api/v3/customer/conversations', headers=headers, json={'title': 'other'}).json()['conversation_id']
    before = finance_snapshot(h.factory)
    result = h.http.post(f'/api/v3/customer/conversations/{thread}/turns', headers=headers, json=body)
    assert result.status_code in {403, 404, 409, 422}, result.text
    assert finance_snapshot(h.factory) == before
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(ChatTurn).where(ChatTurn.client_request_id == 'recipient-select')) == 0


def test_choice_not_visible_to_other_actor_and_diagnostics_hide_tokens(banking_h):
    h = banking_h; turn, data, route = prepare(h)
    other = login(h.http, 'quyuwei', 'quyuwei88')
    assert h.http.post(route, headers=other).status_code in {403, 404, 409}
    for route in [f'/api/v3/conversations/{h.thread_id}', f'/api/v3/customer/conversations/{h.thread_id}']:
        text = h.http.get(route, headers=h.headers).text
        assert KEY not in text and data['choices'][0]['choice_token'] not in text


def test_structured_recipient_click_cannot_change_memo_even_with_exact_label_source(banking_h):
    h = banking_h; turn, data, _ = prepare(h)
    before = finance_snapshot(h.factory)
    def malicious_model(offered):
        decision = model_choice(offered)
        action_ref = decision['changes'][0]['action_ref']
        decision['changes'].append(source_change(offered['message'], 'memo', offered['message'], action_ref))
        return decision
    h.state['decision_factory'] = malicious_model
    accepted = h.http.post(f'/api/v3/customer/conversations/{h.thread_id}/turns', headers=h.headers,
        json=select_payload(data))
    assert accepted.status_code == 202
    assert process_chat_job(h.factory, h.gateway)
    with h.factory() as db:
        new = db.get(ChatTurn, accepted.json()['turn_id'])
        assert new.task_id is None and new.diagnostics_json.get('semantic_processing_error') in {
            'RECIPIENT_CHOICE_SCOPE_EXCEEDED', 'SEMANTIC_MEMO_SOURCE_MISMATCH'}
    assert finance_snapshot(h.factory) == before
