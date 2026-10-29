import type { BillAnalysis, TaskTrace, TraceNode } from "./types";
import { queryKinds } from "./taskReply.ts";

const reads = queryKinds;
export const isReadAction = (action: string) => reads.has(action);
const fields = ["schema_version", "task_id", "task_version", "node_id", "operation_id", "child_task_id",
  "action", "status", "result", "analysis", "observed_at"];
type Data = Record<string, unknown>;
const object = (value: unknown): value is Data => value !== null && typeof value === "object" && !Array.isArray(value);
const id = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 120;
const positive = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
function sameJson(left: unknown, right: unknown, depth = 0): boolean {
  if (depth > 25) return false;
  if (left === right) return typeof left !== "number" || Number.isFinite(left);
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length && left.every((item, index) => sameJson(item, right[index], depth + 1));
  if (!object(left) || !object(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && sameJson(left[key], right[key], depth + 1));
}
function recordedRead(action: string, result: Data, analysis: unknown): boolean {
  if (result.error) return false;
  if (action === "BILL_ANALYSIS") return object(analysis) && typeof analysis.transaction_count === "number"
    && Number.isSafeInteger(analysis.transaction_count) && analysis.transaction_count >= 0;
  const key: Record<string, string> = { CARD_STATUS: "cards", SUBSCRIPTION_LIST: "subscriptions", WEALTH_COMPARE: "products",
    FAMILY_VIEW: "delegations", GOAL_VIEW: "goals", CARBON_VIEW: "events", ASSET_POSITIONS_READ: "positions",
    BENEFITS_VIEW: "activities", PREFERENCE_VIEW: "preferences" };
  if (action === "OCCASION_MEMBER_VIEW") return result.schema_version === "family-profile-v1" && result.bank_state_changed === false
    && result.account_access_granted === false && Array.isArray(result.members) && result.members.every(object);
  if (action === "MEMBERSHIP_VIEW") return object(result.membership) && Array.isArray(result.cards);
  if (key[action]) return Array.isArray(result[key[action]]) && (result[key[action]] as unknown[]).every(object);
  if (action === "FRAUD_ANALYZE") return object(result.risk) && ["LOW", "HIGH", "UNKNOWN"].includes(String(result.risk.risk_level));
  return action === "FX_QUOTE" && object(result.market_rate);
}
export type CompletedRead = { node: TraceNode; analysis: BillAnalysis | null; observedAt: string };

/** Subset display authority from the owned BFF; it never upgrades the parent. */
export function completedReadsForPending(trace: TaskTrace): CompletedRead[] {
  const facts = (trace as TaskTrace & { completed_read_results?: unknown }).completed_read_results;
  const pending = trace.pending_confirmation;
  if (trace.state !== "AWAITING_CONFIRMATION" || !positive(trace.version) || !Array.isArray(facts)
    || !pending || !id(pending.task_id) || !id(pending.confirmation_id) || !/^[0-9a-f]{64}$/.test(pending.plan_hash)
    || !positive(pending.expected_task_version) || trace.outcome?.state !== trace.state || trace.outcome.verified !== false
    || !Array.isArray(trace.outcome.completed_operation_ids)
    || !trace.nodes.some(node => node.child_task_id === pending.task_id && node.status === "WAITING_CONFIRMATION" && !reads.has(node.action))) return [];
  const selected: CompletedRead[] = [], used = new Set<string>();
  for (const fact of facts.slice(0, 30)) {
    if (!object(fact) || Object.keys(fact).length !== fields.length || !fields.every(key => Object.hasOwn(fact, key))
      || fact.schema_version !== "completed-read-result-v1" || fact.task_id !== trace.task_id || fact.task_version !== trace.version
      || !positive(fact.task_version) || fact.status !== "SUCCEEDED" || typeof fact.action !== "string" || !reads.has(fact.action)
      || ![fact.task_id, fact.node_id, fact.operation_id, fact.child_task_id].every(id)
      || !trace.outcome.completed_operation_ids.includes(fact.operation_id as string) || used.has(fact.operation_id as string)
      || !object(fact.result) || typeof fact.observed_at !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(fact.observed_at)
      || !Number.isFinite(Date.parse(fact.observed_at))) continue;
    const matches = trace.nodes.filter(node => node.node_id === fact.node_id);
    if (matches.length !== 1) continue;
    const node = matches[0];
    if (node.operation_id !== fact.operation_id || node.child_task_id !== fact.child_task_id || node.action !== fact.action
      || node.status !== fact.status || !sameJson(node.result, fact.result)
      || (node.action === "BILL_ANALYSIS" ? !object(fact.analysis) || !sameJson(trace.analysis, fact.analysis) : fact.analysis !== null)
      || !recordedRead(node.action, fact.result, fact.analysis)) continue;
    selected.push({ node, analysis: fact.analysis as BillAnalysis | null, observedAt: fact.observed_at });
    used.add(fact.operation_id as string);
  }
  return selected;
}

/** Successful multi-BILL display is bound per child, never to the first bill. */
export function completedBillResults(trace: TaskTrace): CompletedRead[] {
  const bills = trace.nodes.filter(node => node.action === "BILL_ANALYSIS");
  const facts = (trace as TaskTrace & { completed_read_results?: unknown }).completed_read_results;
  if (bills.length < 2 || trace.state !== "SUCCEEDED" || trace.pending_confirmation || !positive(trace.version)
    || trace.outcome?.state !== "SUCCEEDED" || trace.outcome.verified !== true
    || !Array.isArray(trace.outcome.completed_operation_ids) || !Array.isArray(facts)) return [];
  const selected: CompletedRead[] = [];
  for (const node of bills) {
    if (node.status !== "SUCCEEDED" || ![node.node_id,node.operation_id,node.child_task_id].every(id)
      || trace.nodes.filter(other => other.node_id === node.node_id || other.operation_id === node.operation_id
        || other.child_task_id === node.child_task_id).length !== 1
      || trace.outcome.completed_operation_ids.filter(operation => operation === node.operation_id).length !== 1) return [];
    const matches = facts.filter(fact => object(fact) && fact.operation_id === node.operation_id);
    if (matches.length !== 1) return [];
    const fact = matches[0];
    if (!object(fact) || Object.keys(fact).length !== fields.length || !fields.every(key => Object.hasOwn(fact,key))
      || fact.schema_version !== "completed-read-result-v1" || fact.task_id !== trace.task_id
      || fact.task_version !== trace.version || !positive(fact.task_version) || fact.action !== "BILL_ANALYSIS"
      || ["node_id","operation_id","child_task_id","action","status"].some(key => fact[key] !== (node as unknown as Data)[key])
      || !object(fact.result) || !sameJson(node.result,fact.result) || !recordedRead("BILL_ANALYSIS",fact.result,fact.analysis)
      || typeof fact.observed_at !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(fact.observed_at)
      || !Number.isFinite(Date.parse(fact.observed_at))) return [];
    selected.push({node,analysis:fact.analysis as BillAnalysis,observedAt:fact.observed_at});
  }
  return selected;
}
