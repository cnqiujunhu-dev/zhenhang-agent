import type { ConversationTurn } from "./conversationTypes";

type QueueTurn = Pick<ConversationTurn, "turn_id" | "conversation_id" | "sequence" | "status" | "error_code" | "task_link">;
export type ConversationQueueBlocker = { conversationId: string; turnId: string; sequence: number; status: "QUEUED" | "LEASED" };
export type ConversationQueueEvidence = { conversationId?: string; scopeKey?: string; blocked?: boolean };
const identity = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 120 && value.trim() === value;
const unsafeTaskStates = new Set(["UNKNOWN", "FAILED", "PARTIAL", "CLOSED", "CANCELLED", "CANCELED"]);
const pending = (turn: QueueTurn): turn is QueueTurn & { status: "QUEUED" | "LEASED" } => (turn.status === "QUEUED" || turn.status === "LEASED")
  && !turn.error_code && !unsafeTaskStates.has(turn.task_link?.state || "");

/** This explains an observed FIFO dependency, never a worker heartbeat or ETA.
 * A partial window is sufficient only when the blocker-to-message range is
 * present and contiguous. Missing earlier history is never guessed or fetched.
 */
export function conversationQueueBlockers(turns: readonly QueueTurn[], evidence: ConversationQueueEvidence): Map<string, ConversationQueueBlocker> {
  const blockers = new Map<string, ConversationQueueBlocker>();
  if (evidence.blocked || !identity(evidence.conversationId) || typeof evidence.scopeKey !== "string"
    || !evidence.scopeKey.trim() || !turns.length) return blockers;
  const ids = new Set<string>(), sequences = new Set<number>();
  for (const turn of turns) {
    if (!turn || turn.conversation_id !== evidence.conversationId || !identity(turn.turn_id)
      || !Number.isSafeInteger(turn.sequence) || turn.sequence < 1 || ids.has(turn.turn_id) || sequences.has(turn.sequence)
      || !["QUEUED", "LEASED", "ANSWERED", "FAILED"].includes(turn.status)) return blockers;
    ids.add(turn.turn_id); sequences.add(turn.sequence);
  }
  const rows = [...turns].sort((a, b) => a.sequence - b.sequence);
  // A coherent FIFO read cannot show two claimed turns or a claimed turn
  // behind an earlier queued turn. Do not explain a mixed/ambiguous snapshot.
  if (rows.filter(turn => pending(turn) && turn.status === "LEASED").length > 1) return blockers;
  let earlier: ConversationQueueBlocker | null = null;
  for (let index = 0; index < rows.length; index += 1) {
    const turn = rows[index];
    // Revalidated metadata placeholders and a missing middle segment cannot
    // explain a later message using a previously seen pending record.
    if (turn.error_code === "HISTORY_ACCESS_RECHECK" || index > 0 && turn.sequence !== rows[index - 1].sequence + 1) earlier = null;
    if (!pending(turn)) continue;
    if (turn.status === "LEASED" && earlier) return new Map();
    if (turn.status === "QUEUED" && earlier) blockers.set(turn.turn_id, earlier);
    if (!earlier) earlier = { conversationId: turn.conversation_id, turnId: turn.turn_id, sequence: turn.sequence, status: turn.status };
  }
  return blockers;
}

/** DOM address uses conversation + full turn identity, never the actor token. */
export function conversationQueueRecordId(conversationId: string, turnId: string): string {
  return `queue-message-${encodeURIComponent(JSON.stringify([conversationId, turnId]))}`;
}

export function queueJumpMustWait(active: Element | null, composing: boolean): boolean {
  return composing || Boolean(active?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]'));
}
