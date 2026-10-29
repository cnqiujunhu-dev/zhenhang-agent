import { canApplyTaskTrace } from "./bankInteraction.ts";
import type { TaskTrace } from "./types";

export type TaskSelectionTraceInput = {
  selectedTaskId: string;
  activeTaskId: string;
  trace: TaskTrace | null;
  stale: boolean;
  accessDenied: boolean;
  visible: boolean;
  readError: boolean;
};

/** Reopening an already authorized current record does not discard its readable detail. */
export function canRetainTaskSelectionTrace(input: TaskSelectionTraceInput): boolean {
  return Boolean(input.selectedTaskId && input.selectedTaskId === input.activeTaskId && input.trace
    && !input.stale && !input.accessDenied && input.visible && !input.readError
    && canApplyTaskTrace(input.trace, input.selectedTaskId, null));
}
