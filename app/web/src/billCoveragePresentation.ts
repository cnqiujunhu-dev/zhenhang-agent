import type { BillAnalysis } from "./types";
import type { CustomerLocale } from "./customerLanguage";

export function billCoverageUnusable(coverage: BillAnalysis["coverage"]): boolean {
  if (coverage === null || coverage === undefined) return false; // Historical snapshot, not silently re-dated.
  return !["COMPLETE", "PARTIAL", "OBSERVED_HISTORY"].includes(coverage.status);
}
export function billCoverageNotice(coverage: BillAnalysis["coverage"], locale: CustomerLocale): string | null {
  if (!coverage) return null;
  if (billCoverageUnusable(coverage)) return locale === "en" ? "The requested period is not sufficiently covered. This is not a verified zero-spending statement; no growth conclusion is available." : "所问期间的资料覆盖不足，不能当作完整期零消费，也不能据此判断支出增长。";
  if (coverage.basis === "LIVE_ACTIVITY_ONLY") return locale === "en" ? "Only newly posted activity is available. Other consumption records are not fully covered; this does not mean no spending occurred." : "这里只包含新入账活动，其他消费记录尚未完整覆盖，不能据此说本月没有消费。";
  if (coverage.status === "PARTIAL") return locale === "en" ? "Only the covered transactions are shown, not a full-period total." : "以下仅统计已覆盖的流水，不是完整统计期总额。";
  if (coverage.status === "OBSERVED_HISTORY") return locale === "en" ? "These are the available historical transactions, not an inferred current-period statement." : "以下为已取得的历史流水，不推断为当前统计期账单。";
  return null;
}
export function billComparableChange(analysis: BillAnalysis): number | null {
  const insights = analysis.financial_insights, comparison = insights?.comparison;
  if (analysis.period_fallback || billCoverageUnusable(analysis.coverage) || insights?.currency !== "CNY" || insights?.unit !== "MINOR" || !comparison ||
    !["COMPLETE_PERIODS", "EQUAL_ELAPSED"].includes(comparison.status) || comparison.growth_claim_allowed !== true && comparison.zero_baseline !== true ||
    !Number.isSafeInteger(comparison.spending_change_minor)) return null;
  return comparison.spending_change_minor!;
}

const parsedInstant = (value: unknown) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))?$/.test(value)
  && Number.isFinite(Date.parse(value)) ? Date.parse(value.length===10 ? `${value}T00:00:00+08:00` : value) : null;
function shanghaiParts(value: unknown) {
  const parsed=parsedInstant(value);if(parsed===null)return null;
  const parts=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).formatToParts(parsed);
  const part=(kind:string)=>parts.find(row=>row.type===kind)?.value || "";
  return {year:Number(part("year")),month:Number(part("month")),day:Number(part("day")),hour:Number(part("hour")),minute:Number(part("minute")),second:Number(part("second")),millisecond:parsed%1000};
}
function validMonth(period: unknown): period is NonNullable<BillAnalysis["period_fallback"]>["displayed"] {
  if(!period || typeof period!=="object")return false;
  const row=period as Record<string,unknown>,start=shanghaiParts(row.start),end=shanghaiParts(row.end);
  return row.kind==="MONTH" && row.time_zone==="Asia/Shanghai" && !!start && !!end && start.day===1 && end.day===1
    && start.hour===0 && end.hour===0 && start.minute===0 && end.minute===0 && start.second===0 && end.second===0 && start.millisecond===0 && end.millisecond===0
    && end.year*12+end.month===start.year*12+start.month+1;
}
function checkedMovementActivity(fallback: NonNullable<BillAnalysis["period_fallback"]>) {
  const activity=fallback.requested_activity,coverage=fallback.requested_coverage;
  if(fallback.reason!=="REQUESTED_PERIOD_ONLY_MOVEMENTS" || !activity || Object.keys(activity).length!==9
    || activity.currency!=="CNY" || !Number.isSafeInteger(activity.transaction_count) || activity.transaction_count<1
    || ![activity.spending_minor,activity.income_minor,activity.refund_minor].every(value=>value===0)
    || ![activity.movement_in_minor,activity.movement_out_minor].every(value=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0)
    || !Number.isSafeInteger(activity.movement_in_minor+activity.movement_out_minor) || activity.movement_in_minor+activity.movement_out_minor<=0
    || parsedInstant(activity.period_start)!==parsedInstant(fallback.requested.start) || parsedInstant(activity.period_end)!==parsedInstant(fallback.requested.end)
    || coverage?.status!=="PARTIAL" || coverage.basis!=="LIVE_ACTIVITY_ONLY"
    || parsedInstant(coverage.requested_start)!==parsedInstant(fallback.requested.start) || parsedInstant(coverage.requested_end)!==parsedInstant(fallback.requested.end))return null;
  return activity;
}
/** This is metadata attached to real returned historical facts, never a
 * client request to silently change the accounting period. */
export function billPeriodFallback(analysis: BillAnalysis): BillAnalysis["period_fallback"] | null {
  const fallback=analysis.period_fallback;
  const category=analysis.category_filter,insightCategory=analysis.financial_insights?.category_filter;
  const exactCategory=Boolean(category?.version==="bill-category-filter-v1" && category.basis==="CATEGORY_EXACT_MATCH"
    && category.currency==="CNY" && category.read_only===true && category.bank_state_changed===false
    && typeof category.category==="string" && category.category.trim().length>0 && category.category.length<=32
    && insightCategory && Object.keys(category).length===Object.keys(insightCategory).length
    && Object.keys(category).every(key=>(category as unknown as Record<string,unknown>)[key]===(insightCategory as unknown as Record<string,unknown>)[key]));
  const insights=analysis.financial_insights, categories=analysis.category_spending_minor;
  const emptyCategory=exactCategory && analysis.transaction_count===0 && Number.isSafeInteger(fallback?.unfiltered_period_transaction_count)
    && fallback!.unfiltered_period_transaction_count!>0 && categories && typeof categories==="object" && !Array.isArray(categories) && Object.keys(categories).length===0
    && insights?.version==="financial-diagnostics-v1" && insights.currency==="CNY" && insights.unit==="MINOR"
    && insights.read_only===true && insights.bank_state_changed===false && insights.model_calculated_money===false
    && Array.isArray(analysis.statement_transactions) && analysis.statement_transactions.length===0
    && [analysis.spending_minor,analysis.income_minor,analysis.refund_minor,analysis.movement_in_minor,analysis.movement_out_minor,analysis.net_minor].every(value=>value===0);
  const movementsOnly=fallback?.reason==="REQUESTED_PERIOD_ONLY_MOVEMENTS";
  if(!fallback || fallback.version!=="bill-period-fallback-v1" || fallback.status!=="APPLIED"
    || !["REQUESTED_PERIOD_NOT_COVERED","REQUESTED_PERIOD_ONLY_MOVEMENTS"].includes(fallback.reason)
    || fallback.read_only!==true || fallback.bank_state_changed!==false || !validMonth(fallback.requested) || !validMonth(fallback.displayed)
    || !Number.isSafeInteger(fallback.available_transaction_count) || (fallback.available_transaction_count<=0 && (movementsOnly || !emptyCategory))
    || fallback.available_transaction_count!==analysis.transaction_count || billCoverageUnusable(analysis.coverage) || !analysis.coverage
    || analysis.coverage.basis==="LIVE_ACTIVITY_ONLY" || !fallback.requested_coverage
    || (movementsOnly ? !checkedMovementActivity(fallback) : !billCoverageUnusable(fallback.requested_coverage))
    || movementsOnly && (parsedInstant(fallback.displayed.start)! >= parsedInstant(fallback.requested.start)!)
    || parsedInstant(fallback.requested.start)===parsedInstant(fallback.displayed.start)
    || parsedInstant(analysis.period_start)!==parsedInstant(fallback.displayed.start) || parsedInstant(analysis.period_end)!==parsedInstant(fallback.displayed.end)
    || parsedInstant(analysis.coverage.requested_start)!==parsedInstant(fallback.displayed.start) || parsedInstant(analysis.coverage.requested_end)!==parsedInstant(fallback.displayed.end))return null;
  return fallback;
}
export function billAnalysisForDisplay(primary: BillAnalysis, supplementary?: BillAnalysis | null): BillAnalysis {
  const fallback=supplementary && billPeriodFallback(supplementary);
  if(fallback?.reason==="REQUESTED_PERIOD_ONLY_MOVEMENTS"){
    const activity=checkedMovementActivity(fallback),categories=primary.category_spending_minor;
    const scopeMatches=parsedInstant(primary.period_start)===parsedInstant(fallback.requested.start)
      && parsedInstant(primary.period_end)===parsedInstant(fallback.requested.end)
      && parsedInstant(primary.coverage?.requested_start)===parsedInstant(fallback.requested.start)
      && parsedInstant(primary.coverage?.requested_end)===parsedInstant(fallback.requested.end);
    const activityMatches=activity && primary.coverage?.status==="PARTIAL" && primary.coverage.basis==="LIVE_ACTIVITY_ONLY"
      && primary.transaction_count===activity.transaction_count && primary.spending_minor===activity.spending_minor
      && primary.income_minor===activity.income_minor && primary.refund_minor===activity.refund_minor
      && primary.movement_in_minor===activity.movement_in_minor && primary.movement_out_minor===activity.movement_out_minor
      && categories && typeof categories==="object" && !Array.isArray(categories) && Object.keys(categories).length===0;
    return scopeMatches&&activityMatches?supplementary!:primary;
  }
  return fallback && billCoverageUnusable(primary.coverage) && primary.transaction_count===0
    && parsedInstant(primary.coverage?.requested_start)===parsedInstant(fallback.requested.start)
    && parsedInstant(primary.coverage?.requested_end)===parsedInstant(fallback.requested.end) ? supplementary! : primary;
}
export function billDateLabel(value: unknown, locale: CustomerLocale, exclusiveEnd=false, includeTime=false): string {
  const parsed=parsedInstant(value);if(parsed===null)return locale==="en" ? "Date unavailable" : "日期待核实";
  return new Intl.DateTimeFormat(locale==="en" ? "en-US" : "zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:locale==="en" ? "short" : "long",day:"numeric",...(includeTime ? {hour:"2-digit" as const,minute:"2-digit" as const,hourCycle:"h23" as const} : {})}).format(parsed-(exclusiveEnd ? 1 : 0));
}
export function billMonthLabel(start: unknown, locale: CustomerLocale, includeYear=true): string | null {
  const parsed=parsedInstant(start);if(parsed===null)return null;
  if(locale!=="en") {const parts=shanghaiParts(start)!;return `${includeYear ? `${parts.year}年` : ""}${parts.month}月`;}
  return new Intl.DateTimeFormat("en-US",{timeZone:"Asia/Shanghai",...(includeYear ? {year:"numeric" as const} : {}),month:"long"}).format(parsed);
}
export function billPeriodLabel(analysis: BillAnalysis, locale: CustomerLocale): string {
  const start=analysis.period_start || analysis.coverage?.requested_start,end=analysis.period_end || analysis.coverage?.requested_end;
  if(validMonth({start,end,kind:"MONTH",time_zone:"Asia/Shanghai"}))return `${billMonthLabel(start,locale)}${locale==="en" ? " statement" : "账单"}`;
  if(parsedInstant(start)!==null && parsedInstant(end)!==null)return `${billDateLabel(start,locale)} – ${billDateLabel(end,locale,true)}`;
  return locale==="en" ? "Available statement" : "已有账单";
}
export function billFallbackIntro(analysis: BillAnalysis, locale: CustomerLocale): string | null {
  const fallback=billPeriodFallback(analysis);if(!fallback)return null;
  const crossYear=shanghaiParts(fallback.requested.start)?.year!==shanghaiParts(fallback.displayed.start)?.year;
  const requested=billMonthLabel(fallback.requested.start,locale,crossYear),displayed=billMonthLabel(fallback.displayed.start,locale,crossYear);
  if(fallback.reason==="REQUESTED_PERIOD_ONLY_MOVEMENTS")return locale==="en"
    ? `The available ${requested} records are funds movements. Here are your recorded ${displayed} spending transactions.`
    : `${requested}目前查到的是资金往来，先给你看${displayed}已有消费账单。`;
  return locale==="en" ? `${requested}'s statement has not been updated yet. Here are your available ${displayed} transactions.`
    : `${requested}账单暂未更新，先给你看${displayed}已有账单。`;
}
