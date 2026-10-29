"""Bounded, DB-owned user utterances for a single conversational continuation.

Assistant replies and banking facts are deliberately not historical authority.
The public prompt data is not itself proof: only a DB-verified instance is used
by proposal grounding, and the same receipts are checked again before compile.
"""
from dataclasses import dataclass
import re

from sqlalchemy import select

from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .intelligence import READ_KINDS, IntentProposal, _CONDITIONAL, _GLOBAL_NO_WRITE, _intent_text, ground_proposal, rule_proposal
from .language import EN_NO_WRITE, EN_UNMET_CONDITION
from .models import Task
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord

VERSION = "owned-user-dialogue-v1"
PRIVATE_KEY = "_verified_user_dialogue"
MAX_SOURCE_TURNS = 8
MAX_SOURCE_CHARS = 4000
QUERY_GOAL = re.compile(r"(?:查|看|余额|账单|订阅|自动续费|balance|statement|subscription)", re.I)
BOUNDARY = re.compile(r"(?:不要|不查|别查|不是|取消|算了|换个|换一个|新话题|另外|改成|而是|忽略|系统|指令|密码|验证码|token|password|otp|instead|never|don't|do not|new topic)", re.I)
READ_REPLY = re.compile(r"(?:(?:我的|本人|这张|那张|查)?(?:银行卡|卡片|借记卡|信用卡|钱包|人民币|美元|美元钱包|自动续费|订阅|余额)|"
    r"(?:上|本|这|下)(?:个)?(?:月|季度|年|周)|(?:最近|近|过去)[一二两三四五六七八九十\d]+(?:个)?(?:天|周|月|季度|年)|"
    r"(?:全部|所有|最近的|本人的)|\d{4}(?:[-/]\d{1,2}){1,2}|(?:bank card|debit card|credit card|wallet|last month|this month|auto renewal))", re.I)
CARD_REFERENCE = re.compile(r"(?:尾号|卡尾号)\s*([0-9a-fA-F]{4})(?:的)?(?:那张|这张|卡|银行卡)?$")
REQUERY = re.compile(r"(?:再查一次|再查一下|重新查询|再看一遍|check again)", re.I)
WRITE_RECOVERY_VERSION = "raw-single-transfer-payee-v1"
UNPLANNED_RESTATEMENT_VERSION = "server-unplanned-transfer-restatement-v1"
RESTATEMENT_STATES = {"QUEUED", "UNDERSTANDING", "PLANNING", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION"}
WRITE_RECOVERY_ROLES = {"DEMO_OWNER", "CUSTOMER", "DEV_TEST"}
READ_RESULT_SCOPE_VERSION = "owned-bill-read-result-scope-v1"
SECURITY_READ_VERSION = "reported-security-read-v1"
RESULT_REVISION_VERSION = "owned-read-result-revision-v1"


def _carbon_qualification_question(message):
    return bool(_intent_text(message) == message and re.fullmatch(
        r"(?:我(?:现在|目前|当前)?(?:能|可以)(?:兑换什么|兑哪些|兑换哪些(?:权益)?|换什么)|"
        r"(?:What|Which)\s+(?:rewards?|benefits?)\s+can\s+I\s+(?:currently\s+)?redeem)[？?]?$", message.strip(), re.I))


def _latest_result_source(session, principal, thread, current):
    """Ignore only server-certified asides, never arbitrary recent messages.

    Asides are not user slot authority. Their exact source/role/markers are
    nevertheless bound so a later mutation cannot turn a boundary into a skip.
    """
    from .permissions import actor_role
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    rows = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatTurn.sequence < current.sequence, ChatTurn.status == "ANSWERED").order_by(ChatTurn.sequence.desc()).limit(12)))
    skipped = []
    for row in rows:
        common = (row.context_policy == "AUTO" and not row.reviewed_evidence_refs_json
            and not row.diagnostics_json.get("input_redacted") and row.diagnostics_json.get("actor_role_at_submission") == role
            and _intent_text(row.message) == row.message and not BOUNDARY.search(row.message)
            and not re.search(r"不想|不用|无需|拒绝|别|\bnot\b", row.message, re.I))
        markers = None
        if common and row.mode == "STATUS" and row.task_id == thread.current_task_id:
            status = (row.response_meta_json or {}).get("status_projection")
            if isinstance(status, dict) and status.get("task_id") == thread.current_task_id and status.get("verified") is True and status.get("outcome_kind") == "QUERY_RESULT" and status.get("state") == "SUCCEEDED":
                markers = {"status_projection": status}
        elif common and row.mode == "CONSULT" and row.task_id in {None, thread.current_task_id} and row.diagnostics_json.get("preserve_read_goal_context") is True and row.diagnostics_json.get("read_goal_task_id") == thread.current_task_id:
            from .task_dialogue_context import task_context, relevant_context
            origin = session.get(Task, thread.current_task_id)
            if origin and relevant_context(row.message, task_context(session, principal, origin)):
                markers = {"preserve_read_goal_context": True, "read_goal_task_id": thread.current_task_id}
        if markers is None:
            return row, list(reversed(skipped))
        skipped.append({"turn_id": row.id, "message_hash": canonical_hash({"message": row.message}),
            "mode": row.mode, "actor_role": role, "referenced_task_id": thread.current_task_id,
            "markers_hash": canonical_hash(markers)})
    return None, list(reversed(skipped))  # Bounded: never scan past twelve turns.


def _read_source_proof(session, principal, source):
    """Recheck a source's user receipts, snapshot and immutable read result."""
    from .permissions import actor_role
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    owner = (principal.user_id, principal.sandbox_id)
    if role not in {"CUSTOMER", "DEV_TEST", "DEMO_OWNER", "OWNER"} or not source or (source.user_id, source.sandbox_id) != owner or source.status != "ANSWERED" or source.context_policy != "AUTO" or source.reviewed_evidence_refs_json or source.diagnostics_json.get("input_redacted") or source.diagnostics_json.get("actor_role_at_submission") != role:
        return None
    origin = session.get(Task, source.task_id) if source.task_id else None
    record = session.get(RuntimeRecord, source.task_id) if origin else None
    snapshot = _snapshot(session, origin) if origin else None
    if not origin or (origin.user_id, origin.sandbox_id) != owner or origin.state not in {"SUCCEEDED", "NEED_CLARIFICATION"} or not record or not snapshot:
        return None
    actions = IntentProposal.model_validate(snapshot.proposal_json).actions
    if len(actions) != 1 or actions[0].kind not in {"GOAL_VIEW", "CARBON_VIEW"}:
        return None
    previous = record.metadata_json.get("owned_user_dialogue") or {}
    if previous:
        validate_dialogue_proposal(session, origin, IntentProposal.model_validate(snapshot.proposal_json), record.metadata_json)
        refs = previous.get("source_refs") or [{"turn_id": source.id, "message_hash": canonical_hash({"message": source.message})}]
    else:
        if [a.model_dump() for a in ground_proposal(IntentProposal(actions=actions), source.message).actions] != [a.model_dump() for a in actions]:
            return None
        refs = [{"turn_id": source.id, "message_hash": canonical_hash({"message": source.message})}]
    if not isinstance(refs, list) or not 1 <= len(refs) < MAX_SOURCE_TURNS or refs[-1].get("turn_id") != source.id:
        return None
    utterances, seq = [], 0
    for ref in refs:
        row = session.get(ChatTurn, ref.get("turn_id"))
        if not row or (row.user_id, row.sandbox_id, row.thread_id) != (*owner, source.thread_id) or row.status != "ANSWERED" or not seq < row.sequence <= source.sequence or row.diagnostics_json.get("actor_role_at_submission") != role or row.diagnostics_json.get("input_redacted") or row.reviewed_evidence_refs_json or ref.get("message_hash") != canonical_hash({"message": row.message}):
            return None
        seq = row.sequence
        utterances.append(row.message)
    if sum(map(len, utterances)) > MAX_SOURCE_CHARS:
        return None
    from .runtime import task_trace
    nodes = task_trace(session, principal, origin.id).get("nodes", [])
    if len(nodes) != 1 or nodes[0].get("action") != actions[0].kind or nodes[0].get("status") != ("SUCCEEDED" if origin.state == "SUCCEEDED" else "NEED_CLARIFICATION") or origin.state != "SUCCEEDED" and actions[0].kind != "GOAL_VIEW":
        return None
    result = nodes[0].get("result")
    if not isinstance(result, dict):
        return None
    if actions[0].kind == "GOAL_VIEW" and isinstance(result.get("what_if"), dict):
        from .extensions_models import CashflowGoal
        calculation = result["what_if"]
        goal = session.get(CashflowGoal, calculation.get("goal_id"))
        if not goal or (goal.user_id, goal.sandbox_id) != owner or goal.name != calculation.get("goal_name") or goal.version != calculation.get("goal_version") or goal.status != "ACTIVE":
            return None
    projection = {"source_turn_id": source.id, "origin_task_id": origin.id, "origin_snapshot_id": snapshot.id,
        "origin_snapshot_hash": canonical_hash(snapshot.proposal_json), "read_result_hash": canonical_hash(result),
        "source_role": role, "source_refs": refs, "prior_actions": [a.model_dump() for a in actions]}
    return projection, result, tuple(utterances)


def result_followup_candidate(session, principal, thread, turn):
    """Prepare only one DB-proven own read; no LLM field chooses its target."""
    from .goal_what_if import goal_assumption_update, revise_goal_assumptions, GoalAssumptionUpdate
    from .permissions import actor_role
    update = goal_assumption_update(turn.message)
    carbon = _carbon_qualification_question(turn.message)
    bare_baseline = _current_goal_baseline(turn.message) if _plain_supplement(turn.message) else None
    if not update and not carbon and bare_baseline is None or turn.context_policy != "AUTO" or turn.reviewed_evidence_refs_json or turn.diagnostics_json.get("input_redacted"):
        return None
    source, aside_refs = _latest_result_source(session, principal, thread, turn)
    if not source:
        return {"blocked_reason": "REDEMPTION_DOMAIN_REQUIRED"} if carbon else None
    if source.task_id != thread.current_task_id or turn.requested_task_id and turn.requested_task_id != source.task_id or turn.diagnostics_json.get("actor_role_at_submission") != actor_role(session, principal.sandbox_id, principal.user_id):
        return None
    proved = _read_source_proof(session, principal, source)
    if not proved:
        source_task = session.get(Task, source.task_id)
        snapshot = _snapshot(session, source_task) if source_task else None
        actions = IntentProposal.model_validate(snapshot.proposal_json).actions if snapshot else []
        if (update or carbon) and len(actions) == 1 and actions[0].kind == ("GOAL_VIEW" if update else "CARBON_VIEW"):
            return {"blocked_reason": "SOURCE_CHANGED"}
        return {"blocked_reason": "REDEMPTION_DOMAIN_REQUIRED"} if carbon else None
    projection, result, utterances = proved
    kind = projection["prior_actions"][0]["kind"]
    prior_slots = projection["prior_actions"][0]["slots"]
    if not update and not carbon and kind == "GOAL_VIEW" and prior_slots.get("monthly_saving_minor") is None and prior_slots.get("monthly_saving_total_minor") is not None:
        update = GoalAssumptionUpdate("BASELINE", bare_baseline)
    if update and kind == "GOAL_VIEW":
        original = result.get("what_if")
        if not isinstance(original, dict):
            if prior_slots.get("monthly_saving_minor") is not None or not prior_slots.get("goal_name"):
                return {"blocked_reason": "TARGET_REQUIRED"}
            original = {key: prior_slots.get(key) for key in ("goal_name", "monthly_saving_minor", "monthly_saving_delta_minor", "monthly_saving_total_minor")}
        elif original.get("read_only") is not True or original.get("changes_applied") is not False:
            return None
        if any(prior_slots.get(field) != original.get(field) for field in ("goal_name", "monthly_saving_minor")):
            return None
        total = prior_slots.get("monthly_saving_total_minor")
        if total is not None:
            if original.get("monthly_saving_total_minor") != total or prior_slots.get("monthly_saving_minor") is not None and original.get("monthly_saving_delta_minor") != total - prior_slots["monthly_saving_minor"]:
                return None
        elif prior_slots.get("monthly_saving_delta_minor") != original.get("monthly_saving_delta_minor"):
            return None
        slots = revise_goal_assumptions(original, update)
        if slots is None:
            return {"blocked_reason": "ASSUMPTION_REQUIRED"}
        revision = update.public()
    elif carbon and kind == "CARBON_VIEW":
        slots, revision = {}, {"kind": "CARBON_QUALIFICATION"}
    else:
        return {"blocked_reason": "REDEMPTION_DOMAIN_REQUIRED"} if carbon else None
    if sum(map(len, utterances)) + len(turn.message) > MAX_SOURCE_CHARS:
        return {"blocked_reason": "DIALOGUE_BUDGET"}
    return {**projection, "version": RESULT_REVISION_VERSION, "scope": "READ_ONLY", "conversation_id": thread.id,
        "turn_id": turn.id, "message_hash": canonical_hash({"message": turn.message}),
        "source_refs": projection["source_refs"] + [{"turn_id": turn.id, "message_hash": canonical_hash({"message": turn.message})}],
        "read_revision": revision, "expected_action": kind, "expected_slots": slots,
        "semantic_read_kind": kind, "semantic_read_source": "OWNED_TYPED_QUERY_REFRESH",
        **({"aside_refs": aside_refs} if aside_refs else {})}


def _load_result_followup(session, parent, binding):
    from .conversation_models import ChatThread
    from .services import Principal
    from .permissions import actor_role
    current = session.get(ChatTurn, binding.get("turn_id"))
    thread = session.get(ChatThread, binding.get("conversation_id"))
    source = session.get(ChatTurn, binding.get("source_turn_id"))
    owner = (parent.user_id, parent.sandbox_id)
    def deny():
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "原查询来源或当前权限已变化，请重新核对本次查询；未办理银行操作。", 409)
    if binding.get("semantic_read_kind") != binding.get("expected_action") or binding.get("semantic_read_kind") not in {"GOAL_VIEW", "CARBON_VIEW"} or binding.get("semantic_read_source") != "OWNED_TYPED_QUERY_REFRESH":
        deny()  # This classification is not model guidance or write authority.
    if not current or not thread or not source or binding.get("scope") != "READ_ONLY" or (current.user_id, current.sandbox_id) != owner or (thread.user_id, thread.sandbox_id) != owner or current.thread_id != thread.id or source.thread_id != thread.id or current.task_id != parent.id or current.status != "ANSWERED" or current.message != parent.message or current.context_policy != "AUTO" or current.reviewed_evidence_refs_json or current.diagnostics_json.get("input_redacted") or current.diagnostics_json.get("actor_role_at_submission") != actor_role(session, parent.sandbox_id, parent.user_id) or binding.get("message_hash") != canonical_hash({"message": current.message}):
        deny()
    # Recompose from current DB messages/results, never expected_slots alone.
    shadow = type("ReadThread", (), {"id": thread.id, "current_task_id": source.task_id})()
    fresh = result_followup_candidate(session, Principal(*owner), shadow, current)
    if not fresh or fresh.get("blocked_reason") or fresh != binding:
        deny()
    refs = binding["source_refs"]
    utterances = tuple(session.get(ChatTurn, ref["turn_id"]).message for ref in refs)
    return VerifiedUserDialogue(utterances, tuple(binding["prior_actions"]), "READ_ONLY", read_kind=binding["expected_action"], read_revision={"action": binding["expected_action"], "slots": binding["expected_slots"], "assumption": binding["read_revision"]})


def _bill_result_scope(session, principal, origin):
    if not origin or (origin.user_id, origin.sandbox_id) != (principal.user_id, principal.sandbox_id):
        return None
    from .permissions import require_banking_actor
    if require_banking_actor(session, principal) == "FAMILY_HELPER":
        return None
    from .bill_query_context import completed_multi_bill_scope
    shared = completed_multi_bill_scope(session, principal, origin)
    if shared is not None:
        return shared
    from .runtime import task_trace
    trace = task_trace(session, principal, origin.id)
    nodes = trace.get("nodes", [])
    if not nodes or any(row.get("action") not in READ_KINDS for row in nodes) or not any(
            row.get("action") == "BILL_ANALYSIS" and row.get("status") == "SUCCEEDED" for row in nodes):
        return None
    saved_analysis = trace.get("analysis")
    if not isinstance(saved_analysis, dict):
        return None
    from .customer_read_history import retained_bill_read_scope
    retained=retained_bill_read_scope(session,principal,origin)
    if retained:
        # The visible past consumption is an immutable read-time source, not
        # a fresh latest-month fallback. New imports cannot change what this
        # already completed query means in subsequent conversation.
        if canonical_hash(saved_analysis)!=canonical_hash(retained['original_analysis']):
            raise DomainError('CUSTOMER_READ_SNAPSHOT_SOURCE_CHANGED','原查询来源暂时无法核对，请明确要查的统计期间。',409)
        analysis=retained['displayed_analysis']
        source_kind=retained['source_kind']
    else:
        from .customer_bill_fallback import prepare_customer_bill_supplement
        analysis = prepare_customer_bill_supplement(session, principal, trace) or saved_analysis
        source_kind='ORIGINAL_QUERY_RESULT' if analysis is saved_analysis else 'OWNED_SUPPLEMENTARY_READ'
    start, end = analysis.get("period_start"), analysis.get("period_end")
    if (start is None) != (end is None):
        return None
    if start is not None:
        from datetime import datetime
        try:
            left, right = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if not left.tzinfo or not right.tzinfo or left >= right:
                return None
        except (ValueError, TypeError):
            return None
    from .product_knowledge import source_fingerprint
    import json
    return {"version": READ_RESULT_SCOPE_VERSION, "action": "BILL_ANALYSIS", "origin_task_id": origin.id,
        "period_start": start, "period_end": end, "origin_analysis_hash": canonical_hash(saved_analysis),
        "displayed_analysis_hash": source_fingerprint([{"source_id": "facts:bill-report", "content": json.dumps(analysis, ensure_ascii=False)}]),
        "source_kind": source_kind,
        **({"source_origin":retained['source_origin'],"source_turn_id":retained['source_turn_id'],
            "read_snapshot_hash":retained['snapshot_hash'],"read_snapshot_task_version":retained['task_version']} if retained else {})}


def _recover_raw_transfer(session, origin, snapshot, source):
    """Derive intent from an owned original utterance, never model question text.

    An empty model plan does not erase a literal user request, but is not itself
    authority to invent a write. Recovery is deliberately limited to an exact
    CNY transfer amount with only the payee missing and no existing write op.
    The original empty snapshot remains immutable and separately bound.
    """
    from .permissions import actor_role
    if (not origin or not snapshot or not source or snapshot.task_id != origin.id
            or source.task_id != origin.id or source.mode != "NEW_TASK" or source.status != "ANSWERED"
            or source.context_policy != "AUTO" or source.reviewed_evidence_refs_json
            or source.diagnostics_json.get("input_redacted")
            or (source.user_id, source.sandbox_id) != (origin.user_id, origin.sandbox_id)
            or IntentProposal.model_validate(snapshot.proposal_json).actions):
        return None
    record = session.get(RuntimeRecord, origin.id)
    if not record or record.kind != "PARENT" or record.metadata_json.get("family_origin") or record.metadata_json.get("trusted_compensation"):
        return None
    try:
        role = actor_role(session, origin.sandbox_id, origin.user_id)
    except DomainError:
        return None
    if role not in WRITE_RECOVERY_ROLES:
        return None
    if session.scalar(select(RuntimeOperation.id).where(RuntimeOperation.parent_task_id == origin.id,
            RuntimeOperation.action_kind.not_in(READ_KINDS)).limit(1)) is not None:
        return None  # Stronger than only checking active/succeeded operations.
    text = source.message
    if (_intent_text(text) != text or BOUNDARY.search(text) or _CONDITIONAL.search(text)
            or _GLOBAL_NO_WRITE.search(text) or EN_UNMET_CONDITION.search(text) or EN_NO_WRITE.search(text)):
        return None
    proposal = rule_proposal(text)
    if len(proposal.actions) != 1:
        return None
    action = proposal.actions[0]
    values = action.slots.model_dump(exclude_none=True)
    if (action.kind != "TRANSFER" or action.depends_on or not values.get("amount_minor")
            or set(values) - {"amount_minor", "currency"} or values.get("currency") not in {None, "CNY"}):
        return None
    return action, role


def _plain_supplement(text):
    return bool(text and len(text) <= 80 and _intent_text(text) == text
        and not BOUNDARY.search(text) and not re.search(r"[；;\n\r“”\"'「」]", text))


def _read_reply(text):
    # Only classify the reply boundary here; exact raw text goes to the model.
    body = re.sub(r"^(?:我说的(?:是)?|我指的(?:是)?|是|只看|只查|就(?:看|查)?|查的是|看的是)", "", text.strip())
    body = re.sub(r"(?:的消费|的账单|的余额|余额|的|吧|就行)$", "", body)
    return bool(READ_REPLY.fullmatch(body) or CARD_REFERENCE.fullmatch(body) or REQUERY.fullmatch(body)
        or re.fullmatch(r"(?:我的)?(?:储蓄卡|工资卡|[一二两三\d]+张卡)", body))


def _snapshot(session, task):
    record = session.get(RuntimeRecord, task.id)
    if not record or not record.plan_version:
        return None
    return session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version))


def _same_pending_restatement(messages, prior_actions, current_message):
    """Exact same transfer subject, not recency or model-invented identity."""
    if not _plain_supplement(current_message) or any(BOUNDARY.search(message) for message in messages):
        return False
    current = rule_proposal(current_message).actions
    if len(current) != 1 or current[0].kind not in {"TRANSFER", "FX_TRANSFER"} or current[0].depends_on:
        return False
    prior = list(prior_actions)
    if len(prior) != 1 or prior[0].kind != current[0].kind or prior[0].depends_on:
        return False
    known = prior[0].slots.model_dump(exclude_none=True)
    # Re-prove fields stripped by an older deployment only from user wording.
    for message in messages:
        actions = rule_proposal(message).actions
        if len(actions) == 1 and actions[0].kind == current[0].kind and not actions[0].depends_on:
            for field, value in actions[0].slots.model_dump(exclude_none=True).items():
                if field in known and known[field] != value:
                    return False
                known[field] = value
    values = current[0].slots.model_dump(exclude_none=True)
    return bool(known.get("payee_name") and values.get("payee_name") == known["payee_name"]
        and all(values.get(field) == value for field, value in known.items()))


def continuation_candidate(session, principal, thread, turn, task, ambiguous, *, semantic_read_kind=None, restatement=False):
    """Only the latest owned goal, never an arbitrary old unfinished write."""
    plain = _plain_supplement(turn.message.strip())
    baseline_phrase = bool(_intent_text(turn.message) == turn.message and len(turn.message) <= 80 and _current_goal_baseline(turn.message) is not None
        and re.search(r"(?:每月|每个月).{0,8}(?:存|攒|储蓄|储存)", turn.message)
        and not re.search(r"[；;\n\r“”\"'「」]|不要|不想|拒绝|密码|验证码", turn.message))
    if turn.context_policy != "AUTO" or turn.reviewed_evidence_refs_json or not (plain or baseline_phrase):
        return None
    recent = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatTurn.sequence < turn.sequence).order_by(ChatTurn.sequence.desc()).limit(12)))
    prior = None
    for candidate in recent:
        if candidate.status == "ANSWERED" and candidate.context_policy == "AUTO" and candidate.mode in {"DISCOVERY", "GREETING", "FEEDBACK"} and candidate.diagnostics_json.get("preserve_goal_context") is True:
            continue  # Non-operational asides never become slot or bank authority.
        if candidate.status == "ANSWERED" and candidate.context_policy == "AUTO" and candidate.mode == "STATUS" and candidate.task_id and candidate.task_id == thread.current_task_id:
            continue  # A status question does not replace the underlying goal.
        if candidate.status == "ANSWERED" and candidate.context_policy == "AUTO" and candidate.mode == "CONSULT" and candidate.diagnostics_json.get("preserve_read_goal_context") is True and candidate.diagnostics_json.get("read_goal_task_id") == thread.current_task_id:
            continue  # A related explanation is never the original read authority.
        prior = candidate
        break
    if not prior or prior.status != "ANSWERED" or prior.mode not in {"NEW_TASK", "REVISE", "CLARIFY", "CONSULT", "NEED_CONTEXT"} or (
            prior.diagnostics_json.get("input_redacted") or BOUNDARY.search(prior.message)):
        return None
    if prior.mode == "CONSULT" and (re.search(r"怎么|如何|为什么|解释|流程|示例|(?i:how|why|explain)", prior.message)
            or not re.search(r"查|看|(?i:check|show|read)", prior.message)):
        return None  # A teaching question is not permission to read private data.
    origin = session.get(Task, prior.task_id) if prior.task_id else None
    if turn.requested_task_id and (not origin or origin.id != turn.requested_task_id):
        return None  # An explicit old selection never changes the latest goal.
    if origin and (origin.user_id, origin.sandbox_id) != (principal.user_id, principal.sandbox_id):
        return None
    origin_record = session.get(RuntimeRecord, origin.id) if origin else None
    if origin and not origin_record:
        return None
    previous = (origin_record.metadata_json.get("owned_user_dialogue") or {}) if origin_record else {}
    references = previous.get("source_refs", []) if previous.get("version") == VERSION else []
    if previous.get("restated_current_request") is True:
        # The full rephrasing is independently grounded. Subsequent fragments
        # continue it, rather than concatenating two old transfer commands.
        references = []
    if not references:
        references = [{"turn_id": prior.id, "message_hash": canonical_hash({"message": prior.message})}]
    # A repeated clarification retains the original request, not just its last
    # fragment. Reject overflow rather than silently truncate user authority.
    source_rows = [session.get(ChatTurn, ref.get("turn_id")) for ref in references]
    if any(not row for row in source_rows):
        return None
    from .permissions import actor_role
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    if any((row.user_id, row.sandbox_id, row.thread_id) != (principal.user_id, principal.sandbox_id, thread.id)
            or row.status != "ANSWERED" or row.context_policy != "AUTO" or row.reviewed_evidence_refs_json
            or row.diagnostics_json.get("input_redacted") or row.diagnostics_json.get("actor_role_at_submission") != role
            or ref.get("message_hash") != canonical_hash({"message": row.message})
            for ref, row in zip(references, source_rows)):
        return None
    snapshot = _snapshot(session, origin) if origin else None
    actions = IntentProposal.model_validate(snapshot.proposal_json).actions if snapshot else rule_proposal(prior.message).actions
    semantic_read = bool(semantic_read_kind in READ_KINDS and not any(action.kind not in READ_KINDS for action in actions)
        and (any(action.kind == semantic_read_kind for action in actions) or not actions and QUERY_GOAL.search(source_rows[0].message))
        and origin and origin.id == thread.current_task_id
        and not any(action.kind != semantic_read_kind for action in rule_proposal(turn.message).actions))
    if semantic_read:
        from .task_dialogue_context import task_context, selected_read_kind
        semantic_read = selected_read_kind(turn.message, task_context(session, principal, origin)) == semantic_read_kind
    recovery = None
    if (not actions and not previous and origin and origin.state == "NEED_CLARIFICATION"
            and task is not None and task.id == origin.id and not ambiguous
            and len(source_rows) == 1 and prior.id == source_rows[0].id
            and origin.message == prior.message and principal.role in WRITE_RECOVERY_ROLES):
        recovered = _recover_raw_transfer(session, origin, snapshot, source_rows[0])
        if recovered:
            action, role = recovered
            actions = [action]
            recovery = {"version": WRITE_RECOVERY_VERSION, "source_turn_id": source_rows[0].id,
                "message_hash": canonical_hash({"message": source_rows[0].message}),
                "actions_hash": canonical_hash([action.model_dump()]), "actor_role": role}
    writes = [a for a in actions if a.kind not in READ_KINDS]
    if restatement and (ambiguous or not origin or not task or origin.id != task.id
            or origin.id != thread.current_task_id or origin.state not in RESTATEMENT_STATES
            or not _same_pending_restatement([row.message for row in source_rows], actions, turn.message)):
        return None
    unplanned = None
    if restatement and snapshot is None:
        from .models import Confirmation
        # With no compiled plan, there must be no unexplained operation or
        # confirmation data. The original authority is one real user receipt,
        # not the old in-flight model's result or an invented plan snapshot.
        if (origin_record.plan_version != 0 or len(source_rows) != 1
                or prior.task_id != origin.id or prior.message != origin.message
                or session.scalar(select(RuntimeOperation.id).where(RuntimeOperation.parent_task_id == origin.id).limit(1))
                or session.scalar(select(Confirmation.id).where(Confirmation.task_id == origin.id).limit(1))):
            return {"blocked_reason": "UNPLANNED_SOURCE_CHANGED"}
        unplanned = {"version": UNPLANNED_RESTATEMENT_VERSION, "source_turn_id": prior.id,
            "source_message_hash": canonical_hash({"message": prior.message}),
            "source_actions_hash": canonical_hash([action.model_dump() for action in actions]),
            "source_actor_role": role, "origin_task_version": origin.version,
            "origin_input_revision": origin_record.metadata_json.get("input_revision", 0)}
    readonly = not writes and (bool(actions) or bool(QUERY_GOAL.search(_intent_text(prior.message))) or previous.get("scope") == "READ_ONLY")
    if not plain and not (readonly and len(actions) == 1 and actions[0].kind == "GOAL_VIEW" and actions[0].slots.monthly_saving_delta_minor and baseline_phrase):
        return None
    requery = bool(REQUERY.fullmatch(turn.message.strip()))
    if requery and (not readonly or not actions):
        return None  # Retry language never repeats a completed/inflight write.
    if readonly and not _read_reply(turn.message):
        from .task_dialogue_context import task_context, result_entity_reply
        from .bill_category_filter import category_filter_in_text
        category_reply = len(actions) == 1 and actions[0].kind == "BILL_ANALYSIS" and category_filter_in_text(turn.message) is not None
        goal_baseline = False
        if origin and len(actions) == 1 and actions[0].kind == "GOAL_VIEW":
            slots = actions[0].slots.model_dump(exclude_none=True)
            goal_baseline = bool(slots.get("monthly_saving_delta_minor") and _current_goal_baseline(turn.message) is not None
                and (slots.get("monthly_saving_minor") is None and origin.state == "NEED_CLARIFICATION" or baseline_phrase))
        if not goal_baseline and not category_reply and not semantic_read and (not origin or not result_entity_reply(turn.message, task_context(session, principal, origin))):
            return None  # An unrelated noun never revives a prior bank query.
    if not readonly and writes and ambiguous:
        return {"blocked_reason": "AMBIGUOUS"}
    if not readonly and (ambiguous or not origin or task is None or task.id != origin.id
            or origin.state not in (RESTATEMENT_STATES if restatement else {"NEED_CLARIFICATION"}) or len(actions) != 1):
        return None
    if writes and any(op.status in {"SUCCEEDED", "AUTHORIZED", "RUNNING", "UNKNOWN"} or (op.child_task_id and
            (child := session.get(Task, op.child_task_id)) and child.state in {"SUCCEEDED", "EXECUTING", "AUTHORIZED", "UNKNOWN"})
            for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == origin.id,
                RuntimeOperation.action_kind.not_in(READ_KINDS)))):
        return {"blocked_reason": "EXISTING_WRITE_EFFECT"} if restatement else None
    if restatement:
        from .models import Confirmation
        children = list(session.scalars(select(RuntimeOperation.child_task_id).where(
            RuntimeOperation.parent_task_id == origin.id, RuntimeOperation.child_task_id.is_not(None))))
        if session.scalar(select(Confirmation.id).where(Confirmation.task_id.in_([origin.id, *children]),
                (Confirmation.consumed_at.is_not(None)) | (Confirmation.status == "CONSUMED")).limit(1)):
            return {"blocked_reason": "CONSUMED_CONFIRMATION"}
    if origin and origin.state in {"EXECUTING", "UNKNOWN", "AUTHORIZED", "FAILED", "CLOSED"}:
        return None
    if len(references) >= MAX_SOURCE_TURNS or sum(len(row.message) for row in source_rows) + len(turn.message) > MAX_SOURCE_CHARS:
        return {"blocked_reason": "DIALOGUE_BUDGET"}
    binding = {"version": VERSION, "conversation_id": thread.id, "turn_id": turn.id,
        "source_refs": references + [{"turn_id": turn.id, "message_hash": canonical_hash({"message": turn.message})}],
        "scope": "READ_ONLY" if readonly else "PENDING_SINGLE_ACTION", "origin_task_id": origin.id if origin else None,
        "origin_snapshot_id": snapshot.id if snapshot else None,
        "origin_snapshot_hash": canonical_hash(snapshot.proposal_json) if snapshot else None,
        "prior_actions": [a.model_dump() for a in actions],
        "reuse_task_id": origin.id if not requery and origin and origin.state in ({"NEED_CLARIFICATION", "QUEUED", "UNDERSTANDING", "PLANNING", "AWAITING_CONFIRMATION"} if restatement else {"NEED_CLARIFICATION", "QUEUED", "UNDERSTANDING", "PLANNING"}) else None}
    if recovery:
        binding["write_intent_recovery"] = recovery
    if restatement:
        binding["restated_current_request"] = True
    if unplanned:
        binding["unplanned_restatement_origin"] = unplanned
    if semantic_read:
        binding["semantic_read_kind"] = semantic_read_kind
    if readonly and origin and any(action.kind == "BILL_ANALYSIS" for action in actions):
        from .bill_category_filter import category_filter_in_text
        if category_filter_in_text(turn.message):
            result_scope = _bill_result_scope(session, principal, origin)
            if result_scope:
                binding["read_result_scope"] = result_scope
    return binding


@dataclass(frozen=True)
class VerifiedUserDialogue:
    utterances: tuple[str, ...]
    prior_actions: tuple[dict, ...]
    scope: str
    result_scope: dict | None = None
    read_kind: str | None = None
    card_reference: dict | None = None
    read_revision: dict | None = None
    restated_current_request: bool = False

    @property
    def source_text(self):
        # A neutral separator, not an invented command or assistant paraphrase.
        return self.utterances[-1] if self.restated_current_request else " ".join(self.utterances)

    def public(self):
        value = {"version": VERSION, "source": "OWNED_USER_UTTERANCES_NOT_BANK_FACTS",
            "original_user_messages": list(self.utterances[:-1]), "latest_user_message": self.utterances[-1],
            "prior_intent": [{"kind": action["kind"], "slots": {
                field: value for field, value in action["slots"].items() if value is not None}}
                for action in self.prior_actions], "scope": self.scope,
            "instruction": "理解原请求与本条补充；新话题和否定不继承旧意图。不得重发已完成写操作。"}
        baseline = _proved_goal_baseline(self, self.utterances[-1]) if self.read_revision is None else None
        if baseline is not None:
            value["pending_field_reply"] = {"action": "GOAL_VIEW", "field": "monthly_saving_minor", "value": baseline,
                "source": "EXACT_CURRENT_USER_AMOUNT_FOR_MISSING_READ_ONLY_FIELD"}
        if self.result_scope:
            value["read_result_scope"] = {key: self.result_scope[key] for key in ("version", "action", "period_start", "period_end")}
        if self.read_kind:
            value["selected_read_kind"] = self.read_kind
        if self.card_reference:
            value['resolved_card_reference']={'card_last4':self.card_reference['card_last4'],
                'instruction':'动作只由本条明确临时锁卡指令授权准备；此引用仅提供先前明确查询的同一本人卡片，仍需确认。'}
        if self.read_revision:
            value["read_revision"] = {**self.read_revision,
                "authority": "VERIFIED_USER_ASSUMPTION_NOT_FUNDS_ACTION",
                "instruction": "只重新读取本人当前资料。NEW_TOTAL替换每月总方案，保留原比较基准，不再叠加旧增额；不得生成写操作。"}
        if self.restated_current_request:
            value["restated_current_request"] = True
            value["instruction"] = "这是同一笔未完成需求的完整重述，只按最新原句准备一个操作；历史仅供理解，不新增操作，不复用确认。"
        return value


def load_verified_dialogue(session, parent, metadata, *, execution_context=None):
    from .conversation_service import validate_pending_subject_selection
    validate_pending_subject_selection(session, parent, metadata)
    binding = metadata.get("owned_user_dialogue")
    if not binding:
        return None
    if binding.get("version") == RESULT_REVISION_VERSION:
        return _load_result_followup(session, parent, binding)
    def deny():
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "会话补充的原请求来源已变化，请重新明确需求；未执行业务。", 409)
    from .card_lock_reference import VERSION as CARD_REFERENCE_VERSION, SCOPE as CARD_REFERENCE_SCOPE
    if binding.get('version')==CARD_REFERENCE_VERSION:
        from .card_lock_reference import load_reference
        source,current,tail,number=load_reference(session,parent,binding)
        from .intelligence import ProposedAction,ActionSlots
        return VerifiedUserDialogue((source,current),(ProposedAction(kind='CARD_LOCK',slots=ActionSlots(card_last4=tail)).model_dump(),),
            CARD_REFERENCE_SCOPE,card_reference={'card_last4':tail,'card_number':number})
    if binding.get("version") == SECURITY_READ_VERSION:
        from .fraud_security_dialogue import reported_security_read_request
        from .permissions import actor_role
        thread = session.get(ChatThread,binding.get('conversation_id'))
        current = session.get(ChatTurn,binding.get('turn_id'))
        owner=(parent.user_id,parent.sandbox_id)
        if binding.get('scope')!='READ_ONLY' or not thread or not current or (thread.user_id,thread.sandbox_id)!=owner or (current.user_id,current.sandbox_id)!=owner or current.thread_id!=thread.id or current.task_id!=parent.id or current.status!='ANSWERED' or current.message!=parent.message or binding.get('message_hash')!=canonical_hash({'message':current.message}) or binding.get('source_role')!=actor_role(session,parent.sandbox_id,parent.user_id) or not reported_security_read_request(current.message,credential_only=bool(current.diagnostics_json.get('credential_only'))):
            deny()
        from .intelligence import ProposedAction
        return VerifiedUserDialogue((current.message,),(ProposedAction(kind='FRAUD_ANALYZE').model_dump(),),'READ_ONLY',read_kind='FRAUD_ANALYZE')
    if binding.get("version") != VERSION or binding.get("scope") not in {"READ_ONLY", "PENDING_SINGLE_ACTION"}:
        deny()
    thread = session.get(ChatThread, binding.get("conversation_id"))
    current = session.get(ChatTurn, binding.get("turn_id"))
    owner = (parent.user_id, parent.sandbox_id)
    if not thread or not current or (thread.user_id, thread.sandbox_id) != owner or (
            current.user_id, current.sandbox_id) != owner or current.thread_id != thread.id or (
            current.task_id != parent.id or current.status != "ANSWERED" or current.message != parent.message):
        deny()
    refs = binding.get("source_refs")
    if not isinstance(refs, list) or not 2 <= len(refs) <= MAX_SOURCE_TURNS or refs[-1].get("turn_id") != current.id:
        deny()
    from .permissions import actor_role
    role = actor_role(session, parent.sandbox_id, parent.user_id)
    utterances, source_rows, sequence = [], [], 0
    for ref in refs:
        row = session.get(ChatTurn, ref.get("turn_id"))
        if not row or row.thread_id != thread.id or (row.user_id, row.sandbox_id) != owner or (
                row.status != "ANSWERED" or not sequence < row.sequence <= current.sequence or
                row.diagnostics_json.get("input_redacted") or row.context_policy != "AUTO"
                or row.reviewed_evidence_refs_json or row.diagnostics_json.get("actor_role_at_submission") != role
                or ref.get("message_hash") != canonical_hash({"message": row.message})):
            deny()
        sequence = row.sequence
        utterances.append(row.message)
        source_rows.append(row)
    if sum(map(len, utterances)) > MAX_SOURCE_CHARS:
        deny()
    raw_actions = binding.get("prior_actions", [])
    try:
        # Schema additions with absent/default-null slots do not invalidate an
        # old proof. Signed snapshot/recovery hashes still use raw stored JSON.
        actions = IntentProposal.model_validate({"actions": raw_actions}).model_dump()["actions"]
    except (ValueError, TypeError, KeyError):
        deny()
    baseline_update = bool(binding["scope"] == "READ_ONLY" and len(actions) == 1 and actions[0]["kind"] == "GOAL_VIEW"
        and actions[0]["slots"].get("monthly_saving_delta_minor") and _current_goal_baseline(utterances[-1]) is not None
        and re.search(r"(?:每月|每个月).{0,8}(?:存|攒|储蓄|储存)", utterances[-1])
        and _intent_text(utterances[-1]) == utterances[-1] and len(utterances[-1]) <= 80
        and not re.search(r"[；;\n\r“”\"'「」]|不要|不想|拒绝|密码|验证码", utterances[-1]))
    if not _plain_supplement(utterances[-1]) and not baseline_update:
        deny()
    origin = session.get(Task, binding.get("origin_task_id")) if binding.get("origin_task_id") else None
    if origin and (origin.user_id, origin.sandbox_id) != owner:
        deny()
    if binding.get("origin_task_id") and not origin:
        deny()
    if origin and origin.state in {"EXECUTING", "UNKNOWN", "AUTHORIZED", "FAILED", "CLOSED"}:
        from .conversation_provenance import _DialogueExecutionContext
        if origin.id != parent.id or not isinstance(execution_context, _DialogueExecutionContext) or not execution_context.permits(parent):
            deny()
    snapshot = session.get(PlanSnapshot, binding.get("origin_snapshot_id")) if binding.get("origin_snapshot_id") else None
    recovery = binding.get("write_intent_recovery")
    unplanned = binding.get("unplanned_restatement_origin")
    if snapshot and (not origin or snapshot.task_id != origin.id or
            canonical_hash(snapshot.proposal_json) != binding.get("origin_snapshot_hash")):
        deny()
    if binding.get("origin_snapshot_id") and not snapshot:
        deny()
    if unplanned is not None:
        from .models import Confirmation
        record = session.get(RuntimeRecord, parent.id)
        source = source_rows[0]
        if (not isinstance(unplanned, dict) or unplanned.get("version") != UNPLANNED_RESTATEMENT_VERSION
                or snapshot is not None or binding.get("restated_current_request") is not True
                or binding["scope"] != "PENDING_SINGLE_ACTION" or len(source_rows) != 2
                or not origin or origin.id != parent.id or thread.current_task_id != parent.id
                or source.task_id != parent.id or source.id != unplanned.get("source_turn_id")
                or canonical_hash({"message": source.message}) != unplanned.get("source_message_hash")
                or unplanned.get("source_actor_role") != role
                or type(unplanned.get("origin_task_version")) is not int
                or type(unplanned.get("revised_task_version")) is not int
                or not 1 <= unplanned["origin_task_version"] < unplanned["revised_task_version"] <= parent.version
                or type(unplanned.get("origin_input_revision")) is not int
                or not record or record.metadata_json.get("input_revision", 0) != unplanned["origin_input_revision"] + 1
                or metadata.get("input_revision", 0) != unplanned["origin_input_revision"] + 1
                or current.diagnostics_json.get("unplanned_restatement_origin") != unplanned):
            deny()
        original = [action.model_dump() for action in rule_proposal(source.message).actions]
        if (original != actions or canonical_hash(original) != unplanned.get("source_actions_hash")
                or session.scalar(select(Confirmation.id).where(Confirmation.task_id == parent.id).limit(1))
                or any(operation.plan_version <= 0 for operation in session.scalars(select(RuntimeOperation).where(
                    RuntimeOperation.parent_task_id == parent.id)))):
            deny()
    elif snapshot is None and binding.get("restated_current_request") is True:
        deny()  # No implicit upgrade of a snapshot-less model-shaped context.
    if recovery is not None:
        if (not isinstance(recovery, dict) or recovery.get("version") != WRITE_RECOVERY_VERSION
                or binding["scope"] != "PENDING_SINGLE_ACTION" or len(source_rows) != 2
                or not origin or origin.id != parent.id
                or origin.state not in {"QUEUED", "UNDERSTANDING", "PLANNING", "NEED_CLARIFICATION"}
                or recovery.get("source_turn_id") != source_rows[0].id
                or recovery.get("message_hash") != refs[0].get("message_hash")):
            deny()
        recovered = _recover_raw_transfer(session, origin, snapshot, source_rows[0])
        if not recovered:
            deny()
        action, role = recovered
        if (role != recovery.get("actor_role") or [action.model_dump()] != actions
                or canonical_hash(raw_actions) != recovery.get("actions_hash")):
            deny()
    elif snapshot:
        try:
            snapshot_actions = IntentProposal.model_validate(snapshot.proposal_json).model_dump()["actions"]
        except (ValueError, TypeError, KeyError):
            deny()
        if snapshot_actions != actions:
            deny()
    if binding["scope"] == "PENDING_SINGLE_ACTION":
        if not origin or origin.id != parent.id or len(actions) != 1 or actions[0]["kind"] in READ_KINDS:
            deny()
        if any(op.status == "SUCCEEDED" for op in session.scalars(select(RuntimeOperation).where(
                RuntimeOperation.parent_task_id == parent.id, RuntimeOperation.action_kind.not_in(READ_KINDS)))):
            deny()
    elif any(a["kind"] not in READ_KINDS for a in actions):
        deny()
    restated = binding.get("restated_current_request")
    if restated is not None:
        if restated is not True or binding["scope"] != "PENDING_SINGLE_ACTION" or not _same_pending_restatement(
                utterances[:-1], IntentProposal.model_validate({"actions": actions}).actions, utterances[-1]):
            deny()
    semantic_kind = binding.get("semantic_read_kind")
    if semantic_kind is not None:
        if binding["scope"] != "READ_ONLY" or semantic_kind not in READ_KINDS or any(action["kind"] not in READ_KINDS for action in actions):
            deny()
        if actions:
            from .task_dialogue_context import task_context, selected_read_kind
            from .services import Principal
            if not origin or selected_read_kind(current.message, task_context(session, Principal(*owner), origin)) != semantic_kind:
                deny()
            actions = [action for action in actions if action["kind"] == semantic_kind]
            if not actions:
                deny()
        else:
            from .intelligence import _private_read_veto, domain_for_action
            from .financial_intent import foreign_financial_request
            from .task_dialogue_context import selected_read_kind
            original = source_rows[0].message
            candidates = sorted(READ_KINDS)
            if not QUERY_GOAL.search(original) or _intent_text(original) != original or BOUNDARY.search(original) or _private_read_veto(original) or foreign_financial_request(original):
                deny()
            read_context = {"read_candidates": candidates, "domains": list({domain_for_action(kind) for kind in candidates}), "entities": []}
            if selected_read_kind(current.message, read_context) != semantic_kind:
                deny()
    result_scope = binding.get("read_result_scope")
    if result_scope is not None:
        from .services import Principal
        if binding["scope"] != "READ_ONLY" or not origin or result_scope != _bill_result_scope(session, Principal(*owner), origin):
            deny()
    return VerifiedUserDialogue(tuple(utterances), tuple(actions), binding["scope"], result_scope, semantic_kind,
        restated_current_request=restated is True)


def verified(context):
    value = (context or {}).get(PRIVATE_KEY)
    return value if isinstance(value, VerifiedUserDialogue) else None


def grounding_text(message, context):
    proof = verified(context)
    if proof and proof.card_reference:
        return message  # The old READ sentence is never write authorization.
    return proof.source_text if proof and proof.utterances[-1] == message else message


def _current_goal_baseline(message):
    from .conversation_service import _continuation_money
    money = _continuation_money(message, "CNY")
    if money and money[1] == "CNY":
        return money[0]
    zero = r"(?:人民币|CNY|RMB)?\s*(?:0(?:\.0{1,2})?|零|〇)\s*(?:元|块钱|块|人民币|CNY|RMB)?"
    if re.fullmatch(zero, message.strip(), re.I):
        return 0  # Read-only baseline, never a positive money-write amount.
    match = re.fullmatch(r"(?:如果\s*)?(?:(?:改为|改成)\s*)?(?:原本|原来|目前|现在|本来)?\s*(?:每月|每个月)\s*(?:原本|原来|目前|现在|本来)?\s*(?:能|可以|可)?\s*(?:存|攒|储蓄|储存)\s*(.+?)(?:呢|的话|吧)?[？?]?$", message.strip())
    body = match.group(1) if match else ""
    money = _continuation_money(body, "CNY") if match else None
    return money[0] if money and money[1] == "CNY" else (0 if match and re.fullmatch(zero, body, re.I) else None)


def _proved_goal_baseline(proof, message):
    """An exact field reply to one existing read-only what-if, not a deposit."""
    if not proof or proof.scope != "READ_ONLY" or len(proof.prior_actions) != 1 or proof.utterances[-1] != message:
        return None
    prior = proof.prior_actions[0]
    slots = prior.get("slots", {})
    if prior.get("kind") != "GOAL_VIEW" or not slots.get("monthly_saving_delta_minor"):
        return None
    if slots.get("monthly_saving_minor") is not None and not re.search(r"(?:每月|每个月).{0,8}(?:存|攒|储蓄|储存)", message):
        return None  # A bare amount is a supplement only while this field is missing.
    return _current_goal_baseline(message)


def dialogue_rule_proposal(message, context, **kwargs):
    result = rule_proposal(grounding_text(message, context), **kwargs)
    proof = verified(context)
    if proof and proof.read_revision:
        from .intelligence import ProposedAction, ActionSlots
        return result.model_copy(update={"summary": message, "actions": [ProposedAction(
            kind=proof.read_revision["action"], slots=ActionSlots.model_validate(proof.read_revision["slots"]))]})
    if proof and proof.card_reference:
        if len(result.actions)==1 and result.actions[0].kind=='CARD_LOCK':
            values=result.actions[0].slots.model_dump()
            values['card_last4']=proof.card_reference['card_last4']
            return result.model_copy(update={'actions':[result.actions[0].model_copy(update={'slots':result.actions[0].slots.model_validate(values)})]})
        return result
    baseline = _proved_goal_baseline(proof, message)
    if baseline is not None:
        from .intelligence import ProposedAction, ActionSlots
        # Raw text stays raw. Only a DB-verified single pending GOAL_VIEW can
        # explain the otherwise ambiguous monetary fragment as this field.
        slots = {key: value for key, value in proof.prior_actions[0]["slots"].items() if value is not None}
        slots["monthly_saving_minor"] = baseline
        return result.model_copy(update={"summary": message, "actions": [ProposedAction(kind="GOAL_VIEW", slots=ActionSlots.model_validate(slots))]})
    if proof and proof.scope == "READ_ONLY" and len(proof.prior_actions) == 1 and proof.prior_actions[0]["kind"] == "BILL_ANALYSIS":
        from .bill_category_filter import category_filter_in_text
        category = category_filter_in_text(message)
        if category is not None:
            from .intelligence import ProposedAction, ActionSlots
            slots = {key: value for key, value in proof.prior_actions[0]["slots"].items() if value is not None}
            slots["category_filter"] = category
            return result.model_copy(update={"summary": message, "actions": [ProposedAction(kind="BILL_ANALYSIS", slots=ActionSlots.model_validate(slots))]})
    if (proof and proof.scope == "PENDING_SINGLE_ACTION" and len(proof.prior_actions) == 1
            and _plain_supplement(message.strip())):
        prior = proof.prior_actions[0]
        # A bare payee reply is not syntactically "to <name>". The finite
        # fallback may combine this exact literal only with a DB-verified,
        # single pending transfer that has no payee yet. Grounding still
        # rechecks original money/currency and never consumes confirmation.
        if (prior["kind"] in {"TRANSFER", "FX_TRANSFER"} and not prior["slots"].get("payee_name")
                and re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_· .-]{0,79}", message.strip())
                and not rule_proposal(message).actions):
            from .intelligence import ProposedAction, ActionSlots
            slots = {key: value for key, value in prior["slots"].items() if value is not None}
            slots["payee_name"] = message.strip()
            return result.model_copy(update={"summary": message, "actions": [
                ProposedAction(kind=prior["kind"], slots=ActionSlots.model_validate(slots))]})
    if proof and proof.scope == "READ_ONLY" and CARD_REFERENCE.fullmatch(message.strip()) and (
            any(a["kind"] == "CARD_STATUS" for a in proof.prior_actions) or (
                re.search(r"查|看|(?i:check|show)", _intent_text(proof.utterances[0])) and "余额" in _intent_text(proof.utterances[0]))):
        from .intelligence import ProposedAction, ActionSlots
        return result.model_copy(update={"actions": [ProposedAction(kind="CARD_STATUS", slots=ActionSlots(
            card_last4=CARD_REFERENCE.fullmatch(message.strip()).group(1)))]})
    if proof and proof.scope == "READ_ONLY" and not result.actions:
        for source in proof.utterances[:-1]:
            original = rule_proposal(source, **kwargs)
            if original.actions and all(a.kind in READ_KINDS for a in original.actions):
                return original.model_copy(update={"summary": message})
    return result


def ground_dialogue_proposal(proposal, message, context):
    proof = verified(context)
    grounded = ground_proposal(proposal, grounding_text(message, context))
    if not proof:
        return grounded
    if proof.restated_current_request and (len(proposal.actions) != 1 or proposal.actions[0].depends_on
            or proposal.actions[0].kind != proof.prior_actions[0]["kind"]):
        return grounded.model_copy(update={"actions": []})
    if proof.read_revision:
        from .intelligence import ProposedAction, ActionSlots
        expected = proof.read_revision["slots"]
        if len(proposal.actions) != 1 or proposal.actions[0].kind != proof.read_revision["action"] or proposal.actions[0].depends_on:
            return proposal.model_copy(update={"actions": []})
        values = proposal.actions[0].slots.model_dump(exclude_none=True)
        if proof.read_revision["action"] == "GOAL_VIEW":
            if values.get("currency") == "CNY":
                values.pop("currency")  # Fixed supported calculation denomination, not FX.
            total, base = expected.get("monthly_saving_total_minor"), expected.get("monthly_saving_minor")
            if total is not None and base is not None and values.get("monthly_saving_delta_minor") == total - base:
                values.pop("monthly_saving_delta_minor")  # Rechecked derived change, never added twice.
        # A qualification query has no merchant/card/reward/write target.
        # A total revision has only the independently rederived assumptions.
        if any(key not in expected or value != expected[key] for key, value in values.items()):
            return proposal.model_copy(update={"actions": []})
        return proposal.model_copy(update={"actions": [ProposedAction(kind=proof.read_revision["action"],
            slots=ActionSlots.model_validate(expected))], "clarification_questions": []})
    if proof.card_reference:
        # Prove the current positive single lock independently. Resolve only
        # its missing card against the DB-verified reference, never an LLM guess.
        from .card_lock_reference import lock_reference_request
        if not lock_reference_request(message) or len(proposal.actions)!=1 or proposal.actions[0].kind!='CARD_LOCK' or proposal.actions[0].depends_on:
            return grounded.model_copy(update={'actions':[]})
        raw=proposal.actions[0].slots.model_dump(exclude_none=True)
        if set(raw)-{'card_last4','card_number'} or raw.get('card_last4') not in {None,proof.card_reference['card_last4']} or raw.get('card_number') not in {None,proof.card_reference['card_number']} or len(grounded.actions)!=1 or grounded.actions[0].kind!='CARD_LOCK':
            return grounded.model_copy(update={'actions':[]})
        from .intelligence import ActionSlots
        return grounded.model_copy(update={'actions':[grounded.actions[0].model_copy(update={'slots':ActionSlots(card_last4=proof.card_reference['card_last4'])})]})
    allowed = ({proof.read_kind} if proof.read_kind else {a["kind"] for a in proof.prior_actions} or READ_KINDS) if proof.scope == "READ_ONLY" else {proof.prior_actions[0]["kind"]}
    kept = []
    baseline = _proved_goal_baseline(proof, message)
    from .bill_category_filter import category_filter_in_text
    category = category_filter_in_text(message) if proof.scope == "READ_ONLY" else None
    if proof.scope == "READ_ONLY":
        if re.search(r"工资卡|储蓄卡|[一二两三\d]+张卡", message) and not CARD_REFERENCE.fullmatch(message.strip()):
            return grounded.model_copy(update={"actions": [], "clarification_questions": [
                "请提供要查询的具体卡尾号；不会根据工资卡等称呼或卡片数量替你选择账户。"]})
        reference = CARD_REFERENCE.fullmatch(message.strip())
        if reference and (any(a["kind"] == "CARD_STATUS" for a in proof.prior_actions) or (
                re.search(r"查|看|(?i:check|show)", _intent_text(proof.utterances[0])) and "余额" in _intent_text(proof.utterances[0]))):
            from .intelligence import ActionSlots
            cards = [a for a in proposal.actions if a.kind == "CARD_STATUS" and not a.depends_on]
            card = cards[0] if len(cards) == 1 else None
            if card:
                if card.slots.card_last4 != reference.group(1):
                    return grounded.model_copy(update={"actions": [], "clarification_questions": [
                        "卡片引用与本条明确尾号不一致，请重新核对具体卡片；不会改查全部账户。"]})
                grounded = grounded.model_copy(update={"actions": [card.model_copy(update={"slots": ActionSlots(
                    card_last4=reference.group(1))})]})
        # Temporal Chinese such as “上个月的消费” can look like a third-party
        # possessive to the existing single-utterance veto. A supported temporal
        # reply cannot revoke the independently proven original owned read.
        # Recheck each exact original utterance, not a manufactured instruction.
        existing = {a.kind for a in grounded.actions}
        original_kinds = {a["kind"] for a in proof.prior_actions}
        for action in proposal.actions:
            if action.kind not in original_kinds or action.kind in existing or action.kind not in READ_KINDS:
                continue
            for source in proof.utterances[:-1]:
                prior_grounded = ground_proposal(proposal.model_copy(update={"actions": [action]}), source)
                if prior_grounded.actions:
                    grounded = grounded.model_copy(update={"actions": grounded.actions + prior_grounded.actions})
                    existing.add(action.kind)
                    break
    elif (proof.scope == "PENDING_SINGLE_ACTION" and not grounded.actions
            and proof.utterances[-1] == message and _plain_supplement(message.strip())
            and not _CONDITIONAL.search(message) and not _GLOBAL_NO_WRITE.search(message)
            and not EN_UNMET_CONDITION.search(message) and not EN_NO_WRITE.search(message)):
        # A bounded bare request such as "我想转100元" is a complete original
        # sentence, not an arbitrary-prefix command. Joining a recipient reply
        # changes that grammar; re-prove the write anchor against each exact
        # DB-owned original sentence instead of broadening its regex. Only the
        # existing pending kind survives. The ordinary literal field checks
        # below still prove the latest payee and preserve all original slots.
        original_kind = proof.prior_actions[0]["kind"]
        offered = [action for action in proposal.actions if action.kind == original_kind and not action.depends_on]
        if len(offered) == 1:
            for source in proof.utterances[:-1]:
                original = ground_proposal(proposal.model_copy(update={"actions": offered}), source)
                if original.actions:
                    grounded = grounded.model_copy(update={"actions": original.actions})
                    break
    for action in grounded.actions:
        if action.kind not in allowed:
            continue
        if action.depends_on:
            if proof.scope == "READ_ONLY" and proof.read_kind == action.kind:
                action = action.model_copy(update={"depends_on": []})  # A fresh selected read, not the old DAG.
            else:
                continue
        if proof.scope == "READ_ONLY":
            if category is not None and action.kind == "BILL_ANALYSIS":
                raw = next((a for a in proposal.actions if a.kind == "BILL_ANALYSIS" and not a.depends_on), None)
                if raw is None or raw.slots.category_filter not in {None, category}:
                    continue
                values = action.slots.model_dump()
                values["category_filter"] = category
                action = action.model_copy(update={"slots": action.slots.model_validate(values)})
            if baseline is not None and action.kind == "GOAL_VIEW":
                raw = next((a for a in proposal.actions if a.kind == "GOAL_VIEW" and not a.depends_on), None)
                if raw is None or raw.slots.model_dump().get("monthly_saving_minor") not in {None, baseline}:
                    continue
                values = action.slots.model_dump()
                values["monthly_saving_minor"] = baseline
                action = action.model_copy(update={"slots": action.slots.model_validate(values)})
            targets = ("card_last4", "card_number", "product_name", "merchant", "goal_name", "owner_name",
                "amount_minor", "currency", "source_currency", "target_currency",
                "monthly_saving_minor", "monthly_saving_delta_minor")
            priors = [a["slots"] for a in proof.prior_actions if a["kind"] == action.kind]
            scoped = [slots for slots in priors if any(slots.get(field) is not None for field in targets)]
            values = action.slots.model_dump()
            reference = CARD_REFERENCE.fullmatch(message.strip())
            # Read-only is not permission to expand a named card/object to all
            # records. Missing/changed model targets require a new clarification.
            explicit_card = action.kind == "CARD_STATUS" and reference and values.get("card_last4") == reference.group(1)
            if (scoped and not explicit_card and not any(all(values.get(field) == slots.get(field)
                    for field in targets if slots.get(field) is not None and not (field == "monthly_saving_minor" and baseline is not None)) for slots in scoped)) or (
                    action.kind == "CARD_STATUS" and reference and values.get("card_last4") != reference.group(1)):
                continue
        if proof.scope == "PENDING_SINGLE_ACTION":
            original = proof.prior_actions[0]["slots"]
            values = action.slots.model_dump()
            raw = next((a for a in proposal.actions if a.kind == action.kind), None)
            # Only a literal current reply can fill a previously missing object.
            # The model cannot shorten an account name or borrow bank metadata.
            for field in ("payee_name", "product_name", "merchant", "card_last4"):
                literal = raw.slots.model_dump().get(field) if raw else None
                if original.get(field) is not None and literal == original[field] and any(
                        literal in source for source in proof.utterances[:-1]):
                    values[field] = literal  # A previously checked, unchanged user object.
                if original.get(field) is None and literal == message.strip() and (
                        field != "card_last4" or re.fullmatch(r"\d{4}", literal)):
                    values[field] = literal
            if any(values.get(field) != value for field, value in original.items() if value is not None):
                continue
            action = action.model_copy(update={"slots": action.slots.model_validate(values)})
        kept.append(action)
    return grounded.model_copy(update={"actions": kept, "clarification_questions": (grounded.clarification_questions + (
        ["请重新核对原查询的具体卡片或对象；不会把缺失或变化的目标扩为全部记录。"]
        if proof.scope == "READ_ONLY" and grounded.actions and not kept else []))[:8]})


def validate_dialogue_proposal(session, parent, proposal, metadata, *, execution_context=None):
    proof = load_verified_dialogue(session, parent, metadata, execution_context=execution_context)
    if not proof:
        return
    checked = ground_dialogue_proposal(proposal, parent.message, {PRIVATE_KEY: proof})
    if [a.model_dump() for a in checked.actions] != [a.model_dump() for a in proposal.actions]:
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "补充提案超出已核对的用户原话范围；未执行业务。", 409)
