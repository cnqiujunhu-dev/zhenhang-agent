"""Finite selected GOAL READ source, not a classifier or execution path."""
from copy import deepcopy

from .contracts import DomainError
from .planning import canonical_hash


VERSION="semantic-goal-calculation-scope-v1"


def _deny():
    raise DomainError("SEMANTIC_EXPLANATION_SOURCE_CHANGED","原目标测算的来源或权限已变化，请重新查询；没有办理资金操作。",409)


def _current_source(session,principal,task,version):
    from .task_dialogue_context import task_context
    from .goal_explanation_facts import goal_calculation_source
    if task is None or (task.user_id,task.sandbox_id)!=(principal.user_id,principal.sandbox_id) \
            or task.state!="SUCCEEDED" or task.version!=version:_deny()
    context=task_context(session,principal,task)
    if not context or context.get("action_kinds")!=["GOAL_VIEW"] or not context.get("verified"):_deny()
    source=goal_calculation_source(context)
    if source is None:_deny()
    return source


def selected_goal_calculation_source(session,principal,thread,turn,decision,offered):
    """Only the model's explicit, server-verified single GOAL frame selects it."""
    if decision.mode!="CONSULT" or turn.context_policy=="INDEPENDENT" or not decision.focus_ref:return None
    frame=next((row for row in offered["model"]["frames"] if row["frame_ref"]==decision.focus_ref),None)
    if not frame or [row.get("kind") for row in frame.get("actions",[])]!=["GOAL_VIEW"]:return None
    if not any(frame["actions"][0].get("slots",{}).get(field) is not None for field in
            ("monthly_saving_minor","monthly_saving_delta_minor","monthly_saving_total_minor")):return None
    if frame.get("state")!="SUCCEEDED" or frame.get("read_only") is not True or frame.get("write_effect_possible") is not False:_deny()
    from .semantic_proposal import _frame
    task,binding,actions=_frame(session,principal,thread,offered,decision.focus_ref)
    if [row.get("kind") for row in actions]!=["GOAL_VIEW"]:_deny()
    source=_current_source(session,principal,task,binding["task_version"])
    scope={"schema_version":VERSION,"task_id":task.id,"task_version":task.version,"source_hash":canonical_hash(source)}
    return deepcopy(source),scope


def current_goal_calculation_source(session,principal,scope):
    from .models import Task
    if not isinstance(scope,dict) or set(scope)!={"schema_version","task_id","task_version","source_hash"} \
            or scope.get("schema_version")!=VERSION or type(scope.get("task_version")) is not int \
            or not isinstance(scope.get("task_id"),str) or not isinstance(scope.get("source_hash"),str):_deny()
    source=_current_source(session,principal,session.get(Task,scope["task_id"]),scope["task_version"])
    if canonical_hash(source)!=scope["source_hash"]:_deny()
    return source
