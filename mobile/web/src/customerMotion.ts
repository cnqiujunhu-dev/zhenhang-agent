/** Ephemeral presentation receipts. No banking values or authorization live here. */
export type CustomerMotionSnapshot = {
  scopeKey: string;
  turnId: string;
  taskId?: string | null;
  version?: number;
  state?: string | null;
  status?: string | null;
  visible: boolean;
  contentKey: string;
  urgent?: boolean;
};
export type CustomerMotionRequest = {
  scopeKey: string;
  requestId: string;
  turnId: string;
  startedAtMs: number;
};
export type CustomerMotionEvent = {
  id: string;
  scopeKey: string;
  turnId: string;
  taskId: string | null;
  version: number;
  kind: "reveal" | "update";
  durationMs: 200;
  staggerMs: 40;
  maxDelayMs: 240;
};
export type CustomerMotionInput = {
  previous: CustomerMotionSnapshot | null;
  next: CustomerMotionSnapshot | null;
  liveRequest?: CustomerMotionRequest | null;
  allowMotion: boolean;
  reducedMotion: boolean;
  forcedColors?: boolean;
  hidden?: boolean;
  blocked?: boolean;
  rereading?: boolean;
  sensitiveEditing?: boolean;
  nowMs: number;
};
export const CUSTOMER_MOTION_MAX_RECEIPTS = 64;
export const CUSTOMER_MOTION_FRESH_REQUEST_MS = 120_000;
const states = new Set(["QUEUED", "UNDERSTANDING", "PLANNING", "WAITING", "PLANNED", "POLICY_CHECKED",
  "AWAITING_CONFIRMATION", "NEED_CLARIFICATION", "AUTHORIZED", "EXECUTING", "COMPENSATING",
  "SUCCEEDED", "PARTIAL", "FAILED", "CLOSED", "UNKNOWN"]);
const urgentStates = new Set(["UNKNOWN", "FAILED", "PARTIAL", "CLOSED", "CANCELLED", "CANCELED", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION"]);

/** Bounded per-mounted-conversation ledger, never stored or shared between users. */
export class CustomerMotionLedger {
  private receipts = new Map<string, "ARMED" | "CONSUMED">();
  private sequence = 0;
  get size() { return this.receipts.size; }
  has(key: string) { return this.receipts.get(key) === "CONSUMED"; }
  armed(key: string) { return this.receipts.get(key) === "ARMED"; }
  arm(key: string) {
    if (!this.receipts.has(key)) this.receipts.set(key, "ARMED");
    this.trim();
  }
  private trim() {
    while (this.receipts.size > CUSTOMER_MOTION_MAX_RECEIPTS) this.receipts.delete(this.receipts.keys().next().value!);
  }
  claim(key: string) {
    if (this.has(key)) return null;
    this.receipts.set(key, "CONSUMED");
    this.trim();
    return `customer-motion-${++this.sequence}`;
  }
}

function version(snapshot: CustomerMotionSnapshot) {
  return typeof snapshot.version === "number" && Number.isSafeInteger(snapshot.version) && snapshot.version >= 0 ? snapshot.version : 0;
}
export function customerMotionIdentity(snapshot: CustomerMotionSnapshot, request?: CustomerMotionRequest | null) {
  // Opaque identity only: never summaries, money, confirmation IDs or raw text.
  return JSON.stringify([snapshot.scopeKey, request?.scopeKey === snapshot.scopeKey && request.turnId === snapshot.turnId ? request.requestId : "observed",
    snapshot.turnId, snapshot.taskId || "", version(snapshot), snapshot.state || "", snapshot.status || "", snapshot.contentKey]);
}
export function customerMotionUrgent(snapshot?: CustomerMotionSnapshot | null) {
  return Boolean(snapshot?.urgent || snapshot?.status === "FAILED" || urgentStates.has(snapshot?.state || ""));
}
export function customerMotionDelay(layer: number) {
  return Number.isFinite(layer) ? Math.min(240, Math.max(0, Math.floor(layer)) * 40) : 0;
}

/** Decide once at the visible edge, not when a hidden result first reaches memory. */
export function decideCustomerMotion(input: CustomerMotionInput, ledger: CustomerMotionLedger): CustomerMotionEvent | null {
  const { previous, next, liveRequest, nowMs } = input;
  if (!next?.scopeKey || !next.turnId || !next.contentKey || !Number.isFinite(nowMs)) return null;
  const sameTurn = Boolean(previous && previous.scopeKey === next.scopeKey && previous.turnId === next.turnId);
  const sameBinding = Boolean(sameTurn && previous && (previous.taskId || "") === (next.taskId || ""));
  if (previous && previous.scopeKey === next.scopeKey && previous.turnId === next.turnId
    && previous.taskId && next.taskId && previous.taskId !== next.taskId) return null;
  if (sameBinding && previous && version(next) < version(previous)) return null;
  const freshRequest = Boolean(liveRequest && liveRequest.requestId && liveRequest.scopeKey === next.scopeKey
    && liveRequest.turnId === next.turnId && Number.isFinite(liveRequest.startedAtMs)
    && nowMs >= liveRequest.startedAtMs && nowMs - liveRequest.startedAtMs <= CUSTOMER_MOTION_FRESH_REQUEST_MS);
  const completedMessage = Boolean(sameTurn && previous && ["QUEUED", "LEASED"].includes(previous.status || "")
    && ["ANSWERED", "FAILED"].includes(next.status || ""));
  const stateChanged = Boolean(sameBinding && previous && previous.state && next.state && previous.state !== next.state
    && states.has(previous.state) && states.has(next.state));
  const becameVisible = Boolean(sameBinding && previous && !previous.visible && next.visible);
  const verificationResolved = Boolean(sameBinding && previous?.urgent && !next.urgent
    && previous.contentKey !== next.contentKey && version(next) >= version(previous));
  const identity = customerMotionIdentity(next, liveRequest);
  const requestIdentity = freshRequest ? JSON.stringify(["request", next.scopeKey, liveRequest!.requestId, next.turnId]) : "";
  const newRequest = freshRequest && !ledger.has(requestIdentity);
  const candidate = newRequest || completedMessage || stateChanged || verificationResolved || becameVisible && ledger.armed(identity);
  if (!candidate) return null; // Mounting history, language changes and version ticks are not new replies.
  if (ledger.has(identity)) return null;
  const suppressed = !input.allowMotion || input.reducedMotion || input.forcedColors || input.hidden || input.blocked || input.rereading || input.sensitiveEditing;
  if (customerMotionUrgent(next) || suppressed) {
    // Consuming a suppressed edge prevents a delayed replay after the person resumes reading/editing.
    if (next.visible || customerMotionUrgent(next)) {
      ledger.claim(identity);
      if (requestIdentity) ledger.claim(requestIdentity);
    }
    return null;
  }
  if (!next.visible) {
    ledger.arm(identity); // A query hold retains this exact result edge without using its visible receipt.
    return null;
  }
  const id = ledger.claim(identity);
  if (!id) return null;
  if (requestIdentity) ledger.claim(requestIdentity);
  return { id, scopeKey: next.scopeKey, turnId: next.turnId, taskId: next.taskId || null, version: version(next),
    kind: becameVisible || freshRequest && !previous?.visible ? "reveal" : "update", durationMs: 200, staggerMs: 40, maxDelayMs: 240 };
}
