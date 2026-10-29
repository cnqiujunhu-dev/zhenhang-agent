import { receiptPresentation } from "./receiptPresentation.ts";
import type { JsonValue, TaskTrace } from "./types";

export function outcomeExplanation(trace: TaskTrace) {
  const presentation = receiptPresentation(trace);
  const unfinished = (trace.nodes || [])
    .filter((node) => node.status !== "SUCCEEDED")
    .map((node) => ({
      label: node.label || node.action,
      status: node.status,
      reason: node.wait_reason || node.error || null,
    }));
  const evidence = (trace.nodes || [])
    .filter((node) => node.evidence || node.result?.sources)
    .map((node) => ({
      label: node.label || node.action,
      evidence: node.evidence || null,
      sources: node.result?.sources || null,
    }));
  let next = "可以继续咨询，或发起独立需求；新操作仍需重新核对授权。";
  if (trace.state === "UNKNOWN")
    next = "先等待查单或人工核验。不要重复确认或将未知结果当成已失败。";
  else if (trace.pending_confirmation)
    next = trace.pending_confirmation.requires_step_up
      ? "核对本次实际范围，完成本次验证码验证后再点击确认；聊天里的确认不会执行。"
      : "核对本次实际范围后在确认卡点击确认；文字消息不能替代本次授权。";
  else if (trace.state === "NEED_CLARIFICATION")
    next = "补充本次缺失信息；服务端核验上下文并生成计划，新计划需要重新确认。";
  else if (
    ["QUEUED", "UNDERSTANDING", "PLANNING", "AUTHORIZED", "EXECUTING"].includes(
      trace.state,
    )
  )
    next = "等待服务端处理与核验结果。显示处理进度不代表银行已经提交成功。";
  else if (trace.state === "WAITING")
    next = "等待约定业务时间；如不再需要，可按任务允许的操作撤销。";
  else if (presentation.kind === "PROPOSAL_SAVED")
    next = "由账户本人独立审阅提案并在本人任务中确认；协助者不能代为付款。";
  else if (presentation.kind === "PARTIAL")
    next =
      "保留已核验步骤的原回执，核对尚未完成的步骤；不把部分成功当作全部完成。";
  return {
    presentation,
    unfinished,
    evidence: evidence as unknown as JsonValue,
    next,
    bankExecuted: presentation.kind === "COMMITTED",
    readOnly: presentation.kind === "QUERY_RESULT",
    noBankExecution: [
      "PROPOSAL_SAVED",
      "CONTACT_SAVED",
      "COLLECTION_CREATED",
      "QUERY_RESULT",
    ].includes(presentation.kind),
  };
}
