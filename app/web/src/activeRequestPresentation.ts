import type { ConversationTurn } from "./conversationTypes";

export type ExternalRequestDetails = { taskId: string; version: number; state: string };

/** Only a navigation duplicate is covered. Its warning/reply is never pruned. */
export function requestNavigationCovered(
  turn: Pick<ConversationTurn, "turn_id" | "status" | "mode" | "error_code" | "task_link">,
  latestActionTurnId: string | undefined,
  details: ExternalRequestDetails | undefined,
): boolean {
  return Boolean(details && turn.status === "ANSWERED" && !turn.error_code
    && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
    && turn.turn_id === latestActionTurnId && turn.task_link?.task_id === details.taskId
    && Number.isSafeInteger(turn.task_link.version) && turn.task_link.version! >= 0
    && Number.isSafeInteger(details.version) && details.version >= turn.task_link.version!
    && turn.task_link.state === details.state);
}

/** Prune only a duplicate acknowledgement with an actually rendered, current
 * clarification/confirmation below it. History, warnings and actions remain.
 */
export function requestAcknowledgementCovered(
  turn: Pick<ConversationTurn, "turn_id" | "status" | "mode" | "error_code" | "task_link">,
  latestActionTurnId: string | undefined,
  details: ExternalRequestDetails | undefined,
): boolean {
  return Boolean(details && turn.status === "ANSWERED" && !turn.error_code
    && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
    && turn.turn_id === latestActionTurnId && turn.task_link?.task_id === details.taskId
    && Number.isSafeInteger(turn.task_link.version) && turn.task_link.version! >= 0
    && Number.isSafeInteger(details.version) && details.version >= turn.task_link.version!
    && turn.task_link.state === details.state
    && ["NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION"].includes(details.state));
}
