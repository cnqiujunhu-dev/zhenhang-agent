import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CustomerMotionLedger, customerMotionUrgent, decideCustomerMotion, type CustomerMotionEvent, type CustomerMotionRequest, type CustomerMotionSnapshot } from "./customerMotion";

export type UseCustomerMotionInput = {
  snapshot: CustomerMotionSnapshot | null;
  liveRequest?: CustomerMotionRequest | null;
  allowMotion: boolean;
  blocked?: boolean;
  rereading?: boolean;
  sensitiveEditing?: boolean;
};
const reduced = () => typeof window === "undefined" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const forced = () => typeof window !== "undefined" && window.matchMedia("(forced-colors: active)").matches;
const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
function verificationEditing() {
  const target = typeof document !== "undefined" ? document.activeElement : null;
  return typeof HTMLElement !== "undefined" && target instanceof HTMLElement && target.matches('input[autocomplete="one-time-code"], input[id*="otp" i], input[name*="otp" i], .confirmation-card input, .password-modal input');
}

/** No focus, scroll, requests, bank callbacks or persisted state. */
export function useCustomerMotion(input: UseCustomerMotionInput): CustomerMotionEvent | null {
  const ledger = useRef<CustomerMotionLedger | null>(null);
  if (!ledger.current) ledger.current = new CustomerMotionLedger();
  const previous = useRef<CustomerMotionSnapshot | null>(null);
  const [event, setEvent] = useState<CustomerMotionEvent | null>(null);
  const [reducedMotion, setReducedMotion] = useState(reduced);
  const [forcedColors, setForcedColors] = useState(forced);
  const [documentHidden, setDocumentHidden] = useState(hidden);
  const [actualEditing, setActualEditing] = useState(false);
  const [sensitiveFocus, setSensitiveFocus] = useState(verificationEditing);
  const composition = useRef(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const mediaChanged = () => setReducedMotion(media.matches);
    const colors = window.matchMedia("(forced-colors: active)");
    const colorsChanged = () => setForcedColors(colors.matches);
    const visibilityChanged = () => setDocumentHidden(hidden());
    const focusChanged = () => setSensitiveFocus(verificationEditing());
    let quietTimer: ReturnType<typeof setTimeout> | undefined;
    const isEditor = (target: EventTarget | null) => target instanceof HTMLElement && target.matches('input,textarea,select,[contenteditable="true"]');
    const inputChanged = (change: Event) => {
      if (!isEditor(change.target)) return;
      setActualEditing(true);
      clearTimeout(quietTimer);
      quietTimer = setTimeout(() => { if (!composition.current) setActualEditing(false); }, 750);
    };
    const compositionStarted = (change: Event) => { if (isEditor(change.target)) { composition.current = true; setActualEditing(true); } };
    const compositionEnded = (change: Event) => { if (isEditor(change.target)) { composition.current = false; inputChanged(change); } };
    media.addEventListener("change", mediaChanged);
    colors.addEventListener("change", colorsChanged);
    document.addEventListener("visibilitychange", visibilityChanged);
    document.addEventListener("focusin", focusChanged);
    document.addEventListener("focusout", focusChanged);
    // Observe after React's delegated input/composition handlers. A capture-
    // phase setState can commit the old controlled value before onChange reads
    // the user's first character; motion must never rewrite the draft.
    document.addEventListener("input", inputChanged);
    document.addEventListener("compositionstart", compositionStarted);
    document.addEventListener("compositionend", compositionEnded);
    return () => {
      clearTimeout(quietTimer);
      media.removeEventListener("change", mediaChanged);
      colors.removeEventListener("change", colorsChanged);
      document.removeEventListener("visibilitychange", visibilityChanged);
      document.removeEventListener("focusin", focusChanged);
      document.removeEventListener("focusout", focusChanged);
      document.removeEventListener("input", inputChanged);
      document.removeEventListener("compositionstart", compositionStarted);
      document.removeEventListener("compositionend", compositionEnded);
    };
  }, []);
  const key = JSON.stringify([input.snapshot, input.liveRequest, input.allowMotion, input.blocked, input.rereading,
    input.sensitiveEditing, reducedMotion, forcedColors, documentHidden, actualEditing, sensitiveFocus]);
  useLayoutEffect(() => {
    const next = decideCustomerMotion({ previous: previous.current, next: input.snapshot, liveRequest: input.liveRequest,
      allowMotion: input.allowMotion, reducedMotion, forcedColors, hidden: documentHidden, blocked: input.blocked, rereading: input.rereading,
      sensitiveEditing: Boolean(input.sensitiveEditing || actualEditing || sensitiveFocus || verificationEditing()), nowMs: performance.now() }, ledger.current!);
    previous.current = input.snapshot;
    setEvent(next);
    if (!next) return;
    const timer = window.setTimeout(() => setEvent(current => current?.id === next.id ? null : current), next.durationMs + next.maxDelayMs + 24);
    return () => window.clearTimeout(timer);
  }, [key]);
  // Suppression applies in the same render, before a layout effect can clear an old receipt.
  if (!input.allowMotion || input.blocked || input.rereading || input.sensitiveEditing || actualEditing || sensitiveFocus
    || reducedMotion || forcedColors || documentHidden || customerMotionUrgent(input.snapshot) || event?.scopeKey !== input.snapshot?.scopeKey
    || event?.turnId !== input.snapshot?.turnId || event?.taskId !== (input.snapshot?.taskId || null)
    || event?.version !== (input.snapshot?.version || 0)) return null;
  return event;
}
