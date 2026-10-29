"""Source-checked repayment closure for narration, never payment authority.

Historical posting and a fresh post-payment READ are different facts. A failed
or stale recheck must not erase a successful payment or reuse the earlier bill.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import hmac
import secrets

from sqlalchemy import select

from .authentication import as_utc
from .contracts import DomainError
from .models import Confirmation, Task, ToolCall
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

KEY = 'credit_repayment_closure'
VERSION = 'credit-repayment-closure-v1'
_SEAL = secrets.token_bytes(32)
_READS = {'CREDIT_STATEMENT', 'CREDIT_VIEW'}


def _shape(trace):
    if not isinstance(trace, dict) or trace.get('state') not in {'SUCCEEDED', 'PARTIAL'} \
            or trace.get('pending_confirmation') is not None:
        return None
    nodes = trace.get('nodes')
    if not isinstance(nodes, list) or not nodes or any(not isinstance(n, dict)
            or n.get('action') not in _READS | {'CREDIT_REPAY'} for n in nodes):
        return None
    writes = [n for n in nodes if n['action'] == 'CREDIT_REPAY']
    if len(writes) != 1 or writes[0].get('status') != 'SUCCEEDED':
        return None
    return {'task_id': trace.get('task_id'), 'version': trace.get('version'), 'state': trace['state'],
        'nodes': [{k: n.get(k) for k in ('node_id', 'operation_id', 'child_task_id', 'action',
            'status', 'depends_on', 'result')} for n in nodes]}


def checked_closure(trace):
    value = trace.get(KEY) if isinstance(trace, dict) else None
    shape = _shape(trace)
    if not isinstance(value, dict) or shape is None:
        return None
    body = deepcopy(value)
    seal = body.pop('source_seal', None)
    if body.get('schema_version') != VERSION or body.get('projection_hash') != canonical_hash(shape) \
            or not isinstance(seal, str):
        return None
    expected = hmac.new(_SEAL, canonical_hash(body).encode('ascii'), hashlib.sha256).hexdigest()
    return body if hmac.compare_digest(expected, seal) else None


def fingerprint_closure(trace):
    return checked_closure(trace)  # Process-local seal is not a business version.


def _descends_from(node, ancestor, by_key, seen=None):
    seen = set(seen or ())
    key = node.get('node_id')
    if key in seen:
        return False
    seen.add(key)
    return any(dep == ancestor or dep in by_key and _descends_from(by_key[dep], ancestor, by_key, seen)
        for dep in (node.get('depends_on') or []))


def attach_repayment_closure(session, principal, parent, trace):
    trace = {k: v for k, v in trace.items() if k != KEY}
    shape = _shape(trace)
    if shape is None:
        return trace
    from .customer_profile_common import require_profile_actor
    require_profile_actor(session, principal)
    if not parent or (parent.user_id, parent.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError('PROFILE_OWNER_REQUIRED', '当前信用卡资料不属于本人。', 403)
    try:
        payload = _verify_closure(session, principal, parent, trace, shape)
    except DomainError as error:
        if error.status_code in {401, 403}:
            raise
        return trace
    except (ValueError, TypeError, KeyError, AttributeError):
        return trace
    if payload is None:
        return trace
    payload['source_seal'] = hmac.new(_SEAL, canonical_hash(payload).encode('ascii'), hashlib.sha256).hexdigest()
    return {**trace, KEY: payload}


def _verify_closure(session, principal, parent, trace, shape):
    from .intelligence import action_signature
    from .credit_receipts import completed_credit_receipt_integrity
    record = session.get(RuntimeRecord, parent.id)
    if parent.id != shape['task_id'] or parent.version != shape['version'] or parent.state != shape['state'] \
            or trace.get('audit_chain_valid') is not True or not record or record.kind != 'PARENT':
        return None
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == parent.id,
        PlanSnapshot.version == record.plan_version))
    if not snapshot or snapshot.plan_hash != parent.plan_hash or canonical_hash(snapshot.plan_json) != parent.plan_hash \
            or canonical_hash(parent.plan_json) != parent.plan_hash:
        return None
    operations = {}
    for node in shape['nodes']:
        op = session.get(RuntimeOperation, node['operation_id'])
        bound = [row for row in snapshot.plan_json.get('nodes', []) if row.get('operation_id') == node['operation_id']]
        if not op or len(bound) != 1 or op.parent_task_id != parent.id or op.sandbox_id != principal.sandbox_id \
                or op.active != 1 or op.plan_version != record.plan_version or op.status != node['status'] \
                or op.action_kind != node['action'] or op.child_task_id != node['child_task_id'] \
                or op.operation_key != node['node_id'] or op.depends_on_json != node['depends_on'] \
                or canonical_hash(action_signature(op.args_json)) != op.fingerprint \
                or bound[0].get('args') != op.args_json or bound[0].get('depends_on') != op.depends_on_json \
                or bound[0].get('node_id') != op.operation_key or bound[0].get('kind') != op.action_kind:
            return None
        operations[node['node_id']] = op
    write = next(n for n in shape['nodes'] if n['action'] == 'CREDIT_REPAY')
    op = operations[write['node_id']]
    child = session.get(Task, op.child_task_id)
    if not child or write['result'] != child.result_json:
        return None
    call = session.scalar(select(ToolCall).where(ToolCall.task_id == child.id,
        ToolCall.idempotency_key == op.idempotency_key, ToolCall.status == 'SUCCESS'))
    confirmations = session.scalars(select(Confirmation).where(Confirmation.task_id == child.id,
        Confirmation.status == 'CONSUMED'))
    confirmation = next((c for c in confirmations if completed_credit_receipt_integrity(
        session, parent, op, child, call, c)), None)
    if confirmation is None:
        return None
    receipt = child.result_json
    payload = {'schema_version': VERSION, 'projection_hash': canonical_hash(shape),
        'repayment_id': receipt['repayment_id'], 'amount_minor': receipt['amount_minor'],
        'currency': receipt['currency'], 'debt_minor': None, 'available_credit_minor': None,
        'recheck_state': 'NOT_REQUESTED', 'read_operation_ids': []}
    by_key = {n['node_id']: n for n in shape['nodes']}
    followups = [n for n in shape['nodes'] if n['action'] in _READS
        and _descends_from(n, write['node_id'], by_key)]
    if not followups:
        return payload
    payload['recheck_state'] = 'UNAVAILABLE'
    all_valid = True
    for node in followups:
        checked = _post_read(session, principal, parent, snapshot, node, receipt, confirmation)
        if checked is None:
            all_valid = False
            continue
        payload['read_operation_ids'].append(node['operation_id'])
        debt, available = checked
        if debt is not None:
            if payload['debt_minor'] is not None and payload['debt_minor'] != debt:
                return None
            payload['debt_minor'] = debt
        if available is not None:
            payload['available_credit_minor'] = available
    if all_valid:
        payload['recheck_state'] = 'CURRENT'
    return payload


def _post_read(session, principal, parent, snapshot, node, receipt, confirmation):
    """Require exact owned card, same DAG, after-confirmation observation, live hash."""
    from .customer_read_history import _live_read
    from .credit_facts import credit_read_result_current
    if node['status'] != 'SUCCEEDED':
        return None
    child = session.get(Task, node['child_task_id'])
    result = node['result']
    try:
        if child is None or as_utc(child.created_at) < as_utc(confirmation.consumed_at) \
                or as_utc(datetime.fromisoformat(result['observed_at'])) < as_utc(confirmation.consumed_at):
            return None
        _live_read(session, principal, parent, snapshot, {**node,
            'analysis': child.analysis_json or None, 'observed_at': as_utc(child.updated_at).isoformat()})
        if not credit_read_result_current(session, principal, node['action'], result):
            return None
        if node['action'] == 'CREDIT_STATEMENT':
            if (result.get('card_id'), result.get('credit_account_id')) != (receipt['card_id'], receipt['credit_account_id']):
                return None
            rows = [row for row in result['statements_by_currency'] if row['currency'] == receipt['currency']]
            return (rows[0]['outstanding_minor'], None) if len(rows) == 1 else None
        cards = result.get('cards', [])
        if len(cards) != 1 or (cards[0].get('card_id'), cards[0].get('credit_account_id')) != (
                receipt['card_id'], receipt['credit_account_id']):
            return None
        rows = [row for row in cards[0]['balances'] if row['currency'] == receipt['currency']]
        return (rows[0]['outstanding_minor'], cards[0]['available_base_credit_minor']) if len(rows) == 1 else None
    except DomainError as error:
        if error.status_code in {401, 403}:
            raise
        return None
    except (ValueError, TypeError, KeyError):
        return None


def repayment_closure_response(trace, language='zh-CN'):
    payload = checked_closure(trace)
    if payload is None:
        return None
    english = language == 'en'
    money = lambda value: f'{Decimal(value) / 100:.2f}'
    amount, currency = money(payload['amount_minor']), payload['currency']
    intro = f'Your {currency} {amount} credit-card repayment was posted.' if english else f'信用卡还款{amount} {currency}已入账。'
    details = []
    if payload['debt_minor'] is not None:
        debt = money(payload['debt_minor'])
        details.append(f'The follow-up query shows {currency} {debt} still outstanding.' if english else
            f'还款后复查，该币种剩余欠款为{debt} {currency}。')
    if payload['available_credit_minor'] is not None:
        available = money(payload['available_credit_minor'])
        details.append(f'Available shared credit is CNY {available}.' if english else f'当前可用共享额度为{available} CNY。')
    if payload['recheck_state'] == 'UNAVAILABLE':
        details.append('Some follow-up results could not be verified. The repayment succeeded; retry only the query.' if english else
            '后续查询尚未全部核实。还款已成功，可继续查询欠款和额度，无需重复还款。')
    return {'intro': intro, 'detail': ' '.join(details) or None, 'detail_kind': 'CREDIT',
        'state_kind': 'PARTIAL' if trace['state'] == 'PARTIAL' else 'COMMITTED',
        'read_only': False, 'reply_language': language}
