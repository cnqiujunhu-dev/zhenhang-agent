import type { TraceNode } from "./types";

type RecordData = Record<string, unknown>;
type RecordParent = Pick<TraceNode, "action" | "node_id" | "operation_id" | "child_task_id" | "result">;
const relatedIdentityFields = ["sandbox_id", "user_id", "account_id", "owner_user_id", "grantee_user_id"];
let resetGeneration = 0;

function fullIdentity(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.trim() === value
    && !/[\u0000-\u001f\u007f\u2026]/.test(value);
}
function relatedIdentities(data: RecordData): [string, string][] | null {
  const values: [string, string][] = [];
  for (const field of relatedIdentityFields) {
    if (!Object.hasOwn(data, field)) continue;
    if (!fullIdentity(data[field])) return null;
    values.push([field, data[field] as string]);
  }
  return values;
}

/** React reconciliation keys only, never bank targets, permissions or DOM fields.
 * Full server IDs are local to this operation, node, list and any supplied actor
 * or ownership realm. Names, card tails, amounts and positions prove no identity.
 * If identity is incomplete or duplicated, every row gets a fresh render key:
 * conservative remounting intentionally discards uncontrolled UI state.
 */
export function queryRecordKeys(parent: RecordParent, records: readonly RecordData[], identityField: string): string[] {
  const result = parent.result || {};
  const actor = result.current_actor;
  const actorData = actor && typeof actor === "object" && !Array.isArray(actor) ? actor as RecordData : null;
  const realm = relatedIdentities(result);
  const ids = records.map(record => record[identityField]);
  const relations = records.map(relatedIdentities);
  const complete = fullIdentity(parent.operation_id) && fullIdentity(parent.node_id) && fullIdentity(parent.action)
    && (parent.child_task_id === undefined || fullIdentity(parent.child_task_id)) && realm !== null
    && (actor === undefined || actorData && fullIdentity(actorData.user_id) && fullIdentity(actorData.role))
    && ids.every(fullIdentity) && new Set(ids).size === ids.length && relations.every(value => value !== null)
    && (identityField !== "delegation_id" || records.every(record => fullIdentity(record.owner_user_id) && fullIdentity(record.grantee_user_id)));
  if (!complete) {
    const generation = ++resetGeneration;
    // Position only distinguishes rows in this one disposable render. It is
    // never reused as a stable identity across reads, roles or list reorders.
    return records.map((_record, position) => JSON.stringify(["reset-record", generation, position]));
  }
  const scope = [parent.action, parent.operation_id, parent.node_id, parent.child_task_id || null,
    realm, actorData ? [actorData.user_id, actorData.role] : null, identityField];
  return ids.map((id, position) => JSON.stringify(["server-record", scope, id, relations[position]]));
}
