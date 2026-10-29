// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage, CustomerLanguageScope } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { dateText, Icon, StateBadge, StructuredData, stateLabels } from "./components";
import { receiptNextStep, receiptPresentation } from "./receiptPresentation";
import { TransferChannelInfo } from "./TransferRecipientPanel";
import { FxQuoteDetails } from "./FxPanels";
import type { TaskTrace, BillAnalysis } from "./types";
import { customerName, customerNarrative } from "./customerPresentation";
import { EverydayQueryCards, supportsEverydayQuery } from "./EverydayQueryCards";
import { PersonalServiceCards, supportsPersonalService } from "./PersonalServiceCards";
import { CreditCards, supportsCreditCards } from "./CreditCards";
import { compactCreditQueryDetail, compactCreditQueryIntro } from "./creditQueryPresentation";
import { AAReadCards, AAPayReceipt, AARequestCards } from "./AAResultCards";
import { ScheduledTransferCards } from "./ScheduledTransferCards";
import { RiskAssessmentEntry } from "./RiskAssessmentEntry";
import { aaRequestPresentation } from "./aaPresentation";
import { OwnAccountTransferReceipt } from "./OwnAccountTransferCards";
import { LifePlanCards, FamilyOccasionCards, FamilyOccasionSaveReceipt, type LifePlanResult } from "./LifePlanCards";
import { FamilyProfileCards } from "./FamilyProfileCards";
import { MerchantCards } from "./MerchantCards";
import { cardQueryTitleForNodes, cardQueryNarration } from "./cardQueryPresentation";
import { recordedCompactTransfer } from "./transferResultPresentation";
import { wealthSharedNoticeKey } from "./wealthQueryPresentation";
import { InsightQueryCards, supportsInsightQuery } from "./InsightQueryCards";
import { BusinessResultCards, supportsBusinessResult } from "./BusinessResultCards";
import { isQueryOnlyTask } from "./taskReply";
import { CustomerMotionBoundary } from "./CustomerMotionBoundary";
import type { CustomerMotionEvent } from "./customerMotion";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import { billAnalysisForDisplay } from "./billCoveragePresentation";
import { customerResultNarration } from "./customerResultNarration";
import { reviewedMaterialReply } from "./reviewedMaterialReply";
import { customerReadActionLabel } from "./customerTaskPresentation";
import { completedReadsForPending, completedBillResults, isReadAction } from "./completedReadResults";
import { expiredConfirmationOutcomeOwned, currentConfirmationOutcomeOwned, type ExpiredConfirmationOwnership, type CurrentConfirmationOwnership } from "./confirmationPresentation";
import "./customerReply.css";
import { ReplyText } from "./ReplyText";

// Result labels only. Do not register these WRITE names in the shared action
// directory: doing so would change confirmation-owned filler eligibility.
const writeResultLabels: Record<string,readonly [string,string]>={
  TRANSFER:["转账","Transfer"],TRANSFER_SCHEDULE:["预约转账","Scheduled transfer"],
  OWN_ACCOUNT_TRANSFER:["本人账户划转","Transfer between own accounts"],
  CARD_LOCK:["临时锁卡","Temporarily lock card"],CARD_UNLOCK:["解锁卡片","Unlock card"],
  CARD_REPORT_LOSS:["卡片挂失","Report card lost"],
  AA_PAY:["支付AA份额","Pay AA share"],CREDIT_REPAY:["信用卡还款","Credit card repayment"],
  CREDIT_INSTALLMENT:["消费分期","Purchase installments"],CREDIT_SETTLE_INSTALLMENT:["提前结清分期","Early installment settlement"],
  CREDIT_APPLY:["信用卡申请","Credit card application"],CREDIT_LIMIT_REQUEST:["授信额度调整申请","Credit limit request"],
  CREDIT_ACTIVATE:["信用卡激活","Credit card activation"],OCCASION_SET:["保存家庭生日","Save family birthday"],
  OCCASION_MEMBER_SET:["保存家庭资料","Save family directory details"],
  LIFE_PLAN_AUTHORIZE:["授权本次生活计划","Authorize this life plan"],LIFE_PLAN_CANCEL:["撤销未执行生活计划","Cancel an unexecuted life plan"],
  MERCHANT_PURCHASE:["商品付款","Product payment"],
};

// Display only: the saved nodes, proofs and operation identities stay intact.
// Equal money is never identity. Only the same card ID and complete snapshot
// within this outcome can suppress a duplicated credit-card view.
export function cardReadDisplayNodes(nodes: TaskTrace["nodes"]): TaskTrace["nodes"] {
  const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
  const cards = (value: unknown) => Array.isArray(value)
    ? value.filter((item): item is Record<string, import("./types").JsonValue> => item !== null && typeof item === "object" && !Array.isArray(item)) : [];
  const identity = (card: Record<string, unknown>) => typeof card.card_id === "string" && card.card_id
    ? `${card.card_id}:${canonical(card)}` : null;
  const eligible = (node: TaskTrace["nodes"][number]) => node.status === "SUCCEEDED"
    && (node.action === "CARD_STATUS" || node.action === "CREDIT_VIEW");
  const standalone = new Set(nodes.filter(node => eligible(node) && node.action === "CREDIT_VIEW")
    .flatMap(node => cards(node.result?.cards).map(identity)).filter((key): key is string => key !== null));
  const seen = new Set<string>();
  const projected = nodes.flatMap(node => {
    if (!eligible(node) || !node.result) return [node];
    const field = node.action === "CARD_STATUS" ? "credit_cards" : "cards";
    const original = node.result[field];
    if (!Array.isArray(original)) return [node];
    const next = original.filter(card => {
      if (card === null || typeof card !== "object" || Array.isArray(card)) return true;
      const key = identity(card);
      if (!key) return true;
      if (node.action === "CARD_STATUS" && standalone.has(key) || seen.has(key)) return false;
      seen.add(key); return true;
    });
    if (next.length === original.length) return [node];
    if (node.action === "CREDIT_VIEW" && !next.length && !cards(node.result.applications).length) return [];
    return [{...node, result: {...node.result, [field]: next}}];
  });
  // Reorder only a run of successful card reads, never across a write or any
  // unrelated result. Debit cards precede the independent credit-card view.
  for (let index = 0; index < projected.length;) {
    if (!eligible(projected[index])) { index++; continue; }
    let end = index + 1;
    while (end < projected.length && eligible(projected[end])) end++;
    projected.splice(index, end - index, ...projected.slice(index, end).sort((a, b) =>
      Number(a.action === "CREDIT_VIEW") - Number(b.action === "CREDIT_VIEW")));
    index = end;
  }
  return projected;
}

export function TaskOutcomeCard(props: { trace: TaskTrace; bankToken?: string; onPrompt?: (message: string) => void; motion?: "none" | "settle"; motionEvent?: CustomerMotionEvent | null; timestampInParent?: boolean; clarificationInParent?: boolean; confirmationInParent?: boolean; expiredConfirmationInParent?: ExpiredConfirmationOwnership | null; currentConfirmationInParent?: CurrentConfirmationOwnership | null; historicalReadOperationIds?: string[] }) {
  const pendingNarration = ["QUEUED", "PROCESSING", "STALE"].includes(props.trace.customer_reply_status || "");
  return <CustomerLanguageScope locale={(!pendingNarration && props.trace.assistant_response?.reply_language) || props.trace.reply_language}><TaskOutcomeContent {...props} /></CustomerLanguageScope>;
}
function TaskOutcomeContent({ trace, bankToken, onPrompt, motion = "none", motionEvent = null, timestampInParent = false, clarificationInParent = false, confirmationInParent = false, expiredConfirmationInParent = null, currentConfirmationInParent = null, historicalReadOperationIds = [] }: { trace: TaskTrace; bankToken?: string; onPrompt?: (message: string) => void; motion?: "none" | "settle"; motionEvent?: CustomerMotionEvent | null; timestampInParent?: boolean; clarificationInParent?: boolean; confirmationInParent?: boolean; expiredConfirmationInParent?: ExpiredConfirmationOwnership | null; currentConfirmationInParent?: CurrentConfirmationOwnership | null; historicalReadOperationIds?: string[] }) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const resultNodeLabel=(node:TaskTrace["nodes"][number])=>{
    const chosen=locale==="en"?node.display_label||node.label:node.label;
    return customerName(chosen===node.action
      ? writeResultLabels[node.action]?.[locale==="en"?1:0]||customerReadActionLabel(node.action,locale)||(locale==="en"?customerUiText(node.label):node.label)
      : locale==="en"?node.display_label||customerUiText(node.label):node.label);
  };
  const presentation = receiptPresentation(trace);
  const riskNode=trace.state==='FAILED' && trace.nodes.length===1 ? trace.nodes[0] : null;
  const riskCode=riskNode?.result?.error;
  if(riskNode?.status==='FAILED' && ['TRANSFER','TRANSFER_SCHEDULE','FX_TRANSFER'].includes(riskNode.action)
      && ['TRANSFER_HIGH_VALUE_FREQUENCY_BLOCKED','TRANSFER_RECIPIENT_RECHECK_REQUIRED'].includes(String(riskCode))) {
    const recipient=riskCode==='TRANSFER_RECIPIENT_RECHECK_REQUIRED';
    return <section className="receipt-card warning" role="alert" aria-label={recipient?'历史转账未执行':'异常转账已拦截'}>
      <h2>{recipient?'这笔转账当时未执行':'异常转账已拦截'}</h2>
      <p>{recipient?'当时使用的核实规则已停用。这条记录保留原办理结果。':'今日已向同一收款账户完成两笔大额转账，本次转账触发异常交易风控，暂不执行。'}</p>
      <p>{recipient?'如仍需转账，请重新发起并核对付款信息。':'本次没有扣款，请通过银行服务渠道核实。'}</p>
    </section>;
  }
  const narration = customerResultNarration(trace, presentation);
  const materialReply = reviewedMaterialReply(trace);
  const hasReceiptData = trace.receipt && Object.keys(trace.receipt).length > 0;
  if (clarificationInParent && trace.state === "NEED_CLARIFICATION" && !presentation.recordedNodes.length && !hasReceiptData) return null;
  if (trace.state === "NEED_CLARIFICATION" && !presentation.recordedNodes.length && !hasReceiptData) return <p className="query-reply-detail"><ReplyText slot={`task:${trace.task_id}:clarification`} text={customerNarrative(trace.assistant_response?.detail || trace.assistant_response?.intro || trace.status_message || customerUiText(presentation.description))}/></p>;
  const completedReads = [...completedReadsForPending(trace), ...completedBillResults(trace)];
  const multiBills = trace.nodes.filter(node => node.action === "BILL_ANALYSIS").length > 1;
  const pendingMixed = trace.state === "AWAITING_CONFIRMATION" && trace.nodes.some(node => isReadAction(node.action))
    && trace.nodes.some(node => !isReadAction(node.action));
  const hasIndependentReadResults = completedReads.length > 0;
  const candidateResults = pendingMixed
    ? [...presentation.recordedNodes.filter(node => !isReadAction(node.action)), ...completedReads.map(item => item.node)]
    : presentation.kind === "UNVERIFIED" ? [] : isQueryOnlyTask(trace) && presentation.kind === "QUERY_RESULT"
    ? trace.nodes.filter(node => node.status === "SUCCEEDED") : presentation.recordedNodes;
  // Only exact READ operation identities already displayed at their original
  // turn may be omitted here. A new query, write receipt, warning or pending
  // confirmation never matches this display-only suppression.
  const customerResults = cardReadDisplayNodes(candidateResults.filter(node =>
    (!multiBills || node.action !== "BILL_ANALYSIS" || completedReads.some(item => item.node.node_id === node.node_id))
    && (!isReadAction(node.action) || typeof node.operation_id!=="string" || !historicalReadOperationIds.includes(node.operation_id))));
  const completedReadsDisplayedHere = completedReads.filter(item=>customerResults.some(node=>node.node_id===item.node.node_id));
  const unfinished = trace.nodes.filter((node) => !["SUCCEEDED", "SKIPPED", "CANCELLED", "CANCELED"].includes(node.status));
  const query = isQueryOnlyTask(trace) || presentation.kind === "QUERY_RESULT";
  const response = !(pendingMixed && !hasIndependentReadResults) && narration.response && (narration.response.state_kind === presentation.kind ||
    narration.response.state_kind === "GUIDANCE" && presentation.kind === "QUERY_RESULT") ? narration.response : null;
  // The actual confirmation already owns the review/verification instructions.
  // Never remove a recorded result, an unknown warning, or non-generic detail.
  const genericPendingDetails = new Set([
    "下方这一步尚未执行，是否办理仍由你决定；聊天中的确认不会代替确认流程。",
    "This step has not been executed. A chat reply such as 'yes' does not replace the bank confirmation process.",
  ]);
  const genericPendingIntros = new Set([
    "办理内容已整理好，请先核对下面的确认卡。",
    "好的，办理内容已整理好，请先核对下面的确认卡。",
    "Please review the confirmation card before proceeding.",
  ]);
  // Some server replies repeat the same generic instruction on separate
  // lines. Normalize only that exact known copy; any extra risk/detail line
  // keeps the outcome visible under the original strict parent guard.
  const pendingDetailLines = [...new Set((response?.detail || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean))];
  const genericPendingDetail = pendingDetailLines.every(line => genericPendingDetails.has(line));
  if (confirmationInParent && currentConfirmationOutcomeOwned(trace,currentConfirmationInParent)
    && presentation.kind === "PENDING" && !pendingMixed && !customerResults.length && !hasReceiptData
    && (!response?.intro || genericPendingIntros.has(response.intro))
    && genericPendingDetail) return null;
  if (confirmationInParent && expiredConfirmationOutcomeOwned(trace, expiredConfirmationInParent)
    && presentation.kind === "PENDING" && !customerResults.length && !hasReceiptData
    && response?.state_kind === "PENDING" && response.read_only === false && response.detail_kind === trace.nodes[0].action
    && response.intro === "好的，办理内容已整理好，请先核对下面的确认卡。" && genericPendingDetail) return null;
  const sourceTurn = (trace as TaskTrace & {source_turn_id?:unknown}).source_turn_id;
  const titleId = `outcome-heading-${trace.task_id}${typeof sourceTurn==="string"?`-${encodeURIComponent(sourceTurn)}`:""}`;
  const nextStep = receiptNextStep(trace, presentation);
  const showNextStep = (!query || !["QUERY_RESULT", "COMMITTED"].includes(presentation.kind))
    && nextStep !== "可以继续咨询，或提出新的需求。";
  const urgent = ["UNVERIFIED", "FAILED", "PARTIAL", "UNKNOWN", "CLOSED", "PENDING"].includes(presentation.kind)
    || Boolean(trace.pending_confirmation) || trace.state === "AWAITING_CONFIRMATION"
    || customerResults.some(node => node.action === "FRAUD_ANALYZE");
  const immediateNarration = ["UNVERIFIED", "PARTIAL", "UNKNOWN"].includes(presentation.kind)
    || customerResults.some(node => node.action === "FRAUD_ANALYZE");
  const replyText = (slot: string, text: string) => <ReplyText slot={`task:${trace.task_id}:${trace.state}:${slot}`}
    text={text} immediate={immediateNarration}/>;
  const matchingMotion = motionEvent?.taskId === trace.task_id && motionEvent.version === trace.version ? motionEvent : null;
  const singleCardRead = presentation.kind === "QUERY_RESULT" && customerResults.length === 1
    && customerResults[0].action === "CARD_STATUS" && customerResults[0].status === "SUCCEEDED";
  const onlyCardReads = presentation.kind === "QUERY_RESULT" && trace.state === "SUCCEEDED"
    && customerResults.length > 0 && trace.nodes.every(node => ["CARD_STATUS", "CREDIT_VIEW"].includes(node.action) && node.status === "SUCCEEDED");
  // The completed save card already owns this event's concise result. Remove
  // only exact redundant copy; mixed results and additional warnings remain.
  const singleBirthdaySave = trace.state === "SUCCEEDED" && trace.outcome?.verified === true
    && presentation.kind === "COMMITTED" && !trace.pending_confirmation && customerResults.length === 1
    && customerResults[0].action === "OCCASION_SET" && customerResults[0].status === "SUCCEEDED"
    && customerResults[0].result?.schema_version === "family-occasion-v1"
    && customerResults[0].result?.occasion_state_changed === true && customerResults[0].result?.bank_state_changed === false;
  const singleAACollection = customerResults.length === 1 && aaRequestPresentation(trace,customerResults[0]) !== null;
  const aaCollectionCopy = new Set([
    "AA收款请求已创建。", "仍需参与者自行付款，发起请求不等于已收到全部资金。",
    "收款请求已创建，尚不代表付款或到账。", "AA collection request created.",
    "Participants still need to pay; creating a request does not mean all funds have been received.",
  ]);
  const birthdaySaveCopy = new Set([
    "生日资料已按本次确认保存。", "好的，生日资料已按本次确认保存。",
    "这不会给家庭成员开通账户、授信或购物权限。",
    "The confirmed birthday record was saved.", "This grants the family member no account, credit or payment permission.",
  ]);
  const conciseBirthdayNarration = (value: string | null | undefined) => !value ? value
    : value.split(/\r?\n/).filter(line => !(singleBirthdaySave && birthdaySaveCopy.has(line.trim()))
      && !(singleAACollection && aaCollectionCopy.has(line.trim()))).join("\n");
  // A compact narration is optional and belongs only to a verified, single
  // subscription result whose records will actually render below. The full
  // narrative remains the fallback for old servers, partial or mixed results.
  const subscriptionRows = customerResults.length === 1 && customerResults[0].action === "SUBSCRIPTION_LIST"
    ? customerResults[0].result?.subscriptions : null;
  const subscriptionCardsAvailable = Array.isArray(subscriptionRows)
    && subscriptionRows.every(item => item !== null && typeof item === "object" && !Array.isArray(item));
  const singleSubscriptionRead = trace.state === "SUCCEEDED" && trace.outcome?.verified === true && !trace.pending_confirmation
    && presentation.kind === "QUERY_RESULT" && subscriptionCardsAvailable
    && (narration.progressive || response?.state_kind === "QUERY_RESULT" && response.detail_kind === "SUBSCRIPTIONS" && response.read_only === true
    && typeof response.card_detail === "string" && Boolean(response.card_detail.trim()));
  const semanticCompact=query && presentation.kind==="QUERY_RESULT" && trace.state==="SUCCEEDED" && trace.outcome?.verified===true
    && !trace.pending_confirmation && response?.read_only===true && response.state_kind==="QUERY_RESULT"
    && response.query_layout_version==="customer-query-layout-v1" && typeof response.card_intro==="string" && Boolean(response.card_intro.trim())
    && customerResults.length>0 && customerResults.every(node=>["BILL_ANALYSIS","CARBON_VIEW","CARD_STATUS","SUBSCRIPTION_LIST","WEALTH_COMPARE","FRAUD_ANALYZE","FAMILY_VIEW","ASSET_POSITIONS_READ","MEMBERSHIP_VIEW","BENEFITS_VIEW","PREFERENCE_VIEW"].includes(node.action) && node.status==="SUCCEEDED");
  const quietQueryChrome = presentation.kind === "QUERY_RESULT" && trace.state === "SUCCEEDED"
    && trace.outcome?.verified === true && !trace.pending_confirmation;
  const repeatedWealthNoticeNodes = new Set<string>();
  if (quietQueryChrome && customerResults.length > 1 && customerResults.every(node => node.action === "WEALTH_COMPARE" && node.status === "SUCCEEDED")) {
    const shownNotices = new Set<string>();
    for (const node of customerResults) {
      const key = wealthSharedNoticeKey(node, locale);
      if (key && shownNotices.has(key)) repeatedWealthNoticeNodes.add(node.node_id);
      if (key) shownNotices.add(key);
    }
  }
  const queryBoilerplate = new Set(["查询已完成，未办理付款等操作。", "查询结果已整理好。",
    "Query complete. No payments or other banking actions were performed.", "Query results are ready."]);
  const conciseQueryIntro = (value: string | null | undefined) => quietQueryChrome && value && queryBoilerplate.has(value.trim()) ? null : value;
  const rawIntro=conciseBirthdayNarration(onlyCardReads
    ? cardQueryNarration(semanticCompact ? response.card_intro : response?.intro, customerResults, locale)
    : semanticCompact ? response.card_intro : response?.intro);
  const creditCopyScope = { state: trace.state, pendingConfirmation: Boolean(trace.pending_confirmation),
    readOnly: response?.read_only, detailKind: response?.detail_kind, nodes: customerResults };
  const compactIntro = compactCreditQueryIntro(rawIntro, creditCopyScope);
  const displayIntro=conciseQueryIntro(compactIntro == null ? compactIntro : customerNarrative(compactIntro));
  const introductoryText=response ? displayIntro : conciseQueryIntro(customerUiText(presentation.description));
  const cardFocus=(nodeId:string)=>{
    const value=semanticCompact ? response?.query_focus_by_node?.[nodeId] || (customerResults.length===1 ? response?.query_focus : null) : null;
    return value==="CARD_LIMIT" || value==="CARD_BALANCE_LIMIT" ? value : null;
  };
  // Progressive delivery can select the subscription layout before optional
  // compact copy exists. It must not hide a verified answer to the user's
  // question merely because the cards have already rendered.
  const hasSubscriptionCompactDetail = singleSubscriptionRead && typeof response?.card_detail === "string"
    && Boolean(response.card_detail.trim());
  const detailText = semanticCompact || hasSubscriptionCompactDetail ? response?.card_detail : response?.detail;
  const rawDetail = conciseBirthdayNarration(onlyCardReads ? cardQueryNarration(detailText, customerResults, locale) : detailText);
  const compactDetail = compactCreditQueryDetail(rawDetail, creditCopyScope);
  const displayDetail = compactDetail == null ? compactDetail : customerNarrative(compactDetail);
  const supplementalAllowed=trace.state==="SUCCEEDED" && trace.outcome?.verified===true && presentation.kind==="QUERY_RESULT" && !trace.pending_confirmation;
  const billPrimary=trace.analysis || customerResults.find(node=>node.action==="BILL_ANALYSIS")?.result?.analysis;
  const usingSupplementary=Boolean(supplementalAllowed && billPrimary && typeof billPrimary==="object" && !Array.isArray(billPrimary) && trace.supplementary_bill
    && billAnalysisForDisplay(billPrimary as BillAnalysis,trace.supplementary_bill)===trace.supplementary_bill);
  const billNarrationInParent=presentation.kind==="QUERY_RESULT" && customerResults.length===1 && customerResults[0].action==="BILL_ANALYSIS"
    && response?.state_kind==="QUERY_RESULT" && Boolean(response.intro || displayDetail);
  const insightResult=(node:TaskTrace["nodes"][number])=><section className="query-result-section" data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`} key={node.node_id}>
    <InsightQueryCards node={node} analysis={completedReads.find(item => item.node.node_id === node.node_id)?.analysis || (!multiBills ? trace.analysis : null)} pureRiskQuery={query && trace.nodes.length===1 && node.action==="FRAUD_ANALYZE"}
      queryFocus={semanticCompact && ["CARBON_VIEW","FAMILY_VIEW"].includes(node.action) ? response?.query_focus_by_node?.[node.node_id] || (customerResults.length===1 ? response?.query_focus : null) : null}
      supplementaryAnalysis={node.action==="BILL_ANALYSIS" && supplementalAllowed && !multiBills ? trace.supplementary_bill : null}
      observedAt={completedReads.find(item => item.node.node_id === node.node_id)?.observedAt || (usingSupplementary ? trace.supplementary_bill_observed_at : trace.result_observed_at)}
      narrationInParent={node.action==="BILL_ANALYSIS" && billNarrationInParent} onPrompt={onPrompt}/></section>;
  if (
    !hasReceiptData &&
    !trace.status_message &&
    !presentation.recordedNodes.length && !trace.assistant_response
  )
    return null;
  const compactTransfer = trace.state === "SUCCEEDED" && trace.outcome?.verified === true
    && presentation.kind === "COMMITTED" && !trace.pending_confirmation
    && trace.nodes.length === 1 && customerResults.length === 1 && recordedCompactTransfer(customerResults[0]);
  if (compactTransfer) return <section className="receipt-card receipt-success receipt-transfer-compact"
    data-outcome-kind={presentation.kind} data-outcome-tone={presentation.tone}>
    <CustomerMotionBoundary event={matchingMotion} interactive>
      <BusinessResultCards node={customerResults[0]}/>
    </CustomerMotionBoundary>
  </section>;
  const showOutcomeHeading = !quietQueryChrome || onlyCardReads || singleSubscriptionRead;
  return (
    <section
      className={`receipt-card receipt-${presentation.tone} ${query || hasIndependentReadResults ? "customer-query-reply" : ""}`}
      data-outcome-kind={presentation.kind}
      data-outcome-tone={presentation.tone}
      aria-labelledby={showOutcomeHeading ? titleId : undefined}
      aria-label={showOutcomeHeading ? undefined : locale === "en" ? "Query results" : "查询结果"}
    >
      {showOutcomeHeading && <CustomerMotionBoundary event={matchingMotion} urgent={urgent} interactive={false} className="customer-motion-heading">
      <h2 id={titleId} className={`receipt-title${query ? " query-reply-title" : ""}`}>
        <Icon name={presentation.icon} size={18} />
        <strong>{completedReadsDisplayedHere.length > 0 ? locale === "en" ? "Available query results" : "已查到的结果"
          : onlyCardReads ? cardQueryTitleForNodes(customerResults, locale)
          : singleSubscriptionRead ? customerUiText("订阅与自动续费") : customerUiText(presentation.title)}</strong>
      </h2>
      </CustomerMotionBoundary>}
      {materialReply&&<p className="query-reply-intro customer-material-answer" data-material-answer="fresh-read-only">{replyText("material", materialReply)}</p>}
      {/* Only prose uses visual reveal. Registered facts, risk warnings and
          every confirmation control remain independently and immediately visible. */}
      {!narration.progressive && !materialReply && introductoryText && (!(singleBirthdaySave || singleAACollection) || displayIntro) && <p className={query ? "query-reply-intro" : "receipt-description"} data-reply-motion={motion}>{replyText("intro", customerNarrative(introductoryText))}</p>}
      {!narration.progressive && !materialReply && displayDetail && <p className="query-reply-detail">{replyText("detail", customerNarrative(displayDetail))}</p>}
      {query && presentation.kind === "QUERY_RESULT" && !timestampInParent && !usingSupplementary && <p className="query-reply-asof">
        {typeof trace.result_observed_at === "string" && Number.isFinite(Date.parse(trace.result_observed_at))
          ? (locale === "en" ? `Retrieved at ${dateText(trace.result_observed_at, true)}` : `查询于 ${dateText(trace.result_observed_at, true)}`)
            + (Date.now() - Date.parse(trace.result_observed_at) > 86_400_000 ? locale === "en" ? "; historical result, not current account status." : " · 历史查询，不代表当前账户状态。" : "")
          : locale === "en" ? "Retrieval time was not recorded; not current account data." : "查询时间未记录，不代表当前账户状态。"}
      </p>}
      {!customerResults.length && hasReceiptData && presentation.kind !== "UNVERIFIED" ? (
        <CustomerResultDisclosure scopeKey={`${trace.task_id}:${trace.version}:${JSON.stringify(trace.receipt)}`}>
          <StructuredData data={trace.receipt} />
        </CustomerResultDisclosure>
      ) : null}
      {customerResults.length > 0 && ["FAILED", "PARTIAL", "UNKNOWN", "CLOSED"].includes(presentation.kind) && <strong><CustomerText text="已完成的事项" /></strong>}
      {bankToken && customerResults.some(node => node.action === "WEALTH_COMPARE") && <RiskAssessmentEntry key={bankToken} token={bankToken}/>}
      {customerResults.map((node, index) => <CustomerMotionBoundary key={node.node_id} event={matchingMotion}
        layer={index + 1} interactive urgent={urgent}>{node.action==="TRANSFER_SCHEDULE_VIEW" ? (
        <ScheduledTransferCards node={node}/>
      ) : node.action==="AA_VIEW" ? (
        <section className="query-result-section" data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`}><AAReadCards node={node} heading={resultNodeLabel(node)}/></section>
      ) : node.action === "AA_PAY" ? (
        <AAPayReceipt trace={trace} node={node} heading={resultNodeLabel(node)}/>
      ) : node.action === "AA_REQUEST" ? (
        <AARequestCards trace={trace} node={node}/>
      ) : node.action==="OWN_ACCOUNT_TRANSFER" ? (
        <OwnAccountTransferReceipt result={node.result}/>
      ) : (node.action.startsWith("CREDIT_") && supportsCreditCards(node.action) || node.result?.schema_version === "credit-card-control-v1") ? (
        <section className="query-result-section" data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`}><CreditCards node={node} onPrompt={onPrompt}/></section>
      ) : node.action.startsWith("LIFE_PLAN_") ? (
        <LifePlanCards result={node.result as unknown as LifePlanResult}/>
      ) : node.action === "OCCASION_MEMBER_VIEW" || node.action === "OCCASION_MEMBER_SET" ? (
        <FamilyProfileCards result={node.result}/>
      ) : node.action === "OCCASION_VIEW" ? (
        <FamilyOccasionCards result={node.result as unknown as Parameters<typeof FamilyOccasionCards>[0]["result"]}/>
      ) : node.action === "OCCASION_SET" ? (
        <FamilyOccasionSaveReceipt result={node.result}/>
      ) : node.action.startsWith("MERCHANT_") ? (
        <MerchantCards result={node.result as unknown as Parameters<typeof MerchantCards>[0]["result"]}/>
      ) : supportsPersonalService(node.action) ? (
        <section className="query-result-section" data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`}><PersonalServiceCards node={node} onPrompt={onPrompt}/></section>
      ) : supportsEverydayQuery(node.action) ? (
        <section className="query-result-section" data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`} key={node.node_id}><EverydayQueryCards node={node} onPrompt={onPrompt}
          headingInParent={onlyCardReads || singleCardRead || singleSubscriptionRead || repeatedWealthNoticeNodes.has(node.node_id)}
          suppressSharedWealthNotice={repeatedWealthNoticeNodes.has(node.node_id)} queryFocus={node.action==="CARD_STATUS" ? cardFocus(node.node_id) : null}/>
          {completedReads.find(item => item.node.node_id === node.node_id) && <p className="query-reply-asof">{locale === "en" ? "Retrieved at " : "查询于 "}{dateText(completedReads.find(item => item.node.node_id === node.node_id)!.observedAt, true)}</p>}
        </section>
      ) : supportsInsightQuery(node.action) ? (
        materialReply && node.action==="BILL_ANALYSIS" ? <CustomerResultDisclosure scopeKey={`material-bank:${trace.task_id}:${trace.version}:${node.node_id}`}
          label={locale==="en" ? "View bank statement" : "查看银行账单"} className="material-bank-details">{insightResult(node)}</CustomerResultDisclosure>
          : insightResult(node)
      ) : supportsBusinessResult(node.action) ? (
        <div data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`}><BusinessResultCards node={node} key={node.node_id}/></div>
      ) : (
        <details className="business-receipt" data-reading-key={`${trace.task_id}:${trace.version}:${node.node_id}`} key={`${node.node_id}:${trace.version}`}>
          <summary>
            <span>{resultNodeLabel(node)}</span>
            {["FAMILY_PROPOSE", "AA_REQUEST", "CONTACT_BIND"].includes(
              node.action,
            ) ? (
              <span className="receipt-record-status">
                {node.action === "FAMILY_PROPOSE"
                  ? customerUiText("提案已保存，未执行银行操作")
                  : node.action === "CONTACT_BIND"
                    ? customerUiText("个人身份已保存，未转账")
                    : customerUiText("收款请求已登记，未代表到账")}
              </span>
            ) : (
              <StateBadge state={node.status} label={stateLabels[node.status] || customerUiText("状态更新中")} />
            )}
          </summary>
          <StructuredData data={node.result} />
          <TransferChannelInfo scope={node.result} />
          <FxQuoteDetails data={node.result} />
        </details>
      )}</CustomerMotionBoundary>)}
      {narration.progressive && !materialReply && (!(singleBirthdaySave || singleAACollection) || displayIntro || displayDetail) && <div className="customer-result-narration" aria-live="off" data-narration-status={narration.waiting ? "pending" : narration.pending ? "unavailable" : "ready"}>
        {narration.waiting ? <p className="customer-result-preparing" role="status"><span className="spinner" aria-hidden="true" />{locale === "en" ? "Your results are ready. I’m finishing the explanation…" : "结果已查到，正在整理说明…"}</p>
          : <>{displayIntro && displayIntro !== customerUiText(presentation.description) && <p className="query-reply-intro">{replyText("intro", customerNarrative(displayIntro))}</p>}
            {displayDetail && <p className="query-reply-detail">{replyText("detail", customerNarrative(displayDetail))}</p>}</>}
      </div>}
      {["FAILED", "PARTIAL", "UNKNOWN"].includes(presentation.kind) && unfinished.length > 0 && (
        <div className="outcome-unfinished">
          <strong><CustomerText text="尚未完成" /></strong>
          {unfinished.map((node) => (
            <p key={node.node_id}>
              {resultNodeLabel(node)} · {customerUiText(stateLabels[node.status] || "状态更新中")}
              {node.wait_reason ? `：${customerNarrative(locale === "en" ? node.display_wait_reason || customerUiText(node.wait_reason) : node.wait_reason)}` : ""}
            </p>
          ))}
        </div>
      )}
      {showNextStep ? <p className="outcome-next-step">{customerUiText(nextStep)}</p> : null}
    </section>
  );
}
