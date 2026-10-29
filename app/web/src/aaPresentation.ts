import type { TraceNode, TaskTrace } from "./types";

type Data = Record<string, unknown>;
const object = (value: unknown): Data | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Data : null;
const minor = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && !Object.is(value, -0);
const positive = (value: unknown): value is number => minor(value) && value > 0;
const tail = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{4}$/i.test(value);
const instant = (value: unknown): value is string => typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const label = (value: unknown): string | null => typeof value === "string" && value.trim().length > 0 && value.trim().length <= 80
  && !/[\x00-\x1f<>]/.test(value) && !/(?:zhenhang|xiaozhao)[a-f0-9]{8}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|(?:actor|sandbox|account|task|participant|request|aa-due|aa-collection)(?:_id)?\s*:/i.test(value) ? value.trim() : null;
export type Status = "pending" | "processing" | "paid" | "expired" | "cancelled" | "declined" | "review";
function status(row: Data): Status {
  if (row.status === "PAID") return row.receipt_verified === true ? "paid" : "review";
  if (row.status === "UNVERIFIED" || row.payment_task_state === "UNKNOWN") return "review";
  if (row.status === "DECLINED") return "declined";
  if (row.expired === true || row.effective_request_status === "EXPIRED" || row.request_status === "EXPIRED") return "expired";
  if (row.status === "CANCELLED" || row.request_status === "CANCELLED") return "cancelled";
  return row.status === "PAYMENT_PENDING" ? "processing" : row.status === "PENDING" ? "pending" : "review";
}
type Obligation = {person: string; memo: string; amount: number; expires: string; status: Status};
type Participant = {name: string; amount: number; status: Status};
export type Collection = {memo: string; total: number; received: number | null; remaining: number | null; known: number | null; expires: string; status: string; participants: Participant[]};
export type AAReadPresentation = {scope: string; obligations: Obligation[]; collections: Collection[]; observed: string};

/** Display-only adapter: consume current v1 BFF rows, never prose or IDs. */
export function aaReadPresentation(node: TraceNode): AAReadPresentation | null {
  const data = object(node.result), query = object(data?.query_scope);
  if (node.action !== "AA_VIEW" || node.status !== "SUCCEEDED" || node.error || !data || data.error
    || data.schema_version !== "aa-obligations-read-v1" || data.bank_state_changed !== false
    || data.authority !== "OWNED_AA_REGISTERED_FACTS_NOT_PAYMENT_AUTHORIZATION" || !instant(data.observed_at)
    || !query || Object.keys(query).some(key=>!["aa_scope","aa_note","aa_collection_ref","aa_obligation_ref"].includes(key))
    || !["ALL","PAYABLE","COLLECTIBLE","PENDING"].includes(String(query.aa_scope))
    || !Array.isArray(data.payable_obligations) || !Array.isArray(data.collections)
    || ["PAYABLE","PENDING"].includes(String(query.aa_scope)) && data.collections.length > 0 || query.aa_scope === "COLLECTIBLE" && data.payable_obligations.length > 0) return null;
  // Named and deictic follow-ups use the same owned read contract with narrower selectors.
  // Validate every returned row against that scope; selectors never authorize a payment.
  if (Object.hasOwn(query,"aa_note") && (typeof query.aa_note !== "string" || !query.aa_note.trim()
    || query.aa_note.length > 160 || [...data.payable_obligations,...data.collections].some(row=>object(row)?.memo !== query.aa_note))) return null;
  if (Object.hasOwn(query,"aa_collection_ref") && Object.hasOwn(query,"aa_obligation_ref")) return null;
  for (const [key,prefix,rows,other] of [
    ["aa_collection_ref","aa-collection:",data.collections,data.payable_obligations],
    ["aa_obligation_ref","aa-due:",data.payable_obligations,data.collections],
  ] as const) {
    if (!Object.hasOwn(query,key)) continue;
    const ref=query[key], field=key==="aa_collection_ref"?"collection_ref":"obligation_ref";
    if (typeof ref !== "string" || !ref.startsWith(prefix) || ref.length <= prefix.length || /\s/.test(ref)
      || other.length || rows.some(row=>object(row)?.[field] !== ref)) return null;
  }
  const obligations: Obligation[] = [];
  for (const value of data.payable_obligations) {
    const row = object(value), person = label(row?.collector_display_name) || label(row?.collector_username);
    if (!row || !person || !positive(row.version) || !positive(row.amount_minor) || row.currency !== "CNY" || !instant(row.expires_at)
      || !["PENDING","PAYMENT_PENDING","PAID","DECLINED","CANCELLED","UNVERIFIED"].includes(String(row.status))
      || typeof row.expired !== "boolean" || !["ACTIVE","COMPLETED","EXPIRED","CANCELLED"].includes(String(row.effective_request_status))) return null;
    obligations.push({person, memo:typeof row.memo === "string" ? row.memo.slice(0,160) : "", amount:row.amount_minor, expires:row.expires_at, status:status(row)});
  }
  const collections: Collection[] = [];
  for (const value of data.collections) {
    const collection = aaCollectionPresentation(value);
    if (!collection) return null;
    collections.push(collection);
  }
  return {scope:String(query.aa_scope), obligations, collections, observed:data.observed_at};
}

/** A current collection requires per-participant receipts, not just equal totals. */
export function aaCollectionPresentation(value: unknown): Collection | null {
    const row = object(value), recipient = object(row?.recipient);
    if (!row || row.payment_mode !== "PARTICIPANT_BANK_TRANSFER" || recipient?.currency !== "CNY" || recipient.instrument_type !== "DEBIT"
      || !positive(row.version) || !positive(row.total_minor) || !instant(row.expires_at) || !Array.isArray(row.participants) || !row.participants.length
      || !["COLLECTING","PARTIAL","COMPLETED","EXPIRED","CANCELLED","UNVERIFIED"].includes(String(row.status))
      || !["ACTIVE","COMPLETED","EXPIRED","CANCELLED"].includes(String(row.request_status))) return null;
    const participants: Participant[] = [];
    let sum = 0, paid = 0, unverified = false;
    for (const item of row.participants) {
      const person = object(item), name = label(person?.name);
      if (!person || !name || !positive(person.amount_minor) || !["PENDING","PAID","DECLINED","PAYMENT_PENDING","UNVERIFIED"].includes(String(person.status))) return null;
      const current = status({...person,request_status:row.request_status});
      sum += person.amount_minor;
      if (!Number.isSafeInteger(sum)) return null;
      if (current === "paid") paid += person.amount_minor;
      if (current === "review") unverified = true;
      participants.push({name, amount:person.amount_minor, status:current});
    }
    if (sum !== row.total_minor) return null;
    const received = row.receipt_status === "VERIFIED" && minor(row.received_minor) && row.received_minor === paid
      && minor(row.known_received_minor) && row.known_received_minor === paid && !unverified
      && row.received_minor <= row.total_minor ? row.received_minor : null;
    return {memo:typeof row.memo === "string" ? row.memo.slice(0,160) : "",total:row.total_minor, received, remaining:received === null ? null : row.total_minor - received,
      known:minor(row.known_received_minor) && row.known_received_minor === paid && row.known_received_minor <= row.total_minor ? row.known_received_minor : null,
      expires:row.expires_at, status:row.request_status === "EXPIRED" || row.request_status === "CANCELLED" ? row.request_status : String(row.status), participants};
}

export function aaRequestPresentation(trace: TaskTrace, node: TraceNode): Collection | null {
  return node.action === "AA_REQUEST" && node.status === "SUCCEEDED" && !node.error && !node.result?.error
    && trace.state === "SUCCEEDED" && Number.isSafeInteger(trace.version) && trace.version > 0 && !trace.pending_confirmation && trace.outcome?.verified === true
    && trace.outcome.kind === "BUSINESS_RECEIPT" && trace.outcome.state === trace.state
    && !!node.operation_id && trace.outcome.completed_operation_ids?.includes(node.operation_id)
    && trace.nodes.includes(node) ? aaCollectionPresentation(node.result) : null;
}

export type PayReview = {recipient: string; amount: number; source: string; target: string; memo: string};
export function aaPayReview(scope: unknown): PayReview | null {
  const data = object(scope), recipient = label(data?.recipient_username);
  if (!data || data.transfer_channel !== "INTERNAL_BANK" || data.currency !== "CNY" || !recipient
    || !positive(data.amount_minor) || !tail(data.source_card_display_last4) || !tail(data.recipient_card_display_last4)
    || typeof data.memo !== "string" || data.memo.length > 200 || /[\x00-\x1f]/.test(data.memo)
    || ["fee_minor","charged_minor","total_amount_minor"].some(key=>Object.hasOwn(data,key))) return null;
  return {recipient,amount:data.amount_minor,source:data.source_card_display_last4,target:data.recipient_card_display_last4,memo:data.memo};
}
export const aaPayReviewReady = (scope: unknown) => aaPayReview(scope) !== null;


export function aaPayReceipt(trace: TaskTrace, node: TraceNode): PayReview | null {
  const data = object(node.result), proof = object(data?.payment_confirmation), source = object(proof?.source), recipient = object(proof?.recipient);
  if (trace.state !== "SUCCEEDED" || !Number.isSafeInteger(trace.version) || trace.version < 1 || trace.pending_confirmation
    || trace.outcome?.verified !== true || trace.outcome.kind !== "BUSINESS_RECEIPT" || trace.outcome.state !== "SUCCEEDED"
    || !node.operation_id || !Array.isArray(trace.outcome.completed_operation_ids) || !trace.outcome.completed_operation_ids.includes(node.operation_id)
    || !Array.isArray(trace.nodes) || !trace.nodes.some(row=>row.node_id === node.node_id && row.operation_id === node.operation_id && row.child_task_id === node.child_task_id
      && row.action === node.action && row.status === node.status && JSON.stringify(row.result) === JSON.stringify(node.result))
    || node.action !== "AA_PAY" || node.status !== "SUCCEEDED" || node.error || !data || data.error
    || typeof data.transfer_id !== "string" || !data.transfer_id || data.transfer_channel !== "INTERNAL_BANK"
    || data.receipt_verified === false || data.settlement_verified === false || !proof || proof.currency !== "CNY"
    || !positive(proof.amount_minor) || data.amount_minor !== proof.amount_minor || data.currency !== undefined && data.currency !== "CNY"
    || source?.instrument_type !== "DEBIT" || source.currency !== "CNY" || !tail(source.display_last4)
    || recipient?.instrument_type !== "DEBIT" || recipient.currency !== "CNY" || !tail(recipient.display_last4)
    || !label(recipient.username) || recipient.username !== data.recipient_username) return null;
  return {recipient:recipient.username as string,amount:proof.amount_minor,source:source.display_last4,target:recipient.display_last4,memo:""};
}
