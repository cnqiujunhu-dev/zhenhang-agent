/** Display receipts only. Never confer read authority or delay available data. */
export type ResourceArrivalSnapshot = {
  scopeKey: string;
  generation: number;
  state: "UNREAD" | "LOADING" | "READY" | "ERROR" | "CANCELLED";
  visible: boolean;
  count: number;
  suppressed?: boolean;
};

export class ResourceReadArrivalLedger {
  private previous: ResourceArrivalSnapshot | null = null;
  private sequence = 0;
  observe(next: ResourceArrivalSnapshot): string | null {
    const previous = this.previous;
    if (!Number.isSafeInteger(next.generation) || next.generation < 0) return null;
    if (previous?.scopeKey === next.scopeKey && next.generation < previous.generation) return null;
    this.previous = { ...next };
    if (!next.scopeKey
      || !Number.isSafeInteger(next.count) || next.count <= 0 || next.state !== "READY"
      || !next.visible || next.suppressed || !previous
      || previous.scopeKey !== next.scopeKey || previous.generation !== next.generation
      || previous.state !== "LOADING") return null;
    return `resource-arrival-${++this.sequence}`;
  }
}
