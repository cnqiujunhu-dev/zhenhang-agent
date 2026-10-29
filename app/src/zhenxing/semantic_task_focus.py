"""Bind deliberate UI selection without trapping independent new requests.

Automatic current-task navigation is advisory. An explicit selected task is a
hard target only for modes that operate on an existing task. Neither grants
permission nor replaces the independently checked plan/confirmation version.
"""
from .contracts import DomainError

BOUND_MODES = frozenset({'CONTINUE', 'REVISE', 'RESUME', 'PAUSE', 'CANCEL', 'STATUS'})


def selected_task_id(turn):
    return turn.requested_task_id if (turn.diagnostics_json or {}).get('explicit_task_selection') is True else None


def bind_selected_focus(offered, turn):
    selected = selected_task_id(turn)
    if not selected:
        return
    ref = next((ref for ref, binding in offered['frame_bindings'].items()
                if binding['task_id'] == selected), None)
    # A missing frame is not silently replaced with another current task. New
    # independent questions remain possible; bound modes are rejected below.
    offered['selected_task'] = {'task_id': selected, 'expected_version': turn.expected_task_version,
                                'frame_ref': ref}
    if ref:
        offered['model']['selected_focus_ref'] = ref


def checked_focus(session, principal, offered, decision, turn):
    from .services import owned_task
    entry = offered['frame_bindings'].get(decision.focus_ref)
    selected = selected_task_id(turn)
    if selected and decision.mode in BOUND_MODES:
        if not entry or entry['task_id'] != selected:
            raise DomainError('CHAT_SELECTED_TASK_MISMATCH',
                '这次选择的事项和理解到的对象不一致。请重新选中要处理的事项，再告诉我要怎么调整。', 409)
    if not entry:
        return None
    task = owned_task(session, principal, entry['task_id'], lock=True)
    if decision.mode in BOUND_MODES:
        # Includes changes while the model was running, not just browser age.
        expected = turn.expected_task_version if turn.requested_task_id == task.id else None
        if task.version != entry['task_version'] or expected is not None and expected != task.version:
            raise DomainError('CHAT_SELECTED_TASK_CHANGED',
                '这项办理已经更新。我没有修改它，请先核对最新内容后继续。', 409)
    return task
