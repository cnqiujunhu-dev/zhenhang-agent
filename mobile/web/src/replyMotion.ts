export type ReplyMotionSnapshot = {
  turnId: string;
  status: "QUEUED" | "LEASED" | "ANSWERED" | "FAILED";
  taskId?: string | null;
  taskState?: string | null;
};
export type ReplyMotionOptions = {
  allowMotion: boolean;
  reducedMotion: boolean;
  latestTurnId: string;
  historical?: boolean;
};
const observedTaskStates = new Set([
  "QUEUED", "UNDERSTANDING", "PLANNING", "WAITING", "PLANNED", "POLICY_CHECKED",
  "AWAITING_CONFIRMATION", "NEED_CLARIFICATION", "AUTHORIZED", "EXECUTING",
  "COMPENSATING", "SUCCEEDED", "PARTIAL", "FAILED", "CLOSED", "UNKNOWN",
]);

/** Motion reflects a changed server state; it never supplies timing or progress. */
export function replyMotion(
  previous: ReplyMotionSnapshot | null,
  next: ReplyMotionSnapshot | null,
  options: ReplyMotionOptions,
): "none" | "settle" {
  if (!options.allowMotion || options.reducedMotion || options.historical || !previous || !next
    || !next.turnId || next.turnId !== options.latestTurnId || previous.turnId !== next.turnId) return "none";
  if (["QUEUED", "LEASED"].includes(previous.status)
    && ["ANSWERED", "FAILED"].includes(next.status)) return "settle";
  if (previous.status === "ANSWERED" && next.status === "ANSWERED"
    && previous.taskId && previous.taskId === next.taskId
    && previous.taskState && next.taskState && previous.taskState !== next.taskState
    && observedTaskStates.has(previous.taskState) && observedTaskStates.has(next.taskState)) return "settle";
  return "none";
}
