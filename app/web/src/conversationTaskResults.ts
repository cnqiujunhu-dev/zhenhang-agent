import type { ConversationTurn } from "./conversationTypes";
import type { TaskTrace } from "./types";
import { isQueryOnlyTask } from "./taskReply";
import type { ReadSnapshots } from "./conversationReadSnapshots";

export const HISTORY_RESULT_BATCH = 6;
export const HISTORY_RESULT_CACHE_LIMIT = 24;
export const HISTORY_RESULT_CONCURRENCY = 3;
export const HISTORY_RESULT_MAX_AGE_MS = 60_000;
export const HISTORY_RESULT_METADATA_LIMIT = 200;
const terminal = new Set(["SUCCEEDED", "FAILED", "PARTIAL", "UNKNOWN", "CLOSED"]);
let completedReadReceipts = 0;

export interface TaskResultCandidate {
  taskId: string;
  version: number;
  unavailable?: true;
}
export interface ConversationTaskResult {
  status: "LOADING" | "READY" | "NOT_QUERY" | "ERROR" | "HIDDEN" | "UNAVAILABLE";
  trace?: TaskTrace;
  error?: string;
  readAt?: number;
  /** In-memory receipt minted only after this reader validates a completed GET. */
  readReceipt?: number;
  version: number;
}
export interface ConversationTaskResultsSnapshot {
  historyReads?: ReadSnapshots;
  items: Record<string, ConversationTaskResult>;
  remaining: number;
  loading: boolean;
  permissionBlocked: boolean;
  knownQueryIds?: string[];
  recheckingAccess?: boolean;
  recheckError?: string;
  paused?: boolean;
}
export interface ConversationTaskResults extends ConversationTaskResultsSnapshot {
  retry: (taskId: string) => void | ConversationReadAttempt | Promise<ConversationReadAttempt>;
  loadMore: () => void;
  refresh: () => void;
  recheckAccess?: () => void;
}
export type ConversationReadAttempt = "STARTED" | "UNAVAILABLE" | "INTERRUPTED";

/** Historical terminal versions remain readable while a newer revision runs.
 * They are not the current revision's result; inlineReplyTurn binds placement.
 * Only task IDs already authorized by the selected conversation can be read. */
export function historyResultCandidates(turns: ConversationTurn[]): TaskResultCandidate[] {
  const found = new Map<string, TaskResultCandidate>();
  for (const turn of [...turns].sort((a, b) => b.sequence - a.sequence)) {
    const link = turn.task_link;
    if (!link || !terminal.has(link.state) || !Number.isSafeInteger(link.version) || link.version < 0
      || (found.get(link.task_id)?.version ?? -1) >= link.version) continue;
    found.set(link.task_id, { taskId: link.task_id, version: link.version });
  }
  return [...found.values()].map(candidate => {
    const linked = [...turns].reverse().filter(turn => turn.task_link?.task_id === candidate.taskId
      && !["FAILED", "QUEUED", "LEASED"].includes(turn.status)
      && Number.isSafeInteger(turn.task_link.version) && turn.task_link.version <= candidate.version);
    const owner = linked.find(turn => ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
      || ["CREATED", "CONTINUED"].includes(turn.task_link?.relationship || "")) || linked[0];
    return owner && [owner.read_result_snapshot_status, owner.reply_snapshot_status, owner.status_snapshot_status].includes("UNAVAILABLE")
      ? { ...candidate, unavailable: true } : candidate;
  });
}

type ReadTask = (taskId: string, signal: AbortSignal) => Promise<TaskTrace>;
type FailureStatus = (error: unknown) => number | undefined;
const emptySnapshot = (): ConversationTaskResultsSnapshot => ({
  items: {}, remaining: 0, loading: false, permissionBlocked: false,
});

/**
 * Bounded, in-memory history reader. One instance belongs to one login/thread.
 * Disposal and per-request generations reject even transports ignoring abort.
 * All network requests are GETs; retry never creates or confirms a bank task.
 */
export class ConversationTaskResultReader {
  private disposed = false;
  private revision = 0;
  private candidates: TaskResultCandidate[] = [];
  private requested: string[] = [];
  private discovered = new Set<string>();
  private knownQueries = new Set<string>();
  private lastFocusRevalidation = -Infinity;
  private paused = false;
  private controllers = new Map<string, AbortController>();
  private queue: string[] = [];
  private snapshot = emptySnapshot();

  constructor(
    private readonly read: ReadTask,
    private readonly changed: (snapshot: ConversationTaskResultsSnapshot) => void,
    private readonly failureStatus: FailureStatus = () => undefined,
    private readonly now: () => number = Date.now,
  ) {}

  getSnapshot() { return this.snapshot; }
  getReadGeneration() { return this.revision; }

  update(candidates: TaskResultCandidate[]) {
    if (this.disposed) return;
    const previous = new Map(this.candidates.map(item => [item.taskId, item]));
    this.candidates = candidates;
    const current = new Map(candidates.map(item => [item.taskId, item]));
    const items = { ...this.snapshot.items };
    const changed: TaskResultCandidate[] = [];
    for (const id of this.requested) {
      if (!current.has(id)) {
        this.controllers.get(id)?.abort();
        this.controllers.delete(id);
        delete items[id];
      } else if (Boolean(previous.get(id)?.unavailable) !== Boolean(current.get(id)?.unavailable)) {
        this.controllers.get(id)?.abort();
        this.controllers.delete(id);
        this.queue = this.queue.filter(item => item !== id);
        const entry = items[id], candidate = current.get(id)!;
        if (candidate.unavailable && entry?.status === "READY" && entry.trace?.state === "SUCCEEDED"
          && isQueryOnlyTask(entry.trace)) {
          items[id] = { status: "UNAVAILABLE", version: entry.version };
        } else changed.push(candidate);
      }
    }
    this.requested = this.requested.filter(id => current.has(id));
    this.discovered = new Set([...this.discovered].filter(id => current.has(id)));
    this.knownQueries = new Set([...this.knownQueries].filter(id => current.has(id)));
    this.queue = this.queue.filter(id => current.has(id));
    this.snapshot = { ...this.snapshot, items };
    for (const candidate of changed) this.enqueue(candidate);
    if (!this.requested.length) this.allocate(HISTORY_RESULT_BATCH);
    else {
      // New explicit turns enter the latest batch, without rereading all history.
      const latest = candidates.slice(0, HISTORY_RESULT_BATCH).filter(item => !this.discovered.has(item.taskId));
      for (const item of [...latest].reverse()) this.add(item, true);
      for (const id of this.requested) {
        const candidate = current.get(id)!;
        if ((this.snapshot.items[id]?.version ?? -1) < candidate.version) this.enqueue(candidate);
      }
      this.emit();
      this.pump();
    }
  }

  loadMore() {
    if (this.disposed || this.snapshot.permissionBlocked) return;
    this.allocate(HISTORY_RESULT_BATCH);
  }

  retry(taskId: string): boolean {
    if (this.disposed || this.snapshot.permissionBlocked || this.paused) return false;
    const candidate = this.candidates.find(item => item.taskId === taskId);
    if (!candidate) return false;
    if (!this.requested.includes(taskId)) this.add(candidate, true);
    else this.enqueue(candidate);
    this.emit();
    this.pump();
    return true;
  }

  /** A second-stage explanation GET may refresh an already verified result
   * within its original access lifetime. It does not clear/remount the cards.
   * Ordinary retry, version changes and fresh authorization keep hiding data. */
  refreshNarration(taskId: string): boolean {
    if (this.disposed || this.snapshot.permissionBlocked || this.paused || this.controllers.has(taskId)) return false;
    const candidate = this.candidates.find(item => item.taskId === taskId), entry = this.snapshot.items[taskId];
    const trace = entry?.trace;
    if (!candidate || !this.requested.includes(taskId) || entry?.status !== "READY" || !trace
      || entry.version !== candidate.version || trace.version !== entry.version || trace.task_id !== taskId
      || trace.state !== "SUCCEEDED" || trace.outcome?.state !== trace.state || trace.outcome.verified !== true
      || trace.pending_confirmation || !isQueryOnlyTask(trace) || !["QUEUED", "PROCESSING"].includes(trace.customer_reply_status || "")
      || typeof entry.readAt !== "number" || this.now() - entry.readAt >= HISTORY_RESULT_MAX_AGE_MS) return false;
    if (!this.queue.includes(taskId)) this.queue.push(taskId);
    this.pump();
    return true;
  }

  /** The hook may register only a candidate from a freshly verified conversation. */
  retryAuthorizedCandidate(candidate: TaskResultCandidate): boolean {
    if (this.disposed || this.snapshot.permissionBlocked || this.paused || !candidate.taskId
      || !Number.isSafeInteger(candidate.version) || candidate.version < 0) return false;
    const existing = this.candidates.find(item => item.taskId === candidate.taskId);
    if (existing && existing.version > candidate.version) return false;
    this.candidates = existing ? this.candidates.map(item => item.taskId === candidate.taskId ? candidate : item)
      : [...this.candidates, candidate];
    return this.retry(candidate.taskId);
  }

  revalidate(options: { coalesce?: boolean } = {}) {
    if (this.disposed || this.snapshot.permissionBlocked || this.paused) return;
    if (options.coalesce && this.now() - this.lastFocusRevalidation < 1000) return;
    if (options.coalesce) this.lastFocusRevalidation = this.now();
    this.revision += 1;
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
    this.queue = [];
    // Hide sensitive snapshots until their live authorization is checked again.
    this.snapshot = { ...this.snapshot, items: {} };
    for (const id of this.requested) {
      const candidate = this.candidates.find(item => item.taskId === id);
      if (candidate) this.enqueue(candidate);
    }
    this.emit();
    this.pump();
  }

  expire() {
    if (Object.values(this.snapshot.items).some(item => item.trace &&
      this.now() - (item.readAt ?? 0) >= HISTORY_RESULT_MAX_AGE_MS)) this.revalidate();
  }
  pause() {
    if(this.disposed||this.paused)return;
    this.paused=true;this.revision+=1;
    for(const controller of this.controllers.values())controller.abort();
    this.controllers.clear();this.queue=[];
    this.snapshot={...this.snapshot,items:{},loading:false,paused:true};this.emit();
  }
  resume() {
    if(this.disposed||!this.paused)return;
    this.paused=false;this.snapshot={...this.snapshot,paused:false};this.lastFocusRevalidation=-Infinity;
    this.revalidate({coalesce:true});
  }

  blockPermissions() {
    if (this.disposed) return;
    this.revision += 1;
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
    this.queue = [];
    this.snapshot = { items: {}, remaining: 0, loading: false, permissionBlocked: true };
    this.changed(this.snapshot);
  }

  /** Called only after the hook has freshly authorized the conversation GET. */
  restoreAccess(candidates: TaskResultCandidate[]) {
    if (this.disposed) return;
    this.revision += 1;
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
    this.queue = [];
    this.requested = [];
    this.discovered.clear();
    this.knownQueries.clear();
    this.snapshot = emptySnapshot();
    this.candidates = candidates;
    this.allocate(HISTORY_RESULT_BATCH);
  }

  dispose() {
    this.disposed = true;
    this.revision += 1;
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
    this.queue = [];
    this.requested = [];
    this.discovered.clear();
    this.knownQueries.clear();
    this.snapshot = emptySnapshot();
  }

  private allocate(count: number) {
    const next = this.candidates.filter(item => !this.discovered.has(item.taskId)).slice(0, count);
    for (const candidate of next) this.add(candidate);
    this.emit();
    this.pump();
  }

  private add(candidate: TaskResultCandidate, newest = false) {
    this.discovered.add(candidate.taskId);
    if (!this.requested.includes(candidate.taskId)) {
      if (newest) this.requested.unshift(candidate.taskId);
      else this.requested.push(candidate.taskId);
    }
    while (this.requested.length > HISTORY_RESULT_CACHE_LIMIT) {
      const evicted = newest ? this.requested.pop()! : this.requested.shift()!;
      this.controllers.get(evicted)?.abort();
      this.controllers.delete(evicted);
      this.queue = this.queue.filter(id => id !== evicted);
      const items = { ...this.snapshot.items };
      delete items[evicted];
      this.snapshot = { ...this.snapshot, items };
    }
    this.enqueue(candidate);
  }

  private enqueue(candidate: TaskResultCandidate) {
    if (this.snapshot.permissionBlocked || this.controllers.has(candidate.taskId)) return;
    this.snapshot = { ...this.snapshot, items: {
      ...this.snapshot.items,
      [candidate.taskId]: { status: "LOADING", version: candidate.version },
    } };
    if (!this.queue.includes(candidate.taskId)) this.queue.push(candidate.taskId);
  }

  private emit() {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot,
      remaining: this.snapshot.permissionBlocked ? 0 : this.candidates.filter(item => !this.discovered.has(item.taskId)).length,
      loading: Object.values(this.snapshot.items).some(item => item.status === "LOADING"),
      knownQueryIds: this.snapshot.permissionBlocked ? [] : [...this.knownQueries],
    };
    this.changed(this.snapshot);
  }

  private pump() {
    if (this.disposed || this.snapshot.permissionBlocked || this.paused) return;
    while (this.controllers.size < HISTORY_RESULT_CONCURRENCY && this.queue.length) {
      const id = this.queue.shift()!;
      const candidate = this.candidates.find(item => item.taskId === id);
      if (!candidate || !this.requested.includes(id)) continue;
      const controller = new AbortController();
      this.controllers.set(id, controller);
      const revision = this.revision;
      const current = () => !this.disposed && !controller.signal.aborted &&
        revision === this.revision && this.controllers.get(id) === controller && this.requested.includes(id);
      let read: Promise<TaskTrace>;
      try { read = this.read(id, controller.signal); }
      catch (error) { read = Promise.reject(error); }
      read.then(trace => {
        if (!current()) return;
        const expectedVersion = this.candidates.find(item => item.taskId === id)?.version ?? candidate.version;
        if (trace.task_id !== id || !Array.isArray(trace.nodes) || !Number.isSafeInteger(trace.version) || trace.version < expectedVersion)
          throw new Error("查询记录不完整");
        const entry: ConversationTaskResult = isQueryOnlyTask(trace) && terminal.has(trace.state)
          ? this.candidates.find(item => item.taskId === id)?.unavailable && trace.state === "SUCCEEDED"
            ? { status: "UNAVAILABLE", version: trace.version }
            : { status: "READY", trace, version: trace.version, readAt: this.now(), readReceipt: ++completedReadReceipts }
          : { status: "NOT_QUERY", version: trace.version };
        if (entry.status === "READY" || entry.status === "UNAVAILABLE") {
          this.knownQueries.delete(id);
          this.knownQueries.add(id);
          while (this.knownQueries.size > HISTORY_RESULT_METADATA_LIMIT)
            this.knownQueries.delete(this.knownQueries.values().next().value!);
        }
        this.snapshot = { ...this.snapshot, items: { ...this.snapshot.items, [id]: entry } };
      }).catch(error => {
        if (!current()) return;
        const status = this.failureStatus(error);
        if (status === 401 || status === 403) { this.blockPermissions(); return; }
        this.snapshot = { ...this.snapshot, items: { ...this.snapshot.items, [id]: {
          status: status === 404 ? "HIDDEN" : "ERROR", version: candidate.version,
          error: status === 404 ? "这条查询记录目前不可查看。" : "这条查询结果暂未读取完成，可以重新读取。",
        } } };
      }).finally(() => {
        if (this.controllers.get(id) !== controller) return;
        this.controllers.delete(id);
        if (!this.disposed) { this.emit(); this.pump(); }
      });
    }
  }
}
