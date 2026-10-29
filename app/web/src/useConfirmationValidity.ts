import {useEffect, useRef, useState} from "react";
import {sameAuthRecoveryScope, type AuthRecoveryScope} from "./useAuthRecovery";

export type ConfirmationValidityState = "CURRENT" | "EXPIRED" | "UNAVAILABLE";
type Observation = {state: ConfirmationValidityState; deadlineMs: number | null; remainingMs: number};
type Lifetime = {deadlineMs: number; monotonicDeadlineMs: number; expired: boolean};
export const CONFIRMATION_VALIDITY_RECORD_LIMIT = 128;

function scopeKey(scope: AuthRecoveryScope | null): string | null {
  if (!scope || !Number.isSafeInteger(scope.actorEpoch) || scope.actorEpoch < 0
    || !Number.isSafeInteger(scope.expectedVersion) || scope.expectedVersion < 1
    || [scope.taskId, scope.childTaskId, scope.confirmationId, scope.planHash].some(value => typeof value !== "string" || !value)) return null;
  return JSON.stringify([scope.actorEpoch, scope.taskId, scope.childTaskId, scope.confirmationId, scope.planHash, scope.expectedVersion]);
}

/** Display/entry guard only. The server remains authoritative for execution.
 * A known confirmation deadline can shorten but cannot extend in this lifetime,
 * and observed expiry remains expired when the wall clock moves backwards. */
export class ConfirmationValidityLifetime {
  private records = new Map<string, Lifetime>();
  observe(scope: AuthRecoveryScope | null, expiresAt: string | undefined, wallNow = Date.now(), monotonicNow = performance.now()): Observation {
    const key = scopeKey(scope);
    if (!key) return {state: "UNAVAILABLE", deadlineMs: null, remainingMs: 0};
    const previous = this.records.get(key);
    if (previous?.expired) return {state: "EXPIRED", deadlineMs: previous.deadlineMs, remainingMs: 0};
    // Never evict a known expired identity just to admit an unfamiliar scope.
    if (!previous && this.records.size >= CONFIRMATION_VALIDITY_RECORD_LIMIT)
      return {state: "UNAVAILABLE", deadlineMs: null, remainingMs: 0};
    const deadline = typeof expiresAt === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(expiresAt) ? Date.parse(expiresAt) : NaN;
    if (![deadline, wallNow, monotonicNow].every(Number.isFinite)) return {state: "UNAVAILABLE", deadlineMs: null, remainingMs: 0};
    const next = {deadlineMs: Math.min(previous?.deadlineMs ?? deadline, deadline),
      monotonicDeadlineMs: Math.min(previous?.monotonicDeadlineMs ?? Infinity, monotonicNow + Math.max(0, deadline - wallNow)), expired: false};
    next.expired = wallNow >= next.deadlineMs || monotonicNow >= next.monotonicDeadlineMs;
    this.records.set(key, next);
    return {state: next.expired ? "EXPIRED" : "CURRENT", deadlineMs: next.deadlineMs,
      remainingMs: next.expired ? 0 : Math.max(0, Math.min(next.deadlineMs - wallNow, next.monotonicDeadlineMs - monotonicNow))};
  }
}

export function confirmationValidityText(locale: string, state: ConfirmationValidityState) {
  if (state === "EXPIRED") return locale === "en"
    ? "This confirmation expired. Edit the request and prepare a new confirmation. Nothing is resubmitted automatically."
    : "本次确认已过期。请修改需求并重新准备确认，不会自动重新提交。";
  return locale === "en"
    ? "This confirmation's current validity could not be verified. Read the latest request details or reload this page before preparing a new confirmation. Reloading does not verify or execute an action."
    : "本次确认当前有效性尚未核验。请读取最新办理信息，或重新加载页面后再准备新的确认；重新加载不会验证或执行操作。";
}

export function confirmationValidityLabel(locale: string, state: ConfirmationValidityState) {
  return state === "EXPIRED" ? locale === "en" ? "Confirmation expired" : "确认已过期"
    : locale === "en" ? "Confirmation needs checking" : "确认有效期待核验";
}

/** Expiry timers and restored-page checks update display only, never a request. */
export function useConfirmationValidity(scope: AuthRecoveryScope | null, expiresAt: string | undefined) {
  const lifetime = useRef<ConfirmationValidityLifetime | null>(null);
  const lifetimeActor = useRef(scope?.actorEpoch);
  if (scope && lifetimeActor.current !== scope.actorEpoch) {
    lifetime.current = new ConfirmationValidityLifetime();
    lifetimeActor.current = scope.actorEpoch;
  }
  if (!lifetime.current) lifetime.current = new ConfirmationValidityLifetime();
  const binding = useRef({scope, expiresAt});
  binding.current = {scope, expiresAt};
  const [, refresh] = useState(0);
  const observation = lifetime.current.observe(scope, expiresAt);
  useEffect(() => {
    if (!scope) return;
    const boundScope = {...scope}, boundExpiry = expiresAt;
    let disposed = false, timer: ReturnType<typeof setTimeout> | undefined;
    const observe = () => {
      if (disposed || !sameAuthRecoveryScope(boundScope, binding.current.scope) || boundExpiry !== binding.current.expiresAt) return;
      clearTimeout(timer);
      const latest = lifetime.current!.observe(boundScope, boundExpiry);
      refresh(version => version + 1);
      if (latest.state === "CURRENT") timer = setTimeout(observe, Math.max(1, Math.min(2_147_483_647, latest.remainingMs)));
    };
    observe();
    window.addEventListener("focus", observe);
    window.addEventListener("pageshow", observe);
    document.addEventListener("visibilitychange", observe);
    return () => {
      disposed = true; clearTimeout(timer);
      window.removeEventListener("focus", observe); window.removeEventListener("pageshow", observe);
      document.removeEventListener("visibilitychange", observe);
    };
  }, [scope?.actorEpoch, scope?.taskId, scope?.childTaskId, scope?.confirmationId, scope?.planHash, scope?.expectedVersion, expiresAt]);
  function isCurrent(boundScope: AuthRecoveryScope | null = scope) {
    if (!sameAuthRecoveryScope(boundScope, binding.current.scope)) return false;
    return lifetime.current!.observe(binding.current.scope, binding.current.expiresAt).state === "CURRENT";
  }
  return {...observation, current: observation.state === "CURRENT", isCurrent};
}
