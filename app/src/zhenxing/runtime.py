from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import append_audit, verify_audit_chain
from .authentication import as_utc, consume_auth, requires_step_up
from .contracts import ConfirmTaskRequest, DomainError
from .intelligence import READ_KINDS
from .model_gateway import IntentDecision, ModelGateway
from .models import AuditEvent, Card, Confirmation, EvidenceSnapshot, InvestmentProduct, ModelInvocation, OutboxMessage, PolicyDecisionRecord, Task, ToolCall, User, utcnow
from .planning import canonical_hash
from .runtime_models import AuthChallenge, PlanSnapshot, RuntimeOperation, RuntimeRecord, RuntimeTaskRequest, WorkflowJob
from .services import Principal, confirm_task_action, create_task, owned_task, task_view
from .capability_catalog import action_intent
from .permissions import actor_role


ACTION_INTENTS = {
    "TRANSFER_SCHEDULE": "TRANSFER_SCHEDULE", "AA_REQUEST": "AA_REQUEST",
    "BILL_ANALYSIS": "BILL_ANALYSIS", "CARD_STATUS": "CARD_MANAGEMENT",
    "CARD_LOCK": "CARD_MANAGEMENT", "CARD_UNLOCK": "CARD_MANAGEMENT",
    "CARD_REPORT_LOSS": "CARD_MANAGEMENT", "CARD_REPLACEMENT": "CARD_MANAGEMENT",
    "CARD_SET_LIMIT": "CARD_MANAGEMENT", "TRANSFER": "TRANSFER",
    "WEALTH_COMPARE": "WEALTH", "WEALTH_BUY": "WEALTH", "WEALTH_REDEEM": "WEALTH",
    "SUBSCRIPTION_LIST": "SUBSCRIPTION_MANAGEMENT", "SUBSCRIPTION_CANCEL": "SUBSCRIPTION_MANAGEMENT",
    "SUBSCRIPTION_START": "SUBSCRIPTION_MANAGEMENT", "SUBSCRIPTION_RENEW": "SUBSCRIPTION_MANAGEMENT",
}
ACTION_INTENTS.update({kind: action_intent(kind) for kind in (
    "FRAUD_ANALYZE", "FAMILY_VIEW", "FAMILY_GRANT", "FAMILY_REVOKE", "FAMILY_PROPOSE",
    "GOAL_VIEW", "GOAL_CREATE", "GOAL_RESERVE", "GOAL_RELEASE", "CARBON_VIEW", "CARBON_CLAIM", "CARBON_REDEEM",
)})
logger = logging.getLogger(__name__)
ACTION_INTENTS.update({"FX_QUOTE": "FOREX_QUERY", "FX_TRANSFER": "FOREX_TRANSFER", "FX_EXCHANGE": "FOREIGN_EXCHANGE"})
ACTION_INTENTS["CONTACT_BIND"] = "CONTACT_MANAGEMENT"
ACTION_INTENTS.update({"ASSET_POSITIONS_READ": "WEALTH", "MEMBERSHIP_VIEW": "CUSTOMER_BENEFITS",
    "BENEFITS_VIEW": "CUSTOMER_BENEFITS", "PREFERENCE_VIEW": "CUSTOMER_PREFERENCES",
    "PREFERENCE_SET": "CUSTOMER_PREFERENCES", "PREFERENCE_REMOVE": "CUSTOMER_PREFERENCES"})
from .upgraded_capabilities import CAPABILITIES as UPGRADED_CAPABILITIES
ACTION_INTENTS.update({kind: value[2] for kind, value in UPGRADED_CAPABILITIES.items()})


def trusted_family_context(session: Session, principal: Principal) -> dict | None:
    """Only an authenticated server snapshot may start work for a bound proxy."""
    from .family_account_context import validate_family_context_actor
    supplied = getattr(principal, "family_context", None)
    current = validate_family_context_actor(session, principal.sandbox_id, principal.user_id,
        expected_context=supplied)
    if current is not None and supplied is None:
        raise DomainError("FAMILY_CONTEXT_INVALID", "请从自己的账户重新进入家庭协助", 401)
    return dict(current) if current is not None else None


def validate_runtime_family_context(session: Session, task: Task) -> dict | None:
    """Pin queued work to its original helper session, not a later replacement."""
    from .family_account_context import validate_family_context_actor
    record = session.get(RuntimeRecord, task.id)
    if record and record.root_task_id:
        task = session.get(Task, record.root_task_id)
        record = session.get(RuntimeRecord, task.id) if task else None
    if task is None:
        raise DomainError("FAMILY_CONTEXT_INVALID", "家庭协助任务来源已失效", 401)
    proof = (record.metadata_json or {}).get("family_context_origin") if record else None
    current = validate_family_context_actor(session, task.sandbox_id, task.user_id,
        expected_context=proof)
    if current is not None and proof is None:
        raise DomainError("FAMILY_CONTEXT_INVALID", "家庭协助任务缺少有效身份来源，请重新提出需求", 401)
    return current

RUNTIME_TRANSITIONS = {
    "QUEUED": {"UNDERSTANDING", "FAILED", "CLOSED"},
    "UNDERSTANDING": {"PLANNING", "QUEUED", "FAILED", "CLOSED"},
    "PLANNING": {"QUEUED", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "EXECUTING", "WAITING", "UNKNOWN", "SUCCEEDED", "FAILED", "PARTIAL", "CLOSED"},
    "NEED_CLARIFICATION": {"QUEUED", "AWAITING_CONFIRMATION", "WAITING", "SUCCEEDED", "FAILED", "PARTIAL", "CLOSED"},
    "AWAITING_CONFIRMATION": {"QUEUED", "EXECUTING", "FAILED", "PARTIAL", "CLOSED"},
    "EXECUTING": {"AWAITING_CONFIRMATION", "WAITING", "UNKNOWN", "SUCCEEDED", "FAILED", "PARTIAL", "CLOSED"},
    "WAITING": {"QUEUED", "AWAITING_CONFIRMATION", "EXECUTING", "UNKNOWN", "SUCCEEDED", "FAILED", "PARTIAL", "CLOSED"},
    "UNKNOWN": {"AWAITING_CONFIRMATION", "WAITING", "SUCCEEDED", "PARTIAL"},
    "PARTIAL": {"QUEUED", "AWAITING_CONFIRMATION", "EXECUTING", "WAITING", "UNKNOWN", "SUCCEEDED", "FAILED", "CLOSED"},
    "FAILED": {"QUEUED", "CLOSED"},
    "SUCCEEDED": set(),
    "CLOSED": set(),
}


class _FixedIntent:
    is_model_call = False
    def __init__(self, intent: str, label: str):
        self.intent = intent
        self.label = label

    def classify(self, _message):
        return IntentDecision(self.intent, self.label, 1.0)


def _state(session: Session, task: Task, state: str, message: str | None = None) -> None:
    if task.state == "CLOSED" and state != "CLOSED":
        return
    if task.state != state:
        previous = task.state
        if state not in RUNTIME_TRANSITIONS.get(previous, set()):
            raise DomainError("INVALID_RUNTIME_TRANSITION", f"不允许任务状态从{previous}迁移至{state}", 409)
        task.state = state
        task.version += 1
        append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                     event_type="WORKFLOW_STATE_CHANGED", payload={"from": previous, "to": state})
    if message is not None:
        task.result_json = {**(task.result_json or {}), "message": message}


def _operations(session: Session, task_id: str, active_only: bool = True) -> list[RuntimeOperation]:
    query = select(RuntimeOperation).where(RuntimeOperation.parent_task_id == task_id)
    if active_only:
        query = query.where(RuntimeOperation.active == 1)
    return list(session.scalars(query.order_by(RuntimeOperation.ordinal, RuntimeOperation.created_at)))


def task_deadline_exceeded(session: Session, parent: Task) -> bool:
    record = session.get(RuntimeRecord, parent.id)
    deadline = record.metadata_json.get("deadline_at") if record else None
    return bool(deadline and as_utc(datetime.fromisoformat(deadline)) <= utcnow())


def _require_live_task(session: Session, parent: Task) -> None:
    if task_deadline_exceeded(session, parent):
        raise DomainError("TASK_DEADLINE_EXCEEDED", "任务已超过24小时处理期限，请新建任务；已有回执仍保留", 409)


def expire_runtime_task(session: Session, parent: Task) -> bool:
    """Stop new work, never erase receipts or infer failure for unknown results.

    A confirmed future schedule is a separate bank mandate. Its business-time
    execution and UNKNOWN reconciliation are not bounded by the dialogue TTL.
    """
    if parent.state in {"SUCCEEDED", "CLOSED", "FAILED"} or not task_deadline_exceeded(session, parent):
        return False
    record = session.get(RuntimeRecord, parent.id)
    if record.metadata_json.get("budget_exhausted"):
        return False
    ops = _operations(session, parent.id)
    for op in ops:
        if op.status in {"SUCCEEDED", "UNKNOWN", "WAITING_TIME"}:
            continue
        call = session.scalar(select(ToolCall).where(ToolCall.task_id == op.child_task_id,
                                                     ToolCall.idempotency_key == op.idempotency_key)) if op.child_task_id else None
        if call and call.status == "SUCCESS":
            op.status = _child_status(session.get(Task, op.child_task_id))
            continue
        op.status = "CANCELLED"
        if op.child_task_id:
            child = session.get(Task, op.child_task_id)
            child.state = "CLOSED"
            child.version += 1
            for confirmation in session.scalars(select(Confirmation).where(
                    Confirmation.task_id == child.id, Confirmation.status == "PENDING")):
                confirmation.status = "EXPIRED"
            for challenge in session.scalars(select(AuthChallenge).where(AuthChallenge.task_id == child.id)):
                if challenge.status in {"PENDING", "VERIFIED"}:
                    challenge.status = "REVOKED"
    for job in session.scalars(select(WorkflowJob).where(WorkflowJob.task_id == parent.id,
            WorkflowJob.kind.in_(["PLAN", "EXECUTE"]), WorkflowJob.status.in_(["QUEUED", "LEASED"]))):
        job.status = "CANCELLED"
        job.last_error = "TASK_DEADLINE_EXCEEDED"
        job.completed_at = utcnow()
        job.lease_expires_at = None
    record.metadata_json = {**record.metadata_json, "budget_exhausted": True}
    if ops:
        refresh_parent(session, parent, prepare_ready=False)
    else:
        _state(session, parent, "FAILED")
    parent.result_json = {**parent.result_json, "error": "TASK_DEADLINE_EXCEEDED",
        "message": "任务处理期限已到，未授权/未执行步骤已关闭；已有回执、定时授权及查单保留。"}
    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                 event_type="TASK_BUDGET_EXHAUSTED", payload={"budget": "24_HOURS"})
    return True


def expire_runtime_tasks(session: Session) -> int:
    parents = list(session.scalars(select(Task).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(
        RuntimeRecord.kind == "PARENT", Task.state.notin_(["SUCCEEDED", "CLOSED", "FAILED"])
    ).with_for_update(skip_locked=True)))
    return sum(expire_runtime_task(session, parent) for parent in parents)


def _job(session: Session, task: Task, kind: str, key: str, payload: dict, delay: float = 0) -> WorkflowJob:
    existing = session.scalar(select(WorkflowJob).where(WorkflowJob.dedupe_key == key))
    if existing:
        return existing
    result = WorkflowJob(task_id=task.id, sandbox_id=task.sandbox_id, kind=kind,
                         dedupe_key=key, payload_json=payload,
                         next_run_at=utcnow() + timedelta(seconds=delay))
    session.add(result)
    session.flush()
    return result


def accept_task(
    session: Session, principal: Principal, message: str,
    idempotency_key: str | None = None,
) -> Task:
    """Accept once per scoped request key; no key keeps the legacy call contract."""
    from .permissions import require_banking_actor
    require_banking_actor(session, principal)
    family_context = trusted_family_context(session, principal)
    request_hash = canonical_hash({"message": message})
    from .redaction import redact_credentials
    message = redact_credentials(message)
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", idempotency_key):
            raise DomainError("INVALID_IDEMPOTENCY_KEY", "请求幂等键须为1至120位字母、数字或 . _ : -", 422)
        # PostgreSQL READ COMMITTED observes the preceding committed acceptance
        # after this scope's user lock is acquired. Different users remain parallel.
        user = session.scalar(select(User).where(
            User.id == principal.user_id, User.sandbox_id == principal.sandbox_id,
        ).with_for_update())
        if user is None:
            raise DomainError("USER_NOT_FOUND", "当前账套用户不存在", 403)

    def replay() -> Task | None:
        if idempotency_key is None:
            return None
        existing = session.scalar(select(RuntimeTaskRequest).where(
            RuntimeTaskRequest.sandbox_id == principal.sandbox_id,
            RuntimeTaskRequest.user_id == principal.user_id,
            RuntimeTaskRequest.idempotency_key == idempotency_key,
        ))
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise DomainError("IDEMPOTENCY_KEY_CONFLICT", "同一请求幂等键已用于不同任务内容，请核对原任务", 409)
        return owned_task(session, principal, existing.task_id)

    if existing_task := replay():
        validate_runtime_family_context(session, existing_task)
        return existing_task
    if session.get_bind().dialect.name == "sqlite":
        # Python sqlite3 legacy transaction mode does not BEGIN on SELECT or
        # SAVEPOINT. Start the outer transaction so releasing our savepoint can
        # never commit a task before the caller's commit (or survive rollback).
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    try:
        # A uniqueness race rolls back the entire tentative acceptance, including
        # its task, audit event and planning job, without rolling back caller work.
        with session.begin_nested():
            task = Task(sandbox_id=principal.sandbox_id, user_id=principal.user_id,
                        message=message, intent_type="UNSUPPORTED", state="QUEUED",
                        model_label="pending", fic_json={}, plan_json={}, analysis_json={}, result_json={})
            session.add(task)
            session.flush()
            session.add(RuntimeRecord(task_id=task.id, kind="PARENT", metadata_json={
                "messages": [message], "clarification_count": 0, "request_id": str(uuid4()),
                "input_revision": 0,
                "customer_reply_enabled": os.getenv("ZX_CUSTOMER_REPLY_ENABLED", "0") == "1",
                "deadline_at": (utcnow() + timedelta(hours=24)).isoformat(),
                **({"family_context_origin": family_context} if family_context else {}),
            }))
            if idempotency_key is not None:
                session.add(RuntimeTaskRequest(
                    sandbox_id=principal.sandbox_id, user_id=principal.user_id,
                    idempotency_key=idempotency_key, request_hash=request_hash, task_id=task.id,
                ))
            append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id,
                         event_type="TASK_ACCEPTED", payload={"schema_version": "2.0"})
            _job(session, task, "PLAN", f"plan:{task.id}:0", {})
        return task
    except IntegrityError:
        if existing_task := replay():
            validate_runtime_family_context(session, existing_task)
            return existing_task
        raise


def _prepare_operation(session: Session, parent: Task, operation: RuntimeOperation) -> None:
    family_context = validate_runtime_family_context(session, parent)
    from .intelligence import ProposedAction, canonical_action_message, transfer_memo_issue
    action = ProposedAction.model_validate(operation.args_json)
    # A unique anomaly-linked card is an authoritative resolution, not a model-selected entity ID.
    if action.kind == "CARD_LOCK" and not action.slots.card_last4 and not action.slots.card_number:
        prior = _operations(session, parent.id)
        card_ids = set()
        for item in prior:
            child = session.get(Task, item.child_task_id) if item.child_task_id else None
            if child and child.analysis_json:
                card_ids.update(row["card_id"] for row in child.analysis_json.get("anomalies", []) if row.get("card_id"))
        if len(card_ids) == 1:
            card = session.get(Card, next(iter(card_ids)))
            if card and card.sandbox_id == parent.sandbox_id:
                action.slots.card_last4 = card.last4
    canonical = canonical_action_message(action)
    from .permissions import actor_role
    role = actor_role(session, parent.sandbox_id, parent.user_id)
    if role == "FAMILY_HELPER" and action.kind not in {"FAMILY_VIEW", "FAMILY_PROPOSE"}:
        child = Task(sandbox_id=parent.sandbox_id, user_id=parent.user_id, message=canonical,
                     intent_type=ACTION_INTENTS[action.kind], state="FAILED", model_label="server-role-policy",
                     fic_json={}, plan_json={}, analysis_json={}, result_json={"error": "FAMILY_HELPER_SCOPE_REQUIRED",
                     "message": "协助身份只能查看已授予的家庭投影或提交待本人确认的提案，未访问本人银行数据或执行业务。"})
        session.add(child)
        session.flush()
        session.add(RuntimeRecord(task_id=child.id, root_task_id=parent.id, kind="CHILD", metadata_json={"operation_id": operation.id}))
        operation.child_task_id, operation.status = child.id, "FAILED"
        append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                     event_type="CAPABILITY_DENIED", payload={"operation_id": operation.id, "action": action.kind,
                                                               "reason": "FAMILY_HELPER_SCOPE_REQUIRED"})
        return
    memo_issue = transfer_memo_issue(action, parent.message)
    if memo_issue:
        from .semantic_memo_source import verified_complete_inner_memo
        if verified_complete_inner_memo(session,parent,operation,action):memo_issue=None
    if memo_issue:
        from .contracts import FinancialIntentContract
        child = Task(sandbox_id=parent.sandbox_id, user_id=parent.user_id, message=canonical,
                     intent_type=ACTION_INTENTS[action.kind], state="NEED_CLARIFICATION",
                     model_label="server-memo-validation", plan_json={}, analysis_json={},
                     result_json={"error": "TRANSFER_MEMO_INCOMPLETE", "message": memo_issue},
                     fic_json=FinancialIntentContract(intent_id=str(uuid4()), intent_version=1,
                         intent_type=ACTION_INTENTS[action.kind], actor_user_id=parent.user_id,
                         sandbox_id=parent.sandbox_id, source_channel="WEB", status="NEED_CLARIFICATION",
                         slots={field: {"value": value, "source": "USER_TEXT"}
                                for field, value in action.slots.model_dump(exclude_none=True).items()},
                         requested_capabilities=[]).model_dump())
        session.add(child)
        session.flush()
        session.add(RuntimeRecord(task_id=child.id, root_task_id=parent.id, kind="CHILD",
                                  metadata_json={"operation_id": operation.id}))
        operation.child_task_id, operation.status = child.id, "NEED_CLARIFICATION"
        append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                     event_type="DOMAIN_ACTION_PREPARED", payload={"operation_id": operation.id,
                     "child_task_id": child.id, "kind": operation.action_kind, "status": operation.status,
                     "reason": "TRANSFER_MEMO_INCOMPLETE"})
        return
    from .compiled_actions import bind_compiled_action,KEY as COMPILED_KEY
    phase_context=None
    if session.get(RuntimeRecord,parent.id).metadata_json.get('cloud_semantic_intent'):
        prior_writes=[item for item in _operations(session,parent.id) if item.action_kind not in READ_KINDS and item.status=='SUCCEEDED']
        if prior_writes:
            from .semantic_phase_context import build_semantic_phase_context
            phase_context=build_semantic_phase_context(session,parent,operation,'PREPARE_NEXT_STEP')
    try:
        compiler_proof=bind_compiled_action(session,Principal(parent.user_id,parent.sandbox_id,role=role,family_context=family_context),parent,operation,action,phase_context=phase_context)
    except DomainError as error:
        if error.code!='TEMPORAL_SOURCE_REQUIRED':raise
        from .contracts import FinancialIntentContract
        from .temporal_fields import FIELDS
        values=action.slots.model_dump(exclude_none=True)
        child=Task(sandbox_id=parent.sandbox_id,user_id=parent.user_id,message=canonical,
            intent_type=ACTION_INTENTS[action.kind],state='NEED_CLARIFICATION',model_label='server-temporal-source-validation',
            plan_json={},analysis_json={},result_json={'error':error.code,'message':error.message,
                'missing_fields':sorted(FIELDS.get(action.kind,frozenset()).intersection(values))},
            fic_json=FinancialIntentContract(intent_id=str(uuid4()),intent_version=1,intent_type=ACTION_INTENTS[action.kind],
                actor_user_id=parent.user_id,sandbox_id=parent.sandbox_id,source_channel='WEB',status='NEED_CLARIFICATION',
                slots={field:{'value':value,'source':'USER_TEXT'} for field,value in values.items()},requested_capabilities=[]).model_dump())
        session.add(child);session.flush()
        session.add(RuntimeRecord(task_id=child.id,root_task_id=parent.id,kind='CHILD',metadata_json={'operation_id':operation.id}))
        operation.child_task_id,operation.status=child.id,'NEED_CLARIFICATION'
        append_audit(session,sandbox_id=parent.sandbox_id,task_id=parent.id,event_type='TEMPORAL_SOURCE_CLARIFICATION_REQUIRED',
            payload={'operation_id':operation.id,'child_task_id':child.id,'kind':operation.action_kind})
        return
    child = create_task(session, Principal(parent.user_id, parent.sandbox_id, role=role,
                        family_context=family_context), canonical,
                        ModelGateway(_FixedIntent(ACTION_INTENTS[action.kind], "server-domain-compiler")),
                        proposed_action=action.model_dump(), source_context={"message": parent.message, "source_root_task_id": parent.id,
                            "source_operation_id": operation.id,
                            **({COMPILED_KEY:compiler_proof} if compiler_proof is not None else {}),
                            "fx_market_rate": session.get(RuntimeRecord, parent.id).metadata_json.get("fx_market_rate"),
                            "fx_market_error": session.get(RuntimeRecord, parent.id).metadata_json.get("fx_market_error")})
    from .conversation_provenance import annotate_child_slots
    annotate_child_slots(session, parent, child,
        session.get(RuntimeRecord, parent.id).metadata_json.get("conversation_context") or {})
    session.add(RuntimeRecord(task_id=child.id, root_task_id=parent.id, kind="CHILD",
                              metadata_json={"operation_id": operation.id, "prepared_actor_role":role}))
    session.flush()
    if action.kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"} and child.state == "AWAITING_CONFIRMATION":
        from .extensions import evaluate_transfer_risk
        risk = evaluate_transfer_risk(session, task=child, context={"message": parent.message})
        child.result_json = {**child.result_json, "fraud_risk": risk}
        if risk["decision"] != "ALLOW":
            child.state = "FAILED"
            child.version += 1
            child.result_json = {**child.result_json, "error": "FRAUD_TRANSFER_BLOCKED",
                                 "message": "转账风险尚未通过核验，请先暂停并通过可信渠道核实；确认不会绕过风控。"}
            for policy in session.scalars(select(PolicyDecisionRecord).where(PolicyDecisionRecord.task_id == child.id)):
                if policy.decision == "REQUIRE_CONFIRMATION":
                    policy.decision = "DENY"
                    policy.reason_codes_json = list(risk["reason_codes"])
        if action.kind == "TRANSFER" and child.state == "AWAITING_CONFIRMATION":
            from .models import Account
            from .extensions import reserved_goal_cash_minor
            account = session.scalar(select(Account).where(Account.sandbox_id == parent.sandbox_id,
                                                           Account.user_id == parent.user_id))
            amount = next((node["amount_minor"] for node in child.plan_json.get("nodes", []) if node.get("amount_minor")), None)
            if account and amount and account.balance_minor - reserved_goal_cash_minor(session, parent.sandbox_id, parent.user_id, account.id) < amount:
                child.state = "FAILED"
                child.version += 1
                child.result_json = {"error": "GOAL_RESERVE_BREACH", "message": "转账会占用已预留的目标资金，请先修改金额或目标，当前没有扣款。"}
                for policy in session.scalars(select(PolicyDecisionRecord).where(PolicyDecisionRecord.task_id == child.id)):
                    if policy.decision == "REQUIRE_CONFIRMATION":
                        policy.decision, policy.reason_codes_json = "DENY", ["GOAL_RESERVE_BREACH"]
    operation.child_task_id = child.id
    operation.status = _child_status(child)
    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                 event_type="DOMAIN_ACTION_PREPARED",
                 payload={"operation_id": operation.id, "child_task_id": child.id,
                          "kind": operation.action_kind, "status": operation.status})


def _child_status(child: Task) -> str:
    return {"AWAITING_CONFIRMATION": "WAITING_CONFIRMATION", "SUCCEEDED": "SUCCEEDED",
            "NEED_CLARIFICATION": "NEED_CLARIFICATION", "FAILED": "FAILED",
            "CLOSED": "CANCELLED", "UNKNOWN": "UNKNOWN", "WAITING": "WAITING_TIME"}.get(child.state, child.state)


def refresh_parent(session: Session, parent: Task, prepare_ready: bool = True) -> None:
    if parent.state == "CLOSED":
        return
    ops = _operations(session, parent.id)
    statuses = {item.operation_key: item.status for item in ops}
    for op in ops:
        if op.status in {"RUNNING", "AUTHORIZED", "UNKNOWN", "FAILED", "CANCELLED"}:
            statuses[op.operation_key] = op.status
            continue
        deps_ready = all(statuses.get(key) == "SUCCEEDED" for key in op.depends_on_json)
        if not op.child_task_id and deps_ready and prepare_ready:
            if op.action_kind in {"CREDIT_VIEW", "CREDIT_STATEMENT"}:
                from .credit_read_preparation import prepare_credit_read
                prepare_credit_read(session, parent, op, _prepare_operation)
            else:
                _prepare_operation(session, parent, op)
        elif op.child_task_id:
            child = session.get(Task, op.child_task_id)
            op.status = _child_status(child)
        statuses[op.operation_key] = op.status
    if not ops:
        return
    values = list(statuses.values())
    if "UNKNOWN" in values:
        _state(session, parent, "UNKNOWN", "正在按原业务动作查单，暂不重复执行。")
    elif any(value in {"RUNNING", "AUTHORIZED"} for value in values):
        _state(session, parent, "EXECUTING", "正在执行已确认的步骤。")
    elif "NEED_CLARIFICATION" in values:
        child = next(session.get(Task, item.child_task_id) for item in ops if item.status == "NEED_CLARIFICATION")
        _state(session, parent, "NEED_CLARIFICATION", child.result_json.get("message", "请补充必要信息。"))
    elif "WAITING_CONFIRMATION" in values:
        _state(session, parent, "AWAITING_CONFIRMATION", "计划已核验，请确认当前步骤。")
    elif "WAITING_TIME" in values:
        _state(session, parent, "WAITING", "等待计划约定的业务时间，执行前将重新核验。")
    elif any(value in {"FAILED", "CANCELLED"} for value in values):
        target = "CLOSED" if all(value == "CANCELLED" for value in values) else ("PARTIAL" if "SUCCEEDED" in values else "FAILED")
        _state(session, parent, target,
               "已有步骤完成，剩余步骤未完成。" if "SUCCEEDED" in values else "当前任务未执行成功，请查看原因。")
    elif all(value == "SUCCEEDED" for value in values):
        was_done = parent.state == "SUCCEEDED"
        _state(session, parent, "SUCCEEDED", "全部步骤已完成，结果已核验。")
        if not was_done:
            session.add(OutboxMessage(sandbox_id=parent.sandbox_id, task_id=parent.id,
                                     event_type="TASK_COMPLETED", payload_json={"task_id": parent.id, "summary": parent.message}))


def _transfer_confirmation_display(session: Session, parent: Task, child: Task,
                                   confirmation: Confirmation, plan_scope: dict) -> dict | None:
    """Read bound transfer display facts without changing plans or authorization."""
    from .models import Account, Payee
    if (child.sandbox_id, child.user_id) != (parent.sandbox_id, parent.user_id) or (
            confirmation.task_id, confirmation.sandbox_id, confirmation.user_id) != (
            child.id, child.sandbox_id, child.user_id) or confirmation.plan_hash != child.plan_hash or (
            canonical_hash(child.plan_json) != child.plan_hash):
        return None
    slots = child.fic_json.get("slots", {})
    payee_id = plan_scope.get("payee_id")
    if not payee_id or confirmation.target_id != payee_id or slots.get("payee_id", {}).get("value") != payee_id:
        return None
    payee = session.scalar(select(Payee).where(Payee.id == payee_id,
        Payee.sandbox_id == parent.sandbox_id, Payee.user_id == parent.user_id, Payee.status == "ACTIVE"))
    if payee is None or payee.account_last4 != plan_scope.get("payee_last4"):
        return None
    source = session.scalar(select(Account).where(Account.id == plan_scope.get("source_account_id"),
        Account.sandbox_id == parent.sandbox_id, Account.user_id == parent.user_id))
    if source is None or ("expected_account_version" in plan_scope
                         and source.version != plan_scope["expected_account_version"]):
        return None
    try:
        from .internal_banking import recipient_scope
        destination = {key: plan_scope.get(key) for key in ("transfer_channel", "recipient_username", "recipient_account_id")}
        if recipient_scope(session, payee) != destination:
            return None
        if plan_scope.get("recipient_context"):
            from .contacts import recipient_context
            bound = plan_scope["recipient_context"]
            if recipient_context(session, parent.sandbox_id, parent.user_id, bound.get("requested_reference")) != bound:
                return None
    except DomainError:
        return None
    name = ((plan_scope.get("recipient_context") or {}).get("requested_reference") or plan_scope.get("recipient_username")) \
        if destination["transfer_channel"] == "INTERNAL_BANK" else slots.get("payee_name", {}).get("value")
    if destination["transfer_channel"] == "EXTERNAL_SIM" and name != payee.display_name:
        return None  # No new mutable/foreign display name can replace the bound target.
    display = {"payee_name": name if isinstance(name, str) and name else payee.display_name}
    if destination["transfer_channel"] == "INTERNAL_BANK":
        # A submitted card number/alias is a recipient reference, not a person's
        # name. Read the name only through the independently verified bank owner
        # binding above. This supplement is display-only: never change the
        # signed plan, destination, confirmation hash or submitted reference.
        from .access_models import LoginAccount
        from .models import User
        recipient_login = session.scalar(select(LoginAccount).where(
            LoginAccount.username == destination["recipient_username"],
            LoginAccount.status == "ACTIVE", LoginAccount.role.in_({"CUSTOMER", "DEV_TEST"})))
        recipient_account = session.get(Account, destination["recipient_account_id"])
        if recipient_login is not None and recipient_account is not None and (
                recipient_account.user_id, recipient_account.sandbox_id) == (
                recipient_login.user_id, recipient_login.sandbox_id):
            recipient_user = session.get(User, recipient_login.user_id)
            if recipient_user is not None and recipient_user.sandbox_id == recipient_login.sandbox_id \
                    and isinstance(recipient_user.display_name, str) and recipient_user.display_name.strip():
                display["recipient_display_name"] = recipient_user.display_name
    if isinstance(plan_scope.get("memo"), str):
        display["memo"] = plan_scope["memo"]  # The hashed plan, never a reparsed message.
    from .transfer_receipt_display import bound_confirmation_card_numbers
    numbers = bound_confirmation_card_numbers(session, parent, plan_scope)
    if numbers is None:
        return None
    display.update(numbers)  # Full numbers of already selected cards, not new defaults.
    return display


def validate_task_trace_access(session: Session, principal: Principal, task_id: str):
    """The trace's current access gate, without computing financial displays.

    History callers need only this gate, not a discarded pending confirmation,
    evidence trace or semantic display supplement. Never memoize permissions.
    """
    parent = owned_task(session, principal, task_id)
    validate_runtime_family_context(session, parent)
    record = session.get(RuntimeRecord, parent.id)
    if record is None or record.kind != "PARENT":
        raise DomainError("RUNTIME_TASK_NOT_FOUND", "不是当前运行时任务", 404)
    ops = _operations(session, parent.id)
    for op in ops:
        if op.action_kind != "FAMILY_VIEW" or not op.child_task_id:
            continue
        child = session.get(Task, op.child_task_id)
        if not child or (child.sandbox_id, child.user_id) != (parent.sandbox_id, parent.user_id):
            continue
        result = child.result_json or {}
        if result.get("authorized_balances"):
            from .extensions import is_family_helper
            if is_family_helper(session, principal.sandbox_id, principal.user_id):
                from .extensions import family_view
                required = {row.get("owner_user_id") for row in result.get("delegations", [])
                            if row.get("valid") and row.get("grantee_user_id") == principal.user_id
                            and "READ_BALANCE" in row.get("scopes", [])}
                current = family_view(session, principal.sandbox_id, principal.user_id)
                readable = {row["owner_user_id"] for row in current["delegations"]
                            if row["valid"] and row["grantee_user_id"] == principal.user_id
                            and "READ_BALANCE" in row["scopes"]}
                if not required or None in required or not required <= readable:
                    raise DomainError("FAMILY_HISTORY_ACCESS_CHANGED", "此前余额资料的授权范围已失效，请重新核对当前委托", 403)
    return parent, record, ops


def task_trace(session: Session, principal: Principal, task_id: str, *, customer_pending_display: bool = True) -> dict:
    parent, record, ops = validate_task_trace_access(session, principal, task_id)
    pending = None
    pending_unavailable = None
    confirmation_unavailable_reason = None
    nodes = []
    completed_read_results = []
    analysis = None
    customer_needed_fields = []
    for op in ops:
        child = session.get(Task, op.child_task_id) if op.child_task_id else None
        if child and (child.sandbox_id, child.user_id) != (parent.sandbox_id, parent.user_id):
            if customer_pending_display and op.action_kind in {"TRANSFER", "TRANSFER_SCHEDULE"} and op.status == "WAITING_CONFIRMATION":
                pending_unavailable = "确认所需资料已变化，请修改需求并重新准备确认方案；这笔待确认转账尚未执行。"
            child = None
        child_view = task_view(session, child) if child else None
        if child and child.analysis_json and analysis is None:
            analysis = child.analysis_json
        result = child.result_json if child else {}
        if child and op.action_kind == 'TRANSFER' and op.status == 'SUCCEEDED':
            from .transfer_receipt_display import completed_transfer_receipt_display
            transfer_display = completed_transfer_receipt_display(session, principal, parent, op, child)
            if transfer_display is not None:
                result = {**result, **transfer_display}  # Copy only; stored receipt/proof stays immutable.
        if op.status == "NEED_CLARIFICATION" and result.get("error") != "TRANSFER_MEMO_INCOMPLETE":
            slots = op.args_json.get("slots") or {}
            missing = []
            if op.action_kind in {"TRANSFER", "TRANSFER_SCHEDULE", "FX_TRANSFER"}:
                if not slots.get("payee_name") and not slots.get("recipient_card_reference"): missing.append("payee_name")
                if slots.get("amount_minor") is None: missing.append("amount_minor")
            elif op.action_kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
                if not slots.get("product_name"): missing.append("product_name")
                if slots.get("amount_minor") is None and slots.get("units") is None: missing.append("amount_minor")
            elif op.action_kind == "AA_REQUEST":
                if slots.get("amount_minor") is None: missing.append("amount_minor")
                names=slots.get("participant_names")
                if not isinstance(names,list) or not 2 <= len(names) <= 20 \
                        or any(not isinstance(name,str) or not name.strip() for name in names) or len(set(names)) != len(names):
                    missing.append("participant_names")
                elif slots.get("participant_count") is not None and slots["participant_count"] != len(names):
                    missing.append("participant_count")
            elif op.action_kind == "GOAL_VIEW" and result.get("error", "").startswith("GOAL_WHAT_IF_"):
                if result.get("error") == "GOAL_WHAT_IF_BASELINE_REQUIRED": missing.append("monthly_saving_minor")
                if result.get("error") == "GOAL_WHAT_IF_DELTA_REQUIRED": missing.append("monthly_saving_delta_minor")
                if result.get("error") == "GOAL_WHAT_IF_TARGET_REQUIRED": missing.append("goal_name")
            elif op.action_kind in {"SUBSCRIPTION_CANCEL","SUBSCRIPTION_START","SUBSCRIPTION_RENEW"} and not slots.get("merchant"):
                missing.append("merchant")
            elif op.action_kind == 'CREDIT_INSTALLMENT':
                from .credit_preparation_contract import missing_credit_fields
                missing.extend(missing_credit_fields(op.action_kind, slots))
            elif op.action_kind == 'CREDIT_APPLY' and result.get('error') == 'CREDIT_SELECTION_REQUIRED':
                if result.get('missing_fields') == ['card_reference']:
                    missing.append('card_reference')
            elif op.action_kind in {'CREDIT_STATEMENT', 'CREDIT_VIEW'} and result.get('error') == 'CREDIT_SELECTION_REQUIRED':
                if result.get('missing_fields') == ['card_reference']:
                    missing.append('card_reference')
            elif op.action_kind in {"CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOSS", "CARD_REPLACEMENT", "CARD_SET_LIMIT"}:
                if not slots.get("card_number") and not slots.get("card_last4"): missing.append("card_number")
            if missing: customer_needed_fields.append({"action":op.action_kind,"missing_fields":missing})
        evidence = None
        if child:
            evidence = {"fic": child.fic_json, "plan_hash": child.plan_hash,
                "policy": [{"node": row.node_key, "decision": row.decision, "reasons": row.reason_codes_json,
                            "version": row.policy_version} for row in session.scalars(select(PolicyDecisionRecord).where(
                    PolicyDecisionRecord.task_id == child.id))],
                "sources": [{"evidence_id": row.id, "kind": row.source_kind, "version": row.source_version}
                    for row in session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.task_id == child.id))]}
        card = session.get(Card, result.get("card_id")) if result.get("card_id") else None
        can_compensate = bool(op.status == "SUCCEEDED" and op.action_kind in {"CARD_LOCK", "CARD_UNLOCK"}
            and card and card.sandbox_id == parent.sandbox_id and card.user_id == principal.user_id
            and card.version == result.get("card_version") and card.status == result.get("card_status")
            and op.id not in record.metadata_json.get("compensations", {})
            and parent.state not in {"EXECUTING", "UNKNOWN"})
        nodes.append({"node_id": op.operation_key, "operation_id": op.id, "action": op.action_kind,
                      "label": op.label, "status": op.status, "depends_on": op.depends_on_json,
                      "child_task_id": op.child_task_id, "result": result,
                      "wait_reason": result.get("message") if op.status in {"NEED_CLARIFICATION", "FAILED"} else None,
                      "error": result.get("error"), "idempotency_key": op.idempotency_key, "evidence": evidence,
                      "can_compensate": can_compensate,
                      "compensation_task_id": record.metadata_json.get("compensations", {}).get(op.id, {}).get("task_id")})
        from .intelligence import READ_KINDS, action_signature
        child_record=session.get(RuntimeRecord,child.id) if child else None
        snapshot=session.scalar(select(PlanSnapshot).where(PlanSnapshot.task_id==parent.id,
            PlanSnapshot.version==record.plan_version)) if record.plan_version else None
        bound_node=next((item for item in (snapshot.plan_json.get('nodes',[]) if snapshot else [])
            if isinstance(item,dict) and item.get('operation_id')==op.id),None)
        if (op.action_kind in READ_KINDS and op.status=='SUCCEEDED' and child and child.state=='SUCCEEDED'
            and op.plan_version==record.plan_version and op.args_json.get('kind')==op.action_kind
            and canonical_hash(action_signature(op.args_json))==op.fingerprint
            and child_record and child_record.kind=='CHILD' and child_record.root_task_id==parent.id
            and child_record.metadata_json.get('operation_id')==op.id
            and child_record.metadata_json.get('prepared_actor_role')==actor_role(session,parent.sandbox_id,parent.user_id)
            and snapshot and snapshot.plan_hash==parent.plan_hash==canonical_hash(parent.plan_json)
            and bound_node and bound_node.get('node_id')==op.operation_key and bound_node.get('kind')==op.action_kind
            and canonical_hash(bound_node.get('args'))==canonical_hash(op.args_json)
            and bound_node.get('depends_on')==op.depends_on_json):
            completed_read_results.append({'schema_version':'completed-read-result-v1','task_id':parent.id,
                'task_version':parent.version,'node_id':op.operation_key,'operation_id':op.id,
                'child_task_id':child.id,'action':op.action_kind,'status':'SUCCEEDED','result':result,
                'analysis':child.analysis_json or None,'observed_at':as_utc(child.updated_at).isoformat()})
        if pending is None and op.status == "WAITING_CONFIRMATION" and child_view and child_view.confirmation_id:
            confirmation = session.get(Confirmation, child_view.confirmation_id)
            plan_scope = next((item for item in child.plan_json.get("nodes", []) if item.get("node_id") == confirmation.node_key), {})
            transfer_display = {}
            life_display = None
            banking_display = None
            if customer_pending_display and op.action_kind in {"TRANSFER_SCHEDULE","AA_REQUEST"}:
                from .banking_workflow_confirmation import customer_banking_confirmation
                try:
                    banking_display=customer_banking_confirmation(session,principal,child,confirmation,op.action_kind,
                        parent=parent,operation=op)
                except DomainError:
                    pending_unavailable="本次日程、卡片或参与人范围暂时无法完整核验，请重新准备；尚未执行。"
                    continue
            if op.action_kind in {"MERCHANT_PURCHASE", "OCCASION_MEMBER_SET", "OCCASION_SET", "LIFE_PLAN_AUTHORIZE", "LIFE_PLAN_CANCEL"}:
                from .life_confirmation import customer_life_confirmation
                try:
                    life_display = customer_life_confirmation(session,principal,child,confirmation,op.action_kind,
                        parent=parent,operation=op)
                except DomainError as exc:
                    if exc.code == "LIFE_CONFIRMATION_EXPIRED":
                        confirmation_unavailable_reason = "EXPIRED"
                        pending_unavailable = "本次确认已过期，请修改需求并重新准备；尚未执行。"
                    else:
                        pending_unavailable = "本次确认范围已变化或暂时无法核对，请重新准备；尚未执行。"
                    continue
            if customer_pending_display and op.action_kind in {"TRANSFER", "TRANSFER_SCHEDULE", "AA_PAY"}:
                transfer_display = _transfer_confirmation_display(session, parent, child, confirmation, plan_scope)
                if transfer_display is None:
                    pending_unavailable = "确认所需资料已变化，请修改需求并重新准备确认方案；这笔待确认转账尚未执行。"
                    continue
            pending = {"task_id": child.id, "confirmation_id": confirmation.id,
                       "plan_hash": child.plan_hash, "expected_task_version": child.version,
                       "node_key": confirmation.node_key, "label": op.label,
                       "summary": child.result_json.get("message", op.label),
                       "requires_step_up": requires_step_up(session, child, confirmation),
                       "expires_at": as_utc(confirmation.expires_at).isoformat()}
            if life_display is not None:
                pending["label"] = life_display["title"]
            safe_scope = {key: plan_scope[key] for key in ("transfer_channel", "recipient_username", "payee_name", "payee_last4", "amount_minor") if key in plan_scope}
            if op.action_kind == 'SUBSCRIPTION_CANCEL':
                safe_scope.update({key:plan_scope[key] for key in
                    ('subscription_id','merchant','source_type','expected_version') if key in plan_scope})
            if op.action_kind in {"SUBSCRIPTION_START","SUBSCRIPTION_RENEW"}:
                safe_scope.update({key:plan_scope[key] for key in (
                    "merchant","currency","effect_kind","monthly_price_minor","total_amount_minor","months","auto_renew","source_card_last4",
                    "effective_from","entitlement_end_at","expires_at","effective_from_policy",
                    "product_version","billing_months","action","auto_renew_setting_source",
                    "future_billing_months","future_amount_minor","mandate_amount_minor","mandate_billing_months") if key in plan_scope})
            safe_scope.update(transfer_display)
            if customer_pending_display and op.action_kind in {"TRANSFER","TRANSFER_SCHEDULE","AA_PAY"} and plan_scope.get("transfer_payment_scope"):
                from .transfer_payment_scope import transfer_confirmation_scope, validate_transfer_payment_scope
                payment=plan_scope["transfer_payment_scope"]
                try:
                    display=transfer_confirmation_scope(payment)
                    if display is None or (payment.get("amount_minor"),payment.get("currency"),
                            (payment.get("source") or {}).get("account_id"),(payment.get("recipient") or {}).get("account_id")) != (
                            plan_scope.get("amount_minor"),"CNY",plan_scope.get("source_account_id"),plan_scope.get("recipient_account_id")):
                        raise DomainError("TRANSFER_INSTRUMENT_SCOPE_CHANGED","确认收付款卡范围已变化。",409)
                    validate_transfer_payment_scope(session,principal,payment,scheduled=op.action_kind=="TRANSFER_SCHEDULE",
                        require_funds=op.action_kind!="TRANSFER_SCHEDULE",lock=False)
                except DomainError:
                    pending=None
                    pending_unavailable="确认所需收付款卡或资金范围已变化，请重新核对；尚未执行这笔转账。"
                    continue
                safe_scope.update(currency=display["currency"],amount_minor=display["amount_minor"],
                    source_card_display_last4=display["source"]["display_last4"],
                    recipient_card_display_last4=display["recipient"]["display_last4"])
            if plan_scope.get("alias_binding"):
                safe_scope["alias_binding"] = plan_scope["alias_binding"]
            if plan_scope.get("recipient_context"):
                target_view = plan_scope["recipient_context"]
                safe_scope.update({key: value for key, value in target_view.items()
                    if key in {"requested_reference", "recipient_username", "recipient_card_number", "recipient_card_display_last4"}})
            if plan_scope.get("source_card_context"):
                source_view = plan_scope["source_card_context"]
                safe_scope.update({"source_card_number": source_view.get("card_number"),
                    "source_card_display_last4": source_view.get("display_last4")})
            if plan_scope.get("recipient_account_id"):
                from .internal_banking import account_reference
                safe_scope["recipient_reference"] = account_reference(plan_scope["recipient_account_id"])
            if op.action_kind.startswith("FX_"):
                candidate = child.result_json.get("fx_quote") or child.result_json.get("preview") or child.result_json.get("quote")
                if isinstance(candidate, dict):
                    fx_fields = {"quote_id", "kind", "requested_amount_minor", "requested_currency",
                        "source_currency", "target_currency", "debit_minor", "credit_minor", "fee_minor", "fee_currency",
                        "market_rate", "rate", "market_date", "provider", "source_url", "fetched_at", "expires_at",
                        "reference_only", "recipient_username", "recipient_reference", "route", "rounding_policy",
                        "price_policy", "fee_source", "settlement_notice", "synthetic", "source_card_last4", "recipient_card_last4",
                        "source_card_number", "source_card_display_last4", "source_card_legacy_last4",
                        "recipient_card_number", "recipient_card_display_last4", "alias_binding"}
                    safe_scope.update({key: value for key, value in candidate.items() if key in fx_fields})
            if life_display is not None:
                safe_scope["domain_confirmation"] = life_display
            if banking_display is not None:
                safe_scope["domain_confirmation"] = banking_display
            if safe_scope:
                pending["scope"] = safe_scope
    events = list(session.scalars(select(AuditEvent).where(AuditEvent.task_id == parent.id).order_by(AuditEvent.seq)))
    allowed = []
    if parent.state == "NEED_CLARIFICATION":
        allowed.append("clarify")
    if pending:
        allowed.append("confirm")
    if any(node["can_compensate"] for node in nodes):
        allowed.append("compensate")
    if parent.state in {"QUEUED", "UNDERSTANDING", "PLANNING", "NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "PARTIAL", "FAILED", "WAITING"}:
        allowed.extend(["cancel"] if record.metadata_json.get("trusted_compensation") or record.metadata_json.get("family_origin") else ["revise", "cancel"])
    from .intelligence import READ_KINDS
    completed = [op for op in ops if op.status == "SUCCEEDED"]
    from .subscription_commerce import verified_subscription_no_effect
    verified_no_effects={op.id for op in completed if op.action_kind in {"SUBSCRIPTION_START","SUBSCRIPTION_RENEW","SUBSCRIPTION_CANCEL"}
        and op.child_task_id and verified_subscription_no_effect(session,session.get(Task,op.child_task_id))}
    verified = bool(parent.state == "SUCCEEDED" and ops and len(completed) == len(ops))
    if verified:
        verified = all(op.child_task_id and (op.action_kind in READ_KINDS or op.id in verified_no_effects or session.scalar(select(ToolCall.id).where(
            ToolCall.task_id == op.child_task_id, ToolCall.status == "SUCCESS",
            ToolCall.idempotency_key == op.idempotency_key)) is not None) for op in ops)
    outcome_kind = "PROCESS"
    if parent.state == "UNKNOWN":
        outcome_kind = "UNKNOWN"
    elif parent.state in {"FAILED", "CLOSED"}:
        outcome_kind = "FAILURE"
    elif parent.state == "PARTIAL":
        outcome_kind = "PARTIAL_RESULT"
    elif verified:
        outcome_kind = ("QUERY_RESULT" if all(op.action_kind in READ_KINDS or op.id in verified_no_effects for op in ops) else
                        "PROPOSAL_SAVED" if all(op.action_kind == "FAMILY_PROPOSE" for op in ops) else "BUSINESS_RECEIPT")
    outcome = {"kind": outcome_kind, "state": parent.state, "verified": verified,
               "completed_operation_ids": [op.id for op in completed]}
    result_observed_at = None
    if verified and all(op.action_kind in READ_KINDS or op.id in verified_no_effects for op in ops):
        result_observed_at = max(as_utc(session.get(Task, op.child_task_id).updated_at) for op in ops).isoformat()
    trace = {"schema_version": "2.0", "request_id": record.metadata_json.get("request_id"),
            "task_id": parent.id, "version": parent.version, "state": parent.state,
            "created_at": as_utc(parent.created_at).isoformat(), "updated_at": as_utc(parent.updated_at).isoformat(),
            "result_observed_at": result_observed_at,
            "message": parent.message, "summary": record.metadata_json.get("summary", parent.message),
            "reply_language": record.metadata_json.get("reply_language") if record.metadata_json.get("reply_language") in {"zh-CN", "en"} else None,
            "model_label": parent.model_label, "plan_version": record.plan_version,
            "event_cursor": events[-1].seq if events else 0, "nodes": nodes,
            "pending_confirmation": pending, "analysis": analysis,
            "completed_read_results":completed_read_results,
            "status_message": pending_unavailable or parent.result_json.get("message", ""), "outcome": outcome,
            "confirmation_unavailable_reason": confirmation_unavailable_reason if pending is None else None,
            "receipt": parent.result_json if parent.state in {"SUCCEEDED", "FAILED", "PARTIAL", "CLOSED", "UNKNOWN"} else {},
            "allowed_actions": allowed, "audit_chain_valid": verify_audit_chain(events),
            "model_decisions": record.metadata_json.get("decisions", []),
            "laya_advice": record.metadata_json.get("laya_advice"),
            "planning_diagnostics": record.metadata_json.get("planning_diagnostics"),
            "domains": record.metadata_json.get("domains", []),
            "multi_step": record.metadata_json.get("multi_step", False),
            "cross_domain": record.metadata_json.get("cross_domain", False),
            "compensation_source": {key: record.metadata_json["trusted_compensation"][key]
                for key in ("source_task_id", "source_operation_id")} if record.metadata_json.get("trusted_compensation") else None,
            "clarification_questions": record.metadata_json.get("clarification_questions", []),
            "actor_role": actor_role(session, parent.sandbox_id, parent.user_id),
            "customer_reply": record.metadata_json.get("customer_reply"),
            "customer_needed_fields": customer_needed_fields}
    from .reviewed_bill_reply import attach_reviewed_bill_comparison
    from .customer_query_focus import verified_query_focus
    focus=verified_query_focus(session,principal,parent)
    if focus:
        trace['customer_query_focus']=focus
    from .customer_read_question_facts import attach_read_question_facts
    trace=attach_read_question_facts(session,principal,parent,trace)
    from .planning_failure_facts import attach_planning_failure
    trace=attach_planning_failure(session,principal,parent,trace,events)
    return attach_reviewed_bill_comparison(session,principal,parent,trace)
            


def confirm_runtime(session: Session, principal: Principal, task_id: str, payload: ConfirmTaskRequest) -> Task:
    from .permissions import require_banking_actor
    require_banking_actor(session, principal)
    parent = owned_task(session, principal, task_id, lock=True)
    validate_runtime_family_context(session, parent)
    _require_live_task(session, parent)
    from .extensions import validate_family_origin
    validate_family_origin(session, parent)
    from .conversation_provenance import validate_current_continuation
    # Display freshness must not replace the existing confirmation/CAS checks;
    # the execution path retains its original authoritative scope and rechecks.
    trace = task_trace(session, principal, parent.id, customer_pending_display=False)
    pending = trace["pending_confirmation"]
    if pending is None or pending["confirmation_id"] != payload.confirmation_id:
        raise DomainError("NO_CURRENT_CONFIRMATION", "当前步骤已变化，请刷新", 409)
    child = owned_task(session, principal, pending["task_id"], lock=True)
    if child.version != payload.expected_task_version or child.plan_hash != payload.plan_hash:
        raise DomainError("PLAN_CHANGED", "当前步骤版本或计划已变化", 409)
    confirmation = session.get(Confirmation, pending["confirmation_id"])
    if confirmation.status != "PENDING" or as_utc(confirmation.expires_at) <= utcnow():
        raise DomainError("CONFIRMATION_EXPIRED", "当前确认已使用或过期，请重新核对计划", 409)
    if confirmation.plan_hash != child.plan_hash:
        raise DomainError("PLAN_CHANGED", "确认不属于当前计划", 409)
    op = next(item for item in _operations(session, parent.id) if item.child_task_id == child.id)
    phase_context=None
    if session.get(RuntimeRecord,parent.id).metadata_json.get('cloud_semantic_intent') \
            and any(item.action_kind not in READ_KINDS and item.status=='SUCCEEDED' for item in _operations(session,parent.id)):
        from .semantic_phase_context import build_semantic_phase_context
        phase_context=build_semantic_phase_context(session,parent,op,'CONFIRM_NEXT_STEP')
    validate_current_continuation(session,parent,phase_context=phase_context)
    consume_auth(session, task=child, confirmation=confirmation,
                 challenge_id=payload.auth_challenge_id, user_id=principal.user_id, consume=False)
    execution = _job(session, parent, "EXECUTE", f"execute:{op.id}", {
        "operation_id": op.id, "request": payload.model_dump(),
    })
    if execution.status == "FAILED":
        execution.status = "QUEUED"
        execution.payload_json = {"operation_id": op.id, "request": payload.model_dump()}
        execution.next_run_at = utcnow()
        execution.last_error = None
    op.status = "AUTHORIZED"
    _state(session, parent, "EXECUTING", "确认已接受，等待执行器处理。")
    dialogue_binding = None
    if any(session.get(RuntimeRecord, parent.id).metadata_json.get(key) for key in
           ("owned_user_dialogue", "dialogue_slot_update", "dialogue_result_reference", "cloud_semantic_intent")):
        from .conversation_provenance import dialogue_authorization_binding
        dialogue_binding = dialogue_authorization_binding(session, parent, execution, op, payload)
        execution.payload_json = {**execution.payload_json, "dialogue_authorization_binding": dialogue_binding}
    from .execution_provenance import accepted_authorization
    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                 event_type="ACTION_AUTHORIZED", payload={"operation_id": op.id, "simulation_auth": bool(payload.auth_challenge_id),
                    **({"dialogue_authorization_binding": dialogue_binding} if dialogue_binding else {}),
                    "authorization_provenance": accepted_authorization(session, parent, child, confirmation, op, payload)})
    return parent


def cancel_runtime(session: Session, principal: Principal, task_id: str, expected_version: int) -> Task:
    parent = owned_task(session, principal, task_id, lock=True)
    if parent.version != expected_version:
        raise DomainError("TASK_VERSION_CHANGED", "任务版本已变化", 409)
    if parent.state in {"EXECUTING", "UNKNOWN", "SUCCEEDED", "CLOSED"}:
        raise DomainError("TASK_NOT_CANCELLABLE", "请先等当前操作结果确定", 409)
    for op in _operations(session, parent.id):
        if op.status == "SUCCEEDED":
            continue
        op.status = "CANCELLED"
        if op.child_task_id:
            child = session.get(Task, op.child_task_id)
            from .banking_extensions import cancel_schedules_for_task
            cancel_schedules_for_task(session, child.id)
            child.state = "CLOSED"
            child.version += 1
            for confirmation in session.scalars(select(Confirmation).where(
                Confirmation.task_id == child.id, Confirmation.status == "PENDING")):
                confirmation.status = "CANCELLED"
    for job in session.scalars(select(WorkflowJob).where(WorkflowJob.task_id == parent.id, WorkflowJob.status == "QUEUED")):
        job.status = "CANCELLED"
    _state(session, parent, "CLOSED", "未执行步骤已取消；已有回执与状态保留。")
    return parent


def clarify_runtime(session: Session, principal: Principal, task_id: str, message: str,
                    expected_version: int, revise: bool = False) -> Task:
    from .redaction import redact_credentials
    message = redact_credentials(message)
    from .contracts import ClarifyTaskRequest
    from .services import clarify_task
    parent = owned_task(session, principal, task_id, lock=True)
    validate_runtime_family_context(session, parent)
    _require_live_task(session, parent)
    if parent.version != expected_version:
        raise DomainError("TASK_VERSION_CHANGED", "任务版本已变化", 409)
    if parent.state in {"EXECUTING", "UNKNOWN", "SUCCEEDED", "CLOSED"}:
        raise DomainError("TASK_NOT_REVISABLE", "请先确定执行结果；完成任务请发起新请求", 409)
    record = session.get(RuntimeRecord, parent.id)
    if record.metadata_json.get("trusted_compensation"):
        raise DomainError("COMPENSATION_PLAN_FIXED", "恢复提案已绑定原操作；如需其他操作请取消并新建任务", 409)
    if record.metadata_json.get("family_origin"):
        raise DomainError("FAMILY_APPROVED_REQUEST_FIXED", "本人已受理的协助目标固定；如需修改请重新提出并受理", 409)
    count = record.metadata_json.get("clarification_count", 0)
    if count >= 8:
        raise DomainError("CLARIFICATION_BUDGET_EXCEEDED", "补充轮次已达上限，请整理目标后新建任务", 409)
    ops = _operations(session, parent.id)
    pending_info = next((op for op in ops if op.status == "NEED_CLARIFICATION" and op.child_task_id), None)
    memo_completion = None
    if pending_info and not revise and session.get(Task, pending_info.child_task_id).result_json.get("error") == "TRANSFER_MEMO_INCOMPLETE":
        from .intelligence import recompose_transfer_memo
        memo_completion = recompose_transfer_memo(parent.message, message)
        if memo_completion is None:
            raise DomainError("TRANSFER_MEMO_COMPLETION_REQUIRED", "请补充唯一转账的完整备注或明确无需备注；金额和收款人已保留。", 422)
    extension_intents = {"FRAUD_ANALYSIS", "FAMILY_ASSISTANCE", "GOAL_CASHFLOW", "CARBON_BENEFITS",
        "CUSTOMER_BENEFITS", "CUSTOMER_PREFERENCES"}
    if pending_info and not revise and memo_completion is None and session.get(Task, pending_info.child_task_id).intent_type not in extension_intents:
        child = session.get(Task, pending_info.child_task_id)
        clarify_task(session, principal, child.id,
                     ClarifyTaskRequest(message=message, expected_task_version=child.version), from_runtime=True)
        pending_info.status = _child_status(child)
        record.metadata_json = {**record.metadata_json, "clarification_count": count + 1,
                                "input_revision": record.metadata_json.get("input_revision", 0) + 1}
        refresh_parent(session, parent)
        return parent
    # A new plan may reuse completed operations; never invalidate their original receipts.
    for op in ops:
        if op.status != "SUCCEEDED":
            op.active = 0
            op.status = "CANCELLED"
            if op.child_task_id:
                from .banking_extensions import cancel_schedules_for_task
                cancel_schedules_for_task(session, op.child_task_id)
                for confirmation in session.scalars(select(Confirmation).where(
                    Confirmation.task_id == op.child_task_id, Confirmation.status == "PENDING")):
                    confirmation.status = "CANCELLED"
                for auth in session.scalars(select(AuthChallenge).where(AuthChallenge.task_id == op.child_task_id)):
                    if auth.status in {"PENDING", "VERIFIED"}:
                        auth.status = "REVOKED"
    messages = record.metadata_json.get("messages", [parent.message]) + [message]
    parent.message = memo_completion if memo_completion is not None else (message if revise else f"{parent.message}；补充：{message}")
    record.metadata_json = {**record.metadata_json, "messages": messages, "clarification_count": count + 1,
                            "input_revision": record.metadata_json.get("input_revision", 0) + 1}
    # A revise before the original PLAN is leased must replace that queued job,
    # not leave two jobs which both plan the new message and duplicate actions.
    for old_job in session.scalars(select(WorkflowJob).where(WorkflowJob.task_id == parent.id,
            WorkflowJob.kind == "PLAN", WorkflowJob.status.in_(["QUEUED", "LEASED"]))):
        old_job.status = "SUPERSEDED"
        old_job.completed_at = utcnow()
        old_job.lease_expires_at = None
    if parent.state == "QUEUED":
        parent.version += 1
    _state(session, parent, "QUEUED", "正在为新目标生成新版本计划，已完成步骤不会重做。")
    _job(session, parent, "PLAN", f"plan:{parent.id}:{record.plan_version}:{count + 1}", {})
    return parent


def _lease_job(factory, worker_id: str) -> str | None:
    with factory.begin() as session:
        now = utcnow()
        job = session.scalar(select(WorkflowJob).where(
            or_(and_(WorkflowJob.status == "QUEUED", WorkflowJob.next_run_at <= now),
                and_(WorkflowJob.status == "LEASED", WorkflowJob.lease_expires_at <= now)),
        ).order_by(WorkflowJob.next_run_at, WorkflowJob.created_at).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        parent = session.scalar(select(Task).where(Task.id == job.task_id).with_for_update())
        if job.kind not in {"RECONCILE", "CUSTOMER_REPLY"} and expire_runtime_task(session, parent):
            return None
        if job.attempts >= job.max_attempts:
            job.status = "FAILED"
            job.last_error = "RETRY_BUDGET_EXCEEDED"
            if job.kind == "CUSTOMER_REPLY":
                from .customer_reply_jobs import unavailable_reply
                record = session.get(RuntimeRecord, parent.id)
                if record and (record.metadata_json.get("customer_reply") or {}).get("fingerprint") == job.payload_json.get("fingerprint"):
                    unavailable_reply(record, "CUSTOMER_REPLY_PREVIOUS_OUTCOME_UNKNOWN_NO_REISSUE")
            elif job.kind == "RECONCILE":
                _state(session, parent, "UNKNOWN", "自动查单仍无法确定结果，需人工核验，暂不重复执行。")
            elif job.kind == "EXECUTE":
                op = session.get(RuntimeOperation, job.payload_json.get("operation_id"))
                call = session.scalar(select(ToolCall).where(ToolCall.task_id == op.child_task_id,
                    ToolCall.idempotency_key == op.idempotency_key)) if op else None
                if call and call.status == "SUCCESS":
                    op.status = _child_status(session.get(Task, op.child_task_id))
                    _complete_job(job, call.result_json)
                elif op:
                    op.status = "FAILED"
                    child = session.get(Task, op.child_task_id)
                    child.result_json = {**child.result_json, "error": "RETRY_BUDGET_EXCEEDED", "message": "执行尝试预算已耗尽，未取得成功回执。"}
                refresh_parent(session, parent, prepare_ready=False)
            else:
                _state(session, parent, "FAILED", "任务尝试次数已耗尽，请查看回执。")
            return None
        job.status = "LEASED"
        job.attempts += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=90 if job.kind == "CUSTOMER_REPLY" else 420)
        return job.id


def _complete_job(job: WorkflowJob, result: dict | None = None) -> None:
    job.status = "SUCCEEDED"
    job.result_json = result or {}
    job.completed_at = utcnow()
    job.lease_expires_at = None


def run_one_job(factory, gateway: ModelGateway, worker_id: str | None = None) -> bool:
    worker_id = worker_id or f"worker-{os.getpid()}-{uuid4().hex}"
    job_id = _lease_job(factory, worker_id)
    if job_id is None:
        return False
    kind = "UNKNOWN"
    planning_revision = None
    trusted_proposal = None
    semantic_proof = None
    execution_evidence = None
    try:
        with factory.begin() as session:
            job = session.get(WorkflowJob, job_id)
            parent = session.scalar(select(Task).where(Task.id == job.task_id).with_for_update())
            if parent.state == "CLOSED":
                job.status = "CANCELLED"
                return True
            kind = job.kind
            if kind in {"PLAN", "EXECUTE"}:
                validate_runtime_family_context(session, parent)
                from .conversation_provenance import validate_dialogue_actor_epoch
                validate_dialogue_actor_epoch(session,parent,session.get(RuntimeRecord,parent.id).metadata_json)
            if kind == "PLAN":
                record = session.get(RuntimeRecord, parent.id)
                planning_revision = record.metadata_json.get("input_revision", 0)
                if any(record.metadata_json.get(key) for key in ("owned_user_dialogue","dialogue_slot_update","dialogue_result_reference","cloud_semantic_intent")) and parent.state in {
                        "EXECUTING", "UNKNOWN", "AUTHORIZED", "SUCCEEDED", "FAILED", "CLOSED"}:
                    job.status, job.completed_at = "SUPERSEDED", utcnow()
                    return True  # Never overwrite a newer execution/receipt state.
                from .extensions import validate_family_origin
                validate_family_origin(session, parent)
                if record.metadata_json.get("trusted_compensation"):
                    from .compensation import validate_trusted_compensation
                    trusted_proposal = validate_trusted_compensation(session, parent)
                _state(session, parent, "UNDERSTANDING", "正在理解目标与必要信息。")
                message = parent.message
                context = {"completed_actions": [op.args_json for op in _operations(session, parent.id) if op.status == "SUCCEEDED"]}
                from .semantic_proposal import load_verified_semantic_intent,PRIVATE_KEY as SEMANTIC_KEY
                semantic_proof=load_verified_semantic_intent(session,parent,record.metadata_json)
                from .semantic_requirements import require_semantic_proof
                require_semantic_proof(session,parent,record.metadata_json,semantic_proof)
                if semantic_proof:
                    context[SEMANTIC_KEY]=semantic_proof
                    context['semantic_intent']=semantic_proof.public()
                from .owned_user_dialogue import load_verified_dialogue, PRIVATE_KEY
                dialogue = load_verified_dialogue(session, parent, record.metadata_json)
                if dialogue:
                    context[PRIVATE_KEY] = dialogue
                    context["owned_user_dialogue"] = dialogue.public()
                from .dialogue_slot_updates import load_verified_slot_update, PRIVATE_KEY as SLOT_KEY
                slot_update = load_verified_slot_update(session, parent, record.metadata_json)
                if slot_update:
                    context[SLOT_KEY] = slot_update
                    context["dialogue_slot_update"] = slot_update.public()
                if not slot_update:
                    from .dialogue_reference_integration import load_verified_result_reference, PRIVATE_KEY as REFERENCE_KEY
                    reference = load_verified_result_reference(session, parent, record.metadata_json)
                    if reference:
                        context[REFERENCE_KEY] = reference
                        context["dialogue_result_reference"] = reference.public()
                from .agent_context import planning_facts
                from .planning_fact_projection import requires_prior_planning_context
                if semantic_proof is not None:
                    # The model has already selected typed capabilities. No
                    # keyword-purpose parser or bank-fact preload is necessary
                    # to compile them; each chosen domain tool reads fresh facts.
                    from .capability_catalog import catalog
                    from .permissions import actor_role
                    registered=catalog(actor_role(session,parent.sandbox_id,parent.user_id))
                    context['authoritative_facts']={'available_actions':[entry['kind'] for entry in registered['capabilities']],
                        'fact_projection':{'version':'semantic-domain-read-on-demand-v1','authority':'ROLE_CATALOG_NOT_BANK_FACTS',
                            'selected_actions':[action.kind for action in semantic_proof.expected_proposal.actions],
                            'bank_facts_preloaded':False,'catalog_version':registered['version']}}
                else:
                    context["authoritative_facts"] = planning_facts(session, Principal(parent.user_id, parent.sandbox_id),
                        message=message, preserve_context=bool(planning_revision or record.metadata_json.get("clarification_count")
                            or context["completed_actions"] or requires_prior_planning_context(record.metadata_json.get("conversation_context"), message)
                            or record.metadata_json.get("trusted_compensation")
                            or record.metadata_json.get("family_origin")))
                record.metadata_json = {**record.metadata_json,
                    "planning_fact_projection": context["authoritative_facts"]["fact_projection"]}
                context["allowed_actions"] = context["authoritative_facts"]["available_actions"]
                from .model_routing import read_routing_policy
                context["routing_policy"] = read_routing_policy(session).model_dump()
                context["customer_llm_required"] = bool(record.metadata_json.get("customer_reply_enabled"))
                from .simulation_models import SimulationState
                simulation = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == parent.sandbox_id))
                if simulation:
                    context["business_time"] = simulation.business_time.isoformat()
            if kind == "EXECUTE":
                from .simulation_models import SimulationState
                simulation = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == parent.sandbox_id))
                fault = simulation.fault_mode if simulation else "NONE"
                op = session.get(RuntimeOperation, job.payload_json["operation_id"])
                if fault == "PRICE_CHANGED" and op.action_kind in {"WEALTH_BUY", "WEALTH_REDEEM"}:
                    child = session.get(Task, op.child_task_id)
                    product_id = next((node.get("product_id") for node in child.plan_json.get("nodes", [])
                                       if node.get("product_id")), None)
                    product = session.get(InvestmentProduct, product_id) if product_id else None
                    if product and product.sandbox_id == parent.sandbox_id:
                        product.version += 1
        if kind == "CUSTOMER_REPLY":
            from .customer_reply_jobs import process_customer_reply_job
            return process_customer_reply_job(factory, gateway, job_id, worker_id)
        if kind == "EXECUTE" and fault == "DELAY":
            time.sleep(1)  # simulated bank latency, outside the domain transaction
        if kind == "PLAN":
            # Network/model inference happens without holding a database transaction.
            advice = diagnostics = None
            if trusted_proposal is not None:
                proposal = trusted_proposal
            elif semantic_proof is not None:
                proposal=semantic_proof.expected_proposal
                from .semantic_plan_diagnostics import SemanticPlanDiagnostics
                diagnostics=SemanticPlanDiagnostics.from_proof(semantic_proof)
            elif hasattr(gateway, "propose_with_advice"):
                planned = gateway.propose_with_advice(message, context=context)
                proposal, advice, diagnostics = planned.proposal, planned.advice, planned.diagnostics
            else:
                proposal = gateway.propose(message, context=context)
            # Fetch the public reference rate without a database transaction.
            # A proposed or frontend-provided rate is never settlement authority.
            fx_market_rate = fx_market_error = None
            from .intelligence import transfer_memo_issue
            if any((action.kind.startswith("FX_") and not transfer_memo_issue(action, message))
                    or action.kind in {'CREDIT_REPAY', 'CREDIT_SETTLE_INSTALLMENT'}
                        and action.slots.currency == 'USD' and action.slots.source_currency == 'CNY'
                    for action in proposal.actions):
                from .fx_rates import get_current_market_rate
                try:
                    fx_market_rate = get_current_market_rate()
                except DomainError as error:
                    fx_market_error = {"code": error.code, "message": error.message}
            with factory.begin() as session:
                job = session.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
                if job.lease_owner != worker_id or job.status != "LEASED":
                    return True
                parent = session.scalar(select(Task).where(Task.id == job.task_id).with_for_update())
                if parent.state == "CLOSED":
                    job.status = "CANCELLED"
                    return True
                record = session.get(RuntimeRecord, parent.id)
                if expire_runtime_task(session, parent):
                    return True
                validate_runtime_family_context(session, parent)
                from .extensions import validate_family_origin
                validate_family_origin(session, parent)
                if record.metadata_json.get("input_revision", 0) != planning_revision:
                    job.status = "SUPERSEDED"
                    job.completed_at = utcnow()
                    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                        event_type="STALE_PLAN_DISCARDED", payload={"input_revision": planning_revision})
                    return True
                if any(record.metadata_json.get(key) for key in ("owned_user_dialogue","dialogue_slot_update","dialogue_result_reference","cloud_semantic_intent")) and parent.state != "UNDERSTANDING":
                    job.status, job.completed_at = "SUPERSEDED", utcnow()
                    return True
                if trusted_proposal is not None:
                    from .compensation import validate_trusted_compensation
                    proposal = validate_trusted_compensation(session, parent)
                if semantic_proof is not None:
                    from .semantic_proposal import validate_semantic_proposal
                    validate_semantic_proposal(session,parent,proposal,record.metadata_json)
                    from .semantic_phase_context import validate_semantic_phase_capabilities
                    validate_semantic_phase_capabilities(proposal)
                from .conversation_provenance import validate_continuation_proposal
                validate_continuation_proposal(session, parent, proposal, record.metadata_json)
                from .owned_user_dialogue import validate_dialogue_proposal
                validate_dialogue_proposal(session, parent, proposal, record.metadata_json)
                from .dialogue_slot_updates import validate_slot_update_proposal
                validate_slot_update_proposal(session, parent, proposal, record.metadata_json)
                if not record.metadata_json.get("dialogue_slot_update"):
                    from .dialogue_reference_integration import validate_result_reference_proposal
                    validate_result_reference_proposal(session, parent, proposal, record.metadata_json)
                record.plan_version += 1
                _state(session, parent, "PLANNING", "正在编译注册能力并核验计划。")
                parent.model_label = proposal.model_label[:80]
                if trusted_proposal is None:
                    session.add(ModelInvocation(task_id=parent.id, sandbox_id=parent.sandbox_id,
                        provider_label=parent.model_label, purpose="UNIFIED_SEMANTIC_INTENT_PROPOSAL" if semantic_proof else "INTENT_AND_PLAN_PROPOSAL",
                        outcome="FALLBACK" if proposal.fallback else "SUCCESS"))
                from .intelligence import action_signature, domain_for_action, READ_KINDS
                domains = list(dict.fromkeys(domain_for_action(action.kind) for action in proposal.actions))
                parent.intent_type = "CROSS_SCENARIO" if len(domains) > 1 else (
                    ACTION_INTENTS.get(proposal.actions[0].kind, "UNSUPPORTED") if proposal.actions else "UNSUPPORTED")
                prior = _operations(session, parent.id)
                completed = {}
                for old_op in prior:
                    if old_op.status == "SUCCEEDED":
                        completed.setdefault(canonical_hash(action_signature(old_op.args_json)), []).append(old_op)
                keys = {}
                previous_key = prior[-1].operation_key if prior and prior[-1].status == "SUCCEEDED" else None
                previous_write_key = next((op.operation_key for op in reversed(prior)
                    if op.action_kind not in READ_KINDS and op.status=='SUCCEEDED'),None)
                for index, action in enumerate(proposal.actions):
                    data = action.model_dump()
                    fingerprint = canonical_hash(action_signature(data))
                    if completed.get(fingerprint):
                        keys[index] = completed[fingerprint].pop(0).operation_key
                        previous_key=keys[index]
                        if action.kind not in READ_KINDS:
                            previous_write_key=keys[index]
                        continue
                    key = f"p{record.plan_version}-a{index + 1}"
                    dependencies = [keys[value] for value in action.depends_on if value in keys]
                    # A read explicitly ordered after payment keeps that edge.
                    # An independent query need not wait for money approval.
                    # Writes keep both prior-step and prior-write barriers so
                    # detaching a read can never open a later write in parallel.
                    if action.kind not in READ_KINDS and previous_key and previous_key not in dependencies:
                        dependencies.append(previous_key)
                    if action.kind not in READ_KINDS and previous_write_key and previous_write_key not in dependencies:
                        dependencies.append(previous_write_key)
                    op = RuntimeOperation(parent_task_id=parent.id, sandbox_id=parent.sandbox_id,
                        operation_key=key, fingerprint=fingerprint, action_kind=action.kind,
                        label=action.label or action.kind, args_json=data, depends_on_json=dependencies,
                        ordinal=len(prior) + index, plan_version=record.plan_version,
                        idempotency_key=f"op-{uuid4()}")
                    session.add(op)
                    keys[index] = key
                    previous_key = key
                    if action.kind not in READ_KINDS:
                        previous_write_key=key
                session.flush()
                plan = {"version": record.plan_version, "nodes": [{"node_id": op.operation_key,
                    "operation_id": op.id, "kind": op.action_kind, "args": op.args_json,
                    "depends_on": op.depends_on_json} for op in _operations(session, parent.id)]}
                parent.plan_json = plan
                parent.plan_hash = canonical_hash(plan)
                session.add(PlanSnapshot(task_id=parent.id, version=record.plan_version,
                    plan_hash=parent.plan_hash, proposal_json=proposal.model_dump(), plan_json=plan))
                record.metadata_json = {**record.metadata_json, "summary": proposal.summary,
                    "domains": domains, "multi_step": len(proposal.actions) > 1, "cross_domain": len(domains) > 1,
                    "fx_market_rate": fx_market_rate, "fx_market_error": fx_market_error,
                    "laya_advice": {**advice.model_dump(), "plan_version": record.plan_version,
                                    "input_revision": planning_revision} if advice is not None else None,
                    "planning_diagnostics": diagnostics.model_dump() if diagnostics is not None else None,
                    "clarification_questions": proposal.clarification_questions,
                    "decisions": record.metadata_json.get("decisions", []) + [{
                        "provider": proposal.model_label, "action_count": len(proposal.actions),
                        "plan_version": record.plan_version, "fallback": proposal.fallback,
                        "source": "SERVER_COMPENSATION" if trusted_proposal else "CLOUD_SEMANTIC_RECEIPT" if semantic_proof else "MODEL_GATEWAY",
                        "input_revision": planning_revision, "plan_hash": parent.plan_hash,
                        "planning_diagnostics": diagnostics.model_dump() if diagnostics is not None else None,
                        "laya_advice": {**advice.model_dump(), "plan_version": record.plan_version,
                                       "input_revision": planning_revision} if advice is not None else None,
                        "failure_reasons": getattr(proposal, "failure_reasons", [])}]}
                append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                    event_type="AGENT_PLAN_COMPILED", payload={"plan_version": record.plan_version,
                    "action_count": len(proposal.actions), "model": proposal.model_label})
                if "DIALOGUE_BOUND_OBJECT_MISMATCH" in proposal.failure_reasons:
                    _state(session,parent,"NEED_CLARIFICATION","对象暂时未核对好，已有信息会保留；没有继续办理。")
                elif not proposal.actions:
                    _state(session, parent, "NEED_CLARIFICATION",
                           "；".join(proposal.clarification_questions) or "请说明希望查询或办理的业务。")
                else:
                    refresh_parent(session, parent)
                _complete_job(job)
                from .customer_reply_jobs import enqueue_customer_reply
                enqueue_customer_reply(session, parent)
        elif kind == "EXECUTE":
            with factory.begin() as session:
                job = session.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
                if job.lease_owner != worker_id or job.status != "LEASED":
                    return True
                parent = session.scalar(select(Task).where(Task.id == job.task_id).with_for_update())
                op = session.get(RuntimeOperation, job.payload_json["operation_id"])
                if expire_runtime_task(session, parent):
                    return True
                family_context = validate_runtime_family_context(session, parent)
                from .extensions import validate_family_origin
                validate_family_origin(session, parent)
                if parent.state == "CLOSED" or not op.active:
                    job.status = "CANCELLED"
                    return True
                from .execution_provenance import execution_attempt
                execution_evidence = execution_attempt(job, op)
                from .conversation_provenance import validate_current_continuation
                validate_current_continuation(session, parent, execution_job=job)
                from .simulation_models import SimulationState
                simulation = session.scalar(select(SimulationState).where(SimulationState.sandbox_id == parent.sandbox_id))
                fault = simulation.fault_mode if simulation else "NONE"
                if fault == "PRE_COMMIT_FAILURE":
                    raise DomainError("SIMULATED_PRE_COMMIT_FAILURE", "服务在提交前拒绝，账户没有变化", 409)
                op.status = "RUNNING"
                execution_evidence = {**execution_evidence, "dispatch_started": True}
                append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                             event_type="ACTION_STARTED", payload={"operation_id": op.id,
                                "execution_provenance": execution_evidence})
                child = confirm_task_action(session, Principal(parent.user_id, parent.sandbox_id,
                    family_context=family_context),
                    op.child_task_id, ConfirmTaskRequest.model_validate(job.payload_json["request"]), op.idempotency_key,
                    from_runtime=True)
                op.status = _child_status(child)
                if fault == "AFTER_COMMIT_RESPONSE_LOST":
                    op.status = "UNKNOWN"
                    job.status = "UNKNOWN"
                    _job(session, parent, "RECONCILE", f"reconcile:{op.id}", {"operation_id": op.id}, delay=1)
                    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                                 event_type="SIMULATED_RESPONSE_LOST", payload={"operation_id": op.id})
                else:
                    _complete_job(job, child.result_json)
                refresh_parent(session, parent)
                from .customer_reply_jobs import enqueue_customer_reply
                enqueue_customer_reply(session, parent)
        elif kind == "RECONCILE":
            with factory.begin() as session:
                job = session.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
                if job.lease_owner != worker_id:
                    return True
                parent = session.scalar(select(Task).where(Task.id == job.task_id).with_for_update())
                op = session.get(RuntimeOperation, job.payload_json["operation_id"])
                call = session.scalar(select(ToolCall).where(ToolCall.task_id == op.child_task_id,
                                                             ToolCall.idempotency_key == op.idempotency_key))
                if call and call.status == "SUCCESS":
                    op.status = _child_status(session.get(Task, op.child_task_id))
                    append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                                 event_type="ACTION_RECONCILED", payload={"operation_id": op.id, "status": "SUCCESS"})
                    _complete_job(job, call.result_json)
                    refresh_parent(session, parent)
                    from .customer_reply_jobs import enqueue_customer_reply
                    enqueue_customer_reply(session, parent)
                else:
                    job.status = "QUEUED"
                    job.next_run_at = utcnow() + timedelta(seconds=2)
                    job.last_error = "RESULT_STILL_UNKNOWN"
        return True
    except Exception as exc:
        logger.exception("workflow_job_failed job_id=%s kind=%s", job_id, kind)
        with factory.begin() as session:
            job = session.scalar(select(WorkflowJob).where(WorkflowJob.id == job_id).with_for_update())
            if job is None or job.lease_owner != worker_id or job.status != "LEASED":
                return True
            parent = session.scalar(select(Task).where(Task.id == job.task_id).with_for_update())
            if parent.state == "CLOSED":
                job.status = "CANCELLED"
                return True
            record = session.get(RuntimeRecord, parent.id)
            if job.kind == "PLAN" and planning_revision is not None and record.metadata_json.get("input_revision", 0) != planning_revision:
                job.status = "SUPERSEDED"
                job.completed_at = utcnow()
                return True
            if job.kind == "RECONCILE":
                job.status = "QUEUED"
                job.next_run_at = utcnow() + timedelta(seconds=2)
                job.last_error = f"QUERY_{type(exc).__name__}"
                _state(session, parent, "UNKNOWN", "查单暂时中断，继续核验；暂不重复执行。")
                append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                    event_type="RECONCILE_RETRY_SCHEDULED", payload={"error": type(exc).__name__})
                return True
            job.status = "FAILED"
            job.last_error = getattr(exc, "code", type(exc).__name__)
            controlled_failure = None
            failure_audit = {}
            if job.kind == "EXECUTE":
                op = session.get(RuntimeOperation, job.payload_json.get("operation_id"))
                if op:
                    op.status = "WAITING_CONFIRMATION" if job.last_error in {"AUTH_GRANT_INVALID", "STEP_UP_REQUIRED"} else "FAILED"
                    child = session.get(Task, op.child_task_id)
                    child.result_json = {**child.result_json, "error": job.last_error,
                                         "message": getattr(exc, "message", "执行失败，请重新核对计划。")}
                refresh_parent(session, parent, prepare_ready=False)
            else:
                _state(session, parent, "FAILED", "规划暂时无法完成，请重新描述目标。")
                from .planning_failure_facts import record_plan_failure, VERSION as FAILURE_VERSION
                controlled_failure = record_plan_failure(parent,job,exc)
                if job.kind == 'PLAN':
                    # Version ALL new failures: a non-DomainError with a code
                    # attribute must never downgrade to legacy code-only facts.
                    failure_audit={'job_id':job.id,'planning_failure_capture_version':FAILURE_VERSION}
                if controlled_failure:
                    parent.result_json={**parent.result_json,'message':'这次付款准备未完成，请查看具体说明。'}
                    failure_audit['controlled_planning_failure_hash']=canonical_hash(controlled_failure)
            append_audit(session, sandbox_id=parent.sandbox_id, task_id=parent.id,
                         event_type="WORKFLOW_JOB_FAILED", payload={"kind": job.kind, "error": job.last_error,
                            **failure_audit,
                            **({"execution_provenance": {**execution_evidence, "stage": "FAILED_ATTEMPT"}}
                               if job.kind == "EXECUTE" and execution_evidence is not None else {})})
        return True


def refresh_waiting_tasks(session: Session) -> int:
    parents = list(session.scalars(select(Task).join(RuntimeRecord, RuntimeRecord.task_id == Task.id).where(
        RuntimeRecord.kind == "PARENT", Task.state == "WAITING").with_for_update(skip_locked=True)))
    for parent in parents:
        refresh_parent(session, parent)
        from .customer_reply_jobs import enqueue_customer_reply
        enqueue_customer_reply(session, parent)
    return len(parents)
