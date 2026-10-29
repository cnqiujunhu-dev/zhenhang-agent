import { useRef, useState } from "react";
import { useReadVisibility } from "./useReadVisibility.ts";
import { isMoneyCurrency, type MoneyCurrency } from "./typedMoney.ts";
import type { OwnTransferReceipt } from "./types";

type Direction = "INCOMING" | "OUTGOING";
export type OwnTransferHistory = { scope: "OWN_TRANSFER_RECEIPTS"; synthetic: boolean;
  counterparty_balances_disclosed: false; counterparty_tasks_disclosed: false;
  receipts: (OwnTransferReceipt & { currency: MoneyCurrency; direction: Direction })[] };
export type OwnFxStatement = { statement_id: string; operation_id: string; kind: "INITIAL_FUNDING" | "TRANSFER" | "EXCHANGE";
  account_ref: string; currency: MoneyCurrency; amount_minor: number; direction: Direction;
  merchant: string; occurred_at: string; operation_ref: string };
export type OwnFxHistory = { scope: "OWN_FX_STATEMENTS"; synthetic: boolean;
  counterparty_balances_disclosed: false; counterparty_tasks_disclosed: false; statements: OwnFxStatement[] };
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const identifier = (value: unknown): value is string => text(value) && value === value.trim() && value.length <= 256;
const minor = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value);
const direction = (value: unknown): value is Direction => value === "INCOMING" || value === "OUTGOING";

// Transfer's SQLite DTO may omit an offset; FX uses as_utc(...).isoformat().
function historyDate(value: unknown, naiveAllowed: boolean) {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})?$/.exec(value);
  if (!match || !naiveAllowed && !match[7]) return false;
  const [, year, month, day, hour, minute, second] = match;
  const days = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  return Number(year) > 0 && Number(month) >= 1 && Number(month) <= 12 && Number(day) >= 1 && Number(day) <= days
    && Number(hour) < 24 && Number(minute) < 60 && Number(second) < 60 && Number.isFinite(Date.parse(value));
}
function envelope(value: unknown, scope: string, key: string): value is Record<string, unknown> {
  return record(value) && value.scope === scope && typeof value.synthetic === "boolean"
    && value.counterparty_balances_disclosed === false && value.counterparty_tasks_disclosed === false && Array.isArray(value[key]);
}

/** Transfer receipts carry positive absolute money; direction supplies the display sign. */
export function checkedOwnTransferHistory(value: unknown): OwnTransferHistory | null {
  if (!envelope(value, "OWN_TRANSFER_RECEIPTS", "receipts")) return null;
  const identities = new Set<string>();
  const transferKinds = new Map<string, boolean>();
  for (const row of value.receipts as unknown[]) {
    if (!record(row) || !identifier(row.transfer_id) || !direction(row.direction) || !isMoneyCurrency(row.currency)
      || !minor(row.amount_minor) || row.amount_minor <= 0 || !text(row.counterparty) || typeof row.memo !== "string"
      || !historyDate(row.created_at, true) || typeof row.settlement_verified !== "boolean" || row.status !== "POSTED"
      || !["INTERNAL_BANK", "EXTERNAL_SIM", "INTERNAL_BANK_FX", "FX_SIM"].includes(String(row.transfer_channel))) return null;
    if (["INTERNAL_BANK", "EXTERNAL_SIM"].includes(String(row.transfer_channel)) && row.currency !== "CNY"
      || row.transfer_channel === "EXTERNAL_SIM" && row.direction !== "OUTGOING") return null;
    const requested = row.requested_currency !== undefined || row.requested_amount_minor !== undefined;
    const fx = row.transfer_channel === "INTERNAL_BANK_FX" || row.transfer_channel === "FX_SIM";
    if ((requested || fx) && (!isMoneyCurrency(row.requested_currency) || !minor(row.requested_amount_minor) || row.requested_amount_minor <= 0)) return null;
    const key = JSON.stringify([row.transfer_id, row.direction, row.currency]);
    if (identities.has(key) || transferKinds.has(row.transfer_id) && (!fx || transferKinds.get(row.transfer_id) !== true)) return null;
    identities.add(key);
    transferKinds.set(row.transfer_id, fx);
  }
  return value as unknown as OwnTransferHistory;
}

/** FX statement amounts are signed deltas; sign and declared direction must agree. */
export function checkedOwnFxHistory(value: unknown): OwnFxHistory | null {
  if (!envelope(value, "OWN_FX_STATEMENTS", "statements")) return null;
  const identities = new Set<string>();
  for (const row of value.statements as unknown[]) {
    if (!record(row) || !identifier(row.statement_id) || identities.has(row.statement_id) || !identifier(row.operation_id)
      || !identifier(row.account_ref) || !isMoneyCurrency(row.currency) || !direction(row.direction)
      || !minor(row.amount_minor) || row.amount_minor === 0 || row.direction === "INCOMING" !== (row.amount_minor > 0)
      || !text(row.merchant) || !historyDate(row.occurred_at, false) || row.operation_ref !== `fx:${row.operation_id}:${row.currency}`
      || !["INITIAL_FUNDING", "TRANSFER", "EXCHANGE"].includes(String(row.kind))) return null;
    if (row.kind === "INITIAL_FUNDING" && (row.currency !== "USD" || row.direction !== "INCOMING")) return null;
    identities.add(row.statement_id);
  }
  return value as unknown as OwnFxHistory;
}

type EndpointRead = { data: unknown; loading: boolean; error: string; cancelled: boolean; paused: boolean;
  readReceipt: number; readGeneration: number; getReadGeneration: () => number };
/** A small consumer fence for this pair of panels; transport remains useEndpoint. */
export function useOwnHistoryReadGate(endpoint: EndpointRead, token: string, revision: number, active = true) {
  const visibility = useReadVisibility();
  const key = JSON.stringify([token, revision, visibility.epoch, active]);
  const observed = Math.max(endpoint.readReceipt, endpoint.readGeneration);
  const bound = useRef({ key, floor: 0, seen: observed, scopeEpoch: 0 });
  const [, paint] = useState(0);
  if (bound.current.key !== key) bound.current = { key, floor: Math.max(bound.current.seen, observed),
    seen: Math.max(bound.current.seen, observed), scopeEpoch: bound.current.scopeEpoch + 1 };
  bound.current.seen = Math.max(bound.current.seen, observed);
  const scopeEpoch = bound.current.scopeEpoch;
  const current = Boolean(active && token && visibility.visible && !endpoint.paused && !endpoint.loading && !endpoint.error && !endpoint.cancelled
    && endpoint.readReceipt > bound.current.floor && endpoint.readReceipt === endpoint.readGeneration);
  return { current, visible: visibility.visible, scopeKey: JSON.stringify([key, scopeEpoch]),
    invalidate() {
      if (bound.current.key !== key || bound.current.scopeEpoch !== scopeEpoch || !active || !token || !visibility.visible || document.visibilityState !== "visible") return false;
      bound.current.floor = Math.max(bound.current.seen, endpoint.readReceipt, endpoint.getReadGeneration());
      paint(value => value + 1); return true;
    } };
}
