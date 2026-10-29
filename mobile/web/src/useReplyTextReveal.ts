import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { replyTextFrame, replyTextPlan, type ReplyTextRevealLedger } from "./replyTextReveal";

type Input = { text: string; identity: string; enabled: boolean; ledger: ReplyTextRevealLedger };
type Frame = { identity: string; text: string; visible: string; animated: boolean };
const suppressed = () => typeof window === "undefined" || typeof window.matchMedia !== "function"
  || window.matchMedia("(prefers-reduced-motion: reduce)").matches
  || window.matchMedia("(forced-colors: active)").matches
  || typeof document !== "undefined" && document.visibilityState === "hidden";

/** Displays an already received reply; never starts requests, changes a bank
 * action, focuses an element, or scrolls. Old text is never a render fallback. */
export function useReplyTextReveal(input: Input) {
  const current = useRef(input); current.current = input;
  const plan = useMemo(() => replyTextPlan(input.text), [input.text]);
  const run = useRef<{ identity: string; text: string; startedAt: number; complete: boolean } | null>(null);
  const [frame, setFrame] = useState<Frame | null>(null);
  const eligible = Boolean(input.enabled && input.identity && plan.durationMs && !suppressed());
  const matchingFrame = frame?.identity === input.identity && frame.text === input.text;
  // This synchronous first frame prevents a full answer flashing before the
  // layout effect starts. Existing history and changed text stay fully shown.
  const beginning = eligible && !matchingFrame && !input.ledger.has(input.identity);
  const animated = Boolean(matchingFrame && frame.animated || beginning);
  const visible = eligible ? matchingFrame ? frame.visible : beginning ? "" : input.text : input.text;

  useLayoutEffect(() => {
    let raf = 0, disposed = false;
    const sameInput = () => current.current.identity === input.identity && current.current.text === input.text;
    const publish = (value: string, started: boolean) => {
      if (!disposed && sameInput()) setFrame(old => old?.identity === input.identity && old.text === input.text
        && old.visible === value && old.animated === started ? old
        : { identity: input.identity, text: input.text, visible: value, animated: started });
    };
    const previous = run.current;
    const sameRun = previous?.identity === input.identity && previous.text === input.text;
    if (!eligible) {
      if (input.identity && input.text) input.ledger.claim(input.identity);
      run.current = { identity: input.identity, text: input.text, startedAt: 0, complete: true };
      publish(input.text, Boolean(sameRun && matchingFrame && frame.animated));
      return;
    }
    // A StrictMode effect replay resumes the same local clock. A real remount
    // has no run and the shared ledger prevents replaying the completed text.
    if (!sameRun && !input.ledger.claim(input.identity)) {
      run.current = { identity: input.identity, text: input.text, startedAt: 0, complete: true };
      publish(input.text, false);
      return;
    }
    if (!sameRun) run.current = { identity: input.identity, text: input.text, startedAt: performance.now(), complete: false };
    const active = run.current!;
    if (active.complete) { publish(input.text, Boolean(matchingFrame && frame.animated)); return; }
    const finish = () => {
      active.complete = true;
      cancelAnimationFrame(raf); raf = 0;
      publish(input.text, true);
    };
    const advance = (now: number) => {
      raf = 0;
      if (disposed || !sameInput()) return;
      if (!current.current.enabled || suppressed()) { finish(); return; }
      const next = replyTextFrame(plan, now - active.startedAt);
      publish(next.visible, true);
      if (next.complete) active.complete = true;
      else raf = requestAnimationFrame(advance);
    };
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const colors = window.matchMedia("(forced-colors: active)");
    const cancelWhenSuppressed = () => { if (suppressed()) finish(); };
    media.addEventListener("change", cancelWhenSuppressed);
    colors.addEventListener("change", cancelWhenSuppressed);
    document.addEventListener("visibilitychange", cancelWhenSuppressed);
    advance(performance.now());
    return () => {
      disposed = true; cancelAnimationFrame(raf);
      media.removeEventListener("change", cancelWhenSuppressed);
      colors.removeEventListener("change", cancelWhenSuppressed);
      document.removeEventListener("visibilitychange", cancelWhenSuppressed);
    };
  }, [input.identity, input.text, input.enabled, input.ledger, plan, eligible]);
  return { visible, animated, revealing: animated && visible !== input.text };
}
