import type { TaskTrace } from "./types";

function plainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}
function emptyOptionalObject(value: unknown): boolean {
  return value === undefined || value === null || plainObject(value) && Object.keys(value).length === 0;
}

/** A fresh customer DTO marker is presentation only, never bank authority.
 * No text/FEEDBACK intent can hide an existing confirmation or uncertain
 * result. Resuming/new confirmation/version withdraws this display guard.
 */
export function isCustomerPausedPlan(trace: TaskTrace | null | undefined): boolean {
  const pause = trace?.paused_plan;
  return Boolean(trace && pause && Object.keys(pause).length === 5 && pause.version === "customer-paused-plan-v1"
    && pause.paused === true && pause.confirmation_available === false
    && pause.task_id === trace.task_id && Number.isSafeInteger(pause.task_version) && pause.task_version >= 1
    && pause.task_version === trace.version && ["NEED_CLARIFICATION", "AWAITING_CONFIRMATION"].includes(trace.state)
    && trace.pending_confirmation === null && Array.isArray(trace.nodes) && trace.nodes.length === 0
    && emptyOptionalObject(trace.receipt) && emptyOptionalObject(trace.analysis)
    && !trace.compensation_source
    && (trace.outcome === undefined || trace.outcome === null || plainObject(trace.outcome)
      && Array.isArray(trace.outcome.completed_operation_ids) && trace.outcome.completed_operation_ids.length === 0
      && trace.outcome.state === trace.state && trace.outcome.kind === "PROCESS" && trace.outcome.verified === false));
}
