// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText, localizeFinancialValue } from "./customerLanguage";
import { Icon, money, dateText, SpendingChart, type IconName } from "./components";
import { customerName } from "./customerPresentation";
import type { BillAnalysis, TraceNode } from "./types";
import type { ReactNode } from "react";
import "./insightQueryCards.css";
import { billCoverageUnusable, billCoverageNotice, billComparableChange, billAnalysisForDisplay, billPeriodFallback,
  billFallbackIntro, billPeriodLabel, billDateLabel, billMonthLabel } from "./billCoveragePresentation";
import { goalPresentation } from "./goalPresentation";
import { extensionQueryPresentation } from "./extensionQueryPresentation";
import { queryRecordKeys } from "./queryRecordIdentity";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import { customerReadCoverage } from "./customerReadCoverage";
import { OwnedBillingCards } from "./OwnedBillingCards";

type Data = Record<string, unknown>;
type Props = { node: TraceNode; analysis?: BillAnalysis | null; supplementaryAnalysis?: BillAnalysis | null; observedAt?: string | null; narrationInParent?: boolean; pureRiskQuery?: boolean; queryFocus?: string | null; onPrompt?: (message: string) => void };
const queryTitles: Record<string, [string, IconName]> = {
  BILL_ANALYSIS: ["账单与支出", "bill"], FRAUD_ANALYZE: ["转账风险提示", "shield"],
  FAMILY_VIEW: ["家庭协助", "family"], GOAL_VIEW: ["目标与可用资金", "target"], CARBON_VIEW: ["低碳积分与权益", "leaf"],
};
export const supportsInsightQuery = (action: string) => Object.hasOwn(queryTitles, action);
const object = (value: unknown): Data | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Data : null;
const rows = (value: unknown): Data[] | null => Array.isArray(value) && value.every((item) => object(item)) ? value as Data[] : null;
const number = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value);
const count = (value: unknown) => number(value) && value >= 0 ? String(value) : "未返回";
const amount = (value: unknown) => number(value) ? `${money(value, { currency: "CNY" })} 人民币` : "未返回";
const name = (value: unknown, missing = "名称未返回") => typeof value === "string" && value.trim() ? customerName(value) : missing;
const date = (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value)) ? dateText(value, true) : "未返回";
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : null;

function Metric({ label, value }: { label: string; value: ReactNode }) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  return <div className="insight-metric"><dt>{label}</dt><dd>{typeof value === "string" ? localizeFinancialValue(value, locale) : value}</dd></div>;
}
function Draft({ onPrompt, message, children, context }: { onPrompt?: Props["onPrompt"]; message: string; children: ReactNode; context?: string }) {
  const { t: customerUiText } = useCustomerLanguage();
  const label = typeof children === "string" ? children : typeof (children as { props?: { text?: unknown } })?.props?.text === "string" ? customerUiText((children as { props: { text: string } }).props.text) : null;
  return onPrompt ? <button type="button" className="insight-draft-button" onClick={() => onPrompt(message)}
    aria-label={context && label ? `${label}：${context}` : undefined}
    title={customerUiText("只填入对话内容，不会自动办理")}>{children}<Icon name="arrow" size={14} /></button> : null;
}
function Notice({ children }: { children: ReactNode }) {
  const { t: customerUiText } = useCustomerLanguage();
  return <p className="insight-notice"><Icon name="info" size={14} />{children}</p>;
}

function BillCard({ node, analysis, supplementaryAnalysis, observedAt, narrationInParent, onPrompt }: Props) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  // Current banking workflow returns bill totals separately in trace.analysis.
  const result = object(node.result);
  const primary = object(analysis) || object(result?.analysis);
  if (!primary) return <Notice><CustomerText text="账单统计尚未返回，请稍后查看；不能把缺失数据当作零支出。" /></Notice>;
  if (primary.billing_schema_version === "owned-bill-analysis-v2") return <OwnedBillingCards analysis={primary} />;
  const financial=billAnalysisForDisplay(primary as unknown as BillAnalysis,supplementaryAnalysis);
  const data=financial as unknown as Data;
  const categoryFilter=object(data.category_filter);
  const insightFilter=object(object(data.financial_insights)?.category_filter);
  const categoryScoped=categoryFilter?.version === "bill-category-filter-v1" && categoryFilter.basis === "CATEGORY_EXACT_MATCH"
    && categoryFilter.currency === "CNY" && categoryFilter.read_only === true && categoryFilter.bank_state_changed === false
    && typeof categoryFilter.category === "string" && Boolean(categoryFilter.category.trim()) && categoryFilter.category.length<=32
    && insightFilter && Object.keys(categoryFilter).length===Object.keys(insightFilter).length && Object.keys(categoryFilter).every(key=>categoryFilter[key]===insightFilter[key]);
  if (data.category_filter!==undefined && data.category_filter!==null && !categoryScoped)
    return <Notice>{locale === "en" ? "The statement category scope needs verification; no category amount is shown as a full statement total." : "账单筛选条件还需要核对，暂不把分类金额当作整期账单展示。"}</Notice>;
  const coverage = data.coverage as BillAnalysis["coverage"];
  const liveOnly = coverage?.basis === "LIVE_ACTIVITY_ONLY";
  const coverageNotice = billCoverageNotice(coverage, locale);
  const fallback=billPeriodFallback(financial),fallbackIntro=billFallbackIntro(financial,locale);
  const requestedActivity=financial!==(primary as unknown as BillAnalysis) && fallback?.reason==="REQUESTED_PERIOD_ONLY_MOVEMENTS" ? fallback.requested_activity : null;
  const comparableChange = billComparableChange(financial);
  const reviews = (financial.financial_insights?.review_candidates || []).filter(item => item.action === "REVIEW_ONLY" && item.currency === "CNY" && item.unit === "MINOR" && item.guaranteed_saving_minor === null && Number.isSafeInteger(item.amount_minor) && item.amount_minor >= 0).slice(0, 3);
  const reference = financial.reference_display;
  const referenceReady = !liveOnly && reference?.status === "AVAILABLE" && reference.reference_only === true && reference.currency === "USD" && reference.original_currency === "CNY"
    && Number.isSafeInteger(reference.value_minor) && reference.value_minor! >= 0 && Number.isSafeInteger(reference.original_minor) && reference.original_minor === financial.spending_minor;
  const coverageInfo = coverage && <div className="bill-coverage" data-coverage-status={coverage.status}>
    <CustomerResultDisclosure scopeKey={JSON.stringify(coverage)} label={locale === "en" ? "Statement period details" : "查看统计期间"}>
    {coverageNotice && <Notice>{coverageNotice}</Notice>}
    {(coverage.requested_start || coverage.requested_end) && <small>{locale === "en" ? fallback ? "Displayed period" : "Requested period" : fallback ? "当前显示期间" : "所问期间"}: {billDateLabel(coverage.requested_start,locale)} – {billDateLabel(coverage.requested_end,locale,true)}</small>}
    {fallback && <small>{locale === "en" ? "Originally requested" : "原先询问期间"}: {billDateLabel(fallback.requested.start,locale)} – {billDateLabel(fallback.requested.end,locale,true)}</small>}
    <small>{locale === "en" ? "Data through" : "数据截止"}: {coverage.data_through ? billDateLabel(coverage.data_through,locale) : "—"}</small>
    {observedAt && <small>{locale === "en" ? "Retrieved at" : "查询时间"}: {billDateLabel(observedAt,locale,false,true)}</small>}
    </CustomerResultDisclosure>
  </div>;
  if (billCoverageUnusable(coverage)) return <><p className="bill-helpful-intro">{locale === "en" ? "A statement is not available for that period yet." : "这段时间暂时还没有可用账单。"}</p>{coverageInfo}</>;
  if(financial.period_fallback && !fallback)return <><Notice>{locale === "en" ? "The available statement's reporting period needs verification. No amounts are shown as your requested period." : "已有账单的期间信息还需要核对，暂不把这些金额作为所问月份展示。"}</Notice>{coverageInfo}</>;
  const categories = object(data.category_spending_minor);
  const validCategories = categories && Object.values(categories).every((value) => number(value) && value >= 0);
  const chartReady = number(data.spending_minor) && data.spending_minor >= 0 && validCategories
    && Object.values(categories).reduce<number>((sum, value) => sum + (value as number), 0) === data.spending_minor;
  const anomalies = rows(data.anomalies);
  const anomalyKeys = queryRecordKeys(node, anomalies || [], "transaction_id");
  const categorySummary=chartReady ? Object.entries(categories!).sort((left,right)=>(right[1] as number)-(left[1] as number)).slice(0,3) : [];
  const statementRows=rows(data.statement_transactions);
  const statementStart=typeof data.period_start==="string" ? Date.parse(data.period_start) : Number.NaN;
  const statementEnd=typeof data.period_end==="string" ? Date.parse(data.period_end) : Number.NaN;
  const outsideStatement=(row:Data)=>typeof row.occurred_at==="string" && Number.isFinite(Date.parse(row.occurred_at)) && Number.isFinite(statementStart) && Number.isFinite(statementEnd)
    && (Date.parse(row.occurred_at)<statementStart || Date.parse(row.occurred_at)>=statementEnd);
  const statementEntries=(statementRows || []).filter(row=>!outsideStatement(row)).slice(0,50);
  const statementKeys=queryRecordKeys(node,statementEntries,"transaction_id");
  const transactionAmount=(row:Data)=>row.currency==="CNY" && number(row.amount_minor) ? `${money(row.amount_minor,{currency:"CNY",signed:true})}${locale==="en" ? " CNY" : " 元"}` : locale==="en" ? "Amount or currency unverified" : "金额或币种待核实";
  const statementList=(entries:Data[],offset=0)=><ul className="bill-transaction-list" aria-label={locale==="en" ? "Statement transactions" : "账单明细"}>{entries.map((row,index)=><li key={statementKeys[offset+index]}><div><strong>{name(row.merchant,locale==="en" ? "Transaction name unavailable" : "交易名称待核实")}</strong><small>{billDateLabel(row.occurred_at,locale)} · {locale==="en" ? ({MOVEMENT:"Funds movement",REFUND:"Refund",EXPENSE:"Expense",INCOME:"Income"} as Record<string,string>)[String(row.cash_flow_type)] || "Type unverified" : ({MOVEMENT:"资金转移",REFUND:"退款",EXPENSE:"支出",INCOME:"收入"} as Record<string,string>)[String(row.cash_flow_type)] || "类型待核实"}</small></div><span>{transactionAmount(row)}</span></li>)}</ul>;
  return <>
    {fallbackIntro && !narrationInParent && <p className="bill-helpful-intro">{fallbackIntro}</p>}
    <p className="bill-period-label">{billPeriodLabel(financial,locale)}{categoryScoped && <> · {name(categoryFilter!.category)}{data.transaction_count===0 ? <small>{locale === "en" ? " · no matching records" : " · 暂无匹配记录"}</small> : null}</>}{coverage?.status==="PARTIAL" ? <small>{locale === "en" ? " · Available records" : " · 已有记录"}</small> : null}</p>
    {requestedActivity && <p className="bill-requested-activity">
      {locale==="en" ? `${billMonthLabel(requestedActivity.period_start,locale)} recorded funds movements: ${requestedActivity.transaction_count}`
        : `${billMonthLabel(requestedActivity.period_start,locale)}已记录资金活动：${requestedActivity.transaction_count}笔`}
      {requestedActivity.movement_in_minor>0 && <> · {locale==="en" ? "Incoming " : "转入 "}{localizeFinancialValue(amount(requestedActivity.movement_in_minor),locale)}</>}
      {requestedActivity.movement_out_minor>0 && <> · {locale==="en" ? "Outgoing " : "转出 "}{localizeFinancialValue(amount(requestedActivity.movement_out_minor),locale)}</>}
    </p>}
    {liveOnly && <p className="bill-availability-note">{locale === "en" ? "Only newly posted activity is available." : "目前仅查到新入账活动。"}</p>}
    <dl className="insight-metrics insight-compact-metrics">
      {!liveOnly && <Metric label={categoryScoped ? locale==="en" ? "Category spending" : "该分类支出" : customerUiText("消费支出")} value={amount(data.spending_minor)} />}
      <Metric label={customerUiText("流水笔数")} value={count(data.transaction_count)} />
      {number(data.income_minor) && (!categoryScoped || data.income_minor > 0) && (!liveOnly || data.income_minor > 0) && <Metric label={categoryScoped ? locale === "en" ? "Income in this category" : "该分类收入" : customerUiText("收入")} value={amount(data.income_minor)} />}
    </dl>
    {categorySummary.length>0 && !(categoryScoped && categorySummary.length===1 && categorySummary[0][0]===categoryFilter!.category && categorySummary[0][1]===data.spending_minor) && <ul className="bill-category-summary" aria-label={locale === "en" ? "Main spending categories" : "主要支出分类"}>{categorySummary.map(([category,value])=><li key={category}><span>{locale==="en" ? financial.financial_insights?.category_labels_en?.[category] || customerUiText(category) : category}</span><strong>{amount(value)}</strong></li>)}</ul>}
    {statementEntries.length>0 && <div className="bill-transaction-preview"><h4>{locale==="en" ? "Recent transactions" : "最近账单"}</h4>{statementList(statementEntries.slice(0,3))}
      {(data.statement_transactions_truncated===true || number(data.transaction_count) && data.transaction_count>statementEntries.length) && <small className="bill-availability-note">{locale==="en" ? `Showing the latest ${statementEntries.length} transactions; the summary includes all ${count(data.transaction_count)} recorded transactions.` : `明细仅列最近 ${statementEntries.length} 笔，汇总包含已取得的全部 ${count(data.transaction_count)} 笔流水。`}</small>}
    </div>}
    {data.statement_transactions!==undefined && statementRows===null && <p className="bill-availability-note">{locale==="en" ? "Transaction details are incomplete; this does not mean there are none." : "逐笔明细尚未完整返回，不代表没有账单记录。"}</p>}
    {statementRows?.some(outsideStatement) && <p className="bill-availability-note">{locale==="en" ? "Some transaction dates do not match this statement and are not included here." : "部分明细日期与当前账单期间不一致，未混入展示。"}</p>}
    {!liveOnly && anomalies && anomalies.length > 0 && <p className="insight-notice">{locale === "en" ? `${anomalies.length} transactions need review. Open details to check them.` : `有 ${anomalies.length} 笔扣款需要留意，展开查看明细。`}</p>}
    {!liveOnly && !chartReady && <Notice><CustomerText text="支出分类数据尚未完整返回或与总额未核对一致。" /></Notice>}
    <CustomerResultDisclosure scopeKey={JSON.stringify(data)} context={customerUiText("账单与支出")}>
    {statementEntries.length>3 && <div className="bill-transaction-more"><h4>{locale==="en" ? "More transactions" : "更多账单"}</h4>{statementList(statementEntries.slice(3),3)}</div>}
    {coverageInfo}
    {referenceReady && <div className="bill-key-insight bill-reference-display"><strong>{locale === "en" ? "Approximate spending equivalent" : "消费支出的参考换算"}</strong><span>≈ {money(reference!.value_minor!, { currency: "USD" })} USD</span><small>{locale === "en" ? "Original posted consumption" : "原入账消费"}: {money(reference!.original_minor!)} CNY</small>
      <small>{locale === "en" ? "Reference conversion only; no exchange or money movement occurred." : "仅为参考换算，未发生换汇或资金转移。"}</small>
      <small>{locale === "en" ? "Reference rate and source" : "参考汇率与来源"}: USD/CNY {reference!.rate || "—"} · {reference!.provider || "—"} · {reference!.market_date || "—"}</small>{reference!.fetched_at && <small>{locale === "en" ? "Retrieved at" : "读取于"}: {date(reference!.fetched_at)}</small>}
    </div>}
    {comparableChange !== null && <div className="bill-key-insight"><strong>{locale === "en" ? "Spending change across comparable periods" : "可比期间的消费变化"}</strong><span>{money(comparableChange, { currency: "CNY", signed: true })} CNY</span>
      {financial.financial_insights?.comparison?.zero_baseline === true && <small>{locale === "en" ? "No recorded consumption in the comparison baseline; no percentage or multiple is calculated." : "对照期没有已记录消费，不计算增长比例或倍数。"}</small>}
      <small>{locale === "en" ? "Based on posted consumption, not transfers or a forecast." : "依据已入账消费，不包含资金搬移，也不是预测。"}</small></div>}
    {reviews.length > 0 && <div className="bill-review-candidates"><strong>{locale === "en" ? "Spending areas to review" : "可以先核对的支出"}</strong><ul>{reviews.map((item, index) => <li key={index}><span>{locale === "en" ? financial.financial_insights?.category_labels_en?.[item.category] || customerUiText(item.category) : item.category}</span><strong>{money(item.amount_minor)} CNY</strong></li>)}</ul><small>{locale === "en" ? "These are posted amounts to review, not promised savings. Essential expenses may be necessary." : "以上是已发生、可核对的支出，不是承诺可省金额；必要开支仍可能需要保留。"}</small></div>}
    <dl className="insight-metrics">
      {!liveOnly && <Metric label={customerUiText("退款")} value={amount(data.refund_minor)} />}
      {number(data.net_minor) && <Metric label={customerUiText("现金净变动")} value={amount(data.net_minor)} />}
      {number(data.movement_in_minor) && data.movement_in_minor > 0 && <Metric label={customerUiText("资金转入")} value={amount(data.movement_in_minor)} />}
      {number(data.movement_out_minor) && data.movement_out_minor > 0 && <Metric label={customerUiText("资金转出")} value={amount(data.movement_out_minor)} />}
    </dl>
    {!liveOnly && chartReady && <SpendingChart analysis={data as unknown as BillAnalysis} />}
    {!liveOnly && <div className="insight-section">
      <h4><CustomerText text="需要留意的扣款" /></h4>
      {anomalies === null ? <p><CustomerText text="异常线索尚未返回。" /></p> : anomalies.length === 0 ? <p><CustomerText text="本期没有需要单列核对的异常线索。" /></p> : <ul className="insight-records">{anomalies.map((item, index) => <li key={anomalyKeys[index]}>
        <div><strong>{name(item.merchant, customerUiText("待核对交易"))}</strong><span>{amount(item.amount_minor)}</span></div>
        {text(item.reason) && <p>{item.reason as string}</p>}
        <small>{text(item.occurred_at) ? `${date(item.occurred_at)} · ` : ""}<CustomerText text="请结合实际流水核对，不自动取消或锁卡。" /></small>
      </li>)}</ul>}
    </div>}
    <Draft onPrompt={onPrompt} message={customerUiText("查询自动续费")}><CustomerText text="看看自动续费" /></Draft>
    </CustomerResultDisclosure>
  </>;
}

const riskReasons: Record<string, string> = {
  SAFETY_ACCOUNT_INDUCEMENT: "出现转入所谓安全账户的要求", ADVANCE_FEE_INDUCEMENT: "出现先付款换取返利或奖励的要求",
  AUTHORITY_IMPERSONATION: "出现冒充公检法的汇款要求", SECRECY_INDUCEMENT: "出现要求隐瞒家人或银行的线索",
  REMOTE_ACCESS_INDUCEMENT: "远程控制或屏幕共享涉及账户、密码或支付", REFUND_ADVANCE_FEE_INDUCEMENT: "要求先付费或缴保证金才能退款",
  CREDENTIAL_COLLECTION_INDUCEMENT: "有人索要验证码或支付密码",
  RECIPIENT_CONTEXT_NOT_RESOLVED: "收款人尚未唯一核对", REGISTERED_RECIPIENT_UNAVAILABLE: "收款账号当前不可用",
  KNOWN_SYNTHETIC_CONTACT_NO_RISK_FLAG: "当前已登记联系人未触发风险标记", SYNTHETIC_CONTACT_NOT_FLAGGED: "当前已登记联系人未触发风险标记",
};
function FraudCard({ node, onPrompt,pureRiskQuery=false }: Props) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const risk = object(object(node.result)?.risk);
  if (!risk) return <Notice><CustomerText text="风险核验结果尚未返回，请勿据此认定转账安全。" /></Notice>;
  const blocked = risk.decision === "BLOCK", allowed = risk.decision === "ALLOW";
  const reasons = Array.isArray(risk.reason_codes) ? risk.reason_codes.filter((value): value is string => typeof value === "string") : null;
  return <>
    <p className={`insight-risk ${blocked ? "is-blocked" : allowed ? "is-checked" : "is-unknown"}`}>
      {blocked ? pureRiskQuery ? locale==="en" ? "High-risk signs detected; do not transfer on this request" : "发现高风险，请勿依照这项要求转账" : customerUiText("发现高风险，暂不能转账") : allowed ? customerUiText("当前未触发拦截规则") : customerUiText("信息尚未核清，需要继续核对")}
    </p>
    {blocked && <p className="insight-risk-next">{locale === "en" ? "Stop contact and transfers. Do not share your screen or verification codes. Check independently through the bank or platform's official channels. If money has been lost, keep the evidence and contact your bank and the police." : "先停止联系与转账，不要屏幕共享或提供验证码。自行通过银行或平台官方渠道核实；若资金已受损，保留证据并联系银行与警方。"}</p>}
    {text(risk.payee_name) && <p><CustomerText text="核对对象：" />{name(risk.payee_name)}</p>}
    {reasons?.length ? <ul className="insight-bullets">{reasons.map((reason, index) => <li key={index}>{customerUiText(riskReasons[reason] || "有一项线索仍需进一步核对")}</li>)}</ul> : <p><CustomerText text="具体风险线索尚未返回。" /></p>}
    <Notice>{blocked ? customerUiText("风险拦截不能通过普通确认跳过。") : allowed ? customerUiText("未触发规则不等于没有风险；办理仍需核对收款人和金额。") : customerUiText("请补充收款人及对方的具体要求，不把未知当作安全。")}<CustomerText text="本次查询没有转账。" /></Notice>
    <CustomerResultDisclosure scopeKey={JSON.stringify(risk)} context={customerUiText("转账风险提示")}>
    <Draft onPrompt={onPrompt} message={customerUiText("检查转账风险，我收到的要求是：")}><CustomerText text="补充转账背景" /></Draft>
    </CustomerResultDisclosure>
  </>;
}

function FamilyCard({ node, onPrompt, queryFocus }: Props) {
  const { locale } = useCustomerLanguage();
  const presentation = extensionQueryPresentation(locale), copy = presentation.family;
  const data = object(node.result) || {};
  const coverage = customerReadCoverage("FAMILY_VIEW", data);
  if (coverage.status === "UNAVAILABLE_SCOPE") return <Notice>{locale === "en"
    ? "I couldn't verify which family member this result belongs to. Please ask me to check again."
    : "暂时无法核对这条结果对应的家人，请再告诉我查哪位家人。"}</Notice>;
  if (coverage.status === "FILTERED" && coverage.balance_authorization) return <Notice>{locale === "en"
    ? `You do not currently have permission to view this balance. Ask ${coverage.balance_authorization.owner_name} to grant access again.`
    : `当前没有余额查看授权，请${coverage.balance_authorization.owner_name}本人重新授权。`}</Notice>;
  const selectedFamily = coverage.status === "FILTERED";
  const actor = object(data.current_actor), permissions = object(data.permissions), delegations = rows(data.delegations);
  const helper = actor?.role === "FAMILY_HELPER", owner = actor?.role === "OWNER";
  const proposals = rows(data.proposals), balances = rows(data.authorized_balances);
  const delegationKeys = queryRecordKeys(node, delegations || [], "delegation_id");
  const visibleBalances = balances?.filter(balance => text(balance.owner_name) && delegations?.some(row => row.valid === true && text(row.owner_name)
    && row.owner_name === balance.owner_name && Array.isArray(row.scopes) && row.scopes.includes("READ_BALANCE")));
  const balanceKeys = queryRecordKeys(node, visibleBalances || [], "owner_user_id");
  // Focus is a server-verified projection, never inferred from message words.
  // Legacy results remain useful: helpers see their authorized balances first,
  // while owners see permissions. This does not widen the returned scope.
  const balanceFocus = queryFocus === "FAMILY_AUTHORIZED_BALANCES" || helper && queryFocus !== "FAMILY_DELEGATIONS";
  const delegationList = (entries: Data[], compact = false) => <ul className="insight-records">{entries.map((row, index) => {
    const scopes = Array.isArray(row.scopes) ? row.scopes.filter((value): value is string => typeof value === "string") : null;
    return <li key={delegationKeys[index]}>
      <div><strong>{name(row.owner_name, copy.missingName)} → {name(row.grantee_name, copy.missingName)}</strong><span className={row.valid === true ? "insight-active" : "insight-muted"}>{row.valid === true ? copy.valid : row.valid === false ? copy.expired : copy.unknownValidity}</span></div>
      <p>{scopes?.length ? scopes.map((scope) => copy.scopeLabels[scope] || copy.unknownScope).join(locale === "en" ? ", " : "、") : copy.missingScopes}</p>
      {!compact && scopes?.includes("PROPOSE_TRANSFER") && <p>{row.limit_period === "MONTH" ? copy.monthlyLimit : row.limit_period === "PER_PROPOSAL" || row.limit_period === "TRANSACTION" ? copy.proposalLimit : copy.unknownLimitPeriod}: {presentation.amount(row.maximum_amount_minor)}{row.limit_period === "MONTH" ? ` · ${copy.monthlyUsed}: ${presentation.amount(row.month_used_minor)}` : ""}</p>}
      <small>{copy.expires}: {presentation.date(row.expires_at)}{row.valid === false ? ` · ${copy.expiredBoundary}` : ""}</small>
    </li>;
  })}</ul>;
  const noDelegations = delegations === null ? copy.missingDelegations : selectedFamily
    ? locale === "en" ? "No delegation was found for this family member." : "这位家人暂时没有对应的授权记录。" : copy.emptyDelegations;
  const canDisplayBalances = helper && permissions?.can_read_owner_balance === true;
  const balancesSummary = canDisplayBalances && visibleBalances?.length ? <dl className="insight-metrics">
    {visibleBalances.slice(0, 3).map((balance, index) => <Metric key={balanceKeys[index]} label={name(balance.owner_name, copy.missingName)} value={presentation.amount(balance.balance_minor)} />)}
  </dl> : <p>{!permissions || !balances ? locale === "en" ? "The authorized balance is not available yet." : "授权余额暂未取得。"
    : locale === "en" ? "No balance is available under the current family permissions." : "当前家庭授权范围内没有可查看的余额。"}</p>;
  return <>
    <div className="family-answer" data-family-focus={balanceFocus ? "BALANCES" : "DELEGATIONS"}>
      <h4>{balanceFocus ? copy.authorizedBalances : selectedFamily ? locale === "en" ? "This family member's permissions" : "这位家人的授权" : copy.delegations}</h4>
      {balanceFocus ? balancesSummary : delegations?.length ? delegationList(delegations.slice(0, 3), true) : <p>{noDelegations}</p>}
      {(balanceFocus ? visibleBalances?.length || 0 : delegations?.length || 0) > 3 && <p className="insight-subtitle">{locale === "en" ? "More records are available in details." : "其余记录可展开查看。"}</p>}
    </div>
    <CustomerResultDisclosure scopeKey={JSON.stringify(data)} context={copy.delegations}>
    <p className="insight-subtitle">{name(actor?.display_name, copy.missingIdentity)} · {helper ? copy.helper : owner ? copy.owner : copy.unknownRole}</p>
    {permissions ? <p>{owner && permissions.can_grant === true ? copy.ownerScope : helper ? copy.helperScope : copy.unavailablePermissions}</p> : <Notice>{copy.missingPermissions}</Notice>}
    <Notice>{copy.confirmationBoundary}</Notice>
    <div className="insight-section"><h4>{copy.delegations}</h4>
      {delegations?.length ? delegationList(delegations) : <p>{noDelegations}</p>}
    </div>
    {canDisplayBalances && visibleBalances && (!balanceFocus || visibleBalances.length > 3) && <div className="insight-section"><h4>{copy.authorizedBalances}</h4><dl className="insight-metrics">{visibleBalances.map((balance, index) => <Metric key={balanceKeys[index]} label={name(balance.owner_name, copy.missingName)} value={presentation.amount(balance.balance_minor)} />)}</dl></div>}
    {proposals && <p>{selectedFamily ? locale === "en" ? `Proposals for this family member: ${proposals.length}` : `这位家人的协助提案：${proposals.length} 项` : copy.proposals(proposals.length)}</p>}
    {owner && permissions?.can_grant === true && <Draft onPrompt={onPrompt} message={copy.setupDraft}>{copy.setupLabel}</Draft>}
    </CustomerResultDisclosure>
  </>;
}

function GoalCard({ node, onPrompt }: Props) {
  const { locale } = useCustomerLanguage();
  const presentation = goalPresentation(locale);
  const data = object(node.result) || {}, goals = rows(data.goals), forecast = object(data.forecast);
  const goalKeys = queryRecordKeys(node, goals || [], "goal_id");
  const scenarios = object(forecast?.scenarios);
  const portfolio = presentation.portfolio(data.portfolio);
  const portfolioComplete = Object.values(portfolio).every(value => value !== null);
  const whatIf=presentation.whatIf(data.what_if,goals);
  return <>
    {whatIf && <section className="insight-what-if" aria-label={locale === "en" ? "Savings scenario comparison" : "储蓄方案对比"}>
      <h4>{name(whatIf.goalName,presentation.missingName)}</h4>
      <p>{whatIf.improvedMonths===null ? locale==="en" ? "The new plan saves nothing each month; no completion month can currently be estimated." : "新计划每月不再储蓄，暂时无法预计完成月份。"
        : whatIf.baselineMonths === null ? locale === "en" ? `The original plan saves nothing each month. A time saving cannot be compared; the new plan estimates ${whatIf.improvedMonths} months.` : `原计划每月没有储蓄，无法比较提前多久；新计划约需 ${whatIf.improvedMonths} 个月。`
        : locale === "en" ? `Estimated ${whatIf.baselineMonths} → ${whatIf.improvedMonths} months (${whatIf.monthsSaved!>0 ? `${whatIf.monthsSaved} ${whatIf.monthsSaved===1 ? "month" : "months"} sooner` : whatIf.monthsSaved!<0 ? `${-whatIf.monthsSaved!} ${whatIf.monthsSaved===-1 ? "month" : "months"} later` : "no change in the estimate"}).` : `预计 ${whatIf.baselineMonths} → ${whatIf.improvedMonths} 个月，${whatIf.monthsSaved!>0 ? `可提前 ${whatIf.monthsSaved} 个月` : whatIf.monthsSaved!<0 ? `约晚 ${-whatIf.monthsSaved!} 个月` : "预计月份与原计划相同"}。`}</p>
      <CustomerResultDisclosure scopeKey={JSON.stringify(data.what_if)} label={locale === "en" ? "View assumptions" : "查看估算条件"}>
        <p>{locale === "en" ? "Original monthly saving" : "原每月储蓄"}: {presentation.amount(whatIf.monthly)} · {whatIf.newTotal!==null ? locale==="en" ? "New total monthly saving" : "新的每月总储蓄" : locale === "en" ? "Extra monthly saving" : "每月额外储蓄"}: {presentation.amount(whatIf.newTotal??whatIf.delta)}</p>
        <p>{locale === "en" ? "The stated monthly plan is used for this goal, with other conditions unchanged. Months are rounded up; unreserved cash and other goals are not included. This is an estimate, not a guaranteed date. No money was reserved or moved." : "按所述每月计划为该目标储蓄，其他条件不变；按整月向上取整，未预留现金和其他目标资金不自动计入。这只是估算，不保证日期，也没有调整预留或转账。"}</p>
      </CustomerResultDisclosure>
    </section>}
    {data.what_if !== undefined && !whatIf && <Notice>{locale === "en" ? "The savings comparison needs verification; no completion estimate is shown." : "储蓄对比条件还需要核对，暂不展示完成时间估算。"}</Notice>}
    <dl className="insight-metrics insight-compact-metrics"><Metric label={presentation.totalCash} value={presentation.amount(data.account_balance_minor)} /><Metric label={presentation.spendableCash} value={presentation.amount(data.spendable_after_emergency_minor)} /></dl>
    {goals && goals.length > 0 && <ul className="insight-compact-records" aria-label={locale === "en" ? "Goal progress and gaps" : "目标进度与缺口"}>{goals.map((row,index) => {
      const progress = typeof row.progress === "number" && Number.isFinite(row.progress) && row.progress >= 0 && row.progress <= 1 ? row.progress : null;
      return <li key={goalKeys[index]}><strong>{name(row.goal_name,presentation.missingName)}</strong><span>{progress === null ? presentation.progressUnknown : presentation.progressText((progress * 100).toFixed(1))}</span><span>{presentation.remaining}: {presentation.amount(row.remaining_minor)}</span></li>;
    })}</ul>}
    {goals === null && <Notice>{presentation.missingGoals}</Notice>}
    {goals?.length && !portfolioComplete ? <Notice>{presentation.portfolioUnknown}</Notice> : null}
    {forecast?.sample_status !== "SUFFICIENT_FOR_SCENARIO" && <Notice>{presentation.sample(forecast?.sample_status)}</Notice>}
    <CustomerResultDisclosure scopeKey={JSON.stringify(data)} context={presentation.portfolioTitle}>
    <section className="insight-goal-portfolio" aria-label={presentation.portfolioTitle}
      data-goal-portfolio-state={goals?.length ? portfolioComplete ? "RETURNED" : "INCOMPLETE" : "UNAVAILABLE"}>
      <h4>{presentation.portfolioTitle}</h4>
      <p>{presentation.sharedFunds}</p>
      {goals?.length ? <>
        <dl className="insight-metrics insight-portfolio-metrics">
          <Metric label={presentation.monthlyGap} value={presentation.amount(portfolio.monthlyGap)} />
          <Metric label={presentation.currentCashGap} value={presentation.amount(portfolio.currentCashGap)} />
          <Metric label={presentation.requiredMonthly} value={presentation.amount(portfolio.requiredMonthly)} />
          <Metric label={presentation.monthlyCapacity} value={presentation.amount(portfolio.monthlyCapacity)} />
        </dl>
        {!portfolioComplete && <p className="insight-portfolio-warning">{presentation.portfolioUnknown}</p>}
        {forecast?.sample_status !== "SUFFICIENT_FOR_SCENARIO" && <p className="insight-portfolio-warning">{presentation.sample(forecast?.sample_status)}</p>}
        <small>{presentation.portfolioSource}</small>
      </> : <p className="insight-portfolio-warning">{goals === null ? presentation.missingGoals : presentation.portfolioEmpty}</p>}
    </section>
    <dl className="insight-metrics insight-goal-cash">
      <Metric label={presentation.totalCash} value={presentation.amount(data.account_balance_minor)} /><Metric label={presentation.reservedCash} value={presentation.amount(data.goal_reserved_minor)} />
      <Metric label={presentation.spendableCash} value={presentation.amount(data.spendable_after_emergency_minor)} />
    </dl>
    <p className="insight-subtitle">{presentation.reserveExplanation}</p>
    {goals === null ? null : goals.length === 0 ? <p>{presentation.emptyGoals}</p> : <ul className="insight-records insight-goal-records">{goals.map((row, index) => {
      const progress = typeof row.progress === "number" && Number.isFinite(row.progress) && row.progress >= 0 && row.progress <= 1 ? row.progress : null;
      const percentage = progress === null ? null : (progress * 100).toFixed(1);
      const goalName = name(row.goal_name, presentation.missingName);
      return <li key={goalKeys[index]}>
        <div><strong>{goalName}</strong><span>{presentation.feasibility(row, forecast?.sample_status)}</span></div>
        <p>{presentation.target}: {presentation.amount(row.target_minor)} · {presentation.reserved}: {presentation.amount(row.reserved_minor)} · {presentation.remaining}: {presentation.amount(row.remaining_minor)}</p>
        {progress !== null && <div className="insight-goal-progress" role="progressbar"
          aria-label={presentation.progressLabel(goalName)} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={Number(percentage)} aria-valuetext={presentation.progressValue(percentage!)}>
          <span style={{ width: `${progress * 100}%` }} aria-hidden="true" />
        </div>}
        <p className="insight-progress-text">{percentage === null ? presentation.progressUnknown : presentation.progressText(percentage)}</p>
        <small>{presentation.targetDate}: {presentation.date(row.due_date)} · {number(row.monthly_suggested_minor) && row.monthly_suggested_minor >= 0 ? presentation.monthlyText(presentation.amount(row.monthly_suggested_minor)) : presentation.monthlyUnknown}</small>
        {row.status === "ACTIVE" && text(row.goal_name) && <Draft onPrompt={onPrompt} message={presentation.reserveDraft(row.goal_name as string)} context={goalName}>{presentation.reserveDraftLabel}</Draft>}
      </li>;
    })}</ul>}
    <div className="insight-section"><h4>{presentation.forecastTitle}</h4>
      {!forecast ? <p>{presentation.forecastUnknown}</p> : <>
        <p>{presentation.horizon(forecast.horizon_days)} · {presentation.sample(forecast.sample_status)}</p>
        {scenarios ? <dl className="insight-metrics insight-goal-scenarios">{presentation.scenarios.map(([key, label]) => <Metric key={key} label={label} value={presentation.amount(object(scenarios[key])?.net_90d_minor, true)} />)}</dl> : <p>{presentation.scenarioUnknown}</p>}
      </>}
    </div>
    <Notice>{presentation.forecastBoundary}</Notice>
    {goals?.length === 0 && <Draft onPrompt={onPrompt} message={presentation.setupDraft}>{presentation.setupLabel}</Draft>}
    </CustomerResultDisclosure>
  </>;
}

function CarbonCard({ node, onPrompt,queryFocus }: Props) {
  const { locale } = useCustomerLanguage();
  const presentation = extensionQueryPresentation(locale), copy = presentation.carbon;
  const data = object(node.result) || {}, wallet = object(data.wallet), events = rows(data.events), rewards = rows(data.rewards), redemptions = rows(data.redemptions);
  const coverage = customerReadCoverage(node.action, data);
  const activitiesQueried = coverage.status === "UNFILTERED" || coverage.status === "FILTERED" && coverage.selected_dimensions.includes("ACTIVITIES");
  const rewardsQueried = coverage.status === "UNFILTERED" || coverage.status === "FILTERED" && coverage.selected_dimensions.includes("REWARDS");
  const eventKeys = queryRecordKeys(node, events || [], "behavior_id");
  const rewardKeys = queryRecordKeys(node, rewards || [], "reward_id");
  const redemptionKeys = queryRecordKeys(node, redemptions || [], "redemption_id");
  const claimFocus=activitiesQueried && (queryFocus==="CARBON_CLAIMABLE_RECORDS" || !rewardsQueried);
  const claimable=(events || []).map((row,index)=>({row,index})).filter(({row})=>row.claimable===true && row.claimed===false && row.method_supported===true && number(row.estimated_points) && row.estimated_points>0);
  const claimStateKnown=events!==null && events.every(row=>typeof row.claimable==="boolean" && typeof row.claimed==="boolean" && typeof row.method_supported==="boolean" && number(row.estimated_points) && row.estimated_points>=0);
  const compactClaims=<>{events===null || !claimStateKnown && !claimable.length ? <p>{locale==="en" ? "Some activity conditions are missing; no claim eligibility is assumed." : "部分行为领取条件尚未返回，暂不能认定可领取。"}</p> : !claimable.length ? <p>{locale==="en" ? "No recorded activity currently meets the claim conditions." : "当前没有满足领取条件的行为记录。"}</p> : <ul className="insight-compact-records" aria-label={locale==="en" ? "Claimable low-carbon activities" : "可领取的低碳行为"}>{claimable.slice(0,3).map(({row,index})=><li key={eventKeys[index]}><strong>{name(row.behavior_name,copy.missingName)}</strong><span>{presentation.date(row.occurred_at)}</span><span>{presentation.count(row.estimated_points)} {copy.pointsUnit}</span></li>)}</ul>}
    {claimable.length>3 && <p className="insight-notice">{locale==="en" ? "More claimable records are in the details." : "更多可领取记录在详情里。"}</p>}
    {!claimStateKnown && claimable.length>0 && <Notice>{locale==="en" ? "Other records have conditions that still need verification." : "其他记录的领取条件还需要核对。"}</Notice>}</>;
  const compactRewards=<>{rewards === null ? <p>{copy.missingRewards}</p> : rewards.length === 0 ? <p>{copy.emptyRewards}</p> : <ul className="insight-compact-records" aria-label={copy.rewards}>{rewards.map((row,index) => {
      const walletKnown = number(wallet?.points) && wallet.points >= 0, costKnown = number(row.cost_points) && row.cost_points > 0;
      const eligibilityKnown = walletKnown && costKnown && number(row.stock) && row.stock >= 0 && typeof row.can_redeem === "boolean";
      const available = eligibilityKnown && row.can_redeem === true && (row.stock as number) > 0 && (wallet!.points as number) >= (row.cost_points as number);
      const gap = walletKnown && costKnown ? Math.max(0,(row.cost_points as number) - (wallet!.points as number)) : null;
      return <li key={rewardKeys[index]}><strong>{name(row.reward_name,copy.missingName)}</strong><span>{locale === "en" ? "Required" : "兑换需"} {presentation.count(row.cost_points)} {copy.pointsUnit}</span><span>{row.stock === 0 ? copy.outOfStock : available ? copy.rewardEligible : eligibilityKnown ? gap && gap > 0 ? locale === "en" ? `${gap} more points needed` : `还差 ${gap} 积分` : copy.notRedeemable : copy.unknownEligibility}</span></li>;
    })}</ul>}</>;
  const selectedActivities = coverage.status === "FILTERED" && activitiesQueried ? <ul className="insight-compact-records" aria-label={copy.activities}>{(events || []).map((row,index) =>
    <li key={eventKeys[index]}><strong>{name(row.behavior_name,copy.missingName)}</strong><span>{presentation.date(row.occurred_at)}</span><span>{presentation.count(row.estimated_points)} {copy.pointsUnit} · {copy.claimStatus(row)}</span></li>)}</ul> : compactClaims;
  if (coverage.status === "UNAVAILABLE_SCOPE") return <Notice>{locale === "en" ? "The original query scope could not be verified. Please query again; missing records are not treated as zero." : "原查询范围暂时无法核对，请重新查询；未核验的记录不会当作零记录。"}</Notice>;
  return <>
    {claimFocus ? selectedActivities : <><dl className="insight-metrics"><Metric label={copy.points} value={presentation.count(wallet?.points)} /></dl>{rewardsQueried && compactRewards}</>}
    {activitiesQueried && events?.some(row => row.method_supported === false) && <Notice>{copy.unsupportedNotice}</Notice>}
    <CustomerResultDisclosure scopeKey={JSON.stringify(data)} context={copy.points}>
    {claimFocus && <><dl className="insight-metrics"><Metric label={copy.points} value={presentation.count(wallet?.points)} /></dl>{rewardsQueried && compactRewards}</>}
    {activitiesQueried && <div className="insight-section"><h4>{copy.activities}</h4>
      {events === null ? <p>{copy.missingActivities}</p> : events.length === 0 ? <p>{copy.emptyActivities}</p> : <ul className="insight-records">{events.map((row, index) => <li key={eventKeys[index]}>
        <div><strong>{name(row.behavior_name, copy.missingName)}</strong><span>{copy.claimStatus(row)}</span></div>
        <small>{copy.modeLabels[String(row.mode)] || copy.unknownMode} · {presentation.distance(row.distance_m)} · {presentation.date(row.occurred_at)}</small>
        {row.method_supported === false ? <p>{copy.unsupportedNotice}</p> : <p>{copy.avoidedEmissions}: {presentation.avoidedEmissions(row.estimated_avoided_grams)} · {copy.estimatedPoints}: {presentation.count(row.estimated_points)}</p>}
        {row.claimable === true && row.claimed === false && row.method_supported === true && text(row.behavior_name) && <Draft onPrompt={onPrompt} message={copy.claimDraft(row.behavior_name as string)} context={name(row.behavior_name, copy.missingName)}>{copy.claimLabel}</Draft>}
      </li>)}</ul>}
    </div>}
    {rewardsQueried && <div className="insight-section"><h4>{copy.rewards}</h4>
      {rewards === null ? <p>{copy.missingRewards}</p> : rewards.length === 0 ? <p>{copy.emptyRewards}</p> : <ul className="insight-records">{rewards.map((row, index) => {
        const available = row.can_redeem === true && number(row.stock) && row.stock > 0 && number(row.cost_points) && row.cost_points > 0 && number(wallet?.points) && wallet.points >= row.cost_points;
        return <li key={rewardKeys[index]}><div><strong>{name(row.reward_name, copy.missingName)}</strong><span>{row.stock === 0 ? copy.outOfStock : available ? copy.rewardEligible : typeof row.can_redeem === "boolean" && number(row.stock) && row.stock >= 0 && number(row.cost_points) && row.cost_points > 0 && number(wallet?.points) && wallet.points >= 0 ? copy.notRedeemable : copy.unknownEligibility}</span></div>
          <p>{presentation.count(row.cost_points)} {copy.pointsUnit} · {copy.remainingStock}: {presentation.count(row.stock)}</p>
          {available && text(row.reward_name) && <Draft onPrompt={onPrompt} message={copy.redeemDraft(row.reward_name as string)} context={name(row.reward_name, copy.missingName)}>{copy.redeemLabel}</Draft>}
        </li>;
      })}</ul>}
    </div>}
    {coverage.status === "UNFILTERED" && redemptions && redemptions.length > 0 && <div className="insight-section"><h4>{copy.redeemedRewards}</h4><ul className="insight-records">{redemptions.map((row, index) => <li key={redemptionKeys[index]}><strong>{name(row.reward_name, copy.missingName)}</strong><small>{presentation.count(row.cost_points)} {copy.pointsUnit} · {row.status === "ISSUED_SIMULATION" ? copy.redeemed : copy.unknownRedemption}</small></li>)}</ul></div>}
    <Notice>{copy.boundary}</Notice>
    </CustomerResultDisclosure>
  </>;
}

export function InsightQueryCards(props: Props) {
  const { locale,t: customerUiText } = useCustomerLanguage();
  if (!supportsInsightQuery(props.node.action)) return null;
  const title = queryTitles[props.node.action];
  const pending = props.node.status !== "SUCCEEDED";
  return <section className="insight-query-card" aria-label={customerUiText(title[0])} data-query-action={props.node.action}>
    <h3><Icon name={title[1]} size={19} />{props.node.action==="CARBON_VIEW" && props.queryFocus==="CARBON_CLAIMABLE_RECORDS" ? locale==="en" ? "Claimable low-carbon activities" : "可领取的低碳行为" : customerUiText(title[0])}</h3>
    {pending ? <Notice>{props.node.status === "UNKNOWN" ? customerUiText("查询结果尚未确定，请稍后查看，不重复提交。") : props.node.status === "FAILED" ? customerUiText("本次查询未完成，请查看办理说明。") : customerUiText("查询结果尚未就绪，请稍候查看。")}</Notice>
      : props.node.action === "BILL_ANALYSIS" ? <BillCard {...props} />
      : props.node.action === "FRAUD_ANALYZE" ? <FraudCard {...props} />
      : props.node.action === "FAMILY_VIEW" ? <FamilyCard {...props} />
      : props.node.action === "GOAL_VIEW" ? <GoalCard {...props} /> : <CarbonCard {...props} />}
  </section>;
}
