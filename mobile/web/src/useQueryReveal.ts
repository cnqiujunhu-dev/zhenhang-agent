import { useCallback, useEffect, useRef, useState } from "react";
import { queryReveal, queryRevealRequestKey, type QueryRevealInput } from "./queryReveal.ts";
import { isQueryOnlyTask } from "./taskReply.ts";

const now = () => performance.now();
const prefersReducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Cancellable local clock; no response snapshots, requests or persisted state. */
export function useQueryReveal(input: Omit<QueryRevealInput, "nowMs" | "reducedMotion" | "cancelled">) {
  const [, refreshClock] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion);
  const cancelled = useRef(new Set<string>());
  const key = queryRevealRequestKey(input.request);
  const binding = useRef({key, scopeKey: input.scopeKey});
  const lastBinding = useRef({key, scopeKey: input.scopeKey});
  binding.current = {key, scopeKey: input.scopeKey};
  const cancel = useCallback(() => {
    if (!key) return;
    if (key) cancelled.current.add(key);
    refreshClock(value => value + 1);
  }, [key]);
  const blocked = Boolean(input.blocked || ["ERROR", "HIDDEN"].includes(input.resultReadStatus || "")
    || input.trace?.pending_confirmation || input.turn?.status === "FAILED" || input.turn?.error_code === "HISTORY_ACCESS_RECHECK"
    || input.trace && (["FAILED", "PARTIAL", "UNKNOWN", "CLOSED", "CANCELLED", "CANCELED", "AWAITING_CONFIRMATION"].includes(input.trace.state)
      || input.trace.nodes.length > 0 && !isQueryOnlyTask(input.trace)
      || input.trace.nodes.some(node => node.result?.error || ["FAILED", "UNKNOWN"].includes(node.status))
      || input.trace.state === "SUCCEEDED" && input.trace.outcome?.verified !== true));
  const changedScopeForSameRequest = lastBinding.current.scopeKey !== input.scopeKey && lastBinding.current.key === key;
  const view = queryReveal({...input, nowMs: now(), reducedMotion,
    cancelled: cancelled.current.has(key) || changedScopeForSameRequest, blocked});
  useEffect(() => {
    const wasCancelled = cancelled.current.has(key);
    if (lastBinding.current.scopeKey !== input.scopeKey && lastBinding.current.key) cancelled.current.add(lastBinding.current.key);
    lastBinding.current = {key, scopeKey: input.scopeKey};
    if ((blocked || reducedMotion) && key) cancelled.current.add(key);
    if (!wasCancelled && cancelled.current.has(key)) refreshClock(value => value + 1);
    // The set contains opaque local receipt keys only and stays bounded.
    while (cancelled.current.size > 4) cancelled.current.delete(cancelled.current.values().next().value!);
  }, [input.scopeKey, key, blocked, reducedMotion]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setReducedMotion(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (view.nextWakeAtMs === null) return;
    const expected = {key, scopeKey: input.scopeKey};
    const timer = window.setTimeout(() => {
      if (binding.current.key === expected.key && binding.current.scopeKey === expected.scopeKey && !cancelled.current.has(expected.key)) refreshClock(value => value + 1);
    }, Math.max(0, Math.ceil(view.nextWakeAtMs - now()) + 1));
    return () => window.clearTimeout(timer);
  }, [key, input.scopeKey, view.nextWakeAtMs]);
  return {...view, cancel};
}
