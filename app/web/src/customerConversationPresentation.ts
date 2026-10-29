import type { ConversationTurn } from "./conversationTypes";

type ReplyTurn = Pick<ConversationTurn, "status" | "mode" | "error_code" | "task_link" | "read_result_snapshot_status" | "reply_snapshot_status" | "status_snapshot_status">;
type QueryReadContext = {
  originalResultTurn: boolean;
  knownQuery: boolean;
  resultStatus?: string;
};

/** Read/recovery text can replace generic completion filler, never a warning. */
export function queryReadHasOwnReply(turn: ReplyTurn, context: QueryReadContext): boolean {
  if (turn.status !== "ANSWERED" || turn.error_code === "HISTORY_ACCESS_RECHECK"
    || [turn.read_result_snapshot_status, turn.reply_snapshot_status, turn.status_snapshot_status].includes("UNAVAILABLE")
    || turn.task_link?.state !== "SUCCEEDED" || !context.originalResultTurn
    || !["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")) return false;
  return ["LOADING", "ERROR", "HIDDEN"].includes(context.resultStatus || "");
}

export function showConversationMode(turn: ReplyTurn, hasInlineResult: boolean): boolean {
  // Task-routing modes describe the orchestration, not the customer's reply.
  // An access recheck is the only mode label that changes what can be read.
  return turn.error_code === "HISTORY_ACCESS_RECHECK";
}
