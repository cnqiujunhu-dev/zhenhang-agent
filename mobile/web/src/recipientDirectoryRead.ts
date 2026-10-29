import { useRef } from "react";
import { useEndpoint } from "./useEndpoint";
import { useOwnHistoryReadGate } from "./ownHistoryRead";
import { validSimulationCardNumber } from "./cardIdentity";
import { isMoneyCurrency } from "./typedMoney";
import type { RegisteredRecipient } from "./types";

export const RECIPIENT_DIRECTORY_LIMIT = 30;
export function recipientPrefix(value: string): string | null {
  const prefix = value.trim().toLowerCase();
  return /^(?:[a-z][a-z0-9_]{0,31})?$/.test(prefix) ? prefix : null;
}
export function recipientDirectoryPath(prefix: string) {
  const query = new URLSearchParams({ limit: String(RECIPIENT_DIRECTORY_LIMIT), ...(prefix ? { q: prefix } : {}) });
  return `/api/v2/transfers/recipients?${query}`;
}
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const currencies = (value: unknown) => Array.isArray(value) && value.length > 0 && value.every(isMoneyCurrency) && new Set(value).size === value.length;
export function checkedRecipientDirectory(value: unknown, prefix: string): RegisteredRecipient[] | null {
  if (!record(value) || value.scope !== "MINIMAL_REGISTERED_ACCOUNT_DIRECTORY" || typeof value.synthetic !== "boolean"
    || !Array.isArray(value.registered_accounts) || value.registered_accounts.length > RECIPIENT_DIRECTORY_LIMIT) return null;
  const names = new Set<string>();
  for (const row of value.registered_accounts) {
    if (!record(row) || typeof row.username !== "string" || !/^[a-z][a-z0-9_]{2,31}$/.test(row.username)
      || !row.username.startsWith(prefix) || names.has(row.username) || !text(row.account_reference)
      || row.transfer_channel !== "INTERNAL_BANK" || !currencies(row.accepted_currencies)
      || row.display_name !== undefined && row.display_name !== null && typeof row.display_name !== "string"
      || row.account_last4 !== undefined && row.account_last4 !== null && !text(row.account_last4)
      || !Array.isArray(row.available_receiving_cards)
      || row.primary_card_number !== null && !validSimulationCardNumber(row.primary_card_number)) return null;
    const cards = new Set<string>();
    for (const card of row.available_receiving_cards) {
      if (!record(card) || !validSimulationCardNumber(card.card_number) || cards.has(card.card_number)
        || typeof card.display_last4 !== "string" || !/^[0-9a-f]{4}$/i.test(card.display_last4)
        || card.display_last4.toLowerCase() !== card.card_number.slice(-4).toLowerCase() || !currencies(card.accepted_currencies)) return null;
      cards.add(card.card_number);
    }
    if (row.primary_card_number !== null && !cards.has(row.primary_card_number as string)) return null;
    names.add(row.username);
  }
  return value.registered_accounts as unknown as RegisteredRecipient[];
}

/** Reuse the existing GET transport/freshness fence; query epochs prevent A-B-A cache reuse. */
export function useRecipientDirectoryRead(token: string, revision: number, prefix: string, requestKey: number) {
  const context = JSON.stringify([token, revision, prefix, requestKey]);
  const bound = useRef({ context, epoch: 0 });
  if (bound.current.context !== context) bound.current = { context, epoch: bound.current.epoch + 1 };
  const endpoint = useEndpoint<unknown>(recipientDirectoryPath(prefix), token, bound.current.epoch);
  const read = useOwnHistoryReadGate(endpoint, token, bound.current.epoch);
  const rows = read.current ? checkedRecipientDirectory(endpoint.data, prefix) : null;
  return { endpoint, read, rows };
}
