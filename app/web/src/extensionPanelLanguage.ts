import { translateCustomerText, type CustomerLocale } from "./customerLanguage.ts";

// Fixed interface copy only. Record names, narrative sources, scope values,
// API fields and the original request drafts never pass through this table.
const english: Record<string, string> = {
  "创建后切换到该独立身份；未获得委托时没有协助权限。": "Switch to this independent identity after creation. Assistance requires an active delegation.",
  "创建并切换": "Create and switch",
  "协助身份": "Helper identities", "协助者": "Helper", "本人": "Owner",
  "切换协助身份": "Switch to helper", "准备查看余额委托": "Prepare balance-access delegation",
  "准备转账提案委托": "Prepare transfer-proposal delegation", "自定义授权范围与额度": "Choose delegation scope and limit",
  "金额边界": "Amount limit", "额度（元）": "Limit (CNY)", "请输入明确额度": "Enter an exact limit",
  "有效期（1 至 180 天）": "Validity (1–180 days)", "生成本人授权计划": "Prepare the owner's authorization plan",
  "有效范围与委托": "Active scopes and delegations", "月度额度": "Monthly limit", "单笔额度": "Per-proposal limit",
  "· 有效至": "· Valid until", "准备撤销委托": "Prepare delegation revocation",
  "准备转账协助提案": "Prepare transfer assistance", "准备卡片保护提案": "Prepare card-protection assistance",
  "本人待审提案": "Proposals for owner review", "的协助提案": "'s assistance proposal",
  "打开本人办理任务": "Open the owner's request", "生成本人确认任务": "Prepare the owner's confirmation request",
  "拒绝提案": "Reject proposal", "本人审阅后仍需独立确认；此身份不能打开本人业务参数。": "The owner must review and independently confirm. This identity cannot open the owner's action details.",
  "提案已处理。": "The proposal has been processed.", "当前没有待审协助提案。": "No assistance proposals await review.",
  "查看余额": "View balance", "提出转账提案": "Propose a transfer", "提出锁卡提案": "Propose a card lock",
  "提出转账方案": "Propose a transfer", "提出锁卡方案": "Propose a card lock",
  "委托额度周期": "Delegation limit period", "委托额度": "Delegation limit", "委托有效天数": "Delegation validity in days",
  "本月已占用": "Used this month", "卡片尾号": "Card ending", "当前核验": "Current recipient check",
  "尾号": "ending", "项风险源记录": "risk-source records", "计划截至": "Target date", "月度缺口": "Monthly gap",
  "出现转入所谓安全账户的诱导": "Inducement to transfer to a so-called safety account",
  "出现先付款换取返利或奖励的诱导": "Inducement to pay upfront for a rebate or reward",
  "出现冒充公检法要求汇款的诱导": "A payment request impersonating law enforcement",
  "出现隐瞒家人或银行的转账要求": "A request to conceal a transfer from family or the bank",
  "远程控制或屏幕共享涉及账户、密码或支付": "Remote access or screen sharing involving accounts, passwords or payments",
  "要求先付费或缴保证金才能退款": "An upfront fee or deposit demanded before a refund",
  "收款人信息尚未唯一核验": "Recipient information has not been uniquely verified",
  "当前已知联系人未触发风险标记": "No current risk flag for the known contact",
  "尚未指定唯一收款人": "No unique recipient has been selected", "高风险 · 已拦截": "High risk · blocked",
  "当前证据未触发拦截规则": "Current evidence did not trigger a blocking rule", "风险未核清 · 等待核验": "Risk remains unverified · awaiting checks",
  "风险结论基于当前返回的证据；没有核实的收款信息会单列等待核验。": "This risk assessment follows the returned evidence. Unverified recipient information is listed separately for review.",
  "收款联系人": "Recipient contact", "反诈核验收款人": "Recipient for risk checks", "请选择当前账套中的联系人": "Select a contact from this account",
  "· 存在同名待核验": "· Duplicate name needs review", "对方要求或转账背景": "Recipient's request or transfer context",
  "反诈核验转账背景": "Transfer context for risk checks", "如有异常要求，可描述收到的实际话术": "Describe any unusual request using the actual message received",
  "重新核验风险": "Recheck risk", "本次规则线索": "Risk-rule evidence",
  "风险拦截不能被普通执行确认覆盖。": "An ordinary execution confirmation cannot override a risk block.",
  "后续动作仍须经过业务策略核验。": "Any further action still requires policy checks.",
  "核验报告不自动执行转账或锁卡。": "A risk report does not transfer money or lock a card.",
  "未返回规则版本": "Rule version was not returned", "尚无可展示的收款人风险源记录": "No recipient risk-source record is available",
  "让 Agent 对本次背景进行风险分析": "Prepare a risk-analysis request for this context",
  "扣除目标预留与应急金后的额度": "Funds after goal and emergency reserves", "账户现金": "Account cash",
  "目标预留占用支出额度，现金仍保留在账户。": "Goal reserves reduce spendable funds; the cash remains in the account.",
  "准备建立新目标": "Prepare a new goal", "目标名称": "Goal name", "储蓄目标名称": "Savings goal name",
  "输入自己的目标名称": "Enter your goal name", "目标金额（元）": "Target amount (CNY)", "储蓄目标金额": "Savings goal amount",
  "请输入金额": "Enter an amount", "计划截止日期（可选）": "Target date (optional)", "储蓄目标截止日期": "Savings goal target date",
  "生成目标计划": "Prepare goal plan", "当前目标": "Current goals", "仍需预留": "Still to reserve",
  "按期限月均目标": "Monthly target for this deadline", "未设置截止日期": "No target date set",
  "已预留到位，尚未支出": "Fully reserved, not spent", "已到期，需要重新评估": "Past the target date; review required",
  "当前闲置现金可覆盖": "Current available cash can cover this goal", "样本不足，需核对收入假设": "Insufficient data; review income assumptions",
  "需要明确目标期限": "A target date is needed", "按基准假设可安排": "Feasible under baseline assumptions",
  "存在月度筹资缺口": "There is a monthly funding gap",
  "预留账本核对一致": "Reserve ledger reconciled", "预留账本存在差异": "Reserve ledger has a discrepancy",
  "可自愿调整的方案": "Optional adjustment", "调整金额（元）": "Adjustment amount (CNY)", "调整预留金额": "Reserve adjustment amount",
  "准备预留": "Prepare reserve", "准备释放": "Prepare release", "当前没有已创建的目标，可先填写目标计划。": "No goals have been created. You can prepare a goal plan.",
  "现金流与情景参考": "Cash flow and scenarios", "90天基准情景月均参考": "90-day baseline monthly reference",
  "月均收入": "Average monthly income", "月均消费支出": "Average monthly consumption", "已观测退款参考": "Observed refunds",
  "估算净结余": "Estimated net surplus", "历史与情景估算不保证未来收入。应急金": "Historical and scenario estimates do not guarantee future income. Emergency reserves",
  "单独保护。": "are protected separately.", "展开": "View", "天情景推演": "-day scenarios",
  "历史样本不足，收入假设需要本人核实。": "Historical data is insufficient. The owner must review income assumptions.",
  "情景依据历史和明确压力假设，不是统计置信区间或收入保证。": "Scenarios use history and explicit stress assumptions, not statistical confidence intervals or guaranteed income.",
  "保守": "Conservative", "基准": "Baseline", "较宽松": "Less conservative",
  "预计收入": "Estimated income", "预计支出": "Estimated spending", "情景净额": "Scenario net amount", "应急金缺口": "Emergency reserve gap",
  "未来情景计入退款：": "Refunds included in future scenarios:", "多个目标共用同一笔现金与结余": "Goals share the same cash and surplus",
  "合计月需预留": "Total monthly reserve needed", "· 基准月缺口": "· Baseline monthly gap", "· 当前现金缺口": "· Current cash gap",
  "低碳权益与预算": "Low-carbon rewards and budget", "当前现金价值": "Current cash value", "，已实现节省": ", realized savings",
  "当前个人低碳积分": "Current low-carbon points", "低碳活动": "Low-carbon activities",
  "减排量为估算值，不是核证碳资产；权益兑换不代表现金到账。": "Emissions avoided are estimates, not certified carbon assets. Reward redemption does not mean cash was received.",
  "个人低碳行为记录": "Personal low-carbon activities", "对应积分": "Associated points", "行为记录数据源": "Activity record source",
  "行为凭据已核验": "Activity evidence verified", "当前没有可展示的行为凭据。": "No activity evidence is available.",
  "当前没有已配置的权益。": "No rewards are configured.", "兑换回执": "Redemption receipts", "查看估算方法与核对": "View estimation method and reconciliation",
  "积分换算": "Points conversion", "g / 积分": "g / point", "积分总账净额": "Net points ledger",
  "已返回核证资产标记": "Certified-asset flag returned", "不是核证碳资产": "Not a certified carbon asset",
  "步行": "Walking", "骑行": "Cycling", "地铁": "Metro", "公交": "Bus",
  "取消数据加载": "Cancel loading", "正在更新可信状态…": "Updating recorded information…",
  "已取消本次只读加载，未改变业务任务。": "This read was cancelled. Business requests were not changed.",
  "让 Agent 梳理": "Prepare a discussion: ",
};

export function extensionPanelUiText(text: string, locale: CustomerLocale): string {
  if (locale !== "en") return text;
  const key = text.replace(/\s+/g, " ").trim();
  return Object.hasOwn(english, key) ? english[key] : translateCustomerText(text, locale);
}

export function extensionScenarioPresentation(label: string, locale: CustomerLocale) {
  const title = translateCustomerText(label, locale);
  return { title,
    sectionAria: locale === "en" ? `${title} information` : `${label}真实数据`,
    refreshAria: locale === "en" ? `Reload ${title}` : `刷新${label}`,
    footerDraft: locale === "en" ? `Prepare a discussion: ${title}` : `让 Agent 梳理${label}` };
}

/** Translate only the interface suffix, never the actual record/actor name. */
export function extensionNamedControl(name: string, label: string, locale: CustomerLocale) {
  return locale === "en" ? `${name}: ${extensionPanelUiText(label, locale)}` : `${name}${label}`;
}

/** Known operation codes acquire display labels; raw API codes remain intact. */
export function extensionProposalLabel(kind: string, locale: CustomerLocale) {
  const label = kind === "TRANSFER" ? "转账" : kind === "CARD_LOCK" ? "锁定卡片" : null;
  return label ? translateCustomerText(label, locale) : kind;
}
