import type { ConversationTurn } from "./conversationTypes";
import type { TaskTrace } from "./types";
import { isQueryOnlyTask } from "./taskReply.ts";

export const QUERY_REVEAL_TARGET_MS = 1400;
export const QUERY_REVEAL_MAX_MS = 1600;
export const QUERY_REVEAL_EXIT_MS = 160;

/** Ephemeral receipt from the current send, never reconstructed from history. */
export type QueryRevealRequest = {
  requestId: string;
  turnId: string;
  scopeKey: string;
  startedAtMs: number;
};
export type QueryRevealView = {
  phase: "none" | "reading" | "organizing";
  turnId: string;
  text: string;
  holdResult: boolean;
  exiting: boolean;
  nextWakeAtMs: number | null;
};
export type QueryRevealInput = {
  request?: QueryRevealRequest | null;
  scopeKey?: string;
  turn?: ConversationTurn;
  trace?: TaskTrace | null;
  nowMs: number;
  reducedMotion: boolean;
  cancelled?: boolean;
  blocked?: boolean;
  resultReadStatus?: string;
};
const idle = (): QueryRevealView => ({phase: "none", turnId: "", text: "", holdResult: false, exiting: false, nextWakeAtMs: null});
const unsafeStates = new Set(["FAILED", "PARTIAL", "UNKNOWN", "CLOSED", "CANCELLED", "CANCELED",
  "NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION", "AUTHORIZED", "COMPENSATING"]);

export function queryRevealRequestKey(request?: QueryRevealRequest | null): string {
  return request ? JSON.stringify([request.scopeKey, request.requestId, request.turnId, request.startedAtMs]) : "";
}

/** Presentation budget only: it cannot alter, retry, confirm or execute a task. */
export function queryReveal(input: QueryRevealInput): QueryRevealView {
  const {request, turn, trace, nowMs} = input;
  if (!request || !request.requestId || !request.turnId || !request.scopeKey
    || request.scopeKey !== input.scopeKey || turn?.turn_id !== request.turnId
    || !Number.isFinite(nowMs) || !Number.isFinite(request.startedAtMs) || nowMs < request.startedAtMs
    || input.cancelled || input.blocked || input.reducedMotion
    || ["ERROR", "HIDDEN"].includes(input.resultReadStatus || "")
    || turn.status === "FAILED" || turn.error_code === "HISTORY_ACCESS_RECHECK"
    || turn.mode && turn.mode !== "NEW_TASK"
    || !trace || trace.task_id !== turn.task_link?.task_id || trace.version < turn.task_link.version
    || !isQueryOnlyTask(trace) || unsafeStates.has(trace.state)
    || trace.nodes.some(node => node.result?.error || ["FAILED", "UNKNOWN", "CLOSED", "CANCELLED", "CANCELED"].includes(node.status))) return idle();
  const elapsed = nowMs - request.startedAtMs;
  if (elapsed >= QUERY_REVEAL_MAX_MS) return idle();
  if (trace.state === "SUCCEEDED") {
    // A succeeded label without a verified query outcome must remain visible
    // immediately as unverified; the presentation timer cannot hide a warning.
    if (trace.outcome?.verified !== true || trace.outcome.kind !== "QUERY_RESULT" || trace.outcome.state !== "SUCCEEDED"
      || trace.nodes.some(node => node.status !== "SUCCEEDED") || elapsed >= QUERY_REVEAL_TARGET_MS) return idle();
    const exitsAt = request.startedAtMs + QUERY_REVEAL_TARGET_MS - QUERY_REVEAL_EXIT_MS;
    return {phase: "organizing", turnId: turn.turn_id, text: "正在整理结果…", holdResult: true, exiting: nowMs >= exitsAt,
      nextWakeAtMs: nowMs < exitsAt ? exitsAt : request.startedAtMs + QUERY_REVEAL_TARGET_MS};
  }
  if (!["QUEUED", "UNDERSTANDING", "PLANNING", "PLANNED", "POLICY_CHECKED", "EXECUTING"].includes(trace.state)) return idle();
  return {phase: "reading", turnId: turn.turn_id, text: "正在读取资料…", holdResult: false, exiting: false,
    nextWakeAtMs: request.startedAtMs + QUERY_REVEAL_MAX_MS};
}
