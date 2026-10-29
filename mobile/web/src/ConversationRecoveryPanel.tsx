import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { conversationRecoveryCopy } from "./conversationRecoveryCopy";

/** The primary action only reads the original acknowledgement. Same-request
 * replay and leaving recovery remain explicit secondary choices. */
export function ConversationRecoveryPanel({ message, incomplete, busy, waiting, remainingSeconds,
  canCheck, canRetry, canLeave, onCheck, onRetry, onLeave }: {
  message?: string; incomplete: boolean; busy: boolean; waiting: boolean; remainingSeconds: number;
  canCheck: boolean; canRetry: boolean; canLeave: boolean;
  onCheck: () => void; onRetry: () => void; onLeave: () => void;
}) {
  const { locale } = useCustomerLanguage(), copy = conversationRecoveryCopy(locale);
  return <div className="pending-request-note conversation-recovery-panel">
    <p role="status" aria-live="polite">{message || (incomplete ? copy.incomplete : copy.uncertain)}</p>
    {(canCheck || busy || waiting || canRetry || canLeave) && <button type="button" className="button subtle compact" disabled={busy || waiting || !canCheck}
      onClick={() => { if (!busy && !waiting && canCheck) onCheck(); }}>
      {busy ? copy.checking : waiting ? copy.later(remainingSeconds) : copy.check}</button>}
    {(canRetry || canLeave) && <details className="conversation-recovery-options">
      <summary>{copy.more}</summary>
      {canRetry && <><p>{copy.retryNotice}</p><button type="button" className="text-button" disabled={busy || waiting}
        onClick={() => { if (!busy && !waiting) onRetry(); }}>{copy.retry}</button></>}
      {canLeave && <button type="button" className="text-button" disabled={busy}
        onClick={() => { if (!busy) onLeave(); }}>{copy.leave}</button>}
    </details>}
  </div>;
}
