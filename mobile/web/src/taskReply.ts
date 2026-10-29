import type { TaskTrace } from "./types";
import { queryKinds, readTraceOwner, readSourceUnavailable, unavailableCompletedRead, type ReadAvailabilityTurn } from "./conversationReadAvailability.ts";
export { queryKinds } from "./conversationReadAvailability.ts";
export function isQueryOnlyTask(trace: TaskTrace | null | undefined): boolean {
  return Boolean(trace?.nodes.length && !trace.pending_confirmation && trace.nodes.every(node => queryKinds.has(node.action)));
}
export function inlineReplyTurn(trace: TaskTrace | null | undefined, turns: ReadAvailabilityTurn[]): string | null {
  if (!isQueryOnlyTask(trace) || !trace || !Number.isSafeInteger(trace.version) || trace.version < 0
    || !["SUCCEEDED", "FAILED", "PARTIAL", "UNKNOWN", "CLOSED"].includes(trace.state)) return null;
  if (unavailableCompletedRead(trace, turns)) return null;
  return readTraceOwner(trace, turns)?.turn_id || null;
}

/** Position the existing action card; this never supplies execution authority.
 * A status question never owns execution or an unconfirmed request. A newer
 * verified write receipt can follow an earlier same-task status observation,
 * without replacing that observation or crossing a different task's turn.
 * Older evidence stays with the earlier request it actually covers, never a
 * newer revision. App's current-version/confirmation guards remain unchanged.
 * Unlinked standalone tasks retain their separate presentation.
 */
export function requestDetailsTurn(trace: TaskTrace | null | undefined, turns: (ReadAvailabilityTurn & {sequence?:number;conversation_id?:string})[]): string | null {
  if (!trace || !Number.isSafeInteger(trace.version) || trace.version < 0 || unavailableCompletedRead(trace, turns)) return null;
  const owner = [...turns].reverse().find(turn => turn.task_link?.task_id === trace.task_id
    && (["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
      || ["CREATED", "CONTINUED"].includes(turn.task_link?.relationship || ""))
    && turn.status === "ANSWERED" && !turn.error_code
    && Number.isSafeInteger(turn.task_link?.version) && turn.task_link!.version! >= 0
    && turn.task_link!.version! <= trace.version);
  if (!owner || trace.state !== "SUCCEEDED" || trace.pending_confirmation || trace.outcome?.kind !== "BUSINESS_RECEIPT"
    || trace.outcome.state !== trace.state || trace.outcome.verified !== true || !trace.nodes.length
    || trace.nodes.some(node => node.status !== "SUCCEEDED" || node.error || node.result?.error)
    || isQueryOnlyTask(trace) || !owner.conversation_id || !Number.isSafeInteger(owner.sequence) || owner.sequence! < 1)
    return owner?.turn_id || null;
  const conversationTurns=turns.filter(turn=>turn.conversation_id===owner.conversation_id);
  if(conversationTurns.some(turn=>!Number.isSafeInteger(turn.sequence)||turn.sequence!<1)
    ||new Set(conversationTurns.map(turn=>turn.sequence)).size!==conversationTurns.length)return owner.turn_id;
  let anchor=owner.turn_id;
  for(const turn of conversationTurns.filter(turn=>turn.sequence!>owner.sequence!).sort((left,right)=>left.sequence!-right.sequence!)){
    // A new task keeps its own location and focus. This receipt must not be
    // appended beyond it just because the current trace was polled later.
    if(turn.task_link && turn.task_link.task_id!==trace.task_id
      ||["NEW_TASK","REVISE","CLARIFY"].includes(turn.mode||""))break;
    if(turn.status!=="ANSWERED" || turn.error_code || readSourceUnavailable(turn) || turn.mode!=="STATUS"
      ||turn.task_link?.task_id!==trace.task_id || !["STATUS","CURRENT"].includes(turn.task_link.relationship||"")
      ||!Number.isSafeInteger(turn.task_link.version) || turn.task_link.version!<owner.task_link!.version!
      ||turn.task_link.version!>trace.version)continue;
    anchor=turn.turn_id;
  }
  return anchor;
}
