/** Local, bounded customer-journey timings. No message, money, token or account data is exported. */
export type JourneySample = {
  kind: "QUERY" | "CONSULT" | "OTHER";
  accepted_ms: number;
  first_readable_ms: number;
  complete_ms: number | null;
};
export type JourneyTicket = { id: number; epoch: number };
type Pending = {
  ticket: JourneyTicket; started: number; accepted?: number;
  turnId?: string; taskId?: string; readable?: number; complete?: number;
  kind: JourneySample["kind"]; published: boolean;
  readableKind?: JourneySample["kind"];
};
export class JourneyMetrics {
  private epoch = 0;
  private sequence = 0;
  private actor = "";
  private pending = new Map<number, Pending>();
  private samples: JourneySample[] = [];
  private counts = { submitted: 0, accepted: 0, first_readable: 0, completed_visible_reply: 0, evicted_pending: 0 };
  private clock: () => number;
  constructor(clock: () => number = () => performance.now()) { this.clock = clock; }
  forActor(actor: string) {
    if (actor === this.actor) return;
    this.actor = actor;
    this.epoch += 1;
    this.pending.clear();
    this.samples = [];
    this.counts = { submitted: 0, accepted: 0, first_readable: 0, completed_visible_reply: 0, evicted_pending: 0 };
  }
  begin(): JourneyTicket {
    const ticket = { id: ++this.sequence, epoch: this.epoch };
    this.counts.submitted += 1;
    this.pending.set(ticket.id, { ticket, started: this.clock(), kind: "OTHER", published: false });
    while (this.pending.size > 40) {
      const oldest = this.pending.keys().next().value!;
      if (!this.pending.get(oldest)?.published) this.counts.evicted_pending += 1;
      this.pending.delete(oldest);
    }
    return ticket;
  }
  accepted(ticket: JourneyTicket, turnId: string, taskId: string | undefined, kind: JourneySample["kind"]) {
    const item = this.pending.get(ticket.id);
    if (!item || ticket.epoch !== this.epoch || item.accepted !== undefined) return;
    item.accepted = this.clock(); item.turnId = turnId; item.taskId = taskId; item.kind = kind;
    this.counts.accepted += 1;
  }
  targets() {
    return [...this.pending.values()].filter(item => item.accepted !== undefined && !item.published)
      .map(item => ({ ticket: item.ticket, turnId: item.turnId!, taskId: item.taskId }));
  }
  displayed(ticket: JourneyTicket, complete: boolean, kind?: JourneySample["kind"]): JourneySample | null {
    const item = this.pending.get(ticket.id);
    if (!item || ticket.epoch !== this.epoch || item.accepted === undefined || item.published) return null;
    const now = this.clock();
    // An unclassified progress/acknowledgement is not a business reply. Never
    // retain its timestamp and later relabel that early time as a query result.
    if (!complete && (kind || item.kind) === "OTHER") return null;
    if (kind && item.readable !== undefined && item.readableKind !== kind) {
      item.readable = undefined;
      this.counts.first_readable = Math.max(0, this.counts.first_readable - 1);
    }
    if (kind) item.kind = kind;
    if (item.readable === undefined) {
      item.readable = now;
      item.readableKind = item.kind;
      this.counts.first_readable += 1;
    }
    if (!complete) return null;
    item.complete = now; item.published = true;
    this.counts.completed_visible_reply += 1;
    const duration = (point: number) => Math.round(Math.max(0, point - item.started));
    const sample: JourneySample = { kind: item.kind, accepted_ms: duration(item.accepted),
      first_readable_ms: duration(item.readable), complete_ms: duration(now) };
    this.samples.push(sample);
    if (this.samples.length > 80) this.samples.shift();
    return { ...sample };
  }
  summary() { return summarizeJourneySamples(this.samples); }
  /** Denominators include unanswered and never-visible sends; samples do not
   * represent independent users, settlement success or a production SLA. */
  coverage() {
    return { ...this.counts,
      accepted_not_yet_readable: Math.max(0, this.counts.accepted - this.counts.first_readable),
      submitted_not_yet_accepted: Math.max(0, this.counts.submitted - this.counts.accepted),
      retained_timing_samples: this.samples.length,
      active_pending: [...this.pending.values()].filter(item => !item.published).length,
      storage: "EPHEMERAL_IN_THIS_CUSTOMER_VIEW" as const,
      complete_means: "VISIBLE_REPLY_NOT_SETTLEMENT_OR_SUCCESS_RATE" as const,
    };
  }
}
export function summarizeJourneySamples(samples: JourneySample[]) {
  const percentile = (values: number[], p: number) => {
    const ordered = values.filter(Number.isFinite).sort((a, b) => a - b);
    return ordered.length ? ordered[Math.max(0, Math.ceil(ordered.length * p) - 1)] : null;
  };
  const metric = (field: keyof Omit<JourneySample, "kind">) => {
    const values = samples.flatMap(item => item[field] === null ? [] : [item[field] as number]);
    return { count: values.length, p50_ms: percentile(values, .5), p95_ms: percentile(values, .95) };
  };
  return { count: samples.length, accepted: metric("accepted_ms"), first_readable: metric("first_readable_ms"), complete: metric("complete_ms") };
}
