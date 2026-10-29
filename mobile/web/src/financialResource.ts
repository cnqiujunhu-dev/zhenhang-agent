import { ApiError } from "./api.ts";

type Kind = "accounts" | "cards" | "investments" | "subscriptions" | "notifications" | "bill";
const text = (value: unknown) => typeof value === "string" && value.trim().length > 0;
const integer = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value);
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const optionalInteger = (row: Record<string, unknown>, key: string) => row[key] === undefined || integer(row[key]);

/** V1 read DTOs: empty arrays are valid; missing/null/bool money is never fabricated as zero. */
export function assertFinancialResource<T>(kind: Kind, value: T): T {
  const rowValid = (row: unknown) => {
    if (!record(row)) return false;
    if (kind === "accounts") return text(row.account_id) && text(row.name) && integer(row.balance_minor)
      && optionalInteger(row,"available_balance_minor") && optionalInteger(row,"goal_reserved_minor")
      && (row.currency === undefined || row.currency === "CNY");
    if (kind === "cards") return text(row.card_id) && text(row.last4) && text(row.status) && integer(row.limit_minor)
      && (row.balances_by_currency === undefined || record(row.balances_by_currency) && Object.values(row.balances_by_currency).every(integer));
    if (kind === "investments") return text(row.product_id) && text(row.name) && text(row.status)
      && integer(row.price_minor) && integer(row.holding_units) && integer(row.risk_level);
    if (kind === "subscriptions") return text(row.subscription_id) && text(row.merchant) && text(row.status) && integer(row.amount_minor);
    if (kind === "notifications") return text(row.notification_id) && text(row.event_type) && text(row.created_at) && record(row.payload);
    return integer(row.transaction_count) && (row.transaction_count as number) >= 0
      && ["spending_minor","income_minor","refund_minor","net_minor"].every(key=>integer(row[key]))
      && record(row.category_spending_minor) && Object.values(row.category_spending_minor).every(integer);
  };
  if (kind === "bill" ? !rowValid(value) : !Array.isArray(value) || !value.every(rowValid))
    throw new ApiError("资料尚未完整核验，暂不展示不确定的金额或状态，请重新读取。", 0, "RESOURCE_SCHEMA_INVALID");
  return value;
}
