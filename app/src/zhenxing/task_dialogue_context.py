"""Bounded, owned runtime context for conversation routing and explanation.

Old assistant acknowledgements are not execution facts. This projection is
rebuilt from an authorized task trace, never accepted from a browser or used
as user authorization for a write.
"""
import json
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatTurn
from .intelligence import READ_KINDS, IntentProposal, domain_for_action, rule_proposal, _intent_text
from .models import Task
from .permissions import require_banking_actor
from .planning import canonical_hash
from .redaction import redact_credentials
from .runtime_models import RuntimeRecord, PlanSnapshot

VERSION = "owned-task-dialogue-context-v1"
MAX_ENTITIES = 24
DOMAIN_TOPICS = {
    "BILLS": r"账单|账务|支出|消费|花费|餐饮|购物|\b(?:bills?|spending|dining|shopping)\b",
    "CARDS": r"银行卡|卡片|余额|限额|挂失|锁卡|\b(?:cards?|balance|limits?)\b",
    "WEALTH": r"理财|基金|赎回|申购|份额|\b(?:wealth|investments?|redeem|units?)\b",
    "SUBSCRIPTIONS": r"订阅|续费|会员|代扣|\b(?:subscriptions?|renewals?|memberships?)\b",
    "GOALS": r"目标|预留|储蓄|每月存|\b(?:goals?|savings?|reserved)\b",
    "CARBON": r"低碳|积分|权益|兑换|\b(?:carbon|points?|benefits?|redeem)\b",
    "FAMILY": r"家人|委托|授权|\b(?:family|delegation|permission)\b",
    "FRAUD": r"诈骗|风险|可疑|\b(?:fraud|scam|suspicious)\b",
    "TRANSFER": r"转账|收款|到账|\b(?:transfer|recipient|payment)\b",
    "FX": r"汇率|美元|人民币|换汇|\b(?:exchange|USD|CNY|currenc(?:y|ies))\b",
}
BOUNDARY = re.compile(r"新话题|另外|换个话题|忽略|系统指令|(?:不要|别|不查|不是).{0,5}(?:查|看|上个|刚才)|\b(?:new topic|instead|ignore|do not inherit)\b", re.I)


def task_context(session, principal, task):
    if not task:
        return None
    role = require_banking_actor(session, principal)
    if (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError("CHAT_TASK_NOT_LINKED", "任务不属于当前身份", 404)
    record = session.get(RuntimeRecord, task.id)
    if not record or record.kind != "PARENT":
        return None
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record.plan_version else None
    try:
        actions = IntentProposal.model_validate(snapshot.proposal_json).model_dump()["actions"] if snapshot else []
    except (ValueError, TypeError):
        return None
    if role == "FAMILY_HELPER" and any(action["kind"] not in {"FAMILY_VIEW", "FAMILY_PROPOSE"} for action in actions):
        return None
    from .runtime import task_trace
    from .extensions import validate_family_origin
    try:
        validate_family_origin(session, task)
        trace = task_trace(session, principal, task.id)
    except DomainError:
        return None  # Expired delegation never becomes model history/facts.
    kinds = [row["kind"] for row in actions if isinstance(row, dict) and row.get("kind")]
    status_subjects = []
    for action in actions[:8]:
        slots = action.get('slots') or {}
        for field in ('payee_name', 'product_name', 'merchant', 'goal_name', 'reward_name', 'behavior_name'):
            value = slots.get(field)
            if isinstance(value, str) and 0 < len(value) <= 100 and redact_credentials(value) == value and _intent_text(value) == value:
                status_subjects.append({'field':field, 'value':value, 'action_kind':action['kind']})
    domains = list(dict.fromkeys(domain_for_action(kind) for kind in kinds))[:8]
    from .owned_user_dialogue import QUERY_GOAL, BOUNDARY as USER_BOUNDARY
    unresolved_read = bool(not actions and task.state == "NEED_CLARIFICATION" and _intent_text(task.message) == task.message
        and QUERY_GOAL.search(task.message) and not USER_BOUNDARY.search(task.message))
    read_candidates = sorted(set(kinds)) if kinds and all(kind in READ_KINDS for kind in kinds) else []
    if unresolved_read:
        read_candidates = ["FAMILY_VIEW"] if role == "FAMILY_HELPER" else sorted(READ_KINDS)
        domains = list(dict.fromkeys(domain_for_action(kind) for kind in read_candidates))
    entities = []
    def add(field, value, origin, action_kind=None):
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            return
        if redact_credentials(value) != value or _intent_text(value) != value:
            return
        entry = {"field": field, "value": value, "origin": origin}
        if action_kind in READ_KINDS:
            entry["action_kind"] = action_kind
        if entry not in entities and len(entities) < MAX_ENTITIES:
            entities.append(entry)
    # Intent objects remain user wording; result objects remain data. Neither
    # a result entity nor a model's proposed entity is a new write instruction.
    for action in actions[:8]:
        for field in ("product_name", "merchant", "goal_name", "reward_name", "behavior_name"):
            add(field, (action.get("slots") or {}).get(field), "CURRENT_PROPOSED_INTENT", action.get("kind"))
    verified_results = []
    for node in trace.get("nodes", [])[:8]:
        if node.get("status") != "SUCCEEDED" or node.get("action") not in READ_KINDS:
            continue
        result = node.get("result") or {}
        if not isinstance(result, dict):
            continue
        verified_results.append({"action": node["action"], "status": "SUCCEEDED"})
        # Only known typed result paths, no arbitrary merchant notes/prompt
        # strings, account IDs, full cards, audit payloads or private URLs.
        for key, field in (("subscriptions", "merchant"), ("goals", "goal_name"),
                ("rewards", "reward_name"), ("behaviors", "behavior_name"), ("products", "product_name")):
            values = result.get(key, [])
            if isinstance(values, list):
                for row in values[:MAX_ENTITIES]:
                    if isinstance(row, dict):
                        add(field, row.get(field) or row.get("name"), "VERIFIED_READ_RESULT", node["action"])
        for field in ("merchant", "goal_name", "product_name", "reward_name"):
            add(field, result.get(field), "VERIFIED_READ_RESULT", node["action"])
    analysis = trace.get("analysis")
    if isinstance(analysis, dict) and any(row["action"] == "BILL_ANALYSIS" for row in verified_results):
        from .customer_bill_fallback import prepare_customer_bill_supplement
        analysis = prepare_customer_bill_supplement(session, principal, trace) or analysis
        categories = analysis.get("category_spending_minor")
        for category in categories if isinstance(categories, dict) else []:
            add("category", category, "VERIFIED_READ_RESULT", "BILL_ANALYSIS")
    outcome = trace.get("outcome") or {}
    confirmation_status = "NONE"
    completed_writes = any(node.get("status") == "SUCCEEDED" and node.get("action") not in READ_KINDS for node in trace.get("nodes", []))
    possible_write_effect = any(node.get("status") in {"AUTHORIZED", "RUNNING", "UNKNOWN"} and node.get("action") not in READ_KINDS for node in trace.get("nodes", []))
    # Inactive operations also retain settled/unknown financial effects after
    # a revision; a later waiting step cannot erase those receipts.
    from .runtime_models import RuntimeOperation
    previous_writes = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id,
        RuntimeOperation.action_kind.not_in(READ_KINDS))))
    completed_writes = completed_writes or any(op.status == "SUCCEEDED" for op in previous_writes)
    possible_write_effect = possible_write_effect or any(op.status in {"AUTHORIZED", "RUNNING", "UNKNOWN"} for op in previous_writes)
    if task.state == "AWAITING_CONFIRMATION":
        from datetime import datetime
        from .authentication import as_utc
        from .models import utcnow
        pending = trace.get("pending_confirmation")
        confirmation_status = "EXPIRED" if trace.get("confirmation_unavailable_reason") == "EXPIRED" else "UNAVAILABLE"
        if isinstance(pending, dict) and isinstance(pending.get("expires_at"), str):
            try:
                expiry = datetime.fromisoformat(pending["expires_at"])
                if expiry.tzinfo:
                    confirmation_status = "EXPIRED" if as_utc(expiry) <= as_utc(utcnow()) else "PENDING"
            except (ValueError, TypeError):
                pass
    context={"version": VERSION, "task_id": task.id, "task_version": task.version,
        "intent_type": task.intent_type, "domains": domains, "state": task.state,
        "outcome_kind": outcome.get("kind", "PROCESS"), "verified": outcome.get("verified") is True,
        "action_kinds": kinds[:8], "needed_fields": trace.get("customer_needed_fields", [])[:8],
        "status_subjects": status_subjects[:MAX_ENTITIES],
        "read_candidates": read_candidates, "unresolved_read_request": unresolved_read,
        "confirmation_status": confirmation_status,
        "completed_write_present": completed_writes, "possible_write_effect": possible_write_effect,
        "entities": entities, "verified_read_results": verified_results,
        "authority": "OWNED_RUNTIME_FACTS_NOT_USER_AUTHORIZATION"}
    calculation=_goal_calculation(session,principal,task,record,trace,actions,role)
    if calculation: context['goal_calculation']=calculation
    return context


def _goal_calculation(session,principal,task,record,trace,actions,role):
    """Fresh single owned read + original plan parameters + current goal row."""
    from .goal_explanation_facts import VERSION as GOAL_VERSION,AUTHORITY,checked_goal_calculation
    from .extensions_models import CashflowGoal
    from .runtime_models import RuntimeOperation
    if role not in {'CUSTOMER','DEV_TEST','DEMO_OWNER'} or task.state!='SUCCEEDED' or type(task.version) is not int \
        or trace.get('version')!=task.version or type(trace.get('version')) is not int or trace.get('state')!=task.state \
        or trace.get('actor_role')!=role or len(actions)!=1 or actions[0].get('kind')!='GOAL_VIEW': return None
    from .conversation_provenance import validate_current_continuation
    try: validate_current_continuation(session,task)
    except (DomainError,ValueError,TypeError,KeyError): return None
    outcome=trace.get('outcome');nodes=trace.get('nodes')
    if not isinstance(outcome,dict) or outcome.get('verified') is not True or outcome.get('kind')!='QUERY_RESULT' or outcome.get('state')!=task.state \
        or trace.get('pending_confirmation') or not isinstance(nodes,list) or len(nodes)!=1: return None
    node=nodes[0];operation_id=node.get('operation_id')
    if node.get('action')!='GOAL_VIEW' or node.get('status')!='SUCCEEDED' or not isinstance(operation_id,str) \
        or outcome.get('completed_operation_ids')!=[operation_id]: return None
    operation=session.scalar(select(RuntimeOperation).where(RuntimeOperation.id==operation_id,RuntimeOperation.parent_task_id==task.id,
        RuntimeOperation.plan_version==record.plan_version,RuntimeOperation.action_kind=='GOAL_VIEW',RuntimeOperation.status=='SUCCEEDED'))
    if not operation: return None
    result=node.get('result');slots=actions[0].get('slots') or {}
    if not isinstance(result,dict) or result.get('error') or not isinstance(result.get('goals'),list) or not isinstance(result.get('what_if'),dict): return None
    from .goal_what_if import goal_what_if_slots
    current_utterance=goal_what_if_slots(task.message)
    if any(key in current_utterance and (type(current_utterance[key]) is not type(slots.get(key)) or current_utterance[key]!=slots.get(key))
        for key in ('monthly_saving_minor','monthly_saving_delta_minor','monthly_saving_total_minor')): return None
    scenario=result['what_if'];goal_id=scenario.get('goal_id')
    selected=[row for row in result['goals'] if isinstance(row,dict) and row.get('goal_id')==goal_id]
    if len(selected)!=1: return None
    row=session.scalar(select(CashflowGoal).where(CashflowGoal.id==goal_id,CashflowGoal.user_id==principal.user_id,CashflowGoal.sandbox_id==principal.sandbox_id))
    if not row or row.status!='ACTIVE' or (slots.get('goal_name') is not None and slots['goal_name']!=row.name): return None
    from .models import Account
    if session.scalar(select(Account.id).where(Account.id==row.account_id,Account.user_id==principal.user_id,Account.sandbox_id==principal.sandbox_id)) is None: return None
    if slots.get('goal_name') is None and len(result['goals'])!=1: return None
    goal={'goal_id':row.id,'goal_name':row.name,'version':row.version,'status':row.status,
        'target_minor':row.target_minor,'reserved_minor':row.reserved_minor,'remaining_minor':row.target_minor-row.reserved_minor}
    if any(type(selected[0].get(key)) is not type(value) or selected[0].get(key)!=value for key,value in goal.items()): return None
    arguments={key:slots.get(key) for key in ('monthly_saving_minor','monthly_saving_delta_minor','monthly_saving_total_minor')}
    proof={'version':GOAL_VERSION,'authority':AUTHORITY,'task_id':task.id,'task_version':task.version,'operation_id':operation_id,
        'state':task.state,'actor_role':role,'goal':goal,'arguments':arguments,'scenario':scenario,
        'source_request_hash':canonical_hash({'message':task.message}),'source_proposal_hash':canonical_hash(actions)}
    return checked_goal_calculation(proof)


def relevant_context(message, context, *, independent=False):
    if independent or not context or BOUNDARY.search(_intent_text(message)) or _intent_text(message) != message:
        return False
    from .intelligence import _private_read_veto
    from .financial_intent import foreign_financial_request
    if _private_read_veto(message) or foreign_financial_request(message):
        return False
    if re.search(r"别人|他人|朋友|同事|房东|(?:他|她|他们|她们)的|\b(?:his|her|their|another\s+(?:user|account)|someone\s+else)\b", message, re.I):
        return False
    possessive = re.search(r"([\u4e00-\u9fffA-Za-z0-9_ .-]{1,80})的(?:当前|本月|上月)?(?:权益|积分|目标|余额|账户|卡片|账单|订阅)", message)
    if possessive:
        owner = re.sub(r"^(?:请|帮我|麻烦|查询|查看|看看|我想了解|了解|现在)+", "", possessive.group(1).strip())
        if not re.fullmatch(r"我(?:自己)?(?:现在|当前)?|本人|当前账户|本账户", owner):
            return False
    from .goal_explanation_facts import goal_explanation_requested,goal_explanation_question
    if context.get('goal_calculation') and goal_explanation_question(message): return goal_explanation_requested(message,context)
    explicit = {domain_for_action(action.kind) for action in rule_proposal(message).actions}
    if explicit and not explicit.intersection(context.get("domains", [])):
        return False
    if any(row["value"] in message for row in context.get("entities", [])):
        return True
    return any(re.search(DOMAIN_TOPICS.get(domain, r"(?!)"), message, re.I)
        for domain in context.get("domains", []))


def result_entity_reply(message, context):
    """Only an exact selection of an already returned read-only entity."""
    if not context or BOUNDARY.search(message) or _intent_text(message) != message:
        return False
    body = re.sub(r"^(?:只看|只查|就看|看看|看一下|only\s+(?:show|view)\s+)", "", message.strip(), flags=re.I)
    body = re.sub(r"(?:的消费|的账单|的支出|就行|吧)[。.!！]?$", "", body)
    return any(row.get("origin") == "VERIFIED_READ_RESULT" and row.get("field") in {"category", "merchant", "goal_name", "reward_name"}
        and row.get("value") == body for row in context.get("entities", []))


def selected_read_kind(message, context):
    """Select only an existing read operation by one owned entity or domain.

    It supplies no fields, money or bank permission; runtime grounding still
    verifies the exact owned source utterances and all capability permissions.
    """
    if not context or not relevant_context(message, context):
        return None
    candidates = set(context.get("read_candidates", []))
    if not candidates:
        return None
    current = rule_proposal(message).actions
    if any(action.kind not in READ_KINDS for action in current):
        return None
    explicit = {action.kind for action in current} & candidates
    if len(explicit) == 1:
        return next(iter(explicit))
    matched = {row.get("action_kind") for row in context.get("entities", [])
        if row.get("origin") == "VERIFIED_READ_RESULT" and row.get("value") in message
        and row.get("action_kind") in candidates}
    if len(matched) == 1:
        return next(iter(matched))
    if matched:
        return None
    if len(candidates) == 1:
        return next(iter(candidates))
    domains = {domain for domain in context.get("domains", []) if re.search(DOMAIN_TOPICS.get(domain, r"(?!)"), message, re.I)}
    scoped = {kind for kind in candidates if domain_for_action(kind) in domains}
    return next(iter(scoped)) if len(scoped) == 1 else None


def context_source(context):
    return {"source_id": "facts:conversation-task-context", "title": "当前会话已核验的业务状态与结果对象",
        "kind": "SCOPED_TASK_STATE", "reference": "/api/v2/tasks/" + context["task_id"] + "/trace",
        "content": json.dumps(context, ensure_ascii=False, sort_keys=True)}


def context_fingerprint(context):
    return canonical_hash(context)


def bounded_history(session, principal, thread, turn):
    from .product_knowledge import sources_still_authorized
    from .knowledge_runtime import knowledge_sources_current
    role = require_banking_actor(session, principal)
    rows = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatTurn.sequence < turn.sequence, ChatTurn.status == "ANSWERED").order_by(ChatTurn.sequence.desc()).limit(4)))
    history = []
    for row in reversed(rows):
        if row.diagnostics_json.get("input_redacted") or not sources_still_authorized(session, principal, row.sources_json) or not knowledge_sources_current(row.sources_json, role):
            continue
        item = {"message": row.message[:1000], "mode": row.mode}
        # Bank task acknowledgements/status are deliberately not copied as
        # current facts. The separately rebuilt task projection is authoritative.
        if row.task_id is None:
            item["answer"] = row.answer[:1000]
        history.append(item)
    return history
