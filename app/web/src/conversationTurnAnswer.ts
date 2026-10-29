import type { ConversationTurn } from "./conversationTypes";
import { getCustomerLocale, translateCustomerText, type CustomerLocale } from "./customerLanguage";
import { stateLabels } from "./components";
import { customerNarrative } from "./customerPresentation";
import { visibleConsultationAnswer } from "./consultationBoundary";
import { readSourceUnavailable } from "./conversationReadAvailability.ts";

/** Deduplicate a generic question only when the more specific server question
 * asks for that same information. Never merge slots or invent a recipient.
 */
export function clarificationReplyText(intro?: string | null, detail?: string | null): string {
  const opening = intro?.trim() || "", question = detail?.trim() || "";
  if (!opening || !question || opening === question) return question || opening;
  const completeQuestion = /[?？]/.test(question);
  const genericOpening = /^(?:请(?:再)?补充(?:一下|一点|以下)?(?:必要)?信息|还需要(?:一点|一些)?信息|我还需要(?:一点|一些)?信息)[。！!]?$/;
  const amountOpening = /^(?:请问)?(?:这次|这笔|这笔钱)?(?:想|要|打算)?转(?:账|帐)?(?:多少钱|多少金额|金额是多少)[?？]$/;
  const amountQuestion = /多少钱|多少金额|金额(?:是|为)?多少|how much/i;
  const recipientOpening = /^(?:请问)?(?:这次|这笔|这笔钱)?(?:想|要|打算)?转(?:账|帐)?给谁[?？]$/;
  const recipientQuestion = /给谁|收款人(?:是|为)?谁|谁(?:来)?收款|who(?:m)? .*?(?:send|transfer)|(?:send|transfer).*?to whom/i;
  const cardOpening = /^(?:请问)?(?:这次|这笔|这笔钱)?(?:想|要|打算)?(?:用|从)哪张(?:银行)?卡(?:转出|转账|转帐)?[?？]$/;
  const cardQuestion = /哪张(?:银行)?卡|哪个(?:银行)?账户|which .*?(?:card|account)/i;
  const englishAmountOpening = /^How much (?:would you like to|do you want to|should I) (?:transfer|send)\?$/i;
  const genericEnglishOpening = /^(?:I need a little more information|Please provide the missing information)[.!]?$/i;
  if (completeQuestion && (genericOpening.test(opening) || genericEnglishOpening.test(opening)
    || (amountOpening.test(opening) || englishAmountOpening.test(opening)) && amountQuestion.test(question)
    || recipientOpening.test(opening) && recipientQuestion.test(question)
    || cardOpening.test(opening) && cardQuestion.test(question))) return question;
  return `${opening}\n\n${question}`;
}

export function customerTurnAnswer(turn: ConversationTurn, locale: CustomerLocale = turn.reply_language || getCustomerLocale(), preserveSavedAnswer = false): string {
  if (turn.status === "ANSWERED" && !turn.error_code && readSourceUnavailable(turn)
    && (!turn.task_link || turn.task_link.state === "SUCCEEDED")) return locale === "en"
      ? "This earlier reply cannot be expanded right now. You can check its original request or ask again."
      : "这条历史回复暂时不能展开。你可以查看原单，或重新查询。";
  const statusSnapshot = turn.status_snapshot;
  const statusProjection = statusSnapshot?.projection;
  if (turn.mode === "STATUS" && turn.status === "ANSWERED" && !turn.error_code && turn.task_link
    && turn.status_snapshot_status === "RETAINED" && statusSnapshot?.schema_version === "conversation-status-snapshot-v1"
    && statusSnapshot.authority === "HISTORICAL_STATUS_NOT_CURRENT_AUTHORIZATION"
    && statusSnapshot.conversation_id === turn.conversation_id && statusSnapshot.turn_id === turn.turn_id
    && statusSnapshot.task_id === turn.task_link.task_id && Number.isSafeInteger(statusSnapshot.task_version)
    && statusSnapshot.task_version > 0 && statusSnapshot.task_version <= turn.task_link.version
    && statusSnapshot.reply_language === (turn.reply_language || locale)
    && typeof statusSnapshot.text === "string" && statusSnapshot.text.trim() && statusSnapshot.text.length <= 5000
    && statusSnapshot.text === turn.answer && /^[0-9a-f]{64}$/.test(statusSnapshot.fingerprint)
    && Number.isFinite(Date.parse(statusSnapshot.observed_at)) && /(?:Z|[+-]\d\d:\d\d)$/.test(statusSnapshot.observed_at)
    && statusProjection?.task_id === statusSnapshot.task_id && statusProjection.version === statusSnapshot.task_version
    && statusProjection.state === statusSnapshot.state && typeof statusProjection.verified === "boolean"
    && ["NONE", "PENDING", "EXPIRED", "UNAVAILABLE"].includes(statusProjection.confirmation_status)
    && [null, "PROCESS", "UNKNOWN", "FAILURE", "PARTIAL_RESULT", "QUERY_RESULT", "BUSINESS_RECEIPT", "PROPOSAL_SAVED"].includes(statusProjection.outcome_kind)
    && (!statusProjection.verified || statusSnapshot.state === "SUCCEEDED" && ["QUERY_RESULT", "BUSINESS_RECEIPT", "PROPOSAL_SAVED"].includes(statusProjection.outcome_kind || ""))) {
    const answer = customerNarrative(statusSnapshot.text);
    return turn.task_link.state === "UNKNOWN" && statusSnapshot.state !== "UNKNOWN" ? answer + (locale === "en"
      ? "\n\nThe current result is still being checked. Do not repeat the request."
      : "\n\n当前办理结果还在核查，请不要重复提交。") : answer;
  }
  // NEW_TASK/REVISE.answer is an acceptance placeholder, not the question
  // eventually shown. Only an owned server-persisted snapshot can retain it.
  const snapshot=turn.reply_snapshot;
  if (preserveSavedAnswer && turn.status === "ANSWERED" && !turn.error_code
    && ["AVAILABLE","RETAINED"].includes(turn.reply_snapshot_status || "") && snapshot?.schema_version === "conversation-reply-snapshot-v1" && snapshot.authority === "HISTORICAL_NARRATION_NOT_CURRENT_STATUS_OR_AUTHORIZATION"
    && snapshot.conversation_id === turn.conversation_id && snapshot.turn_id === turn.turn_id && snapshot.task_id === turn.task_link?.task_id
    && Number.isSafeInteger(snapshot.task_version) && snapshot.task_version > 0 && snapshot.task_version <= turn.task_link.version
    && typeof snapshot.text === "string" && snapshot.text.trim() && snapshot.text.length <= 10000
    && typeof snapshot.intro === "string" && snapshot.intro.length > 0 && snapshot.intro.length <= 5000
    && (snapshot.detail === null || typeof snapshot.detail === "string" && snapshot.detail.length <= 5000)
    && /^[0-9a-f]{64}$/.test(snapshot.fingerprint) && Number.isFinite(Date.parse(snapshot.observed_at))
    && snapshot.text === snapshot.intro + (snapshot.detail ? "\n\n" + snapshot.detail : "")
    && ["NEED_CLARIFICATION","AWAITING_CONFIRMATION","SUCCEEDED","PARTIAL","FAILED","UNKNOWN","WAITING"].includes(snapshot.state)
    && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")) return customerNarrative(snapshot.state === "NEED_CLARIFICATION"
      ? clarificationReplyText(snapshot.intro, snapshot.detail) : snapshot.text);
  if (preserveSavedAnswer && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")) {
    if (turn.task_link?.state === "UNKNOWN") return locale === "en"
      ? "The result of this earlier request was unverified. Do not repeat it; check the original request."
      : "这项较早办理的结果尚未核实，请勿重复提交，先核查原办理结果。";
    if (["FAILED","PARTIAL"].includes(turn.task_link?.state || "")) return locale === "en"
      ? "This earlier request did not fully complete. Check its details before any further action."
      : "这项较早办理未全部完成，请先查看说明与下一步。";
    return locale === "en" ? "This earlier reply is unavailable right now." : "这条较早回复暂不可查看。";
  }
  const projection = turn.response_meta?.status_projection;
  if (turn.mode === "STATUS" && turn.status === "ANSWERED" && !turn.error_code && turn.answer && turn.task_link
    && projection && typeof projection === "object" && !Array.isArray(projection)
    && projection.task_id === turn.task_link.task_id && projection.version === turn.task_link.version
    && projection.state === turn.task_link.state && typeof projection.verified === "boolean") return customerNarrative(turn.answer);
  if (locale === "en") {
    if (turn.error_code === "HISTORY_ACCESS_RECHECK") return "Accessible content will appear after verification. Previous explanations are hidden for now.";
    if (turn.status === "FAILED") return turn.task_link?.state === "UNKNOWN"
      ? "The result is still being checked. Do not repeat the request."
      : "I couldn't finish this request. You can ask me to try again.";
    if (turn.mode === "STATUS" && turn.task_link) return turn.task_link.state === "UNKNOWN" ? "The result has not been verified. Do not repeat the request; check the original status." : `Request status: ${translateCustomerText(stateLabels[turn.task_link.state] || "状态更新中", "en")}.`;
    if (["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "") && ["UNKNOWN", "FAILED", "CLOSED", "SUCCEEDED"].includes(turn.task_link?.state || "")) return turn.task_link?.state === "UNKNOWN" ? "The result is still being checked. Do not repeat the request." : turn.task_link?.state === "FAILED" ? "This request did not complete. Check its details and next steps." : turn.task_link?.state === "CLOSED" ? "This request is closed. Completed actions are retained." : "A result is available in the linked request.";
    if (turn.mode === "NEW_TASK") return "I will check the relevant information. Anything requiring your confirmation will be shown separately.";
    if (["REVISE", "CLARIFY"].includes(turn.mode || "")) return "Your additional details were received. Review the updated information before confirming.";
    if (turn.mode === "CONFIRM_HINT") return "Review and confirm in the action card. Typing “confirm” does not execute an action.";
    if (turn.mode === "CANCEL_HINT") return "Use the request's cancellation control. A message does not cancel business actions or undo completed ones.";
  }
  if (turn.error_code === "HISTORY_ACCESS_RECHECK") return "核验完成后会显示仍可访问的内容，暂不展示此前的解释。";
  if (turn.status === "FAILED")
    return turn.task_link?.state === "UNKNOWN" ? "这项办理的结果还在核查，请不要重复提交。"
      : "这条消息暂未处理完成，你可以告诉我再试一次。";
  if (turn.mode === "STATUS" && turn.task_link)
    return `办理状态：${stateLabels[turn.task_link.state] || "状态更新中"}。${turn.task_link.state === "UNKNOWN" ? "结果尚未确定，请勿重复提交。" : ""}`;
  if (["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")) {
    if (turn.task_link?.state === "SUCCEEDED") return "这项需求已有结果，可以查看对应办理记录。";
    if (turn.task_link?.state === "UNKNOWN") return "这项办理的结果还在核查，请不要重复提交。";
    if (turn.task_link?.state === "FAILED") return "这项办理暂未完成，可以查看具体说明和下一步。";
    if (turn.task_link?.state === "CLOSED") return "这项请求已关闭，已完成的事项会保留。";
    if (turn.mode === "NEW_TASK") return "好的，我来核对一下相关资料。需要你确认的事项会单独提示。";
  }
  if (["REVISE", "CLARIFY"].includes(turn.mode || ""))
    return "已收到补充，正在更新办理信息。请重新核对并确认。";
  if (turn.mode === "CONFIRM_HINT")
    return "请在办理卡中核对并确认；文字“确认”不会执行操作。";
  if (turn.mode === "CANCEL_HINT")
    return "请使用这项办理的取消按钮；文字撤销不会取消业务，已完成的操作不会自动撤回。";
  return customerNarrative((turn.mode === "CONSULT" ? visibleConsultationAnswer(turn.answer) : turn.answer) || "")
    .replace(/【([^【】\n]+)｜[A-Z][A-Z_]+】/g, "【$1】");
}


