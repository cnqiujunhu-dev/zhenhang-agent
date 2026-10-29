import type { TaskTrace } from "./types";
import type { ConversationTurn } from "./conversationTypes";
import { isQueryOnlyTask } from "./taskReply.ts";

const initialStates = new Set(["QUEUED", "UNDERSTANDING", "PLANNING"]);
const queryStates = new Set(["PLANNED", "POLICY_CHECKED", "EXECUTING"]);
type PreparationTurn = Pick<ConversationTurn, "status" | "mode" | "error_code" | "task_link">;

/** Only an authorized matching preparation without results belongs to its reply. */
export function conversationOwnsPreparation(trace: TaskTrace | null | undefined, turns: readonly PreparationTurn[]): boolean {
  if (!trace || !Array.isArray(trace.nodes) || !Number.isSafeInteger(trace.version) || trace.version < 0
    || trace.pending_confirmation || trace.compensation_source
    || trace.receipt && Object.keys(trace.receipt).length
    || trace.analysis && Object.keys(trace.analysis).length
    || trace.outcome?.completed_operation_ids?.length
    || trace.nodes.some(node=>node.result && Object.keys(node.result).length || node.error
      || ["SUCCEEDED","UNKNOWN","FAILED","PARTIAL"].includes(node.status))) return false;
  const preparing = initialStates.has(trace.state) && trace.nodes.length === 0
    || queryStates.has(trace.state) && isQueryOnlyTask(trace);
  if (!preparing) return false;
  const latest = turns.at(-1);
  return Boolean(latest?.status === "ANSWERED" && latest.error_code !== "HISTORY_ACCESS_RECHECK"
    && ["NEW_TASK","REVISE","CLARIFY"].includes(latest.mode || "")
    && latest.task_link?.task_id === trace.task_id && Number.isSafeInteger(latest.task_link.version)
    && latest.task_link.version >= 0 && latest.task_link.version <= trace.version);
}

export function preparationReply(query: boolean, locale: string): string {
  return locale === "en" ? query ? "I'm checking the requested information. I'll show the results here." : "I'm working on your request. I'll show the result or any details you need to review here."
    : query ? "正在帮你查询，查到后会直接列在这里。" : "我正在处理你的需求，结果或需要你核对的信息会显示在这里。";
}
