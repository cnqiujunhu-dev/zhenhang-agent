import type { Conversation, ConversationTurn } from "./conversationTypes";
export interface ConversationTaskSelection {
  scope: string;
  conversation_id: string;
  task_id: string;
  selected_after_sequence: number;
}
export const conversationTaskSelectionKey = (
  scope: string,
  conversationId: string,
) => `zx_v3_task_selection:${scope}:${conversationId}`;
export function taskSelectionRecord(
  value: unknown,
  scope: string,
  conversationId: string,
): ConversationTaskSelection | null {
  if (!value || typeof value !== "object") return null;
  const row = value as ConversationTaskSelection;
  return row.scope === scope &&
    row.conversation_id === conversationId &&
    typeof row.task_id === "string" &&
    Boolean(row.task_id) &&
    Number.isInteger(row.selected_after_sequence) &&
    row.selected_after_sequence >= 0
    ? row
    : null;
}
export function resolveConversationTaskSelection(
  conversation: Conversation,
  stored: unknown,
  scope: string,
  unavailableQueries: ReadonlySet<string> = new Set(),
) {
  const ids = new Set(
    conversation.turns.map((turn) => turn.task_link?.task_id).filter(id => id && !unavailableQueries.has(id)),
  );
  const selected = taskSelectionRecord(
    stored,
    scope,
    conversation.conversation_id,
  );
  const newest = [...conversation.turns]
    .reverse()
    .find(
      (turn) =>
        turn.status === "ANSWERED" &&
        turn.task_link &&
        !unavailableQueries.has(turn.task_link.task_id) &&
        ["CREATED", "CONTINUED"].includes(turn.task_link.relationship),
    );
  if (selected && ids.has(selected.task_id)) {
    if (newest?.task_link && newest.sequence > selected.selected_after_sequence)
      return {
        taskId: newest.task_link.task_id,
        reason: "NEWER_EXPLICIT_TURN" as const,
      };
    return { taskId: selected.task_id, reason: "RESTORED_SELECTION" as const };
  }
  if (selected && !unavailableQueries.has(selected.task_id) && conversation.has_earlier)
    return { taskId: null, reason: "WAITING_EARLIER_SELECTION" as const };
  return {
    taskId:
      conversation.current_task_id && ids.has(conversation.current_task_id)
        ? conversation.current_task_id
        : null,
    reason: "SERVER_CURRENT" as const,
  };
}
export function makeConversationTaskSelection(
  scope: string,
  conversationId: string,
  taskId: string,
  turns: ConversationTurn[],
): ConversationTaskSelection {
  return {
    scope,
    conversation_id: conversationId,
    task_id: taskId,
    selected_after_sequence: Math.max(0, ...turns.map((turn) => turn.sequence)),
  };
}
