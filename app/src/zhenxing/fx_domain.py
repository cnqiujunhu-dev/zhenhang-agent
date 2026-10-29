"""Typed FX actions within the existing controlled Agent workflow."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from sqlalchemy import select

from .audit import append_audit
from .contracts import DomainError
from .fx_banking import create_fx_quote, lock_quote_resources, post_fx_operation, quote_public
from .fx_models import FxQuote
from .fx_rates import public_rate
from .internal_banking import resolve_transfer_payees
from .models import Confirmation, EvidenceSnapshot, OutboxMessage, TaskNode, ToolCall, utcnow
from .planning import canonical_hash
from .platform import add_task_nodes, record_policy
from .workflow import finish_node, transition_task


FX_TOOLS = {"FX_QUOTE": "fx.quote.v1", "FX_TRANSFER": "fx.transfer.v1", "FX_EXCHANGE": "fx.exchange.v1"}
FX_INTENTS = {"FX_QUOTE": "FOREX_QUERY", "FX_TRANSFER": "FOREX_TRANSFER", "FX_EXCHANGE": "FOREIGN_EXCHANGE"}
FX_TOOL_DEFINITIONS = {tool: {"read_only": kind == "FX_QUOTE", "risk_level": "L1" if kind == "FX_QUOTE" else "L3", "owner": "banking.fx"}
                       for kind, tool in FX_TOOLS.items()}


def _message(quote):
    data = quote_public(quote)
    recipient = "本人" if quote.kind == "EXCHANGE" else data["recipient_username"]
    if quote.scope_json.get("declared_recipient_context"):
        recipient += "的收款卡" + data["recipient_card_number"]
    reference = data.get("source_card_number") or ("旧兼容尾号" + data["source_card_last4"])
    return (f"拟从付款卡{reference}的{data['source_currency']}资金来源扣除{data['debit_minor']/100:.2f} {data['source_currency']}，"
            f"向{recipient}到账{data['credit_minor']/100:.2f} {data['target_currency']}。"
            f"USD/CNY参考率{data['rate']}，来源{data['provider']}（{data['market_date']}）；"
            f"手续费为0，采用本平台费用政策，不代表银行实际成交价格。{data['settlement_notice']}；120秒内有效，须本人确认。")


def prepare_fx_task(session, task, action, context=None):
    context = context or {}
    kind, slots = action.get("kind"), action.get("slots") or {}
    tool = FX_TOOLS.get(kind)
    if tool is None:
        raise DomainError("FX_ACTION_UNKNOWN", "不支持此外汇动作", 422)
    if task.intent_type != FX_INTENTS[kind]:
        raise DomainError("FX_INTENT_MISMATCH", "报价与资金操作的任务权限类型不一致", 403)
    node_key = kind.lower()
    try:
        market = context.get("fx_market_rate")
        if not market:
            raise DomainError("FX_RATE_UNAVAILABLE", "未获得事务外服务器参考汇率，未使用模型估计", 503)
        if kind == "FX_QUOTE":
            data = public_rate(market)
            result = {"market_rate": data, "message": f"USD/CNY日频参考率为{data['rate']}（{data['market_date']}）。不是银行成交价格，未执行资金操作。", "read_only": True, "synthetic": True}
            amount, currency = slots.get("amount_minor"), slots.get("currency")
            if amount is not None:
                if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or currency not in {"USD", "CNY"}:
                    raise DomainError("FX_AMOUNT_REQUIRED", "参考换算请明确金额与币种", 422)
                source_currency, target_currency = slots.get("source_currency"), slots.get("target_currency")
                explicit = source_currency is not None or target_currency is not None
                if explicit and (source_currency not in {"USD", "CNY"} or target_currency not in {"USD", "CNY"}
                                 or source_currency == target_currency or currency not in {source_currency, target_currency}):
                    raise DomainError("FX_EXCHANGE_DIRECTION_REQUIRED", "参考换算请明确CNY/USD不同币种方向", 422)
                amount_role = "TARGET" if explicit and currency == target_currency else "SOURCE"
                output_currency = source_currency if amount_role == "TARGET" else (target_currency if explicit else "CNY" if currency == "USD" else "USD")
                value = Decimal(amount)*Decimal(data["rate"]) if currency == "USD" else Decimal(amount)/Decimal(data["rate"])
                rounding = ROUND_CEILING if amount_role == "TARGET" else ROUND_FLOOR
                result["conversion"] = {"input_currency": currency, "input_amount_minor": amount, "output_currency": output_currency,
                    "output_amount_minor": int(value.to_integral_value(rounding=rounding)), "amount_role": amount_role,
                    "rounding": "REFERENCE_SOURCE_CEIL" if amount_role == "TARGET" else "REFERENCE_TARGET_FLOOR", "executable": False,
                    "estimate_kind": "TARGET_COST_ESTIMATE" if amount_role == "TARGET" else "SOURCE_AMOUNT_ESTIMATE" if explicit else "REFERENCE_ONLY",
                    "source_currency": source_currency if explicit else currency, "target_currency": target_currency if explicit else output_currency}
            args, quote = {}, None
        else:
            if kind != "FX_TRANSFER" and (slots.get("recipient_alias") or slots.get("replace_alias")):
                raise DomainError("CONTACT_ACTION_INVALID", "本人换汇不包含收款人身份绑定", 403)
            quote = create_fx_quote(session, task.sandbox_id, task.user_id, "TRANSFER" if kind == "FX_TRANSFER" else "EXCHANGE", slots, market)
            from .contacts import prepare_alias_proposal
            alias_binding = prepare_alias_proposal(session, task, action, context)
            if alias_binding:
                quote.scope_json = {**quote.scope_json, "alias_binding": alias_binding}
                quote.scope_hash = canonical_hash(quote.scope_json)
            payee_id = None
            if kind == "FX_TRANSFER":
                payee = resolve_transfer_payees(session, task.sandbox_id, task.user_id, slots["payee_name"])[0]
                from .extensions import evaluate_transfer_risk
                risk = evaluate_transfer_risk(session, task=task, context={**context, "payee_id": payee.id, "message": context.get("message", task.message)})
                if risk["decision"] != "ALLOW":
                    raise DomainError("FX_TRANSFER_RISK_BLOCKED", "完整原请求的转账风险未通过，确认不能跳过", 403)
                payee_id = payee.id
                quote.scope_json = {**quote.scope_json, "payee_id": payee.id}
                quote.scope_hash = canonical_hash(quote.scope_json)
            args = {"quote_id": quote.id, "quote_scope_hash": quote.scope_hash, "action_kind": kind, "payee_id": payee_id}
            result = {"message": _message(quote), "fx_quote": quote_public(quote), "synthetic": True}
    except DomainError as error:
        task.result_json = {"message": error.message, "error": error.code, "synthetic": True}
        if error.status_code != 422:
            transition_task(task, "FAILED")
        return task
    node = {"node_id": node_key, "tool_name": tool, "read_only": kind == "FX_QUOTE", "depends_on": [],
            "requires_confirmation": kind != "FX_QUOTE", "status": "SUCCEEDED" if kind == "FX_QUOTE" else "WAITING_CONFIRMATION", **args}
    plan = {"task_id": task.id, "sandbox_id": task.sandbox_id, "plan_version": 1, "policy_version": "fx-policy-v1", "nodes": [node]}
    task.plan_json, task.plan_hash, task.result_json = plan, canonical_hash(plan), result
    task.fic_json = {**task.fic_json, "status": "READY", "requested_capabilities": [tool],
                     "slots": {key: {"value": value, "source": "USER_TEXT"} for key, value in slots.items() if value is not None}}
    if quote:
        task.fic_json["slots"].update({"quote_id": {"value": quote.id, "source": "SERVER_REFERENCE_AND_POLICY"},
                                      "fx_quote": {"value": quote_public(quote), "source": "SERVER_REFERENCE_AND_POLICY"}})
    add_task_nodes(session, task_id=task.id, sandbox_id=task.sandbox_id, plan=plan)
    record_policy(session, task_id=task.id, sandbox_id=task.sandbox_id, node_key=node_key,
                  decision="ALLOW" if kind == "FX_QUOTE" else "REQUIRE_CONFIRMATION",
                  reason_codes=["CURRENCY_SEPARATED_JOURNAL", "SERVER_REFERENCE_RATE", "OWNER_SOURCE_AND_EXACT_QUOTE"], policy_version="fx-policy-v1")
    session.add(EvidenceSnapshot(task_id=task.id, sandbox_id=task.sandbox_id, source_kind="FX_REFERENCE_AND_SCOPE",
        source_version="fx-policy-v1", payload_json=result))
    if kind == "FX_QUOTE":
        transition_task(task, "SUCCEEDED")
    else:
        transition_task(task, "AWAITING_CONFIRMATION")
        session.add(Confirmation(sandbox_id=task.sandbox_id, task_id=task.id, user_id=task.user_id, node_key=node_key,
            action_type=tool, target_id=quote.id, plan_hash=task.plan_hash,
            view_hash=canonical_hash({"plan_hash": task.plan_hash, "quote_scope_hash": quote.scope_hash}), expires_at=quote.expires_at))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="FX_REFERENCE_READ" if kind == "FX_QUOTE" else "FX_PLAN_PROPOSED",
                 payload={"kind": kind, "quote_id": quote.id if quote else None, "funds_changed": False})
    session.flush()
    return task


def dispatch_fx_task(session, task, principal, request, key):
    if (task.user_id, task.sandbox_id) != (principal.user_id, principal.sandbox_id):
        raise DomainError("TASK_NOT_FOUND", "外汇任务不属于此主体", 404)
    node = next((item for item in task.plan_json.get("nodes", []) if item.get("action_kind") in {"FX_TRANSFER", "FX_EXCHANGE"}), None)
    if node is None:
        raise DomainError("FX_PLAN_NODE_MISSING", "没有可确认外汇节点", 409)
    kind, tool = node["action_kind"], FX_TOOLS[node["action_kind"]]
    if task.intent_type != FX_INTENTS[kind]:
        raise DomainError("FX_INTENT_MISMATCH", "资金操作不属于此任务权限类型", 403)
    prior = session.scalar(select(ToolCall).where(ToolCall.sandbox_id == task.sandbox_id, ToolCall.tool_name == tool, ToolCall.idempotency_key == key))
    quote = session.get(FxQuote, node["quote_id"])
    if quote is None or (quote.user_id, quote.sandbox_id) != (task.user_id, task.sandbox_id) or quote.scope_hash != node["quote_scope_hash"] \
            or quote.kind != ("TRANSFER" if kind == "FX_TRANSFER" else "EXCHANGE") \
            or (kind == "FX_TRANSFER" and quote.scope_json.get("payee_id") != node.get("payee_id")):
        raise DomainError("FX_QUOTE_SCOPE_CHANGED", "报价对象或范围不属于当前确认", 409)
    if prior is None:
        from .contacts import validate_alias_proposal
        if quote.scope_json.get("alias_binding"):
            validate_alias_proposal(session, task, quote.scope_json["alias_binding"])
        from .fx_recipient_selection import validate_fx_recipient_contexts
        validate_fx_recipient_contexts(session, quote)
        if canonical_hash(task.plan_json) != task.plan_hash:
            raise DomainError("PLAN_INTEGRITY_FAILED", "计划完整性变化", 409)
        lock_quote_resources(session, quote)
        if kind == "FX_TRANSFER":
            from .extensions import evaluate_transfer_risk
            risk = evaluate_transfer_risk(session, task=task, context={"payee_id": node["payee_id"], "for_commit": True})
            if risk["decision"] != "ALLOW":
                raise DomainError("FX_TRANSFER_RISK_BLOCKED", "临执行完整请求风险未通过", 403)
    from .tools import ToolGateway
    request_hash, node_row, confirmation, duplicate = ToolGateway().authorize_write(session, task=task,
        sandbox_id=task.sandbox_id, user_id=task.user_id, request=request, idempotency_key=key, tool_name=tool, node_key=kind.lower())
    if duplicate:
        return task
    if confirmation.target_id != quote.id or confirmation.view_hash != canonical_hash({"plan_hash": task.plan_hash, "quote_scope_hash": quote.scope_hash}):
        raise DomainError("FX_CONFIRMATION_VIEW_CHANGED", "外汇确认不绑定实际扣款与到账方案", 409)
    op = post_fx_operation(session, quote, key, task.id)
    from .contacts import commit_alias_proposal
    saved_alias = commit_alias_proposal(session, task, quote.scope_json.get("alias_binding"))
    task.result_json = {"message": "外汇资金已按确认币种与金额入账，并完成分币种核对。", "fx_operation_id": op.id,
        "fx_kind": op.kind, "fx_quote": quote_public(quote), "source_currency": op.source_currency, "target_currency": op.target_currency,
        "debit_minor": op.debit_minor, "credit_minor": op.credit_minor, "synthetic": True, "fee_minor": 0, "fee_source": "SIM_POLICY"}
    if saved_alias:
        task.result_json["contact_alias"] = saved_alias
    confirmation.status, confirmation.consumed_at = "CONSUMED", utcnow()
    finish_node(session, task, node_row, task.result_json)
    session.add(ToolCall(sandbox_id=task.sandbox_id, task_id=task.id, tool_name=tool, idempotency_key=key,
                        request_hash=request_hash, status="SUCCESS", result_json=task.result_json))
    append_audit(session, sandbox_id=task.sandbox_id, task_id=task.id, event_type="FX_OPERATION_POSTED",
                 payload={"operation_id": op.id, "quote_id": quote.id, "source_currency": op.source_currency,
                          "target_currency": op.target_currency, "debit_minor": op.debit_minor, "credit_minor": op.credit_minor})
    session.add(OutboxMessage(sandbox_id=task.sandbox_id, task_id=task.id, event_type="TASK_COMPLETED", payload_json={"task_id": task.id, "operation_id": op.id}))
    session.flush()
    return task
