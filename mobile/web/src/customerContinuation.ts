import type { ConversationTurn } from "./conversationTypes";
import type { TaskTrace } from "./types";
import type { CustomerLocale } from "./customerLanguage";
import { customerActionLabel } from "./customerTaskPresentation.ts";
import { customerName } from "./customerPresentation.ts";

const closed = new Set(["SUCCEEDED", "FAILED", "CLOSED", "CANCELLED", "CANCELED"]);
export function customerContinuationHint(turns: ConversationTurn[], trace: TaskTrace | null, blocked = false, locale: CustomerLocale = "zh-CN"): string | null {
  if (blocked || !trace || !["NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION"].includes(trace.state)) return null;
  const linked = new Map<string, string>();
  for (const turn of turns) if (turn.task_link) linked.set(turn.task_link.task_id, turn.task_link.state);
  if (!linked.has(trace.task_id)) return null;
  linked.set(trace.task_id, trace.state);
  // UNKNOWN, active execution, and another unfinished request prevent implicit
  // continuation. This is a display hint, not a task-selection authority.
  const open = [...linked].filter(([, state]) => !closed.has(state));
  if (open.length !== 1 || open[0][0] !== trace.task_id) return null;
  const node = trace.nodes.find(item => !closed.has(item.status) && !["SKIPPED"].includes(item.status));
  const action = node?.action || "";
  const scope = trace.pending_confirmation?.scope || node?.result;
  const recipient = scope && typeof scope.payee_name === "string" ? customerName(scope.payee_name) : null;
  const title = ["TRANSFER", "FX_TRANSFER", "TRANSFER_SCHEDULE"].includes(action)
    ? recipient ? locale === "en" ? `transfer to ${recipient}` : `给${recipient}转账` : locale === "en" ? "the transfer" : "这笔转账"
    : locale === "en" ? ({ SUBSCRIPTION_CANCEL: "subscription cancellation", CARD_LOCK: "card protection", CARD_UNLOCK: "card unlock", FX_EXCHANGE: "currency exchange" } as Record<string, string>)[action] || "this request" : customerActionLabel(action);
  return locale === "en" ? `Adding details to: ${title}` : `补充：${title}`;
}

export function subscriptionDueLabel(nextDueAt: unknown, now: number, locale: CustomerLocale,
  context: { dueStatus?: unknown; status?: unknown } = {}): string {
  const due = typeof nextDueAt === "string" ? Date.parse(nextDueAt) : Number.NaN;
  if (!Number.isFinite(due)) return locale === "en" ? "Payment date not returned" : "扣款日期未返回";
  const needsReview = locale === "en" ? "Recorded payment date · needs review" : "记录扣款日（待核实）";
  const { dueStatus, status } = context;
  if (["CANCELLED", "CANCELED"].includes(String(status)) || dueStatus === "RECORDED_INACTIVE" && status !== "ACTIVE")
    return locale === "en" ? "Original recorded payment date" : "原记录扣款日";
  if (status !== undefined && status !== "ACTIVE" || !Number.isFinite(now) || dueStatus === "UNKNOWN") return needsReview;
  const past = due < now;
  if (dueStatus !== undefined && dueStatus !== (past ? "PAST_RECORDED_DATE" : "UPCOMING")) return needsReview;
  return past ? locale === "en" ? "Recorded due date has passed · latest plan needs review" : "记录扣款日已过，最新计划待核实"
    : locale === "en" ? "Recorded next payment" : "记录的下次扣款日";
}
