import type { CustomerLocale } from "./customerLanguage";
import { formatMoneyMinor } from "./typedMoney.ts";
import { customerName } from "./customerPresentation.ts";

type Data = Record<string, unknown>;
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

// Localized descriptions of returned extension facts. English extension writes
// are not registered in EN_ACTION_PATTERNS, so their shortcuts ask for advice.
// Names, permissions, identifiers and financial records are never translated.
export function extensionQueryPresentation(locale: CustomerLocale) {
  const english = locale === "en";
  const missing = english ? "Not returned" : "未返回";
  const family = english ? {
    missingIdentity: "Identity not returned", missingName: "Name not returned",
    helper: "Family helper", owner: "Account owner", unknownRole: "Identity needs review",
    ownerScope: "You can ask about family permissions and review their recorded scope. Changes still require the owner's review and confirmation.",
    helperScope: "Assistance is limited to active delegations. Helpers cannot pay or lock cards for the owner.",
    unavailablePermissions: "No actionable family permissions were returned.",
    missingPermissions: "Permissions were not returned. Check the current identity.",
    delegations: "Family delegations", missingDelegations: "Delegations were not returned.", emptyDelegations: "No family delegations.",
    valid: "Valid", expired: "Expired / revoked", unknownValidity: "Validity not returned",
    unknownScope: "Scope needs review", missingScopes: "Scope not returned", monthlyLimit: "Monthly aggregate limit",
    proposalLimit: "Per-proposal limit", unknownLimitPeriod: "Limit period needs review", monthlyUsed: "Used this month",
    expires: "Valid until", expiredBoundary: "Expired access cannot be reused.", authorizedBalances: "Authorized balances",
    proposalsBoundary: "Saving a proposal does not execute a bank action.",
    confirmationBoundary: "The owner independently reviews and confirms transfers or card actions. Expired or revoked delegations cannot be reused.",
    setupLabel: "Ask about family permissions",
    setupDraft: "How can I set up family assistance permissions? Advice only; do not grant or revoke access or perform a bank action.",
  } : {
    missingIdentity: "当前身份未返回", missingName: "名称未返回",
    helper: "家庭协助者", owner: "账户本人", unknownRole: "身份待核对",
    ownerScope: "你可以提出家庭授权需求，再核对确认。",
    helperScope: "协助范围以每条有效委托为准，不可直接代本人付款或锁卡。",
    unavailablePermissions: "当前未返回可办理的家庭协助权限。",
    missingPermissions: "权限信息尚未返回，请先核对当前身份。",
    delegations: "家庭委托", missingDelegations: "委托记录尚未返回。", emptyDelegations: "当前没有家庭委托。",
    valid: "有效", expired: "已失效", unknownValidity: "有效性未返回",
    unknownScope: "范围需核对", missingScopes: "授权范围未返回", monthlyLimit: "每月累计上限",
    proposalLimit: "单笔建议上限", unknownLimitPeriod: "额度周期待核对", monthlyUsed: "本月已用",
    expires: "有效至", expiredBoundary: "失效后不可继续按旧范围协助。", authorizedBalances: "已授权余额",
    proposalsBoundary: "建议保存不代表银行操作已执行。",
    confirmationBoundary: "转账或卡片操作仍须本人独立审阅并确认。撤销或到期的委托不能继续使用。",
    setupLabel: "了解授权方式", setupDraft: "如何设置家庭协助权限？仅咨询，不办理银行操作",
  };
  const scopeLabels: Record<string, string> = english
    ? { READ_BALANCE: "View authorized balance", PROPOSE_TRANSFER: "Propose a transfer", PROPOSE_CARD_LOCK: "Propose a card lock" }
    : { READ_BALANCE: "查看授权余额", PROPOSE_TRANSFER: "提出转账建议", PROPOSE_CARD_LOCK: "提出锁卡建议" };
  const carbon = english ? {
    missingName: "Name not returned", points: "Current low-carbon points", activities: "Low-carbon activities",
    missingActivities: "Activities were not returned.", emptyActivities: "No recorded low-carbon activities.",
    claimed: "Claimed", unsupported: "Cannot yet calculate", claimable: "Recorded as claimable", notClaimable: "Not claimable",
    unknownClaim: "Claim conditions need review", unknownMode: "Activity type needs review", missingDistance: "Distance not returned",
    unsupportedNotice: "This activity is not supported by the current method. No points will be granted.",
    avoidedEmissions: "Estimated avoided emissions", estimatedPoints: "Corresponding points",
    claimLabel: "Ask about claiming points", rewards: "Available rewards", missingRewards: "Rewards were not returned.",
    emptyRewards: "No rewards to display.", outOfStock: "Out of stock", rewardEligible: "Meets recorded reward conditions",
    notRedeemable: "Not redeemable", unknownEligibility: "Reward conditions need review", pointsUnit: "points", remainingStock: "Remaining stock",
    redeemLabel: "Ask about reward redemption", redeemedRewards: "Redeemed rewards", redeemed: "Redeemed", unknownRedemption: "Status needs review",
    boundary: "Emission savings are estimates, not certified carbon assets. Rewards are not cash received. An activity cannot be claimed twice.",
  } : {
    missingName: "名称未返回", points: "当前低碳积分", activities: "低碳行为",
    missingActivities: "行为记录尚未返回。", emptyActivities: "当前没有已记录的低碳行为。",
    claimed: "已领取", unsupported: "暂不能核算", claimable: "可领取", notClaimable: "暂不可领取",
    unknownClaim: "领取条件待核对", unknownMode: "行为方式待核对", missingDistance: "里程未返回",
    unsupportedNotice: "当前方法尚不支持该行为，不发放积分。",
    avoidedEmissions: "估算避免排放", estimatedPoints: "对应积分",
    claimLabel: "准备领取积分", rewards: "可兑换权益", missingRewards: "权益列表尚未返回。",
    emptyRewards: "当前没有可展示的权益。", outOfStock: "已兑完", rewardEligible: "可准备兑换",
    notRedeemable: "暂不可兑换", unknownEligibility: "兑换条件待核对", pointsUnit: "积分", remainingStock: "剩余",
    redeemLabel: "准备兑换", redeemedRewards: "已兑换权益", redeemed: "已兑换", unknownRedemption: "状态待核对",
    boundary: "减排量为估算值，不是核证碳资产；权益不代表现金到账。同一行为不能重复领取。",
  };
  const modeLabels: Record<string, string> = english
    ? { WALK: "Walking", BICYCLE: "Cycling", METRO: "Metro", BUS: "Bus" }
    : { WALK: "步行", BICYCLE: "骑行", METRO: "地铁", BUS: "公交" };

  return {
    family: { ...family, scopeLabels,
      proposals: (count: number) => english ? `${count} recorded proposals. ${family.proposalsBoundary}` : `已登记${count}条协助建议；${family.proposalsBoundary}` },
    carbon: { ...carbon, modeLabels,
      claimStatus: (row: Data) => row.claimed === true ? carbon.claimed : row.method_supported === false ? carbon.unsupported
        : row.claimable === true && row.claimed === false && row.method_supported === true ? carbon.claimable
        : row.claimable === false && row.claimed === false && row.method_supported === true ? carbon.notClaimable : carbon.unknownClaim,
      // Advisory context uses the same legacy-label cleanup as the visible
      // card. Chinese operational drafts retain the canonical recorded name.
      claimDraft: (rawName: string) => english ? `How can I claim carbon points for ${customerName(rawName)}? Advice only; do not claim points or change records.` : `核算碳积分：${rawName}`,
      redeemDraft: (rawName: string) => english ? `How can I redeem the low-carbon reward ${customerName(rawName)}? Advice only; do not redeem it or change my points.` : `兑换权益：${rawName}` },
    count: (value: unknown) => nonnegative(value) ? String(value) : missing,
    // Family transfer proposals and authorized balances use the current CNY
    // extension contract. No foreign-currency amount is inferred or summed.
    amount: (value: unknown) => nonnegative(value) ? `${formatMoneyMinor(value, { currency: "CNY" })}${english ? " CNY" : " 人民币"}` : missing,
    date: (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value))
      ? new Intl.DateTimeFormat(locale, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai" }).format(new Date(value)) : missing,
    distance: (value: unknown) => nonnegative(value) ? `${(value / 1000).toFixed(1)} km` : carbon.missingDistance,
    avoidedEmissions: (value: unknown) => nonnegative(value) ? `${(value / 1000).toFixed(3)} kg` : missing,
  };
}
