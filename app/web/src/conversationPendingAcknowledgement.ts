import type { PendingConversationRequest } from "./conversationTypes";

/** A late acknowledgement may clear only the exact pending request it accepted. */
export function matchesConversationAcknowledgement(
  record: PendingConversationRequest | null,
  accepted: PendingConversationRequest,
): boolean {
  return Boolean(record && record.scope === accepted.scope &&
    record.conversation_id === accepted.conversation_id &&
    record.payload.client_request_id === accepted.payload.client_request_id &&
    record.conversation_request_id === accepted.conversation_request_id);
}
