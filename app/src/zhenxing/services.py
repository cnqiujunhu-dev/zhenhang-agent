from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .analytics import analyze_bills  # Public compatibility export used by v1 routes.

from .cards import prepare_card_task
from .audit import append_audit, verify_audit_chain
from .contracts import (
    BillAnalysis,
    CardView,
    CancelTaskRequest,
    ClarifyPayeeRequest,
    ClarifyTaskRequest,
    ConfirmTaskRequest,
    DomainError,
    FinancialIntentContract,
    TaskView,
)
from .cross import prepare_cross_task
from .model_gateway import ModelGateway, RuleIntentModel
from .models import (
    Account,
    AuditEvent,
    Card,
    Confirmation,
    DemoSession,
    EvidenceSnapshot,
    ModelInvocation,
    PolicyDecisionRecord,
    Task,
    ToolCall,
    new_id,
    utcnow,
)
from .planning import build_lock_plan, canonical_hash
from .platform import (
    add_task_nodes,
    append_message,
    get_or_create_conversation,
    record_bill_evidence,
    record_model_invocation,
    record_policy,
    task_nodes,
)
from .seed import create_demo_sandbox
from .subscriptions import prepare_subscription_task
from .tools import ToolGateway
from .transfers import prepare_transfer_task
from .workflow import transition_task
from .wealth import prepare_wealth_task
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from . import access_models  # Register account tables for isolated metadata fixtures.
from . import family_account_context_models  # Register independent family bindings.
from . import dialogue_state_models  # Register durable conversational focus.


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Principal:
    user_id: str
    sandbox_id: str
    role: str = "DEMO_OWNER"
    agent_id: str = "orchestrator"
    family_context: dict | None = None


def create_session(session: Session, profile: str = "baseline") -> dict:
    sandbox, user, _card = create_demo_sandbox(session, profile=profile)
    from .card_identifiers import bootstrap_card_identifiers
    bootstrap_card_identifiers(session, user_id=user.id)
    from .extensions import ensure_extension_fixtures
    ensure_extension_fixtures(session, sandbox.id, user.id)
    get_or_create_conversation(session, sandbox.id, user.id)
    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(hours=4)
    session.add(
        DemoSession(
            sandbox_id=sandbox.id,
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
        )
    )
    return {
        "token": token,
        "sandbox_id": sandbox.id,
        "user_id": user.id,
        "expires_at": expires_at.isoformat(),
    }


def authenticate(session: Session, authorization: str | None) -> Principal:
    from .accounts import authenticate_access_session
    return authenticate_access_session(session, authorization)


def owned_task(session: Session, principal: Principal, task_id: str, *, lock: bool = False) -> Task:
    query = select(Task).where(
        Task.id == task_id,
        Task.user_id == principal.user_id,
        Task.sandbox_id == principal.sandbox_id,
    )
    if lock:
        query = query.with_for_update()
    task = session.scalar(query)
    if task is None:
        raise DomainError("TASK_NOT_FOUND", "任务不存在", 404)
    return task


def _require_runtime_command(session: Session, task: Task, from_runtime: bool) -> None:
    from .runtime_models import RuntimeRecord
    if not from_runtime and session.get(RuntimeRecord, task.id) is not None:
        raise DomainError("RUNTIME_COMMAND_REQUIRED", "请通过任务运行时操作该任务", 403)


def task_view(session: Session, task: Task) -> TaskView:
    pending = list(session.scalars(select(Confirmation).where(
        Confirmation.task_id == task.id,
        Confirmation.status == "PENDING",
    )))
    by_node = {item.node_key: item for item in pending}
    statuses = {item.node_key: item.status for item in task_nodes(
        session, task_id=task.id, sandbox_id=task.sandbox_id,
    )}
    confirmation = next((by_node[item["node_id"]]
                         for item in task.plan_json.get("nodes", [])
                         if item["node_id"] in by_node and all(
                             statuses.get(dependency) == "SUCCEEDED"
                             for dependency in item.get("depends_on", [])
                         )), None)
    analysis = BillAnalysis.model_validate(task.analysis_json) if task.analysis_json else None
    return TaskView(
        task_id=task.id,
        state=task.state,
        version=task.version,
        intent_type=task.intent_type,
        model_label=task.model_label,
        message=task.message,
        fic=FinancialIntentContract.model_validate(task.fic_json) if task.fic_json else None,
        analysis=analysis,
        plan=task.plan_json or {},
        plan_hash=task.plan_hash,
        confirmation_id=confirmation.id if confirmation else None,
        confirmation_node_key=confirmation.node_key if confirmation else None,
        result=task.result_json or {},
    )


def create_task(
    session: Session,
    principal: Principal,
    message: str,
    gateway: ModelGateway,
    *, proposed_action: dict | None = None, source_context: dict | None = None,
) -> Task:
    from .permissions import require_banking_actor
    require_banking_actor(session, principal)
    from .redaction import redact_credentials
    message = redact_credentials(message)
    from .compiled_actions import validate_compiled_action
    compiled=validate_compiled_action(session,principal,message,proposed_action,source_context)
    if compiled is not None:
        from .capability_catalog import action_intent
        from .model_gateway import IntentDecision
        proposed=IntentDecision(action_intent(compiled.kind),'server-verified-semantic-domain-compiler',1.0)
        deterministic=proposed
    else:
        proposed = gateway.classify(message)
        deterministic = RuleIntentModel().classify(message)
    intent_type = proposed.intent_type
    if deterministic.intent_type == "CROSS_SCENARIO":
        # Explicit multi-domain user text must not be silently collapsed to one model-proposed scene.
        intent_type = "CROSS_SCENARIO"
    if intent_type in {"BILL_ANOMALY_LOCK_CARD", "CROSS_SCENARIO"} \
            and deterministic.intent_type != intent_type:
        # A model may propose a composed write, but the user's text must request every sensitive step.
        intent_type = deterministic.intent_type

    task = Task(
        sandbox_id=principal.sandbox_id,
        user_id=principal.user_id,
        message=message,
        intent_type=intent_type,
        state="NEED_CLARIFICATION",
        model_label=proposed.model_label,
        fic_json={},
        plan_json={},
        analysis_json={},
        result_json={},
    )
    session.add(task)
    session.flush()
    conversation = get_or_create_conversation(session, principal.sandbox_id, principal.user_id)
    append_message(
        session,
        conversation_id=conversation.id,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        role="USER",
        content=message,
    )
    if getattr(gateway.primary, "is_model_call", True):
        record_model_invocation(session, task_id=task.id, sandbox_id=principal.sandbox_id,
                                provider_label=proposed.model_label)
    fic = FinancialIntentContract(
        intent_id=new_id(),
        intent_version=1,
        intent_type=intent_type,
        actor_user_id=principal.user_id,
        sandbox_id=principal.sandbox_id,
        source_channel="WEB",
        slots={},
        requested_capabilities=(
            ["bill.analyze.v1", "card.lock.v1"]
            if intent_type == "BILL_ANOMALY_LOCK_CARD"
            else (["bill.analyze.v1"] if intent_type == "BILL_ANALYSIS" else [])
        ),
        status="NEED_CLARIFICATION" if intent_type == "UNSUPPORTED" else "PROPOSED",
    )
    task.fic_json = fic.model_dump()
    append_audit(
        session,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        event_type="TASK_CREATED",
        payload={"intent_type": intent_type, "model_label": proposed.model_label},
    )
    if intent_type in {"CUSTOMER_BENEFITS", "CUSTOMER_PREFERENCES"}:
        from .customer_profile_runtime import prepare_customer_profile_task
        if proposed_action is None:
            task.result_json = {"message": "请通过对话入口说明需要查看的资料或修改的偏好。"}
            return task
        return prepare_customer_profile_task(session, task, proposed_action, context=source_context)
    if intent_type in {'CREDIT_MANAGEMENT', 'FAMILY_OCCASIONS', 'LIFE_PLAN', 'MERCHANT_SERVICE', 'OWN_ACCOUNT_TRANSFER'}:
        if proposed_action is None:
            task.result_json = {'message': '你想办理哪项服务？可以直接告诉我。'}
            return task
        from .upgraded_runtime import prepare_upgraded_task
        return prepare_upgraded_task(session, task, proposed_action, source_context)
    if proposed_action and proposed_action.get('kind') == 'TRANSFER_SCHEDULE_VIEW':
        from .scheduled_transfer_reads import prepare_task
        return prepare_task(session, task=task, conversation_id=conversation.id, proposed_action=proposed_action)
    if proposed_action and proposed_action.get('kind') == 'AA_VIEW':
        from .aa_obligations import prepare_aa_view_task
        return prepare_aa_view_task(session, task=task, conversation_id=conversation.id, proposed_action=proposed_action)
    if proposed_action and proposed_action.get('kind') == 'AA_PAY':
        from .aa_participant_payments import prepare_aa_payment_child
        return prepare_aa_payment_child(session, task=task, conversation_id=conversation.id,
            proposed_action=proposed_action, source_context=source_context)
    if intent_type in {"FRAUD_ANALYSIS", "FAMILY_ASSISTANCE", "GOAL_CASHFLOW", "CARBON_BENEFITS"}:
        from .extensions import prepare_extension_task
        if proposed_action is None:
            task.result_json = {"message": "请通过v2受控任务入口提供明确扩展场景请求。"}
            return task
        return prepare_extension_task(session, task, proposed_action, context=source_context)
    if intent_type in {"FOREX_QUERY", "FOREX_TRANSFER", "FOREIGN_EXCHANGE"}:
        from .fx_domain import prepare_fx_task
        if proposed_action is None:
            task.result_json = {"message": "请通过统一Agent入口明确币种、金额及收款对象；汇率不能由对话自行指定。"}
            return task
        return prepare_fx_task(session, task, proposed_action, source_context or {})
    if intent_type == "CONTACT_MANAGEMENT":
        from .contact_domain import prepare_contact_task
        if proposed_action is None:
            task.result_json = {"message": "请通过统一入口明确联系人账号和本人身份称呼。"}
            return task
        return prepare_contact_task(session, task, proposed_action, source_context)
    if intent_type != proposed.intent_type:
        append_audit(
            session, sandbox_id=principal.sandbox_id, task_id=task.id,
            event_type="INTENT_POLICY_OVERRIDE",
            payload={"proposed": proposed.intent_type, "selected": intent_type,
                     "reason": "DETERMINISTIC_ACTION_BOUNDARY"},
        )
    if intent_type == "TRANSFER":
        return prepare_transfer_task(session, task=task, conversation_id=conversation.id,
                                     proposed_action=proposed_action, source_context=source_context)
    if intent_type in {"TRANSFER_SCHEDULE", "AA_REQUEST"}:
        from .banking_extensions import prepare_extra_task
        return prepare_extra_task(session, task=task, conversation_id=conversation.id,
                                  proposed_action=proposed_action, source_context=source_context)
    if intent_type == "CARD_MANAGEMENT":
        if proposed_action:
            from .credit_runtime import is_credit_reference, prepare_credit_task
            slots = proposed_action.get('slots') or {}
            ref = slots.get('card_number') or slots.get('card_last4')
            try:
                credit_selected = bool(ref and is_credit_reference(session, principal, ref))
            except DomainError as error:
                if error.code != 'PAYMENT_INSTRUMENT_AMBIGUOUS':
                    raise
                task.result_json = {'message': error.message, 'error': error.code}
                return task
            if credit_selected and slots.get('card_type') != 'DEBIT':
                if slots.get('card_balance_currencies') is not None:
                    raise DomainError('CARD_BALANCE_SCOPE_DEBIT_ONLY', '现金币种集合只查询本人借记卡，不把信用额度当作余额。', 422)
                credit_action = dict(proposed_action)
                if credit_action['kind'] == 'CARD_STATUS':
                    credit_action['kind'] = 'CREDIT_VIEW'
                return prepare_credit_task(session, task, credit_action, source_context)
        card_slots = (proposed_action or {}).get('slots') or {}
        if card_slots.get('card_type') is not None or card_slots.get('card_balance_currencies') is not None:
            return prepare_card_task(session, task=task, conversation_id=conversation.id,
                card_type=card_slots.get('card_type'),
                card_reference=card_slots.get('card_number') or card_slots.get('card_last4'),
                card_balance_currencies=card_slots.get('card_balance_currencies'), currency=card_slots.get('currency'))
        return prepare_card_task(session, task=task, conversation_id=conversation.id)
    if intent_type == "SUBSCRIPTION_MANAGEMENT":
        from .intelligence import ProposedAction
        subscription_action=ProposedAction.model_validate(proposed_action) if isinstance(proposed_action,dict) else proposed_action
        if subscription_action is not None and subscription_action.kind in {"SUBSCRIPTION_START","SUBSCRIPTION_RENEW"}:
            from .subscription_commerce import prepare_subscription_commerce_task
            return prepare_subscription_commerce_task(session,task=task,conversation_id=conversation.id,
                action="START" if subscription_action.kind=="SUBSCRIPTION_START" else "RENEW",
                merchant=subscription_action.slots.merchant,
                months=subscription_action.slots.subscription_months if subscription_action.slots.subscription_months is not None else 1,
                auto_renew=subscription_action.slots.subscription_auto_renew,
                source_card_reference=subscription_action.slots.card_number or subscription_action.slots.card_last4,
                source_message=(source_context or {}).get('message',task.message))
        return prepare_subscription_task(session, task=task, conversation_id=conversation.id,
            requested_merchant=subscription_action.slots.merchant if subscription_action is not None else None)
    if intent_type == "WEALTH":
        from .intelligence import ProposedAction
        wealth_action=ProposedAction.model_validate(proposed_action) if isinstance(proposed_action,dict) else proposed_action
        return prepare_wealth_task(session, task=task, conversation_id=conversation.id,
            requested_action=wealth_action,
            requested_product_name=wealth_action.slots.product_name if wealth_action is not None and wealth_action.kind in {"WEALTH_COMPARE", "WEALTH_BUY", "WEALTH_REDEEM"} else None)
    if intent_type == "CROSS_SCENARIO":
        return prepare_cross_task(session, task=task, conversation_id=conversation.id)
    if intent_type not in {"BILL_ANALYSIS", "BILL_ANOMALY_LOCK_CARD"}:
        task.result_json = {
            "message": "该场景已进入平台意图注册表，业务工具仍在接入中；当前不会执行任何操作。"
        }
        append_message(
            session,
            conversation_id=conversation.id,
            sandbox_id=principal.sandbox_id,
            task_id=task.id,
            role="ASSISTANT",
            content=task.result_json["message"],
        )
        return task

    account = session.scalar(
        select(Account).where(
            Account.sandbox_id == principal.sandbox_id,
            Account.user_id == principal.user_id,
        )
    )
    if account is None:
        raise DomainError("ACCOUNT_NOT_FOUND", "未找到当前账户", 500)
    from .financial_diagnostics import analyze_financial_request
    from .bill_period_fallback import allow_fallback_for_bill_task
    report_source = (source_context or {}).get("message", message)
    filter_category = ((proposed_action or {}).get("slots") or {}).get("category_filter") if intent_type == "BILL_ANALYSIS" else None
    verified_read_period = None
    if compiled is not None and intent_type=='BILL_ANALYSIS' and (source_context or {}).get('source_root_task_id'):
        from .semantic_read_scope import inherited_bill_period
        from .reports import bill_request_period
        from .reporting_clock import report_clock
        parent=session.get(Task,source_context['source_root_task_id'])
        verified_read_period=bill_request_period(report_source,report_clock(session,principal.sandbox_id,account.id).calendar_time)
        if parent and verified_read_period is None:
            verified_read_period=inherited_bill_period(session,principal,parent,operation_id=source_context.get('source_operation_id'))
    if verified_read_period is None and filter_category is not None and (source_context or {}).get("source_root_task_id"):
        from .runtime_models import RuntimeRecord
        from .owned_user_dialogue import load_verified_dialogue, READ_RESULT_SCOPE_VERSION
        parent = session.get(Task, source_context["source_root_task_id"])
        record = session.get(RuntimeRecord, parent.id) if parent else None
        if parent and record and record.kind == "PARENT" and (parent.sandbox_id, parent.user_id) == (task.sandbox_id, task.user_id):
            proof = load_verified_dialogue(session, parent, record.metadata_json)
            scope = proof.result_scope if proof and proof.scope == "READ_ONLY" else None
            if isinstance(scope, dict) and scope.get("version") == READ_RESULT_SCOPE_VERSION and scope.get("action") == "BILL_ANALYSIS":
                if scope.get("period_start") and scope.get("period_end"):
                    verified_read_period = (datetime.fromisoformat(scope["period_start"]), datetime.fromisoformat(scope["period_end"]))
            if verified_read_period is None and record.metadata_json.get('dialogue_result_reference'):
                from .reports import bill_request_period
                from .reporting_clock import report_clock
                from .dialogue_reference_integration import inherited_result_reference_bill_period
                if bill_request_period(report_source,report_clock(session,principal.sandbox_id,account.id).calendar_time) is None:
                    verified_read_period=inherited_result_reference_bill_period(session,principal,parent)
    if filter_category is not None and not allow_fallback_for_bill_task(session, task, report_source, source_context):
        task.result_json = {"error": "BILL_CATEGORY_READ_ONLY_REQUIRED", "message": "分类筛选仅用于独立查询，不替代后续银行办理所需的完整账单核验。请先单独查看分类账单。"}
        return task
    try:
        analysis = analyze_financial_request(session, principal.sandbox_id, account.id, report_source,
        allow_recent_fallback=intent_type == "BILL_ANALYSIS" and
            allow_fallback_for_bill_task(session, task, report_source, source_context),
        include_statement_transactions=intent_type == "BILL_ANALYSIS", category_filter=filter_category, period=verified_read_period,
        card_reference=((proposed_action or {}).get('slots') or {}).get('card_number') or ((proposed_action or {}).get('slots') or {}).get('card_last4'),
        currency=((proposed_action or {}).get('slots') or {}).get('currency'),
        instrument_breakdown_version='owned-bill-instrument-breakdown-v1' if intent_type == 'BILL_ANALYSIS' else None,
        query_context_version='owned-bill-query-context-v1' if intent_type == 'BILL_ANALYSIS' else None)
    except DomainError as error:
        if error.code not in {"BILL_CATEGORY_UNKNOWN", "BILL_CATEGORY_INVALID"}:
            raise
        task.result_json = {"error": error.code, "message": error.message}
        return task
    task.analysis_json = analysis.model_dump()
    evidence_id = record_bill_evidence(
        session,
        task_id=task.id,
        sandbox_id=principal.sandbox_id,
        payload={
            "transaction_count": analysis.transaction_count,
            "spending_minor": analysis.spending_minor,
            "period_start": analysis.period_start, "period_end": analysis.period_end,
            "coverage": analysis.coverage, "financial_insights": analysis.financial_insights,
            "period_fallback": analysis.period_fallback,
            "category_filter": analysis.category_filter,
            "anomaly_ids": [item["transaction_id"] for item in analysis.anomalies],
        },
    )
    append_audit(
        session,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        event_type="BILL_ANALYZED",
        payload={
            "transaction_count": analysis.transaction_count,
            "spending_minor": analysis.spending_minor,
            "period_start": analysis.period_start, "period_end": analysis.period_end,
            "coverage": analysis.coverage, "financial_insights": analysis.financial_insights,
            "period_fallback": analysis.period_fallback,
            "category_filter": analysis.category_filter,
            "anomaly_ids": [item["transaction_id"] for item in analysis.anomalies],
        },
    )
    if intent_type == "BILL_ANALYSIS":
        task.plan_json = {
            "nodes": [
                {
                    "node_id": "analyze-bill",
                    "tool_name": "bill.analyze.v1",
                    "status": "SUCCEEDED",
                    "evidence_id": evidence_id,
                }
            ]
        }
        add_task_nodes(session, task_id=task.id, sandbox_id=principal.sandbox_id, plan=task.plan_json)
        fic.status = "READY"
        task.fic_json = fic.model_dump()
        transition_task(task, "SUCCEEDED")
        task.result_json = {"message": ("账单查询已返回，但所选期间资料尚未覆盖，不能据此认定零消费或计算期间增长。"
                                      if analysis.coverage and analysis.coverage["status"] in {"UNKNOWN", "INSUFFICIENT"}
                                      else "账单分析已完成，未执行任何卡片或资金操作。")}
        append_audit(
            session,
            sandbox_id=principal.sandbox_id,
            task_id=task.id,
            event_type="TASK_SUCCEEDED",
            payload={"read_only": True},
        )
        append_message(
            session,
            conversation_id=conversation.id,
            sandbox_id=principal.sandbox_id,
            task_id=task.id,
            role="ASSISTANT",
            content=task.result_json["message"],
        )
        return task

    if analysis.coverage and analysis.coverage["status"] in {"UNKNOWN", "INSUFFICIENT"}:
        task.result_json = {"message": "所选期间资料尚未覆盖，无法核对待核验交易关联卡片；未执行卡片操作。"}
        return task
    if any(item.get('instrument_type') == 'CREDIT' or not item.get('transaction_id') for item in analysis.anomalies):
        task.result_json = {'message': '已列出需要核对的交易。请明确要锁定的卡片，我会先展示卡片和影响，再请你确认。',
            'reason_code': 'EXPLICIT_CARD_SELECTION_REQUIRED'}
        return task
    anomaly_card_ids = {item["card_id"] for item in analysis.anomalies if item["card_id"]}
    if len(anomaly_card_ids) != 1:
        task.result_json = {"message": "需先确认关联的卡片，当前没有唯一的待核验卡片。"}
        return task
    card_id = next(iter(anomaly_card_ids))
    card = session.scalar(
        select(Card).where(
            Card.id == card_id,
            Card.sandbox_id == principal.sandbox_id,
            Card.user_id == principal.user_id,
            Card.status != ARCHIVED_RESOURCE_STATUS,
        )
    )
    if card is None:
        raise DomainError("CARD_NOT_FOUND", "待核验交易未关联可操作卡片", 409)
    if card.status != "ACTIVE":
        task.result_json = {"message": "卡片已经不在可锁定状态，请先查看卡片状态。"}
        return task

    plan, plan_hash = build_lock_plan(
        task_id=task.id,
        sandbox_id=principal.sandbox_id,
        card_id=card.id,
        card_last4=card.last4,
        card_version=card.version,
        analysis=analysis,
    )
    task.plan_json = plan
    add_task_nodes(session, task_id=task.id, sandbox_id=principal.sandbox_id, plan=plan)
    record_policy(
        session,
        task_id=task.id,
        sandbox_id=principal.sandbox_id,
        node_key="lock-card",
        decision="REQUIRE_CONFIRMATION",
        reason_codes=["CARD_STATE_CHANGE", "USER_REQUESTED_PROTECTION"],
        policy_version="card-lock-v1",
    )
    task.plan_hash = plan_hash
    fic.slots = {
        "card_id": {
            "value": card.id,
            "source": "AUTHORITATIVE_TRANSACTION_LINK",
            "evidence_refs": [item["transaction_id"] for item in analysis.anomalies],
        }
    }
    fic.status = "READY"
    task.fic_json = fic.model_dump()
    transition_task(task, "AWAITING_CONFIRMATION")
    task.result_json = {"message": f"发现 1 笔待核验交易。确认后将临时锁定尾号 {card.last4} 的卡片。"}
    confirmation = Confirmation(
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        user_id=principal.user_id,
        node_key="lock-card",
        action_type="card.lock.v1",
        target_id=card.id,
        plan_hash=plan_hash,
        view_hash=canonical_hash({"plan_hash": plan_hash, "card_last4": card.last4, "action": "TEMPORARY_LOCK"}),
        expires_at=utcnow() + timedelta(minutes=5),
    )
    session.add(confirmation)
    session.flush()
    append_audit(
        session,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        event_type="PLAN_PROPOSED",
        payload={"plan_hash": plan_hash, "card_last4": card.last4, "node_count": 2},
    )
    append_audit(
        session,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        event_type="CONFIRMATION_REQUIRED",
        payload={"confirmation_id": confirmation.id},
    )
    append_message(
        session,
        conversation_id=conversation.id,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        role="ASSISTANT",
        content=task.result_json["message"],
    )
    return task


def confirm_and_lock_card(
    session: Session,
    principal: Principal,
    task_id: str,
    request: ConfirmTaskRequest,
    idempotency_key: str,
) -> Task:
    task = owned_task(session, principal, task_id, lock=True)
    return ToolGateway().dispatch_lock(
        session,
        task=task,
        user_id=principal.user_id,
        sandbox_id=principal.sandbox_id,
        request=request,
        idempotency_key=idempotency_key,
    )


def confirm_task_action(
    session: Session,
    principal: Principal,
    task_id: str,
    request: ConfirmTaskRequest,
    idempotency_key: str,
    *, from_runtime: bool = False,
) -> Task:
    task = owned_task(session, principal, task_id, lock=True)
    _require_runtime_command(session, task, from_runtime)
    gateway = ToolGateway()
    if any(str(node.get('tool_name', '')).startswith('credit.') for node in task.plan_json.get('nodes', [])):
        from .credit_runtime import dispatch_credit_task
        return dispatch_credit_task(session, task=task, principal=principal, request=request, idempotency_key=idempotency_key)
    if task.intent_type in {'FAMILY_OCCASIONS', 'LIFE_PLAN', 'MERCHANT_SERVICE', 'OWN_ACCOUNT_TRANSFER'}:
        from .upgraded_runtime import dispatch_upgraded_task
        return dispatch_upgraded_task(session, task=task, principal=principal, request=request, idempotency_key=idempotency_key)
    if task.intent_type == "CUSTOMER_PREFERENCES":
        from .customer_profile_runtime import dispatch_customer_profile_task
        return dispatch_customer_profile_task(session, task=task, principal=principal,
            request=request, idempotency_key=idempotency_key)
    if task.intent_type == "CONTACT_MANAGEMENT":
        from .contact_domain import dispatch_contact_task
        return dispatch_contact_task(session, task, principal, request, idempotency_key)
    if task.intent_type in {"FOREX_TRANSFER", "FOREIGN_EXCHANGE"}:
        from .fx_domain import dispatch_fx_task
        return dispatch_fx_task(session, task, principal, request, idempotency_key)
    if task.intent_type in {"FRAUD_ANALYSIS", "FAMILY_ASSISTANCE", "GOAL_CASHFLOW", "CARBON_BENEFITS"}:
        from .extensions import dispatch_extension_task
        return dispatch_extension_task(session, task=task, principal=principal,
                                       request=request, idempotency_key=idempotency_key)
    if task.intent_type in {"TRANSFER_SCHEDULE", "AA_REQUEST"}:
        from .banking_extensions import dispatch_extra_task
        return dispatch_extra_task(session, task=task, principal=principal,
                                   request=request, idempotency_key=idempotency_key)
    if task.intent_type == "TRANSFER":
        return gateway.dispatch_transfer(
            session,
            task=task,
            user_id=principal.user_id,
            sandbox_id=principal.sandbox_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    if task.intent_type == "CARD_MANAGEMENT":
        return gateway.dispatch_card_manage(
            session, task=task, user_id=principal.user_id,
            sandbox_id=principal.sandbox_id, request=request,
            idempotency_key=idempotency_key,
        )
    if task.intent_type == "SUBSCRIPTION_MANAGEMENT":
        if request and any(node.get("tool_name")=="subscription.start.v1" for node in (task.plan_json or {}).get("nodes",[])):
            return gateway.dispatch_subscription_start(session,task=task,user_id=principal.user_id,
                sandbox_id=principal.sandbox_id,request=request,idempotency_key=idempotency_key)
        if request and any(node.get("tool_name")=="subscription.renew.v1" for node in (task.plan_json or {}).get("nodes",[])):
            return gateway.dispatch_subscription_renew(session,task=task,user_id=principal.user_id,
                sandbox_id=principal.sandbox_id,request=request,idempotency_key=idempotency_key)
        return gateway.dispatch_subscription_cancel(
            session, task=task, user_id=principal.user_id,
            sandbox_id=principal.sandbox_id, request=request,
            idempotency_key=idempotency_key,
        )
    if task.intent_type == "WEALTH":
        return gateway.dispatch_wealth_order(
            session, task=task, user_id=principal.user_id,
            sandbox_id=principal.sandbox_id, request=request,
            idempotency_key=idempotency_key,
        )
    if task.intent_type == "CROSS_SCENARIO":
        confirmation = session.scalar(select(Confirmation).where(
            Confirmation.id == request.confirmation_id,
            Confirmation.task_id == task.id,
            Confirmation.sandbox_id == principal.sandbox_id,
            Confirmation.user_id == principal.user_id,
        ))
        if confirmation is None:
            raise DomainError("CONFIRMATION_NOT_FOUND", "确认记录不存在", 404)
        if confirmation.node_key == "lock-card":
            return gateway.dispatch_lock(
                session, task=task, user_id=principal.user_id,
                sandbox_id=principal.sandbox_id, request=request,
                idempotency_key=idempotency_key,
            )
        if confirmation.node_key == "cancel-subscription":
            return gateway.dispatch_subscription_cancel(
                session, task=task, user_id=principal.user_id,
                sandbox_id=principal.sandbox_id, request=request,
                idempotency_key=idempotency_key,
            )
    if task.intent_type == "BILL_ANOMALY_LOCK_CARD":
        return gateway.dispatch_lock(
            session,
            task=task,
            user_id=principal.user_id,
            sandbox_id=principal.sandbox_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    raise DomainError("NO_CONFIRMABLE_ACTION", "当前任务没有可执行的确认动作", 409)


def cancel_task(
    session: Session,
    principal: Principal,
    task_id: str,
    request: CancelTaskRequest,
    *, from_runtime: bool = False,
) -> Task:
    task = owned_task(session, principal, task_id, lock=True)
    _require_runtime_command(session, task, from_runtime)
    if task.version != request.expected_task_version:
        raise DomainError("TASK_VERSION_CHANGED", "任务版本已变化，请刷新后重试", 409)
    if task.state not in {"NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "PARTIAL"}:
        raise DomainError("TASK_NOT_CANCELLABLE", "当前任务不能撤销", 409)
    was_partial = task.state == "PARTIAL"
    for confirmation in session.scalars(select(Confirmation).where(
        Confirmation.task_id == task.id,
        Confirmation.sandbox_id == principal.sandbox_id,
        Confirmation.status == "PENDING",
    ).with_for_update()):
        confirmation.status = "CANCELLED"
    transition_task(task, "CLOSED")
    task.result_json = {
        "message": ("已关闭任务；先前完成的操作保留，后续待确认动作已撤销。"
                    if was_partial else "任务已撤销，未执行任何待确认动作。"),
        "partial_effects_retained": was_partial,
        "simulated": True,
    }
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=task.id,
                 event_type="TASK_CLOSED_BY_USER",
                 payload={"partial_effects_retained": was_partial})
    conversation = get_or_create_conversation(session, principal.sandbox_id, principal.user_id)
    append_message(session, conversation_id=conversation.id, sandbox_id=principal.sandbox_id,
                   task_id=task.id, role="ASSISTANT", content=task.result_json["message"])
    return task


def clarify_task_payee(
    session: Session,
    principal: Principal,
    task_id: str,
    request: ClarifyPayeeRequest,
) -> Task:
    task = owned_task(session, principal, task_id, lock=True)
    _require_runtime_command(session, task, False)
    if task.intent_type != "TRANSFER" or task.state != "NEED_CLARIFICATION":
        raise DomainError("TASK_NOT_CLARIFIABLE", "当前任务不需要选择收款人", 409)
    if task.version != request.expected_task_version:
        raise DomainError("TASK_VERSION_CHANGED", "任务版本已变化，请刷新后重试", 409)
    conversation = get_or_create_conversation(session, principal.sandbox_id, principal.user_id)
    append_message(
        session,
        conversation_id=conversation.id,
        sandbox_id=principal.sandbox_id,
        task_id=task.id,
        role="USER",
        content=f"已选择收款人 {request.payee_id}",
    )
    return prepare_transfer_task(
        session,
        task=task,
        conversation_id=conversation.id,
        selected_payee_id=request.payee_id,
    )


def clarify_task(
    session: Session,
    principal: Principal,
    task_id: str,
    request: ClarifyTaskRequest,
    *, from_runtime: bool = False,
) -> Task:
    task = owned_task(session, principal, task_id, lock=True)
    _require_runtime_command(session, task, from_runtime)
    if task.state != "NEED_CLARIFICATION" or task.plan_json:
        raise DomainError("TASK_NOT_CLARIFIABLE", "当前任务不能补充信息", 409)
    if task.version != request.expected_task_version:
        raise DomainError("TASK_VERSION_CHANGED", "任务版本已变化，请刷新后重试", 409)
    if task.intent_type not in {"TRANSFER", "CARD_MANAGEMENT", "SUBSCRIPTION_MANAGEMENT", "WEALTH", "CROSS_SCENARIO"}:
        raise DomainError("TASK_NOT_CLARIFIABLE", "该场景请发起新任务", 409)
    supplement = request.message.strip()
    if not supplement:
        raise DomainError("CLARIFICATION_EMPTY", "补充信息不能为空", 422)
    conversation = get_or_create_conversation(session, principal.sandbox_id, principal.user_id)
    append_message(session, conversation_id=conversation.id, sandbox_id=principal.sandbox_id,
                   task_id=task.id, role="USER", content=supplement)
    if supplement in {"取消", "算了", "不用了", "撤销"} or re.search(
        r"(不要|别|不用|不想|先不).{0,8}(转账|转|买|申购|赎回|锁卡|挂失|补卡|取消订阅)", supplement
    ):
        return cancel_task(session, principal, task_id,
                           CancelTaskRequest(expected_task_version=request.expected_task_version), from_runtime=from_runtime)
    supplement_intent = RuleIntentModel().classify(supplement).intent_type
    if supplement_intent not in {"UNSUPPORTED", task.intent_type} and not (
        task.intent_type == "CROSS_SCENARIO"
        and supplement_intent in {"BILL_ANALYSIS", "CARD_MANAGEMENT", "SUBSCRIPTION_MANAGEMENT"}
    ) and re.search(r"转账|转给|给.{1,12}转\d|汇款|申购|赎回|锁卡|挂失|补卡|取消|退订", supplement):
        raise DomainError("INTENT_SWITCH_REQUIRES_NEW_TASK", "改变业务目标请发起新任务", 409)

    old_result = task.result_json or {}
    selected_payee_id = None
    if task.intent_type == "TRANSFER":
        last4 = re.search(r"(?:尾号|末四位)?\s*(\d{4})\s*$", supplement)
        candidates = old_result.get("payee_candidates", [])
        if last4:
            matches = [item for item in candidates if item["account_last4"] == last4.group(1)]
            if len(matches) == 1:
                selected_payee_id = matches[0]["payee_id"]
        amount_only = re.fullmatch(r"\s*(\d+(?:\.\d{1,2})?)\s*元?\s*", supplement)
        if amount_only and selected_payee_id is None:
            payee_match = re.search(r"(?:给|向)([\u4e00-\u9fa5A-Za-z0-9]{1,12})转(?:账)?", task.message)
            if payee_match is None:
                payee_match = re.search(r"转账给([\u4e00-\u9fa5A-Za-z]{1,12})", task.message)
            if payee_match is not None:
                task.message = f"给{payee_match.group(1)}转{amount_only.group(1)}元"
            else:
                task.message = f"{task.message}，{supplement}"
        elif selected_payee_id is None:
            task.message = f"{task.message}，{supplement}"
    else:
        task.message = f"{task.message}，{supplement}"
    if len(task.message) > 2000:
        raise DomainError("CLARIFICATION_TOO_LONG", "补充后任务内容过长", 422)
    fic = FinancialIntentContract.model_validate(task.fic_json)
    if selected_payee_id is None:
        fic.intent_version += 1
    fic.status = "PROPOSED"
    task.fic_json = fic.model_dump()
    task.result_json = {}
    append_audit(session, sandbox_id=principal.sandbox_id, task_id=task.id,
                 event_type="CLARIFICATION_RECEIVED",
                 payload={"intent_version": fic.intent_version,
                          "message_hash": hashlib.sha256(supplement.encode("utf-8")).hexdigest()})
    if task.intent_type == "TRANSFER":
        result = prepare_transfer_task(session, task=task, conversation_id=conversation.id,
                                       selected_payee_id=selected_payee_id)
    elif task.intent_type == "CARD_MANAGEMENT":
        result = prepare_card_task(session, task=task, conversation_id=conversation.id)
    elif task.intent_type == "SUBSCRIPTION_MANAGEMENT":
        result = prepare_subscription_task(session, task=task, conversation_id=conversation.id)
    elif task.intent_type == "WEALTH":
        result = prepare_wealth_task(session, task=task, conversation_id=conversation.id)
    else:
        result = prepare_cross_task(session, task=task, conversation_id=conversation.id)
    if result.state == "NEED_CLARIFICATION":
        result.version += 1
        if result.result_json.get("message"):
            append_message(session, conversation_id=conversation.id,
                           sandbox_id=principal.sandbox_id, task_id=task.id,
                           role="ASSISTANT", content=result.result_json["message"])
    return result


def list_cards(session: Session, principal: Principal) -> list[CardView]:
    from .fx_banking import fxcardprojection
    from .card_identifiers import card_identifier_view
    from .customer_membership import card_tier_display
    cards = session.scalars(
        select(Card).where(
            Card.sandbox_id == principal.sandbox_id,
            Card.user_id == principal.user_id,
            Card.status != ARCHIVED_RESOURCE_STATUS,
        )
    )
    return [
        CardView(
            card_id=card.id,
            last4=card.last4,
            status=card.status,
            limit_minor=card.limit_minor,
            version=card.version,
            **(card_identifier_view(session, card) or {}),
            **card_tier_display(session, card),
            **{key: value for key, value in fxcardprojection(session, card).items()
               if key in {"currency_mode", "supported_currencies", "balances_by_currency", "fx_card",
                          "fx_profile_version", "usd_wallet_version", "limit_currency"}},
        )
        for card in cards
    ]


def list_recent_tasks(session: Session, principal: Principal, limit: int = 30) -> list[dict]:
    from .customer_history_archive import visible_history
    tasks = session.scalars(
        select(Task)
        .where(Task.sandbox_id == principal.sandbox_id, Task.user_id == principal.user_id,
               visible_history(session, principal, Task.created_at))
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(limit)
    )
    return [{
        "task_id": task.id,
        "state": task.state,
        "intent_type": task.intent_type,
        "message": task.message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    } for task in tasks]


def get_evidence(session: Session, principal: Principal, task_id: str) -> dict:
    task = owned_task(session, principal, task_id)
    events = list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.task_id == task.id, AuditEvent.sandbox_id == principal.sandbox_id)
            .order_by(AuditEvent.seq)
        )
    )
    nodes = task_nodes(session, task_id=task.id, sandbox_id=principal.sandbox_id)
    decisions = list(
        session.scalars(
            select(PolicyDecisionRecord).where(
                PolicyDecisionRecord.task_id == task.id,
                PolicyDecisionRecord.sandbox_id == principal.sandbox_id,
            )
        )
    )
    snapshots = list(
        session.scalars(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.task_id == task.id,
                EvidenceSnapshot.sandbox_id == principal.sandbox_id,
            )
        )
    )
    invocations = list(
        session.scalars(
            select(ModelInvocation).where(
                ModelInvocation.task_id == task.id,
                ModelInvocation.sandbox_id == principal.sandbox_id,
            )
        )
    )
    return {
        "task_id": task.id,
        "plan_hash": task.plan_hash,
        "state": task.state,
        "result": task.result_json,
        "audit_chain_valid": verify_audit_chain(events),
        "nodes": [
            {"node_id": node.node_key, "tool_name": node.tool_name, "status": node.status}
            for node in nodes
        ],
        "policy": [
            {"node_id": decision.node_key, "decision": decision.decision, "reason_codes": decision.reason_codes_json}
            for decision in decisions
        ],
        "evidence_snapshots": [
            {"source_kind": item.source_kind, "source_version": item.source_version, "payload": item.payload_json}
            for item in snapshots
        ],
        "model_invocations": [
            {"provider": item.provider_label, "purpose": item.purpose, "outcome": item.outcome}
            for item in invocations
        ],
        "events": [
            {"seq": event.seq, "type": event.event_type, "payload": event.payload_json}
            for event in events
        ],
    }


def query_execution(
    session: Session, principal: Principal, task_id: str, idempotency_key: str,
) -> dict:
    if not idempotency_key or len(idempotency_key) > 120:
        raise DomainError("INVALID_IDEMPOTENCY_KEY", "幂等键无效", 400)
    # On PostgreSQL this waits for an in-flight confirmation transaction on the task row.
    task = owned_task(session, principal, task_id, lock=True)
    from .runtime_models import RuntimeOperation, RuntimeRecord
    managed = session.get(RuntimeRecord, task.id)
    task_ids = [task.id]
    if managed and managed.kind == "PARENT":
        task_ids.extend(row.child_task_id for row in session.scalars(select(RuntimeOperation).where(
            RuntimeOperation.parent_task_id == task.id, RuntimeOperation.idempotency_key == idempotency_key))
            if row.child_task_id)
    calls = list(session.scalars(select(ToolCall).where(
        ToolCall.task_id.in_(task_ids),
        ToolCall.sandbox_id == principal.sandbox_id,
        ToolCall.idempotency_key == idempotency_key,
    ).order_by(ToolCall.created_at, ToolCall.id)))
    return {
        "task_id": task.id,
        "task_state": task.state,
        "status": "RECORDED" if calls else "NOT_RECORDED",
        "retry_with_same_key_allowed": managed is None and not calls and task.state in {"AWAITING_CONFIRMATION", "PARTIAL"},
        "executions": [{
            "tool_name": call.tool_name,
            "status": call.status,
            "result": call.result_json,
            "created_at": call.created_at.isoformat(),
        } for call in calls],
    }


def health_ready(session: Session) -> bool:
    return session.scalar(select(text("1"))) == 1
