import type { TaskTrace } from "./types";
import type { ReceiptPresentation } from "./receiptPresentation";

/** Narration is a second, optional delivery. It cannot gate verified facts or
 * reuse the previous task revision's prose while its replacement is pending. */
export function customerResultNarration(trace: TaskTrace, presentation: ReceiptPresentation) {
  const pending = ["QUEUED", "PROCESSING", "STALE"].includes(trace.customer_reply_status || "");
  const progressive = Boolean(trace.customer_reply_status) && trace.state === "SUCCEEDED"
    && trace.outcome?.state === trace.state && trace.outcome.verified === true
    && ["QUERY_RESULT", "COMMITTED"].includes(presentation.kind) && !trace.pending_confirmation;
  return { pending, waiting: ["QUEUED", "PROCESSING"].includes(trace.customer_reply_status || ""),
    progressive, response: pending ? null : trace.assistant_response };
}
