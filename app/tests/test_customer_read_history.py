"""Production READ snapshot route with fake cloud and temporary SQLite only.

All confirmation/execution/challenge requests are blocked by the fixture. No
live API, local model or production database is involved.
"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import (cloud_asgi, choice, send, drain,
    finance_snapshot, source_change)
from zhenxing.conversation_models import ChatTurn, ChatThread
from zhenxing.customer_read_history import (STORE_KEY, retain_customer_read_snapshot,
    customer_read_results, customer_read_snapshot_status)
from zhenxing.customer_reply_jobs import reply_trace
from zhenxing.models import Task
from zhenxing.runtime_models import RuntimeRecord, RuntimeOperation, PlanSnapshot
from zhenxing.planning import canonical_hash
from zhenxing.services import Principal


def read(h, text='查询银行卡余额', kinds=('CARD_STATUS',), fields=(), key='read'):
    h.state['semantic'] = choice('REQUEST', action_kinds=list(kinds),
        dependencies=[[] for _ in kinds], changes=list(fields))
    row = send(h, text, key)
    assert row.status == 'ANSWERED' and row.task_id, row.diagnostics_json
    drain(h)
    return row


def path(h, row):
    return f'/api/v3/customer/conversations/{h.thread_id}/turns/{row.id}/read-results'


def marker(h, row):
    response = h.http.get(f'/api/v3/customer/conversations/{h.thread_id}', headers=h.headers)
    assert response.status_code == 200, response.text
    return next(item for item in response.json()['turns'] if item['turn_id'] == row.id)


def test_actual_read_retained_before_narration_and_get_cannot_confirm_or_write(cloud_asgi, monkeypatch):
    h = cloud_asgi; before = finance_snapshot(h.factory)
    monkeypatch.setenv('ZX_CUSTOMER_REPLY_ENABLED', '0')
    row = read(h)
    seen = marker(h, row)
    assert seen['read_result_snapshot_status'] == 'RETAINED', seen
    result = h.http.get(path(h, row), headers=h.headers)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['source_turn_id'] == row.id and body['source_conversation_id'] == h.thread_id
    assert body['read_only'] is True and body['bank_confirmation_permitted'] is False
    assert body['pending_confirmation'] is None and body['allowed_actions'] == []
    assert body['version'] == seen['read_result_snapshot_version']
    assert body['outcome']['kind'] == 'QUERY_RESULT' and body['outcome']['verified'] is True
    assert body['nodes'] and all(node['action'] == 'CARD_STATUS' and node['status'] == 'SUCCEEDED' for node in body['nodes'])
    assert body['assistant_response']['intro'] and body['result_observed_at']
    assert body['schema_version'] == '2.0'
    assert body['source_business_kind'] == 'QUERY_ONLY' and body['read_phase'] == 'QUERY_RESULT'
    assert body['assistant_response']['read_only'] is True and body['assistant_response']['state_kind'] == 'QUERY_RESULT'
    assert 'semantic_binding' not in str(body) and 'snapshot_hash' not in str(body)
    assert [event['purpose'] for event in h.state['events']] == ['SEMANTIC']
    assert finance_snapshot(h.factory) == before


def test_mixed_pending_keeps_read_then_amount_revision_preserves_exact_original_turn(cloud_asgi):
    h = cloud_asgi; before = finance_snapshot(h.factory)
    text = '分析2026年9月账单，然后给quyuwei转10元'
    first = read(h, text, ('BILL_ANALYSIS', 'TRANSFER'),
        (source_change(text, 'payee_name', 'quyuwei', 'new:1'),
         source_change(text, 'amount_minor', '10元', 'new:1')), 'mixed-read-first')
    original = h.http.get(path(h, first), headers=h.headers)
    assert original.status_code == 200, original.text
    original = original.json()
    assert [node['action'] for node in original['nodes']] == ['BILL_ANALYSIS']
    assert original['source_business_kind'] == 'MIXED_WORKFLOW' and original['read_phase'] == 'BEFORE_ACTION'
    assert original['analysis'] and original['pending_confirmation'] is None
    old_version = original['version']
    def revise(offered):
        frame = next(item for item in offered['frames'] if any(action['kind'] == 'TRANSFER' for action in item['actions']))
        action = next(action for action in frame['actions'] if action['kind'] == 'TRANSFER')
        return choice('REVISE', focus_ref=frame['frame_ref'], action_kinds=[item['kind'] for item in frame['actions']],
            changes=[source_change('改成15元', 'amount_minor', '15元', action['action_ref'])])
    h.state['decision_factory'] = revise
    later = send(h, '改成15元', 'mixed-write-revision')
    assert later.task_id == first.task_id and later.mode == 'REVISE', later.diagnostics_json
    drain(h)
    after = h.http.get(path(h, first), headers=h.headers)
    assert after.status_code == 200, after.text
    assert after.json() == original
    seen = marker(h, first)
    assert seen['read_result_snapshot_status'] == 'RETAINED' and seen['read_result_snapshot_version'] == old_version
    assert seen['task_link']['version'] > old_version
    assert finance_snapshot(h.factory) == before


@pytest.mark.parametrize('tamper', ['fact_result', 'fact_rehash', 'operation_kind', 'child_result',
    'source_message', 'foreign_parent', 'source_plan', 'source_context', 'binding_hash', 'version_rehash', 'turn_proof'])
def test_retained_read_rejects_forged_hash_and_wrong_exact_source(cloud_asgi, tamper):
    h = cloud_asgi; row = read(h)
    assert h.http.get(path(h, row), headers=h.headers).status_code == 200
    with h.factory.begin() as db:
        record = db.get(RuntimeRecord, row.task_id)
        metadata = deepcopy(record.metadata_json)
        item = metadata[STORE_KEY]['items'][0]
        fact = item['facts'][0]
        if tamper in {'fact_result', 'fact_rehash'}:
            fact['result'] = {'cards': []}
            if tamper == 'fact_rehash':
                item.pop('snapshot_hash'); item['snapshot_hash'] = canonical_hash(item)
        elif tamper == 'operation_kind': db.get(RuntimeOperation, fact['operation_id']).action_kind = 'TRANSFER'
        elif tamper == 'child_result': db.get(Task, fact['child_task_id']).result_json = {'cards': []}
        elif tamper == 'source_message': db.get(ChatTurn, row.id).message = '查看其他人的卡片'
        elif tamper == 'foreign_parent': db.get(Task, row.task_id).user_id = 'other-user'
        elif tamper == 'source_plan': db.get(PlanSnapshot, item['source_snapshot_id']).plan_json = {'nodes': []}
        elif tamper == 'source_context':
            item['source_conversation_id'] = 'other-thread'
            item.pop('snapshot_hash'); item['snapshot_hash'] = canonical_hash(item)
        elif tamper == 'binding_hash':
            item['semantic_binding']['binding_hash'] = '0' * 64
            item.pop('snapshot_hash'); item['snapshot_hash'] = canonical_hash(item)
        elif tamper == 'version_rehash':
            item['task_version'] += 1
            for fact in item['facts']: fact['task_version'] = item['task_version']
            item.pop('snapshot_hash'); item['snapshot_hash'] = canonical_hash(item)
        else:
            turn = db.get(ChatTurn, row.id)
            turn.diagnostics_json = {**turn.diagnostics_json, 'customer_read_snapshot_binding': {'snapshot_hash': '0' * 64}}
        record.metadata_json = metadata
    response = h.http.get(path(h, row), headers=h.headers)
    assert response.status_code in {404, 409}, response.text


def test_missing_legacy_store_is_not_inferred_from_latest_task_or_assistant_text(cloud_asgi):
    h = cloud_asgi; row = read(h)
    with h.factory.begin() as db:
        record = db.get(RuntimeRecord, row.task_id)
        record.metadata_json = {key: value for key, value in record.metadata_json.items() if key != STORE_KEY}
    assert marker(h, row)['read_result_snapshot_status'] == 'NOT_RETAINED'
    assert h.http.get(path(h, row), headers=h.headers).status_code == 404


def test_repeated_capture_is_immutable_first_snapshot_not_a_llm_reply_overwrite(cloud_asgi):
    h = cloud_asgi; row = read(h)
    with h.factory.begin() as db:
        parent, record = db.get(Task, row.task_id), db.get(RuntimeRecord, row.task_id)
        before = deepcopy(record.metadata_json[STORE_KEY])
        assert retain_customer_read_snapshot(db, parent, record, reply_trace(db, parent)) is False
        assert record.metadata_json[STORE_KEY] == before


def test_closed_thread_access_is_explicitly_blocked_not_disguised_as_missing_read(cloud_asgi):
    h = cloud_asgi; row = read(h)
    with h.factory.begin() as db: db.get(ChatThread, h.thread_id).status = 'CLOSED'
    response = h.http.get(path(h, row), headers=h.headers)
    assert response.status_code == 403, response.text


def test_owner_epoch_change_is_not_hidden_as_a_missing_historical_result(cloud_asgi):
    from zhenxing.access_models import LoginAccount
    from zhenxing.contracts import DomainError
    h = cloud_asgi; row = read(h)
    with h.factory.begin() as db:
        parent = db.get(Task, row.task_id)
        actor = db.scalar(select(LoginAccount).where(LoginAccount.user_id == parent.user_id,
            LoginAccount.sandbox_id == parent.sandbox_id))
        actor.version += 1
    with h.factory() as db:
        parent = db.get(Task, row.task_id); principal = Principal(parent.user_id, parent.sandbox_id)
        with pytest.raises(DomainError) as failed:
            customer_read_snapshot_status(db, principal, h.thread_id, row.id)
        assert failed.value.status_code == 403


def test_same_owner_other_thread_cannot_launder_a_source_turn_read(cloud_asgi):
    h = cloud_asgi; row = read(h)
    response = h.http.post('/api/v3/customer/conversations', headers=h.headers, json={'title': 'A different thread'})
    assert response.status_code == 201
    other = response.json()['conversation_id']
    denied = h.http.get(f'/api/v3/customer/conversations/{other}/turns/{row.id}/read-results', headers=h.headers)
    assert denied.status_code == 404, denied.text


def test_read_retention_observation_failure_does_not_fail_or_reissue_bank_work(cloud_asgi, monkeypatch):
    from zhenxing.customer_reply_jobs import enqueue_customer_reply
    h = cloud_asgi; row = read(h); before = finance_snapshot(h.factory)
    monkeypatch.setattr('zhenxing.customer_reply_jobs.reply_trace',
        lambda *_: (_ for _ in ()).throw(RuntimeError('isolated source observation failure')))
    with h.factory.begin() as db:
        parent, record = db.get(Task, row.task_id), db.get(RuntimeRecord, row.task_id)
        original_state = parent.state, parent.version
        retained = deepcopy(record.metadata_json[STORE_KEY])
        assert enqueue_customer_reply(db, parent) is None
        assert (parent.state, parent.version) == original_state
        assert record.metadata_json[STORE_KEY] == retained
        assert record.metadata_json['customer_read_snapshot_unavailable'] == 'SOURCE_NOT_RETAINED'
    assert finance_snapshot(h.factory) == before
