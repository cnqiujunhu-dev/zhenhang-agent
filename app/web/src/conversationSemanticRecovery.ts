import type { ConversationTurn } from "./conversationTypes";

/** A server-issued retry is for the same understanding turn only. It never
 * grants bank execution or retries an uncertain financial result. */
export function understandingRetryVersion(turn: ConversationTurn, context: {
  conversationId?: string; latestTurnId?: string; blocked: boolean;
}): number | null {
  const value = turn.response_meta?.recovery;
  if (context.blocked || !context.conversationId || turn.conversation_id !== context.conversationId
    || turn.turn_id !== context.latestTurnId || turn.task_link || turn.status !== "ANSWERED" || turn.mode !== "NEED_CONTEXT"
    || !value || typeof value !== "object" || Array.isArray(value)
    || value.version !== "semantic-recovery-v1" || value.action !== "RETRY_UNDERSTANDING"
    || value.can_retry !== true || !["SERVICE_UNAVAILABLE", "RESPONSE_INVALID"].includes(String(value.kind))
    || !Number.isSafeInteger(value.retry_version) || Number(value.retry_version) < 0 || Number(value.retry_version) >= 2)
    return null;
  return Number(value.retry_version);
}

export function understandingRetryAcknowledged(value: unknown, original: ConversationTurn): value is ConversationTurn {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const turn = value as ConversationTurn;
  return turn.turn_id === original.turn_id && turn.conversation_id === original.conversation_id
    && turn.sequence === original.sequence && ["QUEUED", "LEASED", "ANSWERED", "FAILED"].includes(turn.status)
    && Array.isArray(turn.sources);
}
