// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText, getCustomerLocale, translateCustomerText, localizeFinancialValue, type CustomerLocale } from "./customerLanguage";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ModelDiagnostics } from "./ModelDiagnostics";
import { currentCardIdentity } from "./cardIdentity";
import "./bankCardFace.css";
import { customerName, customerNarrative } from "./customerPresentation";
import { customerDataEntries, customerActionLabel } from "./customerTaskPresentation";
import {
  formatMoneyMinor,
  currencyForMinorField,
  type MoneyOptions,
} from "./typedMoney";
import type {
  BillAnalysis,
  Card,
  JsonValue,
  LayaAdvice,
  TaskTrace,
  TraceNode,
} from "./types";

export type IconName =
  | "spark"
  | "home"
  | "bill"
  | "transfer"
  | "wealth"
  | "card"
  | "subscription"
  | "workflow"
  | "arrow"
  | "check"
  | "clock"
  | "close"
  | "shield"
  | "bell"
  | "chevron"
  | "refresh"
  | "history"
  | "info"
  | "send"
  | "eye"
  | "lock"
  | "menu"
  | "download"
  | "leaf"
  | "target"
  | "family";
const dialogInertLocks = new WeakMap<HTMLElement, { count: number; original: boolean }>();
const dialogOwners = new WeakMap<HTMLElement, { owners: symbol[]; tabIndex: string | null }>();
let dialogScrollLocks = 0;
let dialogOriginalOverflow = "";
let dialogGroupReturnFocus: HTMLElement | null = null;
const dialogFocusableSelector = 'a[href], button, input, select, textarea, summary, [contenteditable="true"], [tabindex]';
function dialogFocusableElements(dialog: HTMLElement) {
  return Array.from(dialog.querySelectorAll<HTMLElement>(dialogFocusableSelector))
    .filter(element => element.tabIndex >= 0 && !element.matches(":disabled") && !element.closest('[inert], [hidden]') && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden");
}
export function useDialogFocus(active: boolean, options: { onDismiss?: () => void; dialogRef?: { current: HTMLElement | null } } = {}) {
  const dismissRef = useRef(options.onDismiss);
  dismissRef.current = options.onDismiss;
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]');
    const dialog = options.dialogRef ? options.dialogRef.current : dialogs[dialogs.length - 1];
    if (!dialog) return;
    const higherDialogs = Array.from(dialogs).slice(Array.from(dialogs).indexOf(dialog) + 1);
    if (dialogScrollLocks === 0) { dialogOriginalOverflow = document.body.style.overflow; dialogGroupReturnFocus = previous; }
    dialogScrollLocks += 1;
    document.body.style.overflow = "hidden";
    const owner = Symbol("dialog-focus-owner");
    const ownership = dialogOwners.get(dialog) || { owners: [], tabIndex: dialog.getAttribute("tabindex") };
    ownership.owners.push(owner); dialogOwners.set(dialog, ownership);
    if (ownership.tabIndex === null) dialog.tabIndex = -1;
    const inertSiblings: HTMLElement[] = [];
    // Hide only branches outside the dialog, never an ancestor containing it.
    let branch: HTMLElement = dialog;
    while (branch.parentElement) {
      for (const sibling of Array.from(branch.parentElement.children)) {
        if (!(sibling instanceof HTMLElement) || sibling === branch || ["SCRIPT", "STYLE", "LINK"].includes(sibling.tagName)) continue;
        // A lower modal must not lock a higher sibling mounted in the same commit.
        if (higherDialogs.some(higher => sibling.contains(higher))) continue;
        const lock = dialogInertLocks.get(sibling) || { count: 0, original: sibling.inert };
        lock.count += 1; dialogInertLocks.set(sibling, lock);
        inertSiblings.push(sibling);
        sibling.inert = true;
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    const focusables = () => dialogFocusableElements(dialog);
    if (dialogs[dialogs.length - 1] === dialog) (dialog.querySelector<HTMLElement>("[data-dialog-initial-focus]") || focusables()[0] || dialog).focus({ preventScroll: true });
    const trap = (event: KeyboardEvent) => {
      // Only the currently topmost modal owns keyboard input.
      const openDialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]');
      if (openDialogs[openDialogs.length - 1] !== dialog || dialogOwners.get(dialog)?.owners.at(-1) !== owner) return;
      if (event.key === "Escape" && dismissRef.current) {
        event.preventDefault();
        event.stopPropagation();
        dismissRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusables();
      const first = elements[0],
        last = elements[elements.length - 1];
      if (!first) {
        event.preventDefault();
        dialog.focus({ preventScroll: true });
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          !dialog.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          !dialog.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      for (const element of inertSiblings) {
        const lock = dialogInertLocks.get(element);
        if (!lock) continue;
        lock.count -= 1;
        if (lock.count === 0) { element.inert = lock.original; dialogInertLocks.delete(element); }
      }
      ownership.owners = ownership.owners.filter(token => token !== owner);
      if (!ownership.owners.length) {
        if (ownership.tabIndex === null) dialog.removeAttribute("tabindex");
        dialogOwners.delete(dialog);
      }
      dialogScrollLocks -= 1;
      if (dialogScrollLocks === 0) document.body.style.overflow = dialogOriginalOverflow;
      const canFocus = (element: HTMLElement | null) => Boolean(element?.isConnected && element !== document.body && !element.closest('[inert], [hidden]') && element.getClientRects().length > 0);
      const remainingTop = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]'))
        .filter(element => (element !== dialog || ownership.owners.length > 0) && !element.closest('[inert], [hidden]') && element.getClientRects().length > 0).at(-1);
      if (remainingTop) {
        // Removing a lower layer must not steal focus from the active top layer.
        if (!remainingTop.contains(document.activeElement)) {
          const next = canFocus(previous) && remainingTop.contains(previous) ? previous
            : remainingTop.querySelector<HTMLElement>("[data-dialog-initial-focus]") || dialogFocusableElements(remainingTop)[0] || remainingTop;
          next?.focus({ preventScroll: true });
        }
      } else {
        const next = canFocus(previous) ? previous : dialogScrollLocks === 0 && canFocus(dialogGroupReturnFocus) ? dialogGroupReturnFocus : null;
        next?.focus({ preventScroll: true });
      }
      if (dialogScrollLocks === 0) dialogGroupReturnFocus = null;
    };
  }, [active, options.dialogRef]);
}
const paths: Record<IconName, ReactNode> = {
  spark: (
    <>
      <path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6L12 3Z" />
      <path d="M20 3v4M18 5h4" />
    </>
  ),
  home: (
    <>
      <path d="m3 10 9-7 9 7v10H3Z" />
      <path d="M9 20v-7h6v7" />
    </>
  ),
  bill: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 7h6M9 11h6M9 15h3" />
    </>
  ),
  transfer: (
    <>
      <path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4" />
    </>
  ),
  wealth: (
    <>
      <path d="M4 20V11m6 9V7m6 13V3M3 20h18" />
      <path d="m4 7 6-4" />
    </>
  ),
  card: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="3" />
      <path d="M3 10h18M7 15h4" />
    </>
  ),
  subscription: (
    <>
      <path d="M4 10a8 8 0 0 1 14-5l2 2M20 4v3h-3M20 14a8 8 0 0 1-14 5l-2-2M4 20v-3h3" />
    </>
  ),
  workflow: (
    <>
      <rect x="3" y="3" width="6" height="6" rx="1.5" />
      <rect x="15" y="15" width="6" height="6" rx="1.5" />
      <rect x="3" y="15" width="6" height="6" rx="1.5" />
      <path d="M6 9v6M9 6h9v9" />
    </>
  ),
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  check: <path d="m5 12 4 4L19 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  close: <path d="m6 6 12 12M6 18 18 6" />,
  shield: (
    <>
      <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" />
      <path d="m8 12 3 3 5-6" />
    </>
  ),
  bell: (
    <>
      <path d="M18 8a6 6 0 0 0-12 0v7l-2 3h16l-2-3V8ZM10 21h4" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  refresh: (
    <>
      <path d="M20 10a8 8 0 1 0-2 8M20 4v6h-6" />
    </>
  ),
  history: (
    <>
      <path d="M3 10a9 9 0 1 1 2 8M3 4v6h6" />
      <path d="M12 7v5l4 2" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7h.01" />
    </>
  ),
  send: (
    <>
      <path d="m3 3 18 9-18 9 3-9-3-9ZM6 12h15" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
    </>
  ),
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  leaf: (
    <>
      <path d="M20 3c-8-1-15 3-15 10a6 6 0 0 0 6 6c7 0 10-8 9-16Z" />
      <path d="M3 21 15 9M9 15h6M12 12V7" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <path d="m12 12 7-7M17 3l2 2 2 2" />
    </>
  ),
  family: (
    <>
      <circle cx="8" cy="8" r="3" />
      <path d="M2 20v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 5 5v1" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
    </>
  ),
};
export function Icon({
  name,
  size = 20,
  className = "",
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
export function Brand({ compact = false }: { compact?: boolean }) {
  const { t: customerUiText } = useCustomerLanguage();
  return (
    <div className="brand">
      <span className="brand-mark">
        <svg viewBox="0 0 36 36" fill="none" aria-hidden="true">
          <path
            d="M9 27V15m7 12V9m7 18V13m-15 18h22"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
          />
          <circle cx="28" cy="8" r="2" fill="currentColor" />
        </svg>
      </span>
      {!compact && (
        <span>
          <strong><CustomerText text="圳行" /><span>Agent</span>
          </strong>
          <small><CustomerText text="让金融服务自然发生" /></small>
        </span>
      )}
    </div>
  );
}
export const stateLabels: Record<string, string> = {
  QUEUED: "等待处理",
  UNDERSTANDING: "理解需求",
  PLANNING: "编排计划",
  PLANNED: "计划已生成",
  POLICY_CHECKED: "校验完成",
  NEED_CLARIFICATION: "等待补充",
  AWAITING_CONFIRMATION: "待你确认",
  AUTHORIZED: "已获授权",
  EXECUTING: "正在执行",
  UNKNOWN: "正在查证",
  PARTIAL: "部分已完成",
  SUCCEEDED: "已完成",
  FAILED: "执行失败",
  CLOSED: "已关闭",
  COMPENSATING: "正在恢复",
  PENDING: "等待前置步骤",
  READY: "准备就绪",
  RUNNING: "正在执行",
  WAITING: "等待约定时间",
  WAITING_TIME: "等待约定时间",
  WAITING_CONFIRMATION: "待你确认",
  SKIPPED: "已跳过",
  CANCELLED: "已撤销",
  PAID: "已付款",
  COLLECTING: "收款中",
  COMPLETED: "已完成",
  SCHEDULED: "已预约",
  POSTED: "已入账",
  BUY: "申购",
  REDEEM: "赎回",
  BANK_MANDATE: "银行代扣",
  MERCHANT_SIM: "商户扣款",
  MERCHANT_TOKEN: "商户侧订阅",
  ACTIVE: "正常",
  LOCKED: "已锁定",
  LOST: "已挂失",
  CANCELLED_BY_USER: "已取消",
  CANCELED: "已取消",
  INACTIVE: "未启用",
  EXPIRED: "已过期",
  REVOKED: "已撤销",
  PENDING_OWNER_APPROVAL: "等待本人审阅",
  OWNER_TASK_CREATED: "本人任务已生成",
  REJECTED: "已拒绝",
  ISSUED_SIMULATION: "权益已发放",
};
export function toneOf(state: string) {
  if (
    [
      "SUCCEEDED",
      "ACTIVE",
      "AUTHORIZED",
      "POLICY_CHECKED",
      "PAID",
      "COMPLETED",
    ].includes(state)
  )
    return "success";
  if (["FAILED", "LOST"].includes(state)) return "danger";
  if (
    [
      "AWAITING_CONFIRMATION",
      "WAITING_CONFIRMATION",
      "NEED_CLARIFICATION",
      "PARTIAL",
      "UNKNOWN",
      "LOCKED",
      "WAITING",
      "WAITING_TIME",
    ].includes(state)
  )
    return "warning";
  if (["CLOSED", "CANCELLED", "CANCELED", "SKIPPED"].includes(state))
    return "neutral";
  return "working";
}
export function StateBadge({
  state,
  label,
}: {
  state: string;
  label?: string;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  return (
    <span className={`state-badge ${toneOf(state)}`} data-state={state}>
      <i />
      {customerUiText(label || stateLabels[state] || state)}
    </span>
  );
}
export function money(minor: number, options: MoneyOptions = {}) {
  return formatMoneyMinor(minor, options);
}
export function dateText(date?: string, includeYear = false) {
  if (!date) return "—";
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat(getCustomerLocale(), {
    ...(includeYear ? { year: "numeric" as const } : {}),
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(parsed);
}
const fieldLabels: Record<string, string> = {
  merchant: "商户", monthly_price_minor: "月费", total_amount_minor: "本次总价", charged_minor: "已扣款",
  months: "付费月数", auto_renew: "自动续费", effective_from: "生效时间", effective_policy: "生效方式",
  entitlement_end_at: "权益有效至", entitlement_preserved: "原付费权益保留", refunded_minor: "退款金额",
  mandate_amount_minor: "后续自动扣款金额", mandate_billing_months: "自动续费周期（月）",
  message: "说明",
  amount_minor: "金额",
  balance_minor: "账户余额",
  spending_minor: "总支出",
  refund_minor: "退款",
  available_minor: "可用金额",
  remaining_minor: "剩余金额",
  emergency_reserve_minor: "应急储备",
  payee: "收款人",
  payee_name: "收款人",
  memo: "备注",
  payee_last4: "收款账号尾号",
  transfer_channel: "转账渠道",
  recipient_username: "收款平台账号",
  recipient_reference: "收款账户标识",
  recipient_display_name: "收款显示名称",
  source_username: "付款平台账号",
  source_reference: "付款账户标识",
  source_card_last4: "实际付款卡尾号",
  card_number: "16字符账户卡号",
  display_last4: "卡号尾码",
  legacy_last4: "旧兼容尾号（历史字段）",
  source_card_number: "实际付款卡号（当时快照）",
  source_card_display_last4: "实际付款卡HEX尾码（当时快照）",
  source_card_legacy_last4: "付款卡旧兼容尾号（当时快照）",
  recipient_card_number: "收款卡号",
  recipient_card_display_last4: "收款卡HEX尾码",
  issuer_prefix: "卡号前缀",
  requested_currency: "原输入币种",
  requested_amount_minor: "原输入金额",
  source_currency: "扣款币种",
  target_currency: "入账币种",
  debit_minor: "实际扣款金额",
  credit_minor: "实际入账金额",
  fx_quote: "外汇资金方案",
  rate: "USD/CNY参考基准汇率",
  market_date: "市场观察日期",
  expiry: "成交方案有效截止",
  quote_id: "成交方案编号",
  provider: "数据或模型提供方",
  source_url: "实际数据来源地址",
  fee_source: "费用策略来源",
  funding_notice: "付款资金方案",
  settlement_notice: "实际入账方案",
  balances_by_currency: "分币种余额（不直接相加）",
  supported_currencies: "支持币种",
  currency_mode: "卡片币种模式",
  market_rate: "只读参考行情（不是成交方案）",
  conversion: "后端参考换算（不可直接执行）",
  input_currency: "参考输入币种",
  output_currency: "参考输出币种",
  input_amount_minor: "参考输入金额",
  output_amount_minor: "参考输出金额",
  cny_reference_minor: "后端CNY参考等值（非新增人民币资金）",
  usd_balance_minor: "美元独立余额",
  account_last4: "账号尾号",
  card_last4: "卡片尾号",
  card_status: "卡片状态",
  status: "状态",
  action: "操作",
  action_type: "操作类型",
  product_name: "产品",
  subscription: "订阅",
  source_type: "扣款来源",
  units: "份额",
  limit_minor: "卡片限额",
  note: "备注",
  currency: "币种",
  receipt_no: "回执编号",
  receipt_id: "回执编号",
  transaction_id: "交易编号",
  transfer_id: "转账编号",
  order_id: "订单编号",
  unit_price_minor: "份额单价",
  product_version: "产品版本",
  holding_units: "剩余持仓份额",
  order_type: "交易类型",
  source_account_balance_minor: "付款账户余额",
  source_balance_minor: "付款账户余额（回执时点）",
  account_balance_minor: "账户余额",
  fee_minor: "手续费",
  capital_guaranteed: "本金保证",
  yield_prediction_provided: "收益预测",
  redemption_timing: "赎回到账规则",
  term: "产品期限",
  minimum_units: "起始份额",
  terms: "产品条款",
  terms_version: "条款版本",
  risk_profile: "风险档案",
  product_fact_source: "产品数据来源",
  committed_at: "执行时间",
  executed_at: "执行时间",
  created_at: "生成时间",
  next_due_at: "下次扣款",
  reason: "原因",
  decision: "策略决定",
  model: "模型",
  model_label: "模型",
  intent_type: "意图",
  confidence: "置信度",
  tool_name: "业务能力",
  node_key: "节点",
  node_id: "节点",
  version: "版本",
  mode: "模式",
  risk_level: "风险等级",
  explanation: "分析结论",
  count: "数量",
  transaction_count: "交易笔数",
  receipt: "执行回执",
  result: "执行结果",
  sandbox_id: "账套",
  task_id: "任务",
  task_version: "任务版本",
  event_seq: "事件序号",
  plan_version: "计划版本",
  verified: "验证结果",
  label: "名称",
  input: "模型输入",
  output: "模型输出",
  source: "来源",
  error: "异常",
  preview: "本次确认参数",
  scope: "适用范围",
  points: "核算积分",
  credited_points: "已入账积分",
  cost_points: "兑换所需积分",
  wallet_points: "积分钱包余额",
  remaining_points: "剩余积分",
  target_minor: "目标金额",
  reserved_minor: "已预留金额",
  maximum_amount_minor: "授权金额上限",
  available_balance_minor: "可用余额",
  goal_reserved_minor: "目标预留",
  goal_name: "目标名称",
  behavior_name: "行为名称",
  reward_name: "权益名称",
  grantee_name: "协助人",
  owner_name: "账户本人",
  proposal_kind: "提案类型",
  limit_period: "额度周期",
  expires_days: "授权有效天数",
  expires_at: "有效截止",
  due_date: "目标截止日期",
  due_at: "约定执行时间",
  estimated_avoided_grams: "估算避免排放",
  method_version: "估算方法版本",
  methodology_version: "估算方法版本",
  expected_reward_version: "核验权益版本",
  expected_wallet_version: "核验钱包版本",
  expected_account_version: "核验账户版本",
  expected_product_version: "核验产品版本",
  expected_holding_version: "核验持仓版本",
  delegation_version: "委托版本",
  expected_delegation_version: "核验委托版本",
  expected_profile_id: "核验风险档案引用",
  operation_key: "执行幂等引用",
  scopes: "授权范围",
  account_id: "账户引用",
  reward_id: "权益引用",
  wallet_id: "钱包引用",
  behavior_id: "行为凭据引用",
  delegation_id: "委托引用",
  product_id: "产品引用",
};
function formatValue(
  key: string,
  value: JsonValue,
  technical = false,
  declaredCurrency?: string,
  context?: Record<string, JsonValue>,
  locale: CustomerLocale = getCustomerLocale(),
): ReactNode {
  const presentationText = (text: string) => technical ? text : translateCustomerText(text, locale);
  if (value === null) return "—";
  if (typeof value === "number" && key.endsWith("_minor")) {
    if (
      key === "fee_minor" &&
      value === 0 &&
      (context?.fee_source === "SIM_POLICY" || context?.source === "SIM_POLICY")
    )
      return presentationText("0（当前费用规则）");
    const currency = context
      ? currencyForMinorField(key, context, declaredCurrency)
      : declaredCurrency || "CNY";
    return currency
      ? (technical ? money(value, { currency }) : localizeFinancialValue(money(value, { currency }), locale))
      : `${value}${presentationText("（金额币种未返回）")}`;
  }
  if (
    key === "balances_by_currency" &&
    value &&
    typeof value === "object" &&
    !Array.isArray(value)
  )
    return (
      <dl className="structured-currency-balances">
        {Object.entries(value).map(([currency, minor]) => (
          <div key={currency}>
            <dt>{currency}</dt>
            <dd>
              {typeof minor === "number"
                ? money(minor, { currency })
                : presentationText("余额未返回")}
            </dd>
          </div>
        ))}
      </dl>
    );
  if (typeof value === "number" && /(?:^|_)points$/.test(key))
    return `${new Intl.NumberFormat(technical ? "zh-CN" : locale).format(value)} ${presentationText("积分")}`;
  if (typeof value === "number" && key === "estimated_avoided_grams")
    return `${new Intl.NumberFormat(technical ? "zh-CN" : locale).format(value)} ${presentationText("克（估算值）")}`;
  if (typeof value === "number" && key === "expires_days") return `${value} ${presentationText("天")}`;
  if (typeof value === "boolean") return presentationText(key === "auto_renew" ? value ? "已开启" : "未开启" : value ? "是" : "否");
  if (typeof value === "string") {
    if (!technical && key === "effective_policy") return presentationText(value === "START_NOW" ? "立即生效"
      : value === "EXTEND_FROM_ENTITLEMENT_END" ? "接续现有权益到期时间" : "生效方式待核对");
    if (!technical && ["term","redemption_timing"].includes(key)) return presentationText(value);
    if (!technical && ["card_number", "source_card_number", "recipient_card_number"].includes(key))
      return /^(?:(?:zhenhang|xiaozhao)[0-9a-f]{8}|\d{12,19})$/i.test(value)
        ? `${locale === "en" ? "ending " : "尾号 "}${value.slice(-4)}`
        : presentationText("卡片标识待核实");
    if (key === "memo") return value; // A customer's exact note is data, not a status or translated label.
    if (key === "transfer_channel")
      return value === "INTERNAL_BANK"
        ? presentationText("平台内互转（双方账户）")
        : value === "EXTERNAL_SIM"
          ? presentationText("外部清算转出")
          : value;
    if (key.endsWith("_at") || key === "effective_from" || key === "due_date" || key === "expiry")
      return dateText(value, true);
    if (key === "limit_period") {
      const labels: Record<string, string> = {
        MONTH: "每月累计上限",
        PER_PROPOSAL: "单笔提案上限",
        TRANSACTION: "单笔上限",
      };
      return Object.hasOwn(labels, value) ? presentationText(labels[value]) : value;
    }
    if (key === "scopes") {
      const labels: Record<string, string> = {
        READ_BALANCE: "查看授权余额",
        PROPOSE_TRANSFER: "提出转账提案",
        PROPOSE_CARD_LOCK: "提出卡片保护提案",
      };
      return Object.hasOwn(labels, value) ? presentationText(labels[value]) : value;
    }
    if (!technical && ["name", "product_name", "behavior_name", "reward_name", "display_name"].includes(key))
      return customerName(value);
    if (!technical && ["message", "summary", "explanation", "notice"].includes(key))
      return customerNarrative(value);
    if (!technical && ["status", "card_status", "schedule_status", "mode", "source_type", "order_type", "currency_mode"].includes(key)) {
      const labels: Record<string, string> = key === "currency_mode"
        ? { CNY_ONLY: "人民币卡", CNY_USD: "人民币美元双币卡" }
        : stateLabels;
      return Object.hasOwn(labels, value) ? presentationText(labels[value]) : value;
    }
    return technical ? stateLabels[value] || value : value;
  }
  if (Array.isArray(value))
    return (
      <ul className="value-list">
        {value.map((item, index) => (
          <li key={index}>
            {typeof item === "object" && item !== null ? (
              <StructuredData
                data={item}
                technical={technical}
                currency={declaredCurrency}
              />
            ) : (
              formatValue(key, item, technical, declaredCurrency, context, locale)
            )}
          </li>
        ))}
      </ul>
    );
  if (typeof value === "object")
    return (
      <StructuredData
        data={value}
        technical={technical}
        currency={declaredCurrency}
      />
    );
  return String(value);
}
export function StructuredData({
  data,
  technical = false,
  currency,
  confirmation = false,
}: {
  data: JsonValue | undefined;
  technical?: boolean;
  currency?: string;
  confirmation?: boolean;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  if (data === undefined || data === null) return null;
  if (typeof data !== "object") {
    const original = String(data);
    // This one controlled server summary predates the values-first layout.
    // Keep its safety meaning without pointing to values that are now above.
    const presented = confirmation && !technical && original === "Check the actual recipient, card, amount, currencies and date below. This step has not been executed; confirmation and any required verification are separate."
      ? "Review the recipient, card, amount, currencies and date for this action. It has not been executed; confirmation and any required verification are separate."
      : original;
    const masked = confirmation && !technical ? presented.replace(/\b(?:zhenhang|xiaozhao)[0-9a-f]{8}\b/gi, value => `${locale === "en" ? "card ending " : "尾号 "}${value.slice(-4)}`) : presented;
    return <p className="structured-message">{technical ? original : customerNarrative(masked)}</p>;
  }
  if (Array.isArray(data))
    return (
      <div className="structured-array">
        {data.map((item, i) => (
          <StructuredData
            key={i}
            data={item}
            technical={technical}
            currency={currency}
            confirmation={confirmation}
          />
        ))}
      </div>
    );
  const secretFields = new Set([
    "password",
    "current_password",
    "new_password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "token_hash",
    "api_key",
    "api_key_hash",
    "secret",
    "code_hash",
    "salt",
  ]);
  const sourceEntries = Object.entries(data).filter(
    ([key]) => !secretFields.has(key),
  );
  const entries = technical ? sourceEntries : customerDataEntries(Object.fromEntries(sourceEntries), {confirmation});
  const internalRecipient = data.transfer_channel === "INTERNAL_BANK"
    && typeof data.recipient_username === "string" && Boolean(data.recipient_username.trim());
  const namedInternalRecipient = internalRecipient && typeof data.recipient_display_name === "string"
    && Boolean(data.recipient_display_name.trim());
  if (confirmation && !technical) {
    // Review priority changes presentation only; retain every customer business field.
    const priority = (key: string) => {
      if (["amount_minor", "requested_amount_minor", "debit_minor", "credit_minor", "total_amount_minor"].includes(key)) return 0;
      if (["currency", "requested_currency", "source_currency", "target_currency"].includes(key)) return 1;
      if (["payee", "payee_name", "recipient_display_name", "recipient_username", "owner_name", "grantee_name", "product_name", "merchant"].includes(key)) return 2;
      if (["source_card_context", "source_card_number", "source_card_display_last4", "source_card_last4",
        "recipient_card_number", "recipient_card_display_last4"].includes(key)) return 2.5;
      if (key === "memo") return 3;
      return 4;
    };
    entries.sort(([left], [right]) => priority(left) - priority(right));
  }
  // Only known secondary fields are folded. Unrecognized bank business facts,
  // every amount/currency, payee, permission, note and risk remain first-class
  // review information. The original DTO still owns value formatting.
  const secondary = new Set(["transfer_channel", "rate", "funding_notice", "settlement_notice",
    "currency_mode", "supported_currencies", "redemption_timing", "term", "count", "transaction_count",
    "receipt_no", "receipt_id", "transaction_id", "transfer_id", "order_id", "created_at", "committed_at", "executed_at"]);
  const compact = confirmation && !technical;
  if (compact && internalRecipient) {
    // This is the internal cash-account identifier, not the selected receiving
    // card. Preserve it in details with an explicit label; never pass this
    // presentation classification to authorization or infer another card.
    secondary.add("payee_last4");
    if (namedInternalRecipient) {
      secondary.add("payee_name");
      secondary.add("recipient_username");
    }
  }
  const mainEntries = compact ? entries.filter(([key]) => !secondary.has(key)) : entries;
  const detailEntries = compact ? entries.filter(([key]) => secondary.has(key)) : [];
  const cardReviewLabels: Record<string,string> = locale === "en" ? {
    card_number:"Card",card_last4:"Card ending",display_last4:"Card ending",source_card_context:"Payment card",
    source_card_number:"Payment card",source_card_display_last4:"Payment card ending",source_card_last4:"Payment card ending",
    recipient_card_number:"Receiving card",recipient_card_display_last4:"Receiving card ending",
    recipient_display_name:"Recipient",
    ...(internalRecipient ? {payee_name:"Recipient reference",payee_last4:"Internal account identifier ending"} : {}),
  } : {card_number:"银行卡",card_last4:"卡片尾号",display_last4:"卡片尾号",source_card_context:"付款银行卡",
    source_card_number:"付款银行卡",source_card_display_last4:"付款卡尾号",source_card_last4:"付款卡尾号",
    recipient_card_number:"收款银行卡",recipient_card_display_last4:"收款卡尾号",recipient_display_name:"收款人",
    ...(internalRecipient ? {payee_name:"收款标识",payee_last4:"内部账户标识尾码"} : {})};
  const rows = (selected: [string, JsonValue][]) => (
      <dl className={`structured-data${confirmation ? " structured-confirmation" : ""}`}>
        {selected.map(([key, value]) => (
          <div key={key} data-confirmation-field={confirmation ? key : undefined}>
            <dt>{technical ? fieldLabels[key] || key.replaceAll("_", " ") : (() => { if(Object.hasOwn(cardReviewLabels,key))return cardReviewLabels[key]; const label = key === "merchant" && confirmation && Object.hasOwn(data,"monthly_price_minor") ? "服务名称" : fieldLabels[key] || key.replaceAll("_", " "); const translated = customerUiText(label); return locale === "en" && /[\u4e00-\u9fff]/.test(translated) ? key.replaceAll("_minor", "").replaceAll("_", " ") : translated; })()}</dt>
            <dd>
              {(() => { const rendered = formatValue(
                key,
                value,
                technical,
                typeof data.currency === "string" ? data.currency : currency,
                data,
                locale,
              ); return rendered; })()}
            </dd>
          </div>
        ))}
      </dl>
  );
  return <>{rows(mainEntries)}{detailEntries.length > 0 && <details className="confirmation-field-details">
    <summary>{locale === "en" ? "More action details" : "更多办理信息"}</summary>
    {rows(detailEntries)}
  </details>}</>;
}
const chartColors = [
  "#188c80",
  "#426daa",
  "#74b3bc",
  "#a4bd8e",
  "#7c89ae",
  "#cfb88a",
  "#b5c8dc",
];
const categoryLabels: Record<string, string> = {
  FOOD: "餐饮",
  DINING: "餐饮",
  TRANSPORT: "交通",
  SHOPPING: "购物",
  ENTERTAINMENT: "娱乐",
  HOUSING: "居住",
  UTILITIES: "生活缴费",
  SUBSCRIPTION: "订阅",
  OTHER: "其他",
  TRANSFER: "转账",
};
export function SpendingChart({ analysis }: { analysis: BillAnalysis }) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const categories = Object.entries(analysis.category_spending_minor || {})
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = categories.reduce((sum, [, value]) => sum + value, 0);
  let offset = 0;
  return (
    <div className="spending-chart">
      <div className="donut">
        <svg viewBox="0 0 160 160" role="img" aria-label={customerUiText("账单支出分类分布")}>
          <circle
            cx="80"
            cy="80"
            r="60"
            stroke="#eef2f5"
            strokeWidth="17"
            fill="none"
          />
          {categories.map(([key, value], index) => {
            const size = total ? (value / total) * 376.991 : 0;
            const segment = (
              <circle
                key={key}
                cx="80"
                cy="80"
                r="60"
                fill="none"
                stroke={chartColors[index % chartColors.length]}
                strokeWidth="17"
                strokeDasharray={`${size} ${376.991 - size}`}
                strokeDashoffset={-offset}
                transform="rotate(-90 80 80)"
              />
            );
            offset += size;
            return segment;
          })}
        </svg>
        <div>
          <small><CustomerText text="统计期支出" /></small>
          <strong>{money(analysis.spending_minor)}</strong>
        </div>
      </div>
      <div className="chart-legend">
        {categories.length ? (
          categories.slice(0, 7).map(([key, value], i) => (
            <div key={key}>
              <span>
                <i
                  style={{ background: chartColors[i % chartColors.length] }}
                />
                {locale === "en" ? analysis.financial_insights?.category_labels_en?.[key] || customerUiText(categoryLabels[key] || key) : categoryLabels[key] || key}
              </span>
              <strong>{money(value)}</strong>
            </div>
          ))
        ) : (
          <p className="muted"><CustomerText text="当前统计期没有支出。" /></p>
        )}
      </div>
    </div>
  );
}
export function BankCardNumber({ identity }: { identity: { number: string | null; displayTail: string } }) {
  const { locale } = useCustomerLanguage();
  const en = locale === "en";
  const [revealedNumber, setRevealedNumber] = useState<string | null>(null);
  const [copyResult, setCopyResult] = useState<{number: string; status: "COPIED" | "FAILED"} | null>(null);
  const numberVisible = Boolean(identity.number && revealedNumber === identity.number);
  const copyStatus = copyResult && copyResult.number === identity.number ? copyResult.status : null;
  async function copyNumber() {
    if (!identity.number) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("COPY_UNAVAILABLE");
      await navigator.clipboard.writeText(identity.number);
      setCopyResult({number: identity.number, status: "COPIED"});
    } catch {
      setCopyResult({number: identity.number, status: "FAILED"});
      setRevealedNumber(identity.number);
    }
  }
  return (<>
      <div className="bank-card-number-row">
      <div className={`card-number${numberVisible ? " card-number-revealed" : ""}`}>
        {numberVisible ? <><code>{identity.number!.slice(0, 8)}</code><code>{identity.number!.slice(8)}</code></> : <>
        <span>••••</span>
        <span>••••</span>
        <span>••••</span>
        <strong>{identity.displayTail}</strong>
        </>}
      </div>
      {identity.number && <div className="bank-card-number-actions">
            <button type="button" className="card-number-visibility"
              aria-pressed={numberVisible}
              aria-label={en ? `${numberVisible ? "Hide" : "Show"} full card number ending ${identity.displayTail}`
                : `${numberVisible ? "隐藏" : "显示"}尾号${identity.displayTail}的完整卡号`}
              title={en ? (numberVisible ? "Hide card number" : "Show card number") : (numberVisible ? "隐藏卡号" : "显示卡号")}
              onClick={() => setRevealedNumber(numberVisible ? null : identity.number)}><Icon name="eye" size={17}/></button>
            <button
              type="button"
              className="simulation-card-copy"
              onClick={copyNumber}
              aria-label={en ? `Copy full card number ending ${identity.displayTail}` : `复制尾号${identity.displayTail}的完整卡号`}
            ><CustomerText text={copyStatus === "COPIED" ? "已复制" : "复制"} /></button>
      </div>}
      </div>
      {copyStatus && <span className={copyStatus === "FAILED" ? "card-copy-error" : "sr-only"} role="status">
        {copyStatus === "COPIED" ? (en ? "Card number copied." : "卡号已复制")
          : (en ? "Copy is unavailable. You can select the displayed card number." : "复制不可用，可手动选择卡号")}
      </span>}
  </>);
}

export function CardTile({ card }: { card: Card }) {
  const { t: customerUiText } = useCustomerLanguage();
  const identity = currentCardIdentity(card);
  const dual = card.currency_mode === "CNY_USD" || card.fx_card === true;
  return (
    <div
      className={`bank-card ${dual ? "bank-card-dual" : "bank-card-cny"}`}
      aria-label={`${dual ? customerUiText("CNY USD双币") : customerUiText("人民币")}卡尾码${identity.displayTail}`}
      data-currency-mode={dual ? "CNY_USD" : "CNY_ONLY"}
      data-card-tail={identity.displayTail}
    >
      <div className="bank-card-top">
        <Brand compact />
        <span><CustomerText text="圳行 ·" />{dual ? customerUiText("双币卡") : customerUiText("人民币卡")}</span>
        <Icon name="card" size={20} />
      </div>
      <div className="card-currency-badges">
        <span><CustomerText text="CNY · 人民币" /></span>
        {dual && <span><CustomerText text="USD · 美元" /></span>}
        {card.card_tier_code && card.card_tier_name && <span>{card.card_tier_name}</span>}
      </div>
      <BankCardNumber identity={identity}/>
      {dual && (
        <div className="dual-card-balances">
          {["CNY", "USD"].map((currency) => (
            <div key={currency}>
              <small>{currency}<CustomerText text="独立余额" /></small>
              <strong>
                {Number.isSafeInteger(card.balances_by_currency?.[currency])
                  ? money(card.balances_by_currency![currency], { currency })
                  : customerUiText("余额未返回")}
              </strong>
            </div>
          ))}
        </div>
      )}
      <div className="bank-card-bottom">
        {card.status !== "ACTIVE" && <span className="card-restriction">
          <StateBadge state={card.status} />
        </span>}
        <span className="bank-card-limit">
          <small>{dual ? customerUiText("人民币单笔限额") : customerUiText("单笔限额")}</small>
          <strong>
            {money(card.limit_minor, {
              currency: card.limit_currency || "CNY",
            })}
          </strong>
        </span>
      </div>
      {dual && (
        <p className="dual-card-balance-note"><CustomerText text="两币分别记账，不汇总为人民币；CNY是绑定账户同一资金。" /></p>
      )}
    </div>
  );
}
export function Skeleton({
  lines = 3,
  compact = false,
}: {
  lines?: number;
  compact?: boolean;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  return (
    <div
      className={`skeleton ${compact ? "compact" : ""}`}
      aria-label={customerUiText("正在读取真实数据")}
      role="status"
    >
      <span className="sr-only"><CustomerText text="正在读取真实数据" /></span>
      {Array.from({ length: lines }, (_, index) => (
        <span
          className="skeleton-line"
          key={index}
          style={{ width: index === lines - 1 ? "64%" : "100%" }}
        />
      ))}
    </div>
  );
}
const expandedStatuses = new Set([
  "RUNNING",
  "EXECUTING",
  "AUTHORIZED",
  "WAITING_CONFIRMATION",
  "NEED_CLARIFICATION",
  "UNKNOWN",
  "FAILED",
  "WAITING_TIME",
]);
export function WorkflowList({
  nodes,
  taskState,
  technical = false,
}: {
  nodes: TraceNode[];
  taskState?: string;
  technical?: boolean;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const signature = nodes
    .map((node) => `${node.node_id}:${node.status}`)
    .join("|");
  const previous = useRef(new Map<string, string>());
  const [expanded, setExpanded] = useState(
    () =>
      new Set(
        nodes
          .filter((node) => expandedStatuses.has(node.status))
          .map((node) => node.node_id),
      ),
  );
  useEffect(() => {
    const changed = nodes.filter(
      (node) => previous.current.get(node.node_id) !== node.status,
    );
    previous.current = new Map(
      nodes.map((node) => [node.node_id, node.status]),
    );
    setExpanded((current) => {
      const next = new Set(current);
      for (const node of changed) {
        if (expandedStatuses.has(node.status)) next.add(node.node_id);
        else if (["SUCCEEDED", "CANCELLED", "SKIPPED"].includes(node.status))
          next.delete(node.node_id);
      }
      return next.size === current.size &&
        [...next].every((id) => current.has(id))
        ? current
        : next;
    });
  }, [signature]);
  if (!nodes.length) {
    const inactive = ["SUCCEEDED", "FAILED", "CLOSED"].includes(
      taskState || "",
    );
    return inactive ? (
      <p className="empty-text"><CustomerText text="本次任务没有可执行节点。" /></p>
    ) : (
      <div className="preparing">
        <span className="spinner" />
        <div>
          <strong><CustomerText text="正在整理你的需求" /></strong>
          <small><CustomerText text="计划生成后显示实际步骤，当前没有执行进度。" /></small>
          <Skeleton lines={2} compact />
        </div>
      </div>
    );
  }
  if (!technical) return (
    <ol className="workflow-list customer-workflow" aria-label={customerUiText("办理步骤")}>
      {nodes.map((node, index) => (
        <li key={node.node_id} className={toneOf(node.status)} data-node-status={node.status}>
          <div className="workflow-row">
            <span className="step-symbol">
              {node.status === "SUCCEEDED" ? <Icon name="check" size={15} /> : index + 1}
            </span>
            <span className="workflow-label">
              <strong>{customerName(node.label || customerActionLabel(node.action))}</strong>
            </span>
            <StateBadge state={node.status} />
          </div>
        </li>
      ))}
    </ol>
  );
  return (
    <ol className="workflow-list">
      {nodes.map((node, index) => {
        const open = expanded.has(node.node_id);
        const active = ["RUNNING", "EXECUTING", "AUTHORIZED"].includes(
          node.status,
        );
        const detailId = `workflow-detail-${node.node_id}`;
        const result = node.result
          ? Object.fromEntries(
              Object.entries(node.result).filter(
                ([key]) =>
                  ![
                    "products",
                    "transactions",
                    "candidates",
                    "simulated",
                    "payee_candidates",
                  ].includes(key),
              ),
            )
          : null;
        return (
          <li
            key={node.node_id}
            className={`${toneOf(node.status)} ${active ? "node-active" : ""} ${open ? "expanded" : ""}`}
            data-node-status={node.status}
          >
            <button
              className="workflow-row"
              onClick={() =>
                setExpanded((current) => {
                  const next = new Set(current);
                  if (next.has(node.node_id)) next.delete(node.node_id);
                  else next.add(node.node_id);
                  return next;
                })
              }
              aria-expanded={open}
              aria-controls={detailId}
            >
              <span className="step-symbol">
                {node.status === "SUCCEEDED" ? (
                  <Icon name="check" size={15} />
                ) : ["FAILED", "CANCELLED"].includes(node.status) ? (
                  <Icon name="close" size={14} />
                ) : (
                  index + 1
                )}
              </span>
              <span className="workflow-label">
                <strong>{node.label || node.action}</strong>
                <small>
                  {node.error ||
                    node.wait_reason ||
                    stateLabels[node.status] ||
                    node.status}
                </small>
              </span>
              <StateBadge state={node.status} />
              <Icon name="chevron" size={13} className="workflow-chevron" />
            </button>
            {open && (
              <div className="workflow-details" id={detailId}>
                {node.depends_on?.length > 0 && (
                  <p className="node-dependency"><CustomerText text="前置步骤：" />{node.depends_on
                      .map(
                        (id) =>
                          nodes.find((item) => item.node_id === id)?.label ||
                          id,
                      )
                      .join("、")}
                  </p>
                )}
                {result && Object.keys(result).length > 0 ? (
                  <StructuredData data={result} />
                ) : (
                  <p className="empty-text">
                    {node.wait_reason || customerUiText("此步骤尚未产生执行回执。")}
                  </p>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
export function Dag({ nodes }: { nodes: TraceNode[] }) {
  const { t: customerUiText } = useCustomerLanguage();
  if (!nodes.length)
    return <p className="empty-text"><CustomerText text="当前尚未生成计划节点。" /></p>;
  const depth = new Map<string, number>();
  const visit = (node: TraceNode, seen = new Set<string>()): number => {
    if (depth.has(node.node_id)) return depth.get(node.node_id)!;
    if (seen.has(node.node_id)) return 0;
    seen.add(node.node_id);
    const parents = (node.depends_on || [])
      .map((id) => nodes.find((item) => item.node_id === id))
      .filter((item): item is TraceNode => Boolean(item));
    const level = parents.length
      ? Math.max(...parents.map((item) => visit(item, new Set(seen)))) + 1
      : 0;
    depth.set(node.node_id, level);
    return level;
  };
  nodes.forEach((node) => visit(node));
  const levels = Math.max(...depth.values()) + 1;
  const slots = new Map<number, number>();
  const positions = nodes.map((node) => {
    const level = depth.get(node.node_id) || 0;
    const slot = slots.get(level) || 0;
    slots.set(level, slot + 1);
    return { node, x: slot * 244 + 16, y: level * 126 + 12 };
  });
  const width = Math.max(1, ...slots.values()) * 244 + 16;
  const height = levels * 126;
  return (
    <div className="dag-scroll">
      <div className="dag" style={{ width, height }}>
        <svg width={width} height={height} aria-hidden="true">
          <defs>
            <marker
              id="dag-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#a8b9c9" />
            </marker>
          </defs>
          {positions.flatMap((item) =>
            (item.node.depends_on || []).map((id) => {
              const parent = positions.find(
                (entry) => entry.node.node_id === id,
              );
              if (!parent) return null;
              const x = parent.x + 108,
                y = parent.y + 82,
                endX = item.x + 108,
                endY = item.y;
              return (
                <path
                  key={`${id}-${item.node.node_id}`}
                  d={`M ${x} ${y} C ${x} ${y + 25}, ${endX} ${endY - 25}, ${endX} ${endY}`}
                  fill="none"
                  stroke="#a8b9c9"
                  strokeWidth="1.5"
                  markerEnd="url(#dag-arrow)"
                />
              );
            }),
          )}
        </svg>
        {positions.map(({ node, x, y }) => (
          <div
            className={`dag-node ${toneOf(node.status)}`}
            style={{ left: x, top: y }}
            key={node.node_id}
          >
            <strong>{node.label || node.action}</strong>
            <StateBadge state={node.status} />
            <small>{node.node_id}</small>
          </div>
        ))}
      </div>
    </div>
  );
}
export function LayaEvidence({ advice }: { advice: LayaAdvice }) {
  const { t: customerUiText } = useCustomerLanguage();
  const statuses: Record<string, string> = {
    OFF: "已关闭",
    READY: "观察已记录",
    UNAVAILABLE: "未配置可用后端",
    ERROR: "观察失败",
    TIMED_OUT: "观察超时",
    PENDING: "等待观察",
    BUSY: "后端繁忙",
    BUDGET_EXCEEDED: "观察预算已用尽",
    SKIPPED: "本次已跳过",
  };
  const domains: Record<string, string> = {
    BILL: "账单",
    TRANSFER: "转账",
    WEALTH: "理财",
    CARD: "卡片",
    SUBSCRIPTION: "订阅",
  };
  const agreement: Record<string, string> = {
    AGREE: "与主规划覆盖一致",
    DISAGREE: "存在待评测分歧",
    UNKNOWN: "尚未完成对比",
    UNAVAILABLE: "暂无覆盖对比",
  };
  const validAnswers =
    advice.mode === "SHADOW" && advice.status === "READY"
      ? (advice.answers || []).filter(
          (answer) =>
            typeof answer.raw_p_true === "number" &&
            Number.isFinite(answer.raw_p_true),
        )
      : [];
  return (
    <section className="evidence-section laya-evidence">
      <div className="laya-heading">
        <h3><CustomerText text="Laya 影子决策" /></h3>
        <span className="state-badge neutral">
          <i />
          {statuses[advice.status] || advice.status}
        </span>
      </div>
      <p className="muted small">
        {advice.backend === "TEST_STUB"
          ? customerUiText("当前记录来自测试桩。")
          : advice.laya_used
            ? customerUiText("当前记录来自实际 Laya 后端。")
            : customerUiText("本次没有使用 Laya 推理。")}<CustomerText text="影子观察不改变任务计划或业务授权。" /></p>
      {validAnswers.length > 0 && (
        <>
          <div className="laya-domain-compare">
            <div>
              <small><CustomerText text="主规划覆盖" /></small>
              <span>
                {(advice.primary_domains || [])
                  .map((domain) => domains[domain] || domain)
                  .join("、") || customerUiText("无已映射领域")}
              </span>
            </div>
            <div>
              <small><CustomerText text="影子建议覆盖" /></small>
              <span>
                {(advice.candidate_domains || [])
                  .map((domain) => domains[domain] || domain)
                  .join("、") || customerUiText("无已映射领域")}
              </span>
            </div>
          </div>
          <p className="laya-calibration"><CustomerText text="原始分数尚未校准，不表示预测正确率；0.5 阈值仅用于探索性观察。" /></p>
          <div className="laya-scores">
            {validAnswers.map((answer) => {
              const definition =
                advice.question_definitions?.[answer.question_id];
              const label =
                typeof definition === "string"
                  ? definition
                  : definition &&
                      typeof definition === "object" &&
                      !Array.isArray(definition) &&
                      typeof definition.question === "string"
                    ? definition.question
                    : answer.question_id;
              return (
                <div key={answer.question_id}>
                  <span>{label}</span>
                  <strong>
                    {(answer.raw_p_true * 100).toFixed(1)}%
                    <small><CustomerText text="原始 P(true)" /></small>
                  </strong>
                  <div className="laya-score-track">
                    <span
                      style={{
                        width: `${Math.min(100, Math.max(0, answer.raw_p_true * 100))}%`,
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
      {advice.coverage_difference && (
        <div className="laya-coverage">
          <strong>
            {agreement[advice.coverage_difference.agreement] ||
              advice.coverage_difference.agreement}
          </strong>
          <p>
            {advice.coverage_difference.interpretation ||
              customerUiText("分歧仅作为人工评测线索，不用于追加动作或授权。")}
          </p>
        </div>
      )}
      {typeof advice.latency_ms === "number" && (
        <p className="muted small"><CustomerText text="本次观察耗时" />{advice.latency_ms.toFixed(0)} ms
        </p>
      )}
      {advice.error_code && (
        <p className="muted small"><CustomerText text="观察错误码：" />{advice.error_code}</p>
      )}
      {advice.identity && (
        <details className="laya-identity">
          <summary><CustomerText text="模型与权重身份记录" /></summary>
          <StructuredData data={advice.identity} technical />
        </details>
      )}
    </section>
  );
}
export function EvidenceDrawer({
  trace,
  onClose,
}: {
  trace: TaskTrace;
  onClose: () => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { onDismiss: onClose, dialogRef });
  function exportTrace() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `zhenxing-trace-${trace.task_id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={dialogRef}
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow"><CustomerText text="可核验执行过程" /></span>
            <h2 id="evidence-title"><CustomerText text="Agent 工作流" /></h2>
          </div>
          <button
            className="icon-button"
            aria-label={customerUiText("关闭执行证据")}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-content">
          <div className="trace-meta">
            <StateBadge state={trace.state} />
            <span>
              {trace.plan_version > 0
                ? `计划 v${trace.plan_version}`
                : customerUiText("计划未生成")}
            </span>
            <span><CustomerText text="事件" />{trace.event_cursor ?? "—"}</span>
          </div>
          <p className="trace-message">{trace.message}</p>
          <section className="evidence-section">
            <h3><CustomerText text="任务依赖图" /><small><CustomerText text="节点与连线来自实际计划" /></small>
            </h3>
            <Dag nodes={trace.nodes || []} />
          </section>
          <section className="evidence-section">
            <h3><CustomerText text="节点回执" /></h3>
            {(trace.nodes || []).map((node) => (
              <details className="node-detail" key={node.node_id}>
                <summary>
                  <span>{node.label || node.action}</span>
                  <StateBadge state={node.status} />
                </summary>
                <p className="muted small"><CustomerText text="能力：" />{node.action}<CustomerText text="· 节点：" />{node.node_id}
                </p>
                {node.error && <p className="error-inline">{node.error}</p>}
                {node.wait_reason && (
                  <p className="muted">{node.wait_reason}</p>
                )}
                {node.result ? (
                  <StructuredData data={node.result} technical />
                ) : (
                  <p className="empty-text"><CustomerText text="此节点暂未产生执行回执。" /></p>
                )}
              </details>
            ))}
          </section>
          <section className="evidence-section">
            <h3><CustomerText text="模型决策记录" /></h3>
            <p className="muted small"><CustomerText text="理解来源：" />{trace.model_label || customerUiText("等待模型返回")}<CustomerText text="。下面展示已记录的决策信息。" /></p>
            {trace.model_decisions ? (
              <StructuredData data={trace.model_decisions} technical />
            ) : (
              <p className="empty-text"><CustomerText text="此任务暂无可展示的模型决策记录。" /></p>
            )}
          </section>
          {trace.planning_diagnostics && (
            <ModelDiagnostics data={trace.planning_diagnostics} />
          )}
          {trace.laya_advice && <LayaEvidence advice={trace.laya_advice} />}
          <details className="raw-trace">
            <summary><CustomerText text="查看完整 Trace" /></summary>
            <pre>{JSON.stringify(trace, null, 2)}</pre>
          </details>
        </div>
        <footer>
          <span className="muted small"><CustomerText text="任务" />{trace.task_id.slice(0, 12)}…
          </span>
          <button className="button secondary" onClick={exportTrace}>
            <Icon name="download" size={16} /><CustomerText text="导出执行证据" /></button>
        </footer>
      </aside>
    </div>
  );
}
