import type { JsonValue, TaskTrace, TraceNode } from "./types";
import { queryKinds } from "./taskReply.ts";
import { recordedSubscriptionPayment, recordedActiveSubscription, recordedSubscriptionAutoOff, recordedSubscriptionGuidance, recordedSubscriptionRestoration } from "./subscriptionCommercePresentation.ts";
import { recordedOwnAccountTransfer } from "./ownAccountTransferPresentation.ts";
import { aaPayReceipt, aaRequestPresentation } from "./aaPresentation.ts";

export type OutcomeKind =
  | "PROGRESS"
  | "PENDING"
  | "EXECUTING"
  | "UNKNOWN"
  | "FAILED"
  | "PARTIAL"
  | "CLOSED"
  | "SCHEDULED"
  | "PROPOSAL_SAVED"
  | "COLLECTION_CREATED"
  | "CONTACT_SAVED"
  | "QUERY_RESULT"
  | "COMMITTED"
  | "UNVERIFIED"
  | "GUIDANCE";
export interface ReceiptPresentation {
  kind: OutcomeKind;
  title: string;
  tone: "neutral" | "info" | "warning" | "danger" | "success";
  icon: "clock" | "info" | "close" | "check" | "family";
  description: string;
  recordedNodes: TraceNode[];
}
const reads = queryKinds;
const nonOutputKeys = new Set([
  "message",
  "error",
  "simulated",
  "synthetic",
  "preview",
  "args",
  "order_preview",
  "read_only",
  "plan_hash",
  "confirmation_id",
  "requires_confirmation",
  "requires_step_up",
  "sources",
]);
const hasValue = (data: Record<string, JsonValue>, key: string) =>
  typeof data[key] === "string" && Boolean(data[key]);
const hasNumber = (data: Record<string, JsonValue>, key: string) =>
  typeof data[key] === "number" && Number.isFinite(data[key]);

/** A success state alone or a planning preview is not a financial receipt. */
export function hasRecordedResult(node: TraceNode, trace?: TaskTrace): boolean {
  const result = node.result;
  if (node.status !== "SUCCEEDED" || !result || result.error) return false;
  if (
    node.action === "BILL_ANALYSIS" &&
    trace?.analysis &&
    Number.isInteger(trace.analysis.transaction_count)
  )
    return true;
  if (reads.has(node.action))
    return Object.keys(result).some((key) => !nonOutputKeys.has(key));
  if (node.action==="OWN_ACCOUNT_TRANSFER") return recordedOwnAccountTransfer(result);
  if (node.action === "CREDIT_REPAY") return result.schema_version === "credit-repayment-v1" && result.status === "POSTED" && hasValue(result,"repayment_id") && result.ledger_balanced === true;
  if (node.action === "CREDIT_INSTALLMENT") return result.schema_version === "credit-installment-v1" && result.status === "ACTIVE" && hasValue(result,"installment_id") && result.new_consumption_minor === 0;
  if (node.action === "CREDIT_SETTLE_INSTALLMENT") return result.schema_version === "credit-repayment-v1" && result.status === "SETTLED" && hasValue(result,"installment_id") && result.ledger_balanced === true;
  if (["CREDIT_APPLY","CREDIT_LIMIT_REQUEST"].includes(node.action)) return result.schema_version === "credit-application-v1" && result.status === "PENDING_REVIEW" && hasValue(result,"application_id") && result.credit_limit_changed === false;
  if (node.action === "CREDIT_ACTIVATE" || node.action.startsWith("CARD_") && result.schema_version === "credit-card-control-v1") return result.instrument_type === "CREDIT" && hasValue(result,"card_id") && hasValue(result,"status") && hasNumber(result,"version");
  if (node.action === "AA_PAY") return !!trace && aaPayReceipt(trace,node) !== null;
  if (node.action === "OCCASION_MEMBER_SET") return result.schema_version === "family-profile-v1" && result.family_profile_state_changed === true && result.bank_state_changed === false && result.account_access_granted === false;
  if (node.action === "OCCASION_SET") return result.schema_version === "family-occasion-v1" && result.occasion_state_changed === true && result.bank_state_changed === false;
  if (node.action === "LIFE_PLAN_AUTHORIZE") return result.schema_version === "life-plans-v1" && result.life_plan_state_changed === true && result.cash_debit_minor === 0 && Array.isArray(result.plans);
  if (node.action === "LIFE_PLAN_CANCEL") return result.schema_version === "life-plans-v1" && Array.isArray(result.plans) && result.plans.length === 1;
  if (node.action === "MERCHANT_PURCHASE") return result.schema_version === "virtual-merchant-purchase-v1" && Array.isArray(result.orders) && result.orders.length === 1 && !!result.payment && typeof result.payment === "object" && !Array.isArray(result.payment) && result.payment.status === "POSTED" && result.payment.ledger_balanced === true;
  if (["PREFERENCE_SET", "PREFERENCE_REMOVE"].includes(node.action))
    return result.schema_version === "customer-preference-change-v1" && result.bank_state_changed === false
      && result.status === (node.action === "PREFERENCE_SET" ? "SAVED" : "RETRACTED");
  if (node.action === "TRANSFER")
    return hasValue(result, "transfer_id") && hasNumber(result, "amount_minor");
  if (["WEALTH_BUY", "WEALTH_REDEEM"].includes(node.action))
    return hasValue(result, "order_id") && hasNumber(result, "amount_minor");
  if (node.action.startsWith("CARD_"))
    return (
      hasValue(result, "card_id") &&
      hasValue(result, "card_status") &&
      hasNumber(result, "card_version")
    );
  if (node.action === "SUBSCRIPTION_CANCEL")
    return hasValue(result, "subscription_id") && result.status === "CANCELLED" || trace?.outcome?.kind === "QUERY_RESULT" && recordedSubscriptionAutoOff(node.action,result);
  if (["SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"].includes(node.action))
    return recordedSubscriptionPayment(node.action, result) || recordedSubscriptionRestoration(node.action,result) || trace?.outcome?.kind === "QUERY_RESULT" && recordedActiveSubscription(node.action, result);
  if (["FAMILY_GRANT", "FAMILY_REVOKE"].includes(node.action))
    return (
      hasValue(result, "delegation_id") &&
      ["ACTIVE", "REVOKED"].includes(String(result.status))
    );
  if (node.action === "FAMILY_PROPOSE")
    return (
      hasValue(result, "proposal_id") && result.bank_action_executed === false
    );
  if (node.action.startsWith("GOAL_"))
    return hasValue(result, "goal_id") && hasNumber(result, "reserved_minor");
  if (node.action === "CARBON_CLAIM")
    return (
      hasValue(result, "behavior_id") &&
      hasNumber(result, "credited_points") &&
      hasNumber(result, "wallet_points")
    );
  if (node.action === "CARBON_REDEEM")
    return (
      hasValue(result, "redemption_id") && result.status === "ISSUED_SIMULATION"
    );
  if (node.action === "AA_REQUEST")
    return hasValue(result, "aa_request_id") && result.status === "COLLECTING";
  if (node.action === "CONTACT_BIND")
    return Boolean(
      result.contact_alias &&
        typeof result.contact_alias === "object" &&
        !Array.isArray(result.contact_alias) &&
        (result.contact_alias as Record<string, JsonValue>).personal_only ===
          true,
    );
  return false;
}

/** Status-first presentation: retained earlier results never turn a current failure green. */
export function receiptPresentation(trace: TaskTrace): ReceiptPresentation {
  const nodes = trace.nodes || [];
  const completedIds = new Set(trace.outcome?.completed_operation_ids || []);
  const recordedNodes = nodes.filter(
    (node) =>
      hasRecordedResult(node, trace) ||
      (!["SUBSCRIPTION_START", "SUBSCRIPTION_RENEW", "AA_PAY"].includes(node.action) && trace.outcome?.verified === true &&
        completedIds.has(node.operation_id || "") &&
        node.status === "SUCCEEDED" &&
        node.result &&
        Object.keys(node.result).length),
  );
  const view = (
    kind: OutcomeKind,
    title: string,
    tone: ReceiptPresentation["tone"],
    icon: ReceiptPresentation["icon"],
    description: string,
  ): ReceiptPresentation => ({
    kind,
    title,
    tone,
    icon,
    description,
    recordedNodes,
  });
  if (["QUEUED", "UNDERSTANDING", "PLANNING"].includes(trace.state))
    return view(
      "PROGRESS",
      "处理进度",
      "info",
      "clock",
      "正在处理你的需求，尚未办理。",
    );
  if (
    [
      "NEED_CLARIFICATION",
      "AWAITING_CONFIRMATION",
      "WAITING_CONFIRMATION",
      "PLANNED",
      "POLICY_CHECKED",
    ].includes(trace.state)
  )
    return view(
      "PENDING",
      trace.state === "NEED_CLARIFICATION" ? "等待补充信息" : "待确认信息",
      "neutral",
      "info",
      "请核对办理信息；确认前不会执行新的操作。",
    );
  if (["AUTHORIZED", "EXECUTING", "COMPENSATING"].includes(trace.state))
    return view(
      "EXECUTING",
      trace.state === "AUTHORIZED" ? "授权已受理，等待执行结果" : "执行进行中",
      "info",
      "clock",
      "正在办理，请稍候查看结果。",
    );
  if (trace.state === "UNKNOWN")
    return view(
      "UNKNOWN",
      "办理结果待确认",
      "warning",
      "clock",
      "结果尚未确定，请等待查询，不要重复提交。",
    );
  if (trace.state === "FAILED")
    return view(
      "FAILED",
      "本次任务未完成",
      "danger",
      "close",
      "请查看未完成事项；之前已完成的操作不会自动撤销。",
    );
  if (trace.state === "PARTIAL")
    return view(
      "PARTIAL",
      "部分步骤已有结果",
      "warning",
      "info",
      "部分事项已完成，其余尚未完成。",
    );
  if (["CLOSED", "CANCELLED", "CANCELED"].includes(trace.state))
    return view(
      "CLOSED",
      "任务已关闭",
      "neutral",
      "info",
      "已停止后续办理；之前已完成的操作不会撤回。",
    );
  if (["WAITING", "WAITING_TIME"].includes(trace.state))
    return view(
      "SCHEDULED",
      "预约已登记，等待执行",
      "neutral",
      "clock",
      "预约已登记，尚未转账。",
    );
  if (trace.state !== "SUCCEEDED")
    return view(
      "UNVERIFIED",
      "任务状态信息",
      "neutral",
      "info",
      "结果尚未核实，请不要重复提交；核实后才能继续办理。",
    );
  const successful = nodes.filter((node) => node.status === "SUCCEEDED");
  if (successful.some(node => node.action === "AA_PAY" && !hasRecordedResult(node,trace)))
    return view("UNVERIFIED", "处理结果待核对", "warning", "info", "付款回执还需要核验，请先查询原付款，不要重复支付。");
  if (nodes.length === 1 && successful.length === 1 && trace.outcome?.state === trace.state && trace.outcome.kind === "QUERY_RESULT"
    && trace.outcome.verified === true && !trace.pending_confirmation && Array.isArray(trace.outcome.completed_operation_ids)
    && trace.outcome.completed_operation_ids.length === 0 && recordedSubscriptionGuidance(nodes[0].action,nodes[0].result || {}))
    return view("GUIDANCE", "服务说明", "neutral", "info", "该服务暂未接入开通或续期办理，本次没有扣款。" );
  if (successful.some(node => ["SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"].includes(node.action) && !hasRecordedResult(node,trace)))
    return view("UNVERIFIED", "处理结果待核对", "warning", "info", "尚未取得已确认的付款与订阅结果，请不要重复提交。");
  if (
    trace.outcome &&
    (trace.outcome.state !== trace.state ||
      !trace.outcome.verified ||
      !["QUERY_RESULT", "BUSINESS_RECEIPT", "PROPOSAL_SAVED"].includes(
        trace.outcome.kind,
      ))
  )
    return view(
      "UNVERIFIED",
      "处理结果待核对",
      "warning",
      "info",
      "尚未取得已确认的办理结果，请不要重复提交。",
    );
  if (trace.outcome?.kind === "PROPOSAL_SAVED")
    return view(
      "PROPOSAL_SAVED",
      "协助提案已保存",
      "info",
      "family",
      "建议已保存，等待本人审阅确认，尚未执行银行操作。",
    );
  if (
    successful.length &&
    successful.every((node) => node.action === "CONTACT_BIND") &&
    successful.every((node) => hasRecordedResult(node, trace))
  )
    return view(
      "CONTACT_SAVED",
      "个人身份已保存，未转账",
      "info",
      "family",
      "已保存个人联系人，未转账。",
    );
  if (trace.outcome?.kind === "QUERY_RESULT")
    return view(
      "QUERY_RESULT",
      "查询与分析结果",
      "success",
      "check",
      "查询已完成，未办理付款等操作。",
    );
  if (
    !successful.length ||
    nodes.some(
      (node) => !["SUCCEEDED", "SKIPPED", "CANCELLED"].includes(node.status),
    ) ||
    (!trace.outcome &&
      successful.some((node) => !hasRecordedResult(node, trace)))
  )
    return view(
      "UNVERIFIED",
      "处理结果待核对",
      "warning",
      "info",
      "尚未取得完整办理结果，请不要重复提交。",
    );
  if (successful.every((node) => node.action === "FAMILY_PROPOSE"))
    return view(
      "PROPOSAL_SAVED",
      "协助提案已保存",
      "info",
      "family",
      "建议已保存，等待本人审阅确认，尚未执行银行操作。",
    );
  if (successful.every((node) => node.action === "AA_REQUEST"))
    return view(
      "COLLECTION_CREATED",
      "收款请求已登记",
      "info",
      "info",
      "收款请求已创建，尚不代表付款或到账。",
    );
  if (successful.every((node) => reads.has(node.action)))
    return view(
      "QUERY_RESULT",
      "查询与分析结果",
      "success",
      "check",
      "查询已完成，未办理付款等操作。",
    );
  if (successful.every((node) => node.action.startsWith("CARBON_")))
    return view(
      "COMMITTED",
      "低碳积分与权益结果",
      "success",
      "check",
      "这是低碳活动的积分或权益记录；减排量为估算值，不是核证碳资产，也不代表现金到账。",
    );
  return view(
    "COMMITTED",
    "办理结果",
    "success",
    "check",
    "本次已完成的办理结果如下。",
  );
}

/** Customer guidance only; never changes allowed actions or sends a request. */
export function receiptNextStep(trace: TaskTrace, presentation = receiptPresentation(trace)): string {
  if (presentation.kind === "UNKNOWN") return "请等待结果查询，勿重复提交。";
  if (trace.state === "NEED_CLARIFICATION") return "请补充所需信息，再核对办理内容。";
  if (presentation.kind === "PENDING" && trace.pending_confirmation)
    return trace.pending_confirmation.requires_step_up
      ? "请核对办理信息，完成验证后确认。"
      : "请核对办理信息后确认。";
  if (["PROGRESS", "EXECUTING"].includes(presentation.kind)) return "正在处理，请稍候；无需重复提交。";
  if (presentation.kind === "UNVERIFIED") return "请等待结果核实，不要重复提交。";
  if (presentation.kind === "SCHEDULED") return "等待约定时间；如不再需要，请查看是否可以取消。";
  if (presentation.kind === "PROPOSAL_SAVED") return "请由账户本人审阅，并确认需要办理的事项。";
  if (presentation.kind === "COLLECTION_CREATED") {
    const current = trace.nodes.length === 1 ? aaRequestPresentation(trace,trace.nodes[0]) : null;
    if (current) return "可以继续咨询，或提出新的需求。";
    return "等待参与者付款，实际到账以收付款记录为准。";
  }
  if (["FAILED", "PARTIAL"].includes(presentation.kind)) return "请查看未完成事项；已完成的操作不会重复办理。";
  return "可以继续咨询，或提出新的需求。";
}
