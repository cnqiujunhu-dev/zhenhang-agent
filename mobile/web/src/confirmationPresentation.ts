import type { JsonValue, TaskTrace } from "./types";
import { queryKinds } from "./taskReply.ts";
import { customerActionLabel } from "./customerTaskPresentation.ts";
import { translateCustomerText } from "./customerLanguage.ts";
import { customerName } from "./customerPresentation.ts";
import { subscriptionRestorationPreview } from "./subscriptionCommercePresentation.ts";
import { validSimulationCardNumber } from "./cardIdentity.ts";
import { exactMajorAmount } from "./typedMoney.ts";

const object = (value: JsonValue | undefined): Record<string, JsonValue> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;
const canonical = (value: JsonValue): string => JSON.stringify(value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])) : value);
const creditConfirmationTitles: Record<string, readonly [string,string]> = {
  AA_PAY: ["支付AA份额", "Pay AA share"],
  CREDIT_REPAY: ["核对信用卡还款", "Review credit card repayment"],
  CREDIT_INSTALLMENT: ["核对消费分期", "Review purchase installments"],
  CREDIT_SETTLE_INSTALLMENT: ["核对提前结清", "Review early installment settlement"],
  CREDIT_APPLY: ["核对信用卡申请", "Review credit card application"],
  CREDIT_LIMIT_REQUEST: ["核对授信额度调整申请", "Review credit limit request"],
  CREDIT_ACTIVATE: ["核对信用卡激活", "Review credit card activation"],
};

/** Localize only a registered action name, preserving descriptive bank copy. */
export function confirmationTitle(label: string | undefined, displayLabel: string | undefined, locale: string, action?: string, scope?: JsonValue): string {
  const data = object(scope);
  if(action === "OWN_ACCOUNT_TRANSFER" || !action && (label === "OWN_ACCOUNT_TRANSFER" || displayLabel === "OWN_ACCOUNT_TRANSFER")) return locale === "en" ? "Transfer between my accounts" : "本人账户转存";
  if(data && subscriptionRestorationPreview(action || "",data)) return locale === "en" ? "Restore automatic renewal" : "恢复自动续费";
  const customerLocale = locale === "en" ? "en" : "zh-CN";
  const selected = locale === "en" ? displayLabel || label : label;
  const creditTitle = creditConfirmationTitles[action || selected || ""];
  if (creditTitle && (!selected || selected === action || !action && selected in creditConfirmationTitles))
    return creditTitle[locale === "en" ? 1 : 0];
  if (!selected) return translateCustomerText("请核对本次操作", customerLocale);
  const actionLabel = customerActionLabel(selected);
  return translateCustomerText(actionLabel === "办理事项" ? selected : actionLabel, customerLocale);
}

export interface ExpiredConfirmationOwnership {
  taskId: string; taskVersion: number; parentNodeId: string; childTaskId: string;
  confirmationId: string; planHash: string; nodeKey: string; expectedVersion: number;
  expiresAt: string; state: "EXPIRED"; recoveryAvailable: true;
}
export interface CurrentConfirmationOwnership {
  taskId: string; taskVersion: number; parentNodeId: string; childTaskId: string;
  confirmationId: string; planHash: string; nodeKey: string; expectedVersion: number;
  expiresAt: string; state: "CURRENT"; cardVisible: true;
  /** Actual native review passed App's existing typed readiness checks. */
  nativeReview?: {schema_version:"customer-native-confirmation-display-v1";action:string;scopeJson:string};
}
const nativeConfirmationSchemas:Record<string,string>={
  MERCHANT_PURCHASE:"customer-life-confirmation-v1",OCCASION_MEMBER_SET:"customer-life-confirmation-v1",
  OCCASION_SET:"customer-life-confirmation-v1",LIFE_PLAN_AUTHORIZE:"customer-life-confirmation-v1",LIFE_PLAN_CANCEL:"customer-life-confirmation-v1",
  TRANSFER_SCHEDULE:"customer-banking-confirmation-v1",AA_REQUEST:"customer-banking-confirmation-v1",
};
const plainObject = (value: unknown): value is Record<string, unknown> => value !== null
  && typeof value === "object" && !Array.isArray(value)
  && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
const emptyOptionalObject = (value: unknown) => value === undefined || value === null
  || plainObject(value) && Object.keys(value).length === 0;

/** Display only: the actually rendered expired card must own this exact
 * confirmation and its enabled edit recovery. It never grants bank authority.
 * A mixed/read/effect/warning-bearing result is not a generic duplicate. */
export function expiredConfirmationOutcomeOwned(trace: TaskTrace, owner?: ExpiredConfirmationOwnership | null): boolean {
  const pending = trace.pending_confirmation;
  if (!plainObject(owner) || Object.keys(owner).length !== 11 || owner.state !== "EXPIRED" || owner.recoveryAvailable !== true
    || trace.state !== "AWAITING_CONFIRMATION" || !pending || owner.taskId !== trace.task_id || owner.taskVersion !== trace.version
    || !Number.isSafeInteger(trace.version) || trace.version < 1 || owner.childTaskId !== pending.task_id
    || owner.confirmationId !== pending.confirmation_id || owner.planHash !== pending.plan_hash || !/^[0-9a-f]{64}$/.test(owner.planHash)
    || owner.nodeKey !== pending.node_key || owner.expectedVersion !== pending.expected_task_version
    || !Number.isSafeInteger(owner.expectedVersion) || owner.expectedVersion < 1 || owner.expiresAt !== pending.expires_at
    || typeof owner.expiresAt !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(owner.expiresAt) || !Number.isFinite(Date.parse(owner.expiresAt))
    || [owner.taskId, owner.parentNodeId, owner.childTaskId, owner.confirmationId, owner.nodeKey].some(value => typeof value !== "string" || !value)
    || !emptyOptionalObject(trace.receipt) || !emptyOptionalObject(trace.analysis) || trace.compensation_source
    || !plainObject(trace.outcome) || trace.outcome.state !== trace.state || trace.outcome.kind !== "PROCESS" || trace.outcome.verified !== false
    || !Array.isArray(trace.outcome.completed_operation_ids) || trace.outcome.completed_operation_ids.length !== 0
    || !Array.isArray(trace.nodes) || trace.nodes.length !== 1) return false;
  const node = trace.nodes[0];
  if (!plainObject(node)) return false;
  if (node.node_id !== owner.parentNodeId || node.child_task_id !== owner.childTaskId || node.status !== "WAITING_CONFIRMATION"
    || !node.operation_id || node.error || node.wait_reason || node.can_compensate || node.compensation_task_id
    || node.action !== "TRANSFER") return false;
  const completedReads = (trace as TaskTrace & { completed_read_results?: unknown }).completed_read_results;
  if (completedReads !== undefined && completedReads !== null && (!Array.isArray(completedReads) || completedReads.length !== 0)) return false;
  if (node.result !== undefined && node.result !== null && !plainObject(node.result)) return false;
  if (node.result?.error) return false;
  const previewOnlyFields = new Set(["message", "transfer_channel", "recipient_username", "recipient_account_id", "simulated", "recipient_context", "fraud_risk"]);
  if (node.result && Object.keys(node.result).some(key => !previewOnlyFields.has(key))) return false;
  const risk = node.result?.fraud_risk;
  return risk === undefined || risk === null || plainObject(risk) && risk.risk_level === "LOW" && risk.decision === "ALLOW"
    && risk.confirmation_can_override === false && Array.isArray(risk.reason_codes)
    && risk.reason_codes.every(code => code === "KNOWN_SYNTHETIC_CONTACT_NO_RISK_FLAG");
}

/** A fresh actually rendered card owns the filler, not a bare boolean or an
 * expired/stale DTO. This projection can only remove pure waiting copy; it
 * never removes a separate read, recorded effect, uncertain result or risk. */
export function currentConfirmationOutcomeOwned(trace: TaskTrace, owner?: CurrentConfirmationOwnership | null): boolean {
  const pending=trace.pending_confirmation;
  if(!plainObject(owner)||!(Object.keys(owner).length===11&&!Object.hasOwn(owner,"nativeReview")
    ||Object.keys(owner).length===12&&Object.hasOwn(owner,"nativeReview"))||owner.state!=="CURRENT"||owner.cardVisible!==true
    ||trace.state!=="AWAITING_CONFIRMATION"||!pending||owner.taskId!==trace.task_id||owner.taskVersion!==trace.version
    ||!Number.isSafeInteger(trace.version)||trace.version<1||owner.childTaskId!==pending.task_id
    ||owner.confirmationId!==pending.confirmation_id||owner.planHash!==pending.plan_hash||!/^[0-9a-f]{64}$/.test(owner.planHash)
    ||owner.nodeKey!==pending.node_key||owner.expectedVersion!==pending.expected_task_version
    ||!Number.isSafeInteger(owner.expectedVersion)||owner.expectedVersion<1||owner.expiresAt!==pending.expires_at
    ||typeof owner.expiresAt!=="string"||!/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(owner.expiresAt)
    ||!Number.isFinite(Date.parse(owner.expiresAt))||Date.parse(owner.expiresAt)<=Date.now()
    ||[owner.taskId,owner.parentNodeId,owner.childTaskId,owner.confirmationId,owner.nodeKey].some(value=>typeof value!=="string"||!value)
    ||!emptyOptionalObject(trace.receipt)||!emptyOptionalObject(trace.analysis)||trace.compensation_source
    ||!plainObject(trace.outcome)||trace.outcome.state!==trace.state||trace.outcome.kind!=="PROCESS"||trace.outcome.verified!==false
    ||!Array.isArray(trace.outcome.completed_operation_ids)||trace.outcome.completed_operation_ids.length!==0
    ||!Array.isArray(trace.nodes)||trace.nodes.length!==1)return false;
  const node=trace.nodes[0];
  if(!plainObject(node))return false;
  const domain=object(object(pending.scope)?.domain_confirmation),review=owner.nativeReview;
  const native=Boolean(plainObject(review)&&Object.keys(review).length===3
    &&review.schema_version==="customer-native-confirmation-display-v1"&&review.action===node.action
    &&nativeConfirmationSchemas[node.action]&&domain?.schema_version===nativeConfirmationSchemas[node.action]
    &&domain.action===node.action&&domain.scope_verified===true&&typeof review.scopeJson==="string"
    &&review.scopeJson===JSON.stringify(domain));
  // Legacy generic cards keep their old tuple contract. Finite native cards
  // additionally require the exact scope reviewed by App after typed Ready;
  // a bare visible flag or a different serialized scope cannot cover them.
  if(nativeConfirmationSchemas[node.action]&&!native || review!==undefined&&!native)return false;
  if(native&&domain&&["warning","warnings","notice","notices","risk","fraud_risk","error"]
    .some(key=>Object.hasOwn(domain,key)))return false;
  if(!plainObject(node)||node.node_id!==owner.parentNodeId||node.child_task_id!==owner.childTaskId||node.status!=="WAITING_CONFIRMATION"
    ||!node.operation_id||node.error||node.wait_reason||node.can_compensate||node.compensation_task_id
    ||typeof node.action!=="string"||!native&&customerActionLabel(node.action)==="办理事项"
    ||queryKinds.has(node.action))return false;
  const reads=(trace as TaskTrace&{completed_read_results?:unknown}).completed_read_results;
  if(reads!==undefined&&reads!==null&&(!Array.isArray(reads)||reads.length!==0))return false;
  if(node.result!==undefined&&node.result!==null&&!plainObject(node.result))return false;
  const result=node.result;
  if(result&&(result.bank_state_changed===true||["warning","warnings","notice","notices","effect_receipt","settled_minor"]
    .some(key=>Object.hasOwn(result,key))))return false;
  // A registered START/RENEW preview records zero charged, not a payment
  // receipt. Its price must agree exactly; all source ownership checks above
  // still apply. Unknown result fields stay visible rather than being assumed
  // to be another harmless preview or an independent warning/effect.
  const subscriptionPreviewFields=new Set(["message","action","merchant","monthly_price_minor","total_amount_minor",
    "currency","months","auto_renew","effective_from","entitlement_end_at","expires_at","effective_policy",
    "auto_renew_setting_source","mandate_amount_minor","mandate_billing_months","charged_minor","risk","fraud_risk","effect_kind","subscription_id","version"]);
  const subscriptionZeroPreview=Boolean(result&&["SUBSCRIPTION_START","SUBSCRIPTION_RENEW"].includes(node.action)
    &&Object.keys(result).every(key=>subscriptionPreviewFields.has(key))
    &&result.action===(node.action==="SUBSCRIPTION_START"?"START":"RENEW")
    &&typeof result.charged_minor==="number"&&Number.isSafeInteger(result.charged_minor)
    &&result.charged_minor===0&&!Object.is(result.charged_minor,-0)
    &&result.currency==="CNY"&&typeof result.merchant==="string"&&Boolean(result.merchant.trim())
    &&typeof result.monthly_price_minor==="number"&&Number.isSafeInteger(result.monthly_price_minor)&&result.monthly_price_minor>0
    &&typeof result.months==="number"&&Number.isSafeInteger(result.months)&&result.months>=1&&result.months<=12
    &&typeof result.total_amount_minor==="number"&&Number.isSafeInteger(result.total_amount_minor)
    &&(result.total_amount_minor===result.monthly_price_minor*result.months || subscriptionRestorationPreview(node.action,result))&&typeof result.auto_renew==="boolean");
  const effects=["transfer_id","order_id","payment_id","transaction_id","receipt","committed_at","executed_at","redemption_id","delegation_id","proposal_id","charged_minor"];
  if(result&&(result.error||effects.some(key=>Object.hasOwn(result,key)&&!(key==="charged_minor"&&subscriptionZeroPreview))))return false;
  for(const key of ["risk","fraud_risk"]){
    const risk=result?.[key];
    if(risk!==undefined&&risk!==null&&(!plainObject(risk)||risk.risk_level!=="LOW"||risk.decision!=="ALLOW"
      ||risk.confirmation_can_override!==false||!Array.isArray(risk.reason_codes)
      ||!risk.reason_codes.every(code=>["KNOWN_SYNTHETIC_CONTACT_NO_RISK_FLAG","SYNTHETIC_CONTACT_NOT_FLAGGED"].includes(String(code)))))return false;
  }
  return true;
}

/** Display only: a registered carbon preview uses the same customer name
 * formatter as its query card. Unknown copy, appended terms or recorded effects
 * remain raw. This text must never be used as source ID, scope or authorization.
 */
export function customerCarbonConfirmation(summary: JsonValue, action: unknown, preview: JsonValue | undefined, locale="zh-CN"): JsonValue {
  const data=object(preview);
  if(typeof summary!=="string"||!data)return summary;
  if(["risk","fraud_risk","warning","warnings","notice","transfer_id","order_id","payment_id","transaction_id",
    "receipt","effect_receipt","committed_at","executed_at","redemption_id","delegation_id","proposal_id","charged_minor"]
    .some(key=>Object.hasOwn(data,key)))return summary;
  const integer=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>0;
  const name=(value:unknown):value is string=>typeof value==="string"&&Boolean(value.trim())&&value.length<=120&&!/[\r\n\u0000-\u001f]/.test(value);
  if(action==="CARBON_REDEEM"&&integer(data.cost_points)&&name(data.reward_name)){
    const exact=`准备使用${data.cost_points}本活动积分兑换${data.reward_name}，将扣减积分和本活动库存，请确认。`;
    if(summary!==exact)return summary;
    const label=customerName(data.reward_name);
    return locale==="en"?`Use ${data.cost_points} points for “${label}” after you review and confirm. This benefit is limited to the current low-carbon programme; it is not cash or proof of an external merchant's acceptance.`
      :`准备使用${data.cost_points}积分兑换“${label}”，确认后才扣减积分。该权益仅用于当前低碳活动，不代表现金或外部商户已受理。`;
  }
  if(action==="CARBON_CLAIM"&&integer(data.points)&&integer(data.estimated_avoided_grams)&&name(data.behavior_name)){
    const exact=`准备为${data.behavior_name}领取${data.points}本活动积分（版本方法估算${data.estimated_avoided_grams}克）。来源为本活动行为记录，并非核证碳资产，请确认。`;
    if(summary!==exact)return summary;
    const label=customerName(data.behavior_name);
    return locale==="en"?`Prepare to claim ${data.points} points for “${label}” after review and confirmation. The ${data.estimated_avoided_grams} grams figure is an estimate from the recorded activity methodology, not a certified carbon asset.`
      :`准备为“${label}”领取${data.points}积分，核对确认后办理。减排${data.estimated_avoided_grams}克是按已记录行为的方法估算，不代表核证碳资产。`;
  }
  return summary;
}

/** Presentation only. Never supply this projection to a confirmation request.
 * Remove only exact repeated fields under the same declared currency. If two
 * sources disagree, retain both so the difference is not silently overwritten.
 */
export function confirmationFacts(scope: JsonValue | undefined, preview: JsonValue | undefined, options?:{action:string;locale:string}): JsonValue | undefined {
  if(options&&["WEALTH_BUY","WEALTH_REDEEM"].includes(options.action)){
    scope=wealthConfirmationFacts(scope,options.action,options.locale);
    preview=wealthConfirmationFacts(preview,options.action,options.locale);
  }
  const first = object(scope), second = object(preview);
  if (!first || !second) return scope === undefined ? preview : preview === undefined ? scope : [scope,preview];
  const currencySame = (first.currency || "CNY") === (second.currency || "CNY");
  const remaining = Object.fromEntries(Object.entries(first).filter(([key,value])=>
    !(key in second && canonical(value) === canonical(second[key]) && (!key.endsWith("_minor") || currencySame))));
  return Object.keys(remaining).length ? [remaining,second] : second;
}

/** Display-only completeness of the registered fixed-price product preview.
 * It never changes App's bank readiness, assessment or confirmation guards. */
export function wealthConfirmationReady(action:string,value:JsonValue|undefined):boolean {
  const data=object(value),terms=object(data?.terms),profile=object(data?.risk_profile);
  const positive=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>0;
  const text=(value:unknown)=>typeof value==="string"&&Boolean(value.trim());
  return Boolean(data&&terms&&profile&&["WEALTH_BUY","WEALTH_REDEEM"].includes(action)
    &&data.order_type===(action==="WEALTH_BUY"?"BUY":"REDEEM")&&text(data.product_id)&&text(data.product_name)
    &&positive(data.product_version)&&positive(data.amount_minor)&&positive(data.units)&&positive(data.unit_price_minor)
    &&Number.isSafeInteger(data.units*data.unit_price_minor)&&data.units*data.unit_price_minor===data.amount_minor
    &&(data.currency===undefined||data.currency==="CNY")&&typeof data.source_card_last4==="string"&&/^[0-9a-f]{4}$/i.test(data.source_card_last4)
    &&terms.terms_version==="simulation-terms-v1"&&typeof terms.fee_minor==="number"&&Number.isSafeInteger(terms.fee_minor)&&terms.fee_minor>=0
    &&text(terms.redemption_timing)&&text(terms.term)&&terms.capital_guaranteed===false
    &&profile.required_for_this_order===(action==="WEALTH_BUY")
    &&!["warning","warnings","notice","notices","risk","fraud_risk","error"].some(key=>Object.hasOwn(data,key)));
}

/** The order preview's old level/expiry alone is not evidence of a valid
 * independent assessment. Sanitize every displayed occurrence, preserving
 * the signed source and all unrelated product and transaction values. */
export function wealthConfirmationFacts(value:JsonValue|undefined,action:string,locale="zh-CN"):JsonValue|undefined {
  if(!["WEALTH_BUY","WEALTH_REDEEM"].includes(action))return value;
  if(Array.isArray(value))return value.map(item=>wealthConfirmationFacts(item,action,locale)??null);
  const data=object(value);if(!data||!Object.hasOwn(data,"risk_profile")
    &&!(data.order_type===(action==="WEALTH_BUY"?"BUY":"REDEEM")&&typeof data.product_name==="string"&&object(data.terms)))return value;
  const profile=object(data.risk_profile),en=locale==="en",expiry=profile?.expires_at;
  const independent=Boolean(profile&&profile.valid===true&&profile.status==="VALID"&&profile.assessment_source==="INDEPENDENT_USER_FORM"
    &&typeof profile.assessment_id==="string"&&Boolean(profile.assessment_id.trim())&&profile.questionnaire_version==="risk-assessment-v3"
    &&typeof profile.risk_level==="number"&&Number.isSafeInteger(profile.risk_level)&&profile.risk_level>=1&&profile.risk_level<=5
    &&typeof expiry==="string"&&/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(expiry)&&Number.isFinite(Date.parse(expiry))&&Date.parse(expiry)>Date.now());
  const redeem=action==="WEALTH_REDEEM"&&data.order_type==="REDEEM"&&profile?.required_for_this_order===false;
  const boundary=redeem?(en?"This redemption of existing holdings does not require a valid assessment; this does not waive assessment for a purchase.":"本次赎回已有持仓不要求有效测评；不代表买入可以免测。")
    :en?"A purchase still requires your own valid risk assessment.":"买入前仍需由你本人完成有效风险测评。";
  const risk:JsonValue=independent?{risk_level:profile!.risk_level,expires_at:profile!.expires_at,
    notice:en?"Your valid independent assessment has been verified.":"本人有效风险测评已核实。",explanation:boundary}
    :(en?"Your valid independent risk assessment remains unverified. ":"本人有效风险测评待完成或核对。")+boundary;
  return {...data,risk_profile:risk};
}

/** Exact registered subscription-v2 cancellation preview, not a new charge or
 * refund receipt. Unknown/extra terms remain in the ordinary review instead. */
export function subscriptionCancelConfirmation(action:string,scope:JsonValue|undefined,result:JsonValue|undefined,summary:JsonValue|undefined) {
  if(action!=="SUBSCRIPTION_CANCEL")return null;
  const data=object(scope),preview=object(result);
  if(!data||!preview||typeof data.subscription_id!=="string"||!data.subscription_id.trim()
    ||typeof data.merchant!=="string"||!data.merchant.trim()||data.merchant.length>120||/[\r\n\u0000-\u001f]/.test(data.merchant)
    ||typeof data.amount_minor!=="number"||!Number.isSafeInteger(data.amount_minor)||data.amount_minor<0
    ||typeof data.expected_version!=="number"||!Number.isSafeInteger(data.expected_version)||data.expected_version<1
    ||!["BANK_MANDATE","MERCHANT_SIM"].includes(String(data.source_type))||preview.source_type!==data.source_type
    ||data.currency!==undefined&&data.currency!=="CNY")return null;
  const fields=new Set(["subscription_id","merchant","amount_minor","source_type","expected_version","currency","node_id","tool_name","read_only","requires_confirmation"]);
  if(Object.keys(data).some(key=>!fields.has(key))||Object.keys(preview).some(key=>!["message","source_type","simulated"].includes(key))
    ||data.node_id!==undefined&&data.node_id!=="cancel-subscription"||data.tool_name!==undefined&&data.tool_name!=="subscription.cancel.v1"
    ||data.read_only!==undefined&&data.read_only!==false||data.requires_confirmation!==undefined&&data.requires_confirmation!==true
    ||preview.simulated!==undefined&&preview.simulated!==true)return null;
  const source=data.source_type==="BANK_MANDATE"?"银行代扣协议":"商户订阅";
  const exact=`准备取消 ${data.merchant} 的${source}（${exactMajorAmount(data.amount_minor)} 元/期），确认后执行。`;
  return preview.message===exact&&summary===exact?{merchant:data.merchant,originalFeeMinor:data.amount_minor,currency:"CNY" as const}:null;
}

/** Compact exactly one legacy server trade summary; unknown wording is kept. */
export function confirmationSummary(summary: JsonValue, preview: JsonValue | undefined): JsonValue {
  // Exact controlled internal-transfer preview wrapper only. This is display
  // text, never the memo, signed scope, hash, raw ledger or technical evidence.
  if(typeof summary==="string")summary=summary.replace(/^准备向平台账号 ([^\r\n]{1,120})（账户标识 SIM-[0-9A-F]{4}）转账 ((?:0|[1-9]\d*)\.\d{2}) 元；确认后双方同时入账。/,
    "准备向平台账号 $1 转账 $2 元；确认后双方同时入账。");
  const data=object(preview);
  if (typeof summary !== "string" || !data || typeof data.product_name !== "string"
    || typeof data.amount_minor !== "number" || !Number.isSafeInteger(data.amount_minor) || data.amount_minor <= 0
    || typeof data.units !== "number" || !Number.isSafeInteger(data.units) || data.units <= 0
    || !["BUY","REDEEM"].includes(String(data.order_type))) return summary;
  const exact=`准备${data.order_type === "BUY" ? "申购" : "赎回"}${data.product_name} ${(data.amount_minor/100).toFixed(2)} 元（${data.units} 份），确认后执行交易；不保证收益。`;
  return summary === exact ? "确认后才执行交易；不保证收益。" : summary;
}

/** TRANSFER review is a display projection of the already verified pending
 * scope only. Never use a user reference, account ID or legacy tail as a card
 * number/name, and never feed these display values into a bank request. */
export function transferConfirmationFacts(scope: JsonValue | undefined) {
  const data = object(scope) || {};
  const registeredName = typeof data.recipient_display_name === "string" && Boolean(data.recipient_display_name.trim())
    && data.recipient_display_name.length <= 120 && !/[\r\n\u0000-\u001f]/.test(data.recipient_display_name)
    ? data.recipient_display_name : null;
  const card = (key: "source" | "recipient") => {
    const number = data[`${key}_card_number`], tail = data[`${key}_card_display_last4`];
    const knownTail = typeof tail === "string" && /^[0-9a-f]{4}$/i.test(tail) ? tail : null;
    const validNumber = validSimulationCardNumber(number);
    const conflict = validNumber && knownTail !== null && number.slice(-4).toLowerCase() !== knownTail.toLowerCase();
    return {number: validNumber && !conflict ? number : null, tail: conflict ? null : knownTail, conflict};
  };
  return {recipientName: registeredName, sourceCard: card("source"), recipientCard: card("recipient"),
    amountMinor: typeof data.amount_minor === "number" && Number.isSafeInteger(data.amount_minor) && data.amount_minor > 0 ? data.amount_minor : null,
    currency: data.currency === "CNY" || data.currency === "USD" ? data.currency : null,
    memo: typeof data.memo === "string" ? data.memo : null};
}

/** Remove only the exact routine bank-authored preview wrapper. An appended
 * risk/exception or unrecognized message stays visible, with its raw business
 * text intact. This is not a question classifier or an authorization check. */
export function transferConfirmationNotices(scope: JsonValue | undefined, summary: JsonValue | undefined, result: JsonValue | undefined): string[] {
  const data = object(scope) || {}, outcome = object(result) || {}, facts = transferConfirmationFacts(scope);
  const compact = (value: unknown): string | null => {
    if (typeof value !== "string" || !value.trim()) return null;
    const controlled = /^准备向平台账号 ([^\r\n]{1,120})（账户标识 SIM-[0-9A-F]{4}）转账 ((?:0|[1-9]\d*)\.\d{2}) 元；确认后双方同时入账。/;
    const match = value.match(controlled);
    if (match && data.transfer_channel === "INTERNAL_BANK" && match[1] === data.recipient_username
      && facts.currency === "CNY" && facts.amountMinor !== null && match[2] === exactMajorAmount(facts.amountMinor))
      return value.slice(match[0].length).trim() || null;
    return value;
  };
  const notices: string[] = [];
  for (const value of [summary, outcome.message, data.warning, data.notice, outcome.warning, outcome.notice]) {
    const text = compact(value); if (text && !notices.includes(text)) notices.push(text);
  }
  for (const owner of [data, outcome]) {
    if (Array.isArray(owner.warnings)) for (const value of owner.warnings) {
      const text = compact(value); if (text && !notices.includes(text)) notices.push(text);
    }
    for (const key of ["risk", "fraud_risk"]) {
      const risk = object(owner[key]); if (!risk) continue;
      const clear = risk.risk_level === "LOW" && risk.decision === "ALLOW" && risk.confirmation_can_override === false
        && Array.isArray(risk.reason_codes) && risk.reason_codes.every(code => typeof code === "string"
          && ["KNOWN_SYNTHETIC_CONTACT_NO_RISK_FLAG", "SYNTHETIC_CONTACT_NOT_FLAGGED"].includes(code));
      if (!clear) {
        const messages = [risk.message, risk.explanation, risk.notice, risk.reason].map(compact).filter((value): value is string => value !== null);
        const fallback = risk.decision === "BLOCK" || risk.risk_level === "HIGH"
          ? "这笔转账存在高风险，请先核对风险提示，不能通过普通确认跳过拦截。"
          : "转账风险核验有待核对的信息，请先查看风险提示。";
        for (const text of messages.length ? messages : [fallback]) if (!notices.includes(text)) notices.push(text);
      }
    }
    if (owner.error && !notices.length) notices.push("这笔转账的办理信息需要重新核对，尚不能据此认定已付款。");
  }
  return notices;
}
