"""Persisted conversation policy. No chat utterance can consume confirmation."""
from decimal import Decimal
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .authentication import as_utc
from .contracts import DomainError
from .conversation_models import ChatThread, ChatTurn
from .decision_advisor import redact_advice_input
from .direct_read_questions import direct_owned_read_kind
from .intelligence import READ_KINDS, _allowed_evidence, _clauses, _cny_money_evidence, _intent_text, _usd_money_evidence, financial_inquiry, rule_proposal
from .models import Task, utcnow
from .permissions import actor_role, require_banking_actor
from .planning import canonical_hash
from .product_knowledge import answer_from_sources, public_sources, scoped_sources, source_fingerprint, sources_still_authorized
from .runtime_models import RuntimeOperation, RuntimeRecord
from .services import Principal, owned_task
from .knowledge_runtime import knowledge_sources_current
from .language import (EN_RECIPIENT_CONTINUATION, detect_reply_language, normalize_language,
                        english_amount_fragment, english_continuation_money, polite_operational_body)


from .conversation_dispatch import LEASE_SECONDS
TERMINAL = {"SUCCEEDED", "FAILED", "CLOSED"}
IN_FLIGHT = {"EXECUTING", "UNKNOWN", "AUTHORIZED"}
CONSULT_PATTERN = re.compile(r"怎么|怎样|如何|为什么|为啥|什么情况下|什么条件|什么时候|何时|解释|介绍|说明|区别|是什么意思|是什么|你刚才|你刚说|可以吗|是否|能否|能不能|可不可以|行不行|可以.{0,12}吗")
ONLY_CONSULT_PATTERN = re.compile(r"(?:仅|只|仅仅)咨询|不办理(?:任何)?银行业务|不执行(?:任何)?操作")
CONTINUE_PATTERN = re.compile(r"^(?:再)?(?:继续|接着|还是|金额|改成|改为|改到|换成)|给(?:他|她|TA|那个人)转|那张卡|它|第二笔", re.I)
STATUS_PATTERN = re.compile(r"进度|成功了?吗|完成了?吗|查单|结果|状态|刚才.{0,8}(?:怎样|怎么)|到账了?吗")
PURE_STATUS_QUESTION = re.compile(r"^(?:这笔|这项|这个|刚才的|刚才那笔|刚才那项|刚才|当前任务)?\s*"
    r"(?:办(?:完|好)|查(?:完|好)|查询(?:完|好|完成)|好|(?:办理|处理|执行)(?:完|好|完成)|完成)\s*(?:了吗|了没|没|吗)\s*[？?]?$")
CANCEL_WORD_PATTERN = re.compile(r"取消|撤销|撤回|别执行|不要执行")
CANCEL_HELP_PATTERN = re.compile(r"怎么|如何|为什么|为啥|能否|能不能|是否|可不可以|(?:流程|规则|条件|方式|方法|解释|说明)|(?:吗|么)\s*[？?]?$")
CANCEL_CONTROL_PATTERN = re.compile(r"^(?:请|帮我|麻烦|现在|先|再|直接)?\s*"
    r"(?:(?:取消|撤销|撤回)(?:(?:当前|刚才|这个|那个|这笔|那笔)?(?:的)?(?:任务|请求|操作|转账))?|"
    r"(?:别|不要|先别)执行(?:当前任务)?)\s*(?=[，,。；;]|$)")
CREDENTIAL_PATTERN = re.compile(r"验证码|校验码|动态码|密码|密钥|token|api[_ -]?key", re.I)
CREDENTIAL_HELP_PATTERN = re.compile(r"忘(?:了|记)|收不到|没(?:有)?收到|过期|失效|用途|安全|干什么")
CREDENTIAL_VALUE_PATTERN = re.compile(
    r"((?:验证码|校验码|动态码|密码|密钥|token|api[_ -]?key)\s*"
    r"(?:是不是|会不会是|收到的是|填的是|需要填|要填|应该填|设置为|改成|改为|换成)\s*[：:=]?\s*)"
    r"[A-Za-z0-9_-]{4,256}", re.I)
RECIPIENT_CONTINUE_PATTERN = re.compile(
    r"^(?:(?:收款人|收款账户|收款账号)(?:改为|改成|换成|是|为)?\s*[：:]?\s*|"
    r"(?:改为|改成|换成|给|向)\s*)"
    r"(?P<recipient>(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[A-Za-z][A-Za-z0-9_]{2,31}|[\u4e00-\u9fff]{2,16})"
    r"(?:\s*[，,]\s*(?:原)?金额不变)?\s*[。！!]?\s*$", re.I)
CONTINUATION_MONEY_PATTERN = re.compile(
    r"^(?:再)?(?:金额(?:改成|改为|调整为)?|改成|改为|改到|换成|给(?:他|她|TA|那个人)转)?\s*"
    r"(?P<body>(?:(?P<prefix>人民币|美元|美金|CNY|RMB|USD)\s*)?"
    r"(?P<number>[\d零〇一二两三四五六七八九十百千万亿]+(?:[.点][\d零〇一二两三四五六七八九]{1,2})?)\s*"
    r"(?:(?P<unit>块钱|块|元|人民币|美元|美金|CNY|RMB|USD)\s*"
    r"(?P<denomination>人民币|美元|美金|CNY|RMB|USD)?\s*)?"
    r"(?P<fraction>(?P<zero_fen>[零〇0]\s*[1-9一二两三四五六七八九]\s*分)|(?:[\d零〇一二两三四五六七八九]\s*(?:角|毛)\s*)?(?:[\d零〇一二两三四五六七八九]\s*分)?|(?P<colloquial_jiao>[\d零〇一二两三四五六七八九]))?"
    r"\s*(?P<trailing_denomination>人民币|CNY|RMB|美元|美金|USD)?)$", re.I)
AMOUNT_LIKE_PATTERN = re.compile(
    r"^(?=.*[\d零〇一二两三四五六七八九十百千万亿])"
    r"(?:[\d零〇一二两三四五六七八九十百千万亿点.wW\s+\-−±,，~～]|人民币|美元|美金|CNY|USD|元|块钱|块|角|毛|分|"
    r"大约|大概|约|左右|上下|以上|以下|至少|最多|以内|超过|不到|到|至|负)+$", re.I)


def _extended(pattern, english):
    return re.compile("(?:"+pattern.pattern+")|(?:"+english+")", re.I)


CONSULT_PATTERN = _extended(CONSULT_PATTERN, r"\b(?:how|why|what|whether|could|would|explain|describe)\b|\bdiscuss\s+(?:the\s+)?(?:wording|meaning|quoted\s+text)\b|^\s*(?:can|may|is|are|does)\b")
ONLY_CONSULT_PATTERN = _extended(ONLY_CONSULT_PATTERN, r"\b(?:only\s+(?:explain|consult)|just\s+(?:explain|ask)|read[- ]only|do\s+not\s+(?:execute|change)|don['’]?t\s+(?:execute|change))\b")
TASK_REFERENCE_PATTERN = re.compile(
    r"当前(?:任务|交易)|关联任务|这个任务|这笔(?:任务|转账|交易)|上一(?:笔|项|个)任务|刚才(?:的)?(?:任务|那笔|那项)|"
    r"\b(?:this|that|current|previous|prior|original|last|earlier)\s+(?:task|request|transaction|payment|transfer|operation)\b", re.I)
CONTINUE_PATTERN = _extended(CONTINUE_PATTERN, r"^\s*(?:continue|change\s+(?:the\s+)?(?:amount|recipient)|change\s+to|make\s+it|amount\b|instead\b|that\s+card|the\s+second)\b")
STATUS_PATTERN = _extended(STATUS_PATTERN, r"\b(?:progress|status|completed|finished|succeeded|received|did\s+it\s+(?:work|go\s+through))\b")
PURE_STATUS_QUESTION = _extended(PURE_STATUS_QUESTION, r"^\s*(?:(?:has|did|is)\s+(?:it|this\s+(?:transfer|request)|the\s+(?:transfer|request))\s+(?:completed|finished|succeeded|work|go\s+through)|(?:check|show)\s+(?:the\s+)?(?:original\s+request\s+)?(?:progress|status))\s*[?!.]?$")
PURE_STATUS_QUESTION = _extended(PURE_STATUS_QUESTION, r"^\s*(?:(?:这笔|这项|这个|刚才的|刚才那笔|刚才那项|刚才|当前任务)\s*)?(?:搞定(?:了)?(?:吗|没)|弄好(?:了)?(?:吗|没)|查到(?:了)?(?:吗|没|吧)|查完(?:了)?(?:吗|没)|办好(?:了)?(?:吗|没))\s*[？?。.!！]?$|^\s*(?:is\s+it\s+done|all\s+done|did\s+you\s+find\s+it)\s*[?!.]?$")
CANCEL_WORD_PATTERN = _extended(CANCEL_WORD_PATTERN, r"\b(?:cancel|revoke|withdraw|don['’]?t\s+execute|do\s+not\s+execute)\b")
CANCEL_HELP_PATTERN = _extended(CANCEL_HELP_PATTERN, r"\b(?:how|why|could|can|whether|rules?|procedure|instructions?)\b")
CANCEL_CONTROL_PATTERN = _extended(CANCEL_CONTROL_PATTERN, r"^\s*(?:please\s+)?(?:cancel|withdraw|revoke)\s+(?:the\s+)?(?:current|previous|this|that)\s+(?:task|request|transfer|operation)\s*(?=[,;.]|$)")
CREDENTIAL_PATTERN = _extended(CREDENTIAL_PATTERN, r"\b(?:password|passcode|verification\s+code|OTP|secret|credential)\b")
CREDENTIAL_HELP_PATTERN = _extended(CREDENTIAL_HELP_PATTERN, r"\b(?:forgot|forgotten|not\s+received|expired|missing|purpose|safety)\b")


def _recipient_match(message):
    return RECIPIENT_CONTINUE_PATTERN.fullmatch(message.strip()) or EN_RECIPIENT_CONTINUATION.fullmatch(message.strip())


from .conversation_turn_state import _turn_language


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", value):
        raise DomainError("CHAT_CLIENT_REQUEST_ID_INVALID", "请提供1至120位请求标识", 422)
    return value


def owned_thread(session, principal, thread_id, lock=False):
    require_banking_actor(session, principal)
    query = select(ChatThread).where(ChatThread.id == thread_id, ChatThread.sandbox_id == principal.sandbox_id,
                                    ChatThread.user_id == principal.user_id)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise DomainError("CHAT_CONVERSATION_NOT_FOUND", "当前身份不能访问该会话", 404)
    return row


def create_conversation(session, principal, title=None, client_request_id=None):
    require_banking_actor(session, principal)
    raw_title = (title or "与圳行Agent的对话").strip()
    request_hash = canonical_hash({"title": raw_title})
    title = redact_advice_input(raw_title)[:80]
    if client_request_id:
        _key(client_request_id)
        previous = session.scalar(select(ChatThread).where(ChatThread.sandbox_id == principal.sandbox_id,
            ChatThread.user_id == principal.user_id, ChatThread.client_request_id == client_request_id))
        if previous:
            if previous.request_hash != request_hash:
                raise DomainError("CHAT_IDEMPOTENCY_CONFLICT", "原请求标识用于不同会话内容", 409)
            return previous
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    try:
        with session.begin_nested():
            row = ChatThread(sandbox_id=principal.sandbox_id, user_id=principal.user_id, title=title,
                             client_request_id=client_request_id, request_hash=request_hash)
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        existing = session.scalar(select(ChatThread).where(ChatThread.sandbox_id == principal.sandbox_id,
            ChatThread.user_id == principal.user_id, ChatThread.client_request_id == client_request_id)) if client_request_id else None
        if existing is None:
            raise DomainError("CHAT_ACCEPTANCE_RETRY", "受理发生并发冲突，请使用原请求标识重试", 409) from None
        if existing.request_hash != request_hash:
            raise DomainError("CHAT_IDEMPOTENCY_CONFLICT", "原请求标识用于不同会话内容", 409) from None
        return existing


def _linked_task_ids(session, thread):
    linked = set(session.scalars(select(ChatTurn.task_id).where(ChatTurn.thread_id == thread.id,
        ChatTurn.task_id.is_not(None))))
    if thread.current_task_id:
        linked.add(thread.current_task_id)
    return linked


def _pending_subject_stub_proof(session, principal, thread, task, latest_message):
    """Read/empty stub or exact legacy duplicate; never erase a bank intent."""
    from .task_dialogue_context import task_context
    from .runtime_models import PlanSnapshot
    from .models import Confirmation
    from .owned_user_dialogue import _same_pending_restatement
    context = task_context(session, principal, task)
    if not context or task.state != "NEED_CLARIFICATION" or context.get("completed_write_present") or context.get("possible_write_effect"):
        return None
    source = session.scalar(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
        ChatTurn.task_id == task.id, ChatTurn.user_id == principal.user_id,
        ChatTurn.sandbox_id == principal.sandbox_id, ChatTurn.message == task.message,
        ChatTurn.status == "ANSWERED").order_by(ChatTurn.sequence.desc()).limit(1))
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    if not source or source.context_policy != "AUTO" or source.mode not in {"NEW_TASK", "REVISE", "CLARIFY"} \
            or source.error_code or source.reviewed_evidence_refs_json \
            or source.diagnostics_json.get("input_redacted") or source.diagnostics_json.get("actor_role_at_submission") != role:
        return None
    operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id)))
    child_ids = [op.child_task_id for op in operations if op.child_task_id]
    if any(op.status in {"SUCCEEDED", "AUTHORIZED", "RUNNING", "UNKNOWN"} and op.action_kind not in READ_KINDS
            or op.child_task_id and (child := session.get(Task, op.child_task_id))
            and child.state in {"SUCCEEDED", "EXECUTING", "AUTHORIZED", "UNKNOWN"} and op.action_kind not in READ_KINDS
            for op in operations) or session.scalar(select(Confirmation.id).where(
                Confirmation.task_id.in_([task.id, *child_ids]),
                (Confirmation.consumed_at.is_not(None)) | (Confirmation.status == "CONSUMED")).limit(1)):
        return None
    raw = rule_proposal(source.message).actions
    if any(kind not in READ_KINDS for kind in context.get("action_kinds", [])) or any(action.kind not in READ_KINDS for action in raw):
        # Both old user sentences must express the same exact recipient and
        # amount. A mere same-kind or same-payee missing-amount request cannot
        # be dismissed as a duplicate; neither can an explicit new request.
        if len(raw) != 1 or not raw[0].slots.amount_minor or not _same_pending_restatement(
                [source.message], raw, latest_message):
            return None
    record = session.get(RuntimeRecord, task.id)
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record and record.plan_version else None
    return {"task_id": task.id, "task_version": task.version, "message_hash": canonical_hash({"message": task.message}),
        "source_turn_id": source.id, "source_hash": canonical_hash({"message": source.message}),
        "source_role": role, "snapshot_id": snapshot.id if snapshot else None,
        "snapshot_hash": canonical_hash(snapshot.proposal_json) if snapshot else None}


def validate_pending_subject_selection(session, parent, metadata):
    """A ignored history stub must still be unchanged at planning/confirmation."""
    proof = metadata.get("pending_subject_selection")
    if not proof:
        return
    thread = session.get(ChatThread, proof.get("conversation_id"))
    from .services import Principal
    owner = Principal(parent.user_id, parent.sandbox_id)
    if not thread or (thread.user_id, thread.sandbox_id) != (parent.user_id, parent.sandbox_id) \
            or not isinstance(proof.get("ignored_stubs"), list) or not 1 <= len(proof["ignored_stubs"]) <= 32:
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "原会话需求来源已变化，请重新核对；未执行业务。", 409)
    for expected in proof["ignored_stubs"]:
        task = session.get(Task, expected.get("task_id"))
        if not task or (task.user_id, task.sandbox_id) != (parent.user_id, parent.sandbox_id) \
                or _pending_subject_stub_proof(session, owner, thread, task, proof.get("subject_message", "")) != expected:
            raise DomainError("CONVERSATION_SCOPE_CHANGED", "原会话需求来源已变化，请重新核对；未执行业务。", 409)


def _task_context(session, principal, thread, turn):
    if turn.context_policy == "INDEPENDENT":
        return None, False
    linked = _linked_task_ids(session, thread)
    if turn.requested_task_id:
        if turn.requested_task_id not in linked:
            raise DomainError("CHAT_TASK_NOT_LINKED", "任务不属于当前会话，请在原会话继续", 409)
        return owned_task(session, principal, turn.requested_task_id), False
    tasks = [owned_task(session, principal, identifier) for identifier in linked]
    tasks = [task for task in tasks if _task_epoch_current(session,principal,task)]
    active = [task for task in tasks if task.state not in TERMINAL]
    if len(active) > 1:
        # Old read/empty clarification stubs are not competing payment goals.
        # Select a latest subject only from its owned, same-role user receipt;
        # two actual pending write requests must still be explicitly selected.
        latest = next((item for item in active if item.id == thread.current_task_id), None)
        recent = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
            ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
            ChatTurn.sequence < turn.sequence, ChatTurn.status == "ANSWERED")
            .order_by(ChatTurn.sequence.desc()).limit(12)))
        previous = None
        for row in recent:
            if row.mode in {"DISCOVERY", "GREETING", "FEEDBACK"} and row.context_policy == "AUTO" \
                    and row.diagnostics_json.get("preserve_goal_context") is True and not row.error_code \
                    and not row.reviewed_evidence_refs_json and not row.diagnostics_json.get("input_redacted") \
                    and row.diagnostics_json.get("actor_role_at_submission") == actor_role(session, principal.sandbox_id, principal.user_id):
                continue  # A certified non-operational aside is not a new goal.
            if row.mode == "NEED_CONTEXT" and not row.task_id and row.context_policy == "AUTO" \
                    and not row.error_code and not row.reviewed_evidence_refs_json \
                    and not row.diagnostics_json.get("input_redacted") \
                    and row.diagnostics_json.get("actor_role_at_submission") == actor_role(session, principal.sandbox_id, principal.user_id) \
                    and (_amount_like_fragment(row.message) or _continuation_money(row.message, "CNY")):
                continue  # A server-blocked amount reply did not change subject.
            previous = row
            break
        if latest and previous and previous.task_id == latest.id and previous.context_policy == "AUTO" \
                and previous.mode in {"NEW_TASK", "REVISE", "CLARIFY", "STATUS"} \
                and not previous.error_code and not previous.reviewed_evidence_refs_json \
                and not (previous.diagnostics_json or {}).get("input_redacted") \
                and (previous.diagnostics_json or {}).get("actor_role_at_submission") == actor_role(session, principal.sandbox_id, principal.user_id):
            latest_record = session.get(RuntimeRecord, latest.id)
            retained = (latest_record.metadata_json or {}).get("pending_subject_selection") if latest_record else None
            retained_ids = {stub.get("task_id") for stub in retained.get("ignored_stubs", [])} if isinstance(retained, dict) else set()
            if retained:
                validate_pending_subject_selection(session, latest, latest_record.metadata_json)
            subject = retained.get("subject_message", latest.message) if retained else latest.message
            stubs = [_pending_subject_stub_proof(session, principal, thread, item,
                subject if item.id in retained_ids else latest.message)
                for item in active if item.id != latest.id]
            if len(stubs) <= 32 and all(stubs):
                turn.diagnostics_json = {**turn.diagnostics_json, "pending_subject_selection": {
                    "conversation_id": thread.id, "subject_message": subject, "ignored_stubs": stubs}}
                return latest, False
        return None, True
    if len(active) == 1:
        return active[0], False
    current=owned_task(session,principal,thread.current_task_id) if thread.current_task_id else None
    return (current,False) if current and _task_epoch_current(session,principal,current) else (None,False)


from .conversation_turn_state import _task_epoch_current


from .conversation_turn_state import _attachment_sources


def submit_turn(session, principal, thread_id, *, message, client_request_id, current_task_id=None,
                expected_task_version=None, context_policy="AUTO", reviewed_evidence_refs=None, reply_language=None,
                explicit_task_selection=False, recipient_choice=None):
    from .runtime import trusted_family_context
    family_context = trusted_family_context(session, principal)
    if context_policy not in {"AUTO", "INDEPENDENT"}:
        raise DomainError("CHAT_CONTEXT_POLICY_INVALID", "会话上下文策略无效", 422)
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
        raise DomainError("CHAT_MESSAGE_INVALID", "请输入1至2000字的需求", 422)
    thread = owned_thread(session, principal, thread_id, lock=True)
    if thread.status != "ACTIVE":
        raise DomainError("CHAT_CONVERSATION_CLOSED", "该会话已关闭，请新建会话", 409)
    _key(client_request_id)
    refs = reviewed_evidence_refs or []
    # Authentication material never enters chat history or a cloud prompt.
    raw_message = message.strip()
    cleaned = CREDENTIAL_VALUE_PATTERN.sub(lambda match: match.group(1) + "[REDACTED]", redact_advice_input(raw_message))
    cleaned = re.sub(r"(\b(?:verification\s+code|passcode|credential|secret)\s*(?:is|:|=)?\s*)[A-Za-z0-9_-]{4,256}\b", lambda match: match.group(1)+"[REDACTED]", cleaned, flags=re.I)
    request_values = {"message": raw_message, "task": current_task_id, "version": expected_task_version,
                      "context_policy": context_policy, "reviewed_evidence_refs": refs}
    if recipient_choice is not None:
        request_values['recipient_choice'] = recipient_choice
    if type(explicit_task_selection) is not bool or explicit_task_selection and (
            not current_task_id or context_policy != 'AUTO' or type(expected_task_version) is not int or expected_task_version < 1):
        raise DomainError('CHAT_SELECTION_INVALID', '请重新选择要继续的事项。', 422)
    if explicit_task_selection:
        request_values['explicit_task_selection'] = True
    if reply_language is not None:
        if reply_language not in {"en", "zh-CN"}:
            raise DomainError("CHAT_REPLY_LANGUAGE_INVALID", "Reply language must be en or zh-CN", 422)
        request_values["reply_language"] = reply_language
    request_hash = canonical_hash(request_values)
    old = session.scalar(select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.client_request_id == client_request_id))
    if old:
        if old.request_hash != request_hash:
            raise DomainError("CHAT_IDEMPOTENCY_CONFLICT", "原请求标识用于不同内容，请保留原请求或创建新请求", 409)
        return old
    recipient_selection = None
    if recipient_choice is not None:
        if context_policy != 'AUTO' or not explicit_task_selection or refs:
            raise DomainError('RECIPIENT_CHOICE_INVALID', '请在当前转账对话中重新选择收款账户。', 409)
        from .recipient_choices import accept_selection
        recipient_selection = accept_selection(session, principal, thread, recipient_choice,
            raw_message, current_task_id, expected_task_version)
    if current_task_id:
        owned_task(session, principal, current_task_id)
        if current_task_id not in _linked_task_ids(session, thread):
            raise DomainError("CHAT_TASK_NOT_LINKED", "只能继续本会话关联任务", 409)
    _attachment_sources(session, principal, refs)  # scope and version proof, not intent
    resolved_language = detect_reply_language(raw_message, reply_language)
    if reply_language is None and not re.search(r"[\u4e00-\u9fff]|\b[A-Za-z]{2,}\b", re.sub(r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}", "", raw_message)):
        previous = session.scalar(select(ChatTurn).where(ChatTurn.thread_id == thread.id).order_by(ChatTurn.sequence.desc()).limit(1))
        if previous:
            resolved_language = _turn_language(previous)  # Presentation only, never intent or authorization.
    row = ChatTurn(thread_id=thread.id, sandbox_id=principal.sandbox_id, user_id=principal.user_id,
        sequence=thread.next_sequence, client_request_id=client_request_id, request_hash=request_hash, message=cleaned,
        requested_task_id=current_task_id, expected_task_version=expected_task_version, context_policy=context_policy,
        reviewed_evidence_refs_json=refs, diagnostics_json={"authority": "SERVER_CONVERSATION_POLICY",
            "actor_role_at_submission": actor_role(session, principal.sandbox_id, principal.user_id),
            **({"explicit_task_selection": True} if explicit_task_selection else {}),
            **({"family_context_origin": family_context} if family_context else {}),
            "input_redacted": cleaned != raw_message, "credential_only": _credential_only(cleaned, cleaned != raw_message),
            "attachments_authorize_writes": False, "reply_language": resolved_language})
    from .dialogue_state import dialogue_actor_scope
    actor_scope, actor_scope_hash = dialogue_actor_scope(session, principal)
    row.diagnostics_json = {**row.diagnostics_json, "dialogue_actor_scope_hash": actor_scope_hash,
        "actor_login_id": actor_scope["login_account_id"], "actor_login_version": actor_scope["login_account_version"]}
    if recipient_selection:
        from .recipient_choices import SELECTION
        row.diagnostics_json = {**row.diagnostics_json, SELECTION: recipient_selection}
    session.add(row)
    thread.next_sequence += 1
    thread.version += 1
    thread.updated_at = utcnow()
    session.flush()
    return row


def thread_view(row):
    return {"conversation_id": row.id, "title": row.title, "status": row.status, "version": row.version,
            "current_task_id": row.current_task_id, "created_at": as_utc(row.created_at).isoformat(),
            "updated_at": as_utc(row.updated_at).isoformat()}


def _reply_snapshot(session, row, role, authorized):
    if not authorized:
        return None, "UNAUTHORIZED"
    if not row.task_id:
        return None, "NOT_APPLICABLE"
    task = session.get(Task, row.task_id)
    if not task or (task.user_id, task.sandbox_id) != (row.user_id, row.sandbox_id):
        return None, "UNAUTHORIZED"
    record = session.get(RuntimeRecord, task.id)
    stored = record.metadata_json.get("conversation_reply_snapshots") if record else None
    if not isinstance(stored, dict):
        return None, "NOT_RETAINED"
    if stored.get("schema_version") != "conversation-reply-snapshots-v1" or stored.get("limit") != 128:
        return None, "INVALID"
    items = stored.get("items")
    if not isinstance(items, list) or len(items) > 128:
        return None, "INVALID"
    # Preserve the original clarification, but a retained terminal result for
    # this same request supersedes its old "waiting for confirmation" wording.
    # Other turns' replies and confirmation authority are never substituted.
    terminal = [item for item in reversed(items) if isinstance(item, dict)
        and item.get("turn_id") == row.id and item.get("state") == task.state
        and task.state in {"SUCCEEDED", "UNKNOWN", "FAILED", "PARTIAL"}]
    for snapshot in terminal + [item for item in items if item not in terminal]:
        if not isinstance(snapshot, dict) or snapshot.get("turn_id") != row.id:
            continue
        if (snapshot.get("conversation_id") != row.thread_id or snapshot.get("task_id") != row.task_id
                or snapshot.get("schema_version") != "conversation-reply-snapshot-v1"
                or snapshot.get("authority") != "HISTORICAL_NARRATION_NOT_CURRENT_STATUS_OR_AUTHORIZATION"
                or type(snapshot.get("task_version")) is not int or not 1 <= snapshot["task_version"] <= task.version
                or snapshot.get("reply_language") != _turn_language(row)
                or not isinstance(snapshot.get("state"), str) or snapshot["state"] not in {"NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "SUCCEEDED", "PARTIAL", "FAILED", "UNKNOWN", "WAITING"}
                or not isinstance(snapshot.get("fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot["fingerprint"])
                or not isinstance(snapshot.get("observed_at"), str)):
            return None, "INVALID"
        if snapshot.get("actor_role") != role:
            return None, "ROLE_CHANGED"
        from .extensions import validate_family_origin
        from .runtime import validate_task_trace_access
        try:
            validate_family_origin(session, task)
            # Same current access gate as the full trace, including delegated
            # READ_BALANCE; no financial display is needed for narration.
            validate_task_trace_access(session, Principal(row.user_id, row.sandbox_id), task.id)
        except DomainError:
            return None, "UNAUTHORIZED"
        if not isinstance(snapshot.get("intro"), str) or not 1 <= len(snapshot["intro"]) <= 5000 or not isinstance(snapshot.get("text"), str) or not 1 <= len(snapshot["text"]) <= 10000:
            return None, "INVALID"
        if snapshot.get("detail") is not None and (not isinstance(snapshot["detail"], str) or len(snapshot["detail"]) > 5000):
            return None, "INVALID"
        if snapshot["text"] != snapshot["intro"] + ("\n\n" + snapshot["detail"] if snapshot.get("detail") else ""):
            return None, "INVALID"
        if any(redact_advice_input(snapshot.get(key) or "") != (snapshot.get(key) or "") for key in ("intro", "detail", "text")):
            return None, "INVALID"
        from datetime import datetime
        try:
            if not datetime.fromisoformat(snapshot["observed_at"]).tzinfo:
                return None, "INVALID"
        except (ValueError, TypeError):
            return None, "INVALID"
        return {key: snapshot.get(key) for key in ("schema_version", "conversation_id", "turn_id", "task_id", "task_version", "state", "reply_language", "actor_role", "fingerprint", "intro", "detail", "text", "observed_at", "authority")}, "RETAINED"
    unretained = stored.get("last_unretained") or {}
    if isinstance(unretained, dict) and unretained.get("turn_id") == row.id and unretained.get("conversation_id") == row.thread_id and stored.get("last_unretained_reason") == "INVALID_REPLY_PAYLOAD":
        return None, "INVALID_NOT_RETAINED"
    if stored.get("capacity_reached") is True and isinstance(unretained, dict) and unretained.get("turn_id") == row.id and unretained.get("conversation_id") == row.thread_id:
        return None, "CAPACITY_REACHED"
    return None, "NOT_RETAINED"


def turn_view(session, principal, row, *, include_diagnostics=True):
    owned_thread(session, principal, row.thread_id)
    authorized = sources_still_authorized(session, principal, row.sources_json)
    # A newly issued helper token must not revive private answers produced by
    # an expired/revoked context. Keep history rows, hide the stale projection.
    try:
        _turn_principal(session, row)
    except DomainError:
        authorized = False
    from .knowledge_runtime import knowledge_sources_current
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    authorized = authorized and knowledge_sources_current(row.sources_json, role)
    from .capability_catalog import suggestion_catalog
    current_suggestions = suggestion_catalog(role, _turn_language(row))
    suggestion_meta = row.response_meta_json or {}
    display_answer = row.answer
    if authorized:
        from .semantic_recovery import display_recovery
        display_answer, suggestion_meta = display_recovery(row, english=_turn_language(row) == 'en')
    public_suggestions = []
    if authorized and suggestion_meta.get("suggestion_role") == role and suggestion_meta.get("suggestion_source") == "CUSTOMER_CAPABILITY_CATALOG":
        for previous in row.suggestions_json or []:
            current = current_suggestions.get(previous.get("id")) if isinstance(previous, dict) else None
            if current and current == previous:
                public_suggestions.append(current)
    link = None
    if row.task_id:
        task = owned_task(session, principal, row.task_id)
        link = {"task_id": task.id, "state": task.state, "version": task.version,
                "relationship": row.task_relationship or "CURRENT"}
    reply_snapshot, reply_snapshot_status = _reply_snapshot(session, row, role, authorized)
    from .conversation_status_snapshot import read_status_snapshot
    status_snapshot, status_snapshot_status = read_status_snapshot(session, principal, row, authorized=authorized)
    return {"turn_id": row.id, "conversation_id": row.thread_id, "sequence": row.sequence,
        "message": row.message, "status": row.status, "mode": row.mode, "reply_language": _turn_language(row),
        "answer": display_answer if authorized else ("The previous authorization has expired. This historical explanation is hidden." if _turn_language(row) == "en" else "此前授权资料的读取范围已失效，历史解释内容已隐藏。"),
        "sources": public_sources(row.sources_json) if authorized else [], "task_link": link,
        "suggestions": public_suggestions, "response_meta": suggestion_meta if authorized else {},
        "field_sources": row.field_sources_json,
        "diagnostics": {key: value for key, value in (row.diagnostics_json or {}).items()
            if key not in {"family_context_origin", "semantic_shadow_observation", "verified_status_snapshot", "semantic_source_bundle_v2", "recipient_choice_set_v1", "recipient_choice_selection_v1"}}
            if include_diagnostics else {},
        "status_snapshot": status_snapshot, "status_snapshot_status": status_snapshot_status,
        "reply_snapshot": reply_snapshot, "reply_snapshot_status": reply_snapshot_status,
        "reviewed_evidence_refs": row.reviewed_evidence_refs_json,
        "error_code": row.error_code, "created_at": as_utc(row.created_at).isoformat(),
        "updated_at": as_utc(row.updated_at).isoformat()}


def conversation_view(session, principal, thread_id, before_sequence=None, *, customer_projection=False):
    from .conversation_history_projection import build_history_projection
    projection = build_history_projection(session, principal, thread_id, before_sequence, customer=customer_projection)
    return projection if customer_projection else projection.view


def close_conversation(session, principal, thread_id):
    thread = owned_thread(session, principal, thread_id, lock=True)
    if thread.status != "CLOSED":
        thread.status, thread.version = "CLOSED", thread.version + 1
        for row in session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.status.in_(["QUEUED", "LEASED"]))):
            row.status, row.error_code, row.completed_at = "FAILED", "CHAT_CONVERSATION_CLOSED", utcnow()
    return thread


def _continuation_money(message, inherited_currency):
    """One exact, source-closed monetary utterance, parsed in integer minor units."""
    english = english_continuation_money(message, inherited_currency)
    if english is not None and _intent_text(message) == message:
        return english
    shorthand = re.fullmatch(
        r"(?:再)?(?:金额(?:改成|改为|调整为)?|改成|改为|改到|换成|给(?:他|她|TA|那个人)转)?\s*"
        r"(?P<body>(?:(?:人民币|CNY|RMB|美元|美金|USD)\s*)?"
        r"\d+(?:\.\d{1,2})?\s*w\s*(?:人民币|CNY|RMB|元|美元|美金|USD)?)", message.strip(), re.I)
    if shorthand and _intent_text(message) == message:
        body = shorthand.group("body")
        usd = bool(re.search(r"美元|美金|USD", body, re.I))
        cny = bool(re.search(r"人民币|CNY|RMB|(?<!美)元", body, re.I))
        if usd and cny or not (usd or cny) and inherited_currency not in {"CNY", "USD"}:
            return None
        from .intelligence import amounts_in_text, usd_amounts_in_text
        # A source-closed numeric field reply inherits only the currency
        # already proved for its pending task. Full new requests still use
        # their own independent currency grounding, never this helper.
        currency = "USD" if usd else "CNY" if cny else inherited_currency
        values = (usd_amounts_in_text if usd else amounts_in_text)(body)
        if len(values) == 1:
            return next(iter(values)), currency, currency if usd or cny else None
        return None
    match = CONTINUATION_MONEY_PATTERN.fullmatch(message.strip())
    if not match or _intent_text(message) != message:
        return None
    body = match.group("body")
    prefix, unit, fraction = match.group("prefix"), match.group("unit"), match.group("fraction") or ""
    if match.group('colloquial_jiao') is not None and unit not in {'元','块','块钱'}:
        return None
    explicit_usd = bool(re.search(r"美元|美金|USD", body, re.I))
    explicit_cny = bool(re.search(r"人民币|CNY|RMB|(?<!美)元|块|角|毛|分", body, re.I))
    if explicit_usd and explicit_cny:
        return None
    if fraction and (not unit or not re.fullmatch(r"块钱|块|元|人民币|CNY", unit, re.I)
            or re.search(r"[.点]", match.group("number"))):
        return None  # No unlabelled/double-decimal fractions or USD 角/分 guesses.
    explicit = "USD" if explicit_usd else "CNY" if explicit_cny else None
    currency = explicit or inherited_currency
    if currency not in {"CNY", "USD"}:
        return None
    # A monetary quantity may repeat its denomination after the unit, e.g.
    # "120块人民币". This is field grammar, not an action/intent classifier.
    # Currency conflict was checked on the complete unmodified source above.
    # Drop only the separately parsed redundant denomination for scalar math;
    # the user's original span and hashes stay unchanged in source proofs.
    normalized = body
    # Denomination can follow the complete yuan/jiao/fen quantity, too.
    # Only normalize the scalar copy; the original source span is unchanged.
    for name in ('trailing_denomination','denomination'):
        if match.group(name):
            start,end=match.span(name)
            start-=match.start('body');end-=match.start('body')
            normalized=normalized[:start]+normalized[end:]
    if match.group('zero_fen'):
        # 零 before an explicit fen digit occupies the zero-jiao place.
        # 一块零三分 = 103 fen, never 130 fen or an imprecise fractional yuan.
        normalized=re.sub(r'([元块]|块钱)\s*[零〇0]\s*([1-9一二两三四五六七八九])\s*分',r'\1\2分',normalized)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub("CNY", "人民币", normalized, flags=re.I)
    normalized = re.sub("RMB", "人民币", normalized, flags=re.I)
    normalized = re.sub("USD", "美元", normalized, flags=re.I)
    if match.group('colloquial_jiao') is not None:
        # A single digit after an integer 元/块 is the spoken jiao place.
        # Only the scalar parser is normalized; original spans/hashes remain
        # untouched. Explicit 分 keeps its existing distinct 1-fen meaning.
        normalized += '角'
    if not prefix and not unit:
        normalized += "美元" if currency == "USD" else "元"
    from .intelligence import amounts_in_text, usd_amounts_in_text
    exact = (usd_amounts_in_text if currency == "USD" else amounts_in_text)(normalized)
    if len(exact) != 1:
        return None
    return next(iter(exact)), currency, explicit


def _amount_like_fragment(message):
    if english_amount_fragment(message):
        return True
    # Classify unsupported monetary fragments as clarifications, not unrelated
    # new tasks. This recognition never supplies a number or currency.
    text = re.sub(r"^(?:再)?(?:金额(?:改成|改为|调整为)?|改成|改为|改到|换成|给(?:他|她|TA|那个人)转)\s*", "", message.strip(), count=1, flags=re.I)
    return bool(AMOUNT_LIKE_PATTERN.fullmatch(text) or text in {"全部余额", "所有余额", "剩余余额"})


def _short_continuation(task, message, turn_id):
    """Return full request + source bindings only from original user wording."""
    text = message.strip()
    original = _intent_text(task.message)
    actions = [a for a in rule_proposal(task.message).actions if a.kind not in READ_KINDS]
    eligible = {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER", "WEALTH_BUY", "WEALTH_REDEEM",
                "CARD_SET_LIMIT", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "FAMILY_PROPOSE"}
    candidates = [a for a in actions if a.kind in eligible]
    if len(candidates) != 1 or len(actions) != 1:
        return None
    action = candidates[0]
    values = action.slots.model_dump()
    target_field = {"TRANSFER": "payee_name", "TRANSFER_SCHEDULE": "payee_name", "FX_TRANSFER": "payee_name",
        "WEALTH_BUY": "product_name", "WEALTH_REDEEM": "product_name", "CARD_SET_LIMIT": "card_number" if values["card_number"] else "card_last4",
        "GOAL_CREATE": "goal_name", "GOAL_RESERVE": "goal_name", "GOAL_RELEASE": "goal_name",
        "FAMILY_PROPOSE": "payee_name"}.get(action.kind)
    if not target_field or not values.get(target_field):
        return None
    old_currency = values.get("currency") or "CNY"
    # Repeating the already pending operation verb plus its missing amount is
    # still a field reply ("赎回100元"), not permission to forget its product.
    # Only the same wealth operation and a source-closed exact money fragment
    # qualify; product changes, multiple commands and units do not enter here.
    if action.kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        verbs = r"(?:赎回|卖出|redeem)" if action.kind == "WEALTH_REDEEM" else r"(?:申购|买入|购买|buy|purchase)"
        repeated = re.fullmatch(verbs + r"\s*(.+)", text, re.I)
        if repeated and _continuation_money(repeated.group(1), old_currency):
            current = rule_proposal(text).actions
            if len(current) == 1 and current[0].kind == action.kind and not current[0].depends_on and set(current[0].slots.model_dump(exclude_none=True)) <= {"amount_minor", "currency"}:
                text = repeated.group(1)
    # A currency-only phrase cannot distinguish amount/source/settlement fields.
    if re.fullmatch(r"(?:还是|改成|用)?(?:人民币|美元|美金|CNY|USD)", text, re.I):
        return None
    money = _continuation_money(text, old_currency)
    if not money:
        return None
    minor, currency, explicit_currency = money
    changed_currency = currency != old_currency
    if changed_currency and (action.kind not in {"TRANSFER", "FX_TRANSFER"} or
            values.get("source_currency") or values.get("target_currency")):
        # Only a simple transfer's denomination can be rewritten. Do not erase
        # a scheduled action or explicit funding/settlement route on a change.
        return None
    expected_kind = ("FX_TRANSFER" if currency == "USD" else "TRANSFER") if changed_currency else action.kind
    from .intelligence import amounts_in_text, usd_amounts_in_text
    spans = sorted(set((_usd_money_evidence if old_currency == "USD" else _cny_money_evidence)(original)))
    if not spans and old_currency == "CNY" and values.get("amount_minor") is not None:
        # Existing precise CNY syntax also permits 转100 with no unit. Match
        # that source span, rather than appending a second contradictory sum.
        for match in re.finditer(r"(?:转[账帐]|转|汇|打款)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d.])(?=$|[，,。；;\s])", original):
            parsed = amounts_in_text("转" + match.group(1))
            if parsed == {values["amount_minor"]}:
                spans.append((match.start(1), match.end(1), values["amount_minor"]))
    new_money = (f"{currency} {Decimal(minor)/100}" if detect_reply_language(task.message) == "en" else f"{Decimal(minor)/100}{'美元' if currency == 'USD' else '人民币'}")
    if len(spans) > 1:
        return None
    if spans:
        start, end, _old = spans[0]
        if old_currency == "CNY":
            fraction = re.match(r"(?:(?:[\d零〇一二两三四五六七八九])(?:角|毛))?(?:(?:[\d零〇一二两三四五六七八九])分)?", original[end:])
            end += fraction.end() if fraction else 0
            if re.match(r"\s+[\d零〇一二两三四五六七八九]\s*(?:角|毛|分)", original[end:]):
                return None  # Do not retain an unsupported old detached fractional sum.
        resolved = task.message[:start] + new_money + task.message[end:]
    else:
        if values.get("amount_minor") is not None or values.get("target_minor") is not None:
            return None  # Never preserve an old sum that could not be located.
        resolved = task.message + "，金额" + new_money
    field = "target_minor" if action.kind == "GOAL_CREATE" else "amount_minor"
    bindings = [{"field": target_field, "value": values[target_field], "task_id": task.id,
                 "sources": [{"source": "USER_CONTEXT", "span": task.message}]},
                {"field": field, "value": minor, "task_id": task.id,
                 "sources": [{"source": "USER_CURRENT", "turn_id": turn_id, "span": message}]},
                {"field": "currency", "value": currency, "task_id": task.id,
                 "sources": [{"source": "USER_CURRENT" if explicit_currency else "USER_CONTEXT",
                              "span": message if explicit_currency else task.message,
                              **({"turn_id": turn_id} if explicit_currency else {}),
                              "basis": "EXPLICIT_USER_DENOMINATION" if explicit_currency or values.get("currency") or re.search(r"人民币|元", task.message) else "EXISTING_CNY_CAPABILITY_REQUEST"}]}]
    rewritten = [candidate for candidate in rule_proposal(resolved).actions if candidate.kind not in READ_KINDS]
    if len(rewritten) != 1 or rewritten[0].kind != expected_kind or getattr(rewritten[0].slots, field) != minor \
            or getattr(rewritten[0].slots, target_field) != values[target_field]:
        return None
    preserved = {key: value for key, value in values.items() if value is not None and key not in {field, "currency"}}
    if any(rewritten[0].slots.model_dump().get(key) != value for key, value in preserved.items()):
        return None  # Rewriting money must not silently drop a card, alias or date.
    expected = rewritten[0].slots.model_dump(exclude_none=True)
    contract = {"version": "chat-continuation-v1", "expected_action_kind": expected_kind,
                "expected_slots": expected, "expected_write_count": 1,
                "origin_task_version": task.version,
                "source_message_hash": canonical_hash({"message": task.message})}
    return resolved, bindings, contract


def _short_recipient_continuation(task, message, turn_id):
    """Bind an explicit single recipient, retaining all other user evidence.

    This is intent recomposition only. Identity/card/alias resolution belongs to
    the new bank plan; neither an object phrase nor this contract authorizes it.
    """
    match = _recipient_match(message)
    if not match or _intent_text(message) != message or re.search(
            r"如果|假设|假如|可能|也许|或者|或是|以及|不要|不想|先别|引用|示例|例如|他说|她说|对方说|截图|备注|文档|房东的|收款人的|\b(?:if|assuming|maybe|possibly|or|and|not|never|example|quote|their|his|her)\b", message, re.I):
        return None
    recipient = match.group("recipient")
    if re.fullmatch(r"人民币|美元|美金|CNY|USD", recipient, re.I) or re.fullmatch(
            r"(?:人民币|美元|美金|CNY|USD)?[零〇一二两三四五六七八九十百千万亿点]+(?:元|人民币|美元|美金|CNY|USD)?", recipient, re.I):
        return None  # Chinese monetary corrections retain the existing amount path.
    if list(_cny_money_evidence(recipient)) or list(_usd_money_evidence(recipient)):
        return None
    if re.search(r"^(?:我|他|她|它|这|那|某)|(?:的|之|不变)$|[或和及与、]", recipient) or recipient.endswith(("_", ".", "-")):
        return None
    if recipient.lower() in {"him", "her", "them", "someone", "anyone", "dollars", "account", "landlord", "that", "this"}:
        return None
    if recipient.lower().startswith(("zhenhang", "xiaozhao")) and not re.fullmatch(r"(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}", recipient):
        return None
    original_actions = rule_proposal(task.message).actions
    actions = [action for action in original_actions if action.kind not in READ_KINDS]
    if len(actions) != 1 or actions[0].kind not in {"TRANSFER", "FX_TRANSFER"}:
        return None  # No implicit selection of a scheduled/multiple write.
    action = actions[0]
    values = action.slots.model_dump(exclude_none=True)
    if values.get("recipient_alias") or values.get("replace_alias"):
        return None  # Changing a recipient is not permission to create/rebind a role.
    intent = _intent_text(task.message)
    old_recipient = values.get("payee_name")
    if old_recipient:
        spans = list(re.finditer(re.escape(old_recipient), intent))
        if len(spans) != 1:
            return None
        source = spans[0]
        resolved = task.message[:source.start()] + recipient + task.message[source.end():]
    else:
        verbs = list(re.finditer(r"(?:转[账帐]|汇款|打款)(?=\s*(?:人民币|美元|美金|USD|CNY|[\d零〇一二两三四五六七八九十百千万亿]|[，,。；;]|$))", intent, re.I))
        if len(verbs) == 1 and _allowed_evidence(action.kind, intent):
            start = verbs[0].start()
            resolved = task.message[:start] + "给" + recipient + task.message[start:]
        else:
            eligible = [clause for clause in _clauses(task.message) if _allowed_evidence(action.kind, clause)
                        and re.search(r"\b(?:transfer|send|remit|pay)\b", clause, re.I)]
            if len(eligible) != 1 or intent.count(eligible[0]) != 1:
                return None
            clause = eligible[0]
            stop = intent.index(clause)+len(clause.rstrip("."))
            resolved = task.message[:stop]+" to "+recipient+task.message[stop:]
    rewritten_actions = rule_proposal(resolved).actions
    rewritten = [item for item in rewritten_actions if item.kind not in READ_KINDS]
    if len(rewritten) != 1 or rewritten[0].kind != action.kind:
        return None
    expected = rewritten[0].slots.model_dump(exclude_none=True)
    if expected.get("payee_name") != recipient or any(expected.get(key) != value for key, value in values.items() if key != "payee_name"):
        return None
    # An extra slot after recomposition must not silently become an alias/card
    # instruction merely because the new recipient's spelling has a keyword.
    if set(expected) != set(values) | {"payee_name"}:
        return None
    read_scope = lambda items: sorted([{"kind": item.kind, "slots": item.slots.model_dump(exclude_none=True)}
        for item in items if item.kind in READ_KINDS], key=canonical_hash)
    expected_reads = read_scope(original_actions)
    if read_scope(rewritten_actions) != expected_reads:
        return None  # A recipient spelling must not add/remove a banking query.
    bindings = [{"field": "payee_name", "value": recipient, "task_id": task.id,
                 "sources": [{"source": "USER_CURRENT", "turn_id": turn_id, "span": message}]}]
    bindings += [{"field": key, "value": value, "task_id": task.id,
                  "sources": [{"source": "USER_CONTEXT", "span": task.message}]}
                 for key, value in expected.items() if key != "payee_name"]
    if "currency" not in expected:
        bindings.append({"field": "currency", "value": "CNY", "task_id": task.id,
            "sources": [{"source": "USER_CONTEXT", "span": task.message, "basis": "EXISTING_CNY_CAPABILITY_REQUEST"}]})
    contract = {"version": "chat-target-continuation-v2", "expected_action_kind": action.kind,
        "expected_slots": expected, "expected_write_count": 1, "origin_task_version": task.version,
        "source_message_hash": canonical_hash({"message": task.message}), "changed_field": "payee_name",
        "expected_read_actions": expected_reads}
    return resolved, bindings, contract


def _short_wealth_units_continuation(task, message, turn_id):
    """A missing whole-unit quantity, retaining one pending product only."""
    if _intent_text(message) != message:
        return None
    originals = [action for action in rule_proposal(task.message).actions if action.kind not in READ_KINDS]
    if len(originals) != 1 or originals[0].kind not in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        return None
    action = originals[0]
    values = action.slots.model_dump(exclude_none=True)
    if not values.get("product_name") or values.get("amount_minor") is not None or values.get("units") is not None:
        return None
    verb = r"(?:赎回|卖出|redeem)" if action.kind == "WEALTH_REDEEM" else r"(?:申购|买入|购买|buy|purchase)"
    match = re.fullmatch(r"(?:" + verb + r"\s*)?(?P<count>[0-9零〇一二两三四五六七八九十百千万]+)\s*(?:份|份额|units?|shares?)", message.strip(), re.I)
    if not match:
        return None
    from .intelligence import wealth_units_in_text
    counts = wealth_units_in_text(match["count"] + "份")
    if len(counts) != 1:
        return None
    count = next(iter(counts))
    resolved = task.message + f"，数量{count}份"
    rewritten = [row for row in rule_proposal(resolved).actions if row.kind not in READ_KINDS]
    if len(rewritten) != 1 or rewritten[0].kind != action.kind or rewritten[0].slots.units != count:
        return None
    expected = rewritten[0].slots.model_dump(exclude_none=True)
    if any(expected.get(field) != value for field, value in values.items()) or set(expected) != set(values) | {"units"}:
        return None
    fields = [{"field": field, "value": value, "task_id": task.id, "sources": [{"source": "USER_CONTEXT", "span": task.message}]} for field, value in values.items()]
    fields.append({"field": "units", "value": count, "task_id": task.id,
        "sources": [{"source": "USER_CURRENT", "turn_id": turn_id, "span": message}]})
    contract = {"version": "chat-continuation-v1", "expected_action_kind": action.kind,
        "expected_slots": expected, "expected_write_count": 1, "origin_task_version": task.version,
        "source_message_hash": canonical_hash({"message": task.message})}
    return resolved, fields, contract


def _short_quantity_replacement(task, message, turn_id):
    """Replace an explicitly rejected whole-unit axis with one exact CNY sum.

    It proves the unchanged original user product and the explicitly rejected
    old quantity. It never interprets a bank price, chooses a new product, or
    permits a settled/inflight action to be repeated.
    """
    if _intent_text(message) != message or len(message) > 160:
        return None
    original_actions = rule_proposal(task.message).actions
    if len(original_actions) != 1 or original_actions[0].kind not in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        return None
    action = original_actions[0]
    values = action.slots.model_dump(exclude_none=True)
    if not values.get("product_name") or values.get("units") is None or values.get("amount_minor") is not None or values.get("currency") not in {None, "CNY"}:
        return None
    verb = r"(?:赎回|卖出)" if action.kind == "WEALTH_REDEEM" else r"(?:申购|买入|购买)"
    match = re.fullmatch(r"(?:改成|改为|换成)\s*" + verb + r"\s*(?P<money>[^，,。；;\n]{1,40})\s*[，,]\s*(?:不按|不用|而不是)\s*(?P<units>[0-9零〇一二两三四五六七八九十百千万]+)\s*份(?:了)?[。！!]?", message.strip())
    if not match:
        return None
    money = _continuation_money(match["money"], "CNY")
    from .intelligence import wealth_units_in_text
    rejected = wealth_units_in_text(match["units"] + "份")
    if not money or money[1] != "CNY" or rejected != {values["units"]}:
        return None
    spans = list(re.finditer(r"(?<![\d./])(?:\d+|[零〇一二两三四五六七八九十百千万]+)\s*(?:份(?![额\u4e00-\u9fff])|(?:units?|shares?)\b)", task.message, re.I))
    spans = [span for span in spans if wealth_units_in_text(span.group()) == rejected]
    if len(spans) != 1:
        return None
    span = spans[0]
    new_money = f"{Decimal(money[0])/100:.2f}元"
    resolved = task.message[:span.start()] + new_money + task.message[span.end():]
    rewritten = rule_proposal(resolved).actions
    if len(rewritten) != 1 or rewritten[0].kind != action.kind:
        return None
    expected = rewritten[0].slots.model_dump(exclude_none=True)
    preserved = {key:value for key,value in values.items() if key != "units"}
    if expected.get("amount_minor") != money[0] or expected.get("units") is not None or any(expected.get(key) != value for key,value in preserved.items()) or set(expected) != set(preserved) | {"amount_minor"}:
        return None
    fields = [{"field":field,"value":value,"task_id":task.id,"sources":[{"source":"USER_CONTEXT","span":task.message}]} for field,value in preserved.items()]
    fields.append({"field":"amount_minor","value":money[0],"task_id":task.id,
        "sources":[{"source":"USER_CURRENT","turn_id":turn_id,"span":message}]})
    contract = {"version":"chat-quantity-replacement-v1","expected_action_kind":action.kind,
        "expected_slots":expected,"expected_write_count":1,"origin_task_version":task.version,
        "source_message_hash":canonical_hash({"message":task.message}),"changed_field":"quantity_axis",
        "removed_units":values["units"],"expected_read_actions":[]}
    return resolved, fields, contract


def _quantity_origin_proof(session, principal, thread, turn, task, continuation):
    """Bind the old plan and exact DB-owned user receipts, not model facts."""
    from .runtime_models import PlanSnapshot
    role = actor_role(session, principal.sandbox_id, principal.user_id)
    if role not in {"CUSTOMER", "DEV_TEST", "DEMO_OWNER", "OWNER"} or turn.diagnostics_json.get("actor_role_at_submission") != role:
        return None
    record = session.get(RuntimeRecord, task.id)
    snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
        PlanSnapshot.version == record.plan_version)) if record and record.plan_version else None
    if not snapshot:
        return None
    from .intelligence import IntentProposal, wealth_units_in_text
    actions = IntentProposal.model_validate(snapshot.proposal_json).actions
    if len(actions) != 1 or actions[0].kind not in {"WEALTH_BUY", "WEALTH_REDEEM"}:
        return None
    resolved, fields, contract = continuation
    old = actions[0].slots.model_dump(exclude_none=True)
    expected = contract["expected_slots"]
    if old.get("product_name") != expected.get("product_name") or old.get("units") != contract["removed_units"] or old.get("amount_minor") is not None:
        return None
    rows = list(session.scalars(select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.task_id == task.id,
        ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
        ChatTurn.status == "ANSWERED", ChatTurn.sequence < turn.sequence).order_by(ChatTurn.sequence.desc()).limit(8)))
    rows = list(reversed(rows))
    if not rows or not any(old["product_name"] in row.message for row in rows) or not any(old["units"] in wealth_units_in_text(row.message) for row in rows):
        return None
    if any(row.diagnostics_json.get("input_redacted") or _intent_text(row.message) != row.message or
            row.diagnostics_json.get("actor_role_at_submission") != role for row in rows):
        return None
    contract = {**contract, "origin_snapshot_id":snapshot.id,"origin_snapshot_hash":canonical_hash(snapshot.proposal_json),
        "source_actor_role":role,
        "source_refs":[{"turn_id":row.id,"message_hash":canonical_hash({"message":row.message})} for row in rows]}
    return resolved, fields, contract


def _short_memo_continuation(task, message, turn_id):
    """Bind a memo-only completion to one original uncompleted transfer."""
    from .intelligence import recompose_transfer_memo
    resolved = recompose_transfer_memo(task.message, message)
    if resolved is None:
        return None
    proposal = rule_proposal(resolved)
    writes = [action for action in proposal.actions if action.kind not in READ_KINDS]
    if len(writes) != 1:
        return None
    action = writes[0]
    values = action.slots.model_dump(exclude_none=True)
    fields = [{"field": field, "value": value, "task_id": task.id,
               "sources": [{"source": "USER_CURRENT", "turn_id": turn_id, "span": message}]
                   if field == "memo" else [{"source": "USER_CONTEXT", "span": task.message}]}
              for field, value in values.items()]
    reads = sorted([{"kind": item.kind, "slots": item.slots.model_dump(exclude_none=True)}
                    for item in proposal.actions if item.kind in READ_KINDS], key=canonical_hash)
    contract = {"version": "chat-memo-continuation-v1", "expected_action_kind": action.kind,
                "expected_slots": values, "expected_write_count": 1, "changed_field": "memo",
                "origin_task_version": task.version, "expected_read_actions": reads,
                "source_message_hash": canonical_hash({"message": task.message})}
    return resolved, fields, contract


def _continuation_question(task, message):
    """Ask only about the unresolved field, never discard already known intent."""
    actions = [action for action in rule_proposal(task.message).actions if action.kind not in READ_KINDS]
    if not actions:
        return "原请求还未识别出明确操作，请说明要办理哪类业务；你的原话与这次补充都已保留。"
    if len(actions) != 1:
        return "原请求包含多个操作，请说明这次要修改哪一步；已明确的其他需求会保留。"
    action = actions[0]
    values = action.slots.model_dump()
    if _recipient_match(message) and action.kind == "TRANSFER_SCHEDULE":
        return "预约时间和原需求已保留。预约转账改收款人需要完整核对日期、金额、币种和新对象，请明确完整的新预约需求。"
    if _recipient_match(message) and (values.get("recipient_alias") or values.get("replace_alias")):
        return "原需求还包含保存或改绑收款身份。改收款人不等于改绑房东等身份，请明确是否保留该绑定指令并给出完整新需求。"
    if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER", "FAMILY_PROPOSE"} and not values.get("payee_name"):
        return "还需要明确收款人，已有金额和币种信息会保留。请提供收款账号、姓名或已绑定身份。"
    if action.kind in {"WEALTH_BUY", "WEALTH_REDEEM"} and not values.get("product_name"):
        return "还需要明确理财产品，已有金额信息会保留。请提供要申购或赎回的具体产品名称。"
    if action.kind in {"GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE"} and not values.get("goal_name"):
        return "还需要明确储蓄目标，已有金额信息会保留。请提供这次要创建、预留或释放的目标名称。"
    if action.kind == "CARD_SET_LIMIT" and not (values.get("card_number") or values.get("card_last4")):
        return "还需要明确要调整限额的卡片，已有金额信息会保留。请提供本人卡号或可唯一识别的尾码。"
    if re.search(r"美元|美金|USD", message, re.I) and re.search(r"人民币|CNY|(?<!美)元", message, re.I):
        return "收款人和原需求已保留。这次补充同时出现人民币和美元，请明确金额按哪种币种计价；付款和到账路线会另行核对。"
    if re.fullmatch(r"(?:还是|改成|用)?(?:人民币|美元|美金|CNY|USD)", message.strip(), re.I):
        return "收款人和原金额已保留。你要改的是金额计价币种，还是付款/到账币种？例如‘金额改成300美元’；付款与到账路线要单独核对。"
    if _amount_like_fragment(message) and _continuation_money(message, "CNY") is None:
        return "原操作对象和原需求已保留。请提供一个确定的正数金额及单一计价币种；约数、范围、全部余额、负数或矛盾单位不能直接用来办理。"
    if re.search(r"美元|美金|USD", message, re.I) and action.kind not in {"TRANSFER", "FX_TRANSFER"}:
        return "原操作对象已保留，但这项操作目前仅支持人民币，不能直接改为美元。请给出人民币金额，或明确提出新的美元转账需求。"
    if values.get("source_currency") or values.get("target_currency"):
        return "原收款人及付款/到账路线已保留。请明确新的计价金额、付款币种和到账币种；不会用改口自动覆盖原币种路线。"
    if not values.get("amount_minor") and not values.get("target_minor"):
        return "操作对象已明确，还需要一个确定金额；请说明具体金额，不使用范围、约数或‘全部余额’代替。"
    return "原操作对象、金额和币种已保留。请说明这次具体要修改哪一项；‘它、第二笔’还不能唯一对应修改目标。"


def _sources_for(session, principal, thread, turn, task):
    if turn.diagnostics_json.get("public_subscription_price_consultation"):
        from .product_knowledge import knowledge_sources, subscription_catalog_source
        from .public_subscription_price_inquiry import public_subscription_price_request
        request = public_subscription_price_request(turn.message)
        catalog = subscription_catalog_source(session, turn.message) if request else None
        if not catalog or task is not None:
            raise DomainError("CONVERSATION_SCOPE_CHANGED", "价格资料已变化，请重新询问。", 409)
        return knowledge_sources(turn.message) + [catalog]
    if turn.diagnostics_json.get("dialogue_reference_explanation"):
        from .dialogue_reference_facts import selected_object_sources
        return selected_object_sources(session,principal,thread,turn.diagnostics_json["dialogue_reference_explanation"])
    if turn.diagnostics_json.get("contextual_safety_source"):
        from .contextual_safety_dialogue import contextual_safety_consultation, validate_source
        candidate = contextual_safety_consultation(session, principal, thread, turn)
        if candidate and validate_source(session, principal, thread, turn, turn.diagnostics_json["contextual_safety_source"]):
            return candidate["records"]
        raise DomainError("CONVERSATION_SCOPE_CHANGED", "之前的资料或权限已变化，请重新查询。", 409)
    if turn.diagnostics_json.get("family_permission_consultation"):
        from .product_knowledge import knowledge_sources
        return [row for row in knowledge_sources("家庭委托授权家人") if row["source_id"] == "kb:family.scope-v1"]
    records = scoped_sources(session, principal, turn.message, task)
    if CREDENTIAL_PATTERN.search(turn.message):
        from .legacy_consultation_facts import credential_channel_source
        records.append(credential_channel_source())
    # Prior server answers carry no user intent authority; only use their KB
    # references to make 'why/you said' explanations evidence-bounded.
    if re.search(r"你刚才|你刚说|为啥|为什么", turn.message):
        last = session.scalar(select(ChatTurn).where(ChatTurn.thread_id == thread.id,
            ChatTurn.sequence < turn.sequence, ChatTurn.status == "ANSWERED").order_by(ChatTurn.sequence.desc()))
        if last:
            for source in last.sources_json:
                if source.get("kind") == "PRODUCT_KNOWLEDGE" and source["source_id"] not in {r["source_id"] for r in records}:
                    records.append(source)
    if task:
        from .task_dialogue_context import task_context, context_source
        context = task_context(session, principal, task)
        if context:
            records.append(context_source(context))
    return records + _attachment_sources(session, principal, turn.reviewed_evidence_refs_json)


from .conversation_turn_state import _finish_status


def _lifecycle_resolution(session, principal, thread, turn, candidate):
    """No model chooses a task UUID or grants permission to a bank operation."""
    from .lifecycle_dialogue import compatible_lifecycle_context, explicitly_current_lifecycle_reference
    from .task_dialogue_context import task_context
    result = {**candidate, "current_allowed": False}
    if not candidate.get("allow_current") or turn.context_policy != "AUTO" or turn.reviewed_evidence_refs_json:
        return result, None
    task, ambiguous = _task_context(session, principal, thread, turn)
    if not turn.requested_task_id and not explicitly_current_lifecycle_reference(turn.message):
        unfinished=[identifier for identifier in _linked_task_ids(session,thread)
            if owned_task(session,principal,identifier).state not in TERMINAL]
        if len(unfinished)>1:
            return result,None
    if not turn.requested_task_id and thread.current_task_id:
        latest = owned_task(session, principal, thread.current_task_id)
        context = task_context(session, principal, latest)
        latest_read = bool(context and (context.get("read_candidates") or context.get("unresolved_read_request")))
        # A prior clarification can leave an older unfinished stub alongside a
        # fully specified new request. "This transfer" refers to the latest
        # actual conversation subject, not to every unfinished task. Prove that
        # subject from an owned, same-role answered row; never select from a
        # model UUID, an attachment, a plural, or a historical reference.
        previous = session.scalar(select(ChatTurn).where(
            ChatTurn.thread_id == thread.id, ChatTurn.user_id == principal.user_id,
            ChatTurn.sandbox_id == principal.sandbox_id, ChatTurn.sequence < turn.sequence,
            ChatTurn.status == "ANSWERED").order_by(ChatTurn.sequence.desc()).limit(1))
        latest_subject = bool(context and explicitly_current_lifecycle_reference(turn.message)
            and previous and previous.task_id == latest.id
            and previous.mode in {"NEW_TASK", "REVISE", "CLARIFY", "STATUS"}
            and not previous.error_code and previous.context_policy == "AUTO"
            and not previous.reviewed_evidence_refs_json
            and not (previous.diagnostics_json or {}).get("input_redacted")
            and (previous.diagnostics_json or {}).get("actor_role_at_submission")
                == actor_role(session, principal.sandbox_id, principal.user_id))
        if ambiguous and latest_subject:
            others = [owned_task(session, principal, identifier) for identifier in _linked_task_ids(session, thread)
                      if identifier != latest.id]
            unfinished = [item for item in others if item.state not in TERMINAL]
            # Only an empty clarification stub can be ignored. Two actual
            # pending banking requests, even of the same kind, still require
            # explicit selection rather than recency-based task picking.
            latest_subject = bool(len(unfinished) <= 32 and all(item.state == 'NEED_CLARIFICATION'
                and (old_context := task_context(session, principal, item)) is not None
                and not old_context.get('action_kinds') for item in unfinished))
        if not ambiguous or latest_read or latest_subject:
            task, ambiguous = latest, False
    context = task_context(session, principal, task) if task and not ambiguous else None
    if context and compatible_lifecycle_context(turn.message, context):
        result["current_allowed"] = True
        return result, task
    return result, None


def _finish_lifecycle(session, principal, thread, turn, candidate, selection):
    resolution, task = _lifecycle_resolution(session, principal, thread, turn, candidate)
    if selection == "CURRENT" and resolution["current_allowed"] and task:
        _finish_status(session, principal, turn, task)
    else:
        answer = "Please identify the original request you want to check. This message does not confirm, repeat or create a payment." if _turn_language(turn) == "en" else "你想核对哪一笔业务的状态？请指明原办理记录；这条消息不会确认、重新转账或新建付款。"
        _finish(turn, "NEED_CONTEXT", answer)
        turn.answer = answer


from .conversation_turn_state import _finish


def _credential_only(cleaned, was_redacted):
    if not was_redacted:
        return False
    remainder = re.sub(r"\[REDACTED\]", "", cleaned, flags=re.I)
    remainder = re.sub(r"^(?:这是)?(?:我的|我收到的|我这次的|my\b)\s*", "", remainder, flags=re.I)
    remainder = re.sub(r"Bearer|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|verification\s+code|passcode|credential|secret|OTP|验证码|校验码|动态码|密码|密钥|(?:是|为)|\bis\b", "", remainder, flags=re.I)
    return not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", remainder)


def _family_consult_candidate(session, principal, thread, turn):
    from .fraud_security_dialogue import family_permission_question
    if family_permission_question(turn.message):
        return True
    text=turn.message
    if turn.context_policy!='AUTO' or not re.search(r"咨询|只是问|只想问",text) or not re.search(r"授权|委托",text) or not re.search(r"余额",text):
        return False
    previous=session.scalar(select(ChatTurn).where(ChatTurn.thread_id==thread.id,ChatTurn.user_id==principal.user_id,
        ChatTurn.sandbox_id==principal.sandbox_id,ChatTurn.sequence<turn.sequence,ChatTurn.status=='ANSWERED').order_by(ChatTurn.sequence.desc()).limit(1))
    return bool(previous and previous.mode=='CONSULT' and previous.diagnostics_json.get('family_permission_consultation'))


def _result_reference_candidate(session,principal,thread,turn):
    """An optional object lookup cannot veto unrelated dialogue or safety help."""
    from .dialogue_reference_integration import build_result_reference_binding
    if turn.context_policy!="AUTO" or not thread.current_task_id:
        return None
    if not _task_epoch_current(session,principal,owned_task(session,principal,thread.current_task_id)):
        return None
    if re.search(r"(?:尾号|卡号|末四位|ending\s+in)\s*[:：]?\s*[0-9a-fA-F]{4}(?![0-9a-fA-F])|(?<![A-Za-z0-9_])(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}(?![0-9a-fA-F])",turn.message,re.I):
        return None  # Complete new literal identifiers use current text, not an old query object.
    try:
        return build_result_reference_binding(session,principal,thread,turn,origin_task_id=thread.current_task_id)
    except DomainError as error:
        if error.code.startswith("DIALOGUE_REFERENCE"):
            return None  # No proof means no object inheritance or new authority.
        raise


def _minimal_ambiguous_question(session,principal,thread,turn):
    """Name verified candidate subjects, not internal task IDs or a rewrite demand."""
    from .dialogue_state import sync_dialogue_state
    labels=[]
    for frame in sync_dialogue_state(session,principal,thread,current_turn=turn)["frames"]:
        if not frame.get("continuation_allowed"):
            continue
        for action in frame.get("actions",[]):
            slots=action.get("slots",{})
            target=next((slots.get(field) for field in ("payee_name","product_name","merchant","grantee_name") if slots.get(field)),None)
            if not target:
                continue
            target=redact_advice_input(str(target)).replace("\n"," ").replace("\r"," ")[:40]
            label=("给"+target+"的转账") if action["kind"] in {"TRANSFER","FX_TRANSFER","TRANSFER_SCHEDULE"} else target
            if label not in labels:
                labels.append(label)
    if 1<len(labels)<=4:
        return ("Which request do you mean: "+" or ".join(labels)+"? I'll keep the details you already gave.") if _turn_language(turn)=="en" else (
            "你指的是"+"，还是".join(labels)+"？已经说清的信息会保留。")
    return "Which unfinished request do you mean? I'll keep the details already provided." if _turn_language(turn)=="en" else "你指刚才哪一项需求？已经说清的信息会保留。"


def _pending_family_scope_binding(session,principal,thread,turn,task):
    """Explicit edits to an existing grant are not a general permissions FAQ."""
    if not task or turn.context_policy!="AUTO" or turn.reviewed_evidence_refs_json or turn.diagnostics_json.get("input_redacted"):
        return None
    if not re.match(r"^(?:(?:权限|授权范围|范围)\s*)?(?:改成|改为|调整为|换成|设为)|^只(?:让|允许|授权|给)(?:他|她|孩子|儿子|女儿)",turn.message):
        return None
    from .runtime_models import PlanSnapshot
    record=session.get(RuntimeRecord,task.id)
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,PlanSnapshot.version==record.plan_version)) if record else None
    if not snapshot or {row.get("kind") for row in snapshot.proposal_json.get("actions",[])}!={"FAMILY_GRANT"}:
        return None
    from .dialogue_slot_updates import build_slot_update
    return build_slot_update(session,principal,thread,turn,task)


def _accept_slot_continuation(session,principal,thread,turn,task,binding):
    from .runtime import clarify_runtime
    from .dialogue_slot_updates import METADATA_KEY
    if turn.expected_task_version is not None and turn.expected_task_version!=task.version:
        raise DomainError("TASK_VERSION_CHANGED","这项需求已变化，请核对最新方案。",409)
    continued=clarify_runtime(session,principal,task.id,turn.message,task.version,revise=True)
    record=session.get(RuntimeRecord,continued.id)
    metadata={key:value for key,value in record.metadata_json.items() if key not in {"owned_user_dialogue","pending_subject_selection","conversation_context"}}
    record.metadata_json={**metadata,METADATA_KEY:binding,"reply_language":_turn_language(turn),
        "conversation_context":{"conversation_id":thread.id,"turn_id":turn.id,"original_user_message":turn.message,"authority":"VERIFIED_PER_ACTION_USER_FIELDS"}}
    thread.current_task_id,thread.version=continued.id,thread.version+1
    _finish(turn,"REVISE","好的，我会保留前面说清的信息，按这次补充重新准备方案。",continued,"CONTINUED")
    turn.diagnostics_json={**turn.diagnostics_json,"per_action_fields_retained":True}


def _pending_transfer_control_kind(message):
    """Whole current-user controls, not quoted text or arbitrary target names."""
    if not isinstance(message,str) or _intent_text(message)!=message or len(message)>160:
        return None
    if re.fullmatch(r"(?:我)?(?:再|先)?想想(?:再说)?|我再考虑(?:一下)?|让我(?:再)?考虑一下",message):
        return "HESITATE"
    if re.fullmatch(r"先(?:别|不要|不)(?:再)?(?:转(?:账|帐)?|付款|执行)(?:了)?|暂时(?:别|不要|不)(?:转(?:账|帐)?|付款)(?:了)?",message):
        return "PAUSE"
    if re.fullmatch(r"(?:我说的)?不是(?:给|转给)?[^，,。；;\n]{1,80}[。.!！]?",message):
        return "DENY_TARGET"
    if re.fullmatch(r"(?:还是|那就)(?:给|转给|向)(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}(?:吧|好了|就行)?[。.!！]?|"
                    r"(?:还是|那就)(?:给|转给|向)[A-Za-z][A-Za-z0-9_]{2,31}(?:吧|好了|就行)?[。.!！]?|"
                    r"(?:还是|那就)(?:给|转给|向)[\u4e00-\u9fff]{2,16}(?:吧|好了|就行)?[。.!！]?",message):
        return "REPLACE_TARGET"
    if re.fullmatch(r"(?:请|那|现在)?(?:继续|恢复|接着)(?:(?:(?:刚才|之前)(?:的|那笔)?|原来的|这笔|那笔)(?:转(?:账|帐)?|付款)?|转(?:账|帐)?|付款)?(?:吧|了|就好|好吗)?[。.!！]?",message):
        return "RESUME"
    if re.fullmatch(r"(?:取消|撤销)(?:这笔|当前|刚才的)?(?:转账|请求|任务)|(?:不要|别)转(?:账|帐|了)?",message):
        return "CANCEL_TRANSFER"
    return None


def _verified_transfer_plan_source(session, principal, task, record, thread, before_sequence=None):
    from .runtime_models import PlanSnapshot
    from .dialogue_slot_updates import _turn_valid, _source_ref, _actions
    from .dialogue_state import dialogue_actor_scope
    from .conversation_provenance import validate_current_continuation
    from .intelligence import IntentProposal, ground_proposal
    try:
        snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id == task.id,
            PlanSnapshot.version == record.plan_version))
        if record.kind != "PARENT" or snapshot is None or snapshot.plan_hash != task.plan_hash \
                or canonical_hash(snapshot.plan_json) != snapshot.plan_hash \
                or canonical_hash(task.plan_json) != task.plan_hash:
            return None
        query = select(ChatTurn).where(ChatTurn.thread_id == thread.id, ChatTurn.task_id == task.id,
            ChatTurn.user_id == principal.user_id, ChatTurn.sandbox_id == principal.sandbox_id,
            ChatTurn.mode.in_(["NEW_TASK", "REVISE", "CLARIFY"]), ChatTurn.status == "ANSWERED")
        if before_sequence is not None:
            query = query.where(ChatTurn.sequence < before_sequence)
        source = session.scalar(query.order_by(ChatTurn.sequence.desc()).limit(1))
        _, scope_hash = dialogue_actor_scope(session, principal)
        if source is None or not _turn_valid(session, principal, thread, source, scope_hash, prior=True):
            return None
        validate_current_continuation(session, task)
        proposal = IntentProposal.model_validate(snapshot.proposal_json)
        metadata = record.metadata_json
        typed = any(metadata.get(key) for key in ("dialogue_slot_update", "dialogue_result_reference", "owned_user_dialogue")) \
            or (metadata.get("conversation_context") or {}).get("authority") == "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS"
        if not typed and (source.message != task.message or not proposal.actions \
                or _actions(ground_proposal(proposal, source.message)) != _actions(proposal)):
            return None
        if len(proposal.actions) != 1 or proposal.actions[0].kind not in {"TRANSFER", "FX_TRANSFER"} \
                or proposal.actions[0].depends_on:
            return None
        authority = {key: metadata.get(key) for key in ("dialogue_slot_update", "dialogue_result_reference",
            "owned_user_dialogue", "conversation_context", "pending_subject_selection")}
        proof = {"snapshot_id": snapshot.id, "snapshot_hash": canonical_hash(snapshot.proposal_json),
            "plan_hash": snapshot.plan_hash, "source_ref": _source_ref(source), "authority_hash": canonical_hash(authority)}
        return snapshot, proposal.actions[0], proof
    except (DomainError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return None


def _single_mutable_transfer(session,principal,thread,turn,task,ambiguous):
    if ambiguous or task is None or turn.context_policy!="AUTO" or turn.reviewed_evidence_refs_json \
            or turn.diagnostics_json.get("input_redacted") or task.state not in {"NEED_CLARIFICATION","AWAITING_CONFIRMATION"}:
        return None
    task=owned_task(session,principal,task.id,lock=True)
    from .dialogue_slot_updates import _lifecycle
    if not _lifecycle(session,task) or not _task_epoch_current(session,principal,task):
        return None
    record=session.get(RuntimeRecord,task.id)
    if not record or record.metadata_json.get("family_origin") or record.metadata_json.get("trusted_compensation"):
        return None
    verified=_verified_transfer_plan_source(session,principal,task,record,thread,turn.sequence)
    if not verified:
        return None
    return task,record,verified[1]


def _pause_transfer_plan(session,principal,thread,task,record,turn):
    """Stop only unexecuted preparation; retain the intent, not its old grant."""
    from .models import Confirmation
    from .runtime_models import AuthChallenge,WorkflowJob
    verified=_verified_transfer_plan_source(session,principal,task,record,thread,turn.sequence)
    if not verified:
        raise DomainError("DIALOGUE_SLOT_SOURCE_CHANGED","原来的信息已变化，请重新核对；未执行转账。",409)
    old_pause=(record.metadata_json or {}).get("dialogue_pause")
    pending=old_pause.get("pending_slot_update") if isinstance(old_pause,dict) and current_transfer_pause(session,principal,task) else None
    operations=list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id==task.id)))
    ids=[task.id,*[operation.child_task_id for operation in operations if operation.child_task_id]]
    for operation in operations:
        if operation.action_kind not in READ_KINDS and operation.status not in {"SUCCEEDED","UNKNOWN","AUTHORIZED","RUNNING"}:
            operation.active=0;operation.status="CANCELLED"
    for confirmation in session.scalars(select(Confirmation).where(Confirmation.task_id.in_(ids),Confirmation.status=="PENDING")):
        confirmation.status="CANCELLED"
    for challenge in session.scalars(select(AuthChallenge).where(AuthChallenge.task_id.in_(ids),AuthChallenge.status.in_(["PENDING","VERIFIED"]))):
        challenge.status="REVOKED"
    for job in session.scalars(select(WorkflowJob).where(WorkflowJob.task_id==task.id,WorkflowJob.kind.in_(["PLAN","EXECUTE"]),WorkflowJob.status.in_(["QUEUED","LEASED"]))):
        job.status="CANCELLED";job.lease_expires_at=None;job.completed_at=utcnow()
    task.version+=1
    record.metadata_json={**record.metadata_json,"input_revision":record.metadata_json.get("input_revision",0)+1,
        "dialogue_pause":{"version":"owned-unexecuted-plan-pause-v1","turn_id":turn.id,
            "message_hash":canonical_hash({"message":turn.message}),"actor_scope_hash":turn.diagnostics_json.get("dialogue_actor_scope_hash"),
            "intent_message_hash":canonical_hash({"message":task.message}),"task_version":task.version,
            "intent_source":verified[2],**({"pending_slot_update":pending} if pending else {})}}
    from .audit import append_audit
    append_audit(session,sandbox_id=task.sandbox_id,task_id=task.id,event_type="DIALOGUE_PLAN_PAUSED",
        payload={"source_turn_id":turn.id,"source_message_hash":canonical_hash({"message":turn.message}),
            "confirmation_invalidated":True,"bank_write_authorized":False})


def current_transfer_pause(session, principal, task):
    """Fresh, read-only customer projection; prose or metadata alone is not proof.

    A pause retains only an unexecuted owned intent. It never authorizes a bank
    action, and it disappears as soon as the source, actor, plan or grants change.
    """
    if task:
        semantic_record=session.get(RuntimeRecord,task.id)
        if semantic_record and semantic_record.metadata_json.get('semantic_pause'):
            from .semantic_lifecycle import current_semantic_pause
            return current_semantic_pause(session,principal,task)
    from .dialogue_slot_updates import _lifecycle, _turn_valid, validate_paused_slot_update
    from .dialogue_state import dialogue_actor_scope
    from .models import Confirmation
    from .runtime_models import AuthChallenge, WorkflowJob
    try:
        if task is None or (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id):
            return None
        require_banking_actor(session, principal)
        record = session.get(RuntimeRecord, task.id)
        metadata = record.metadata_json if record else None
        pause = metadata.get("dialogue_pause") if isinstance(metadata, dict) else None
        _, scope_hash = dialogue_actor_scope(session, principal)
        if not isinstance(pause, dict) or pause.get("version") != "owned-unexecuted-plan-pause-v1" \
                or type(pause.get("task_version")) is not int or pause["task_version"] != task.version \
                or pause.get("actor_scope_hash") != scope_hash \
                or metadata.get("dialogue_actor_scope_hash") != scope_hash \
                or pause.get("intent_message_hash") != canonical_hash({"message": task.message}) \
                or task.state not in {"NEED_CLARIFICATION", "AWAITING_CONFIRMATION"} \
                or metadata.get("family_origin") or metadata.get("trusted_compensation") \
                or not _lifecycle(session, task):
            return None
        source = session.get(ChatTurn, pause.get("turn_id"))
        thread = session.get(ChatThread, source.thread_id) if source else None
        if source is None or thread is None or source.task_id != task.id or source.mode != "FEEDBACK" \
                or source.task_relationship != "CURRENT" \
                or source.diagnostics_json.get("transfer_plan_paused") is not True \
                or source.diagnostics_json.get("bank_write_authorized") is not False \
                or source.diagnostics_json.get("dialogue_actor_scope_hash") != scope_hash \
                or pause.get("message_hash") != canonical_hash({"message": source.message}) \
                or _pending_transfer_control_kind(source.message) not in {"PAUSE", "HESITATE"} \
                or not _turn_valid(session, principal, thread, source, scope_hash, prior=True):
            return None
        verified = _verified_transfer_plan_source(session, principal, task, record, thread, source.sequence)
        if not verified or pause.get("intent_source") != verified[2]:
            return None
        operations = list(session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id)))
        ids = [task.id, *[op.child_task_id for op in operations if op.child_task_id]]
        if any(op.active for op in operations) \
                or session.scalar(select(Confirmation.id).where(Confirmation.task_id.in_(ids),
                    Confirmation.status == "PENDING").limit(1)) \
                or session.scalar(select(AuthChallenge.id).where(AuthChallenge.task_id.in_(ids),
                    AuthChallenge.status.in_(["PENDING", "VERIFIED"])).limit(1)) \
                or session.scalar(select(WorkflowJob.id).where(WorkflowJob.task_id.in_(ids),
                    WorkflowJob.kind.in_(["PLAN", "EXECUTE"]),
                    WorkflowJob.status.in_(["QUEUED", "LEASED"])).limit(1)):
            return None
        if pause.get("pending_slot_update") is not None:
            validate_paused_slot_update(session, principal, task, pause["pending_slot_update"])
        return {"version": "customer-paused-plan-v1", "task_id": task.id,
            "task_version": task.version, "paused": True, "confirmation_available": False}
    except (DomainError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return None


def _process_pending_transfer_control(session,principal,thread,turn,task,ambiguous):
    kind=_pending_transfer_control_kind(turn.message)
    if not kind or turn.context_policy!="AUTO" or turn.reviewed_evidence_refs_json or turn.diagnostics_json.get("input_redacted"):
        return False
    safe=_single_mutable_transfer(session,principal,thread,turn,task,ambiguous)
    if not safe:
        if kind=="HESITATE":
            answer="好的，不急，想好再告诉我。"
            _finish(turn,"FEEDBACK",answer)
        elif ambiguous:
            _finish(turn,"NEED_CONTEXT",_minimal_ambiguous_question(session,principal,thread,turn))
        elif task and task.state in IN_FLIGHT:
            _finish(turn,"STATUS","这笔已进入处理，不能用这句话停止或改动，需要先核验结果。",task,"CURRENT")
        else:
            _finish(turn,"FEEDBACK","好的，这次不会新建或执行转账。需要继续时再告诉我。",task,"CURRENT" if task else None)
        turn.diagnostics_json={**turn.diagnostics_json,"bank_write_authorized":False,"preserve_goal_context":True}
        return True
    task,record,action=safe
    if turn.expected_task_version is not None and turn.expected_task_version!=task.version:
        raise DomainError("TASK_VERSION_CHANGED","这项需求已变化，请核对最新方案。",409)
    if kind in {"PAUSE","HESITATE"}:
        _pause_transfer_plan(session,principal,thread,task,record,turn)
        _finish(turn,"FEEDBACK","好的，已先暂停准备，不会转出。原来的信息会保留，想好后再告诉我。",task,"CURRENT")
        turn.diagnostics_json={**turn.diagnostics_json,"preserve_goal_context":True,"transfer_plan_paused":True,"bank_write_authorized":False}
        return True
    if kind=="CANCEL_TRANSFER":
        from .runtime import cancel_runtime
        cancel_runtime(session,principal,task.id,task.version)
        _finish(turn,"FEEDBACK","这笔未执行的转账已取消，资金没有转出。",task,"CURRENT")
        turn.diagnostics_json={**turn.diagnostics_json,"bank_write_authorized":False}
        return True
    if kind=="RESUME":
        pause=(record.metadata_json or {}).get("dialogue_pause")
        if not pause:
            _finish(turn,"FEEDBACK","原来的信息已保留，请核对这笔转账的确认信息后再决定。",task,"CURRENT")
            return True
        if not current_transfer_pause(session,principal,task):
            raise DomainError("DIALOGUE_SLOT_SOURCE_CHANGED","原来的暂停信息已变化，请重新核对；未执行转账。",409)
        from .runtime import clarify_runtime
        pending=pause.get("pending_slot_update")
        if pending:
            from .dialogue_slot_updates import validate_paused_slot_update,METADATA_KEY
            verified=validate_paused_slot_update(session,principal,task,pending)
            continued=clarify_runtime(session,principal,task.id,verified.utterances[-1],task.version,revise=True)
            metadata={key:value for key,value in record.metadata_json.items() if key not in {"dialogue_pause","owned_user_dialogue","pending_subject_selection"}}
            record.metadata_json={**metadata,METADATA_KEY:pending}
        else:
            continued=clarify_runtime(session,principal,task.id,task.message,task.version,revise=True)
            record.metadata_json={key:value for key,value in record.metadata_json.items() if key!="dialogue_pause"}
        _finish(turn,"REVISE","好的，我按已经说清的信息重新准备，仍需你核对后确认。",continued,"CONTINUED")
        turn.answer="好的，我按已经说清的信息重新准备，仍需你核对后确认。"
        turn.diagnostics_json={**turn.diagnostics_json,"preserve_goal_context":True,"bank_write_authorized":False}
        return True
    from .dialogue_slot_updates import build_slot_update
    pause=(record.metadata_json or {}).get("dialogue_pause")
    if isinstance(pause,dict) and pause.get("pending_slot_update"):
        if not current_transfer_pause(session,principal,task):
            raise DomainError("DIALOGUE_SLOT_SOURCE_CHANGED","原来的暂停信息已变化，请重新核对；未执行转账。",409)
        from .dialogue_slot_updates import validate_paused_slot_update
        action=validate_paused_slot_update(session,principal,task,pause["pending_slot_update"]).expected_proposal.actions[0]
    if kind=="DENY_TARGET":
        target=action.slots.payee_name
        if not target or not re.fullmatch(r"(?:我说的)?不是(?:给|转给)?\s*"+re.escape(target)+r"[。.!！]?",turn.message):
            _finish(turn,"FEEDBACK","明白，你想把这笔改给谁？金额和原来的信息会保留。",task,"CURRENT")
            return True
        changes=[{"ordinal":0,"field":"payee_name","operation":"CLEAR","value":None,"span":{"start":0,"end":len(turn.message)}}]
        answer="已把原收款人移除。这笔要转给谁？金额和币种会保留。"
    else:
        match=re.fullmatch(r"(?:还是|那就)(?:给|转给|向)(?P<target>(?:zhenhang|xiaozhao)[0-9a-fA-F]{8}|[A-Za-z][A-Za-z0-9_]{2,31}|[\u4e00-\u9fff]{2,16}?)(?:吧|好了|就行)?[。.!！]?",turn.message)
        if not match or match["target"] in {"他","她","他们","她们","那个人"} or action.slots.recipient_alias or action.slots.replace_alias:
            _finish(turn,"NEED_CONTEXT","你想把这笔改给谁？原来的金额会保留。",task,"CURRENT")
            return True
        changes=[{"ordinal":0,"field":"payee_name","operation":"SET","value":match["target"],
            "span":{"start":match.start("target"),"end":match.end("target")}}]
        answer="好的，收款人已按你这次的说明修改，金额会保留，请核对新的确认信息。"
    if (record.metadata_json or {}).get("dialogue_pause"):
        if not current_transfer_pause(session,principal,task):
            raise DomainError("DIALOGUE_SLOT_SOURCE_CHANGED","原来的暂停信息已变化，请重新核对；未执行转账。",409)
        from .dialogue_slot_updates import build_paused_slot_update
        draft=build_paused_slot_update(session,principal,thread,turn,task,changes=changes)
        if draft:
            record.metadata_json={**record.metadata_json,"dialogue_pause":{**record.metadata_json["dialogue_pause"],"pending_slot_update":draft}}
            _finish(turn,"FEEDBACK","已记下这次修改，这笔仍先暂停。想继续时再告诉我。",task,"CURRENT")
            turn.diagnostics_json={**turn.diagnostics_json,"preserve_goal_context":True,"bank_write_authorized":False}
            return True
        _finish(turn,"NEED_CONTEXT","这笔仍先暂停，你想改给哪个收款人？",task,"CURRENT")
        return True
    binding=build_slot_update(session,principal,thread,turn,task,changes=changes)
    if not binding:
        _finish(turn,"NEED_CONTEXT","原来的金额会保留，你想改给哪个收款人？",task,"CURRENT")
        return True
    record.metadata_json={key:value for key,value in record.metadata_json.items() if key!="dialogue_pause"}
    _accept_slot_continuation(session,principal,thread,turn,task,binding)
    turn.answer=answer
    return True


_DECLINE_SUBSCRIPTION_PURCHASE=re.compile(r"^(?:算了[，,]?\s*)?(?:(?:我|还是)?不订了|这次不订了|(?:先)?(?:别|不要)(?:开通|恢复|续订|买)(?:了)?|取消(?:这次|当前|这笔)(?:开通|购买|续订)|cancel\s+this\s+(?:purchase|reactivation|renewal)|don['’]?t\s+(?:subscribe|activate|renew))[。.!！\s]*$",re.I)


def _decline_pending_subscription(session,principal,thread,turn,task,ambiguous):
    """Withdraw only a whole-current unexecuted purchase, not a paid service."""
    if not _DECLINE_SUBSCRIPTION_PURCHASE.fullmatch(turn.message) or _intent_text(turn.message)!=turn.message \
            or turn.context_policy!="AUTO" or turn.reviewed_evidence_refs_json or turn.diagnostics_json.get("input_redacted"):
        return False
    if ambiguous:
        _finish(turn,"NEED_CONTEXT",_minimal_ambiguous_question(session,principal,thread,turn))
        return True
    if task is None:
        _finish(turn,"FEEDBACK","好的，这次不会发起会员购买或扣费。")
        return True
    from .runtime_models import PlanSnapshot
    from .dialogue_slot_updates import _lifecycle
    from .intelligence import IntentProposal
    record=session.get(RuntimeRecord,task.id)
    snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,PlanSnapshot.version==record.plan_version)) if record else None
    if not snapshot:
        return False
    actions=IntentProposal.model_validate(snapshot.proposal_json).actions
    if not actions or any(action.kind not in {"SUBSCRIPTION_START","SUBSCRIPTION_RENEW"} for action in actions):
        return False
    if not _lifecycle(session,task) or not _task_epoch_current(session,principal,task):
        _finish(turn,"STATUS","这次办理的结果需要先核对，不能据此承诺撤回扣款或退款。",task,"CURRENT")
        return True
    if snapshot.plan_hash!=task.plan_hash or canonical_hash(snapshot.plan_json)!=snapshot.plan_hash:
        raise DomainError("PLAN_INTEGRITY_FAILED","办理资料已变化，请先核对最新记录。",409)
    from .conversation_provenance import validate_current_continuation
    from .runtime import cancel_runtime
    validate_current_continuation(session,task)
    cancel_runtime(session,principal,task.id,task.version)
    _finish(turn,"FEEDBACK","已停止这次未执行的会员办理，没有扣费，也没有改变已有会员权益。",task,"CURRENT")
    turn.diagnostics_json={**turn.diagnostics_json,"bank_write_authorized":False,"subscription_purchase_declined":True}
    return True


def _process_policy(session, principal, thread, turn, *, semantic_read_kind=None):
    if turn.diagnostics_json.get("credential_only"):
        _finish(turn, "CONFIRM_HINT", "凭据已脱敏；聊天不会使用秘钥、验证码或登录令牌。请在专用登录/验证控件操作，不会创建银行任务。")
        return None
    from .public_subscription_price_inquiry import public_subscription_price_request
    if public_subscription_price_request(turn.message) and not turn.reviewed_evidence_refs_json and not turn.diagnostics_json.get("input_redacted"):
        from .product_knowledge import knowledge_sources, subscription_catalog_source
        catalog = subscription_catalog_source(session, turn.message)
        if catalog:
            turn.diagnostics_json = {**turn.diagnostics_json, "public_subscription_price_consultation": True,
                "bank_task_created": False, "bank_write_authorized": False, "preserve_goal_context": True}
            return None, knowledge_sources(turn.message) + [catalog]
    task, ambiguous = _task_context(session, principal, thread, turn)
    text = turn.message.strip()
    if PURE_STATUS_QUESTION.fullmatch(text) and turn.context_policy=='AUTO' and not turn.reviewed_evidence_refs_json:
        # Legacy compatibility only. A status pronoun must not be consumed as a
        # bank-object query. Production registry turns still enter the unified
        # cloud semantic path before this policy is ever called.
        if turn.requested_task_id:
            status_task=owned_task(session,principal,turn.requested_task_id)
        elif thread.current_task_id:
            status_task=owned_task(session,principal,thread.current_task_id)
        else:
            status_task=None
        if ambiguous and not turn.requested_task_id:
            from .task_dialogue_context import task_context
            status_context=task_context(session,principal,status_task) if status_task else None
            completed_read=bool(status_context and status_context.get('state')=='SUCCEEDED'
                and status_context.get('outcome_kind')=='QUERY_RESULT' and status_context.get('verified') is True
                and status_context.get('action_kinds')
                and all(kind in READ_KINDS for kind in status_context['action_kinds'])
                and not status_context.get('completed_write_present') and not status_context.get('possible_write_effect'))
            if not completed_read:
                _finish(turn,'NEED_CONTEXT',_minimal_ambiguous_question(session,principal,thread,turn))
                return None
        if status_task and _task_epoch_current(session,principal,status_task):
            _finish_status(session,principal,turn,status_task)
        else:
            _finish(turn,'NEED_CONTEXT','你想查看哪一项办理记录？请告诉我具体事项；不会新建或重复办理。')
        return None
    if _decline_pending_subscription(session,principal,thread,turn,task,ambiguous):
        return None
    if _process_pending_transfer_control(session,principal,thread,turn,task,ambiguous):
        return None
    if not ambiguous and task and turn.context_policy=="AUTO" and not turn.reviewed_evidence_refs_json:
        record=session.get(RuntimeRecord,task.id)
        if record and record.metadata_json.get("dialogue_pause") and current_transfer_pause(session,principal,task) \
                and _single_mutable_transfer(session,principal,thread,turn,task,False):
            from .dialogue_slot_updates import build_paused_slot_update
            pending=build_paused_slot_update(session,principal,thread,turn,task)
            if pending:
                record.metadata_json={**record.metadata_json,"dialogue_pause":{**record.metadata_json["dialogue_pause"],"pending_slot_update":pending}}
                _finish(turn,"FEEDBACK","已记下这次补充，这笔仍先暂停，想继续时再告诉我。",task,"CURRENT")
                turn.diagnostics_json={**turn.diagnostics_json,"preserve_goal_context":True,"bank_write_authorized":False}
                return None
    family_delta=_pending_family_scope_binding(session,principal,thread,turn,task) if not ambiguous else None
    if family_delta:
        _accept_slot_continuation(session,principal,thread,turn,task,family_delta)
        return None
    if turn.context_policy == "AUTO" and not turn.diagnostics_json.get("input_redacted") and not turn.reviewed_evidence_refs_json:
        from .dialogue_state import select_dialogue_focus
        navigation = select_dialogue_focus(session, principal, thread, text,
            requested_task_id=turn.requested_task_id, current_turn=turn)
        if navigation and not navigation.get("ambiguous") and navigation.get("task_id"):
            selected = owned_task(session, principal, navigation["task_id"])
            if selected.state not in {"EXECUTING", "UNKNOWN", "AUTHORIZED", "CLOSED", "FAILED"}:
                task, ambiguous = selected, False
                turn.diagnostics_json = {**turn.diagnostics_json, "dialogue_focus_selection": navigation["proof"]}
    from .fraud_security_dialogue import reported_security_read_request, family_permission_question
    from .contextual_safety_dialogue import contextual_safety_consultation
    safety = contextual_safety_consultation(session, principal, thread, turn)
    if safety:
        turn.diagnostics_json = {**turn.diagnostics_json, "contextual_safety_source": safety["source_binding"],
            "contextual_safety_topic": safety["topic"], "bank_write_authorized": False}
        return None, safety["records"]
    security_read=reported_security_read_request(text, credential_only=bool(turn.diagnostics_json.get('credential_only')))
    from .intelligence import _transfer_stop_resolution
    stop_questions=_transfer_stop_resolution(text)[1]
    if stop_questions and not security_read:
        stop_question='；'.join(stop_questions)
        # A new targeted stop ambiguity is not a supplement to an arbitrary
        # unfinished task. Preserve its precise current-user question, with
        # no bank task/confirmation and no claim that anything was cancelled.
        _finish(turn,'NEED_CONTEXT',stop_question)
        turn.answer=stop_question
        turn.diagnostics_json={**turn.diagnostics_json,'targeted_transfer_stop_clarification':True,
            'bank_write_authorized':False}
        return None
    if security_read:
        from .runtime import accept_task
        from .owned_user_dialogue import SECURITY_READ_VERSION
        accepted = accept_task(session, principal, text, "chat:" + turn.id)
        record = session.get(RuntimeRecord, accepted.id)
        record.metadata_json = {**record.metadata_json,"reply_language":_turn_language(turn),
            "owned_user_dialogue":{"version":SECURITY_READ_VERSION,"scope":"READ_ONLY","conversation_id":thread.id,
                "turn_id":turn.id,"message_hash":canonical_hash({"message":text}),
                "source_role":actor_role(session,principal.sandbox_id,principal.user_id)},
            "conversation_context":{"conversation_id":thread.id,"turn_id":turn.id,"original_user_message":text,
                "authority":"REPORTED_SECURITY_READ_ONLY"}}
        thread.current_task_id, thread.version = accepted.id, thread.version + 1
        _finish(turn,"NEW_TASK","我先核查对方说法的风险；你另外提到的银行办理需求尚未办理，需在核实安全后独立核对。",accepted,"CREATED")
        turn.diagnostics_json = {**turn.diagnostics_json,"reported_security_read_only":True,"bank_write_authorized":False}
        return None
    family_question = _family_consult_candidate(session, principal, thread, turn)
    if family_question:
        turn.diagnostics_json = {**turn.diagnostics_json,"family_permission_consultation":True}
        return None, _sources_for(session, principal, thread, turn, None)
    from .dialogue_reference_integration import build_result_reference_binding, METADATA_KEY as REFERENCE_METADATA
    reference = _result_reference_candidate(session,principal,thread,turn)
    if reference and reference["mode"]=="EXPLANATION":
        turn.diagnostics_json={**turn.diagnostics_json,"dialogue_reference_explanation":reference,"bank_write_authorized":False}
        return None,_sources_for(session,principal,thread,turn,None)
    if reference and reference["mode"] != "EXPLANATION":
        from .runtime import accept_task
        accepted=accept_task(session,principal,text,"chat:"+turn.id)
        record=session.get(RuntimeRecord,accepted.id)
        record.metadata_json={**record.metadata_json,REFERENCE_METADATA:reference,"reply_language":_turn_language(turn),
            "conversation_context":{"conversation_id":thread.id,"turn_id":turn.id,"original_user_message":text,
                "authority":"CURRENT_USER_REQUEST_VERIFIED_RESULT_OBJECT"}}
        thread.current_task_id,thread.version=accepted.id,thread.version+1
        _finish(turn,"NEW_TASK","好的，我会按你选的对象准备。",accepted,"CREATED")
        return None
    from .owned_user_dialogue import result_followup_candidate
    current_epoch_valid=bool(thread.current_task_id and _task_epoch_current(session,principal,owned_task(session,principal,thread.current_task_id)))
    followup = result_followup_candidate(session, principal, thread, turn) if current_epoch_valid else None
    if followup:
        if followup.get("blocked_reason"):
            reason = followup["blocked_reason"]
            answer = ("Which savings goal do you want to compare?" if reason == "TARGET_REQUIRED" else "The original read source or permissions changed. Please check the latest goal or benefits first; no funds were changed." if reason == "SOURCE_CHANGED" else "Is this your new total monthly saving or an extra amount? Please clarify the one assumption; no funds are changed.") if _turn_language(turn) == "en" else ("你想比较哪个储蓄目标？" if reason == "TARGET_REQUIRED" else "原查询来源或权限已变化，请先重新核对目标或权益；不会改动资金。" if reason == "SOURCE_CHANGED" else "这是新的每月总储蓄额，还是每月额外增加的金额？请明确这一个假设；不会改动资金。")
            if reason == "REDEMPTION_DOMAIN_REQUIRED":
                answer = "Do you mean low-carbon points and benefits, or exchanging CNY and USD?" if _turn_language(turn) == "en" else "你想问低碳积分能换哪些权益，还是人民币与美元换汇？"
            _finish(turn, "NEED_CONTEXT", answer)
            turn.answer = answer
            return None
        from .runtime import accept_task
        accepted = accept_task(session, principal, text, "chat:" + turn.id)
        record = session.get(RuntimeRecord, accepted.id)
        record.metadata_json = {**record.metadata_json, "reply_language": _turn_language(turn),
            "owned_user_dialogue": followup, "conversation_context": {"conversation_id": thread.id,
                "turn_id": turn.id, "original_user_message": text, "authority": "OWNED_READ_RESULT_REVISION"}}
        thread.current_task_id, thread.version = accepted.id, thread.version + 1
        _finish(turn, "NEW_TASK", "我按这次的假设重新查询，原比较基准会保留；不会改动资金或自动兑换。", accepted, "CREATED")
        return None
    from .card_lock_reference import lock_reference_request, reference_candidate
    if lock_reference_request(text):
        binding=reference_candidate(session,principal,thread,turn)
        if binding:
            from .runtime import accept_task
            accepted=accept_task(session,principal,text,'chat:'+turn.id)
            record=session.get(RuntimeRecord,accepted.id)
            record.metadata_json={**record.metadata_json,'reply_language':_turn_language(turn),'owned_user_dialogue':binding,
                'conversation_context':{'conversation_id':thread.id,'turn_id':turn.id,'original_user_message':text,'authority':'CURRENT_EXPLICIT_CARD_LOCK_REFERENCE'}}
            thread.current_task_id,thread.version=accepted.id,thread.version+1
            _finish(turn,'NEW_TASK','我会为你准备临时锁住刚才明确查询那张卡的方案，请核对确认后办理。',accepted,'CREATED')
        else:
            _finish(turn,'NEED_CONTEXT','你想临时锁住哪张卡？请提供本人卡片的具体尾号或完整卡号，我不会根据不明确或已变化的记录替你选择。')
        return None
    trusted_intent = _intent_text(text)
    only_consult = bool(ONLY_CONSULT_PATTERN.search(trusted_intent))
    modal_body = polite_operational_body(trusted_intent) if not only_consult and trusted_intent == text else None
    classification = modal_body or text
    credential_help = bool(CREDENTIAL_PATTERN.search(text) and
        (CONSULT_PATTERN.search(classification) or CREDENTIAL_HELP_PATTERN.search(text) or only_consult))
    if re.fullmatch(r"好(?:的)?|确认|确定|同意|可以|办理吧|就这样|(?i:yes|yes please|okay|ok|confirm|confirmed|agreed|go ahead|proceed|do it|sure)", text) or (
            CREDENTIAL_PATTERN.search(text) and (turn.diagnostics_json.get("input_redacted") or not credential_help)):
        _finish(turn, "CONFIRM_HINT", "聊天中的确认或验证码不执行银行操作。请先查看实际任务的确认卡，核对范围并按要求完成二次验证。", task, "CURRENT" if task else None)
        return None
    proposals = rule_proposal(text).actions
    direct_read_kind = direct_owned_read_kind(text)
    direct_read_question = bool(not only_consult and not turn.reviewed_evidence_refs_json
        and direct_read_kind and len(proposals) == 1 and proposals[0].kind == direct_read_kind)
    from .goal_what_if import goal_what_if_requested
    if not only_consult and not turn.reviewed_evidence_refs_json and _intent_text(text) == text and len(proposals) == 1 and proposals[0].kind == "GOAL_VIEW" and goal_what_if_requested(text):
        direct_read_question = True  # Conditions can describe a read-only mathematical scenario, never a write.
    if modal_body and not (proposals or PURE_STATUS_QUESTION.fullmatch(modal_body)
            or TASK_REFERENCE_PATTERN.search(trusted_intent) or CONTINUE_PATTERN.search(modal_body)):
        classification = text  # An unproved object is not the old task's status.
    financial_consult = financial_inquiry(text) and bool(proposals) and all(action.kind in READ_KINDS for action in proposals)
    # Personal financial questions are real reads, not a shortcut which reads
    # private facts first and asks a model to explain them. New conversational
    # deployments send them through the same LLM→tool→narration workflow.
    from .customer_reply_jobs import enabled as conversational_results_enabled
    if financial_consult and conversational_results_enabled() and not only_consult and not turn.reviewed_evidence_refs_json:
        financial_consult = False
        direct_read_question = True
    explicit_write = any(action.kind not in READ_KINDS and any(
        _allowed_evidence(action.kind, clause) and not CONSULT_PATTERN.search(polite_operational_body(_intent_text(clause)) or _intent_text(clause))
        for clause in _clauses(text)) for action in proposals) and not only_consult
    # A complete rephrasing of the exact pending subject is not a second
    # banking request. History remains server-verified context; only this
    # complete current sentence supplies the new plan's write authority.
    if explicit_write and task and not ambiguous:
        from .owned_user_dialogue import continuation_candidate, RESTATEMENT_STATES
        if task.state not in RESTATEMENT_STATES:
            restatement = None
        else:
            restatement = continuation_candidate(session, principal, thread, turn, task, False,
                restatement=True)
        if restatement:
            if restatement.get("blocked_reason"):
                _finish(turn, "NEED_CONTEXT", "原请求的办理资料需要先核对，请先查看原请求结果；不会重复提交这笔转账。", task, "CURRENT")
                return None
            if turn.expected_task_version is not None and turn.expected_task_version != task.version:
                raise DomainError("TASK_VERSION_CHANGED", "关联任务版本已变化，请重新核对后提出新补充", 409)
            from .runtime import clarify_runtime
            continued = clarify_runtime(session, principal, task.id, text, task.version, revise=True)
            record = session.get(RuntimeRecord, continued.id)
            if restatement.get("unplanned_restatement_origin"):
                origin = {**restatement["unplanned_restatement_origin"], "revised_task_version": continued.version}
                restatement = {**restatement, "unplanned_restatement_origin": origin}
                turn.diagnostics_json = {**turn.diagnostics_json, "unplanned_restatement_origin": origin}
            record.metadata_json = {**record.metadata_json, "reply_language": _turn_language(turn),
                **({"pending_subject_selection": turn.diagnostics_json["pending_subject_selection"]} if turn.diagnostics_json.get("pending_subject_selection") else {}),
                "owned_user_dialogue": restatement, "conversation_context": {
                    "conversation_id": thread.id, "turn_id": turn.id,
                    "original_user_message": text, "authority": "OWNED_CURRENT_REQUEST_RESTATEMENT"}}
            thread.current_task_id, thread.version = continued.id, thread.version + 1
            _finish(turn, "REVISE", "我按你这次的说明继续准备这笔转账，已经明确的信息会保留。", continued, "CONTINUED")
            return None
    wealth_amount_reply = None
    wealth_units_reply = None
    quantity_replacement = None
    if task and not ambiguous and task.state in {"NEED_CLARIFICATION", "AWAITING_CONFIRMATION"} and (turn.requested_task_id == task.id or thread.current_task_id == task.id):
        quantity_replacement = _short_quantity_replacement(task, text, turn.id)
        if quantity_replacement:
            quantity_replacement = _quantity_origin_proof(session, principal, thread, turn, task, quantity_replacement)
            if not quantity_replacement:
                _finish(turn, "NEED_CONTEXT", "The original request source or permissions have changed. Restate the product and amount; the old confirmation does not authorize this change. No banking operation was performed."
                    if _turn_language(turn) == "en" else "原请求的来源或权限已变化，请重新明确产品和金额。旧确认不代表本次修改；这次没有办理银行操作。")
                return None
        if quantity_replacement:
            explicit_write = False
    if (task and task.state == "NEED_CLARIFICATION" and not ambiguous
            and (turn.requested_task_id == task.id or thread.current_task_id == task.id)
            and len(proposals) == 1 and proposals[0].kind in {"WEALTH_BUY", "WEALTH_REDEEM"}):
        wealth_units_reply = _short_wealth_units_continuation(task, text, turn.id)
        wealth_amount_reply = _short_continuation(task, text, turn.id) if not wealth_units_reply and set(proposals[0].slots.model_dump(exclude_none=True)) <= {"amount_minor", "currency", "units"} else None
        if wealth_amount_reply or wealth_units_reply:
            explicit_write = False
    if task and not explicit_write and (turn.requested_task_id == task.id or thread.current_task_id == task.id):
        wealth_units_reply = wealth_units_reply or _short_wealth_units_continuation(task, text, turn.id)
    cancel_words = bool(CANCEL_WORD_PATTERN.search(_intent_text(text)))
    cancel_control = any(CANCEL_CONTROL_PATTERN.search((polite_operational_body(_intent_text(clause)) or _intent_text(clause)).strip()) for clause in _clauses(text)) and not only_consult
    cancel_help = cancel_words and (only_consult or bool(CANCEL_HELP_PATTERN.search(classification))) and not cancel_control
    consultation = only_consult or (financial_consult or (bool(CONSULT_PATTERN.search(classification)) and not direct_read_question) or credential_help or cancel_help or bool(turn.reviewed_evidence_refs_json)) and not explicit_write
    if semantic_read_kind in READ_KINDS and not only_consult and not turn.reviewed_evidence_refs_json and not credential_help and not explicit_write:
        consultation = False  # Classification still has no slots or write authority.
    if cancel_words and not consultation and (cancel_control or not explicit_write):
        if ambiguous or task is None:
            answer = "请先选择要撤销的具体办理记录，再查看该记录当前可用的操作。聊天本身没有撤销任何银行业务。"
            task = None
        elif task.state in IN_FLIGHT:
            answer = "这笔业务正在办理或结果尚未确定，请先查看原请求的进度和回执，不要重复付款。聊天本身不会撤销、回滚或重发业务。"
        else:
            from .runtime import task_trace
            available = task_trace(session, principal, task.id).get("allowed_actions", [])
            answer = ("这笔业务当前有撤销入口，请打开关联办理记录，核对状态后使用撤销按钮；入口仅处理未完成步骤，已完成的回执会保留。" if "cancel" in available
                else "这笔业务当前没有可用的撤销入口，请查看办理结果或联系服务支持，已有回执会保留。")
            answer += "聊天本身没有撤销或回滚任何银行业务。"
        _finish(turn, "CANCEL_HINT", answer, task, "CURRENT" if task else None)
        return None
    if consultation and not TASK_REFERENCE_PATTERN.search(trusted_intent):
        from .task_dialogue_context import task_context, relevant_context
        context = task_context(session, principal, task) if task else None
        if not relevant_context(text, context, independent=turn.context_policy == "INDEPENDENT"):
            task = None  # General explanation never inherits an unrelated bank link.
    resource_read = bool(proposals) and all(action.kind in READ_KINDS for action in proposals) \
        and not re.search(r"任务|刚才|办理进度|付款结果|这笔|这项|那笔", text)
    pure_status = bool(PURE_STATUS_QUESTION.fullmatch(classification))
    # A progress question without an explicit old selection refers to the
    # latest displayed request, not an unrelated older unfinished task.
    if pure_status and turn.context_policy == "AUTO" and not turn.requested_task_id and thread.current_task_id:
        latest = owned_task(session, principal, thread.current_task_id)
        from .task_dialogue_context import task_context
        latest_context = task_context(session, principal, latest)
        latest_read = bool(latest_context and (latest_context.get("unresolved_read_request") or latest_context.get("read_candidates")))
        if not ambiguous or latest_read:
            task, ambiguous = latest, False
    if pure_status and (ambiguous or task is None):
        _finish(turn, "NEED_CONTEXT", "你想查看哪一笔业务的进度？请指定对应办理记录；这次不会新建付款或其他银行操作。")
        return None
    if not pure_status and not consultation and not explicit_write and STATUS_PATTERN.search(text) and re.search(
            r"(?:给|向)[^，,。；;]{1,84}(?:转|汇|打)|(?:转[账帐]给|转给|汇给|打给)", _intent_text(text)):
        _finish(turn, "NEED_CONTEXT", "你是在查询这笔指定业务的进度，还是提出新的转账需求？请明确对应办理记录或新需求；这次不会查询无关任务，也不会新建付款。")
        return None
    if (pure_status or STATUS_PATTERN.search(text)) and task and (pure_status or not CONSULT_PATTERN.search(classification)) and not resource_read and not explicit_write:
        _finish_status(session, principal, turn, task)
        return None
    recipient_fragment = bool((_recipient_match(text) and not explicit_write) or re.match(r"^(?:收款人|收款账户|收款账号|recipient\b)", text, re.I)
        or (re.match(r"^(?:给|向)", text) and not proposals))
    memo_fragment = bool(re.match(r"^(?:备注|无需备注|不要备注|不加备注|无备注)", text) and not explicit_write)
    fragment = bool(quantity_replacement or wealth_amount_reply or wealth_units_reply or memo_fragment or recipient_fragment or CONTINUE_PATTERN.search(classification) or _continuation_money(text, "CNY") or _amount_like_fragment(text)
        or re.fullmatch(r"[\d零〇一二两三四五六七八九十百千万亿]+(?:[.点][\d零〇一二两三四五六七八九]{1,2})?\s*(?:元|人民币|美元)?", text))
    # Keep established exact money/recipient/memo contracts. Other short object
    # or time replies use server-owned user utterances, not a fabricated command.
    legacy_context_allowed = bool(task and (turn.requested_task_id == task.id or thread.current_task_id == task.id
        or turn.diagnostics_json.get("dialogue_focus_selection", {}).get("task_id") == task.id))
    legacy_continuation = legacy_context_allowed and fragment and (
        quantity_replacement or wealth_units_reply or (_short_memo_continuation(task, text, turn.id) if memo_fragment else None)
        or (_short_recipient_continuation(task, text, turn.id) if recipient_fragment else None)
        or _short_continuation(task, text, turn.id))
    if task and not ambiguous and turn.context_policy == "AUTO" and not consultation and not direct_read_kind and not (
            any(action.kind in READ_KINDS for action in proposals) and re.search(r"查|看|分析|(?i:check|show|read|list)",text)) and (
            not explicit_write or re.match(r"^(?:把.{0,30})?(?:改成|改为|改到|调整为|换成|金额|限额)",text)
            or (turn.diagnostics_json.get("dialogue_focus_selection") or {}).get("reason")=="NAMED_RESUME"):
        from .dialogue_slot_updates import build_slot_update, METADATA_KEY
        existing_record = session.get(RuntimeRecord, task.id)
        from .runtime_models import PlanSnapshot
        current_snapshot = session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==task.id,
            PlanSnapshot.version==existing_record.plan_version)) if existing_record else None
        prior_kinds={action.get("kind") for action in (current_snapshot.proposal_json or {}).get("actions",[])} if current_snapshot else set()
        if not legacy_continuation or (existing_record.metadata_json or {}).get(METADATA_KEY) or prior_kinds-{ "TRANSFER", "FX_TRANSFER", "WEALTH_BUY", "WEALTH_REDEEM" }:
            binding = build_slot_update(session, principal, thread, turn, task)
            if binding:
                from .runtime import clarify_runtime
                if turn.expected_task_version is not None and turn.expected_task_version != task.version:
                    raise DomainError("TASK_VERSION_CHANGED", "这项需求已变化，请核对最新方案。", 409)
                continued = clarify_runtime(session, principal, task.id, text, task.version, revise=True)
                record = session.get(RuntimeRecord, continued.id)
                metadata = {key:value for key,value in record.metadata_json.items()
                    if key not in {"owned_user_dialogue", "pending_subject_selection", "conversation_context"}}
                record.metadata_json = {**metadata, METADATA_KEY:binding, "reply_language":_turn_language(turn),
                    "conversation_context":{"conversation_id":thread.id,"turn_id":turn.id,
                        "original_user_message":text,"authority":"VERIFIED_PER_ACTION_USER_FIELDS"}}
                thread.current_task_id, thread.version = continued.id, thread.version + 1
                _finish(turn,"REVISE","好的，我会保留前面说清的信息，按这次补充重新准备方案。",continued,"CONTINUED")
                turn.diagnostics_json = {**turn.diagnostics_json,"per_action_fields_retained":True}
                return None
    from .owned_user_dialogue import continuation_candidate, REQUERY
    if not consultation and not explicit_write and not legacy_continuation and (REQUERY.fullmatch(text) or not re.search(
            r"查询|查看|查一下|看看|分析|(?i:check|show|read)", text)):
        if semantic_read_kind is None and turn.context_policy == "AUTO" and thread.current_task_id:
            from .task_dialogue_context import task_context, selected_read_kind
            context = task_context(session, principal, owned_task(session, principal, thread.current_task_id))
            selected = selected_read_kind(text, context)
            if selected and any(row.get("origin") == "VERIFIED_READ_RESULT" and row.get("action_kind") == selected and row.get("value") in text
                    for row in (context or {}).get("entities", [])):
                semantic_read_kind = selected
        dialogue = continuation_candidate(session, principal, thread, turn, task, ambiguous, semantic_read_kind=semantic_read_kind)
        if dialogue:
            if dialogue.get("blocked_reason"):
                answer=_minimal_ambiguous_question(session,principal,thread,turn) if dialogue["blocked_reason"]=="AMBIGUOUS" else (
                    "This conversation reached its context limit. Please identify the request to continue." if _turn_language(turn)=="en" else "这项需求的历史补充较多，请告诉我这次想继续的具体事项。")
                _finish(turn,"NEED_CONTEXT",answer)
                turn.answer=answer
                return None
            from .runtime import accept_task, clarify_runtime
            if dialogue["reuse_task_id"]:
                origin = owned_task(session, principal, dialogue["reuse_task_id"])
                if turn.expected_task_version is not None and turn.expected_task_version != origin.version:
                    raise DomainError("TASK_VERSION_CHANGED", "关联任务版本已变化，请重新核对后提出新补充", 409)
                accepted = clarify_runtime(session, principal, origin.id, text, origin.version, revise=True)
                mode, relationship = "REVISE", "CONTINUED"
            else:
                accepted = accept_task(session, principal, text, "chat:" + turn.id)
                mode, relationship = "NEW_TASK", "CREATED"
            record = session.get(RuntimeRecord, accepted.id)
            record.metadata_json = {**record.metadata_json, "reply_language": _turn_language(turn),
                **({"pending_subject_selection": turn.diagnostics_json["pending_subject_selection"]} if turn.diagnostics_json.get("pending_subject_selection") else {}),
                "owned_user_dialogue": dialogue,
                "conversation_context": {"conversation_id": thread.id, "turn_id": turn.id,
                    "original_user_message": text, "authority": "OWNED_USER_DIALOGUE"}}
            thread.current_task_id, thread.version = accepted.id, thread.version + 1
            _finish(turn, mode, "已将本条补充与本会话原请求一起交给模型理解；未执行银行写操作，原确认不复用。", accepted, relationship)
            return None
    if fragment and not consultation:
        if task and not legacy_context_allowed:
            _finish(turn, "NEED_CONTEXT", "这次补充是指刚才的查询，还是之前未完成的办理需求？请告诉我是哪一项，已有信息会保留。", None)
            return None
        if ambiguous or task is None or task.state in TERMINAL or task.state in IN_FLIGHT:
            _finish(turn, "NEED_CONTEXT", "请明确要继续的唯一未完成任务。已完成或正在执行、结果未知的任务不能用短句重复付款；结果未知请先查单。", task, "CURRENT" if task else None)
            return None
        completed_write = any(op.status == "SUCCEEDED" or (op.child_task_id and
            (child := session.get(Task, op.child_task_id)) and child.state == "SUCCEEDED")
            for op in session.scalars(select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task.id,
                RuntimeOperation.action_kind.not_in(READ_KINDS))))
        if completed_write:
            _finish(turn, "NEED_CONTEXT", "原任务已有办理完成的银行操作，原回执会保留。请先查看原请求结果；新的业务请明确提出独立需求，不能用短句修改或重做已办理的操作。", task, "CURRENT")
            return None
        if turn.expected_task_version is not None and turn.expected_task_version != task.version:
            raise DomainError("TASK_VERSION_CHANGED", "关联任务版本已变化，请重新核对后提出新补充", 409)
        continuation = quantity_replacement or wealth_units_reply or (_short_memo_continuation(task, text, turn.id) if memo_fragment else None)
        continuation = continuation or (_short_recipient_continuation(task, text, turn.id) if recipient_fragment else None)
        continuation = continuation or _short_continuation(task, text, turn.id)
        if not continuation:
            answer = "请为唯一未完成转账提供80字以内、引号闭合的完整备注，或明确无需备注；金额和收款人已保留。" if memo_fragment else _continuation_question(task, text)
            _finish(turn, "NEED_CONTEXT", answer, task, "CURRENT")
            return None
        resolved, fields, contract = continuation
        from .runtime import clarify_runtime
        continued = clarify_runtime(session, principal, task.id, resolved, task.version, revise=True)
        record = session.get(RuntimeRecord, task.id)
        # This exact source-bound field replacement has its own provenance
        # contract. An older dialogue proof names the previous turn/message
        # and must not be reused for this revised input.
        metadata = {key: value for key, value in record.metadata_json.items() if key != "owned_user_dialogue"}
        record.metadata_json = {**metadata, "reply_language": _turn_language(turn), "conversation_context": {
            "conversation_id": thread.id, "turn_id": turn.id, "original_user_message": text,
            "resolved_message_hash": canonical_hash({"message": resolved}), "field_sources": fields,
            "continuation_contract": contract, "authority": "SERVER_BOUND_USER_CONTEXT_NOT_BANK_FACTS"}}
        if turn.diagnostics_json.get("pending_subject_selection"):
            record.metadata_json = {**record.metadata_json,
                "pending_subject_selection": turn.diagnostics_json["pending_subject_selection"]}
        thread.current_task_id, thread.version = continued.id, thread.version + 1
        _finish(turn, "REVISE", "已根据本会话明确原请求与当前补充生成新版本计划。原确认不复用，请等待新确认卡并重新核对。", continued, "CONTINUED", fields)
        return None
    if consultation:
        turn.diagnostics_json = {**turn.diagnostics_json, "legacy_credential_help": credential_help}
        return task, _sources_for(session, principal, thread, turn, task)
    is_write = any(action.kind not in READ_KINDS for action in proposals)
    proved_readonly = bool(proposals) and all(action.kind in READ_KINDS for action in proposals)
    if any(owned_task(session, principal, identifier).state in IN_FLIGHT for identifier in _linked_task_ids(session, thread)) and not proved_readonly:
        _finish(turn, "NEED_CONTEXT", "当前会话有正在执行或结果未知的业务。请先查原任务结果，不盲目重发或新建资金操作。", task, "CURRENT" if task else None)
        return None
    from .runtime import accept_task
    accepted = accept_task(session, principal, text, "chat:" + turn.id)
    record = session.get(RuntimeRecord, accepted.id)
    record.metadata_json = {**record.metadata_json, "reply_language": _turn_language(turn), "conversation_context": {"conversation_id": thread.id,
        "turn_id": turn.id, "original_user_message": text, "authority": "USER_CURRENT",
        "reviewed_evidence_refs": turn.reviewed_evidence_refs_json}}
    thread.current_task_id, thread.version = accepted.id, thread.version + 1
    _finish(turn, "NEW_TASK", "已受理你的原话并创建独立任务。银行操作尚未执行，涉及资金或授权时仍需核对确认。", accepted, "CREATED")
    return None


from .conversation_turn_state import _turn_principal


def process_chat_job(factory, gateway, worker_id=None):
    """Compatibility export for the explicit production/legacy dispatcher."""
    from .conversation_dispatch import process_chat_job as dispatch
    return dispatch(factory, gateway, worker_id)


def _process_legacy_chat_turn(factory, gateway, leased, identity):
    """Offline compatibility path; never used after a production failure."""
    consultation = None
    dialogue = None
    try:
        with factory.begin() as session:
            turn = session.get(ChatTurn, leased)
            thread = session.scalar(select(ChatThread).where(ChatThread.id == turn.thread_id).with_for_update())
            if thread.status != "ACTIVE" or turn.status != "LEASED" or turn.lease_owner != identity:
                return True
            principal = _turn_principal(session, turn)
            role = require_banking_actor(session, principal)
            from .dialogue_brain import eligible_for_dialogue
            from .public_subscription_price_inquiry import public_subscription_price_request
            public_price_only = bool(public_subscription_price_request(turn.message) and not turn.reviewed_evidence_refs_json and not turn.diagnostics_json.get("input_redacted"))
            active_goal = None if public_price_only else _task_context(session, principal, thread, turn)[0]
            from .task_dialogue_context import task_context, relevant_context, bounded_history, result_entity_reply
            latest_goal = owned_task(session, principal, thread.current_task_id) if not public_price_only and thread.current_task_id and turn.context_policy == "AUTO" else None
            if latest_goal and not _task_epoch_current(session,principal,latest_goal):
                latest_goal=None
            context = task_context(session, principal, latest_goal) if latest_goal else None
            contextual_read = result_entity_reply(turn.message, context)
            from .dialogue_reference_integration import build_result_reference_binding
            if not public_price_only and _result_reference_candidate(session,principal,thread,turn):
                contextual_read=True
            from .owned_user_dialogue import result_followup_candidate
            if latest_goal and result_followup_candidate(session, principal, thread, turn):
                contextual_read = True  # Still goes through the LLM bank planner, tool and narration.
            pure_status = bool(PURE_STATUS_QUESTION.fullmatch(turn.message.strip()))
            from .lifecycle_dialogue import lifecycle_candidate
            lifecycle = lifecycle_candidate(turn.message) if not turn.diagnostics_json.get("credential_only") and not turn.diagnostics_json.get("input_redacted") and not pure_status else None
            lifecycle_context, lifecycle_task = _lifecycle_resolution(session, principal, thread, turn, lifecycle) if lifecycle else (None, None)
            guidance_allowed = eligible_for_dialogue(turn.message, has_pending_goal=bool(active_goal and active_goal.state not in TERMINAL and not (context and context.get("read_candidates"))),
                    credential_redacted=bool(turn.diagnostics_json.get("input_redacted")))
            if _DECLINE_SUBSCRIPTION_PURCHASE.fullmatch(turn.message) and _intent_text(turn.message)==turn.message:
                guidance_allowed=False
            from .public_subscription_price_inquiry import public_subscription_price_request
            if public_subscription_price_request(turn.message) and not turn.reviewed_evidence_refs_json and not turn.diagnostics_json.get("input_redacted"):
                guidance_allowed=False
            if _pending_transfer_control_kind(turn.message) and turn.context_policy=="AUTO" and not turn.reviewed_evidence_refs_json:
                guidance_allowed=False  # Typed user controls invalidate the old grant before any inference.
            if _pending_family_scope_binding(session,principal,thread,turn,active_goal):
                guidance_allowed=False
            from .fraud_security_dialogue import reported_security_question, family_permission_question
            from .contextual_safety_dialogue import contextual_safety_consultation
            if contextual_safety_consultation(session,principal,thread,turn) or reported_security_question(turn.message,credential_only=bool(turn.diagnostics_json.get('credential_only'))) or _family_consult_candidate(session,principal,thread,turn):
                lifecycle, lifecycle_context = None, None
                guidance_allowed = False
            if lifecycle or not contextual_read and not pure_status and guidance_allowed:
                from .capability_catalog import customer_directory, suggestion_catalog
                from .knowledge_runtime import directory_source
                from .model_routing import read_routing_policy
                locale = _turn_language(turn)
                history = [] if turn.context_policy == "INDEPENDENT" or lifecycle and not lifecycle_context.get("current_allowed") else bounded_history(session, principal, thread, turn)
                from .product_knowledge import knowledge_sources
                records = [directory_source(role, locale)] + knowledge_sources(turn.message)
                from .product_knowledge import subscription_catalog_source
                catalog_record=subscription_catalog_source(session,turn.message)
                if catalog_record:records.append(catalog_record)
                current_context = task_context(session, principal, lifecycle_task) if lifecycle_task else None if lifecycle else context if relevant_context(turn.message, context) else None
                dialogue = (turn.message, role, locale, customer_directory(role, locale), suggestion_catalog(role, locale),
                    records, history, read_routing_policy(session), current_context, lifecycle_context)
                repeated = bool(turn.diagnostics_json.get("dialogue_cloud_reserved"))
                turn.diagnostics_json = {**turn.diagnostics_json, "dialogue_cloud_reserved": True}
            else:
                consultation = _process_policy(session, principal, thread, turn)
            if consultation:
                task, records = consultation
                history = [] if turn.context_policy == "INDEPENDENT" else bounded_history(session, principal, thread, turn)
                from .model_routing import read_routing_policy
                policy = read_routing_policy(session)
                snapshot = (turn.message, records, history, policy, source_fingerprint(records), task.id if task else None, _turn_language(turn))
                reserved = turn.diagnostics_json.get("consult_cloud_reserved", False)
                turn.diagnostics_json = {**turn.diagnostics_json, "consult_cloud_reserved": True}
        if dialogue:
            from .dialogue_brain import decide_dialogue, _fallback, DialogueResult
            from .knowledge_runtime import retrieve_dialogue_knowledge
            from .customer_capability_data import CUSTOMER_DIRECTORY_VERSION
            message, role, locale, directory, suggestions, records, history, policy, context, lifecycle_context = dialogue
            retrieved, retrieval_meta = retrieve_dialogue_knowledge(message, role)
            records += retrieved
            result = (DialogueResult(_fallback(message, role, directory, suggestions, locale, records, context, lifecycle_context),
                {"fallback": True, "model_api_call_count": None, "failure_reasons": ["DIALOGUE_PREVIOUS_OUTCOME_UNKNOWN_NO_REISSUE"]})
                if repeated else decide_dialogue(gateway, message, role=role, directory=directory, suggestions=suggestions,
                    records=records, history=history, policy=policy, locale=locale, current_task_context=context, lifecycle_context=lifecycle_context))
            with factory.begin() as session:
                turn = session.get(ChatTurn, leased)
                thread = session.scalar(select(ChatThread).where(ChatThread.id == turn.thread_id).with_for_update())
                if thread.status != "ACTIVE" or turn.status != "LEASED" or turn.lease_owner != identity:
                    return True
                principal = _turn_principal(session, turn)
                actual_role = require_banking_actor(session, principal)
                # Guidance is a separate model purpose, including REQUEST
                # classifications. Never overwrite planner/reply diagnostics.
                turn.diagnostics_json = {**turn.diagnostics_json, "dialogue_guidance": result.diagnostics}
                if actual_role != role or not knowledge_sources_current(records, actual_role):
                    _finish(turn, "KNOWLEDGE", "The available information changed. Please ask again to use the current sources."
                        if locale == "en" else "相关资料或当前权限已变化，请重新提问，我会按最新资料回答。")
                    turn.suggestions_json, turn.response_meta_json = [], {}
                    turn.diagnostics_json = {**turn.diagnostics_json, "dialogue_source_changed": True}
                    return True
                if lifecycle_context:
                    from .lifecycle_dialogue import lifecycle_candidate
                    fresh_candidate = lifecycle_candidate(turn.message)
                    selection = result.decision.lifecycle_target if result.decision.kind == "STATUS" else "CLARIFY"
                    # An uncertain model classification cannot turn this
                    # lifecycle question into a NEW_TASK or retry write.
                    _finish_lifecycle(session, principal, thread, turn, fresh_candidate or {"allow_current": False, "reason": "SOURCE_CHANGED"}, selection)
                    turn.diagnostics_json = {**turn.diagnostics_json, "bank_task_created": False,
                        "bank_write_authorized": False, "lifecycle_intent": True}
                    return True
                if context:
                    from .task_dialogue_context import task_context, context_fingerprint
                    current_task = owned_task(session, principal, context["task_id"])
                    if thread.current_task_id != context["task_id"] or context_fingerprint(task_context(session, principal, current_task)) != context_fingerprint(context):
                        _finish(turn, "KNOWLEDGE", "The request changed while I was preparing this reply. Please check its latest result." if locale == "en" else "刚才的业务信息已经变化，请先查看最新结果，再继续告诉我你的需求。")
                        turn.diagnostics_json = {**turn.diagnostics_json, "dialogue_context_changed": True}
                        return True
                if result.decision.kind == "REQUEST":
                    # Classification supplies no slots or authorization. Original
                    # user text still goes through the existing safe bank policy.
                    consultation = _process_policy(session, principal, thread, turn, semantic_read_kind=result.decision.contextual_read_kind)
                    if consultation:
                        task, consult_records = consultation
                        snapshot = (turn.message, consult_records, history, policy, source_fingerprint(consult_records),
                            task.id if task else None, locale)
                        reserved = bool(turn.diagnostics_json.get("consult_cloud_reserved"))
                        turn.diagnostics_json = {**turn.diagnostics_json, "consult_cloud_reserved": True}
                else:
                    mode = "CONSULT" if result.decision.kind in {"KNOWLEDGE", "ADVICE", "PRODUCTS"} else result.decision.kind
                    answer = result.decision.answer
                    if mode == "CONSULT":
                        answer += "\n\nThis reply explains the available information; it did not execute a bank operation." if locale == "en" else "\n\n本轮仅解释，未执行银行操作。"
                    _finish(turn, mode, answer, sources=records)
                    selected = [suggestions[identifier] for identifier in dict.fromkeys(result.decision.suggestion_ids)
                        if identifier in suggestions][:5]
                    turn.suggestions_json = selected
                    from .dialogue_brain import PRODUCTS, HELP
                    product_panel = result.decision.kind == "PRODUCTS" or (
                        result.decision.kind in {"KNOWLEDGE", "ADVICE"} and bool(PRODUCTS.search(message) and HELP.search(message)))
                    turn.response_meta_json = {"suggestion_source": "CUSTOMER_CAPABILITY_CATALOG",
                        "suggestion_catalog_version": CUSTOMER_DIRECTORY_VERSION, "suggestion_role": role,
                        "dialogue_kind": result.decision.kind, "product_panel": product_panel,
                        "preserve_goal_context": result.decision.kind in {"DISCOVERY", "GREETING", "FEEDBACK"},
                        **retrieval_meta}
                    turn.diagnostics_json = {**turn.diagnostics_json, **result.diagnostics, **retrieval_meta,
                        "bank_task_created": False, "bank_write_authorized": False,
                        "preserve_goal_context": result.decision.kind in {"DISCOVERY", "GREETING", "FEEDBACK"},
                        "preserve_read_goal_context": bool(context and mode == "CONSULT" and context.get("action_kinds") and all(kind in READ_KINDS for kind in context["action_kinds"])),
                        "read_goal_task_id": context["task_id"] if context and mode == "CONSULT" else None}
        if consultation:
            from .consultation_gateway import explain
            from .knowledge_runtime import retrieve_dialogue_knowledge
            message, records, history, policy, fingerprint, task_id, reply_language = snapshot
            from .product_knowledge import knowledge_sources
            family_policy = next((row for row in knowledge_sources("家庭委托授权家人")
                                  if row["source_id"] == "kb:family.scope-v1"), None)
            family_policy_only = (turn.diagnostics_json.get("family_permission_consultation") is True
                                  and task_id is None and family_policy is not None and records == [family_policy]
                                  and source_fingerprint(records) == fingerprint)
            typed_scoped_only = (bool(turn.diagnostics_json.get("contextual_safety_source") or turn.diagnostics_json.get("dialogue_reference_explanation"))
                and bool(records) and source_fingerprint(records)==fingerprint
                and all(row.get("kind") in {"SCOPED_BUSINESS_FACTS","PRODUCT_KNOWLEDGE"} for row in records))
            if family_policy_only or typed_scoped_only:
                # This established consultation already has the whole current
                # public policy; unrelated index fragments add no scope facts.
                # Explanation still uses its existing cloud/fallback path.
                retrieved, retrieval_meta = [], {"retrieval_mode": "SCOPED_POLICY",
                    "knowledge_status": "PUBLIC_POLICY_SCOPE_ONLY"}
            else:
                # Other consultations keep general public RAG enrichment.
                retrieved, retrieval_meta = retrieve_dialogue_knowledge(message, role)
                records = records + retrieved
            if reserved:
                answer, diagnostics = answer_from_sources(records, reply_language=reply_language), {"purpose": "CHAT_CONSULTATION", "actual_provider": "server_knowledge",
                    "model_api_call_count": None, "fallback": True, "failure_reasons": ["CONSULT_PREVIOUS_OUTCOME_UNKNOWN_NO_REISSUE"]}
            else:
                from .consultation_fallback import verified_legacy_family_kwargs
                from .legacy_consultation_facts import verified_legacy_public_kwargs
                legacy_scope = verified_legacy_family_kwargs(records,family_policy_only) or verified_legacy_public_kwargs(
                    message,records,credential_help=turn.diagnostics_json.get('legacy_credential_help') is True)
                answer, diagnostics = explain(gateway, message, records, history, policy, reply_language=reply_language,
                    **legacy_scope)
            with factory.begin() as session:
                turn = session.get(ChatTurn, leased)
                thread = session.scalar(select(ChatThread).where(ChatThread.id == turn.thread_id).with_for_update())
                if thread.status != "ACTIVE" or turn.status != "LEASED" or turn.lease_owner != identity:
                    return True
                principal = _turn_principal(session, turn)
                current_role = require_banking_actor(session, principal)
                task = owned_task(session, principal, task_id) if task_id else None
                fresh = _sources_for(session, principal, thread, turn, task)
                if source_fingerprint(fresh) != fingerprint:
                    answer, records = answer_from_sources(fresh, reply_language=reply_language), fresh
                    diagnostics = {**diagnostics, "context_changed_after_inference": True, "fallback": True}
                elif current_role != role or not knowledge_sources_current(retrieved, current_role):
                    answer, records = answer_from_sources(fresh, reply_language=reply_language), fresh
                    diagnostics = {**diagnostics, "knowledge_changed_after_inference": True, "fallback": True}
                _finish(turn, "CONSULT", answer, task, "CURRENT" if task else None, sources=records)
                turn.response_meta_json = {**turn.response_meta_json, **retrieval_meta}
                from .task_dialogue_context import task_context
                context = task_context(session, principal, task) if task else None
                preserve_read = bool(context and context.get("action_kinds") and all(kind in READ_KINDS for kind in context["action_kinds"]))
                turn.diagnostics_json = {**turn.diagnostics_json, **diagnostics, **retrieval_meta,
                    "preserve_read_goal_context": preserve_read, "read_goal_task_id": task.id if preserve_read else None}
    except DomainError as error:
        with factory.begin() as session:
            turn = session.get(ChatTurn, leased)
            if turn and turn.status == "LEASED" and turn.lease_owner == identity:
                if error.code in {'DIALOGUE_REFERENCE_SOURCE_CHANGED','CONVERSATION_SCOPE_CHANGED'}:
                    _finish(turn,'NEED_CONTEXT',
                        'The previous source or permissions have changed. Please select the current object again; no new banking action was prepared.'
                        if _turn_language(turn)=='en' else '前面查询的对象、来源或权限已变化，请重新选择当前对象；没有准备新的银行操作。')
                    turn.diagnostics_json={**turn.diagnostics_json,'source_unavailable_code':error.code,
                        'bank_task_created':False,'bank_write_authorized':False}
                else:
                    turn.status, turn.error_code, turn.answer, turn.completed_at = "FAILED", error.code, ("This request could not be completed. Review its current details and try the supported action; no successful bank operation is implied." if _turn_language(turn) == "en" else error.message), utcnow()
    except Exception:
        with factory.begin() as session:
            turn = session.get(ChatTurn, leased)
            if turn and turn.status == "LEASED" and turn.lease_owner == identity:
                turn.status, turn.error_code, turn.answer, turn.completed_at = "FAILED", "CHAT_PROCESSING_FAILED", ("This reply could not be completed. Keep the original request identifier and check the linked request; do not repeat a payment." if _turn_language(turn) == "en" else "本轮暂未完成，请保留原请求标识并查看关联任务；不要重复付款。"), utcnow()
    return True
