export type RecoveryDelayBinding = {
  actorEpoch: number;
  taskId: string;
  operationId: string;
};
export type RecoveryDelay = RecoveryDelayBinding & {
  startedAtMs: number;
  deadlineMs: number;
};

function validBinding(binding: RecoveryDelayBinding | null | undefined): binding is RecoveryDelayBinding {
  return Boolean(binding) && Number.isSafeInteger(binding!.actorEpoch) && binding!.actorEpoch >= 0
    && typeof binding!.taskId === "string" && binding!.taskId.length > 0 && binding!.taskId.length <= 200
    && typeof binding!.operationId === "string" && binding!.operationId.length > 0 && binding!.operationId.length <= 200;
}

/** Pure in-memory metadata; no credentials, payloads, callbacks or automatic retry. */
export function beginRecoveryDelay(
  binding: RecoveryDelayBinding,
  retryAfterMs: number | undefined,
  nowMs: number,
): RecoveryDelay | null {
  if (!validBinding(binding) || !Number.isFinite(nowMs) || nowMs < 0
    || nowMs > Number.MAX_SAFE_INTEGER - 60_000
    || typeof retryAfterMs !== "number" || !Number.isFinite(retryAfterMs) || retryAfterMs < 0) return null;
  return {
    actorEpoch: binding.actorEpoch,
    taskId: binding.taskId,
    operationId: binding.operationId,
    startedAtMs: nowMs,
    deadlineMs: nowMs + Math.min(60_000, retryAfterMs),
  };
}

/** Expiry enables only an explicit read/check action for this exact binding. */
export function recoveryDelayState(
  delay: RecoveryDelay | null,
  current: RecoveryDelayBinding,
  nowMs: number,
) {
  const validTime = Number.isFinite(nowMs) && nowMs >= 0;
  const matched = validBinding(current) && validTime && Boolean(delay
    && delay.actorEpoch === current.actorEpoch && delay.taskId === current.taskId
    && delay.operationId === current.operationId && Number.isFinite(delay.startedAtMs)
    && delay.startedAtMs >= 0 && Number.isFinite(delay.deadlineMs)
    && delay.deadlineMs <= Number.MAX_SAFE_INTEGER && delay.deadlineMs >= delay.startedAtMs
    && delay.deadlineMs - delay.startedAtMs <= 60_000.001);
  if (!matched || !delay) return { current: false, remainingMs: 0, remainingSeconds: 0, canRead: false };
  const remainingMs = Math.min(60_000, Math.max(0, delay.deadlineMs - Math.max(delay.startedAtMs, nowMs)));
  return { current: true, remainingMs, remainingSeconds: Math.ceil(remainingMs / 1000), canRead: remainingMs === 0 };
}
