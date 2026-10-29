"""Actor-owned scheduled-transfer reads; no creation, cancellation or payment."""
from sqlalchemy import select
from .authentication import as_utc
from .banking_extension_models import ScopedScheduledTransfer, ScheduledTransferSeries
from .contracts import DomainError, FinancialIntentContract
from .models import EvidenceSnapshot, utcnow
from .planning import canonical_hash

VERSION = 'scheduled-transfers-read-v1'
ACTION = 'TRANSFER_SCHEDULE_VIEW'
TOOL = 'transfer.schedules.read.v1'
STATUS_LABELS = {'WAITING': '待执行', 'ACTIVE': '预约有效', 'COMPLETED': '已完成', 'POSTED': '已转出',
                 'FAILED': '未成功', 'CANCELLED': '已取消', 'EXECUTING': '处理中', 'UNKNOWN': '结果待核对'}


def schedules_view(session, principal):
    from .accounts import require_account_role
    from .schedule_presentation import schedule_projection
    login = require_account_role(session, principal, {'CUSTOMER', 'DEV_TEST'})
    rows = session.scalars(select(ScopedScheduledTransfer).where(
        ScopedScheduledTransfer.user_id == principal.user_id,
        ScopedScheduledTransfer.sandbox_id == principal.sandbox_id).order_by(ScopedScheduledTransfer.created_at.desc()))
    items = []
    for row in rows:
        series = session.scalar(select(ScheduledTransferSeries).where(ScheduledTransferSeries.schedule_id == row.id))
        items.append(schedule_projection(session, row, series))
    result = {'schema_version': VERSION, 'query_scope': {}, 'schedules': items,
        'owner_scope_hash': canonical_hash({'user_id': principal.user_id, 'sandbox_id': principal.sandbox_id}),
        'actor_epoch_hash': canonical_hash({'login_id': login.id, 'version': login.version, 'role': login.role}),
        'bank_state_changed': False}
    result['source_fingerprint'] = canonical_hash(result)
    result['observed_at'] = utcnow().isoformat()
    return result


def result_current(session, principal, result):
    if (not isinstance(result, dict) or result.get('schema_version') != VERSION
            or canonical_hash({k: v for k, v in result.items() if k not in {'source_fingerprint', 'observed_at'}}) != result.get('source_fingerprint')
            or schedules_view(session, principal)['source_fingerprint'] != result.get('source_fingerprint')):
        raise DomainError('SCHEDULE_READ_SOURCE_CHANGED', '转账安排已变化，请查询最新状态。', 409)
    return result


def result_entities(result):
    rows = result.get('schedules', [])
    return [{'read_kind': ACTION, 'domain': 'SCHEDULES', 'field': 'schedule_summary',
        'value': '本人定时转账安排', 'label': '本人已有定时转账的时间、金额和执行状态',
        'ordinal': None, 'record_id': result['owner_scope_hash'], 'record_version': result['source_fingerprint'],
        'live_record_hash': canonical_hash(rows), 'facts': {'schedules': rows, 'count': len(rows)}}] + [
        {'read_kind':ACTION,'domain':'SCHEDULES','field':'schedule_reference','value':row['schedule_id'],
         'label':row['payee_name']+' · '+(row.get('memo') or '预约转账')+' · '+row['due_at'],
         'ordinal':index,'record_id':row['schedule_id'],'record_version':canonical_hash(row),
         'live_record_hash':canonical_hash(row),'facts':row}
        for index,row in enumerate(rows,1)]


def summary(result):
    rows = result.get('schedules', [])
    if not rows:
        return '目前没有已登记的定时转账安排。', ''
    counts = {}
    for row in rows:
        label = STATUS_LABELS.get(row.get('status'), '状态待核对')
        counts[label] = counts.get(label, 0) + 1
    return f'查到{len(rows)}项定时转账安排。', '，'.join(f'{name}{count}项' for name, count in counts.items()) + '。'


def prepare_task(session, *, task, conversation_id, proposed_action):
    from .services import Principal
    from .platform import add_task_nodes, append_message
    from .workflow import transition_task
    if proposed_action.get('kind') != ACTION or any(value is not None for value in (proposed_action.get('slots') or {}).values()):
        raise DomainError('SCHEDULE_READ_SCOPE_INVALID', '查询范围无法核对。', 422)
    result = schedules_view(session, Principal(task.user_id, task.sandbox_id))
    evidence = EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id,
        source_kind='OWNED_SCHEDULE_SNAPSHOT', source_version=VERSION, payload_json=result)
    session.add(evidence); session.flush()
    task.plan_json = {'nodes': [{'node_id': 'read-schedules', 'tool_name': TOOL,
        'read_only': True, 'status': 'SUCCEEDED', 'evidence_id': evidence.id, 'args': {}}]}
    task.plan_hash = canonical_hash(task.plan_json)
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=task.plan_json)
    fic = FinancialIntentContract.model_validate(task.fic_json)
    fic.requested_capabilities, fic.status = [TOOL], 'READY'
    task.fic_json, task.result_json = fic.model_dump(), result
    transition_task(task, 'SUCCEEDED')
    append_message(session, conversation_id=conversation_id, sandbox_id=task.sandbox_id,
        task_id=task.id, role='ASSISTANT', content=' '.join(summary(result)))
    return task
