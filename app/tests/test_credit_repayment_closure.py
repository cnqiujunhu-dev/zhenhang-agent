"""Real ASGI/runtime/ledger with disposable SQLite and controlled model transport."""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from test_access_runtime import named_runtime, authorization
from test_semantic_pro_asgi_entry import cloud_asgi, choice, drain, source_change
from test_semantic_upgraded_scope_integration import owner, requested
from test_semantic_credit_identity import _setup
from zhenxing.contracts import DomainError
from zhenxing.credit_models import CreditRepayment, CreditCard, PaymentJournal
from zhenxing.models import Account, Confirmation, Task
from zhenxing.runtime_models import RuntimeOperation
from zhenxing.credit_repayment_closure import KEY, checked_closure, repayment_closure_response


def prepare(h, data, *, independent=False, pre_read=True, other_card=None):
    kinds = (['CREDIT_STATEMENT'] if pre_read else []) + ['CREDIT_REPAY', 'CREDIT_VIEW']
    repay_index = len(kinds) - 2
    text = f"先查询{data['number']}信用卡欠款，用借记卡{data['debit']}还款10元，再复查信用卡欠款和可用额度。"
    if other_card:
        text += f'另外查询{other_card}信用卡。'
    changes = [source_change(text, 'card_reference', other_card if other_card and index == len(kinds)-1 else data['number'], 'new:'+str(index))
        for index in range(len(kinds))]
    changes += [source_change(text, field, value, 'new:'+str(repay_index)) for field, value in
        [('source_card', data['debit']), ('amount_minor', '10元')]]
    dependencies = [[] for _ in kinds]
    if pre_read:
        dependencies[repay_index] = [0]
    if not independent:
        dependencies[-1] = [repay_index]
    return requested(h, text, kinds[0], 'prepare-closure', factory=lambda _: choice('REQUEST',
        action_kinds=kinds, dependencies=dependencies, changes=changes))


def finish(h, trace):
    approval = authorization(h.http, h.headers, trace)
    result = h.http.post(f"/api/v2/tasks/{trace['task_id']}/confirm", headers=h.headers, json=approval)
    assert result.status_code == 202, result.text
    drain(h)
    return h.http.get(f"/api/v2/tasks/{trace['task_id']}/trace", headers=h.headers).json(), approval


def customer(h, identifier):
    response = h.http.get(f'/api/v3/customer/tasks/{identifier}', headers=h.headers)
    assert response.status_code == 200, response.text
    return response.json()


def sealed(h, identifier):
    from zhenxing.customer_reply_jobs import reply_trace
    with h.factory() as session:
        return reply_trace(session, session.get(Task, identifier))


def balances(h):
    with h.factory() as session:
        actor = owner(session)
        return (session.scalar(select(func.sum(Account.balance_minor)).where(Account.user_id == actor.user_id)),
            session.scalar(select(func.count()).select_from(CreditRepayment)))


def test_confirmed_dag_uses_only_fresh_post_payment_result_and_duplicate_is_not_repaid(cloud_asgi):
    h = cloud_asgi; data = _setup(h); before = balances(h)
    prepared = prepare(h, data)
    assert prepared['state'] == 'AWAITING_CONFIRMATION'
    assert balances(h) == before
    final, approval = finish(h, prepared)
    assert final['state'] == 'SUCCEEDED', final
    proof = checked_closure(sealed(h, final['task_id']))
    assert proof is not None
    assert proof['debt_minor'] == 58900 and proof['available_credit_minor'] is not None
    assert proof['recheck_state'] == 'CURRENT' and len(proof['read_operation_ids']) == 1
    view = customer(h, final['task_id'])
    response = view['assistant_response']
    assert '589.00' in response['detail'] and '599.00' not in response['detail']
    assert '10.00 CNY已入账' in response['intro'] and '当前可用共享额度' in response['detail']
    assert KEY not in view  # Internal proof never becomes customer data.
    assert balances(h) == (before[0]-1000, before[1]+1)
    duplicate = h.http.post(f"/api/v2/tasks/{final['task_id']}/confirm", headers=h.headers, json=approval)
    assert duplicate.status_code == 409
    drain(h)
    assert balances(h) == (before[0]-1000, before[1]+1)


def test_partial_payment_can_continue_with_read_only_pronoun_without_new_payment(cloud_asgi, monkeypatch):
    h = cloud_asgi; data = _setup(h)
    prepared = prepare(h, data)
    from zhenxing import credit_runtime
    original = credit_runtime.credit_view
    def failed(*args, **kwargs):
        raise DomainError('CREDIT_QUERY_UNAVAILABLE', '查询暂不可用', 422)
    monkeypatch.setattr(credit_runtime, 'credit_view', failed)
    partial, _ = finish(h, prepared)
    assert partial['state'] == 'PARTIAL'
    monkeypatch.setattr(credit_runtime, 'credit_view', original)
    before = balances(h)
    text = '那再查一下这张信用卡还有多少欠款和可用额度。'
    def decision(offered):
        candidates = [e for e in offered['entities'] if e.get('kind') == 'COMPLETED_CREDIT_RECEIPT_IDENTITY'
            and '继续查询' in e['label']]
        assert len(candidates) == 1, offered['entities']
        identity = candidates[0]
        assert set(identity['consumer_kinds']) == {'CREDIT_VIEW', 'CREDIT_STATEMENT'}
        selected = source_change(text, 'card_reference', '这张信用卡')
        selected['entity_ref'] = identity['entity_ref']
        return choice('REQUEST', action_kinds=['CREDIT_VIEW'], dependencies=[[]],
            changes=[selected], entity_refs=[identity['entity_ref']], question_aspects=['BALANCE'])
    recovered = requested(h, text, 'CREDIT_VIEW', 'read-recover', factory=decision)
    assert recovered['state'] == 'SUCCEEDED', recovered
    assert recovered['pending_confirmation'] is None
    card = recovered['nodes'][0]['result']['cards'][0]
    assert card['card_id'] == data['card_id']
    assert next(b for b in card['balances'] if b['currency'] == 'CNY')['outstanding_minor'] == 58900
    assert balances(h) == before


def test_paid_then_failed_recheck_preserves_payment_and_never_reuses_before_bill(cloud_asgi, monkeypatch):
    h = cloud_asgi; data = _setup(h); before = balances(h)
    prepared = prepare(h, data)
    from zhenxing import credit_runtime
    def failed(*args, **kwargs):
        raise DomainError('CREDIT_QUERY_UNAVAILABLE', '查询暂不可用', 422)
    monkeypatch.setattr(credit_runtime, 'credit_view', failed)
    final, _ = finish(h, prepared)
    assert final['state'] == 'PARTIAL', final
    proof = checked_closure(sealed(h, final['task_id']))
    assert proof['debt_minor'] is None and proof['recheck_state'] == 'UNAVAILABLE'
    response = customer(h, final['task_id'])['assistant_response']
    assert '已入账' in response['intro'] and '无需重复还款' in response['detail']
    assert '599.00' not in str(response) and '589.00' not in str(response)
    assert balances(h) == (before[0]-1000, before[1]+1)


def test_later_purchase_invalidates_old_recheck_but_not_historical_payment(cloud_asgi):
    h = cloud_asgi; data = _setup(h)
    final, _ = finish(h, prepare(h, data))
    initial = sealed(h, final['task_id'])
    assert checked_closure(initial)['recheck_state'] == 'CURRENT'
    from zhenxing.payment_instruments import post_merchant_payment, resolve_source_instrument
    with h.factory.begin() as session:
        actor = owner(session)
        post_merchant_payment(session, actor, instrument_snapshot=resolve_source_instrument(
            session, actor, 'credit:'+data['card_id'], purpose='MERCHANT'), amount_minor=200,
            merchant_ref='later-independent-purchase', operation_key='later-independent-purchase')
    current = sealed(h, final['task_id'])
    assert checked_closure(current)['debt_minor'] is None
    assert checked_closure(current)['recheck_state'] == 'UNAVAILABLE'
    from zhenxing.customer_reply_gateway import fingerprint
    assert fingerprint(initial) != fingerprint(current)
    response = customer(h, final['task_id'])['assistant_response']
    assert '已入账' in response['intro'] and '589.00' not in str(response)


def test_independent_pre_payment_read_is_never_post_payment_evidence(cloud_asgi):
    h = cloud_asgi; data = _setup(h)
    final, _ = finish(h, prepare(h, data, independent=True))
    proof = checked_closure(sealed(h, final['task_id']))
    assert proof['recheck_state'] == 'NOT_REQUESTED' and proof['debt_minor'] is None
    response = customer(h, final['task_id'])['assistant_response']
    assert '已入账' in response['intro'] and '599.00' not in str(response)


def test_failed_pre_query_does_not_prepare_or_execute_repayment(cloud_asgi, monkeypatch):
    h = cloud_asgi; data = _setup(h); before = balances(h)
    from zhenxing import credit_runtime
    def failed(*args, **kwargs):
        raise DomainError('CREDIT_QUERY_UNAVAILABLE', '查询暂不可用', 422)
    monkeypatch.setattr(credit_runtime, 'statement_view', failed)
    final = prepare(h, data)
    assert final['state'] == 'FAILED'
    assert final['pending_confirmation'] is None
    assert all(n['status'] == 'BLOCKED' and n['child_task_id'] is None for n in final['nodes'][1:])
    assert balances(h) == before
    assert checked_closure(sealed(h, final['task_id'])) is None


def test_other_card_in_same_facility_cannot_be_relabelled_as_repayment_recheck(cloud_asgi):
    h = cloud_asgi; data = _setup(h)
    from zhenxing.credit_bootstrap import issue_credit_card
    from zhenxing.credit_models import CreditAccount
    with h.factory.begin() as session:
        other = issue_credit_card(session, session.get(CreditAccount, data['facility_id']), status='ACTIVE')
        number = other.card_number
    final, _ = finish(h, prepare(h, data, other_card=number))
    assert final['state'] == 'SUCCEEDED'
    proof = checked_closure(sealed(h, final['task_id']))
    assert proof['debt_minor'] is None and proof['available_credit_minor'] is None
    assert proof['recheck_state'] == 'UNAVAILABLE'


def test_query_database_error_rolls_back_only_query_savepoint(cloud_asgi, monkeypatch):
    h = cloud_asgi; data = _setup(h); before = balances(h)
    prepared = prepare(h, data)
    from zhenxing import credit_runtime
    from sqlalchemy import text
    def db_error(session, *args, **kwargs):
        session.execute(text('INSERT INTO tasks(id) VALUES(NULL)'))
    monkeypatch.setattr(credit_runtime, 'credit_view', db_error)
    final, _ = finish(h, prepared)
    assert final['state'] == 'PARTIAL'
    assert checked_closure(sealed(h, final['task_id']))['recheck_state'] == 'UNAVAILABLE'
    assert balances(h) == (before[0]-1000, before[1]+1)
    assert '已入账' in customer(h, final['task_id'])['assistant_response']['intro']


def test_write_failure_is_not_swallowed_by_read_recovery(cloud_asgi, monkeypatch):
    h = cloud_asgi; data = _setup(h); before = balances(h)
    prepared = prepare(h, data)
    from zhenxing import credit_runtime
    def failed(*args, **kwargs):
        raise DomainError('PAYMENT_INSUFFICIENT_FUNDS', '资金不足', 409)
    monkeypatch.setattr(credit_runtime, 'repay_credit', failed)
    final, _ = finish(h, prepared)
    assert final['nodes'][1]['status'] == 'FAILED'
    assert final['nodes'][2]['status'] == 'BLOCKED'
    assert checked_closure(sealed(h, final['task_id'])) is None
    assert balances(h) == before


@pytest.mark.parametrize('tamper', ['result', 'dependency', 'version', 'seal', 'amount'])
def test_display_proof_cannot_be_rebound_or_forged(cloud_asgi, tamper):
    h = cloud_asgi; data = _setup(h)
    final, _ = finish(h, prepare(h, data))
    trace = deepcopy(sealed(h, final['task_id']))
    assert repayment_closure_response(trace)
    if tamper == 'result': trace['nodes'][-1]['result']['cards'][0]['available_base_credit_minor'] += 1
    elif tamper == 'dependency': trace['nodes'][-1]['depends_on'] = []
    elif tamper == 'version': trace['version'] += 1
    elif tamper == 'amount': trace[KEY]['amount_minor'] += 1
    else: trace[KEY]['source_seal'] = 'forged'
    assert repayment_closure_response(trace) is None
