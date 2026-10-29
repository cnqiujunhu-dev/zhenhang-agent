import type { Conversation } from "./conversationTypes";
import type { TaskTrace } from "./types";
import type { ComposerTaskChoice } from "./composerClarification";

/** A deliberately opened owned record can be the subject of a later status or
 * lifecycle question even when terminal. This is a target hint, not permission
 * to reopen it. The server binds only modes operating on an existing request. */
export function composerExplicitTaskBinding(input: {
  conversation: Conversation | null; currentId: string; trace: TaskTrace | null; activeId: string;
  policy: "AUTO" | "INDEPENDENT"; scopeKey: string; explicitChoice: ComposerTaskChoice | null; blocked: boolean;
}) {
  const { conversation, trace, explicitChoice: choice } = input;
  if (input.blocked || input.policy !== "AUTO" || !input.scopeKey || !conversation || !trace || !choice
    || conversation.conversation_id !== input.currentId || trace.task_id !== input.activeId
    || choice.scopeKey !== input.scopeKey || choice.taskId !== trace.task_id
    || !Number.isSafeInteger(choice.afterSequence) || choice.afterSequence < 0
    || !Number.isSafeInteger(trace.version) || trace.version < 1) return null;
  const turns = conversation.turns.filter(turn => turn.conversation_id === input.currentId);
  const latestSequence = Math.max(0, ...turns.map(turn => turn.sequence));
  if (choice.afterSequence > latestSequence) return null;
  const links = turns.filter(turn => turn.task_link?.task_id === trace.task_id);
  if (!links.length || links.some(turn => !Number.isSafeInteger(turn.task_link!.version) || turn.task_link!.version < 1)
    || trace.version < Math.max(...links.map(turn => turn.task_link!.version))) return null;
  if (turns.some(turn => turn.sequence > choice.afterSequence && turn.task_link
    && ["CREATED", "CONTINUED"].includes(turn.task_link.relationship))) return null;
  return { taskId: trace.task_id, version: trace.version, state: trace.state, deliberate: true as const };
}
