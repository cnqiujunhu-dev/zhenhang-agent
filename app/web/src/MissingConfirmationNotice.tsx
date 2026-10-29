import { CustomerText } from "./CustomerLanguageProvider";
import type { TaskTrace } from "./types";

/** Recover an unavailable approval view; never infer success or retry a payment. */
export function MissingConfirmationNotice({trace,blocked,onEdit}:{trace:TaskTrace;blocked:boolean;onEdit:()=>void}) {
  if(trace.state!=="AWAITING_CONFIRMATION"||trace.pending_confirmation)return null;
  return <div className="task-recovery-note" role="alert">
    <p><CustomerText text="当前确认资料不可用。请重新核对资料并修改需求，准备新的确认；不会自动重试或执行。" /></p>
    {trace.allowed_actions.includes("REVISE")&&<button className="button secondary compact" disabled={blocked}
      onClick={onEdit}><CustomerText text="修改需求并重新准备确认" /></button>}
  </div>;
}
