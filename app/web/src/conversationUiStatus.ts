import type { CustomerLocale } from "./customerLanguage";
import type { ConversationTurn } from "./conversationTypes";

const uiText = (locale: CustomerLocale, zh: string, en: string) => locale === "en" ? en : zh;

/** Current UI feedback follows the selected page locale, not a provisional reply language. */
export function conversationWaitingText(status: string, locale: CustomerLocale, waitingForEarlierReply = false): string {
  if (status === "QUEUED" && waitingForEarlierReply) return uiText(locale,
    "当前记录显示：这条消息在等待前面的回复。", "The current record shows this message is waiting for an earlier reply.");
  return status === "QUEUED"
    ? uiText(locale, "已收到，正在等待处理。", "Received. Waiting for processing.")
    : uiText(locale, "正在处理你的需求…", "Processing your request…");
}

export function conversationQueueJumpText(locale: CustomerLocale): string {
  return uiText(locale, "查看前面的待处理消息", "View the earlier pending message");
}

export function conversationLinkedTaskProgress(
  turn: Pick<ConversationTurn, "mode" | "task_link"> | undefined,
  dataReady: boolean,
  readError: boolean,
  locale: CustomerLocale,
): string | null {
  if (!turn?.task_link || !["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")) return null;
  const state = turn.task_link.state;
  if (state === "UNKNOWN") return uiText(locale, "办理结果尚未确定，请勿重复提交。", "The result is not yet verified. Do not submit again.");
  if (state === "FAILED") return uiText(locale, "这项办理暂未完成，请查看具体说明和下一步。", "This request did not complete. Review its details and next steps.");
  if (state === "PARTIAL") return uiText(locale, "部分事项已有结果，其余尚未完成，请逐项查看。", "Some actions have results; others are unfinished. Review each action.");
  if (["AWAITING_CONFIRMATION", "WAITING_CONFIRMATION"].includes(state)) return uiText(locale, "办理信息待核对，请在确认卡中审阅并确认。", "Review the request details in the confirmation card before confirming.");
  if (state === "NEED_CLARIFICATION") return uiText(locale, "办理还需要补充信息，请查看具体说明。", "More details are needed. Review the request information.");
  if (["CLOSED", "CANCELLED", "CANCELED"].includes(state)) return uiText(locale, "请求已关闭，已完成的事项会保留。", "The request is closed. Completed actions are retained.");
  if (state === "AUTHORIZED") return uiText(locale, "授权已受理，请等待实际执行结果。", "Authorization received. Wait for the actual execution result.");
  if (["EXECUTING", "COMPENSATING"].includes(state)) return uiText(locale, "正在办理，请稍候查看实际结果。", "Processing the request. Check the actual result shortly.");
  return dataReady || readError ? null : uiText(locale, "需求已受理，正在读取对应办理信息。", "Request accepted. Loading its current details.");
}

export interface ConversationLiveStatus {
  permissionBlocked?: boolean;
  recheckingAccess?: boolean;
  pending?: boolean;
  sending?: boolean;
  /** Already localized UI-owned reveal copy, never turn.answer. */
  revealText?: string | null;
  turn?: Pick<ConversationTurn, "status" | "mode">;
  traceState?: string;
  /** Already localized UI state label, never a server free-form result. */
  traceStateLabel?: string;
  ongoingQueryRead?: boolean;
  authoritativeState?: string;
  linkedProgress?: string | null;
  resultStatus?: string;
  /** A successful read is not yet a readable narrated result. */
  queryOnly?: boolean;
  customerReplyStatus?: string | null;
  controlHint?: string | null;
  waitingForEarlierReply?: boolean;
}

/** Fixed presentation only: never includes a user's message, answer, identifier or amount. */
export function conversationLiveStatus(options: ConversationLiveStatus, locale: CustomerLocale): string {
  if (options.permissionBlocked) return options.recheckingAccess
    ? uiText(locale, "正在重新核验查询资料的访问权限。", "Rechecking access to the query information.")
    : uiText(locale, "查询资料的访问权限需要重新核验，结果内容已隐藏。", "Access needs to be checked again. Query results are hidden.");
  if (options.pending) return options.sending
    ? uiText(locale, "正在发送消息。", "Sending your message.")
    : uiText(locale, "这条消息的发送结果尚未确定，请先恢复这次发送。", "Message delivery is uncertain. Recover this send before trying again.");
  const currentState = options.traceState || options.authoritativeState;
  if (currentState === "UNKNOWN") return uiText(locale, "最新办理结果尚未确定，请勿重复提交。", "The latest result is not yet verified. Do not submit again.");
  if (options.revealText) return options.revealText;
  if (!options.turn) return "";
  if (options.turn.status === "QUEUED") return options.waitingForEarlierReply
    ? conversationWaitingText("QUEUED", locale, true)
    : uiText(locale, "已收到最新消息，正在等待处理。", "Latest message received. Waiting for processing.");
  if (options.turn.status === "LEASED") return uiText(locale, "正在处理最新需求。", "Processing your latest request.");
  if (options.turn.status === "FAILED") return uiText(locale, "这条消息暂未处理完成，请阅读回复中的说明。", "This message did not complete. Read the explanation in the reply.");
  if (options.queryOnly && currentState === "SUCCEEDED" && ["NEW_TASK", "REVISE", "CLARIFY"].includes(options.turn.mode || "")) {
    if (["ERROR", "HIDDEN"].includes(options.resultStatus || "")) return uiText(locale, "最新查询结果目前不可读取，请查看恢复入口。", "The latest query result is unavailable. Review the recovery options.");
    if (["QUEUED", "PROCESSING"].includes(options.customerReplyStatus || "")) return uiText(locale, "资料已查到，正在整理回复…", "The information has been retrieved. Preparing your reply…");
    if (options.customerReplyStatus === "STALE") return uiText(locale, "查询资料已返回，结果说明暂未就绪。", "The information has been retrieved; its explanation is not ready yet.");
  }
  if (options.traceState) {
    if (options.traceState === "UNKNOWN") return uiText(locale, "最新办理结果尚未确定，请勿重复提交。", "The latest result is not yet verified. Do not submit again.");
    if (options.traceState === "SUCCEEDED") return uiText(locale, "最新需求的结果已返回，请查看结果卡。", "The latest result is available. Review its result card.");
    const label = options.traceStateLabel || uiText(locale, "状态更新中", "Updating");
    return uiText(locale, "最新办理状态：" + label + "。请查看结果卡。", "Latest request status: " + label + ". Review its result card.");
  }
  if (options.ongoingQueryRead) return uiText(locale, "正在读取资料…", "Loading information…");
  if (options.authoritativeState === "UNKNOWN") return uiText(locale, "最新办理结果尚未确定，请勿重复提交。", "The latest result is not yet verified. Do not submit again.");
  if (options.authoritativeState === "SUCCEEDED") return uiText(locale, "最新需求的结果已返回，请查看结果卡。", "The latest result is available. Review its result card.");
  if (options.linkedProgress) return options.linkedProgress;
  if (options.resultStatus === "LOADING") return uiText(locale, "正在读取最新查询结果。", "Loading the latest query result.");
  if (["ERROR", "HIDDEN"].includes(options.resultStatus || "")) return uiText(locale, "最新查询结果目前不可读取，请查看恢复入口。", "The latest query result is unavailable. Review the recovery options.");
  if (options.controlHint) return options.controlHint;
  return uiText(locale, "最新回复已返回，请阅读回复正文。", "The latest reply is available. Read the response.");
}
