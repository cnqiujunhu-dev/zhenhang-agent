export function nextReadPollDelay(state: {
  visible: boolean; inFlight?: boolean; cancelled?: boolean; terminal?: boolean; failures?: number;
}, baseMs = 1000): number | null {
  if (!state.visible || state.inFlight || state.cancelled || state.terminal) return null;
  return Math.min(30000, Math.max(250, baseMs) * 2 ** Math.min(5, Math.max(0, state.failures || 0)));
}

/** Pausing is temporary; an explicit read cancellation requires a user restart. */
export class ReadPollGate {
  private visible: boolean;
  private cancelled = false;
  constructor(visible = true) { this.visible = visible; }
  setVisible(visible: boolean): boolean {
    const resume = visible && !this.visible && !this.cancelled;
    this.visible = visible;
    return resume;
  }
  cancel() { this.cancelled = true; }
  reset() { this.cancelled = false; }
  state() { return { visible: this.visible, cancelled: this.cancelled, paused: !this.visible && !this.cancelled }; }
}
