export type SubscriptionLoadState = "LOADING" | "UNAVAILABLE" | "READY";

/** Overview counts only. Different or unknown cycles/currencies never form a fee total. */
export function subscriptionOverview(records: unknown, loadState: SubscriptionLoadState) {
  if (loadState !== "READY" || !Array.isArray(records)) return { active: null, total: null };
  if (!records.every(row => row && typeof row === "object" && !Array.isArray(row)
    && typeof row.subscription_id === "string" && row.subscription_id.trim()
    && (row.status === "ACTIVE" || row.status === "CANCELLED"))) return { active: null, total: null };
  return { active: records.filter(row => row.status === "ACTIVE").length, total: records.length };
}
