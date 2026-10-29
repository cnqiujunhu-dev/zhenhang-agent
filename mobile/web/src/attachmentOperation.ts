import type { AttachmentView } from "./attachmentTypes";
export function selectedAttachmentView(
  selectedId: string,
  detail: AttachmentView | null,
  accepted: AttachmentView | null,
  denied = false,
) {
  if (denied) return null;
  const server = detail?.attachment_id === selectedId ? detail : null;
  const local = accepted?.attachment_id === selectedId ? accepted : null;
  return server && (!local || server.version >= local.version) ? server : local;
}
type AttachmentReadState = { denied: boolean; uncertain: boolean; minimumVersion: number; readReceipt: number; startedRead: number };
/** Actor-scoped read authority. POST acknowledgements never clear a read denial. */
export class AttachmentReadAuthority {
  private token = "";
  private scope = "";
  private entries = new Map<string, AttachmentReadState>();
  private observedReads = new WeakSet<AttachmentView>();
  forActor(token: string, scope: string) {
    if (token === this.token && scope === this.scope) return;
    this.token = token; this.scope = scope;
    this.entries.clear(); this.observedReads = new WeakSet();
  }
  private state(id: string) {
    let state = this.entries.get(id);
    if (!state) {
      state = { denied: false, uncertain: true, minimumVersion: 0, readReceipt: 0, startedRead: 0 };
      this.entries.set(id, state);
      while (this.entries.size > 20) {
        const disposable = [...this.entries].find(([key, entry]) => key !== id && !entry.denied);
        if (!disposable) break; // Denial tombstones survive until a new GET or actor exit.
        this.entries.delete(disposable[0]);
      }
    }
    return state;
  }
  deny(id: string, status: number, detail: AttachmentView | null = null, startedRead = 0) {
    if (!id || ![401, 403, 404, 410].includes(status)) return false;
    const state = this.state(id);
    state.denied = true; state.uncertain = true;
    state.readReceipt = Math.max(state.readReceipt, state.startedRead, startedRead);
    if (detail?.attachment_id === id) this.observedReads.add(detail);
    return true;
  }
  observe(id: string, detail: AttachmentView | null, accepted: AttachmentView | null, read: {
    errorStatus?: number; error?: string; loading?: boolean; paused?: boolean; cancelled?: boolean; readReceipt?: number; readGeneration?: number;
  }) {
    if (!id) return { denied: false, uncertain: false };
    const state = this.state(id);
    const server = detail?.attachment_id === id ? detail : null;
    const local = accepted?.attachment_id === id ? accepted : null;
    const validVersion = (view: AttachmentView | null) => view && Number.isSafeInteger(view.version) && view.version >= 1 ? view.version : 0;
    state.minimumVersion = Math.max(state.minimumVersion, validVersion(server), validVersion(local));
    if (Number.isSafeInteger(read.readGeneration) && read.readGeneration! > state.startedRead) state.startedRead = read.readGeneration!;
    if (this.deny(id, read.errorStatus || 0, server)) {
      if (Number.isSafeInteger(read.readReceipt) && read.readReceipt! > state.readReceipt) state.readReceipt = read.readReceipt!;
      return { ...state };
    }
    const settled = !read.loading && !read.paused && !read.cancelled && !read.error && !read.errorStatus;
    const freshRead = read.readReceipt === undefined ? !this.observedReads.has(server!)
      : Number.isSafeInteger(read.readReceipt) && read.readReceipt > state.readReceipt;
    if (!settled || !server || validVersion(server) < Math.max(1, state.minimumVersion)) state.uncertain = true;
    else if (freshRead) {
      // A scoped successful GET must have started after the denial fence. The
      // retained object during pending/error is not recovery evidence. Legacy
      // source-only renderers without receipts require a different GET object.
      state.denied = false; state.uncertain = false;
      if (read.readReceipt !== undefined) state.readReceipt = read.readReceipt;
      this.observedReads.add(server);
    }
    return { ...state };
  }
  denied(id: string) { return Boolean(this.entries.get(id)?.denied); }
  usable(id: string) {
    const state = this.entries.get(id);
    return Boolean(state && !state.denied && !state.uncertain);
  }
}
export interface AttachmentOperation {
  actor_token: string;
  actor_scope: string;
  actor_epoch: number;
  generation: number;
  attachment_id?: string;
}
export function attachmentOperationCurrent(
  operation: AttachmentOperation,
  current: {
    token: string;
    scope: string;
    epoch: number;
    generation: number;
    selectedId?: string;
  },
  requireSelection = false,
) {
  return (
    operation.actor_token === current.token &&
    operation.actor_scope === current.scope &&
    operation.actor_epoch === current.epoch &&
    operation.generation === current.generation &&
    (!requireSelection || operation.attachment_id === current.selectedId)
  );
}
