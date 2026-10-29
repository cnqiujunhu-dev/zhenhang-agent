import type { ConversationTurn } from "./conversationTypes";
import type { TaskTrace } from "./types";
import type { ConversationTaskResult } from "./conversationTaskResults";
import { queryKinds } from "./taskReply.ts";
import { readSourceUnavailable } from "./conversationReadAvailability.ts";

export const READ_SNAPSHOT_LIMIT = 24;
export const READ_SNAPSHOT_CONCURRENCY = 3;
export interface ReadSnapshotCandidate { turnId: string; conversationId: string; taskId: string; version: number; }
export interface ConversationReadSnapshot extends TaskTrace {
  source_turn_id: string;
  source_conversation_id: string;
  read_only: true;
  bank_confirmation_permitted: false;
  /** Original audited plan, before projecting its READ subset. */
  source_business_kind?: "QUERY_ONLY" | "MIXED_WORKFLOW";
  read_phase?: "QUERY_RESULT" | "BEFORE_ACTION";
}
export interface ReadSnapshotEntry { status: "LOADING" | "READY" | "ERROR" | "HIDDEN"; version: number; trace?: ConversationReadSnapshot; }
export interface ReadSnapshots { items: Record<string, ReadSnapshotEntry>; permissionBlocked: boolean; loading: boolean; }
const terminal = new Set(["SUCCEEDED","FAILED","PARTIAL","UNKNOWN","CLOSED"]);
const positive = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const identifier = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 120;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);

/** Completed pure-query cards already have their ordinary GET. Only a server-
 * retained original READ in a mixed/revised task needs this separate channel. */
export function readSnapshotCandidates(turns: ConversationTurn[], regular: Record<string, ConversationTaskResult>): ReadSnapshotCandidate[] {
  const found = new Map<string, ReadSnapshotCandidate>();
  for (const turn of [...turns].sort((left,right)=>right.sequence-left.sequence)) {
    const link = turn.task_link, existing = link ? regular[link.task_id] : undefined;
    if (turn.read_result_snapshot_status !== "RETAINED" || readSourceUnavailable(turn) || turn.status !== "ANSWERED" || turn.error_code
      || !["NEW_TASK","REVISE","CLARIFY"].includes(turn.mode || "") || !link
      || !identifier(turn.turn_id) || !identifier(turn.conversation_id) || !identifier(link.task_id)
      || !positive(turn.read_result_snapshot_version) || !positive(link.version) || turn.read_result_snapshot_version > link.version
      || ["READY","LOADING","ERROR","HIDDEN"].includes(existing?.status || "")
      || !existing && terminal.has(link.state)) continue;
    found.set(turn.turn_id,{turnId:turn.turn_id,conversationId:turn.conversation_id,taskId:link.task_id,version:turn.read_result_snapshot_version});
  }
  return [...found.values()].slice(0,READ_SNAPSHOT_LIMIT);
}

/** Validate the direct readonly TaskTrace contract against the exact source
 * turn, never against a newer parent or a currently displayed confirmation. */
export function validReadSnapshot(value: unknown, candidate: ReadSnapshotCandidate): value is ConversationReadSnapshot {
  if (!object(value) || value.schema_version !== "2.0" || value.source_turn_id !== candidate.turnId
    || value.source_conversation_id !== candidate.conversationId || value.task_id !== candidate.taskId || value.version !== candidate.version
    || value.read_only !== true || value.bank_confirmation_permitted !== false || value.state !== "SUCCEEDED"
    || value.pending_confirmation !== null || !Array.isArray(value.allowed_actions) || value.allowed_actions.length !== 0
    || !Array.isArray(value.nodes) || value.nodes.length === 0 || value.nodes.length > 30
    || !object(value.outcome) || value.outcome.kind !== "QUERY_RESULT" || value.outcome.state !== "SUCCEEDED" || value.outcome.verified !== true
    || !Array.isArray(value.outcome.completed_operation_ids)
    || typeof value.result_observed_at !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.result_observed_at) || !Number.isFinite(Date.parse(value.result_observed_at))) return false;
  if ((value.source_business_kind !== undefined || value.read_phase !== undefined)
    && !(value.source_business_kind === "QUERY_ONLY" && value.read_phase === "QUERY_RESULT")
    && !(value.source_business_kind === "MIXED_WORKFLOW" && value.read_phase === "BEFORE_ACTION")) return false;
  const operationIds = new Set<string>(), nodeIds = new Set<string>();
  for (const node of value.nodes) {
    if (!object(node) || typeof node.action !== "string" || !queryKinds.has(node.action) || node.status !== "SUCCEEDED"
      || !identifier(node.node_id) || !identifier(node.operation_id) || !identifier(node.child_task_id)
      || node.error || node.can_compensate || node.compensation_task_id || !object(node.result) || node.result.error
      || operationIds.has(node.operation_id) || nodeIds.has(node.node_id)) return false;
    operationIds.add(node.operation_id);nodeIds.add(node.node_id);
  }
  if (value.outcome.completed_operation_ids.length !== operationIds.size
    || new Set(value.outcome.completed_operation_ids).size !== operationIds.size
    || !value.outcome.completed_operation_ids.every(id=>typeof id === "string" && operationIds.has(id))) return false;
  return !value.assistant_response || object(value.assistant_response)
    && value.assistant_response.read_only === true && value.assistant_response.state_kind === "QUERY_RESULT";
}

export function historicalReadForTurn(turn: ConversationTurn, entry?: ReadSnapshotEntry): ConversationReadSnapshot | null {
  const link=turn.task_link;
  if(!link||turn.status!=="ANSWERED"||turn.error_code||turn.read_result_snapshot_status!=="RETAINED"||readSourceUnavailable(turn)
    ||!positive(turn.read_result_snapshot_version)||!positive(link.version)||turn.read_result_snapshot_version>link.version
    ||entry?.status!=="READY"||entry.version!==turn.read_result_snapshot_version)return null;
  return validReadSnapshot(entry.trace,{turnId:turn.turn_id,conversationId:turn.conversation_id,taskId:link.task_id,version:turn.read_result_snapshot_version})?entry.trace:null;
}

/** A successful READ subset cannot replace a mixed task's repayment/transfer
 * receipt. Preserve unclassified old completed replies as well; no prose or
 * monetary-value matching is used to guess the original business kind. */
export function historicalReadPresentation(turn: ConversationTurn, read: ConversationReadSnapshot | null) {
  const terminalReply = ["SUCCEEDED", "UNKNOWN", "FAILED", "PARTIAL", "CLOSED"].includes(turn.task_link?.state || "")
    || ["SUCCEEDED", "UNKNOWN", "FAILED", "PARTIAL", "CLOSED"].includes(turn.reply_snapshot?.state || "");
  const mixed = read?.source_business_kind === "MIXED_WORKFLOW";
  const unclassifiedCompleted = Boolean(read && !read.source_business_kind && terminalReply);
  const preserveReply = mixed || unclassifiedCompleted;
  const unsafeState = ["UNKNOWN", "FAILED", "PARTIAL", "CLOSED"].includes(turn.task_link?.state || "")
    || ["UNKNOWN", "FAILED", "PARTIAL", "CLOSED"].includes(turn.reply_snapshot?.state || "");
  return { replaceReply: Boolean(read && !preserveReply && !unsafeState
      && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")),
    collapsed: Boolean(read && preserveReply), beforeAction: mixed };
}

const empty = (): ReadSnapshots => ({items:{},permissionBlocked:false,loading:false});
/** Bounded sensitive in-memory reader. All traffic is source-scoped GET,
 * never new actions. Disposed/paused generations reject even late transports. */
export class ConversationReadSnapshotReader {
  private snapshot = empty();
  private candidates: ReadSnapshotCandidate[] = [];
  private controllers = new Map<string,AbortController>();
  private queue: string[] = [];
  private generation = 0;
  private disposed = false;
  private paused = false;
  private readonly read: (candidate: ReadSnapshotCandidate, signal: AbortSignal)=>Promise<unknown>;
  private readonly changed: (value: ReadSnapshots)=>void;
  private readonly failureStatus: (error: unknown)=>number | undefined;
  constructor(read: (candidate: ReadSnapshotCandidate, signal: AbortSignal)=>Promise<unknown>,
    changed: (value: ReadSnapshots)=>void, failureStatus: (error: unknown)=>number | undefined = ()=>undefined) {
    this.read=read;this.changed=changed;this.failureStatus=failureStatus;
  }
  getSnapshot() {return this.snapshot;}
  update(candidates: ReadSnapshotCandidate[]) {
    if (this.disposed) return;
    const previous=new Map(this.candidates.map(row=>[row.turnId,row]));
    this.candidates=candidates.slice(0,READ_SNAPSHOT_LIMIT);
    const current=new Map(this.candidates.map(row=>[row.turnId,row]));
    const items={...this.snapshot.items};
    for (const [id,row] of Object.entries(items)) if (!current.has(id) || current.get(id)?.version !== row.version
      || current.get(id)?.taskId !== previous.get(id)?.taskId || current.get(id)?.conversationId !== previous.get(id)?.conversationId) {
      this.controllers.get(id)?.abort();this.controllers.delete(id);delete items[id];
    }
    this.queue=this.queue.filter(id=>current.has(id));this.snapshot={...this.snapshot,items};
    if (!this.paused && !this.snapshot.permissionBlocked) for (const candidate of this.candidates) if (!items[candidate.turnId]) {
      this.snapshot.items[candidate.turnId]={status:"LOADING",version:candidate.version};
      if (!this.queue.includes(candidate.turnId)) this.queue.push(candidate.turnId);
    }
    this.emit();this.pump();
  }
  revalidate() {
    if (this.disposed || this.paused || this.snapshot.permissionBlocked) return;
    this.cancel();this.snapshot=empty();this.update(this.candidates);
  }
  pause() {if(this.disposed||this.paused)return;this.paused=true;this.cancel();this.snapshot={...empty(),permissionBlocked:this.snapshot.permissionBlocked};this.emit();}
  resume() {if(this.disposed||!this.paused)return;this.paused=false;this.revalidate();}
  blockPermissions() {if(this.disposed)return;this.cancel();this.snapshot={...empty(),permissionBlocked:true};this.emit();}
  restoreAccess() {if(this.disposed)return;this.cancel();this.snapshot=empty();this.update(this.candidates);}
  dispose() {this.disposed=true;this.cancel();this.snapshot=empty();}
  private cancel() {this.generation++;for(const controller of this.controllers.values())controller.abort();this.controllers.clear();this.queue=[];}
  private emit() {if(this.disposed)return;this.snapshot={...this.snapshot,loading:Object.values(this.snapshot.items).some(row=>row.status==="LOADING")};this.changed(this.snapshot);}
  private pump() {
    if(this.disposed||this.paused||this.snapshot.permissionBlocked)return;
    while(this.controllers.size<READ_SNAPSHOT_CONCURRENCY&&this.queue.length){
      const id=this.queue.shift()!,candidate=this.candidates.find(row=>row.turnId===id);if(!candidate)continue;
      const controller=new AbortController(),generation=this.generation;this.controllers.set(id,controller);
      const current=()=>!this.disposed&&!this.paused&&!controller.signal.aborted&&generation===this.generation
        &&this.controllers.get(id)===controller&&this.candidates.some(row=>row.turnId===id&&row.version===candidate.version
          &&row.taskId===candidate.taskId&&row.conversationId===candidate.conversationId);
      Promise.resolve().then(()=>current()?this.read(candidate,controller.signal):undefined).then(value=>{
        if(!current())return;if(!validReadSnapshot(value,candidate))throw new Error("READ_SNAPSHOT_INVALID");
        this.snapshot={...this.snapshot,items:{...this.snapshot.items,[id]:{status:"READY",version:candidate.version,trace:value}}};
      }).catch(error=>{
        if(!current())return;const status=this.failureStatus(error);if(status===401||status===403){this.blockPermissions();return;}
        this.snapshot={...this.snapshot,items:{...this.snapshot.items,[id]:{status:status===404?"HIDDEN":"ERROR",version:candidate.version}}};
      }).finally(()=>{if(this.controllers.get(id)!==controller)return;this.controllers.delete(id);this.emit();this.pump();});
    }
  }
}
