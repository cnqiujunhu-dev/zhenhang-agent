import type { TaskTrace } from "./types";

export const universalScene = {
  id: "assistant",
  label: "智能助手",
  icon: "spark",
  description: "直接表达需求，自动识别领域与跨域编排",
} as const;
/** Optional, read-only UI context. It is never an intent or task payload field. */
export const agentCapabilities = [
  {
    id: "bill",
    label: "账单洞察",
    icon: "bill",
    description: "账单、支出图表与月度年度报告",
  },
  {
    id: "transfer",
    label: "智能转账",
    icon: "transfer",
    description: "收款目录、身份联系人与收付款记录",
  },
  {
    id: "wealth",
    label: "财富管理",
    icon: "wealth",
    description: "产品资料、持仓与独立风险评估",
  },
  {
    id: "card",
    label: "卡片服务",
    icon: "card",
    description: "本人卡片、双币钱包与汇率资料",
  },
  {
    id: "subscription",
    label: "订阅管理",
    icon: "subscription",
    description: "持续扣款与周期分析",
  },
  {
    id: "cross",
    label: "联动任务",
    icon: "workflow",
    description: "一次表达多个目标，由 Agent 规划依赖",
  },
  {
    id: "fraud",
    label: "反诈守护",
    icon: "shield",
    description: "风险线索与行动边界",
  },
  {
    id: "family",
    label: "家庭适老",
    icon: "family",
    description: "授权投影、协助提案与本人审阅",
  },
  {
    id: "goals",
    label: "目标现金流",
    icon: "target",
    description: "目标预留与现金流情景",
  },
  {
    id: "carbon",
    label: "低碳权益",
    icon: "leaf",
    description: "授权低碳行为与个人权益",
  },
  { id: "assets", label: "资产持仓", icon: "wealth", description: "理财、股票和贵金属持仓与参考价值" },
  { id: "membership", label: "客户与卡片等级", icon: "card", description: "分别查看客户等级、卡片等级和风险测评" },
  { id: "benefits", label: "优惠活动", icon: "leaf", description: "活动条件、有效期与本人适用资格" },
  { id: "preferences", label: "个人偏好", icon: "spark", description: "查看、修改或撤回消费和出行偏好" },
] as const;
export function resourceContextFor(id: string, helper: boolean) {
  if (helper) return "family";
  return agentCapabilities.some((item) => item.id === id) ? id : "overview";
}
export function canPrepareResourceDraft(context: {
  busy: boolean;
  pendingAcceptance: boolean;
  currentMessage: string;
  proposedMessage: string;
}) {
  const proposed = context.proposedMessage.trim();
  const current = context.currentMessage.trim();
  return (
    Boolean(proposed) &&
    !context.busy &&
    !context.pendingAcceptance &&
    (!current || current === proposed)
  );
}
export const readonlySuggestions = [
  {
    id: "bills",
    label: "分析本月账单",
    message: "分析我的本月账单",
    icon: "bill",
  },
  {
    id: "subscriptions",
    label: "查询自动续费",
    message: "查询我的自动续费订阅清单",
    icon: "subscription",
  },
  {
    id: "combined",
    label: "查卡片和订阅",
    message: "先查询我的卡片状态和限额，再查询自动续费订阅清单",
    icon: "workflow",
  },
] as const;

/** Scene selection is presentation only. The backend accepts the natural-language request. */
export function taskAcceptancePayload(message: string) {
  return { message: message.trim() };
}
export function canUseReadonlySuggestion(context: {
  authenticated: boolean;
  hasBankAccount: boolean;
  helper: boolean;
  busy: boolean;
  activeTask: boolean;
  pendingAcceptance: boolean;
  hasTrace: boolean;
  hasDraft: boolean;
}) {
  return (
    context.authenticated &&
    context.hasBankAccount &&
    !context.helper &&
    !context.busy &&
    !context.activeTask &&
    !context.pendingAcceptance &&
    !context.hasTrace &&
    !context.hasDraft
  );
}
const domainNames: Record<string, string> = {
  BILLS: "账单",
  BILL: "账单",
  TRANSFER: "转账与收款",
  WEALTH: "理财",
  CARDS: "卡片",
  CARD: "卡片",
  SUBSCRIPTIONS: "订阅",
  SUBSCRIPTION: "订阅",
  FRAUD: "反诈",
  FAMILY: "家庭协助",
  GOALS: "目标现金流",
  GOAL: "目标现金流",
  CARBON: "低碳权益",
  FX: "汇率与换汇",
};
function domainFromAction(action: string) {
  if (
    ["TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST", "FX_TRANSFER"].includes(
      action,
    )
  )
    return "TRANSFER";
  if (action === "BILL_ANALYSIS") return "BILLS";
  if (["FX_QUOTE", "FX_EXCHANGE"].includes(action)) return "FX";
  for (const [prefix, domain] of [
    ["CARD_", "CARDS"],
    ["WEALTH_", "WEALTH"],
    ["SUBSCRIPTION_", "SUBSCRIPTIONS"],
    ["FRAUD_", "FRAUD"],
    ["FAMILY_", "FAMILY"],
    ["GOAL_", "GOALS"],
    ["CARBON_", "CARBON"],
  ])
    if (action.startsWith(prefix)) return domain;
  return null;
}
export function detectedTaskDomains(trace: TaskTrace) {
  const declared = (trace.domains || []).filter(
    (value) => typeof value === "string" && value.length,
  );
  const codes = declared.length
    ? declared
    : (trace.nodes || [])
        .map((node) => domainFromAction(node.action))
        .filter((value): value is string => Boolean(value));
  const labels = [
    ...new Set(codes.map((code) => domainNames[code.toUpperCase()] || code)),
  ];
  return {
    labels,
    source: declared.length ? "BACKEND_DECLARED" : "ACTUAL_NODES",
    crossDomain: trace.cross_domain === true || labels.length > 1,
    multiStep: trace.multi_step === true || trace.nodes.length > 1,
  };
}
