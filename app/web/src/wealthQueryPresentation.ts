import type { CustomerLocale } from "./customerLanguage";

type Data = Record<string, unknown>;
type WealthReadNode = { action: string; status?: string; result?: unknown };
const object = (value: unknown): Data | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Data : null;
const sharedCodes = ["RISK_ASSESSMENT_REQUIRED", "RISK_PROFILE_EXPIRED"] as const;

/** Common display facts only. Missing/unknown restrictions are never inferred. */
export function wealthQueryPresentation(node: WealthReadNode, locale: CustomerLocale = "zh-CN") {
  const result = object(node.result), products = result?.products;
  const records = Array.isArray(products) && products.length && products.every(row => object(row)) ? products as Data[] : [];
  const eligible = node.action === "WEALTH_COMPARE" && node.status === "SUCCEEDED" && records.length > 0;
  const commonRiskCodes: readonly string[] = eligible ? sharedCodes.filter(code => records.every(row => row.suitable === false
    && Array.isArray(row.suitability_reason_codes) && row.suitability_reason_codes.includes(code))) : [];
  const sharedCapitalRisk = Boolean(eligible && records.every(row => object(row.terms)?.capital_guaranteed === false));
  const notices: string[] = commonRiskCodes.map(code => code === "RISK_ASSESSMENT_REQUIRED"
    ? locale === "en" ? "Complete your own risk assessment before a purchase." : "买入前需先由你本人完成风险测评。"
    : locale === "en" ? "Your risk assessment has expired; complete it again before a purchase." : "风险测评已过期，买入前需重新测评。");
  if (sharedCapitalRisk) notices.push(locale === "en" ? "Investments carry risk; principal is not guaranteed." : "理财有风险，本金不保证。");
  return { commonRiskCodes, sharedCapitalRisk, notice: notices.join(locale === "en" ? " " : "") || null };
}

/** Caller may deduplicate this exact notice only inside one verified read task. */
export function wealthSharedNoticeKey(node: WealthReadNode, locale: CustomerLocale = "zh-CN"): string | null {
  const notice = wealthQueryPresentation(node, locale).notice;
  return notice ? JSON.stringify([locale, notice]) : null;
}
