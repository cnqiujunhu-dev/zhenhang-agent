import type { Confirmation, JsonValue, TraceNode } from "./types";
import { exactMajorAmount } from "./typedMoney";
import { customerActionLabel } from "./customerTaskPresentation";
import { customerName } from "./customerPresentation";

const record = (value: JsonValue | undefined): Record<string, JsonValue> => value && typeof value === "object" && !Array.isArray(value) ? value : {};
function spokenMoney(value: JsonValue | undefined, currency: JsonValue | undefined): string | null {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || !["CNY", "USD"].includes(String(currency))) return null;
  return `${exactMajorAmount(value)}${currency === "USD" ? "美元" : "人民币"}`;
}
export function confirmationSpeech(node: TraceNode | undefined, pending: Confirmation, locale: "zh-CN" | "en" = "zh-CN"): string {
  const result = node?.result || {};
  const preview = record(result.order_preview || result.preview);
  const context = Object.keys(preview).length ? preview : result;
  const quote = record(result.fx_quote || context.fx_quote);
  const target = ["recipient_username", "payee_name", "product_name", "merchant", "goal_name", "grantee_name", "behavior_name", "reward_name"]
    .map(key => quote[key] || context[key]).find(value => typeof value === "string");
  const name = typeof target === "string" ? customerName(target).replace(/\d{5,}/g, "").slice(0,60) : "页面列明的对象";
  if (locale === "en") {
    const action = node?.action?.startsWith("FX_") ? "currency action" : ({ TRANSFER: "transfer", TRANSFER_SCHEDULE: "scheduled transfer", CARD_LOCK: "temporary card lock", CARD_UNLOCK: "card unlock", CARD_REPORT_LOSS: "report card lost", SUBSCRIPTION_CANCEL: "subscription cancellation" } as Record<string, string>)[node?.action || ""] || "action";
    const parts = [`Review this ${action} for ${typeof target === "string" ? name : "the recipient shown on this page"}.`];
    const funds = (value: JsonValue | undefined, currency: JsonValue | undefined) => typeof value === "number" && Number.isSafeInteger(value) && ["CNY", "USD"].includes(String(currency)) ? `${exactMajorAmount(value)} ${currency}` : null;
    if (node?.action.startsWith("FX_")) {
      const debit = funds(quote.debit_minor ?? context.debit_minor, quote.source_currency || context.source_currency);
      const credit = funds(quote.credit_minor ?? context.credit_minor, quote.target_currency || context.target_currency);
      parts.push(debit && credit ? `Debit ${debit}; credit ${credit}.` : "Debit and credit details are incomplete. Review the displayed text.");
    } else { const amount = funds(context.amount_minor ?? context.limit_minor ?? context.maximum_amount_minor, context.currency || "CNY"); if (amount) parts.push(`Amount ${amount}.`); }
    parts.push(pending.requires_step_up ? "Review the page and complete this action's verification before confirming." : "Review the page. This action executes only after confirmation.");
    return parts.join(" ");
  }
  const parts = [`请核对${customerActionLabel(node?.action || "")}，对象是${name}。`];
  if (node?.action.startsWith("FX_")) {
    const debit = spokenMoney(quote.debit_minor ?? context.debit_minor, quote.source_currency || context.source_currency);
    const credit = spokenMoney(quote.credit_minor ?? context.credit_minor, quote.target_currency || context.target_currency);
    parts.push(debit && credit ? `本次扣款${debit}，到账${credit}。` : "扣款和到账币种、金额尚未完整返回，请以文字核对。");
  } else {
    const amount = spokenMoney(context.amount_minor ?? context.limit_minor ?? context.maximum_amount_minor, context.currency || "CNY");
    if (amount) parts.push(`金额${amount}。`);
  }
  parts.push(pending.requires_step_up ? "请核对页面并完成本次验证，再确认办理。" : "请核对页面，确认后才办理。");
  return parts.join("");
}
