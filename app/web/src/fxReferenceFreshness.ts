import type { TraceNode } from "./types";

const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : null;

/** Presentation eligibility only. This does not validate or authorize a bank action. */
export function fxReferenceState(node: TraceNode, nowMs = Date.now()) {
  const result = object(node.result), rate = object(result?.market_rate), conversion = object(result?.conversion);
  const rateText = text(rate?.rate);
  const rateValid = rate?.base_currency === "USD" && rate?.quote_currency === "CNY"
    && Boolean(rateText && /^\d+(?:\.\d+)?$/.test(rateText) && Number(rateText) >= 1 && Number(rateText) <= 20);
  const parsed = typeof rate?.expires_at === "string" ? Date.parse(rate.expires_at) : NaN;
  const deadline = Number.isFinite(parsed) ? parsed : null;
  const expired = deadline !== null && Number.isFinite(nowMs) && deadline <= nowMs;
  const sourceCurrency = text(conversion?.source_currency), targetCurrency = text(conversion?.target_currency);
  const inputCurrency = text(conversion?.input_currency), inputAmount = conversion?.input_amount_minor;
  const canPrepare = node.action === "FX_QUOTE" && node.status === "SUCCEEDED" && rateValid
    && Number.isFinite(nowMs) && deadline !== null && !expired
    && conversion?.amount_role === "SOURCE" && typeof inputAmount === "number" && Number.isSafeInteger(inputAmount) && inputAmount > 0
    && inputCurrency === sourceCurrency && ["USD", "CNY"].includes(sourceCurrency || "")
    && ["USD", "CNY"].includes(targetCurrency || "") && sourceCurrency !== targetCurrency && conversion?.executable === false;
  // A retained click must match this displayed record and original input, even
  // when the newest packet happens to have the same deadline.
  const scopeKey = JSON.stringify([node.node_id, node.action, node.status,
    rate?.base_currency, rate?.quote_currency, rateText, rate?.expires_at,
    rate?.provider, rate?.market_date, rate?.fetched_at,
    conversion?.amount_role, inputCurrency, inputAmount, sourceCurrency, targetCurrency,
    conversion?.output_currency, conversion?.output_amount_minor, conversion?.executable]);
  return { scopeKey, deadline, expired, canPrepare };
}
