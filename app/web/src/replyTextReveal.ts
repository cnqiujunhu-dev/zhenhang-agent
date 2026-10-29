import type { QueryRevealRequest } from "./queryReveal";

export const REPLY_TEXT_MAX_MS = 1800;
export const REPLY_TEXT_REQUEST_MAX_AGE_MS = 120000;

/** Local presentation receipts only. No account values or reply text are retained. */
export class ReplyTextRevealLedger {
  private readonly consumed = new Set<string>();
  has(key: string): boolean { return this.consumed.has(key); }
  claim(key: string): boolean {
    if (!key || this.consumed.has(key)) return false;
    this.consumed.add(key);
    while (this.consumed.size > 512) this.consumed.delete(this.consumed.values().next().value!);
    return true;
  }
  get size(): number { return this.consumed.size; }
}

export function replyTextRequestKey(request: QueryRevealRequest | null | undefined,
  scopeKey: string, turnId: string): string {
  return request?.requestId && request.turnId === turnId && request.scopeKey === scopeKey && scopeKey
    ? JSON.stringify([scopeKey, turnId, request.requestId]) : "";
}

export function replyTextRequestFresh(request: QueryRevealRequest | null | undefined, nowMs: number): boolean {
  return Boolean(request && Number.isFinite(nowMs) && Number.isFinite(request.startedAtMs)
    && nowMs >= request.startedAtMs && nowMs - request.startedAtMs <= REPLY_TEXT_REQUEST_MAX_AGE_MS);
}

export type ReplyTextPlan = { text: string; ends: number[]; durationMs: number };

/** Numbers, currencies, card tails and dates are whole visual tokens. The rest
 * uses grapheme boundaries, so a family emoji or accented letter never breaks. */
export function replyTextPlan(text: string): ReplyTextPlan {
  if (typeof Intl.Segmenter !== "function") return { text, ends: [text.length], durationMs: 0 };
  const ends: number[] = [];
  const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  const append = (value: string, offset: number) => {
    for (const piece of segmenter.segment(value)) ends.push(offset + piece.index + piece.segment.length);
  };
  const numbers = /(?:(?:CNY|USD|RMB|[¥￥$€])\s*)?[+-]?\d[\dA-Za-z.,:/_-]*(?:\s?(?:人民币|美元|万元|CNY|USD|RMB|元|%))?/gu;
  let previous = 0;
  for (const match of text.matchAll(numbers)) {
    append(text.slice(previous, match.index), previous);
    previous = match.index + match[0].length;
    ends.push(previous);
  }
  append(text.slice(previous), previous);
  return { text, ends, durationMs: ends.length < 2 ? 0 : Math.min(REPLY_TEXT_MAX_MS, Math.max(140, ends.length * 22)) };
}

export function replyTextFrame(plan: ReplyTextPlan, elapsedMs: number): { visible: string; complete: boolean } {
  if (plan.durationMs === 0 || elapsedMs >= plan.durationMs)
    return { visible: plan.text, complete: true };
  const count = Math.min(plan.ends.length, Math.max(0, Math.floor(plan.ends.length * Math.max(0, elapsedMs) / plan.durationMs)));
  return { visible: count ? plan.text.slice(0, plan.ends[count - 1]) : "", complete: false };
}
