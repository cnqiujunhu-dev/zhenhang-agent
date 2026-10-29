import type { TaskTrace } from "./types";

export type TaskFocusPresentation = {
  selectors: readonly string[];
  label: string;
  confirmationId: string | null;
};

/** Choose a reading/focus destination only; never changes confirmation or task state. */
export function taskFocusPresentation(
  trace: Pick<TaskTrace, "task_id" | "state" | "version" | "nodes" | "pending_confirmation">,
  locale: "zh-CN" | "en",
): TaskFocusPresentation {
  const label = (zh: string, en: string) => locale === "en" ? en : zh;
  const view = (selectors: string[], text: string, confirmationId: string | null = null): TaskFocusPresentation =>
    ({ selectors, label: text, confirmationId });
  if (trace.state === "NEED_CLARIFICATION") return view(
    [".clarification-card", ".task-recovery-note", ".receipt-card"],
    label("当前事项需要补充的信息", "Information needed for this request"),
  );
  if (trace.state === "UNKNOWN") return view(
    [".unknown-note", ".task-recovery-note", ".receipt-card"],
    label("本次操作结果正在核查", "This operation's result is being checked"),
  );
  if (["FAILED", "PARTIAL", "CLOSED", "CANCELLED", "CANCELED"].includes(trace.state)) return view(
    [".receipt-card", ".task-recovery-note"],
    trace.state === "FAILED" ? label("本次事项未完成的说明", "Why this request did not complete")
      : trace.state === "PARTIAL" ? label("当前事项已完成和未完成的部分", "Completed and unfinished parts of this request")
      : label("当前事项已关闭，已完成的操作保留", "This request is closed; completed actions are retained"),
  );
  const pending = trace.pending_confirmation;
  // The customer endpoint returns runtime parents. Its pending tuple belongs to
  // a waiting child; expected_task_version is that child's version, not the parent's.
  const waitingChildren = pending && Array.isArray(trace.nodes)
    ? trace.nodes.filter(node => node.child_task_id === pending.task_id) : [];
  const currentChildConfirmation = Boolean(pending
    && [pending.task_id, pending.confirmation_id, pending.plan_hash, pending.node_key]
      .every(value => typeof value === "string" && value.trim())
    && Number.isSafeInteger(pending.expected_task_version) && pending.expected_task_version >= 1
    && typeof trace.task_id === "string" && trace.task_id.trim() && pending.task_id !== trace.task_id
    && waitingChildren.length === 1 && waitingChildren[0].status === "WAITING_CONFIRMATION");
  if (["AWAITING_CONFIRMATION", "WAITING_CONFIRMATION", "PLANNED", "POLICY_CHECKED"].includes(trace.state)
    && currentChildConfirmation && Number.isSafeInteger(trace.version) && trace.version >= 1) return view(
    [".confirmation-card", ".task-recovery-note", ".receipt-card"],
    label("当前事项的待确认信息", "Review and confirm this request"), pending!.confirmation_id,
  );
  return view([".receipt-card", ".task-recovery-note"],
    label("当前事项的办理详情", "Current request details"));
}
