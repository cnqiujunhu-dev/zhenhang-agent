import type { TaskTrace } from "./types";

export type BankInteractionTicket = { epoch: number; sequence: number; taskId: string };
/** Synchronous UI single-flight; server permissions and idempotency remain authoritative. */
export class BankInteractionGate {
  private actor = "";
  private epoch = 0;
  private sequence = 0;
  private active: BankInteractionTicket | null = null;
  get epochId() { return this.epoch; }
  forActor(actor: string) {
    if (this.actor === actor) return;
    this.actor = actor; this.epoch += 1; this.active = null;
  }
  start(taskId: string): BankInteractionTicket | null {
    if (this.active || !this.actor || !taskId) return null;
    this.active = { epoch: this.epoch, sequence: ++this.sequence, taskId };
    return this.active;
  }
  current(ticket: BankInteractionTicket, currentTaskId: string) {
    return this.active === ticket && ticket.epoch === this.epoch && ticket.taskId === currentTaskId;
  }
  finish(ticket: BankInteractionTicket) {
    if (this.active !== ticket || ticket.epoch !== this.epoch) return false;
    this.active = null; return true;
  }
}

export function canApplyTaskTrace(incoming: TaskTrace, expectedId: string, current: TaskTrace | null) {
  return incoming?.task_id === expectedId && Number.isSafeInteger(incoming.version) && incoming.version >= 1
    && Array.isArray(incoming.nodes) && Array.isArray(incoming.allowed_actions)
    && (!current || current.task_id !== expectedId || incoming.version >= current.version);
}

export function verificationConfirmed(result: unknown, challengeId: string) {
  if (!result || typeof result !== "object") return false;
  const value = result as { verified?: unknown; challenge_id?: unknown };
  return value.verified === true && (value.challenge_id === undefined || value.challenge_id === challengeId);
}
