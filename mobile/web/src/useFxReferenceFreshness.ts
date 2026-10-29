import { useEffect, useRef, useState } from "react";
import { fxReferenceState } from "./fxReferenceFreshness.ts";
import type { TraceNode } from "./types";

export type FxReferenceWarning = "EXPIRED" | "CHANGED" | "INVALID";

/** One local deadline; no requests, rate refresh, persistence, focus or bank actions. */
export function useFxReferenceFreshness(node: TraceNode) {
  const latest = useRef(node);
  latest.current = node;
  const expiredScopes = useRef(new Set<string>());
  const [, invalidate] = useState(0);
  const [warning, setWarning] = useState<{ scopeKey: string; reason: FxReferenceWarning } | null>(null);
  const current = fxReferenceState(node);
  const rememberExpiry = (scopeKey: string) => {
    expiredScopes.current.add(scopeKey);
    while (expiredScopes.current.size > 8) expiredScopes.current.delete(expiredScopes.current.values().next().value!);
  };
  if (current.expired) rememberExpiry(current.scopeKey);
  const expired = current.expired || expiredScopes.current.has(current.scopeKey);
  useEffect(() => {
    if (current.deadline === null || expired || typeof window === "undefined" || typeof document === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observe = () => {
      clearTimeout(timer);
      const observed = fxReferenceState(latest.current);
      if (observed.scopeKey !== current.scopeKey) return;
      if (observed.expired) rememberExpiry(observed.scopeKey);
      invalidate(value => value + 1);
      if (!observed.expired && observed.deadline !== null && document.visibilityState === "visible")
        timer = setTimeout(observe, Math.max(1, Math.min(2_147_483_647, observed.deadline - Date.now())));
    };
    observe();
    window.addEventListener("focus", observe);
    window.addEventListener("pageshow", observe);
    document.addEventListener("visibilitychange", observe);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", observe);
      window.removeEventListener("pageshow", observe);
      document.removeEventListener("visibilitychange", observe);
    };
  }, [current.scopeKey, current.deadline, expired]);
  const checkCurrent = () => {
    const observed = fxReferenceState(latest.current);
    if (observed.expired) rememberExpiry(observed.scopeKey);
    const isExpired = observed.expired || expiredScopes.current.has(observed.scopeKey);
    const reason: FxReferenceWarning | null = observed.scopeKey !== current.scopeKey ? "CHANGED"
      : isExpired ? "EXPIRED" : !observed.canPrepare ? "INVALID" : null;
    if (reason) {
      setWarning({ scopeKey: observed.scopeKey, reason });
      invalidate(value => value + 1);
      return false;
    }
    setWarning(null);
    return true;
  };
  return { expired, canPrepare: current.canPrepare && !expired,
    warning: warning?.scopeKey === current.scopeKey ? warning.reason : null, checkCurrent };
}
