"""AA display note from its own confirmed scope or original creation message.

Legacy titles were omitted by the old compiler. A unique labelled title on
the original single-AA parent may be displayed, but is never payment authority.
"""
import re
from sqlalchemy import select
from .models import Task
from .runtime_models import RuntimeOperation


def collection_note(session, request):
    task = session.get(Task, request.task_id)
    if not task or (task.user_id, task.sandbox_id) != (request.user_id, request.sandbox_id):
        return ""
    nodes = [node for node in (task.plan_json or {}).get("nodes", []) if node.get("node_id") == "create-aa-request"]
    if len(nodes) == 1 and isinstance(nodes[0].get("aa_note"), str):
        return nodes[0]["aa_note"][:160]
    operations = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.child_task_id == task.id, RuntimeOperation.action_kind == "AA_REQUEST")))
    if len(operations) != 1:
        return ""
    op = operations[0]
    parent = session.get(Task, op.parent_task_id)
    siblings = list(session.scalars(select(RuntimeOperation).where(
        RuntimeOperation.parent_task_id == op.parent_task_id, RuntimeOperation.action_kind == "AA_REQUEST")))
    if not parent or len(siblings) != 1 or (parent.user_id, parent.sandbox_id) != (request.user_id, request.sandbox_id):
        return ""
    # Display-only compatibility for an exact, explicitly labelled old title.
    names = re.findall(r'(?:名称|备注|标题)\s*[：:]?\s*[“「"]([^”」"\r\n]{1,160})[”」"]', parent.message)
    return names[0] if len(names) == 1 else ""
