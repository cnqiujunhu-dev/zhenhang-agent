import type { Conversation } from "./conversationTypes";
import type { TaskTrace } from "./types";

export type ComposerTaskChoice = { scopeKey: string; taskId: string; afterSequence: number };
const closed = new Set(["SUCCEEDED", "FAILED", "CLOSED", "CANCELLED", "CANCELED"]);

/** Optional current-view routing only. Text is never completed or reclassified here. */
export function composerTaskBinding(input: {
  conversation: Conversation | null; currentId: string; trace: TaskTrace | null; activeId: string;
  policy: "AUTO" | "INDEPENDENT"; scopeKey: string; explicitChoice: ComposerTaskChoice | null; blocked: boolean;
}) {
  const { conversation, trace } = input;
  if (input.blocked || input.policy !== "AUTO" || !conversation || conversation.conversation_id !== input.currentId
    || !trace || trace.task_id !== input.activeId || !Number.isSafeInteger(trace.version) || trace.version < 1) return null;
  const linked = new Map<string, { state: string; version: number }>();
  for (const turn of [...conversation.turns].sort((a, b) => a.sequence - b.sequence)) {
    if (turn.conversation_id !== input.currentId || !turn.task_link || !Number.isSafeInteger(turn.task_link.version)) continue;
    linked.set(turn.task_link.task_id, { state: turn.task_link.state, version: turn.task_link.version });
  }
  const link = linked.get(trace.task_id);
  if (!link || trace.version < link.version || closed.has(trace.state)) return null;
  linked.set(trace.task_id, { state: trace.state, version: trace.version });
  const unfinished = [...linked].filter(([, item]) => !closed.has(item.state));
  const choice = input.explicitChoice;
  const newerRequest = choice && conversation.turns.some(turn => turn.sequence > choice.afterSequence && turn.task_link
    && ["CREATED", "CONTINUED"].includes(turn.task_link.relationship));
  const deliberate = choice?.scopeKey === input.scopeKey && choice.taskId === trace.task_id && !newerRequest;
  if (!deliberate && conversation.current_task_id !== trace.task_id) return null;
  if (!deliberate && (unfinished.length !== 1 || unfinished[0][0] !== trace.task_id)) return null;
  return { taskId: trace.task_id, version: trace.version, state: trace.state, deliberate };
}

/** A hint requires a current, uniquely owned clarification; it grants no action. */
export function composerClarificationContext(input: Parameters<typeof composerTaskBinding>[0]) {
  const binding = composerTaskBinding(input);
  return binding?.state === "NEED_CLARIFICATION" ? binding : null;
}
