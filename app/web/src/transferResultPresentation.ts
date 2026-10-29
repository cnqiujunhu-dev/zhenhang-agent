import type { JsonValue, TraceNode } from "./types";

export interface CompactTransferResult {
  status: "SUCCESS" | "FAILED" | "UNKNOWN";
  recipientName: string | null;
  recipientCardNumber: string | null;
  memo: string | null;
}

const value = (item: JsonValue | undefined): string | null =>
  typeof item === "string" && item.trim() ? item.trim() : null;
const object = (item: JsonValue | undefined): Record<string, JsonValue> =>
  item !== null && typeof item === "object" && !Array.isArray(item) ? item : {};
const cardNumber = (item: JsonValue | undefined): string | null => {
  const text = value(item);
  // Only a recorded card number, never an account ID, reference or suffix.
  return text && (/^(?:zhenhang|xiaozhao)[0-9a-f]{8}$/i.test(text)
    || /^\d{12,19}$/.test(text.replace(/[ -]/g, ""))) ? text : null;
};
const internalReference = (text: string) => /^(?:ACCT[-:]|debit:|credit:)/i.test(text)
  || /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(text) || cardNumber(text) !== null;

/** Display-only projection of an actual TRANSFER node. No message parsing,
 * directory lookup, account fallback, result mutation or execution authority. */
export function compactTransferResult(node: TraceNode): CompactTransferResult | null {
  if (node.action !== "TRANSFER" || !["SUCCEEDED", "FAILED", "UNKNOWN"].includes(node.status)) return null;
  const data = node.result || {};
  const resultStatus = value(data.status)?.toUpperCase();
  const transferId = value(data.transfer_id);
  const positiveAmount = typeof data.amount_minor === "number" && Number.isSafeInteger(data.amount_minor) && data.amount_minor > 0;
  const resultFailure = ["FAILED", "REJECTED", "DECLINED"].includes(resultStatus || "");
  const resultUncertain = resultStatus !== undefined && !["POSTED", "SUCCESS", "SUCCEEDED", "COMPLETED"].includes(resultStatus || "");
  const error = Boolean(node.error || data.error);
  const posted = node.status === "SUCCEEDED" && Boolean(transferId) && positiveAmount && !error
    && !resultUncertain && (data.preview === undefined || data.preview === null || data.preview === false)
    && data.read_only !== true && data.bank_state_changed !== false;
  const failed = !transferId && (node.status === "FAILED" || resultFailure);
  const status = posted ? "SUCCESS" : failed ? "FAILED" : "UNKNOWN";
  const recipient = object(data.recipient_context);
  const username = value(data.recipient_username) || value(recipient.recipient_username);
  const names = [value(data.recipient_display_name), value(data.payee_name)];
  const recipientName = names.find(name => name !== null && name !== username && !internalReference(name)) || null;
  const recordedCards = [cardNumber(data.recipient_card_number), cardNumber(recipient.recipient_card_number)]
    .filter((number): number is string => number !== null);
  // A generic card_number is accepted only when no explicit recipient number
  // exists. Never let a source-card field replace the receiving card.
  const uniqueCards = [...new Set(recordedCards)];
  const recipientCardNumber = uniqueCards.length === 1 ? uniqueCards[0]
    : uniqueCards.length > 1 ? null : cardNumber(data.card_number);
  // Missing historical data is unknown, not proof that the original note was
  // empty. Explicit null/blank means no note; the component labels that "无".
  const memo = data.memo === undefined ? null
    : typeof data.memo === "string" ? data.memo.trim() : data.memo === null ? "" : null;
  return {status, recipientName, recipientCardNumber, memo};
}

/** The parent still must enforce verified, single-result, completed outcome
 * ownership before suppressing its own headings or narrative. */
export function recordedCompactTransfer(node: TraceNode): boolean {
  return compactTransferResult(node)?.status === "SUCCESS";
}
