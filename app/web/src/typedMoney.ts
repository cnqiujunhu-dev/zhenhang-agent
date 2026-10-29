import type { JsonValue } from "./types";

export type MoneyCurrency = "CNY" | "USD";
export type MoneyOptions = { signed?: boolean; currency?: string };
export function isMoneyCurrency(value: unknown): value is MoneyCurrency {
  return value === "CNY" || value === "USD";
}
export function formatMoneyMinor(minor: number, options: MoneyOptions = {}) {
  if (!Number.isSafeInteger(minor)) return "金额未返回";
  const currency = options.currency || "CNY";
  if (!isMoneyCurrency(currency))
    return `${currency} ${exactMajorAmount(minor)}`;
  const integer = BigInt(minor);
  const absolute = integer < 0n ? -integer : integer;
  const whole = absolute / 100n;
  const cents = (absolute % 100n).toString().padStart(2, "0");
  // Intl groups the exact BigInt whole amount. Preserve the negative sign for -0.01 too.
  const signedWhole = integer < 0n ? (whole === 0n ? -0 : -whole) : whole;
  const formatted = new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
    .formatToParts(signedWhole)
    .map((part) => (part.type === "fraction" ? cents : part.value))
    .join("");
  return `${options.signed && minor > 0 ? "+" : ""}${formatted}`;
}
export function exactMajorAmount(minor: number) {
  if (!Number.isSafeInteger(minor))
    throw new Error("金额超出可准确表示的范围。");
  const value = BigInt(minor);
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? "-" : ""}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, "0")}`;
}
/** Interpret declared currencies only. No FX rate, conversion, or cross-currency aggregation here. */
export function currencyForMinorField(
  key: string,
  data: Record<string, JsonValue>,
  inherited?: string,
): string | undefined {
  const quote =
    data.fx_quote &&
    typeof data.fx_quote === "object" &&
    !Array.isArray(data.fx_quote)
      ? data.fx_quote
      : {};
  const context = { ...quote, ...data };
  const currency = (field: string) =>
    typeof context[field] === "string" ? String(context[field]) : undefined;
  if (key === "cny_reference_minor") return "CNY";
  if (key === "usd_balance_minor") return "USD";
  if (key === "requested_amount_minor") return currency("requested_currency");
  if (key === "input_amount_minor") return currency("input_currency");
  if (key === "output_amount_minor") return currency("output_currency");
  if (key === "debit_minor")
    return currency("source_currency") || currency("currency") || inherited;
  if (key === "credit_minor")
    return currency("target_currency") || currency("currency") || inherited;
  if (key.startsWith("source_"))
    return (
      currency("source_currency") || currency("currency") || inherited || "CNY"
    );
  if (key.startsWith("target_"))
    return (
      currency("target_currency") || currency("currency") || inherited || "CNY"
    );
  if (key === "fee_minor")
    return (
      currency("fee_currency") ||
      currency("source_currency") ||
      inherited ||
      currency("currency") ||
      "CNY"
    );
  return (
    currency("currency") || currency("requested_currency") || inherited || "CNY"
  );
}
