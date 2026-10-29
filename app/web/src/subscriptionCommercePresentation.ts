import type { JsonValue } from "./types";

type SubscriptionData = Record<string, JsonValue>;
const text = (value: unknown) => typeof value === "string" && value.trim().length > 0;
const integer = (value: unknown, minimum = 0): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
export function subscriptionDate(value: unknown): value is string {
  return typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
}
/** A mandate restoration preserves existing paid access and does not debit. */
export function subscriptionRestorationPreview(action: string, data: SubscriptionData): boolean {
  return action === "SUBSCRIPTION_START" && data.action === "START" && data.effect_kind === "RESTORE_AUTORENEW"
    && data.currency === "CNY" && text(data.merchant) && data.auto_renew === true
    && data.auto_renew_setting_source === "CURRENT_USER" && data.effective_policy === "RESTORE_AT_EXISTING_PAID_EXPIRY"
    && data.total_amount_minor === 0 && data.charged_minor === 0
    && integer(data.months, 1) && data.months <= 12 && integer(data.monthly_price_minor, 1)
    && integer(data.mandate_amount_minor, 1) && data.mandate_billing_months === data.months
    && Number.isSafeInteger(data.months * data.monthly_price_minor) && data.mandate_amount_minor === data.months * data.monthly_price_minor
    && subscriptionDate(data.entitlement_end_at) && subscriptionDate(data.effective_from)
    && Date.parse(data.entitlement_end_at) === Date.parse(data.effective_from)
    && !data.payment_id && !data.transaction_id;
}
export function recordedSubscriptionRestoration(action: string, data: SubscriptionData): boolean {
  return subscriptionRestorationPreview(action, data) && data.status === "ACTIVE" && text(data.subscription_id)
    && integer(data.version, 1) && data.mandate_restored === true && data.entitled === true && data.already_active !== true;
}
/** Receipt facts only. Neither a prepared price nor a SUCCEEDED label alone
 * proves payment. Values are displayed, never used to calculate an order. */
export function recordedSubscriptionPayment(action: string, data: SubscriptionData): boolean {
  return ["SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"].includes(action)
    && data.effect_kind !== "RESTORE_AUTORENEW" && data.mandate_restored !== true
    && text(data.subscription_id) && text(data.merchant) && data.status === "ACTIVE" && data.currency === "CNY"
    && data.already_active !== true && text(data.payment_id) && text(data.transaction_id)
    && integer(data.months, 1) && data.months <= 12 && integer(data.monthly_price_minor, 1)
    && integer(data.total_amount_minor, 1) && integer(data.charged_minor, 1)
    && data.charged_minor === data.total_amount_minor && Number.isSafeInteger(data.monthly_price_minor * data.months)
    && data.total_amount_minor === data.monthly_price_minor * data.months
    && typeof data.auto_renew === "boolean" && subscriptionDate(data.effective_from) && subscriptionDate(data.entitlement_end_at)
    && Date.parse(data.entitlement_end_at) > Date.parse(data.effective_from);
}
export function recordedActiveSubscription(action: string, data: SubscriptionData): boolean {
  return action === "SUBSCRIPTION_START" && data.effect_kind !== "RESTORE_AUTORENEW" && data.already_active === true && data.charged_minor === 0
    && text(data.subscription_id) && text(data.merchant) && (data.status === "ACTIVE"
      || data.status === "CANCELLED" && data.no_effect_kind === "START_PAID_RIGHTS_ACTIVE" && data.entitled === true
      && data.auto_renew === false && subscriptionDate(data.entitlement_end_at))
    && !data.payment_id && !data.transaction_id;
}
export function recordedSubscriptionAutoOff(action: string, data: SubscriptionData): boolean {
  return action === "SUBSCRIPTION_CANCEL" && data.already_cancelled === true && data.auto_renew === false
    && data.status === "ACTIVE" && data.refunded_minor === 0 && text(data.subscription_id) && text(data.merchant)
    && !data.payment_id && !data.transaction_id;
}
export function recordedSubscriptionGuidance(action: string, data: SubscriptionData): boolean {
  return ["SUBSCRIPTION_START", "SUBSCRIPTION_RENEW"].includes(action) && data.no_effect_kind === "REGISTERED_UNAVAILABLE_GUIDANCE"
    && data.can_start_here === false && data.can_renew_here === false && data.charged_minor === 0 && text(data.merchant)
    && !data.payment_id && !data.transaction_id;
}
