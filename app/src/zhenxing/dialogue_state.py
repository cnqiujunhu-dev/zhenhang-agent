"""Rebuild bounded conversational focus from real, owned user receipts.

The persisted checkpoint is a cache, not executable authority. Every projection
rechecks live account/delegation scope and recomposes task frames from database
receipts, source-bound proposals and operation lifecycle. There are no model,
banking-write, confirmation, or caller-transaction commit operations here.
"""
from copy import deepcopy
import re

from sqlalchemy import case, func, select, update

from .access_models import LoginAccount
from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .dialogue_state_models import ConversationDialogueState
from .intelligence import IntentProposal, READ_KINDS, _intent_text, ground_proposal, rule_proposal
from .models import Task, User, utcnow
from .planning import canonical_hash
from .runtime_models import PlanSnapshot, RuntimeOperation, RuntimeRecord
from .family_read_contract import SELECTOR_FIELDS as FAMILY_READ_FIELDS

VERSION = "conversation-dialogue-state-v1"
AUTHORITY = "DB_OWNED_DIALOGUE_NAVIGATION_NOT_BANK_AUTHORIZATION"
MAX_FRAMES = 32
MAX_SOURCES = 8
RESUMABLE = {"QUEUED", "UNDERSTANDING", "PLANNING", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION"}
EFFECT_STATES = {"AUTHORIZED", "RUNNING", "UNKNOWN", "SUCCEEDED"}
_ASIDE_MODES = {"GREETING", "DISCOVERY", "FEEDBACK", "STATUS", "CONSULT"}
_RESUME = re.compile(r"刚才|之前|原来|回到|继续|接着|返回|\b(?:resume|continue|return|previous|earlier)\b", re.I)
_DOMAIN_WORDS = {
    "TRANSFER": r"转账|转帐|汇款|房租|transfer|payment",
    "FX_TRANSFER": r"转账|转帐|汇款|美元|transfer|dollars?",
    "BILL_ANALYSIS": r"账单|消费|支出|bills?|spending",
    "CARD_STATUS": r"卡片|银行卡|余额|cards?|balance",
    "WEALTH_BUY": r"理财|申购|买入|investment|purchase",
    "WEALTH_REDEEM": r"理财|赎回|redeem",
    "SUBSCRIPTION_LIST": r"订阅|会员|续费|subscriptions?|membership",
    "SUBSCRIPTION_CANCEL": r"订阅|会员|续费|subscriptions?|membership",
    "SUBSCRIPTION_START": r"订阅|会员|开通|恢复|subscriptions?|membership",
    "SUBSCRIPTION_RENEW": r"订阅|会员|续订|续期|续费|subscriptions?|membership",
    "FRAUD_ANALYZE": r"诈骗|短信|骗子|反诈|fraud|scam",
    "FAMILY_VIEW": r"家人|家庭|父母|爸爸|妈妈|family|parent",
    "FAMILY_PROPOSE": r"协助|提案|家人|family|proposal",
}
# The existing proposal schema has one shared ActionSlots union, not a
# per-kind ACTION_SLOT_FIELDS constant. This navigation allowlist narrows that
# union to fields actually consumed by each registered runtime operation.
# It is not sufficient grounding or permission for an update by itself.
ACTION_ALLOWED_FIELDS = {
    "TRANSFER": {"payee_name", "amount_minor", "currency", "memo", "recipient_alias", "replace_alias"},
    "TRANSFER_SCHEDULE": {"payee_name", "amount_minor", "currency", "memo", "scheduled_at", "recipient_alias", "replace_alias"},
    "AA_REQUEST": {"amount_minor", "currency", "participant_names", "participant_count"},
    "FX_TRANSFER": {"payee_name", "amount_minor", "currency", "source_currency", "target_currency", "card_number", "card_last4", "recipient_card_reference", "recipient_alias", "replace_alias"},
    "FX_EXCHANGE": {"amount_minor", "currency", "source_currency", "target_currency", "card_number", "card_last4"},
    "FX_QUOTE": {"amount_minor", "currency", "source_currency", "target_currency"},
    "BILL_ANALYSIS": {"category_filter"},
    "CARD_STATUS": {"card_last4", "card_number", "currency", "card_type", "card_balance_currencies"},
    "CARD_LOCK": {"card_last4", "card_number"},
    "CARD_UNLOCK": {"card_last4", "card_number"},
    "CARD_REPORT_LOSS": {"card_last4", "card_number"},
    "CARD_REPLACEMENT": {"card_last4", "card_number"},
    "CARD_SET_LIMIT": {"card_last4", "card_number", "amount_minor", "currency"},
    "WEALTH_COMPARE": {"product_name"},
    "ASSET_POSITIONS_READ": {"asset_class", "product_name"},
    "MEMBERSHIP_VIEW": set(),
    "BENEFITS_VIEW": {"benefit_name"},
    "PREFERENCE_VIEW": {"preference_kind"},
    "PREFERENCE_SET": {"preference_kind", "preference_value"},
    "PREFERENCE_REMOVE": {"preference_kind"},
    "WEALTH_BUY": {"product_name", "amount_minor", "currency", "units"},
    "WEALTH_REDEEM": {"product_name", "amount_minor", "currency", "units"},
    "SUBSCRIPTION_LIST": {"merchant"},
    "SUBSCRIPTION_CANCEL": {"merchant"},
    "SUBSCRIPTION_START": {"merchant", "subscription_months", "subscription_auto_renew"},
    "SUBSCRIPTION_RENEW": {"merchant", "subscription_months", "subscription_auto_renew"},
    "FRAUD_ANALYZE": {"context_message"},
    "FAMILY_VIEW": FAMILY_READ_FIELDS,
    "FAMILY_GRANT": {"grantee_name", "scopes", "maximum_amount_minor", "expires_days", "limit_period"},
    "FAMILY_REVOKE": {"grantee_name"},
    "FAMILY_PROPOSE": {"owner_name", "proposal_kind", "payee_name", "amount_minor", "card_last4", "card_number"},
    "CARBON_VIEW": {"reward_name", "behavior_name"},
    "CARBON_CLAIM": {"behavior_name"},
    "CARBON_REDEEM": {"reward_name"},
    "CONTACT_BIND": {"payee_name", "recipient_alias", "replace_alias", "card_number"},
    "GOAL_VIEW": {"goal_name", "monthly_saving_minor", "monthly_saving_delta_minor", "monthly_saving_total_minor"},
    "GOAL_CREATE": {"goal_name", "target_minor", "due_date"},
    "GOAL_RESERVE": {"goal_name", "amount_minor"},
    "GOAL_RELEASE": {"goal_name", "amount_minor"},
}


from .upgraded_capabilities import ACTION_FIELDS as UPGRADED_ACTION_FIELDS
ACTION_ALLOWED_FIELDS.update(UPGRADED_ACTION_FIELDS)
ACTION_ALLOWED_FIELDS['TRANSFER'].update({'card_number', 'card_last4', 'recipient_card_reference'})
ACTION_ALLOWED_FIELDS['TRANSFER_SCHEDULE'].update({'card_number', 'card_last4', 'recipient_card_reference',
    'schedule_frequency', 'schedule_occurrence_count', 'schedule_end_at','schedule_occurrence_times','schedule_reference',
    'schedule_start_month','schedule_day_of_month'})
ACTION_ALLOWED_FIELDS['AA_REQUEST'].update({'recipient_card_reference', 'aa_expires_at', 'aa_note'})
ACTION_ALLOWED_FIELDS['BILL_ANALYSIS'].update({'card_number', 'card_last4', 'currency'})
for _kind in ('WEALTH_BUY', 'WEALTH_REDEEM', 'SUBSCRIPTION_START', 'SUBSCRIPTION_RENEW'):
    ACTION_ALLOWED_FIELDS[_kind].update({'card_number', 'card_last4'})


def dialogue_actor_scope(session, principal):
    """Stable across a customer's token refresh, pinned to helper proof epoch."""
    from .permissions import require_banking_actor
    from .runtime import trusted_family_context
    role = require_banking_actor(session, principal)
    user = session.get(User, principal.user_id)
    if user is None or user.sandbox_id != principal.sandbox_id:
        raise DomainError("CHAT_CONVERSATION_NOT_FOUND", "会话不属于当前身份", 404)
    family = trusted_family_context(session, principal)
    account = session.scalar(select(LoginAccount).where(LoginAccount.user_id == principal.user_id,
        LoginAccount.sandbox_id == principal.sandbox_id))
    value = {"user_id": principal.user_id, "sandbox_id": principal.sandbox_id, "role": role,
        "login_account_id": account.id if account else None,
        "login_account_version": account.version if account else None,
        "family_context": family}
    return value, canonical_hash(value)


def _thread(session, principal, thread):
    identifier = thread.id if isinstance(thread, ChatThread) else thread
    row = session.scalar(select(ChatThread).where(ChatThread.id == identifier,
        ChatThread.user_id == principal.user_id, ChatThread.sandbox_id == principal.sandbox_id).with_for_update())
    if row is None:
        raise DomainError("CHAT_CONVERSATION_NOT_FOUND", "会话不属于当前身份", 404)
    return row


def _source_valid(session, principal, task, source, scope, epoch_sequence, source_validator):
    diagnostics = source.diagnostics_json or {}
    if source.status != "ANSWERED" or source.error_code or source.context_policy != "AUTO" \
        or source.sequence <= epoch_sequence or diagnostics.get("input_redacted") \
        or diagnostics.get("actor_role_at_submission") != scope["role"] \
        or source.reviewed_evidence_refs_json \
        or (source.user_id, source.sandbox_id) != (principal.user_id, principal.sandbox_id):
        return False
    # New submissions may carry this snapshot. Legacy receipts are still
    # checked through the epoch checkpoint and original runtime provenance.
    recorded_scope = diagnostics.get("dialogue_actor_scope_hash")
    if recorded_scope is not None and recorded_scope != canonical_hash(scope):
        return False
    for key, expected in (("actor_login_id", scope["login_account_id"]),
                          ("actor_login_version", scope["login_account_version"])):
        if key in diagnostics and diagnostics[key] != expected:
            return False
    if scope["family_context"] is not None and diagnostics.get("family_context_origin") != scope["family_context"]:
        return False
    if source_validator is not None and not source_validator(session, principal, task, source):
        return False
    return True


def _receipt_refs(session, principal, thread, task, receipt, record, scope, epoch_sequence, source_validator):
    metadata = record.metadata_json or {}
    refs = (metadata.get("owned_user_dialogue") or {}).get("source_refs")
    source_limit = MAX_SOURCES
    ledger = metadata.get("dialogue_slot_update")
    if ledger:
        from .dialogue_slot_updates import MAX_TURNS, MAX_LEGACY_SOURCE_TURNS
        if not isinstance(ledger, dict) or not isinstance(ledger.get("updates"), list):
            return None
        genesis_refs = (ledger.get("genesis") or {}).get("source_refs") or [(ledger.get("genesis") or {}).get("source_ref")]
        refs = [*genesis_refs,
            *[update.get("source_ref") for update in ledger["updates"] if isinstance(update, dict)]]
        source_limit = MAX_TURNS + MAX_LEGACY_SOURCE_TURNS
    if not refs:
        refs = [{"turn_id": receipt.id, "message_hash": canonical_hash({"message": receipt.message})}]
    if not isinstance(refs, list) or not 1 <= len(refs) <= source_limit:
        return None
    result, sequence = [], 0
    for ref in refs:
        source = session.get(ChatTurn, ref.get("turn_id")) if isinstance(ref, dict) else None
        if source is None or source.thread_id != thread.id or not sequence < source.sequence <= receipt.sequence \
            or not _source_valid(session, principal, task, source, scope, epoch_sequence, source_validator) \
            or ref.get("message_hash") != canonical_hash({"message": source.message}):
            return None
        sequence = source.sequence
        result.append({"turn_id": source.id, "sequence": source.sequence,
            "message_hash": canonical_hash({"message": source.message})})
    return result


def _grounded_actions(session, task, record, receipt, snapshot):
    metadata = record.metadata_json or {}
    try:
        if snapshot is None:
            if record.plan_version or receipt.message != task.message:
                return None
            return rule_proposal(receipt.message).model_dump()["actions"]
        proposal = IntentProposal.model_validate(snapshot.proposal_json)
        if snapshot.plan_hash != task.plan_hash or canonical_hash(snapshot.plan_json) != snapshot.plan_hash:
            return None
        if metadata.get("dialogue_slot_update"):
            from .dialogue_slot_updates import validate_slot_update_proposal
            validate_slot_update_proposal(session, task, proposal, metadata)
        elif metadata.get("owned_user_dialogue"):
            from .owned_user_dialogue import validate_dialogue_proposal
            validate_dialogue_proposal(session, task, proposal, metadata)
        elif (metadata.get("conversation_context") or {}).get("authority") == "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS":
            from .conversation_provenance import validate_continuation_proposal
            validate_continuation_proposal(session, task, proposal, metadata)
        elif receipt.message != task.message or ground_proposal(proposal, receipt.message).actions != proposal.actions:
            return None
        return proposal.model_dump()["actions"]
    except (DomainError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _frame(session, principal, thread, task, receipt, scope, epoch_sequence, source_validator):
    record = session.get(RuntimeRecord, task.id)
    if record is None or record.kind != "PARENT" or (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id):
        return None
    refs = _receipt_refs(session, principal, thread, task, receipt, record, scope, epoch_sequence, source_validator)
    if refs is None:
        return None
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record.plan_version else None
    actions = _grounded_actions(session, task, record, receipt, snapshot)
    if actions is None:
        return None
    from .runtime import validate_runtime_family_context
    from .task_dialogue_context import task_context
    try:
        validate_runtime_family_context(session, task)
        context = task_context(session, principal, task)
    except DomainError:
        return None
    if context is None:
        return None
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id)))
    effect = context.get("completed_write_present") or context.get("possible_write_effect") or any(
        op.action_kind not in READ_KINDS and (op.status in EFFECT_STATES or
        op.child_task_id and (child := session.get(Task, op.child_task_id)) and
        child.state in {"SUCCEEDED", "EXECUTING", "AUTHORIZED", "UNKNOWN"}) for op in operations)
    writable = task.state in RESUMABLE and not effect and thread.status == "ACTIVE"
    projection = {"frame_ref": "task:" + task.id, "task_id": task.id, "task_version": task.version,
        "state": task.state, "source_sequence": receipt.sequence, "source_refs": refs,
        "source_message_hash": canonical_hash({"message": task.message}),
        "snapshot_id": snapshot.id if snapshot else None,
        "snapshot_hash": canonical_hash(snapshot.proposal_json) if snapshot else None,
        "plan_version": record.plan_version, "actions": [{"action_ref": "action:" + task.id + ":" + str(index),
            "kind": action["kind"], "slots": {key: value for key, value in action["slots"].items() if value is not None},
            "allowed_fields": sorted(ACTION_ALLOWED_FIELDS.get(action["kind"], set())),
            "depends_on": action["depends_on"]} for index, action in enumerate(actions)],
        "needed_fields": deepcopy(context.get("needed_fields", [])),
        "read_only": bool(actions) and all(action["kind"] in READ_KINDS for action in actions),
        "continuation_allowed": bool(writable),
        "writable_continuation": bool(writable and any(action["kind"] not in READ_KINDS for action in actions)),
        "write_effect_possible": bool(effect),
        "domains": context.get("domains", []), "lifecycle": {key: context[key] for key in
            ("outcome_kind", "verified", "confirmation_status", "completed_write_present", "possible_write_effect")},
        "authority": AUTHORITY}
    projection["frame_hash"] = canonical_hash(projection)
    return projection


def _build(session, principal, thread, scope, checkpoint, current_turn, source_validator):
    epoch_sequence = checkpoint.get("epoch_start_sequence", 0)
    if type(epoch_sequence) is not int or epoch_sequence < 0:
        epoch_sequence = 0
    conditions = [ChatTurn.thread_id == thread.id, ChatTurn.user_id == principal.user_id,
        ChatTurn.sandbox_id == principal.sandbox_id, ChatTurn.status == "ANSWERED", ChatTurn.task_id.is_not(None),
        ChatTurn.sequence > epoch_sequence, ChatTurn.mode.in_(["NEW_TASK", "REVISE", "CLARIFY"])]
    if current_turn is not None:
        if (current_turn.thread_id, current_turn.user_id, current_turn.sandbox_id) != (thread.id, principal.user_id, principal.sandbox_id):
            raise DomainError("CHAT_TURN_SCOPE_INVALID", "消息不属于当前会话", 404)
        conditions.append(ChatTurn.sequence < current_turn.sequence)
    # Distinct latest receipt per task, bounded in SQL rather than truncating
    # an unbounded materialized history. Stale derived frames are never merged.
    task_scope = [Task.user_id == principal.user_id, Task.sandbox_id == principal.sandbox_id]
    latest_query = select(ChatTurn.task_id.label("task_id"), func.max(ChatTurn.sequence).label("sequence")) \
        .join(Task, Task.id == ChatTurn.task_id).where(*conditions, *task_scope).group_by(ChatTurn.task_id, Task.state)
    # One current result and suspended unfinished goals outrank old completed
    # reads. Otherwise thirty-two incidental queries would silently forget a
    # pending transfer even though the user never abandoned it.
    priority = case((ChatTurn.task_id == thread.current_task_id, 0), (Task.state.in_(RESUMABLE), 1), else_=2)
    latest = latest_query.order_by(priority, func.max(ChatTurn.sequence).desc()).limit(MAX_FRAMES).subquery()
    receipts = session.scalars(select(ChatTurn).join(latest,
        (ChatTurn.task_id == latest.c.task_id) & (ChatTurn.sequence == latest.c.sequence))
        .where(*conditions).order_by(ChatTurn.sequence.desc()))
    frames = []
    for receipt in receipts:
        task = session.get(Task, receipt.task_id)
        if task is not None and (frame := _frame(session, principal, thread, task, receipt, scope, epoch_sequence, source_validator)):
            frames.append(frame)
    focus = next((frame for frame in frames if frame["task_id"] == thread.current_task_id), None)
    if focus is None and frames:
        focus = frames[0]
    pending_count = session.scalar(select(func.count()).select_from(
        latest_query.where(Task.state.in_(RESUMABLE)).subquery())) or 0
    return {"schema_version": VERSION, "authority": AUTHORITY, "actor_scope_hash": canonical_hash(scope),
        "focus_task_id": focus["task_id"] if focus else None, "focus": focus, "frames": frames,
        "suspended": [frame for frame in frames if frame is not focus and frame["continuation_allowed"]],
        "checkpoint": {"epoch_start_sequence": epoch_sequence,
            "latest_receipt_sequence": max((frame["source_sequence"] for frame in frames), default=epoch_sequence),
            "pending_frames_truncated": pending_count > MAX_FRAMES,
            "thread_version": thread.version}}


def sync_dialogue_state(session, principal, thread, current_turn=None, *, source_validator=None):
    """Caller-owned transaction; row locking plus version CAS on every change."""
    thread = _thread(session, principal, thread)
    scope, scope_hash = dialogue_actor_scope(session, principal)
    state = session.scalar(select(ConversationDialogueState).where(
        ConversationDialogueState.thread_id == thread.id).with_for_update().execution_options(populate_existing=True))
    if state is None:
        dialect = session.get_bind().dialect.name
        if dialect not in {"sqlite", "postgresql"}:
            raise DomainError("DIALOGUE_DATABASE_UNSUPPORTED", "对话状态暂不支持此数据库", 503)
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        session.execute(insert(ConversationDialogueState).values(thread_id=thread.id, version=1,
            actor_scope_hash=scope_hash, focus_task_id=None, state_json={}, checkpoint_json={}, updated_at=utcnow())
            .on_conflict_do_nothing(index_elements=["thread_id"]))
        state = session.scalar(select(ConversationDialogueState).where(
            ConversationDialogueState.thread_id == thread.id).with_for_update().execution_options(populate_existing=True))
    changed_epoch = state.actor_scope_hash != scope_hash
    checkpoint = dict(state.checkpoint_json or {})
    if changed_epoch:
        limit = [ChatTurn.thread_id == thread.id, ChatTurn.status == "ANSWERED"]
        if current_turn is not None:
            limit.append(ChatTurn.sequence < current_turn.sequence)
        checkpoint = {"epoch_start_sequence": session.scalar(select(func.max(ChatTurn.sequence)).where(*limit)) or 0}
    projection = _build(session, principal, thread, scope, checkpoint, current_turn, source_validator)
    if state.state_json != projection or changed_epoch:
        revision = state.version
        result = session.execute(update(ConversationDialogueState).where(
            ConversationDialogueState.thread_id == thread.id, ConversationDialogueState.version == revision)
            .values(version=revision + 1, actor_scope_hash=scope_hash, focus_task_id=projection["focus_task_id"],
                state_json=projection, checkpoint_json=projection["checkpoint"], updated_at=utcnow())
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise DomainError("DIALOGUE_STATE_VERSION_CHANGED", "对话状态已更新，请重试本条消息", 409, retryable=True)
        session.expire(state)
    return {**deepcopy(projection), "revision": state.version}


def _missing_amount(frame):
    if not frame["writable_continuation"]:
        return False
    fields = {field for row in frame["needed_fields"] if isinstance(row, dict)
        for field in row.get("missing_fields", [])}
    if fields & {"amount_minor", "amount", "units", "maximum_amount_minor", "target_minor"}:
        return True
    return frame["state"] == "NEED_CLARIFICATION" and any(action["kind"] in
        {"TRANSFER", "FX_TRANSFER", "TRANSFER_SCHEDULE", "WEALTH_BUY", "WEALTH_REDEEM", "FAMILY_PROPOSE", "CARD_SET_LIMIT"}
        and action["slots"].get("amount_minor") is None and action["slots"].get("units") is None for action in frame["actions"])


def _named_object(frame, message):
    values = [value for action in frame["actions"] for key, value in action["slots"].items()
        if key in {"payee_name", "product_name", "merchant", "grantee_name", "owner_name", "card_last4", "card_number"}
        and isinstance(value, str) and value]
    return any(value in message for value in values)


def _named_domain(frame, message):
    return any(re.search(_DOMAIN_WORDS.get(action["kind"], r"(?!)"), message, re.I)
        for action in frame["actions"])


def _selection(projection, selected, reason, candidates, message):
    proof = {"schema_version": VERSION, "authority": AUTHORITY, "actor_scope_hash": projection["actor_scope_hash"],
        "revision": projection["revision"], "task_id": selected["task_id"] if selected else None,
        "task_version": selected["task_version"] if selected else None,
        "frame_hash": selected["frame_hash"] if selected else None,
        "reason": reason, "message_hash": canonical_hash({"message": message}),
        "candidate_hashes": [{"task_id": frame["task_id"], "frame_hash": frame["frame_hash"]} for frame in candidates]}
    return {"task_id": selected["task_id"] if selected else None, "reason": reason,
        "ambiguous": selected is None and bool(candidates),
        "candidates": [{"frame_ref": frame["frame_ref"], "task_id": frame["task_id"],
            "task_version": frame["task_version"], "actions": frame["actions"]} for frame in candidates], "proof": proof}


def select_dialogue_focus(session, principal, thread, message, requested_task_id=None, current_turn=None, *, source_validator=None):
    """Select owned conversational intent, never authorize an operation.

    Money alone can resume one unfinished amount slot after an unrelated read;
    two real pending goals remain ambiguous. A named resume can select an older
    read or unfinished goal, but never a settled/unknown write for modification.
    """
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
        return None
    if current_turn is not None and current_turn.context_policy != "AUTO":
        return None
    if _intent_text(message) != message:
        return None  # Quoted/third-party instructions never select a write.
    projection = sync_dialogue_state(session, principal, thread, current_turn, source_validator=source_validator)
    frames = projection["frames"]
    if requested_task_id:
        matches = [frame for frame in frames if frame["task_id"] == requested_task_id]
        return _selection(projection, matches[0], "EXPLICIT_OWNED_TASK", matches, message) if matches else None
    if _RESUME.search(message):
        eligible = [frame for frame in frames if frame["continuation_allowed"] or
            frame["read_only"] and frame["state"] == "SUCCEEDED"]
        from .intelligence import _CONDITIONAL, _GLOBAL_NO_WRITE, _NEGATION
        from .language import EN_NO_WRITE, EN_UNMET_CONDITION
        if _CONDITIONAL.search(message) or _GLOBAL_NO_WRITE.search(message) or _NEGATION.search(message) or EN_NO_WRITE.search(message) or EN_UNMET_CONDITION.search(message):
            eligible = [frame for frame in eligible if frame["read_only"]]
        matches = [frame for frame in eligible if _named_object(frame, message)]
        if not matches:
            matches = [frame for frame in eligible if _named_domain(frame, message)]
        if matches:
            unique = len(matches) == 1 and not projection["checkpoint"]["pending_frames_truncated"]
            return _selection(projection, matches[0] if unique else None,
                "NAMED_RESUME" if unique else "AMBIGUOUS_NAMED_RESUME", matches, message)
    from .conversation_service import _continuation_money
    money = _continuation_money(message.strip(), "CNY")
    if money:
        matches = [frame for frame in frames if _missing_amount(frame)]
        if matches:
            unique = len(matches) == 1 and not projection["checkpoint"]["pending_frames_truncated"]
            return _selection(projection, matches[0] if unique else None,
                "UNIQUE_PENDING_AMOUNT" if unique else "AMBIGUOUS_PENDING_AMOUNT", matches, message)
    return None


def validate_dialogue_focus_selection(session, principal, thread, proof, message, current_turn=None, *, source_validator=None):
    """Recheck every referenced frame without trusting the persisted cache."""
    if not isinstance(proof, dict) or proof.get("schema_version") != VERSION or proof.get("authority") != AUTHORITY \
        or proof.get("message_hash") != canonical_hash({"message": message}) \
        or not isinstance(proof.get("candidate_hashes"), list) or not 1 <= len(proof["candidate_hashes"]) <= MAX_FRAMES \
        or any(not isinstance(row, dict) or set(row) != {"task_id", "frame_hash"} for row in proof["candidate_hashes"]):
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "原会话需求来源已变化，请重新核对", 409)
    projection = sync_dialogue_state(session, principal, thread, current_turn, source_validator=source_validator)
    hashes = [{"task_id": frame["task_id"], "frame_hash": frame["frame_hash"]}
        for frame in projection["frames"] if frame["task_id"] in {row.get("task_id") for row in proof.get("candidate_hashes", [])}]
    if proof.get("actor_scope_hash") != projection["actor_scope_hash"] \
        or hashes != proof.get("candidate_hashes") or proof.get("task_id") is not None and not any(
            frame["task_id"] == proof["task_id"] and frame["task_version"] == proof.get("task_version")
            and frame["frame_hash"] == proof.get("frame_hash") for frame in projection["frames"]):
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "原会话需求来源已变化，请重新核对", 409)
    return True
