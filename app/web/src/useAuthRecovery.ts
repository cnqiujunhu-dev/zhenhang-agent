import { useEffect, useRef, useState } from "react";
import type { CustomerRecovery } from "./customerRecovery";

export type AuthRecoveryScope = {
  actorEpoch: number;
  taskId: string;
  childTaskId: string;
  confirmationId: string;
  planHash: string;
  expectedVersion: number;
};
export type AuthRecoveryRecord = {
  scope: AuthRecoveryScope;
  challengeId: string | null;
  operation: "challenge" | "verify";
  info: CustomerRecovery;
  deadlineMs: number | null;
};

export function sameAuthRecoveryScope(a: AuthRecoveryScope | null, b: AuthRecoveryScope | null) {
  return Boolean(a && b && a.actorEpoch === b.actorEpoch && a.taskId === b.taskId
    && a.childTaskId === b.childTaskId && a.confirmationId === b.confirmationId
    && a.planHash === b.planHash && a.expectedVersion === b.expectedVersion);
}
export function authRecoveryNeedsRead(info: CustomerRecovery) {
  return info.resultUnknown || ["REVIEW_LATEST", "RECOVER_ORIGINAL", "VERIFY_ACCESS", "REFRESH", "LOGIN"].includes(info.action);
}
export function authRecoveryText(locale: string, kind: "reading" | "ready" | "wait", seconds = 0) {
  if (locale === "en") return kind === "reading" ? "Reading the latest confirmation…"
    : kind === "ready" ? "Review the latest details, then request a new code if verification is still required. Reading this confirmation does not verify or execute the current step."
    : `Wait ${seconds} seconds before trying again. Nothing will be submitted automatically.`;
  return kind === "reading" ? "正在读取最新确认…"
    : kind === "ready" ? "请重新核对最新内容；若仍需验证，可主动获取新验证码。本次读取不会验证或执行当前步骤。"
    : `请等待${seconds}秒后再试，不会自动提交请求。`;
}

/** In-memory, exact-confirmation recovery. Timers refresh display only; no POST,
 * implicit success, stored credentials or automatic challenge renewal. */
export function useAuthRecovery(scope: AuthRecoveryScope | null, challengeId: string | null) {
  const current = useRef({ scope, challengeId });
  current.current = { scope, challengeId };
  const recordRef = useRef<AuthRecoveryRecord | null>(null);
  const readingRef = useRef<{ scope: AuthRecoveryScope; record: AuthRecoveryRecord } | null>(null);
  const [record, setRecord] = useState<AuthRecoveryRecord | null>(null);
  const [reading, setReading] = useState(false);
  const [clock, setClock] = useState(() => performance.now());
  const matches = (value: AuthRecoveryRecord | null) => Boolean(value
    && sameAuthRecoveryScope(value.scope, current.current.scope)
    && value.challengeId === current.current.challengeId);
  const live = matches(record) ? record : null;
  const remainingMs = live?.deadlineMs === null || live?.deadlineMs === undefined ? 0
    : Math.max(0, live.deadlineMs - clock);
  const liveReading = reading && Boolean(readingRef.current
    && sameAuthRecoveryScope(readingRef.current.scope, scope));
  const blocked = Boolean(live && (authRecoveryNeedsRead(live.info) || remainingMs > 0)) || liveReading;

  useEffect(() => {
    if (matches(recordRef.current)) return;
    recordRef.current = null; readingRef.current = null;
    setRecord(null); setReading(false);
  }, [scope?.actorEpoch, scope?.taskId, scope?.childTaskId, scope?.confirmationId,
    scope?.planHash, scope?.expectedVersion, challengeId]);
  useEffect(() => {
    if (!live?.deadlineMs || remainingMs <= 0) return;
    const timer = setTimeout(() => setClock(performance.now()), Math.min(1000, remainingMs));
    return () => clearTimeout(timer);
  }, [live, clock, remainingMs]);

  function fail(boundScope: AuthRecoveryScope, boundChallenge: string | null,
    operation: "challenge" | "verify", info: CustomerRecovery) {
    if (!sameAuthRecoveryScope(boundScope, current.current.scope)
      || boundChallenge !== current.current.challengeId) return false;
    const now = performance.now();
    const wait = info.retryAfterMs !== undefined ? info.retryAfterMs : info.action === "WAIT" ? 1000 : null;
    const next: AuthRecoveryRecord = { scope: { ...boundScope }, challengeId: boundChallenge,
      operation, info: { ...info }, deadlineMs: wait === null ? null : now + Math.max(0, wait) };
    recordRef.current = next; setRecord(next); setClock(now);
    return true;
  }
  function canPost() {
    const value = recordRef.current;
    return !readingRef.current && (!matches(value) || Boolean(value
      && !authRecoveryNeedsRead(value.info)
      && (value.deadlineMs === null || performance.now() >= value.deadlineMs)));
  }
  function clear() {
    recordRef.current = null; readingRef.current = null;
    setRecord(null); setReading(false);
  }
  /** Called only by an explicit user action; callback must be read-only. */
  async function readLatest(read: () => Promise<boolean>, onFailure: (error: unknown) => CustomerRecovery) {
    const value = recordRef.current;
    if (!matches(value) || !value || readingRef.current
      || value.deadlineMs !== null && performance.now() < value.deadlineMs) return false;
    const ticket = { scope: value.scope, record: value };
    readingRef.current = ticket; setReading(true);
    try {
      const accepted = await read();
      if (readingRef.current !== ticket || !matches(ticket.record)) return false;
      if (!accepted) return false;
      // The authoritative trace read does not attest verification success.
      clear(); return true;
    } catch (error) {
      if (readingRef.current === ticket && matches(value)) {
        const info = onFailure(error);
        // A read failure must not unlock an unresolved auth result. Preserve the
        // original unknown/access action while showing the latest read failure.
        fail(value.scope, value.challengeId, value.operation,
          { ...value.info, message: info.message, ...(info.action === "WAIT" ? { retryAfterMs: info.retryAfterMs ?? 1000 } : {}) });
      }
      return false;
    } finally {
      if (readingRef.current === ticket) { readingRef.current = null; setReading(false); }
    }
  }
  return { record: live, reading: liveReading, blocked, remainingSeconds: Math.ceil(remainingMs / 1000),
    canRead: Boolean(live && !liveReading && remainingMs === 0), fail, canPost, clear, readLatest };
}
