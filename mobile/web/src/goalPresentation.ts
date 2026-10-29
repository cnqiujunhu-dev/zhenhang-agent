import type { CustomerLocale } from "./customerLanguage";
import { formatMoneyMinor } from "./typedMoney.ts";

type Data = Record<string, unknown>;
const record = (value: unknown): Data | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Data : null;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value);
const nonnegative = (value: unknown): number | null => integer(value) && value >= 0 ? value : null;

// A presentation of returned facts, not a funding calculator. extensions.py
// clamps portfolio monthly capacity to zero; required amounts and gaps are
// also nonnegative minor units. Scenario net cash flow can be negative.
export function goalPresentation(locale: CustomerLocale) {
  const english = locale === "en";
  const copy = english ? {
    portfolioTitle: "Combined goal funding",
    sharedFunds: "All goals share the same cash and monthly surplus. Check their combined funding needs before planning each goal.",
    monthlyGap: "Combined monthly funding gap",
    currentCashGap: "Combined current-cash gap",
    requiredMonthly: "Combined monthly reserve needed",
    monthlyCapacity: "Baseline monthly capacity",
    portfolioUnknown: "Combined funding data is incomplete. Missing amounts do not mean a zero gap.",
    portfolioEmpty: "No goals are recorded yet. Set a goal amount and date before reviewing combined funding.",
    portfolioSource: "These are the returned combined estimates. This query did not reserve funds or make a payment.",
    totalCash: "Total cash", reservedCash: "Reserved for goals", spendableCash: "After goal and emergency reserves",
    reserveExplanation: "Goal reserves reduce spendable funds, not total cash.",
    missingGoals: "Goals were not returned.", emptyGoals: "No savings goals yet. Describe the goal, amount and date.",
    missingName: "Name not returned", target: "Target", reserved: "Reserved", remaining: "Still needed",
    progressUnknown: "Progress was not returned.", targetDate: "Target date", monthlyUnknown: "Monthly plans need review.",
    reserveDraftLabel: "Ask about reserve adjustment", forecastTitle: "Cash-flow scenarios",
    forecastUnknown: "Cash-flow forecast was not returned.", horizonUnknown: "Forecast period not returned",
    sampleInsufficient: "Historical data is insufficient. Review income assumptions first.",
    sampleRecorded: "Estimated from posted historical transactions.", sampleUnknown: "Historical sample coverage needs review.",
    scenarioUnknown: "Scenario amounts were not returned.",
    forecastBoundary: "Forecasts do not guarantee future income or funds. Goals share the same cash; reserving or releasing it requires review and confirmation.",
    setupLabel: "Ask about savings goals", setupDraft: "How do I set up a savings goal? Advice only; do not make any changes.",
    notReturned: "Not returned",
  } : {
    portfolioTitle: "组合目标筹资",
    sharedFunds: "所有目标共用同一份现金与月度结余，请先核对组合资金需求，再安排每个目标。",
    monthlyGap: "组合月度筹资缺口", currentCashGap: "组合当前现金缺口",
    requiredMonthly: "组合每月需预留", monthlyCapacity: "基准月度筹资能力",
    portfolioUnknown: "组合资金数据尚未完整返回，不能把缺失金额当作缺口为零。",
    portfolioEmpty: "尚未设置目标，请先明确目标金额与日期，再核对组合筹资安排。",
    portfolioSource: "以上是本次返回的组合估算；本次查询没有预留资金或付款。",
    totalCash: "现金总额", reservedCash: "目标已预留", spendableCash: "扣除目标与应急金后",
    reserveExplanation: "目标预留减少可支出额度，但不代表现金已花掉。",
    missingGoals: "目标列表尚未返回。", emptyGoals: "尚未设置储蓄目标。可以先说明目标名称、金额与期限。",
    missingName: "名称未返回", target: "目标", reserved: "已预留", remaining: "尚需",
    progressUnknown: "进度尚未返回。", targetDate: "目标日期", monthlyUnknown: "月度安排仍需核对。",
    reserveDraftLabel: "准备调整预留", forecastTitle: "现金流情景",
    forecastUnknown: "现金流预测尚未返回。", horizonUnknown: "预测期间未返回",
    sampleInsufficient: "历史样本不足，先核对收入假设。", sampleRecorded: "基于已发生的历史流水估算。",
    sampleUnknown: "历史样本范围仍需核对。", scenarioUnknown: "情景金额尚未返回。",
    forecastBoundary: "预测不保证未来收入或可用额度；多个目标共用同一现金，预留与释放仍需另行核对确认。",
    setupLabel: "了解如何设置目标", setupDraft: "如何设置储蓄目标？仅咨询，不办理操作",
    notReturned: "未返回",
  };

  const sample = (status: unknown) => status === "INSUFFICIENT_HISTORY" ? copy.sampleInsufficient : status === "SUFFICIENT_FOR_SCENARIO" ? copy.sampleRecorded : copy.sampleUnknown;
  const feasibility = (row: Data, sampleStatus: unknown) => {
    if (row.overdue === true || row.status === "EXPIRED" || row.feasibility === "OVERDUE_REVIEW_REQUIRED")
      return english ? "The goal is overdue; review its date and funding plan." : "已到期，需要重新核对日期与筹资安排。";
    if (row.status !== "ACTIVE") return english ? "Goal status needs review." : "目标状态需要核对。";
    const labels: Record<string, string> = english ? {
      FUNDED_NOT_SPENT: "Reserved in full, not spent",
      SAMPLE_INSUFFICIENT: "Insufficient history; review income assumptions",
      DUE_DATE_REQUIRED: "A target date is needed",
      MONTHLY_GAP: "A monthly funding gap was returned",
    } : {
      FUNDED_NOT_SPENT: "已预留到位，尚未支出",
      SAMPLE_INSUFFICIENT: "历史样本不足，需核对收入假设",
      DUE_DATE_REQUIRED: "需要明确目标期限",
      MONTHLY_GAP: "存在月度筹资缺口",
    };
    // The server checks current cash before historical forecast sufficiency.
    // Missing history invalidates future-income estimates, not returned cash.
    if (row.feasibility === "FEASIBLE_FROM_CURRENT_CASH")
      return english ? "Recorded single-goal cash coverage; review combined funding." : "记录的单目标现金可覆盖，仍需核对组合筹资。";
    if (row.feasibility === "FEASIBLE_BASELINE") {
      if (sampleStatus !== "SUFFICIENT_FOR_SCENARIO") return sample(sampleStatus);
      return english ? "Single-goal baseline; review combined funding." : "单目标基准情景，仍需核对组合筹资。";
    }
    return labels[String(row.feasibility)] || (english ? "Feasibility needs review" : "可行性待核对");
  };

  return {
    ...copy, sample, feasibility,
    whatIf: (value: unknown, goals: Data[] | null) => {
      const scenario = record(value);
      if (!scenario || scenario.calculation_version !== "goal-monthly-saving-what-if-v1"
        || scenario.currency !== "CNY" || scenario.basis !== "USER_STATED_MONTHLY_SAVING"
        || scenario.rounding !== "CEILING_TO_WHOLE_MONTH" || scenario.read_only !== true
        || scenario.changes_applied !== false || scenario.guaranteed !== false
        || typeof scenario.goal_name !== "string" || !scenario.goal_name.trim()) return null;
      const goal = goals?.find(row=>row.goal_id === scenario.goal_id && row.goal_name === scenario.goal_name
        && integer(row.version) && row.version === scenario.goal_version && row.remaining_minor === scenario.remaining_minor);
      const fields = ["remaining_minor","monthly_saving_minor","improved_monthly_saving_minor"];
      if (!goal || !fields.every(key=>integer(scenario[key]) && (scenario[key] as number) >= 0)) return null;
      const remaining=scenario.remaining_minor as number, monthly=scenario.monthly_saving_minor as number,
        delta=scenario.monthly_saving_delta_minor as number, improved=scenario.improved_monthly_saving_minor as number;
      const ceilingMonths=(minor:number,perMonth:number)=>Number((BigInt(minor)+BigInt(perMonth)-1n)/BigInt(perMonth));
      const totalMode=scenario.monthly_saving_total_minor!==undefined && scenario.monthly_saving_total_minor!==null;
      if (!integer(delta) || monthly > 10 ** 13 || Math.abs(delta)>10 ** 13 || improved!==monthly+delta) return null;
      if (totalMode ? !integer(scenario.monthly_saving_total_minor) || scenario.monthly_saving_total_minor<0 || scenario.monthly_saving_total_minor>10 ** 13 || improved!==scenario.monthly_saving_total_minor
        : delta<=0) return null;
      const proposedReachable=improved>0 || remaining===0;
      if (totalMode || scenario.proposed_plan_reachable!==undefined) {
        if (scenario.proposed_plan_reachable!==proposedReachable) return null;
      }
      const expectedImproved=remaining===0 ? 0 : improved>0 ? ceilingMonths(remaining,improved) : null;
      if (scenario.improved_months!==expectedImproved) return null;
      const baselineReachable=monthly>0 || remaining===0;
      if (scenario.baseline_reachable !== baselineReachable) return null;
      if (baselineReachable ? !integer(scenario.baseline_months) || scenario.baseline_months !== (remaining===0 ? 0 : ceilingMonths(remaining,monthly))
        || (proposedReachable ? !integer(scenario.months_saved) || scenario.months_saved!==scenario.baseline_months-(scenario.improved_months as number) : scenario.months_saved!==null)
        : scenario.baseline_months !== null || scenario.months_saved !== null) return null;
      return {goalName:scenario.goal_name,baselineMonths:scenario.baseline_months as number | null,improvedMonths:scenario.improved_months as number|null,
        monthsSaved:scenario.months_saved as number | null,monthly,delta,improved,remaining,newTotal:totalMode?scenario.monthly_saving_total_minor as number:null};
    },
    portfolio: (value: unknown) => {
      const data = record(value);
      return {
        requiredMonthly: nonnegative(data?.required_monthly_minor),
        monthlyCapacity: nonnegative(data?.monthly_capacity_baseline_minor),
        monthlyGap: nonnegative(data?.monthly_gap_minor),
        currentCashGap: nonnegative(data?.current_cash_gap_minor),
      };
    },
    amount: (value: unknown, allowNegative = false) => integer(value) && (allowNegative || value >= 0)
      ? `${formatMoneyMinor(value, { currency: "CNY" })}${english ? " CNY" : " 人民币"}` : copy.notReturned,
    date: (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value))
      ? new Intl.DateTimeFormat(locale, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai" }).format(new Date(value)) : copy.notReturned,
    progressLabel: (goalName: string) => english ? `Reserved progress for ${goalName}` : `${goalName}已预留进度`,
    progressText: (percentage: string) => english ? `Reserved progress: ${percentage}%` : `已预留进度 ${percentage}%`,
    progressValue: (percentage: string) => english ? `${percentage}% reserved` : `已预留 ${percentage}%`,
    monthlyText: (value: string) => english ? `Monthly reserve needed for the recorded date: ${value}` : `按当前期限每月需预留 ${value}`,
    horizon: (days: unknown) => integer(days) && days > 0 ? english ? `Future ${days} days` : `未来 ${days} 天` : copy.horizonUnknown,
    scenarios: english ? [["conservative", "Conservative scenario net surplus"], ["baseline", "Baseline scenario net surplus"], ["less_conservative", "Less-conservative scenario net surplus"]] : [["conservative", "保守情景净结余"], ["baseline", "基准情景净结余"], ["less_conservative", "较宽松情景净结余"]],
    // English uses an advisory question; the existing Chinese draft route is
    // unchanged. Neither callback executes a reservation or supplies an amount.
    reserveDraft: (goalName: string) => english ? `How can I adjust the reserved amount for savings goal ${goalName}? Advice only; do not reserve or release money.` : `为${goalName}预留资金`,
  };
}
