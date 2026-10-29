import type { TaskTrace } from "./types";

export const queryKinds = new Set([
  "BILL_ANALYSIS", "CARD_STATUS", "WEALTH_COMPARE", "SUBSCRIPTION_LIST", "FRAUD_ANALYZE",
  "FAMILY_VIEW", "GOAL_VIEW", "CARBON_VIEW", "FX_QUOTE",
  "ASSET_POSITIONS_READ", "MEMBERSHIP_VIEW", "BENEFITS_VIEW", "PREFERENCE_VIEW",
  "AA_VIEW", "TRANSFER_SCHEDULE_VIEW", "CREDIT_VIEW", "CREDIT_STATEMENT", "OCCASION_MEMBER_VIEW", "OCCASION_VIEW", "LIFE_PLAN_VIEW", "MERCHANT_CATALOG", "MERCHANT_ORDERS",
]);
export type ReadAvailabilityTurn = {
  turn_id: string; status?: string; mode?: string | null; error_code?: string | null;
  read_result_snapshot_status?: string; reply_snapshot_status?: string; status_snapshot_status?: string;
  task_link: { task_id: string; version?: number; relationship?: string; state?: string } | null;
};
export function readSourceUnavailable(turn: ReadAvailabilityTurn | null | undefined): boolean {
  return Boolean(turn && (turn.read_result_snapshot_status === "UNAVAILABLE"
    || turn.reply_snapshot_status === "UNAVAILABLE" || turn.status_snapshot_status === "UNAVAILABLE"));
}
/** Pick the real request before checking availability. Skipping a denied newer
 * request first would wrongly move its result to an older request/version. */
export function readTraceOwner(trace: Pick<TaskTrace, "task_id" | "version">, turns: ReadAvailabilityTurn[]): ReadAvailabilityTurn | undefined {
  if (!Number.isSafeInteger(trace.version) || trace.version < 0) return undefined;
  const linked = [...turns].reverse().filter(turn => turn.task_link?.task_id === trace.task_id
    && !["FAILED", "QUEUED", "LEASED"].includes(turn.status || "")
    && Number.isSafeInteger(turn.task_link.version) && turn.task_link.version! >= 0
    && turn.task_link.version! <= trace.version);
  return linked.find(turn => ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
    || ["CREATED", "CONTINUED"].includes(turn.task_link?.relationship || "")) || linked[0];
}
/** Classification comes from a normal task GET; the turn alone contains no
 * action kinds and must not be used to classify a financial receipt as READ. */
export function unavailableReadSelection(taskId: string, version: number, turns: ReadAvailabilityTurn[]): boolean {
  const linked = turns.filter(turn => turn.task_link?.task_id === taskId);
  return Boolean(linked.length && linked.every(turn => Number.isSafeInteger(turn.task_link?.version)
    && turn.task_link!.version! <= version) && readSourceUnavailable(readTraceOwner({ task_id: taskId, version }, turns)));
}
/** A display-only boundary, never permission or banking execution authority.
 * Unknown outcomes, money receipts and current confirmation stay independent. */
export function unavailableCompletedRead(trace: TaskTrace | null | undefined, turns: ReadAvailabilityTurn[]): boolean {
  return Boolean(trace && trace.state === "SUCCEEDED" && !trace.pending_confirmation && trace.nodes.length
    && trace.nodes.every(node => queryKinds.has(node.action)) && readSourceUnavailable(readTraceOwner(trace, turns)));
}
/** A READ-source error is not a successful trace or execution result. It can
 * only retire the focus of an already completed explicitly unavailable turn;
 * never an UNKNOWN, money receipt, mixed result or pending confirmation. */
export function unavailableReadFailureVersion(error: unknown, taskId: string, turns: ReadAvailabilityTurn[], cached?: TaskTrace | null): number | null {
  const value = error && typeof error === "object" ? error as { status?: unknown; code?: unknown } : {};
  if (value.status !== 409 || typeof value.code !== "string" || !["UNAVAILABLE_SCOPE", "CUSTOMER_READ_SNAPSHOT_SOURCE_CHANGED", "READ_SELECTION_SOURCE_CHANGED"].includes(value.code)
    || !cached || cached.task_id !== taskId || !unavailableCompletedRead(cached, turns)) return null;
  const linked = turns.filter(turn => turn.task_link?.task_id === taskId);
  if (!linked.length || linked.some(turn => !Number.isSafeInteger(turn.task_link?.version))) return null;
  const version = Math.max(...linked.map(turn => turn.task_link!.version!));
  if (cached.version < version) return null;
  if (linked.some(turn => turn.task_link?.version === version && turn.task_link.state !== "SUCCEEDED")) return null;
  const owner = readTraceOwner({ task_id: taskId, version }, turns);
  return owner?.status === "ANSWERED" && owner.task_link?.state === "SUCCEEDED"
    && unavailableReadSelection(taskId, version, turns) ? version : null;
}
