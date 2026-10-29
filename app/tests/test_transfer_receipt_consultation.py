"""The real failing CONSULT shape consumes verified posting facts, not balance.

Production ASGI/model-adapter flow with MockTransport and disposable bank data.
"""
from copy import deepcopy

import pytest
from sqlalchemy import select, func

from test_access_runtime import named_runtime
from test_semantic_pro_asgi_entry import cloud_asgi, choice, send, finance_snapshot, source_change
from test_semantic_transfer_identity import posted_transfer
from zhenxing.models import Account, Task, Transfer, LedgerEntry
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.semantic_transfer_identity import KIND, RECEIPT_KIND, RECEIPT_VERSION

ORIGINAL = '刚才这笔转给谁了，多少钱，用的是哪张收款卡？'


def select_receipt(kind=RECEIPT_KIND, aspects=None, *, write=False):
    def decide(data):
        row = next(item for item in data['entities'] if item.get('kind') == kind)
        if kind == RECEIPT_KIND:
            assert row['entity_ref'] == 'e0'
            assert row['allowed_fields'] == [] and row['available_aspects'] == ['SUMMARY', 'STATUS']
        if write:
            return choice('REQUEST', action_kinds=['TRANSFER'], dependencies=[[]], entity_refs=[row['entity_ref']],
                changes=[{**source_change(data['message'], 'payee_name', data['message']), 'entity_ref': row['entity_ref']}])
        return choice('CONSULT', focus_ref=row['frame_ref'], entity_refs=[row['entity_ref']],
            question_aspects=aspects or ['SUMMARY', 'STATUS'])
    return decide


@pytest.mark.parametrize('kind', [RECEIPT_KIND, KIND])
def test_original_question_and_consult_decision_use_posted_recipient_amount_and_card(posted_transfer, kind):
    h, task_id, source, target = posted_transfer
    before = finance_snapshot(h.factory)
    h.state['decision_factory'] = select_receipt(kind)
    turn = send(h, ORIGINAL, 'actual-receipt-followup-'+kind)
    assert turn.mode == 'CONSULT' and turn.task_id is None, turn.diagnostics_json
    assert 'SEMANTIC_TRANSFER_IDENTITY_REQUIRES_EXPLICIT_READ' not in str(turn.diagnostics_json)
    assert all(value in turn.answer for value in ['quyuwei', '0.13', source['number'][-4:], target['number'][-4:]])
    assert target['number'] not in turn.answer and source['number'] not in turn.answer
    assert '余额' not in turn.answer
    evidence = turn.response_meta_json['transfer_receipt_summary']
    assert evidence['version'] == RECEIPT_VERSION and evidence['verified'] is True
    assert len(evidence['selected']) == 1 and evidence['selected'][0]['task_id'] == task_id
    assert evidence['selected'][0]['source_id'].startswith('bank-receipt:')
    assert turn.diagnostics_json['bank_task_created'] is False and not turn.diagnostics_json['bank_write_authorized']
    assert finance_snapshot(h.factory) == before
    with h.factory() as db:
        assert db.scalar(select(func.count()).select_from(Transfer)) == 1
    actual = [event for event in h.state['events'] if event['purpose'] == 'SEMANTIC'][-1]
    assert actual['offered']['message'] == ORIGINAL


@pytest.mark.parametrize('kind', [RECEIPT_KIND, KIND])
def test_receipt_never_supplies_current_card_balance(posted_transfer, kind):
    h, task_id, source, target = posted_transfer
    with h.factory.begin() as db:
        db.get(Account, source['account_id']).balance_minor += 517
    before = finance_snapshot(h.factory)
    h.state['decision_factory'] = select_receipt(kind, ['BALANCE'])
    turn = send(h, '这张卡现在余额多少？', 'receipt-not-a-current-balance-'+kind)
    assert turn.mode == 'NEED_CONTEXT' and turn.task_id is None
    assert '0.13' not in (turn.answer or '')
    assert finance_snapshot(h.factory) == before


def test_receipt_has_no_writable_parameter_contract(posted_transfer):
    h, *_ = posted_transfer
    before = finance_snapshot(h.factory)
    h.state['decision_factory'] = select_receipt(write=True)
    turn = send(h, '照这笔再转一次。', 'receipt-not-a-new-payment')
    assert turn.mode == 'NEED_CONTEXT' and turn.task_id is None
    assert finance_snapshot(h.factory) == before


def test_receipt_tampered_after_model_offer_is_not_narrated_as_success(posted_transfer):
    h, task_id, *_ = posted_transfer
    h.state['decision_factory'] = select_receipt()
    def corrupt_fixture():
        with h.factory.begin() as db:
            leg = db.scalar(select(LedgerEntry).where(LedgerEntry.delta_minor < 0))
            leg.delta_minor -= 1
        h.state['semantic_side_effect'] = None
    h.state['semantic_side_effect'] = corrupt_fixture
    turn = send(h, ORIGINAL, 'receipt-source-changed')
    assert turn.mode == 'NEED_CONTEXT' and turn.task_id is None
    assert '转账已完成' not in (turn.answer or '')
    assert not turn.response_meta_json.get('transfer_receipt_summary', {}).get('verified')
