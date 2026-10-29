"""Isolate an optional credit READ from the write that made it ready."""
import logging

from .audit import append_audit
from .contracts import DomainError
from .models import Task
from .runtime_models import RuntimeRecord

logger = logging.getLogger(__name__)


def prepare_credit_read(session, parent, operation, prepare):
    if operation.action_kind not in {'CREDIT_VIEW', 'CREDIT_STATEMENT'}:
        raise ValueError('CREDIT_READ_REQUIRED')
    # Roll back only this READ's partially created task/evidence. Never include
    # dispatch/commit of the preceding repayment inside this savepoint.
    connection = session.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    try:
        with session.begin_nested():
            prepare(session, parent, operation)
        return
    except Exception as error:
        logger.warning('credit_read_preparation_failed action=%s error=%s',
            operation.action_kind, type(error).__name__)
        code = error.code if isinstance(error, DomainError) else 'CREDIT_QUERY_UNAVAILABLE'
    from .intelligence import ProposedAction, canonical_action_message
    from .runtime import ACTION_INTENTS
    from .permissions import actor_role
    child = Task(sandbox_id=parent.sandbox_id, user_id=parent.user_id,
        message=canonical_action_message(ProposedAction.model_validate(operation.args_json)),
        intent_type=ACTION_INTENTS[operation.action_kind], state='FAILED', model_label='credit-read-recovery',
        fic_json={}, plan_json={}, analysis_json={}, result_json={
            'action': operation.action_kind, 'error': code, 'bank_state_changed': False,
            'message': '信用卡查询暂未完成，可重新查询。'})
    session.add(child)
    session.flush()
    session.add(RuntimeRecord(task_id=child.id, root_task_id=parent.id, kind='CHILD',
        metadata_json={'operation_id': operation.id,
            'prepared_actor_role': actor_role(session, parent.sandbox_id, parent.user_id)}))
    operation.child_task_id, operation.status = child.id, 'FAILED'
    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
        event_type='CREDIT_READ_PREPARATION_FAILED', payload={
            'operation_id': operation.id, 'action': operation.action_kind, 'error': code})
