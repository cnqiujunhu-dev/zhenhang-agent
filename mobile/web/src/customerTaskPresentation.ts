import type { JsonValue } from "./types";
import { subscriptionRestorationPreview } from "./subscriptionCommercePresentation.ts";

// Customer-facing business information, not internal trace/debug dictionaries.
const businessFields = new Set([
  "message", "summary", "explanation", "notice", "reason", "note", "memo", "label", "name",
  "amount_minor", "balance_minor", "available_minor", "remaining_minor", "available_balance_minor",
  "spending_minor", "refund_minor", "income_minor", "net_minor", "emergency_reserve_minor",
  "source_account_balance_minor", "source_balance_minor", "account_balance_minor", "usd_balance_minor",
  "requested_amount_minor", "debit_minor", "credit_minor", "fee_minor", "limit_minor", "target_minor",
  "reserved_minor", "maximum_amount_minor", "goal_reserved_minor", "unit_price_minor", "price_minor",
  "payee", "payee_name", "payee_last4", "recipient_username", "recipient_display_name", "source_username",
  "card_number", "card_last4", "account_last4", "display_last4", "source_card_last4", "recipient_card_number",
  "source_card_number", "source_card_display_last4", "recipient_card_display_last4", "source_card_context",
  "transfer_channel", "currency", "requested_currency", "source_currency", "target_currency", "limit_currency",
  "currency_mode", "supported_currencies", "balances_by_currency", "fx_quote", "rate", "expiry", "expires_at",
  "funding_notice", "settlement_notice", "merchant", "product_name", "subscription", "source_type", "card_status",
  "status", "order_type", "units", "holding_units", "minimum_units", "risk_level", "capital_guaranteed",
  "yield_prediction_provided", "redemption_timing", "term", "terms", "risk_profile", "suitability_explanation",
  "receipt_no", "receipt_id", "transaction_id", "transfer_id", "order_id", "committed_at", "executed_at",
  "created_at", "next_due_at", "due_at", "due_date", "count", "transaction_count", "receipt", "result",
  "preview", "order_preview", "scope", "points", "credited_points", "cost_points", "wallet_points", "remaining_points",
  "goal_name", "behavior_name", "reward_name", "grantee_name", "owner_name", "proposal_kind", "scopes",
  "limit_period", "expires_days", "estimated_avoided_grams", "distance_m", "mode", "behavior", "reward",
  "already_cancelled", "schedule_status", "direction", "requested_currency", "source_currency", "target_currency",
  "monthly_price_minor", "total_amount_minor", "charged_minor", "months", "auto_renew", "effective_from", "effective_policy",
  "entitlement_end_at", "entitlement_preserved", "refunded_minor", "already_active",
  "mandate_amount_minor", "mandate_billing_months",
]);

export function customerDataEntries(data: Record<string, JsonValue>, options: { confirmation?: boolean } = {}): [string, JsonValue][] {
  const restorePreview = options.confirmation === true && subscriptionRestorationPreview("SUBSCRIPTION_START",data);
  // Display-only omissions: never rewrite the signed scope or expiry used by
  // bank authorization. The confirmation deadline remains in the parent card.
  const subscriptionConfirmation = options.confirmation === true && typeof data.merchant === "string"
    && typeof data.monthly_price_minor === "number" && Number.isSafeInteger(data.monthly_price_minor) && data.monthly_price_minor > 0
    && typeof data.months === "number" && Number.isSafeInteger(data.months) && data.months > 0 && data.months <= 12;
  const sameEntitlementExpiry = subscriptionConfirmation && typeof data.entitlement_end_at === "string" && typeof data.expires_at === "string"
    && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(data.entitlement_end_at) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(data.expires_at)
    && Number.isFinite(Date.parse(data.entitlement_end_at)) && Date.parse(data.entitlement_end_at) === Date.parse(data.expires_at);
  // These are identifiers recorded in the same bank DTO, not a join with
  // today's card list. A new HEX number/tail owns the customer identity; the
  // old compatibility tail must not appear as a second, different card.
  // Conflicting new snapshots remain visible rather than silently repaired.
  const hiddenIdentityFields = new Set<string>();
  for (const [numberKey, displayKey, legacyKeys] of [
    ["card_number", "display_last4", ["card_last4", "last4", "legacy_last4"]],
    ["source_card_number", "source_card_display_last4", ["source_card_last4", "source_card_legacy_last4"]],
    ["recipient_card_number", "recipient_card_display_last4", []],
  ] as const) {
    const number = data[numberKey], display = data[displayKey];
    const validNumber = typeof number === "string" && /^(?:zhenhang|xiaozhao)[0-9a-f]{8}$/i.test(number);
    const validTail = typeof display === "string" && /^[0-9a-f]{4}$/i.test(display);
    const consistent = validNumber && (!validTail || number.slice(-4).toLowerCase() === display.toLowerCase());
    if (consistent) {
      legacyKeys.forEach(key => hiddenIdentityFields.add(key));
      if (validTail) hiddenIdentityFields.add(displayKey);
    }
  }
  return Object.entries(data).filter(([key,value]) => businessFields.has(key)
    && !(restorePreview && ["months","monthly_price_minor","total_amount_minor","charged_minor","effective_policy"].includes(key))
    && !hiddenIdentityFields.has(key)
    && !(key === "recipient_username" && value === null)
    && !(options.confirmation === true && data.auto_renew === false && ["mandate_amount_minor","mandate_billing_months"].includes(key))
    && !(key === "expires_at" && sameEntitlementExpiry));
}

const actions: Record<string, string> = {
  TRANSFER: "转账", FX_TRANSFER: "美元转账", FX_EXCHANGE: "换汇", FX_QUOTE: "查询汇率",
  TRANSFER_SCHEDULE: "预约转账", AA_REQUEST: "发起收款", BILL_ANALYSIS: "查看账单",
  CARD_STATUS: "查看卡片", CARD_LOCK: "锁定卡片", CARD_UNLOCK: "解锁卡片", CARD_SET_LIMIT: "调整限额",
  CARD_REPORT_LOSS: "挂失卡片", CARD_REPLACEMENT: "申请补卡", WEALTH_COMPARE: "比较理财产品",
  WEALTH_BUY: "申购理财", WEALTH_REDEEM: "赎回理财", SUBSCRIPTION_LIST: "查看订阅",
  SUBSCRIPTION_CANCEL: "关闭自动续费", SUBSCRIPTION_START: "开通订阅", SUBSCRIPTION_RENEW: "续期订阅", FRAUD_ANALYZE: "检查转账风险", FAMILY_VIEW: "查看家庭协助",
  FAMILY_GRANT: "设置家庭授权", FAMILY_REVOKE: "取消家庭授权", FAMILY_PROPOSE: "提交家庭协助申请",
  GOAL_VIEW: "查看储蓄目标", GOAL_CREATE: "设置储蓄目标", GOAL_RESERVE: "预留目标资金",
  GOAL_RELEASE: "释放预留资金", CARBON_VIEW: "查看低碳权益", CARBON_CLAIM: "领取低碳积分",
  CARBON_REDEEM: "兑换权益", CONTACT_BIND: "设置收款人称呼",
  ASSET_POSITIONS_READ: "查询资产持仓", MEMBERSHIP_VIEW: "查看客户与卡片等级",
  BENEFITS_VIEW: "查询优惠活动", PREFERENCE_VIEW: "查看个人偏好",
  PREFERENCE_SET: "更新个人偏好", PREFERENCE_REMOVE: "撤回个人偏好",
};
// READ navigation labels only. Registering WRITE names here can change the
// confirmation-owned filler guard, so those titles remain in their own view.
const readActions: Record<string, [string, string]> = {
  AA_VIEW: ["查看AA收付款", "View AA payments"],
  TRANSFER_SCHEDULE_VIEW: ["定时转账安排", "Scheduled transfers"],
  CREDIT_VIEW: ["查看信用卡", "View credit cards"],
  CREDIT_STATEMENT: ["查看信用卡账单", "View credit statements"],
  OCCASION_VIEW: ["查看家庭生日", "View family birthdays"],
  OCCASION_MEMBER_VIEW: ["查看家庭资料", "View family directory details"],
  LIFE_PLAN_VIEW: ["查看生活计划", "View life plans"],
  MERCHANT_CATALOG: ["查看商品", "View products"],
  MERCHANT_ORDERS: ["查看订单", "View orders"],
};
export function customerReadActionLabel(action: string, locale = "zh-CN"): string | null {
  return readActions[action]?.[locale === "en" ? 1 : 0] || null;
}
export function customerActionLabel(action: string, locale = "zh-CN"): string {
  return customerReadActionLabel(action,locale) || actions[action] || "办理事项";
}
