import type {
  Conversation,
  ConversationTurn,
  ConversationTurnInput,
  PendingConversationRequest,
  ReviewedEvidenceRef,
} from "./conversationTypes";

export const conversationModeLabels: Record<string, string> = {
  CONSULT: "咨询说明",
  NEW_TASK: "新办理任务",
  CLARIFY: "继续补充当前任务",
  REVISE: "调整当前任务",
  STATUS: "办理状态",
  CONFIRM_HINT: "银行确认提示",
  CANCEL_HINT: "撤销操作提示",
  NEED_CONTEXT: "需要明确上下文",
};
export function hasProcessingTurn(turns: ConversationTurn[]) {
  return turns.some(
    (turn) => turn.status === "QUEUED" || turn.status === "LEASED",
  );
}
export function mergeConversationTurns(
  server: ConversationTurn[],
  accepted?: ConversationTurn | null,
) {
  const rows = [...server];
  if (accepted && !rows.some((row) => row.turn_id === accepted.turn_id))
    rows.push(accepted);
  return rows.sort((a, b) => a.sequence - b.sequence);
}
export function mergeConversationPages(
  latest: ConversationTurn[],
  earlier: ConversationTurn[],
) {
  const map = new Map(earlier.map((turn) => [turn.turn_id, turn]));
  for (const turn of latest) map.set(turn.turn_id, turn);
  return [...map.values()].sort((a, b) => a.sequence - b.sequence);
}
export function conversationTaskIds(conversation: Conversation | null) {
  return new Set(
    (conversation?.turns || [])
      .map((turn) => turn.task_link?.task_id)
      .filter((id): id is string => Boolean(id)),
  );
}
export function conversationTurnInput({
  message,
  requestId,
  policy,
  conversation,
  taskId,
  taskVersion,
  explicitTaskSelection = false,
  reviewedRefs = [],
  replyLanguage,
}: {
  message: string;
  requestId: string;
  policy: "AUTO" | "INDEPENDENT";
  conversation: Conversation | null;
  taskId?: string | null;
  taskVersion?: number;
  explicitTaskSelection?: boolean;
  reviewedRefs?: ReviewedEvidenceRef[];
  replyLanguage?: "zh-CN" | "en";
}): ConversationTurnInput {
  const input: ConversationTurnInput = {
    message: message.trim(),
    client_request_id: requestId,
    context_policy: policy,
  };
  if (replyLanguage === "en" || replyLanguage === "zh-CN") input.reply_language = replyLanguage;
  // A displayed legacy task or another thread's task is never silently inherited.
  if (
    policy === "AUTO" &&
    taskId &&
    conversationTaskIds(conversation).has(taskId)
  ) {
    input.current_task_id = taskId;
    if (Number.isInteger(taskVersion) && taskVersion! > 0)
      input.expected_task_version = taskVersion;
    if (explicitTaskSelection && input.expected_task_version !== undefined)
      input.explicit_task_selection = true;
  }
  const seen = new Set<string>();
  const refs = reviewedRefs
    .filter((ref) => {
      if (
        !ref ||
        typeof ref.review_id !== "string" ||
        !ref.review_id ||
        !Number.isInteger(ref.attachment_version) ||
        ref.attachment_version < 1 ||
        !/^[0-9a-f]{64}$/i.test(ref.sha256)
      )
        throw new Error("材料核对引用缺少版本或摘要，请重新核对材料。");
      const key = `${ref.review_id}:${ref.attachment_version}:${ref.sha256}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 8)
    .map((ref) => ({
      review_id: ref.review_id,
      attachment_version: ref.attachment_version,
      sha256: ref.sha256,
    }));
  if (refs.length) input.reviewed_evidence_refs = refs;
  return input;
}
export function pendingConversationForScope(
  value: unknown,
  scope: string,
): PendingConversationRequest | null {
  if (!value || typeof value !== "object") return null;
  const row = value as PendingConversationRequest;
  if (
    row.scope !== scope ||
    typeof row.conversation_request_id !== "string" ||
    typeof row.title !== "string" ||
    !row.payload ||
    typeof row.payload.message !== "string" ||
    typeof row.payload.client_request_id !== "string" ||
    !["AUTO", "INDEPENDENT"].includes(row.payload.context_policy)
    || row.payload.reply_language !== undefined && !["en", "zh-CN"].includes(row.payload.reply_language)
  )
    return null;
  return row.conversation_id === null || typeof row.conversation_id === "string"
    ? row
    : null;
}
